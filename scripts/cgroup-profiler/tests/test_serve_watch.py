"""The watch role inside the daemon — RG55-INTERFACE-CONTRACT.md §8.2/§8.4,
RG-55 P6 C7 (CP-8).

`test_liveness.py` owns the judgement (every state transition, on a fake
clock). This file owns the WIRING, and three claims that only a real daemon
can make:

* **the policy is refused before anything exists** — `bad-policy` means
  exit 2 and NO session in the registry, not a session running under a
  policy the daemon had to guess at;
* **`--on-stall kill` never invents a boundary** — an unplaced shared-scope
  lane is reported without signaling a numeric host PID; the same lane under
  `report` is still alive at the end of the test and its verdict says
  `reported`. Exact cgroup-kill writes are checked in `test_serve_placement.py`;
* **`watch` really streams** — over the socket carrier the connection stays
  open and carries `reading` lines, a `verdict` on the state change and
  exactly one `end`, while OTHER verbs keep being answered on the same
  daemon (the accept loop is serial: a streaming verb that blocked it would
  deadlock the very `stop` that ends the session being watched).
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import pytest

import cgprofile as cg
from lib import damon as damon_mod, liveness, serve
from lib import store as store_mod, summary as summary_mod
from tests.conftest import cgroup_files, write_cgroup

CONTAINER_ID = "c" * 64
SESSION_ID = "s-20260912T101500Z-9f01"
TOKEN = "rg55-watch-token-01"
EPOCH_START = datetime(2026, 9, 12, 10, 15, 0, tzinfo=timezone.utc).timestamp()
META = {
    "lane": "rg55-watch-lane", "project": "run-gate-project", "worktree": "/workspaces/vbpub",
    "commit": "0123456789abcdef0123456789abcdef01234567", "run_gate_revision": 41,
    "kind": "command", "expected": None,
}
SCOPE_CGROUP = f"dev.slice/dev-background.slice/docker-{CONTAINER_ID}.scope"


def _fake_cgroup_root(tmp_path: Path, *, procs: str = "") -> Path:
    root = tmp_path / "cgroup"
    write_cgroup(root, "", cgroup_files())
    write_cgroup(root, "dev.slice", cgroup_files())
    write_cgroup(root, "dev.slice/dev-background.slice", cgroup_files())
    files = dict(cgroup_files(memory_current=500 * 1024 * 1024))
    files["cgroup.procs"] = procs
    files["io.stat"] = "8:0 rbytes=0 wbytes=0 rios=0 wios=0"
    write_cgroup(root, SCOPE_CGROUP, files)
    return root


def _fake_proc(tmp_path: Path, *, full_avg10: float = 0.0) -> Path:
    # CP-10: distinct proc dirs (one per `tmp_path`-scoped test) so a caller
    # asking for a raised `full_avg10` (the memory-pressure regression test)
    # never collides with a plain zero-pressure one built for the same test.
    proc = tmp_path / f"proc-psi-{full_avg10:g}" if full_avg10 else tmp_path / "proc"
    proc.mkdir(exist_ok=True)
    (proc / "loadavg").write_text("1.0 1.0 1.0 1/100 999\n")
    (proc / "meminfo").write_text("MemTotal: 1000 kB\nMemAvailable: 500 kB\n")
    pressure = proc / "pressure"
    pressure.mkdir(exist_ok=True)
    for name in ("cpu", "io"):
        (pressure / name).write_text(
            "some avg10=0.00 avg60=0.00 avg300=0.00 total=0\n"
            "full avg10=0.00 avg60=0.00 avg300=0.00 total=0\n"
        )
    (pressure / "memory").write_text(
        "some avg10=0.00 avg60=0.00 avg300=0.00 total=0\n"
        f"full avg10={full_avg10:.2f} avg60=0.00 avg300=0.00 total=0\n"
    )
    return proc


def _start_args(**overrides: Any) -> Dict[str, Any]:
    args = {
        "target": f"containerid:{CONTAINER_ID}", "scope": "container-shared",
        "token": None, "damon": "off", "interval": 1.0, "meta": META,
        "progress_stream": None, "idle_bound": None, "ceiling": None, "on_stall": None,
    }
    args.update(overrides)
    return args


def _server(tmp_path: Path, *, cgroup_root: Path, proc_root: str, **kw: Any) -> serve.SessionServer:
    return serve.SessionServer(
        sessions_dir=str(tmp_path / "sessions"), socket_path=str(tmp_path / "ctl.sock"),
        cgroup_root=str(cgroup_root), proc_root=proc_root, clock=lambda: EPOCH_START,
        session_id_fn=lambda: SESSION_ID, accept_timeout=0.05, **kw,
    )


# ── §8.4/§8.8: `bad-policy` refuses BEFORE the session exists ───────────

class TestPolicyRefusal:
    @pytest.mark.parametrize("overrides, fragment", [
        ({"idle_bound": "soonish"}, "--idle-bound"),
        ({"ceiling": "-4"}, "--ceiling"),
        ({"on_stall": "maim"}, "--on-stall"),
        ({"progress_stream": "progress.ndjson"}, "absolute"),
        ({"on_stall": "kill", "token": TOKEN}, "--place"),
        ({"on_stall": "kill", "place": True}, "--token"),
    ])
    def test_an_unparsable_policy_is_bad_policy_and_starts_nothing(
        self, tmp_path, overrides, fragment
    ):
        server = _server(
            tmp_path, cgroup_root=_fake_cgroup_root(tmp_path),
            proc_root=str(_fake_proc(tmp_path)),
        )
        resp = server._dispatch({"verb": "start", "args": _start_args(**overrides),
                                 "contract": 1})
        assert resp["ok"] is False
        assert resp["error"]["code"] == "bad-policy"
        assert fragment in resp["error"]["message"]
        # "the session is NOT started" (§8.8) — the registry is the oracle,
        # not the response.
        assert server._sessions == {}
        assert server._by_target == {}

    def test_a_valid_policy_reaches_the_session_and_status(self, tmp_path):
        server = _server(
            tmp_path, cgroup_root=_fake_cgroup_root(tmp_path),
            proc_root=str(_fake_proc(tmp_path)), sampler_sleep=lambda _s: time.sleep(0.01),
        )
        resp = server._dispatch({"verb": "start", "args": _start_args(
            token=TOKEN, progress_stream="/run/lane/progress.ndjson", idle_bound="auto",
            ceiling=1800, on_stall="report",
        ), "contract": 1})
        assert resp["ok"] is True
        try:
            status = server._dispatch({"verb": "status", "args": {"session": SESSION_ID},
                                       "contract": 1})
            watch = status["session"]["watch"]
            assert watch["policy"] == {
                "idle_bound_s": 300.0, "ceiling_s": 1800.0, "on_stall": "report",
                "progress_stream": "/run/lane/progress.ndjson",
            }
            assert watch["state"] == "ok" and watch["verdict"] == "none"
            assert set(status["session"]["liveness"]) == {
                "last_activity_at", "idle_for_seconds", "cpu_seconds", "cpu_seconds_recent",
                "io_bytes", "stream", "paused_for_seconds", "pause_reason",
            }
        finally:
            server._dispatch({"verb": "stop", "args": {"session": SESSION_ID}, "contract": 1})

    def test_kill_with_a_refused_placement_starts_no_session(self, tmp_path):
        server = _server(
            tmp_path, cgroup_root=_fake_cgroup_root(tmp_path),
            proc_root=str(_fake_proc(tmp_path)),
        )
        resp = server._dispatch({"verb": "start", "args": _start_args(
            token=TOKEN, place=True, on_stall="kill",
        ), "contract": 1})
        assert resp["ok"] is False and resp["error"]["code"] == "bad-policy"
        assert "verified placement leaf" in resp["error"]["message"]
        assert server._sessions == {} and server._by_target == {}
        rejected = tmp_path / "sessions" / SESSION_ID / "manifest.json"
        assert json.loads(rejected.read_text())[
            "aborted_reason"
        ] == "required-stall-kill-placement-refused"

    def test_the_summary_carries_liveness_and_watch(self, tmp_path):
        server = _server(
            tmp_path, cgroup_root=_fake_cgroup_root(tmp_path),
            proc_root=str(_fake_proc(tmp_path)), sampler_sleep=lambda _s: time.sleep(0.01),
        )
        server._dispatch({"verb": "start", "args": _start_args(), "contract": 1})
        stop = server._dispatch({"verb": "stop", "args": {"session": SESSION_ID}, "contract": 1})
        # §8.7: additive Summary keys, and the same block `status` served.
        assert stop["summary"]["watch"]["state"] == "ok"
        assert stop["summary"]["watch"]["policy"]["on_stall"] == "report"
        assert stop["summary"]["liveness"]["pause_reason"] is None
        on_disk = json.loads(
            (tmp_path / "sessions" / SESSION_ID / "summary.json").read_text()
        )
        assert on_disk["watch"] == stop["summary"]["watch"]


# ── §8.4: the real thing — a real subtree, really killed ────────────────

@pytest.mark.skipif(not os.path.isdir("/proc/self"), reason="needs a real /proc")
class TestRealSubtreeEnforcement:
    """A REAL `sleep` process carrying a REAL token in its environment,
    resolved through the REAL `/proc` by the same `SubtreeResolver` the
    daemon uses in production. Only the cgroup tree is faked (this test
    cannot create a cgroup), and `cgroup.procs` there names the real pid —
    which is exactly what the kernel would put in it.

    CP-10: `proc_root="/proc"` is required for that real pid/environ walk,
    but it must NOT also be what liveness reads for host memory pressure
    (§8.4's pause condition) — this HOST's ambient PSI is whatever the rest
    of the estate happens to be doing at the moment this test runs (other
    gate lanes, mutation runs, …), and `full avg10 > 5` pauses the idle
    clock outright, so these `on_stall` assertions would intermittently
    time out waiting for a `stalled` verdict that real pressure is holding
    off — nothing to do with these two tests' declaration order relative to
    `test_serve_socket_carrier.py::TestPeerCredentials` (see the CP-10
    backlog row and the REPORT's reproduction table for the two false
    correlations that first looked like an ordering bug). `host_proc_root`
    decouples the two: real `/proc` for pids, a fixed zero-pressure fake
    for the host reading liveness pauses on."""

    def _run(self, tmp_path: Path, *, on_stall: str) -> Dict[str, Any]:
        env = dict(os.environ, RUN_GATE_PROFILE_SESSION=TOKEN)
        lane = subprocess.Popen(["sleep", "30"], env=env)
        try:
            root = _fake_cgroup_root(tmp_path, procs=str(lane.pid))
            server = _server(
                tmp_path, cgroup_root=root, proc_root="/proc",
                host_proc_root=str(_fake_proc(tmp_path)),
                sampler_sleep=lambda _s: time.sleep(0.05),
            )
            resp = server._dispatch({"verb": "start", "args": _start_args(
                token=TOKEN, interval=0.25, idle_bound=0.5, on_stall=on_stall,
            ), "contract": 1})
            assert resp["ok"] is True, resp
            assert resp["target"]["token"] == TOKEN
            deadline = time.monotonic() + 30.0
            sess = server._sessions[SESSION_ID]
            while time.monotonic() < deadline:
                if sess.watch.state != "ok":
                    break
                time.sleep(0.05)
            # The lane really was found: liveness watched THE SUBTREE, not
            # the cgroup (scope container-shared).
            assert sess.subtree_resolver is not None
            assert lane.pid in sess.subtree_resolver.current_pids
            state = sess.watch.state
            verdict = sess.watch.verdict
            reason = sess.watch.reason
            # Give the signal a moment to land before asking whether the
            # process is gone.
            try:
                lane.wait(timeout=10.0)
                returncode = lane.returncode
            except subprocess.TimeoutExpired:
                returncode = None
            # An ENFORCED kill ends the lane, so it must end the SESSION too
            # (`_finalize_after_kill`, called from `_enforce_stall_kill` on
            # this session's own sampler thread) — poll rather than read
            # once: the tracker flips `state`/`verdict` under `sess.lock`
            # one step before `_enforce_stall_kill` (and therefore
            # finalize) runs, so this thread can observe the new state a
            # hair before finalize completes on the sampler thread.
            finalize_deadline = time.monotonic() + 5.0
            while time.monotonic() < finalize_deadline and not sess.finished:
                time.sleep(0.02)
            finished_before_stop = sess.finished
            server._dispatch({"verb": "stop", "args": {"session": SESSION_ID}, "contract": 1})
            return {"state": state, "verdict": verdict, "reason": reason,
                    "returncode": returncode, "finished_before_stop": finished_before_stop}
        finally:
            if lane.poll() is None:
                lane.kill()
                lane.wait(timeout=10.0)

    def test_the_same_lane_under_report_is_only_reported(self, tmp_path):
        out = self._run(tmp_path, on_stall="report")
        assert out["state"] == "stalled"
        assert out["verdict"] == "reported"
        assert "cgroup.kill" not in (out["reason"] or "")
        # Still running when the watch gave its verdict — `report` killed
        # nothing (the fixture's own `finally` reaps it).
        assert out["returncode"] is None
        # `report` never enforces, so the session must NOT auto-finalize —
        # only an ENFORCED kill (`record_kill`, never `record_kill_refused`
        # or a bare `reported` verdict) does that.
        assert out["finished_before_stop"] is False

    def test_unreachable_pid_is_reported_and_the_lane_remains_live(
        self, tmp_path, monkeypatch
    ):
        """Host PIDs are observation-only in the private PID namespace;
        without placement the daemon must report, never signal."""
        ready_read, ready_write = os.pipe()
        try:
            release_read, release_write = os.pipe()
        except BaseException:
            os.close(ready_read)
            os.close(ready_write)
            raise
        lane = None
        server = None
        parked = threading.Event()
        try:
            lane = subprocess.Popen(
                [
                    sys.executable, "-c",
                    f"import os; os.write({ready_write}, b'R'); os.read({release_read}, 1)",
                ],
                env=dict(os.environ, RUN_GATE_PROFILE_SESSION=TOKEN),
                pass_fds=(ready_write, release_read),
            )
            os.close(ready_write)
            ready_write = None
            os.close(release_read)
            release_read = None
            # Popen's exec handshake does not mean the child has run yet.
            # Wait for an exact readiness byte, then keep the child blocked
            # on the parent-controlled pipe through the daemon's initial
            # /proc scan and the no-signal assertions. This makes PID
            # visibility independent of how long the parent is descheduled.
            assert os.read(ready_read, 1) == b"R", "token child exited before readiness"
            server = _server(
                tmp_path, cgroup_root=_fake_cgroup_root(tmp_path, procs=str(lane.pid)),
                proc_root="/proc", host_proc_root=str(_fake_proc(tmp_path)),
                sampler_sleep=lambda _seconds: parked.wait(timeout=60),
            )
            start = server._dispatch({
                "verb": "start", "args": _start_args(
                    token=TOKEN, idle_bound=1000, on_stall="report"
                ), "contract": 1,
            })
            assert start["ok"] is True
            sess = server._sessions[SESSION_ID]
            assert lane.pid in sess.subtree_resolver.current_pids
            sess.stop_event.set()
            parked.set()
            sess.thread.join(timeout=10)
            assert not sess.thread.is_alive()
            sess.watch.policy = liveness.parse_policy({"on_stall": "kill"})
            _force_stalled(sess)

            def forbidden_signal(*_args):
                pytest.fail("daemon must never signal a numeric host PID")

            with monkeypatch.context() as patch:
                patch.setattr(serve.os, "kill", forbidden_signal)
                server._enforce_stall_kill(sess, [lane.pid])

            assert lane.poll() is None
            assert sess.watch.state == "stalled"
            assert sess.watch.verdict == "reported"
            assert "kill-refused:unplaced-token-subtree" in sess.watch.reason
            assert sess.finished is False
        finally:
            parked.set()
            try:
                if server is not None and SESSION_ID in server._sessions:
                    server._dispatch({"verb": "stop", "args": {"session": SESSION_ID}, "contract": 1})
            finally:
                try:
                    if lane is not None and lane.poll() is None:
                        try:
                            os.write(release_write, b"X")
                        except BrokenPipeError:
                            pass
                        try:
                            lane.wait(timeout=10)
                        except subprocess.TimeoutExpired:
                            lane.kill()
                            lane.wait(timeout=10)
                finally:
                    for fd in (ready_read, ready_write, release_read, release_write):
                        if fd is not None:
                            os.close(fd)

    def test_a_progress_stream_is_read_through_proc_root(self, tmp_path):
        # The lane's stream path is read as `/proc/<pid>/root/<path>`; in
        # this test the lane shares the daemon's mount namespace, so that
        # resolves back to the real file. Production uses the same explicit
        # process-root view while keeping the daemon's PID namespace private.
        stream = tmp_path / "progress.ndjson"
        stream.write_text(
            json.dumps({"event": "plan", "expect_next_event_within_s": 45}) + "\n"
        )
        env = dict(os.environ, RUN_GATE_PROFILE_SESSION=TOKEN)
        lane = subprocess.Popen(["sleep", "30"], env=env)
        try:
            server = _server(
                tmp_path, cgroup_root=_fake_cgroup_root(tmp_path, procs=str(lane.pid)),
                proc_root="/proc", host_proc_root=str(_fake_proc(tmp_path)),
                sampler_sleep=lambda _s: time.sleep(0.05),
            )
            server._dispatch({"verb": "start", "args": _start_args(
                token=TOKEN, interval=0.25, progress_stream=str(stream),
            ), "contract": 1})
            sess = server._sessions[SESSION_ID]
            deadline = time.monotonic() + 30.0
            while time.monotonic() < deadline and sess.watch.stream is None:
                time.sleep(0.05)
            block = sess.watch.liveness_block()
            assert block["stream"]["path"] == str(stream)
            assert block["stream"]["last_event"] == "plan"
            # D-22: the hint the PRODUCER published drives the idle bound.
            assert block["stream"]["cadence_hint_seconds"] == 45.0
            assert sess.watch.watch_block()["policy"]["idle_bound_s"] == 300.0
            server._dispatch({"verb": "stop", "args": {"session": SESSION_ID}, "contract": 1})
        finally:
            lane.kill()
            lane.wait(timeout=10.0)

    def test_host_pressure_is_read_from_host_proc_root_not_the_real_proc(self, tmp_path):
        # CP-10 regression: `host_proc_root` — NOT `proc_root` (real `/proc`,
        # required here for the pid walk) — is what liveness's pause check
        # reads. Proven the same way the bug manifested: `host_proc_root`
        # claims severe memory pressure (`full avg10=99`, far over the 5.0
        # pause threshold) that this HOST's real `/proc` almost certainly is
        # not reporting at the moment this test runs. A silent lane with a
        # tiny `idle_bound` would reach `stalled` within a couple of ticks
        # if the server were still (bug) reading pressure from `proc_root`;
        # under the fix it stays `ok`/paused for as long as the fake host
        # stays hot, which is exactly what makes `TestRealSubtreeEnforcement`
        # safe to run under this estate's own real, fluctuating memory PSI.
        env = dict(os.environ, RUN_GATE_PROFILE_SESSION=TOKEN)
        lane = subprocess.Popen(["sleep", "10"], env=env)
        try:
            hot_proc = _fake_proc(tmp_path, full_avg10=99.0)
            server = _server(
                tmp_path, cgroup_root=_fake_cgroup_root(tmp_path, procs=str(lane.pid)),
                proc_root="/proc", host_proc_root=str(hot_proc),
                sampler_sleep=lambda _s: time.sleep(0.05),
            )
            resp = server._dispatch({"verb": "start", "args": _start_args(
                token=TOKEN, interval=0.1, idle_bound=0.2, on_stall="report",
            ), "contract": 1})
            assert resp["ok"] is True, resp
            sess = server._sessions[SESSION_ID]
            # The watcher observes on the DISCOVERY cadence
            # (`DISCOVERY_INTERVAL_SECONDS = 2.0`, `lib/serve.py`'s own
            # `_on_session_sample`), not the sample interval above — wait
            # for at least three discovery ticks (well past the tiny
            # `idle_bound`, several `idle_bound`s' worth of real wall-clock
            # time if pausing were not in effect) rather than a fixed sleep
            # shorter than one discovery cycle.
            deadline = time.monotonic() + 15.0
            while time.monotonic() < deadline and sess.watch.readings < 3:
                time.sleep(0.1)
            assert sess.watch.readings >= 3, "no discovery tick landed in time"
            assert sess.watch.state == "ok"
            assert sess.watch.pause_reason == "host-psi"
            assert sess.watch.paused_for_seconds > 0.0
            server._dispatch({"verb": "stop", "args": {"session": SESSION_ID}, "contract": 1})
        finally:
            if lane.poll() is None:
                lane.kill()
                lane.wait(timeout=10.0)


class TestKillTargets:
    """Which pids `--on-stall kill` may signal (`_kill_targets`) — the one
    piece of the kill path that must be right BEFORE anything is signalled."""

    def _session(self, tmp_path: Optional[Path] = None, **kw: Any) -> serve._Session:
        defaults = dict(
            session_id=SESSION_ID, scope="container-shared", container_id=CONTAINER_ID,
            cgroup="/x", slice_name=None, slice_cgroup=None, token=None, interval=1.0,
            meta={}, started_at="2026-09-12T10:15:00Z", baseline_memory_bytes=None,
            pids_at_start=0, host_snapshot={}, rundir=None, summary_acc=None,
            subtree_resolver=None, damon_session=None, damon_status="off",
            damon_requested_on=False, damon_unavailable_reason=None,
        )
        if tmp_path is not None:
            # `_kill_targets`-only tests below construct a bare session with
            # no rundir/summary_acc (they never reach `_finalize_after_kill`
            # — no `on_stall=kill` policy is attached). The two tests that
            # DO call the full `_enforce_stall_kill` need a session real
            # enough to survive the finalize it now triggers on a genuine
            # kill (`_finalize_session_locked` reads both), so those pass
            # `tmp_path` to get one — the same two objects
            # `_create_session_locked` builds for a real session, just
            # fed no samples (finalize's own "stopped before a tick
            # landed" fallback in `_finalize_session_locked` covers that).
            defaults["rundir"] = store_mod.RunDir(str(tmp_path), run_id=SESSION_ID)
            defaults["summary_acc"] = summary_mod.SummaryAccumulator(
                session=SESSION_ID, daemon_name="cgprofile-host-daemon",
                daemon_version=serve.CGPROFILE_VERSION,
                scope=kw.get("scope", defaults["scope"]),
                started_at=defaults["started_at"], interval_seconds=defaults["interval"],
                container_id=CONTAINER_ID, cgroup=defaults["cgroup"], token=kw.get("token"),
            )
        defaults.update(kw)
        return serve._Session(**defaults)

    def test_a_token_subtree_is_killable(self):
        sess = self._session(token=TOKEN)
        sess.placement = SimpleNamespace(placed=True, error=None)
        pids, refusal = serve.SessionServer._kill_targets(sess, [7, 9])
        assert pids == [7, 9] and refusal is None

    def test_scope_container_without_a_token_is_killable(self):
        # An ephemeral gate container: everything in that cgroup IS the lane.
        pids, refusal = serve.SessionServer._kill_targets(
            self._session(scope="container"), [7]
        )
        assert pids == [7] and refusal is None

    def test_shared_scope_without_a_token_is_refused(self):
        # The cgroup here is the whole devcontainer — the IDE, the agents and
        # the caller. The daemon refuses rather than killing the estate.
        pids, refusal = serve.SessionServer._kill_targets(self._session(), [7, 9])
        assert pids == [] and refusal == "no-token-in-shared-scope"

    def test_unplaced_token_tree_is_not_a_signal_target(self):
        pids, refusal = serve.SessionServer._kill_targets(
            self._session(token=TOKEN), [7, 9]
        )
        assert pids == [] and refusal == "unplaced-token-subtree"

    def test_the_refusal_is_recorded_on_the_verdict(self, tmp_path):
        server = _server(
            tmp_path, cgroup_root=_fake_cgroup_root(tmp_path),
            proc_root=str(_fake_proc(tmp_path)),
        )
        sess = self._session(tmp_path)
        sess.watch = liveness.LivenessTracker(
            liveness.parse_policy({"on_stall": "kill"}),
            started_at="2026-09-12T10:15:00Z",
        )
        _force_stalled(sess)
        server._enforce_stall_kill(sess, [7, 9])
        assert sess.watch.verdict == "reported"  # never "killed": nothing died
        assert "kill-refused:no-token-in-shared-scope" in sess.watch.reason

    def test_finalize_after_kill_is_a_no_op_once_the_session_already_finished(
        self, tmp_path
    ):
        """`_finalize_after_kill`'s own `if not sess.finished` guard: the
        live call graph only ever reaches it once per session (`record_kill`
        sets `tracker.enforced = True`, which turns `kill_requested` off),
        but it is the same defensive shape `_session_loop`'s crash path
        already uses, and it is what keeps a hypothetical second enforcement
        call from re-finalizing (double-writing `summary.json`, re-running
        retention) rather than a documented, tested no-op."""
        server = _server(
            tmp_path, cgroup_root=_fake_cgroup_root(tmp_path),
            proc_root=str(_fake_proc(tmp_path)),
        )
        sess = self._session(tmp_path, token=TOKEN)
        sess.watch = liveness.LivenessTracker(
            liveness.parse_policy({"on_stall": "kill"}), started_at="2026-09-12T10:15:00Z",
        )
        server._finalize_after_kill(sess)
        assert sess.finished is True
        first_summary = sess.summary_doc
        server._finalize_after_kill(sess)
        assert sess.finished is True
        assert sess.summary_doc is first_summary  # not re-finalized


# ── §8.2: the streaming verb ────────────────────────────────────────────

class _LineReader:
    """Reads newline-delimited JSON off a socket with a deadline, so a
    hung stream fails the test instead of hanging the suite."""

    def __init__(self, sock: socket.socket) -> None:
        self.sock = sock
        self.buffer = b""

    def next(self, timeout: float = 15.0) -> Dict[str, Any]:
        deadline = time.monotonic() + timeout
        while b"\n" not in self.buffer:
            self.sock.settimeout(max(0.05, deadline - time.monotonic()))
            chunk = self.sock.recv(65536)
            if not chunk:
                raise AssertionError("stream closed with no further line")
            self.buffer += chunk
        raw, self.buffer = self.buffer.split(b"\n", 1)
        return json.loads(raw.decode("utf-8"))

    def drain(self, timeout: float = 15.0) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        while True:
            try:
                out.append(self.next(timeout))
            except AssertionError:
                return out


@pytest.fixture
def streaming_server(tmp_path, monkeypatch):
    monkeypatch.setattr(damon_mod, "available", lambda: True)
    server = _server(
        tmp_path, cgroup_root=_fake_cgroup_root(tmp_path),
        proc_root=str(_fake_proc(tmp_path)), sampler_sleep=lambda _s: time.sleep(0.01),
        # The §8.2 interval floor is 5 s; a test that waited it out twice
        # would take 10 s to prove a line protocol. This is the documented
        # seam (`SessionServer.watch_wait`) and the ONLY thing it changes.
        watch_wait=lambda event, timeout: event.wait(0.02),
    )
    thread = threading.Thread(target=server._accept_loop, daemon=True)
    thread.start()
    deadline = time.monotonic() + 5.0
    while not os.path.exists(server.socket_path) and time.monotonic() < deadline:
        time.sleep(0.01)
    try:
        yield server
    finally:
        server.request_shutdown()
        thread.join(timeout=5.0)


def _connect(server: serve.SessionServer, req: Dict[str, Any]) -> _LineReader:
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.connect(server.socket_path)
    client.sendall((json.dumps(req) + "\n").encode("utf-8"))
    return _LineReader(client)


def _watch_request(session: str = SESSION_ID, **args: Any) -> Dict[str, Any]:
    return {"verb": "watch", "args": dict({"session": session, "watch_interval": 30}, **args),
            "contract": 1}


def _force_stalled(sess: serve._Session) -> None:
    """Drive the session's own tracker past its idle bound on a synthetic
    reading. The transitions themselves are `test_liveness.py`'s subject;
    what this file needs is a REAL state change on a REAL session, on
    demand, so the stream's `verdict`-on-change rule can be observed."""
    with sess.lock:
        sess.watch.observe(liveness.LivenessSample(
            mono=0.0, at="2026-09-12T10:15:00Z", elapsed_seconds=0.0,
        ))
        sess.watch.observe(liveness.LivenessSample(
            mono=10_000.0, at="2026-09-12T12:00:00Z", elapsed_seconds=10_000.0,
        ))
    assert sess.watch.state == "stalled"


class TestWatchStream:
    def test_readings_a_verdict_on_change_and_exactly_one_end(self, streaming_server):
        server = streaming_server
        assert server._dispatch({"verb": "start", "args": _start_args(), "contract": 1})["ok"]
        reader = _connect(server, _watch_request())

        first = reader.next()
        assert first["event"] == "reading"
        assert first["contract"] == 1 and first["session"] == SESSION_ID
        assert set(first) == {"contract", "event", "session", "at", "elapsed_seconds",
                              "live", "liveness", "placement"}
        assert first["placement"] is None  # §8.3 lands with C8
        assert first["liveness"]["pause_reason"] is None

        _force_stalled(server._sessions[SESSION_ID])
        lines = []
        while True:
            line = reader.next()
            lines.append(line)
            if line["event"] == "verdict":
                break
            assert line["event"] == "reading"
        verdict = lines[-1]
        # §8.2 lists exactly these keys on a verdict line.
        assert set(verdict["watch"]) == {"state", "verdict", "reason", "readings"}
        assert verdict["watch"]["state"] == "stalled"
        assert verdict["watch"]["verdict"] == "reported"

        server._dispatch({"verb": "stop", "args": {"session": SESSION_ID}, "contract": 1})
        rest = reader.drain()
        assert [line["event"] for line in rest].count("end") == 1
        assert rest[-1]["event"] == "end"
        assert rest[-1]["reason"] == "stopped"
        # No second verdict: the state never changed again (§8.2 "verdict
        # lines only on a state change").
        assert [line for line in rest if line["event"] == "verdict"] == []
        readings = 1 + len([line for line in lines + rest if line["event"] == "reading"])
        assert readings >= 2

    def test_the_daemon_keeps_answering_other_verbs_while_a_watch_streams(
        self, streaming_server
    ):
        server = streaming_server
        server._dispatch({"verb": "start", "args": _start_args(), "contract": 1})
        reader = _connect(server, _watch_request())
        assert reader.next()["event"] == "reading"
        # The accept loop is serial — if the stream were handled inline this
        # second connection would never be accepted at all.
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(10.0)
            client.connect(server.socket_path)
            client.sendall(
                (json.dumps({"verb": "version", "args": {}, "contract": 1}) + "\n").encode()
            )
            data = b""
            while not data.endswith(b"\n"):
                data += client.recv(65536)
        assert json.loads(data.decode("utf-8"))["ok"] is True
        server._dispatch({"verb": "stop", "args": {"session": SESSION_ID}, "contract": 1})
        reader.drain()

    def test_an_unknown_session_is_one_line_and_the_connection_closes(self, streaming_server):
        reader = _connect(streaming_server, _watch_request("s-20260912T000000Z-0000"))
        line = reader.next()
        assert line == {
            "ok": False, "contract": 1,
            "error": {"code": "unknown-session",
                      "message": "no session s-20260912T000000Z-0000 is live"},
        }
        assert reader.drain() == []

    def test_a_malformed_session_id_is_refused_the_same_way(self, streaming_server):
        reader = _connect(streaming_server, _watch_request("not-a-session"))
        assert reader.next()["error"]["code"] == "unknown-session"

    def test_a_non_string_session_id_is_unknown_session_not_a_regex_type_error(self, tmp_path):
        server = _server(
            tmp_path, cgroup_root=_fake_cgroup_root(tmp_path),
            proc_root=str(_fake_proc(tmp_path)),
        )
        with pytest.raises(serve.RequestError) as excinfo:
            server._watch_prepare({"session": None, "watch_interval": 30})
        assert excinfo.value.code == "unknown-session"

    def test_a_non_numeric_watch_interval_is_bad_argument(self, streaming_server):
        server = streaming_server
        server._dispatch({"verb": "start", "args": _start_args(), "contract": 1})
        reader = _connect(server, _watch_request(watch_interval="often"))
        line = reader.next()
        # A request argument, not a stall policy: `bad-argument`, and no
        # session was at stake.
        assert line["error"]["code"] == "bad-argument"
        assert server._sessions[SESSION_ID].finished is False
        server._dispatch({"verb": "stop", "args": {"session": SESSION_ID}, "contract": 1})

    def test_the_end_reason_is_killed_when_watch_records_enforcement(
        self, streaming_server, monkeypatch,
    ):
        server = streaming_server

        class _PlacedForWatch:
            """Isolate end-event behavior from the cgroup transaction.

            `test_serve_placement.py` owns the real delegated-scope and
            `cgroup.kill` writes. This test supplies the placed-session state
            needed to exercise the watch end-event contract.
            """
            error = None
            leaf_abs = None

            def __init__(self):
                self.placed = False

            def apply(self, _pids):
                self.placed = True

            def migrate(self, _pids):
                return 0

            def leaf_readings(self):
                return {"psi_full_avg10": None, "memory_high_applied": False}

            def block(self):
                return {
                    "requested": True,
                    "leaf": "fake-delegated-leaf" if self.placed else None,
                    "applied": {}, "pids_moved": 0, "error": None,
                }

            def release(self):
                self.placed = False

        monkeypatch.setattr(
            server, "_make_placement", lambda **_kwargs: _PlacedForWatch()
        )
        gates_files = dict(cgroup_files())
        gates_files["memory.max"] = str(6 * 1024 * 1024 * 1024)
        gates_files["cpu.max"] = "500000 100000"
        gates_files["cgroup.subtree_control"] = "memory cpu pids"
        write_cgroup(Path(server.cgroup_root), "dev.slice/dev-gates.slice", gates_files)
        server.slice_unit_verifier = lambda _unit, _path: True
        server._dispatch({"verb": "start", "args": _start_args(
            token=TOKEN, place=True, idle_bound=1000, on_stall="kill",
        ), "contract": 1})
        sess = server._sessions[SESSION_ID]
        reader = _connect(server, _watch_request())
        assert reader.next()["event"] == "reading"
        _force_stalled(sess)
        with sess.lock:
            sess.watch.record_kill([4242])
        server._dispatch({"verb": "stop", "args": {"session": SESSION_ID}, "contract": 1})
        lines = reader.drain()
        assert lines[-1] == {
            "contract": 1, "event": "end", "session": SESSION_ID,
            "at": "2026-09-12T10:15:00Z", "reason": "killed",
        }

    def test_a_daemon_shutdown_ends_every_stream(self, streaming_server):
        server = streaming_server
        server._dispatch({"verb": "start", "args": _start_args(), "contract": 1})
        reader = _connect(server, _watch_request())
        assert reader.next()["event"] == "reading"
        server.request_shutdown()
        lines = reader.drain()
        assert lines[-1]["event"] == "end"
        assert lines[-1]["reason"] == "daemon-shutdown"

    def test_watch_asked_of_the_single_response_path_is_not_streaming(self, tmp_path):
        # §8.8's `not-streaming`, and the reason `_dispatch` has to know
        # about the verb at all: a caller that cannot stream gets a named
        # refusal instead of "unknown verb".
        server = _server(
            tmp_path, cgroup_root=_fake_cgroup_root(tmp_path),
            proc_root=str(_fake_proc(tmp_path)),
        )
        resp = server._dispatch(_watch_request())
        assert resp["ok"] is False
        assert resp["error"]["code"] == "not-streaming"
        assert "§8.2" in resp["error"]["message"]


class TestEdges:
    """The defensive paths: a policy input the contract calls nullable, a
    session without a tracker, and a consumer that hangs up mid-stream."""

    @pytest.mark.parametrize("meta, expected", [
        ({}, None),
        ({"expected": None}, None),
        ({"expected": "soon"}, None),
        ({"expected": {}}, None),
        ({"expected": {"duration_s": None}}, None),
        ({"expected": {"duration_s": True}}, None),
        ({"expected": {"duration_s": 0}}, None),
        ({"expected": {"duration_s": 300}}, 300.0),
        ({"expected": {"duration_s": 12.5}}, 12.5),
    ])
    def test_expected_duration_is_read_only_when_it_is_really_a_duration(self, meta, expected):
        # `--ceiling auto` is 3x this number; a guessed ceiling kills real
        # work, so every shape but a positive number reads as "unknown".
        assert serve._expected_duration_seconds(meta) == expected

    def test_a_session_without_a_tracker_still_stops_and_reports(self, tmp_path):
        server = _server(
            tmp_path, cgroup_root=_fake_cgroup_root(tmp_path),
            proc_root=str(_fake_proc(tmp_path)), sampler_sleep=lambda _s: time.sleep(0.01),
        )
        server._dispatch({"verb": "start", "args": _start_args(), "contract": 1})
        sess = server._sessions[SESSION_ID]
        sess.watch = None  # as a session recovered by a future code path might be
        status = server._dispatch({"verb": "status", "args": {"session": SESSION_ID},
                                   "contract": 1})
        assert status["session"]["liveness"] is None and status["session"]["watch"] is None
        stop = server._dispatch({"verb": "stop", "args": {"session": SESSION_ID},
                                 "contract": 1})
        # §8.7's keys are optional: their ABSENCE is the shape a v1 consumer
        # already handles, never a missing-key crash in the daemon.
        assert "watch" not in stop["summary"]
        assert stop["summary"]["schema"] == 1

    def test_a_consumer_that_hangs_up_mid_stream_is_not_an_error(self, streaming_server):
        server = streaming_server
        server._dispatch({"verb": "start", "args": _start_args(), "contract": 1})
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        client.connect(server.socket_path)
        client.sendall((json.dumps(_watch_request()) + "\n").encode("utf-8"))
        client.close()  # gone before reading a single line
        # The daemon keeps serving: the stream thread swallows the broken
        # pipe rather than letting it reach the accept loop.
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            resp = server._dispatch({"verb": "version", "args": {}, "contract": 1})
            if resp["ok"]:
                break
            time.sleep(0.05)  # pragma: no cover - defensive
        assert resp["ok"] is True
        server._dispatch({"verb": "stop", "args": {"session": SESSION_ID}, "contract": 1})

    def test_a_dead_peer_ends_the_stream_thread_quietly(self, tmp_path):
        # The deterministic half of the hang-up case above: the very first
        # `sendall` fails. Nothing is logged (the consumer is gone — there
        # is nobody to tell) and the connection is closed exactly once.
        server = _server(
            tmp_path, cgroup_root=_fake_cgroup_root(tmp_path),
            proc_root=str(_fake_proc(tmp_path)), sampler_sleep=lambda _s: time.sleep(0.01),
        )
        server._dispatch({"verb": "start", "args": _start_args(), "contract": 1})

        class _DeadConn:
            closed = False
            def settimeout(self, _value): pass
            def sendall(self, _payload): raise BrokenPipeError(32, "Broken pipe")
            def close(self): self.closed = True

        conn = _DeadConn()
        server._watch_connection(conn, {"session": SESSION_ID, "watch_interval": 30})
        assert conn.closed is True
        server._dispatch({"verb": "stop", "args": {"session": SESSION_ID}, "contract": 1})

    def test_an_unexpected_error_in_a_stream_is_logged_not_raised(self, tmp_path, capsys):
        # The same blast-radius rule `_handle_connection` already carries: a
        # bug in ONE stream must never reach the accept loop, because the
        # process dying takes every other session's sampler with it.
        server = _server(
            tmp_path, cgroup_root=_fake_cgroup_root(tmp_path),
            proc_root=str(_fake_proc(tmp_path)), sampler_sleep=lambda _s: time.sleep(0.01),
        )
        server._dispatch({"verb": "start", "args": _start_args(), "contract": 1})

        class _BrokenConn:
            closed = False
            def settimeout(self, _value): pass
            def sendall(self, _payload): raise ValueError("not a socket error at all")
            def close(self): self.closed = True

        conn = _BrokenConn()
        server._watch_connection(conn, {"session": SESSION_ID, "watch_interval": 30})
        assert conn.closed is True
        assert "unhandled error streaming watch: ValueError" in capsys.readouterr().err
        server._dispatch({"verb": "stop", "args": {"session": SESSION_ID}, "contract": 1})

    def test_streaming_diagnostic_is_flushed_before_shutdown_or_failure(self, tmp_path, monkeypatch):
        server = _server(
            tmp_path, cgroup_root=_fake_cgroup_root(tmp_path),
            proc_root=str(_fake_proc(tmp_path)),
        )

        class _BrokenConn:
            def settimeout(self, _value):
                return None

            def sendall(self, _payload):
                raise ValueError("synthetic stream failure")

            def close(self):
                return None

        seen = {}
        real_print = print

        def spy_print(*args, **kwargs):
            seen.update(kwargs)
            real_print(*args, **kwargs)

        monkeypatch.setattr("builtins.print", spy_print)
        server._watch_connection(_BrokenConn(), {"session": None, "watch_interval": 30})
        assert seen.get("flush") is True

    def test_a_blank_line_in_a_stream_is_skipped_by_the_client(self, tmp_path, capsys):
        # Nothing the daemon writes produces one, but `_ctl_stream` is the
        # consumer-side reference and must not print or parse an empty line.
        socket_path = str(tmp_path / "fake.sock")
        server_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server_sock.bind(socket_path)
        server_sock.listen(1)

        def serve_once() -> None:
            conn, _ = server_sock.accept()
            with conn:
                conn.recv(65536)
                conn.sendall(b"\n\n" + json.dumps(
                    {"contract": 1, "event": "end", "session": SESSION_ID,
                     "at": "2026-09-12T10:15:00Z", "reason": "stopped"}
                ).encode("utf-8") + b"\n")
        thread = threading.Thread(target=serve_once, daemon=True)
        thread.start()
        try:
            rc = cg.main(["ctl", "--socket", socket_path, "watch", SESSION_ID])
        finally:
            thread.join(timeout=5.0)
            server_sock.close()
        assert rc == 0
        assert [line for line in capsys.readouterr().out.splitlines() if line == ""] == []


class TestWatchOverExec:
    """The exec carrier: `cgprofile ctl watch` holds the connection open and
    forwards each line with a flush (§8.2 "one long-lived docker exec whose
    stdout the consumer reads line by line")."""

    def test_ctl_watch_forwards_every_line_and_exits_0(self, streaming_server, capsys):
        server = streaming_server
        server._dispatch({"verb": "start", "args": _start_args(), "contract": 1})
        sess = server._sessions[SESSION_ID]
        result: Dict[str, Any] = {}

        def run() -> None:
            result["rc"] = cg.main(["ctl", "--socket", server.socket_path, "watch", SESSION_ID])

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        time.sleep(0.2)
        _force_stalled(sess)
        time.sleep(0.2)
        server._dispatch({"verb": "stop", "args": {"session": SESSION_ID}, "contract": 1})
        thread.join(timeout=15.0)
        assert result.get("rc") == 0
        lines = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line]
        events = [line["event"] for line in lines]
        assert events.count("end") == 1
        assert events.count("verdict") == 1
        assert events.count("reading") >= 2
        assert lines[-1]["event"] == "end"

    @pytest.mark.parametrize("ending", [b"", b'{"event":"end"'])
    def test_a_watch_disconnected_before_a_complete_end_is_exit_3(
        self, tmp_path, capsys, ending,
    ):
        socket_path = str(tmp_path / "truncated.sock")
        server_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server_sock.bind(socket_path)
        server_sock.listen(1)

        def serve_once() -> None:
            conn, _ = server_sock.accept()
            with conn:
                conn.recv(65536)
                conn.sendall(
                    b'{"contract":1,"event":"reading","session":"s-test"}\n' + ending
                )

        thread = threading.Thread(target=serve_once, daemon=True)
        thread.start()
        try:
            rc = cg.main(["ctl", "--socket", socket_path, "watch", SESSION_ID])
        finally:
            thread.join(timeout=60.0)  # suite failsafe, not a verdict clock
            server_sock.close()
        assert rc == 3
        assert json.loads(capsys.readouterr().out.splitlines()[0])["event"] == "reading"

    def test_two_end_events_are_a_transport_fault(self, tmp_path):
        socket_path = str(tmp_path / "duplicate-end.sock")
        server_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server_sock.bind(socket_path)
        server_sock.listen(1)

        def serve_once() -> None:
            conn, _ = server_sock.accept()
            with conn:
                conn.recv(65536)
                end = json.dumps({"contract": 1, "event": "end", "session": SESSION_ID})
                conn.sendall((end + "\n" + end + "\n").encode())

        thread = threading.Thread(target=serve_once, daemon=True)
        thread.start()
        try:
            rc = cg.main(["ctl", "--socket", socket_path, "watch", SESSION_ID])
        finally:
            thread.join(timeout=60.0)
            server_sock.close()
        assert rc == 3

    def test_an_unknown_session_over_exec_is_one_line_and_exit_2(self, streaming_server, capsys):
        rc = cg.main([
            "ctl", "--socket", streaming_server.socket_path, "watch", "s-20260912T000000Z-0000",
        ])
        assert rc == 2
        out = [line for line in capsys.readouterr().out.splitlines() if line]
        assert len(out) == 1
        assert json.loads(out[0])["error"]["code"] == "unknown-session"

    def test_an_unreachable_daemon_is_exit_3(self, tmp_path, capsys):
        rc = cg.main(["ctl", "--socket", str(tmp_path / "nope.sock"), "watch", SESSION_ID])
        assert rc == 3
        assert capsys.readouterr().out == ""

    def test_the_watch_request_is_built_by_the_reference_translator(self):
        args = cg.build_parser().parse_args(["ctl", "watch", SESSION_ID])
        assert cg._ctl_request(args) == {
            "verb": "watch", "args": {"session": SESSION_ID, "watch_interval": 30},
            "contract": 1,
        }
        args = cg.build_parser().parse_args(
            ["ctl", "watch", SESSION_ID, "--watch-interval", "12.5"]
        )
        assert cg._ctl_request(args)["args"]["watch_interval"] == 12.5

    def test_the_start_policy_options_are_forwarded_verbatim(self):
        args = cg.build_parser().parse_args([
            "ctl", "start", "--target", f"containerid:{CONTAINER_ID}",
            "--scope", "container-shared", "--meta", json.dumps(META),
            "--progress-stream", "/run/lane/p.ndjson", "--idle-bound", "auto",
            "--ceiling", "900", "--on-stall", "kill",
        ])
        req = cg._ctl_request(args)
        assert req["args"]["progress_stream"] == "/run/lane/p.ndjson"
        # Verbatim: the strings the caller typed, for the daemon to judge.
        assert req["args"]["idle_bound"] == "auto"
        assert req["args"]["ceiling"] == "900"
        assert req["args"]["on_stall"] == "kill"
