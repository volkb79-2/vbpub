"""The socket carrier — RG55-INTERFACE-CONTRACT.md §8.1/§8.6, RG-55 P6 C6.

Four things are proven here, in rising cost:

* **One wire shape.** `_dispatch` accepts contract §8.1's
  `{"verb", "args", "contract"}` and NOTHING else — v1's flat request
  (`{"verb": "stop", "session": …}`) is refused with `bad-argument` rather
  than silently read as a different, valid-looking request. That refusal is
  what makes "one shape, no compatibility branch" a checkable claim.
* **Who may talk to it.** `SO_PEERCRED` on every accepted connection: uid 0
  always (that is how the exec carrier arrives), everyone the socket mode
  already admitted when no allowlist is configured, and only the listed
  uids when `CGPROFILE_ALLOW_UIDS` is set — an unreadable credential is
  refused in that last case, never waved through.
* **The permissions the daemon re-asserts at every start** (§8.1 "belt and
  braces"): `0770` on the directory, `root:<the directory's own gid>` and
  `0660` on the socket, and ONE INFO line when the directory is still
  `root:root` (host-setup not installed — the socket is root-only, the exec
  carrier is unaffected, and the daemon serves on regardless).
* **Both carriers carry the same documents.** Every verb is run over the
  socket carrier (a plain stdlib client, as P5 will write) and over the
  exec carrier (the in-image `ctl` client invoked in-process against the
  same socket — exactly what `docker exec … cgprofile ctl` runs) against
  ONE serve loop, and the JSON is diffed. The socket-carrier documents are
  also frozen as `fixtures/rg55/socket/<verb>-{request,response}.json`,
  which is what P5 codes its client against.

Determinism (the goldens depend on it): a fixed wall clock, fixed session
ids, a per-thread counting sampler clock, a fake cgroup/proc tree, DAMON
forced "available" but every session started with `--damon off`, and a
sampler sleep that lets each session take EXACTLY one sample and then
blocks until that session is stopped.
"""

from __future__ import annotations

import json
import os
import socket
import struct
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import pytest

import cgprofile as cg
from lib import damon as damon_mod, serve
from tests.conftest import cgroup_files, write_cgroup

RG55_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "rg55"
SOCKET_FIXTURES = RG55_FIXTURES / "socket"

CONTAINER_ID = "a" * 64
SESSION_ID = "s-20260912T101500Z-9f01"
SESSION_ID_2 = "s-20260912T101500Z-9f02"
TOKEN_A = "rg55-parity-token-a"
TOKEN_B = "rg55-parity-token-b"
EPOCH_START = datetime(2026, 9, 12, 10, 15, 0, tzinfo=timezone.utc).timestamp()
META = {
    "lane": "rg55-fixture-lane", "project": "run-gate-project",
    "worktree": "/workspaces/vbpub",
    "commit": "0123456789abcdef0123456789abcdef01234567",
    "run_gate_revision": 41, "kind": "command", "expected": None,
}
# Every golden is written as it would read on a real host: the test's own
# tmp paths are rewritten to the production defaults (contract §1.8) before
# the document is compared or frozen. These two strings are the ONLY
# normalization the socket goldens get.
PROD_SESSIONS_DIR = "/var/lib/cgprofile/sessions"
PROD_SOCKET = "/run/cgprofile/ctl.sock"
REGEN_ENV = "CGPROFILE_REGEN_SOCKET_GOLDENS"
VERBS = ("version", "host", "start", "status", "stop", "report", "gc")
# §8.2's streaming verb answers MANY lines on one connection, so its
# response golden is the ARRAY of those lines rather than one object —
# see the fixtures README.
STREAMING = ("watch",)


# ── tiny doubles ────────────────────────────────────────────────────────

class _FakeConn:
    """A connection double that records what was sent and can answer (or
    refuse) `SO_PEERCRED` — the peer-credential tests need to BE a uid the
    test process is not."""

    def __init__(self, chunks: List[bytes], *, uid: Optional[int] = 0,
                 peercred_error: Optional[Exception] = None) -> None:
        self._chunks = list(chunks)
        self.sent: List[bytes] = []
        self.closed = False
        self.timeout: Optional[float] = None
        self._uid = uid
        self._peercred_error = peercred_error

    def settimeout(self, value: float) -> None:
        self.timeout = value

    def getsockopt(self, level: int, optname: int, buflen: int) -> bytes:
        if self._peercred_error is not None:
            raise self._peercred_error
        assert level == socket.SOL_SOCKET
        assert optname == socket.SO_PEERCRED
        return struct.pack("3i", 4242, self._uid, 4242)

    def recv(self, _size: int) -> bytes:
        return self._chunks.pop(0) if self._chunks else b""

    def sendall(self, payload: bytes) -> None:
        self.sent.append(payload)

    def close(self) -> None:
        self.closed = True

    def reply(self) -> Dict[str, Any]:
        assert len(self.sent) == 1, self.sent
        return json.loads(self.sent[0].decode("utf-8"))


def _wire(verb: str, **args: Any) -> Dict[str, Any]:
    return {"verb": verb, "args": args, "contract": 1}


def _wire_bytes(verb: str, **args: Any) -> bytes:
    return (json.dumps(_wire(verb, **args)) + "\n").encode("utf-8")


# ── the deterministic daemon the goldens and the parity diff share ──────

class _TickBarrier:
    """Fence initial sampler sleep until start returned, then hold sample-zero.

    The sampler calls this on the session thread, whose name carries its id.
    The start-returned event prevents a scheduling race with `thread.start()`
    and registry publication. This sleep precedes `Sampler.run`, so holding
    it leaves the synchronous sample-zero record stable for `status`.
    """

    def __init__(self) -> None:
        self.server: Optional[serve.SessionServer] = None
        self._events: Dict[str, threading.Event] = {}
        self._start_returned: Dict[str, threading.Event] = {}
        self._lock = threading.Lock()

    def event_for(self, session_id: str) -> threading.Event:
        with self._lock:
            return self._events.setdefault(session_id, threading.Event())

    def release_start(self, session_id: str) -> None:
        with self._lock:
            event = self._start_returned.setdefault(session_id, threading.Event())
        event.set()

    def __call__(self, _seconds: float) -> None:
        name = threading.current_thread().name
        prefix = "cgprofile-session-"
        if not name.startswith(prefix):  # pragma: no cover - defensive
            return
        session_id = name[len(prefix):]
        with self._lock:
            start_returned = self._start_returned.setdefault(session_id, threading.Event())
        if not start_returned.wait(timeout=10.0):
            raise AssertionError(f"start did not return for {session_id}")
        server = self.server
        if server is None:  # pragma: no cover - defensive
            return
        sess = server._sessions.get(session_id)
        assert sess is not None, f"session was not registered after start returned: {session_id}"
        self.event_for(session_id).set()
        sess.stop_event.wait(timeout=10.0)


def _per_thread_counting_clock() -> Callable[[], float]:
    """A sampler clock that is deterministic per session thread (0, 1, 2, …
    on each) and FROZEN for every other caller. A single shared counter
    would hand the two sessions interleaved values — and the second `stop`
    different numbers from the first — which is exactly the nondeterminism
    a byte-compared golden cannot have."""
    local = threading.local()

    def clock() -> float:
        if not threading.current_thread().name.startswith("cgprofile-session-"):
            return 0.0
        value = getattr(local, "t", 0.0)
        local.t = value + 1.0
        return value

    return clock


def _fake_roots(tmp_path: Path) -> tuple:
    root = tmp_path / "cgroup"
    write_cgroup(root, "", cgroup_files())
    write_cgroup(root, "dev.slice", cgroup_files())
    write_cgroup(root, "dev.slice/dev-background.slice", cgroup_files())
    write_cgroup(
        root,
        f"dev.slice/dev-background.slice/docker-{CONTAINER_ID}.scope",
        cgroup_files(memory_current=500 * 1024 * 1024),
    )
    proc = tmp_path / "proc"
    proc.mkdir()
    (proc / "loadavg").write_text("1.0 1.0 1.0 1/100 999\n")
    (proc / "meminfo").write_text("MemTotal:  1000 kB\nMemAvailable: 500 kB\n")
    pressure = proc / "pressure"
    pressure.mkdir()
    for name in ("cpu", "memory", "io"):
        (pressure / name).write_text(
            "some avg10=0.00 avg60=0.00 avg300=0.00 total=0\n"
            "full avg10=0.00 avg60=0.00 avg300=0.00 total=0\n"
        )
    return root, proc


def _stub_report_script(tmp_path: Path) -> str:
    """A report tier that writes the file and nothing else — the real one
    needs pandas/plotly, which this suite must not require to freeze the
    `report` response SHAPE."""
    script = tmp_path / "stub_report.py"
    script.write_text(
        "import os, sys\n"
        "run_dir = sys.argv[sys.argv.index('--run-dir') + 1]\n"
        "open(os.path.join(run_dir, 'report.html'), 'w').write('<html>stub</html>')\n"
    )
    return str(script)


def _wait_for_socket(socket_path: str) -> None:
    deadline = time.monotonic() + 5.0
    while not os.path.exists(socket_path) and time.monotonic() < deadline:
        time.sleep(0.01)
    assert os.path.exists(socket_path)


def _normalize(doc: Any, *, sessions_dir: str, socket_path: str) -> Any:
    text = json.dumps(doc)
    text = text.replace(sessions_dir, PROD_SESSIONS_DIR).replace(socket_path, PROD_SOCKET)
    return json.loads(text)


def _raw_socket_request(socket_path: str, req: Dict[str, Any]) -> Dict[str, Any]:
    """The socket carrier as a THIRD-PARTY client writes it (P5's own
    client is stdlib `socket` too) — deliberately not `cg._ctl_roundtrip`,
    so the parity diff compares two independent implementations."""
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(10.0)
        client.connect(socket_path)
        client.sendall((json.dumps(req) + "\n").encode("utf-8"))
        data = b""
        while not data.endswith(b"\n"):
            chunk = client.recv(65536)
            if not chunk:
                break
            data += chunk
    return json.loads(data.decode("utf-8"))


class _StreamReader:
    """Newline-delimited JSON off ONE streaming connection (§8.2): one line
    at a time, then everything up to the daemon's close."""

    def __init__(self, client: socket.socket) -> None:
        self.client = client
        self.buffer = b""

    def next(self) -> Dict[str, Any]:
        while b"\n" not in self.buffer:
            chunk = self.client.recv(65536)
            assert chunk, "stream closed before the next line"
            self.buffer += chunk
        raw, self.buffer = self.buffer.split(b"\n", 1)
        return json.loads(raw.decode("utf-8"))

    def rest(self) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        with self.client:
            while True:
                while b"\n" in self.buffer:
                    raw, self.buffer = self.buffer.split(b"\n", 1)
                    if raw.strip():
                        out.append(json.loads(raw.decode("utf-8")))
                chunk = self.client.recv(65536)
                if not chunk:
                    return out
                self.buffer += chunk


def _ctl_argv(verb: str, socket_path: str, session: Optional[str] = None,
              token: Optional[str] = None) -> List[str]:
    argv = ["ctl", "--socket", socket_path, verb]
    if verb == "start":
        argv += [
            "--target", f"containerid:{CONTAINER_ID}", "--scope", "container-shared",
            "--damon", "off", "--interval", "1.0", "--meta", json.dumps(META),
        ]
        if token is not None:
            argv += ["--token", token]
    elif session is not None:
        argv += [session]
    return argv


def _request_for(verb: str, socket_path: str, session: Optional[str] = None,
                 token: Optional[str] = None) -> Dict[str, Any]:
    """The §8.1 request built by the REFERENCE TRANSLATOR (§8.1 rule 2) —
    the in-image `ctl` client's own `_ctl_request`, driven through the real
    argument parser, never a hand-written dict."""
    argv = _ctl_argv(verb, socket_path, session=session, token=token)
    return cg._ctl_request(cg.build_parser().parse_args(argv))


def _force_stalled(server: serve.SessionServer, session_id: str) -> None:
    """Drive one session's own tracker past its idle bound on two synthetic
    readings — a REAL state change on a REAL session, on demand, so §8.2's
    `verdict`-on-change line is in the frozen stream. The transitions
    themselves are `tests/test_liveness.py`'s subject; this scenario needs
    one to happen at a point a byte-compared golden can see."""
    sess = server._sessions[session_id]
    with sess.lock:
        sess.watch.observe(serve.liveness_mod.LivenessSample(
            mono=0.0, at="2026-09-12T10:15:00Z", elapsed_seconds=0.0))
        sess.watch.observe(serve.liveness_mod.LivenessSample(
            mono=10_000.0, at="2026-09-12T12:00:00Z", elapsed_seconds=10_000.0))
    assert sess.watch.state == "stalled"


class _Scenario:
    """Runs every verb over BOTH carriers against ONE serve loop.

    Call order is chosen so each verb is asked at a point where the answer
    is identical for both carriers: `version`/`host`/`gc` are idempotent;
    `status` is asked twice back to back while exactly one session is live;
    `start`/`stop`/`report` get one session PER CARRIER (a second `start`
    with the same token would legitimately answer `reused: true`), and the
    second session's id/token are rewritten to the first's before the diff.
    """

    def __init__(self, tmp_path: Path, monkeypatch: Any, capsys: Any) -> None:
        self.tmp_path = tmp_path
        self.capsys = capsys
        self.sessions_dir = str(tmp_path / "sessions")
        self.socket_path = str(tmp_path / "run" / "ctl.sock")
        self.barrier = _TickBarrier()
        monkeypatch.setattr(damon_mod, "available", lambda: True)
        ids = iter([SESSION_ID, SESSION_ID_2])
        root, proc = _fake_roots(tmp_path)
        self.server = serve.SessionServer(
            sessions_dir=self.sessions_dir, socket_path=self.socket_path,
            cgroup_root=str(root), proc_root=str(proc),
            clock=lambda: EPOCH_START, sampler_clock=_per_thread_counting_clock(),
            sampler_sleep=self.barrier, session_id_fn=lambda: next(ids),
            accept_timeout=0.05, report_python=sys.executable,
            report_script=_stub_report_script(tmp_path),
        )
        self.barrier.server = self.server
        self.thread = threading.Thread(target=self.server._accept_loop, daemon=True)
        self.socket_docs: Dict[str, Dict[str, Any]] = {}
        self.exec_docs: Dict[str, Dict[str, Any]] = {}

    def __enter__(self) -> "_Scenario":
        self.thread.start()
        _wait_for_socket(self.socket_path)
        return self

    def __exit__(self, *exc: Any) -> None:
        self.server.request_shutdown()
        self.thread.join(timeout=5.0)

    # -- the two carriers -------------------------------------------------

    def _over_socket(self, verb: str, **kw: Any) -> Dict[str, Any]:
        req = _request_for(verb, self.socket_path, **kw)
        return self._record(self.socket_docs, verb, req, _raw_socket_request(self.socket_path, req))

    def _over_exec(self, verb: str, **kw: Any) -> Dict[str, Any]:
        req = _request_for(verb, self.socket_path, **kw)
        self.capsys.readouterr()
        rc = cg.main(_ctl_argv(verb, self.socket_path, **kw))
        resp = json.loads(self.capsys.readouterr().out)
        assert rc == (0 if resp.get("ok") else 2), (verb, rc, resp)
        return self._record(self.exec_docs, verb, req, resp)

    def _record(self, into: Dict[str, Dict[str, Any]], verb: str,
                req: Dict[str, Any], resp: Dict[str, Any]) -> Dict[str, Any]:
        into[verb] = {
            "request": _normalize(req, sessions_dir=self.sessions_dir,
                                  socket_path=self.socket_path),
            "response": _normalize(resp, sessions_dir=self.sessions_dir,
                                   socket_path=self.socket_path),
        }
        return resp

    # -- the scenario itself ---------------------------------------------

    def run(self) -> None:
        for verb in ("version", "host"):
            self._over_socket(verb)
            self._over_exec(verb)

        started = self._over_socket("start", token=TOKEN_A)
        assert started["ok"] is True and started["session"] == SESSION_ID, started
        self.barrier.release_start(SESSION_ID)
        assert self.barrier.event_for(SESSION_ID).wait(timeout=10.0)

        # Exactly one session live, one sample taken: both carriers must see
        # the identical listing.
        self._over_socket("status")
        self._over_exec("status")

        # The single-session form uses the same contract timestamp as the
        # listing. Exercise the shipped CLI against the live socket: a raw
        # response without top-level `at` used to be accepted by the server
        # but rejected by its own reference translator with exit 3.
        specific_req = _request_for("status", self.socket_path, session=SESSION_ID)
        specific_socket = _raw_socket_request(self.socket_path, specific_req)
        self.capsys.readouterr()
        specific_rc = cg.main(_ctl_argv("status", self.socket_path, session=SESSION_ID))
        specific_exec = json.loads(self.capsys.readouterr().out)
        assert specific_rc == 0
        assert specific_exec == specific_socket
        assert specific_exec["at"] == "2026-09-12T10:15:00Z"
        assert specific_exec["session"]["session"] == SESSION_ID

        started_b = self._over_exec("start", token=TOKEN_B)
        assert started_b["session"] == SESSION_ID_2, started_b
        self.barrier.release_start(SESSION_ID_2)
        assert self.barrier.event_for(SESSION_ID_2).wait(timeout=10.0)

        # §8.2: the streaming verb, answering the request golden that C6
        # froze ahead of it. The connection stays OPEN across the `stop`
        # below — that is the whole point of the exception to §8.1's
        # one-request-per-connection rule — and the daemon keeps answering
        # `stop`/`report`/`gc` on other connections while it does.
        watch_req = _request_for("watch", self.socket_path, session=SESSION_ID)
        watch_client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        watch_client.settimeout(15.0)
        watch_client.connect(self.socket_path)
        watch_client.sendall((json.dumps(watch_req) + "\n").encode("utf-8"))
        reader = _StreamReader(watch_client)
        first = reader.next()  # the attach reading, before anything changes

        # A REAL state change on a REAL session, driven on demand so the
        # golden carries all three §8.2 line shapes in a fixed order. The
        # transitions themselves are `test_liveness.py`'s subject; what this
        # scenario needs is one, at a point where the stream's
        # `verdict`-on-change rule is observable.
        # Both sessions, not just the watched one: the `stop` documents the
        # two carriers produce are diffed against each other, so whatever is
        # done to one session's watch state must be done to the other's or
        # the parity claim would be comparing two different lanes.
        _force_stalled(self.server, SESSION_ID)
        _force_stalled(self.server, SESSION_ID_2)

        self._over_socket("stop", session=SESSION_ID)
        self.watch_doc = {
            "request": _normalize(watch_req, sessions_dir=self.sessions_dir,
                                  socket_path=self.socket_path),
            "response": _normalize([first] + reader.rest(), sessions_dir=self.sessions_dir,
                                   socket_path=self.socket_path),
        }
        self._over_exec("stop", session=SESSION_ID_2)
        self._over_socket("report", session=SESSION_ID)
        self._over_exec("report", session=SESSION_ID_2)
        self._over_socket("gc")
        self._over_exec("gc")


def _rewrite_second_session(doc: Any) -> Any:
    """The exec carrier drives the SECOND session (a carrier cannot start
    the same token twice without legitimately answering `reused: true`), so
    its id and token are rewritten to the first's before the diff. Nothing
    else is touched — every other byte must already match."""
    text = json.dumps(doc)
    return json.loads(text.replace(SESSION_ID_2, SESSION_ID).replace(TOKEN_B, TOKEN_A))


@pytest.fixture
def scenario(tmp_path, monkeypatch, capsys):
    with _Scenario(tmp_path, monkeypatch, capsys) as sc:
        sc.run()
        yield sc


# ── §8.1: one wire shape, nothing else ──────────────────────────────────

class TestWireShape:
    def test_request_line_limits_must_be_usable(self, tmp_path):
        with pytest.raises(ValueError, match="request_line_timeout must be positive"):
            serve.SessionServer(
                sessions_dir=str(tmp_path / "sessions"), request_line_timeout=0,
            )
        with pytest.raises(ValueError, match="max_request_line_bytes must be at least 2"):
            serve.SessionServer(
                sessions_dir=str(tmp_path / "sessions"), max_request_line_bytes=1,
            )

    def test_wire_object_keys_must_be_strings(self, tmp_path):
        server = serve.SessionServer(sessions_dir=str(tmp_path / "sessions"))
        response = server._dispatch({1: "version"})
        assert response["error"]["code"] == "bad-argument"
        assert "keys must be strings" in response["error"]["message"]

    def test_malformed_json_wire_values_get_bad_argument_and_server_survives(
        self, tmp_path
    ):
        socket_path = str(tmp_path / "run" / "ctl.sock")
        server = serve.SessionServer(
            sessions_dir=str(tmp_path / "sessions"), socket_path=socket_path,
            accept_timeout=0.05, request_line_timeout=0.4,
        )
        thread = threading.Thread(target=server._accept_loop, daemon=True)
        thread.start()
        _wait_for_socket(socket_path)
        bad_requests = [
            {"verb": ["version"], "args": {}, "contract": 1},
            {"verb": "version", "args": {}, "contract": True},
            {"verb": "start", "args": {
                "target": "containerid:" + "a" * 64, "scope": "container",
                "token": 12, "damon": "off", "meta": {},
            }, "contract": 1},
            {"verb": "start", "args": {
                "target": "containerid:" + "a" * 64, "scope": "container",
                "token": "rg55-wire-token", "damon": "off", "meta": {},
                "place": True, "memory_high": 1.5,
            }, "contract": 1},
        ]
        try:
            for request in bad_requests:
                response = _raw_socket_request(socket_path, request)
                assert response["error"]["code"] == "bad-argument", response
            assert _raw_socket_request(socket_path, _wire("version"))["ok"] is True
        finally:
            server.request_shutdown()
            thread.join(timeout=5.0)

    def test_complete_unterminated_stop_is_not_dispatched_and_server_survives(
        self, tmp_path
    ):
        socket_path = str(tmp_path / "run" / "ctl.sock")
        server = serve.SessionServer(
            sessions_dir=str(tmp_path / "sessions"), socket_path=socket_path,
            accept_timeout=0.05,
        )
        dispatched = []

        def record_stop(args):
            dispatched.append(args)
            return {"ok": True, "stopped": True}

        server.handle_stop = record_stop
        thread = threading.Thread(target=server._accept_loop, daemon=True)
        thread.start()
        _wait_for_socket(socket_path)
        try:
            client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            client.settimeout(2.0)
            with client:
                client.connect(socket_path)
                client.sendall(json.dumps({
                    "verb": "stop",
                    "args": {"session": SESSION_ID},
                    "contract": 1,
                }).encode("utf-8"))
                client.shutdown(socket.SHUT_WR)
                assert client.recv(65536) == b""

            assert dispatched == []
            assert _raw_socket_request(socket_path, _wire("version"))["ok"] is True
        finally:
            server.request_shutdown()
            thread.join(timeout=5.0)

    def test_trickle_line_has_wall_deadline_and_does_not_block_stop(
        self, tmp_path
    ):
        socket_path = str(tmp_path / "run" / "ctl.sock")
        server = serve.SessionServer(
            sessions_dir=str(tmp_path / "sessions"), socket_path=socket_path,
            accept_timeout=0.05, request_line_timeout=0.4,
        )
        server.handle_stop = lambda _args: server._error_response(
            "unknown-session", "no session"
        )
        thread = threading.Thread(target=server._accept_loop, daemon=True)
        thread.start()
        _wait_for_socket(socket_path)
        slow = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        slow.connect(socket_path)
        slow.sendall(b'{"verb":')
        trickle_payload = b'"version","args":{},"contract":1}'
        position = [0]

        def trickle() -> None:
            try:
                while True:
                    byte = trickle_payload[position[0] % len(trickle_payload)]
                    slow.sendall(bytes([byte]))
                    position[0] += 1
                    time.sleep(0.05)
            except OSError:
                pass

        trickler = threading.Thread(target=trickle, daemon=True)
        trickler.start()
        try:
            time.sleep(0.08)
            stopped = _raw_socket_request(
                socket_path,
                {"verb": "stop", "args": {"session": SESSION_ID}, "contract": 1},
            )
            assert stopped["error"]["code"] == "unknown-session"
        finally:
            slow.close()
            trickler.join(timeout=2.0)
            server.request_shutdown()
            thread.join(timeout=5.0)

    def test_deadline_is_absolute_across_successful_receives(self, tmp_path):
        now = [0.0]

        class _AdvancingConn(_FakeConn):
            def recv(self, size: int) -> bytes:
                now[0] += 0.2
                return super().recv(size)

        server = serve.SessionServer(
            sessions_dir=str(tmp_path / "sessions"), request_line_timeout=0.5,
            request_clock=lambda: now[0],
        )
        conn = _AdvancingConn([
            b'{"verb":', b'"version",', b'"args":{},', b'"contract":1}\n',
        ])
        server._handle_connection(conn)
        assert conn.closed is True
        assert conn.sent == []
        assert now[0] == pytest.approx(0.6)

    def test_deadline_expiring_before_recv_closes_without_dispatch(self, tmp_path):
        readings = iter([0.0, 0.0, 0.5])
        server = serve.SessionServer(
            sessions_dir=str(tmp_path / "sessions"), request_line_timeout=0.5,
            request_clock=lambda: next(readings),
        )
        conn = _FakeConn([_wire_bytes("version")])

        server._handle_connection(conn)

        assert conn.closed is True
        assert conn.sent == []

    def test_idle_partial_request_timeout_closes_and_next_request_is_served(
        self, tmp_path
    ):
        class _IdleConn(_FakeConn):
            def recv(self, size: int) -> bytes:
                if self._chunks:
                    return super().recv(size)
                raise socket.timeout()

        server = serve.SessionServer(
            sessions_dir=str(tmp_path / "sessions"),
            request_clock=lambda: 0.0,
        )
        partial = _IdleConn([b'{"verb":'])
        server._handle_connection(partial)

        assert partial.closed is True
        assert partial.sent == []
        assert partial.timeout is not None and partial.timeout > 0

        next_request = _FakeConn([_wire_bytes("version")])
        server._handle_connection(next_request)
        assert next_request.closed is True
        assert next_request.reply()["ok"] is True

    def test_oversized_complete_line_is_refused_by_its_newline_length(self, tmp_path):
        server = serve.SessionServer(
            sessions_dir=str(tmp_path / "sessions"), max_request_line_bytes=8,
        )
        conn = _FakeConn([b"12345678\n"])

        server._handle_connection(conn)

        response = conn.reply()
        assert response["error"]["code"] == "bad-argument"
        assert "exceeds 8 bytes" in response["error"]["message"]

    def test_request_line_size_is_bounded(self, tmp_path):
        socket_path = str(tmp_path / "run" / "ctl.sock")
        server = serve.SessionServer(
            sessions_dir=str(tmp_path / "sessions"), socket_path=socket_path,
            accept_timeout=0.05, max_request_line_bytes=64,
        )
        thread = threading.Thread(target=server._accept_loop, daemon=True)
        thread.start()
        _wait_for_socket(socket_path)
        try:
            response = _raw_socket_request(
                socket_path,
                {"verb": "version", "args": {"padding": "x" * 100}, "contract": 1},
            )
            assert response["error"]["code"] == "bad-argument"
            assert "exceeds 64 bytes" in response["error"]["message"]
        finally:
            server.request_shutdown()
            thread.join(timeout=5.0)

    def test_refuses_the_v1_flat_request_shape(self, tmp_path):
        # The whole point of the migration: a v1 client's `{"verb": "stop",
        # "session": …}` must be REFUSED by name, never quietly read as
        # "stop with no session" (which would answer `unknown-session` and
        # look like a daemon bug to the consumer).
        server = serve.SessionServer(sessions_dir=str(tmp_path / "sessions"))
        resp = server._dispatch({"verb": "stop", "session": "s-20260101T000000Z-abcd"})
        assert resp["ok"] is False
        assert resp["error"]["code"] == "bad-argument"
        assert "session" in resp["error"]["message"]
        assert "§8.1" in resp["error"]["message"]

    def test_refuses_a_foreign_contract_major(self, tmp_path):
        server = serve.SessionServer(sessions_dir=str(tmp_path / "sessions"))
        resp = server._dispatch({"verb": "version", "args": {}, "contract": 2})
        assert resp["error"]["code"] == "bad-argument"
        assert "contract 2" in resp["error"]["message"]

    def test_boolean_is_not_the_integer_contract_version(self, tmp_path):
        server = serve.SessionServer(sessions_dir=str(tmp_path / "sessions"))
        resp = server._dispatch({"verb": "version", "args": {}, "contract": True})
        assert resp["error"]["code"] == "bad-argument"

    def test_a_nonstring_verb_is_a_bad_argument(self, tmp_path):
        server = serve.SessionServer(sessions_dir=str(tmp_path / "sessions"))
        resp = server._dispatch({"verb": ["version"], "args": {}, "contract": 1})
        assert resp["error"]["code"] == "bad-argument"
        assert "verb" in resp["error"]["message"]

    def test_refuses_a_non_object_args(self, tmp_path):
        server = serve.SessionServer(sessions_dir=str(tmp_path / "sessions"))
        resp = server._dispatch({"verb": "version", "args": ["session"], "contract": 1})
        assert resp["error"]["code"] == "bad-argument"
        assert "'args'" in resp["error"]["message"]

    def test_refuses_a_request_that_is_not_an_object(self, tmp_path):
        server = serve.SessionServer(sessions_dir=str(tmp_path / "sessions"))
        assert server._dispatch(["version"])["error"]["code"] == "bad-argument"

    def test_an_argument_less_verb_may_omit_args_entirely(self, tmp_path):
        # Tolerated on purpose: `{"verb": "version", "contract": 1}` carries
        # no ambiguity (there is nothing to misread), and a hand-written
        # probe is the first thing an operator types at this socket.
        server = serve.SessionServer(sessions_dir=str(tmp_path / "sessions"))
        resp = server._dispatch({"verb": "version", "contract": 1})
        assert resp["ok"] is True

    def test_contract_may_be_omitted(self, tmp_path):
        server = serve.SessionServer(sessions_dir=str(tmp_path / "sessions"))
        assert server._dispatch({"verb": "version", "args": {}})["ok"] is True

    def test_unknown_verb_still_reports_the_verb(self, tmp_path):
        server = serve.SessionServer(sessions_dir=str(tmp_path / "sessions"))
        resp = server._dispatch(_wire("frobnicate"))
        assert resp["error"]["code"] == "bad-argument"
        assert "frobnicate" in resp["error"]["message"]


# ── §8.1: peer credentials ──────────────────────────────────────────────

class TestParseAllowUids:
    def test_unset_and_empty_mean_no_allowlist(self):
        assert serve.parse_allow_uids(None) is None
        assert serve.parse_allow_uids("") is None
        assert serve.parse_allow_uids("  ") is None

    def test_parses_a_list_and_drops_duplicates(self):
        assert serve.parse_allow_uids("0, 1000 ,1003,1000") == [0, 1000, 1003]

    def test_a_malformed_entry_raises(self):
        with pytest.raises(ValueError) as exc_info:
            serve.parse_allow_uids("1000,vscode")
        assert "vscode" in str(exc_info.value)

    @pytest.mark.parametrize("value", [",,", "1000,", ",1000", "1000,,1001"])
    def test_empty_entries_in_a_configured_list_raise(self, value):
        with pytest.raises(ValueError, match="empty entries"):
            serve.parse_allow_uids(value)

    def test_a_negative_uid_raises(self):
        with pytest.raises(ValueError):
            serve.parse_allow_uids("-1")


class TestPeerCredentials:
    def _server(self, tmp_path, **kw):
        return serve.SessionServer(sessions_dir=str(tmp_path / "sessions"), **kw)

    def test_no_allowlist_serves_any_peer(self, tmp_path):
        server = self._server(tmp_path)
        conn = _FakeConn([_wire_bytes("version")], uid=1000)
        server._handle_connection(conn)
        assert conn.reply()["ok"] is True

    def test_a_listed_uid_is_served(self, tmp_path):
        server = self._server(tmp_path, allow_uids=[1000, 1003])
        conn = _FakeConn([_wire_bytes("version")], uid=1003)
        server._handle_connection(conn)
        assert conn.reply()["ok"] is True

    def test_an_unlisted_uid_is_refused(self, tmp_path):
        server = self._server(tmp_path, allow_uids=[1000])
        conn = _FakeConn([_wire_bytes("version")], uid=1234)
        server._handle_connection(conn)
        assert conn.reply() == {
            "ok": False, "contract": 1,
            "error": {"code": "peer-refused",
                      "message": "peer uid 1234 is not in CGPROFILE_ALLOW_UIDS"},
        }
        assert conn.closed is True

    def test_root_is_always_allowed_even_when_not_listed(self, tmp_path):
        # This is how the EXEC carrier arrives (`docker exec` runs as uid 0
        # inside the daemon, D-30): an allowlist that locked root out would
        # break the default carrier.
        server = self._server(tmp_path, allow_uids=[1000])
        conn = _FakeConn([_wire_bytes("version")], uid=0)
        server._handle_connection(conn)
        assert conn.reply()["ok"] is True

    def test_unreadable_credentials_are_refused_when_an_allowlist_is_set(self, tmp_path):
        # Fail CLOSED: the operator asked to narrow access, so "I could not
        # tell who this is" cannot mean "let them in".
        server = self._server(tmp_path, allow_uids=[1000])
        conn = _FakeConn([_wire_bytes("version")], peercred_error=OSError("no peercred"))
        server._handle_connection(conn)
        resp = conn.reply()
        assert resp["error"]["code"] == "peer-refused"
        assert "unavailable" in resp["error"]["message"]

    def test_unreadable_credentials_are_served_when_no_allowlist_is_set(self, tmp_path):
        server = self._server(tmp_path)
        conn = _FakeConn([_wire_bytes("version")], peercred_error=OSError("no peercred"))
        server._handle_connection(conn)
        assert conn.reply()["ok"] is True

    def test_a_truncated_peercred_payload_is_treated_as_unreadable(self, tmp_path):
        server = self._server(tmp_path, allow_uids=[1000])
        conn = _FakeConn([_wire_bytes("version")], uid=1000)
        conn.getsockopt = lambda *a, **k: b"\x00\x00"  # type: ignore[assignment]
        server._handle_connection(conn)
        response = conn.reply()
        assert response["error"]["code"] == "peer-refused"
        assert "peer uid unavailable" in response["error"]["message"]

    def test_log_flushes_a_diagnostic_to_stderr_immediately(self, tmp_path, monkeypatch):
        class _Stderr:
            def __init__(self):
                self.writes = []
                self.flushes = 0

            def write(self, value):
                self.writes.append(value)
                return len(value)

            def flush(self):
                self.flushes += 1

        stderr = _Stderr()
        monkeypatch.setattr(serve.sys, "stderr", stderr)
        serve.SessionServer._log("diagnostic")
        assert "".join(stderr.writes) == "cgprofile: diagnostic\n"
        assert stderr.flushes == 1

    def test_watch_thread_is_daemonized_for_an_open_client(self, tmp_path, monkeypatch):
        server = self._server(tmp_path)
        seen = {}

        class _Thread:
            def __init__(self, *args, **kwargs):
                seen.update(kwargs)

            def start(self):
                return None

        monkeypatch.setattr(serve.threading, "Thread", _Thread)
        server._handle_connection(_FakeConn([_wire_bytes("watch", session="not-a-session")]))
        assert seen["daemon"] is True

    def test_a_real_socket_peer_is_this_process_uid(self, tmp_path):
        # The one test that reads a REAL SO_PEERCRED rather than a double:
        # an allowlist holding this process's own uid admits it, and the
        # same daemon refuses a uid it does not hold.
        socket_path = str(tmp_path / "ctl.sock")
        server = serve.SessionServer(
            sessions_dir=str(tmp_path / "sessions"), socket_path=socket_path,
            accept_timeout=0.05, allow_uids=[os.getuid() + 1000],
        )
        thread = threading.Thread(target=server._accept_loop, daemon=True)
        thread.start()
        try:
            _wait_for_socket(socket_path)
            refused = _raw_socket_request(socket_path, _wire("version"))
            assert refused["error"]["code"] == "peer-refused"
            assert str(os.getuid()) in refused["error"]["message"]
            server.allow_uids = [os.getuid()]
            assert _raw_socket_request(socket_path, _wire("version"))["ok"] is True
        finally:
            server.request_shutdown()
            thread.join(timeout=5.0)

    def test_refused_peer_gets_json_after_sending_and_half_closing(self, tmp_path):
        socket_path = str(tmp_path / "ctl.sock")
        server = serve.SessionServer(
            sessions_dir=str(tmp_path / "sessions"), socket_path=socket_path,
            accept_timeout=0.05, allow_uids=[0],
        )
        server._peer_uid = lambda _conn: 1000
        thread = threading.Thread(target=server._accept_loop, daemon=True)
        thread.start()
        try:
            _wait_for_socket(socket_path)
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                client.settimeout(2.0)
                client.connect(socket_path)
                client.sendall((json.dumps(_wire("version", padding="x" * 65536)) + "\n").encode())
                client.shutdown(socket.SHUT_WR)
                response = client.recv(65536)
            assert json.loads(response)["error"]["code"] == "peer-refused"
        finally:
            server.request_shutdown()
            thread.join(timeout=5.0)

    def test_refused_peer_reply_survives_client_write_after_accept(self, tmp_path):
        socket_path = str(tmp_path / "ctl.sock")
        server = serve.SessionServer(
            sessions_dir=str(tmp_path / "sessions"), socket_path=socket_path,
            accept_timeout=0.05, allow_uids=[0],
        )
        accepted = threading.Event()

        def denied_uid(_conn):
            accepted.set()
            return 1000

        server._peer_uid = denied_uid
        thread = threading.Thread(target=server._accept_loop, daemon=True)
        thread.start()
        try:
            _wait_for_socket(socket_path)
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                client.settimeout(2.0)
                client.connect(socket_path)
                assert accepted.wait(timeout=2.0)
                time.sleep(0.05)
                client.sendall((json.dumps(_wire("version")) + "\n").encode())
                response = client.recv(65536)
            assert json.loads(response)["error"]["code"] == "peer-refused"
        finally:
            server.request_shutdown()
            thread.join(timeout=5.0)


class TestServeCliAllowUids:
    def _serve_argv(self, tmp_path):
        return ["serve", "--sessions", str(tmp_path / "sessions"),
                "--socket", str(tmp_path / "ctl.sock")]

    def test_the_environment_reaches_the_server(self, tmp_path, monkeypatch):
        captured: Dict[str, Any] = {}
        monkeypatch.setenv(serve.ALLOW_UIDS_ENV, "0,1000")
        monkeypatch.setattr(cg.access, "have_host_cgroup_view", lambda _root: True)
        monkeypatch.setattr(cg.access, "have_host_proc_view", lambda _root: True)

        class _FakeServer:
            def __init__(self, **kwargs):
                captured.update(kwargs)

            def serve_forever(self):
                return None

        monkeypatch.setattr(serve, "SessionServer", _FakeServer)
        assert cg.main(self._serve_argv(tmp_path)) == 0
        assert captured["allow_uids"] == [0, 1000]

    def test_an_unset_environment_is_no_allowlist(self, tmp_path, monkeypatch):
        captured: Dict[str, Any] = {}
        monkeypatch.delenv(serve.ALLOW_UIDS_ENV, raising=False)
        monkeypatch.setattr(cg.access, "have_host_cgroup_view", lambda _root: True)
        monkeypatch.setattr(cg.access, "have_host_proc_view", lambda _root: True)

        class _FakeServer:
            def __init__(self, **kwargs):
                captured.update(kwargs)

            def serve_forever(self):
                return None

        monkeypatch.setattr(serve, "SessionServer", _FakeServer)
        assert cg.main(self._serve_argv(tmp_path)) == 0
        assert captured["allow_uids"] is None

    def test_a_malformed_environment_refuses_to_start(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv(serve.ALLOW_UIDS_ENV, "1000,,1001")
        monkeypatch.setattr(cg.access, "have_host_cgroup_view", lambda _root: True)
        monkeypatch.setattr(cg.access, "have_host_proc_view", lambda _root: True)
        monkeypatch.setattr(
            serve, "SessionServer",
            lambda **kw: pytest.fail("the daemon must not start with a bad allowlist"),
        )
        with pytest.raises(SystemExit) as exc_info:
            cg.main(self._serve_argv(tmp_path))
        assert exc_info.value.code == 2
        assert "CGPROFILE_ALLOW_UIDS" in capsys.readouterr().err

    @pytest.mark.parametrize("value", [",,", "1000,"])
    def test_empty_environment_entries_refuse_to_start(
        self, tmp_path, monkeypatch, capsys, value
    ):
        monkeypatch.setenv(serve.ALLOW_UIDS_ENV, value)
        monkeypatch.setattr(cg.access, "have_host_cgroup_view", lambda _root: True)
        monkeypatch.setattr(cg.access, "have_host_proc_view", lambda _root: True)
        monkeypatch.setattr(
            serve, "SessionServer",
            lambda **kw: pytest.fail("the daemon must not start with a bad allowlist"),
        )
        with pytest.raises(SystemExit) as exc_info:
            cg.main(self._serve_argv(tmp_path))
        assert exc_info.value.code == 2
        assert "CGPROFILE_ALLOW_UIDS" in capsys.readouterr().err


# ── §8.1: the permissions asserted at every start ───────────────────────

class TestSocketPermissions:
    def _unbound(self, tmp_path, *, make_dir: bool = True, **kw):
        """Build the server WITHOUT binding, so a test may install its
        `os.chmod`/`os.stat`/`os.chown` doubles first — those patches hit
        the one shared `os` module, so anything the test itself does with
        them has to happen before they are in place."""
        socket_dir = tmp_path / "run"
        if make_dir:
            socket_dir.mkdir()
            os.chmod(socket_dir, 0o755)
        server = serve.SessionServer(
            sessions_dir=str(tmp_path / "sessions"),
            socket_path=str(socket_dir / "ctl.sock"), **kw,
        )
        return server, socket_dir

    def _bound(self, tmp_path, **kw):
        server, socket_dir = self._unbound(tmp_path, **kw)
        server._bind()
        return server, socket_dir

    def test_directory_becomes_0770_and_the_socket_0660(self, tmp_path):
        server, socket_dir = self._bound(tmp_path)
        try:
            assert (os.stat(socket_dir).st_mode & 0o777) == serve.SOCKET_DIR_MODE
            assert (os.stat(server.socket_path).st_mode & 0o777) == serve.SOCKET_MODE
        finally:
            server._close_socket()

    def test_the_socket_takes_the_directorys_own_gid(self, tmp_path, monkeypatch, capsys):
        # RW-35(b): the group is whatever the host's tmpfiles.d entry
        # (mdt-cgprofile.conf) put on the directory — never a name this
        # image resolves for itself. The chown itself needs root, so what is
        # asserted is the CALL, with the gid read from the directory.
        chowns: List[tuple] = []
        real_stat = os.stat

        def fake_stat(path, *a, **k):
            st = real_stat(path, *a, **k)
            if str(path).endswith("/run"):
                class _St:
                    st_gid = 4242
                    st_mode = st.st_mode
                return _St()
            return st

        monkeypatch.setattr(serve.os, "stat", fake_stat)
        monkeypatch.setattr(serve.os, "chown", lambda p, u, g: chowns.append((p, u, g)))
        server, _socket_dir = self._bound(tmp_path)
        try:
            assert chowns == [(server.socket_path, 0, 4242)]
            assert "root-only" not in capsys.readouterr().err
        finally:
            server._close_socket()

    def test_a_root_root_directory_logs_the_root_only_note(self, tmp_path, monkeypatch, capsys):
        real_stat = os.stat

        def fake_stat(path, *a, **k):
            st = real_stat(path, *a, **k)
            if str(path).endswith("/run"):
                class _St:
                    st_gid = 0
                    st_mode = st.st_mode
                return _St()
            return st

        monkeypatch.setattr(serve.os, "stat", fake_stat)
        monkeypatch.setattr(serve.os, "chown", lambda p, u, g: None)
        server, _socket_dir = self._bound(tmp_path)
        try:
            err = capsys.readouterr().err
            assert "socket carrier root-only until host-setup is installed" in err
            assert "mdt-cgprofile.conf" in err
            # …and the daemon is nevertheless listening: the exec carrier is
            # unaffected, so a missing host-setup is never fatal.
            assert server._sock is not None
        finally:
            server._close_socket()

    def test_permission_failures_never_stop_the_daemon(self, tmp_path, monkeypatch, capsys):
        server, _socket_dir = self._unbound(tmp_path)
        monkeypatch.setattr(
            serve.os, "chmod",
            lambda *a, **k: (_ for _ in ()).throw(PermissionError("read-only")),
        )
        monkeypatch.setattr(
            serve.os, "chown",
            lambda *a, **k: (_ for _ in ()).throw(PermissionError("not root")),
        )
        server._bind()
        try:
            assert server._sock is not None
            err = capsys.readouterr().err
            assert "could not chmod" in err
            assert "could not set" in err
        finally:
            server._close_socket()

    def test_an_unstattable_directory_is_survivable(self, tmp_path, monkeypatch, capsys):
        real_stat = os.stat
        # The directory is left for `_bind` to create (`os.makedirs` on an
        # EXISTING directory stats it, and would trip over the double
        # below before the code under test is reached).
        server, _socket_dir = self._unbound(tmp_path, make_dir=False)

        def fake_stat(path, *a, **k):
            if str(path).endswith("/run"):
                raise OSError("gone")
            return real_stat(path, *a, **k)

        monkeypatch.setattr(serve.os, "stat", fake_stat)
        server._bind()
        try:
            assert server._sock is not None
            assert "could not stat" in capsys.readouterr().err
        finally:
            server._close_socket()


# ── §8.6: version.transports ────────────────────────────────────────────

class TestVersionTransports:
    def test_shape_before_the_listener_is_bound(self, tmp_path):
        server = serve.SessionServer(
            sessions_dir=str(tmp_path / "sessions"), socket_path="/run/cgprofile/ctl.sock",
        )
        assert server.handle_version({})["transports"] == {
            "exec": True,
            "socket": {"path": "/run/cgprofile/ctl.sock", "listening": False,
                       "allow_uids": [], "peer_cred": True},
        }

    def test_allow_uids_are_disclosed_when_configured(self, tmp_path):
        server = serve.SessionServer(
            sessions_dir=str(tmp_path / "sessions"), allow_uids=[0, 1000],
        )
        assert server.handle_version({})["transports"]["socket"]["allow_uids"] == [0, 1000]


# ── goldens + carrier parity ────────────────────────────────────────────

def _golden(name: str) -> Path:
    return SOCKET_FIXTURES / name


def _dump(doc: Any) -> str:
    return json.dumps(doc, indent=2) + "\n"


def _check_golden(name: str, doc: Any) -> None:
    path = _golden(name)
    text = _dump(doc)
    if os.environ.get(REGEN_ENV) == "1":  # pragma: no cover - maintenance path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return
    assert path.is_file(), f"missing golden {path} (regenerate with {REGEN_ENV}=1)"
    assert path.read_text() == text, f"{path} differs from the live document"


def test_socket_goldens_are_the_live_documents(scenario):
    """Every verb's request and response, frozen byte-for-byte as
    `fixtures/rg55/socket/<verb>-{request,response}.json` — the bytes P5
    codes the run-gate socket client against."""
    for verb in VERBS:
        docs = scenario.socket_docs[verb]
        _check_golden(f"{verb}-request.json", docs["request"])
        _check_golden(f"{verb}-response.json", docs["response"])


def test_every_verb_is_covered_by_a_golden():
    # A verb added to the dispatcher without a golden is a hole in P5's
    # fixture set — this is the test that notices.
    server_verbs = {"version", "start", "status", "host", "stop", "report", "gc"}
    assert set(VERBS) == server_verbs
    # C7: `watch` is a verb the daemon answers but NOT through `_dispatch`
    # (it streams, `_handle_connection` hands it to its own thread), so it
    # is listed separately and covered by its own goldens.
    assert set(STREAMING) == set(serve.STREAMING_VERBS)
    for verb in VERBS + STREAMING:
        assert _golden(f"{verb}-request.json").is_file()
        assert _golden(f"{verb}-response.json").is_file()


def test_watch_request_golden_is_unchanged_by_its_implementation(scenario):
    """C6 froze this REQUEST ahead of the implementation so P5 could write
    its client against it. C7 must answer exactly that request — if
    implementing the verb had changed its request shape, every consumer
    written against the frozen bytes would have been broken by it."""
    expected = {"verb": "watch",
                "args": {"session": SESSION_ID, "watch_interval": 30},
                "contract": 1}
    assert json.loads(_golden("watch-request.json").read_text()) == expected
    assert scenario.watch_doc["request"] == expected


def test_the_watch_stream_golden_is_the_live_document(scenario):
    """§8.2's line protocol, frozen as the ARRAY of lines one connection
    carried: a `reading` on attach, a second `reading` when `stop` wakes the
    stream, the `verdict` the state change earned, and exactly one `end`."""
    lines = scenario.watch_doc["response"]
    assert [line["event"] for line in lines] == ["reading", "reading", "verdict", "end"]
    assert lines[-1]["reason"] == "stopped"
    assert all(line["session"] == SESSION_ID for line in lines)
    assert lines[0]["placement"] is None  # §8.3's block lands with C8
    assert lines[0]["liveness"]["stream"] is None  # no --progress-stream here
    assert lines[2]["watch"]["state"] == "stalled"
    assert lines[2]["watch"]["verdict"] == "reported"  # default policy: no kill
    _check_golden("watch-request.json", scenario.watch_doc["request"])
    _check_golden("watch-response.json", scenario.watch_doc["response"])


def test_the_v1_1_summary_is_frozen(scenario):
    """§8.7's Summary, byte-frozen with its two new blocks. It lives here
    rather than beside `summary-v1.json` because this scenario injects every
    clock the document depends on; the lifecycle harness that reproduces the
    v1 Summary runs its frames at machine speed, so `watch.readings` there
    is a property of the host."""
    summary_doc = scenario.socket_docs["stop"]["response"]["summary"]
    assert summary_doc["watch"]["state"] == "stalled"
    assert summary_doc["watch"]["verdict"] == "reported"
    assert summary_doc["liveness"]["idle_for_seconds"] == 10000.0
    path = RG55_FIXTURES / "summary-v1.1.json"
    text = _dump(summary_doc)
    if os.environ.get(REGEN_ENV) == "1":  # pragma: no cover - maintenance path
        path.write_text(text)
    else:
        assert path.is_file(), f"missing golden {path} (regenerate with {REGEN_ENV}=1)"
        assert path.read_text() == text, f"{path} differs from the live document"


def test_each_watch_line_shape_is_frozen_on_its_own(scenario):
    """One fixture per new SHAPE (contract §8's own rule for the producer),
    beside the stream they came from: `fixtures/rg55/watch-{reading,verdict,
    end}.json` are the three §8.2 line types P5 has to parse."""
    lines = scenario.watch_doc["response"]
    for name, line in (("watch-reading.json", lines[0]),
                       ("watch-verdict.json", lines[2]),
                       ("watch-end.json", lines[3])):
        path = RG55_FIXTURES / name
        text = _dump(line)
        if os.environ.get(REGEN_ENV) == "1":  # pragma: no cover - maintenance path
            path.write_text(text)
            continue
        assert path.is_file(), f"missing golden {path} (regenerate with {REGEN_ENV}=1)"
        assert path.read_text() == text, f"{path} differs from the live document"


def test_watch_asked_of_the_single_response_path_is_not_streaming(tmp_path):
    """§8.8's `not-streaming`. A caller that reached `_dispatch` is waiting
    for exactly one object; the verb writes many. Named refusal, never
    "unknown verb" (which would tell a consumer the daemon is too old)."""
    server = serve.SessionServer(sessions_dir=str(tmp_path / "sessions"))
    resp = server._dispatch(json.loads(_golden("watch-request.json").read_text()))
    assert resp["error"]["code"] == "not-streaming"
    assert "watch" in resp["error"]["message"]


def test_both_carriers_carry_identical_documents(scenario):
    """§8.1 rule 1: "every verb, response, error code and the `contract`
    field are IDENTICAL on both carriers — a consumer may diff them". This
    is that diff, against ONE serve loop."""
    assert set(scenario.socket_docs) == set(VERBS)
    assert set(scenario.exec_docs) == set(VERBS)
    for verb in VERBS:
        over_socket = scenario.socket_docs[verb]
        over_exec = _rewrite_second_session(scenario.exec_docs[verb])
        assert over_exec["request"] == over_socket["request"], f"{verb}: request differs"
        assert over_exec["response"] == over_socket["response"], f"{verb}: response differs"


def test_the_scenario_actually_exercised_a_session(scenario):
    # Guards the parity test against passing vacuously (e.g. if `start` had
    # failed, every later verb would have agreed on the same error).
    start = scenario.socket_docs["start"]["response"]
    assert start["ok"] is True and start["reused"] is False
    status = scenario.socket_docs["status"]["response"]
    assert [s["session"] for s in status["sessions"]] == [SESSION_ID]
    assert status["sessions"][0]["live"]["samples"] == 1
    stop = scenario.socket_docs["stop"]["response"]
    assert stop["already_stopped"] is False
    assert stop["summary"]["schema"] == 1
    assert scenario.socket_docs["report"]["response"]["path"].endswith("/report.html")
