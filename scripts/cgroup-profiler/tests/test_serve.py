"""Tests for lib/serve.py — the session server (RG-55 C4).

Three tiers, cheapest first:

* Unit tests against ``SessionServer`` methods directly (registry logic,
  the write guard, retention, restart recovery) — no threads, no sockets.
* A full session lifecycle driven by calling ``_session_loop`` synchronously
  (never via a real ``threading.Thread``) against the frozen contract
  fixtures (``tests/fixtures/contract/frames``), with the cgroup/proc roots
  as symlinks this suite repoints between ticks — reproducing
  ``summary-v1.json``/``summary-container-v1.json`` byte-for-byte through
  the *wiring* (serve -> sampler -> summary -> store), which
  ``tests/test_summary.py`` already proves correct in isolation.
* One genuine round trip over a real Unix socket, proving the wire protocol
  (JSON-lines request/response, one document, ``contract: 1``) actually
  works end to end, not just via direct method calls.

DAMON is faked at the ``DamonSession`` boundary (not the sysfs boundary
``tests/test_damon.py`` fakes) for the lifecycle test: this suite is proving
``lib.serve`` threads DAMON's classified bytes into ``SummaryAccumulator``
correctly, not re-deriving DAMON's own classification math.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import pytest

import cgprofile as cg
from lib import damon as damon_mod, serve, store, summary, targets as targets_mod
from tests.conftest import cgroup_files, write_cgroup

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "contract"
FRAMES_DIR = FIXTURES / "frames"
_CGPROFILE_PY = str(Path(__file__).resolve().parents[1] / "cgprofile.py")
_HAS_REPORT_DEPS = (
    importlib.util.find_spec("pandas") is not None
    and importlib.util.find_spec("plotly") is not None
)
CONTAINER_ID = "deadbeefcafebabefeedfacefeedbeadf00dbabe1234567890abcdef00112233"
SIMPLE_CONTAINER_ID = "a" * 64
SESSION_ID = "s-20260912T101500Z-9f01"
EPOCH_START = datetime(2026, 9, 12, 10, 15, 0, tzinfo=timezone.utc).timestamp()
EPOCH_END = datetime(2026, 9, 12, 10, 15, 4, tzinfo=timezone.utc).timestamp()


def test_epoch_start_is_the_contract_timestamp():
    assert serve.SessionServer._iso(EPOCH_START) == "2026-09-12T10:15:00Z"
    assert serve.SessionServer._iso(EPOCH_END) == "2026-09-12T10:15:04Z"


# ── write guard ──────────────────────────────────────────────────────────

class TestWriteGuard:
    def test_allows_a_path_under_sessions_dir(self, tmp_path):
        server = serve.SessionServer(sessions_dir=str(tmp_path / "sessions"))
        server._guard_path(str(tmp_path / "sessions" / "s-1" / "manifest.json"))  # no raise

    def test_allows_the_sessions_dir_itself(self, tmp_path):
        server = serve.SessionServer(sessions_dir=str(tmp_path / "sessions"))
        server._guard_path(str(tmp_path / "sessions"))  # no raise

    def test_refuses_a_path_outside_both_roots(self, tmp_path):
        server = serve.SessionServer(sessions_dir=str(tmp_path / "sessions"))
        with pytest.raises(serve.HostWriteError):
            server._guard_path(str(tmp_path / "elsewhere" / "file"))

    def test_refuses_a_symlink_escape_from_inside_sessions_dir(self, tmp_path):
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        escape = sessions_dir / "escape"
        escape.symlink_to(outside)
        server = serve.SessionServer(sessions_dir=str(sessions_dir))
        with pytest.raises(serve.HostWriteError):
            server._guard_path(str(escape / "file"))

    def test_writable_roots_names_both_the_sessions_dir_and_the_damon_admin_root(self, tmp_path):
        server = serve.SessionServer(sessions_dir=str(tmp_path / "sessions"))
        roots = server._writable_roots()
        assert roots[0] == os.path.realpath(str(tmp_path / "sessions"))
        assert roots[1] == os.path.realpath(os.path.dirname(damon_mod.KDAMONDS_DIR))


def test_damon_write_nr_kdamonds_refuses_a_symlink_escape(tmp_path, monkeypatch):
    outside = tmp_path / "outside"
    outside.mkdir()
    admin = tmp_path / "admin"
    admin.mkdir()
    (admin / "kdamonds").symlink_to(outside)
    monkeypatch.setattr(damon_mod, "KDAMONDS_DIR", str(admin / "kdamonds"))

    calls = []
    monkeypatch.setattr(
        damon_mod.SysfsInterface if damon_mod.SysfsInterface else object,
        "_write_int", lambda path, value: calls.append((path, value)),
        raising=False,
    )
    with pytest.raises(damon_mod.HostWriteError):
        damon_mod._write_nr_kdamonds(3)
    assert calls == []


def test_damon_write_nr_kdamonds_writes_when_inside_the_admin_root(tmp_path, monkeypatch):
    admin = tmp_path / "admin" / "kdamonds"
    admin.mkdir(parents=True)
    monkeypatch.setattr(damon_mod, "KDAMONDS_DIR", str(admin))
    written = {}

    class Fake:
        @classmethod
        def _write_int(cls, path, value):
            written["path"] = path
            written["value"] = value

    monkeypatch.setattr(damon_mod, "SysfsInterface", Fake)
    damon_mod._write_nr_kdamonds(7)
    assert written == {"path": str(admin / "nr_kdamonds"), "value": 7}


# ── session id ───────────────────────────────────────────────────────────

def test_default_session_id_matches_the_contract_format(tmp_path):
    server = serve.SessionServer(sessions_dir=str(tmp_path / "sessions"), clock=lambda: EPOCH_START)
    sid = server._default_session_id()
    assert serve._SESSION_ID_RE.match(sid)
    assert sid.startswith("s-20260912T101500Z-")


def test_constructor_rejects_a_bad_damon_default(tmp_path):
    with pytest.raises(ValueError):
        serve.SessionServer(sessions_dir=str(tmp_path / "sessions"), damon_default="maybe")


def test_damon_pool_explicit_arg_is_used_as_is(tmp_path):
    sentinel_pool = object()
    server = serve.SessionServer(sessions_dir=str(tmp_path / "sessions"), damon_pool=sentinel_pool)
    assert server.damon_pool is sentinel_pool


def test_damon_pool_defaults_to_a_real_kdamond_pool_when_omitted(tmp_path):
    server = serve.SessionServer(sessions_dir=str(tmp_path / "sessions"))
    assert isinstance(server.damon_pool, damon_mod.KdamondPool)


def test_parse_iso_epoch_returns_none_for_malformed_text():
    assert serve.SessionServer._parse_iso_epoch("not-a-timestamp") is None
    assert serve.SessionServer._parse_iso_epoch(None) is None


def test_on_topology_noop_returns_none(tmp_path):
    server = serve.SessionServer(sessions_dir=str(tmp_path / "sessions"))
    assert server._on_topology_noop(["a"], ["b"]) is None


# ── a minimal one-container fixture for registry-only tests (no frames) ──

@pytest.fixture
def simple_root(tmp_path) -> Path:
    root = tmp_path / "cgroup"
    write_cgroup(root, "", cgroup_files())
    write_cgroup(root, "dev.slice", cgroup_files())
    write_cgroup(root, "dev.slice/dev-background.slice", cgroup_files())
    write_cgroup(
        root,
        f"dev.slice/dev-background.slice/docker-{SIMPLE_CONTAINER_ID}.scope",
        cgroup_files(memory_current=500 * 1024 * 1024),
    )
    return root


@pytest.fixture
def simple_proc(tmp_path) -> Path:
    root = tmp_path / "proc"
    root.mkdir()
    (root / "loadavg").write_text("1.0 1.0 1.0 1/100 999\n")
    (root / "meminfo").write_text("MemTotal:  1000 kB\nMemAvailable: 500 kB\n")
    pressure = root / "pressure"
    pressure.mkdir()
    for name in ("cpu", "memory", "io"):
        (pressure / name).write_text("some avg10=0.00 avg60=0.00 avg300=0.00 total=0\n"
                                      "full avg10=0.00 avg60=0.00 avg300=0.00 total=0\n")
    return root


@pytest.fixture
def simple_server(tmp_path, simple_root, simple_proc):
    return serve.SessionServer(
        sessions_dir=str(tmp_path / "sessions"),
        cgroup_root=str(simple_root),
        proc_root=str(simple_proc),
        clock=lambda: EPOCH_START,
        session_id_fn=lambda: SESSION_ID,
    )


@pytest.fixture(autouse=True)
def _stop_leaked_session_threads(monkeypatch):
    """RW-48: every ``SessionServer`` this test constructs (via the
    ``simple_server`` fixture or a bare ``serve.SessionServer(...)`` in the
    test body -- both patterns are common in this file) gets every live
    session stopped and its thread joined here, in a fixture finalizer that
    runs on a FAILING test exactly as on a passing one.

    Many tests below already do this by hand (``sess.stop_event.set();
    sess.thread.join(timeout=5.0)``) as their last lines -- that is fine and
    stays, it is simply redundant with this net (``_stop_all_sessions`` is a
    no-op on an already-finished session). What it does NOT cover is a test
    whose own assertion fails BEFORE reaching those lines: the session
    thread is then never told to stop and never joined, and normally that
    is harmless (a daemon thread the interpreter does not wait for) -- but
    is exactly how a `daemon=True -> False` mutation (serve.py:479, RW-28)
    turns one failing assertion into a process that never exits. Track
    every server via `__init__` rather than asking each test to register
    itself, so this net applies uniformly without editing every test.
    """
    created: List[Any] = []
    original_init = serve.SessionServer.__init__

    def _tracking_init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        created.append(self)

    monkeypatch.setattr(serve.SessionServer, "__init__", _tracking_init)
    yield
    for server in created:
        try:
            server._stop_all_sessions(aborted_reason="test-teardown")
        except Exception:  # noqa: BLE001 - teardown must not mask the real failure
            pass


def _start_req(**overrides) -> Dict[str, Any]:
    req = {
        "verb": "start",
        "target": f"containerid:{SIMPLE_CONTAINER_ID}",
        "scope": "container-shared",
        "token": None,
        "damon": "off",
        "interval": 1.0,
        "meta": {"lane": "x", "project": "p", "worktree": "w", "commit": None,
                 "run_gate_revision": 1, "kind": "command", "expected": None},
    }
    req.update(overrides)
    return req


class TestStartRegistry:
    def test_bad_target_format(self, simple_server):
        resp = simple_server._dispatch(_start_req(target="container:foo"))
        assert resp == {"ok": False, "contract": 1,
                         "error": {"code": "bad-argument",
                                   "message": "--target must be 'containerid:<64 hex>'"}}

    def test_bad_container_id_hex(self, simple_server):
        resp = simple_server._dispatch(_start_req(target="containerid:not-hex"))
        assert resp["error"]["code"] == "bad-argument"

    def test_bad_scope(self, simple_server):
        resp = simple_server._dispatch(_start_req(scope="whole-host"))
        assert resp["error"]["code"] == "bad-argument"

    def test_bad_damon(self, simple_server):
        resp = simple_server._dispatch(_start_req(damon="maybe"))
        assert resp["error"]["code"] == "bad-argument"

    def test_bad_token(self, simple_server):
        resp = simple_server._dispatch(_start_req(token="short"))
        assert resp["error"]["code"] == "bad-argument"

    def test_bad_meta_not_an_object(self, simple_server):
        resp = simple_server._dispatch(_start_req(meta="oops"))
        assert resp["error"]["code"] == "bad-argument"

    @pytest.mark.parametrize("interval", ["not-a-number", "NaN", "inf", True])
    def test_malformed_interval_is_a_contract_error(self, simple_server, interval):
        resp = simple_server._dispatch(_start_req(interval=interval))
        assert resp["ok"] is False
        assert resp["contract"] == 1
        assert resp["error"]["code"] == "bad-argument"

    def test_finite_out_of_range_interval_is_clamped_by_contract(self, simple_server):
        assert simple_server._dispatch(_start_req(interval=-1))["interval_seconds"] == 0.25
        # The first session is live and the fixture's fixed id makes a second
        # start an idempotent tokenless case only after B3 is fixed; use the
        # same session to avoid relying on a second id here.
        sess = simple_server._sessions[SESSION_ID]
        sess.stop_event.set()
        sess.thread.join(timeout=5.0)

    def test_no_interval_given_falls_back_to_the_server_default(self, simple_server):
        # `_start_req()`'s own default always supplies an explicit interval
        # (1.0) -- this is the one test that omits it, exercising the
        # `interval_req is None` branch every other test's fixed 1.0 masks.
        req = _start_req()
        del req["interval"]
        resp = simple_server._dispatch(req)
        assert resp["ok"] is True
        assert resp["interval_seconds"] == simple_server.default_interval
        sess = simple_server._sessions[resp["session"]]
        sess.stop_event.set()
        sess.thread.join(timeout=5.0)

    def test_target_not_found(self, simple_server):
        resp = simple_server._dispatch(_start_req(target=f"containerid:{'f' * 64}"))
        assert resp["error"]["code"] == "target-not-found"

    def test_start_then_stop_reports_finished(self, simple_server):
        resp = simple_server._dispatch(_start_req())
        assert resp["ok"] is True
        assert resp["contract"] == 1
        assert resp["session"] == SESSION_ID
        assert resp["reused"] is False
        assert resp["target"]["container_id"] == SIMPLE_CONTAINER_ID
        assert resp["target"]["pids_at_start"] == 0
        sess = simple_server._sessions[SESSION_ID]
        # The sampling thread MUST be a daemon thread — a non-daemon one
        # would keep the whole process alive on exit until every live
        # session's thread happens to finish on its own.
        assert sess.thread.daemon is True
        sess.stop_event.set()
        sess.thread.join(timeout=5.0)
        stop_resp = simple_server._dispatch({"verb": "stop", "session": SESSION_ID})
        assert stop_resp["ok"] is True
        assert stop_resp["summary"]["samples"] >= 1

    def test_idempotent_start_returns_reused(self, simple_server):
        first = simple_server._dispatch(_start_req(token="a-real-token-12"))
        second = simple_server._dispatch(_start_req(token="a-real-token-12"))
        assert second["reused"] is True
        assert second["session"] == first["session"]
        sess = simple_server._sessions[first["session"]]
        sess.stop_event.set()
        sess.thread.join(timeout=5.0)

    def test_no_token_starts_are_independent_and_consume_max_sessions(self, simple_server):
        simple_server.max_sessions = 2
        ids = iter(["s-20260912T101500Z-9f01", "s-20260912T101500Z-9f02"])
        simple_server._session_id_fn = lambda: next(ids)
        first = simple_server._dispatch(_start_req())
        second = simple_server._dispatch(_start_req())
        assert first["reused"] is False
        assert second["reused"] is False
        assert second["session"] != first["session"]
        assert len([s for s in simple_server._sessions.values() if not s.finished]) == 2
        for response in (first, second):
            sess = simple_server._sessions[response["session"]]
            sess.stop_event.set()
            sess.thread.join(timeout=5.0)

    def test_no_token_start_at_max_sessions_is_not_reused(self, simple_server):
        simple_server.max_sessions = 1
        ids = iter(["s-20260912T101500Z-9f01", "s-20260912T101500Z-9f02"])
        simple_server._session_id_fn = lambda: next(ids)
        first = simple_server._dispatch(_start_req())
        second = simple_server._dispatch(_start_req())
        assert first["reused"] is False
        assert second["error"]["code"] == "too-many-sessions"
        sess = simple_server._sessions[first["session"]]
        sess.stop_event.set()
        sess.thread.join(timeout=5.0)

    def test_start_after_the_same_key_finished_creates_a_new_session(self, simple_server):
        first = simple_server._dispatch(_start_req(token="a-finished-token1"))
        sess = simple_server._sessions[first["session"]]
        sess.stop_event.set()
        sess.thread.join(timeout=5.0)
        simple_server._dispatch({"verb": "stop", "session": first["session"]})
        second = simple_server._dispatch(_start_req(token="a-finished-token1"))
        assert second["reused"] is False
        second_sess = simple_server._sessions[second["session"]]
        second_sess.stop_event.set()
        second_sess.thread.join(timeout=5.0)

    def test_too_many_sessions(self, simple_server):
        simple_server.max_sessions = 0
        resp = simple_server._dispatch(_start_req())
        assert resp["error"]["code"] == "too-many-sessions"

    def test_unknown_verb(self, simple_server):
        resp = simple_server._dispatch({"verb": "frobnicate"})
        assert resp["error"]["code"] == "bad-argument"


class TestStatusStopReport:
    def test_stop_series_damon_is_null_without_a_persisted_damon_sample(self, simple_server):
        start_resp = simple_server._dispatch(_start_req(damon="off"))
        sess = simple_server._sessions[start_resp["session"]]
        sess.stop_event.set()
        sess.thread.join(timeout=5.0)
        stop_resp = simple_server._dispatch({"verb": "stop", "session": start_resp["session"]})
        assert stop_resp["series"]["damon"] is None

        # A real persisted record is the positive case; an enabled-but-empty
        # or unavailable/restarted session must not advertise a fake path.
        sess.rundir.append("damon", {"hot": 1, "warm": 2, "cold": 3, "idle": 4})
        assert simple_server._stop_response(
            start_resp["session"], stop_resp["summary"], already_stopped=True
        )["series"]["damon"] == "damon.jsonl"

    def test_stop_response_does_not_conjure_a_missing_run_dir(self, tmp_path):
        sessions_dir = tmp_path / "sessions"
        server = serve.SessionServer(sessions_dir=str(sessions_dir))
        session_id = "s-20260101T000000Z-missing"
        missing_dir = sessions_dir / session_id
        response = server._stop_response(session_id, None, already_stopped=True)
        assert response["series"]["damon"] is None
        assert not missing_dir.exists()

    def test_stop_response_does_not_advertise_a_non_damon_dict(self, simple_server):
        session_id = "s-20260101T000000Z-nodamon"
        session_dir = Path(simple_server.sessions_dir) / session_id
        session_dir.mkdir(parents=True)
        (session_dir / "damon.jsonl").write_text("{}\n")
        response = simple_server._stop_response(session_id, None, already_stopped=True)
        assert response["series"]["damon"] is None

    def test_stop_response_stays_null_when_damon_series_read_fails(
        self, simple_server, monkeypatch,
    ):
        def unreadable_series(_rundir, _name):
            raise OSError("synthetic read failure")

        monkeypatch.setattr(store.RunDir, "read", unreadable_series)
        response = simple_server._stop_response(
            "s-20260101T000000Z-unreadable", None, already_stopped=True,
        )

        assert response["series"]["damon"] is None

    def test_start_records_sample_zero_before_an_immediate_stop(self, simple_server):
        resolved = targets_mod.find_container_cgroup(
            SIMPLE_CONTAINER_ID, root=simple_server.cgroup_root
        )
        with simple_server._lock:
            sess = simple_server._create_session_locked(
                container_id=SIMPLE_CONTAINER_ID, cgroup=resolved, scope="container-shared",
                token=None, interval=1.0, damon_req="off",
                meta={"lane": "x", "project": "p", "worktree": "w", "commit": None,
                      "run_gate_revision": 1, "kind": "command", "expected": None},
            )
            simple_server._sessions[sess.session_id] = sess
            simple_server._finalize_session_locked(sess, aborted_reason=None)
        assert sess.summary_acc.sample_count == 1
        assert list(sess.rundir.read("samples"))[0]["seq"] == 0
        assert sess.summary_doc["samples"] == 1
        assert sess.summary_doc["memory"]["baseline_bytes"] == 500 * 1024 * 1024
        assert sess.summary_doc["memory"]["p90_bytes"] == 500 * 1024 * 1024
    def test_status_unknown_malformed_id(self, simple_server):
        resp = simple_server._dispatch({"verb": "status", "session": "not-a-session-id"})
        assert resp["error"]["code"] == "unknown-session"
        # A malformed (non-matching-regex but string) id must be rejected by
        # the FIRST guard (`isinstance(...) or not _SESSION_ID_RE.match`),
        # never fall through to the second (a real dict lookup miss) -- an
        # `or` -> `and` mutant on the first guard would let it fall through,
        # observable only in the message's repr-quoting: `!r}` (first
        # guard) vs no repr (second guard's `sess is None` branch).
        assert resp["error"]["message"] == "no session 'not-a-session-id' is live or on record"

    def test_status_unknown_wellformed_id(self, simple_server):
        resp = simple_server._dispatch({"verb": "status", "session": "s-20260101T000000Z-abcd"})
        assert resp["error"]["code"] == "unknown-session"

    def test_status_no_session_lists_only_live(self, simple_server):
        start_resp = simple_server._dispatch(_start_req())
        sess = simple_server._sessions[start_resp["session"]]
        listing = simple_server._dispatch({"verb": "status"})
        assert listing["ok"] is True
        assert [s["session"] for s in listing["sessions"]] == [start_resp["session"]]
        assert listing["sessions"][0]["live"]["damon"] is None
        sess.stop_event.set()
        sess.thread.join(timeout=5.0)
        simple_server._dispatch({"verb": "stop", "session": start_resp["session"]})
        after_stop = simple_server._dispatch({"verb": "status"})
        assert after_stop["sessions"] == []

    def test_stop_unknown_session(self, simple_server):
        resp = simple_server._dispatch({"verb": "stop", "session": "s-20260101T000000Z-abcd"})
        assert resp["error"]["code"] == "unknown-session"

    def test_stop_is_idempotent(self, simple_server):
        start_resp = simple_server._dispatch(_start_req())
        sess = simple_server._sessions[start_resp["session"]]
        sess.stop_event.set()
        sess.thread.join(timeout=5.0)
        first = simple_server._dispatch({"verb": "stop", "session": start_resp["session"]})
        second = simple_server._dispatch({"verb": "stop", "session": start_resp["session"]})
        assert first["already_stopped"] is False
        assert second["already_stopped"] is True
        assert second["summary"] == first["summary"]

    def test_stop_reads_a_finished_session_from_disk_when_not_in_memory(self, simple_server):
        start_resp = simple_server._dispatch(_start_req())
        session_id = start_resp["session"]
        sess = simple_server._sessions[session_id]
        sess.stop_event.set()
        sess.thread.join(timeout=5.0)
        simple_server._dispatch({"verb": "stop", "session": session_id})
        del simple_server._sessions[session_id]
        resp = simple_server._dispatch({"verb": "stop", "session": session_id})
        assert resp["already_stopped"] is True
        assert resp["summary"]["session"] == session_id

    def test_report_unknown_session(self, simple_server):
        resp = simple_server._dispatch({"verb": "report", "session": "s-20260101T000000Z-abcd"})
        assert resp["error"]["code"] == "unknown-session"

    def test_report_is_unavailable_with_no_report_python(self, simple_server):
        # RW-14: `simple_server` (like a real dev checkout with no
        # `./setup.sh` run yet) has no `venv/bin/python` on disk, so
        # `SessionServer.__init__`'s own discovery resolves `report_python`
        # to `None` — `handle_report` must refuse cleanly (exit-2 shape),
        # never crash trying to exec a path that does not exist.
        assert simple_server.report_python is None
        start_resp = simple_server._dispatch(_start_req())
        session_id = start_resp["session"]
        sess = simple_server._sessions[session_id]
        sess.stop_event.set()
        sess.thread.join(timeout=5.0)
        simple_server._dispatch({"verb": "stop", "session": session_id})
        resp = simple_server._dispatch({"verb": "report", "session": session_id})
        assert resp["ok"] is False
        assert resp["error"]["code"] == "report-unavailable"

    def test_version_shape(self, simple_server):
        resp = simple_server._dispatch({"verb": "version"})
        assert resp["ok"] is True
        assert resp["contract"] == 1
        assert resp["daemon"]["name"] == serve.DEFAULT_DAEMON_NAME
        assert resp["daemon"]["max_sessions"] == simple_server.max_sessions
        assert resp["daemon"]["sessions_live"] == 0


# ── handle_report: the REAL report tier, not a stub (RW-14) ─────────────

class TestHandleReportRealRender:
    """`handle_report` shells out to the report tier's own interpreter
    (RW-14's module docstring on `handle_report` explains why). These tests
    point ``report_python`` at ``sys.executable`` — this test environment's
    own interpreter, which the project's ``pyproject.toml`` ``test`` extra
    already requires to carry pandas/plotly, the same way
    ``tester-unified:local``'s gate image does — rather than at a built
    ``venv/bin/python``, which a fresh checkout (no ``./setup.sh`` run yet)
    does not have. ``_HAS_REPORT_DEPS`` skips only the one test that needs a
    genuine render if that is somehow untrue wherever this suite runs; the
    r0-r1 gate lane itself must not be such an environment (verified
    separately, per the P1 REPORT).
    """

    def _stopped_session(self, tmp_path, **server_kwargs):
        root = tmp_path / "cgroup"
        write_cgroup(root, "", cgroup_files())
        write_cgroup(root, "dev.slice", cgroup_files())
        write_cgroup(root, "dev.slice/dev-background.slice", cgroup_files())
        write_cgroup(
            root, f"dev.slice/dev-background.slice/docker-{SIMPLE_CONTAINER_ID}.scope",
            cgroup_files(memory_current=500 * 1024 * 1024),
        )
        proc = tmp_path / "proc"
        proc.mkdir()
        (proc / "loadavg").write_text("1.0 1.0 1.0 1/100 999\n")
        (proc / "meminfo").write_text("MemTotal:  1000 kB\nMemAvailable: 500 kB\n")
        pressure = proc / "pressure"
        pressure.mkdir()
        for name in ("cpu", "memory", "io"):
            (pressure / name).write_text("some avg10=0.00 avg60=0.00 avg300=0.00 total=0\n"
                                          "full avg10=0.00 avg60=0.00 avg300=0.00 total=0\n")
        server = serve.SessionServer(
            sessions_dir=str(tmp_path / "sessions"), cgroup_root=str(root), proc_root=str(proc),
            clock=lambda: EPOCH_START, session_id_fn=lambda: SESSION_ID, **server_kwargs,
        )
        start_resp = server._dispatch(_start_req())
        session_id = start_resp["session"]
        sess = server._sessions[session_id]
        sess.stop_event.set()
        sess.thread.join(timeout=5.0)
        server._dispatch({"verb": "stop", "session": session_id})
        return server, session_id

    def test_report_script_explicit_arg_is_used_as_is(self, tmp_path):
        # Same shape of bug as report_python above but for `report_script or
        # DEFAULT_REPORT_SCRIPT` -- an explicit value must win, and (unlike
        # report_python) an explicit value is truthy, so a broken `or`->`and`
        # here would silently overwrite it with DEFAULT_REPORT_SCRIPT instead
        # of merely falling through.
        server = serve.SessionServer(
            sessions_dir=str(tmp_path / "sessions"), report_script="/custom/report.py",
        )
        assert server.report_script == "/custom/report.py"
        assert server.report_script != serve.DEFAULT_REPORT_SCRIPT

    def test_report_python_explicit_arg_is_used_as_is(self, tmp_path):
        # An explicitly-passed report_python= must win outright, never fall
        # through to the DEFAULT_REPORT_PYTHON auto-discovery branch -- this
        # is the one assertion neither the "no venv" nor the "built venv"
        # discovery tests make directly (both only ever observe the
        # discovered value's downstream EFFECT, via a real `report` call).
        server = serve.SessionServer(
            sessions_dir=str(tmp_path / "sessions"), report_python=sys.executable,
        )
        assert server.report_python == sys.executable

    def test_report_python_defaults_to_the_built_venv_when_present(self, tmp_path, monkeypatch):
        # The other branch of SessionServer.__init__'s discovery — a fresh
        # checkout with no built venv (this worktree's own actual state) is
        # exercised by every other test in this class via an explicit
        # report_python=; this pins the "found it" branch, which this
        # checkout cannot exercise for real without running ./setup.sh.
        real_access = os.access

        def fake_access(path, mode):
            if path == serve.DEFAULT_REPORT_PYTHON:
                return True
            return real_access(path, mode)

        monkeypatch.setattr(serve.os, "access", fake_access)
        server = serve.SessionServer(sessions_dir=str(tmp_path / "sessions"))
        assert server.report_python == serve.DEFAULT_REPORT_PYTHON

    @pytest.mark.skipif(not _HAS_REPORT_DEPS, reason="pandas/plotly not installed here")
    def test_renders_a_real_interactive_html_report(self, tmp_path):
        server, session_id = self._stopped_session(
            tmp_path, report_python=sys.executable, report_script=_CGPROFILE_PY,
        )
        resp = server._dispatch({"verb": "report", "session": session_id})
        assert resp["ok"] is True, resp
        html = Path(resp["path"]).read_text()
        # A real render, never the old hand-rolled stub: plotly's own figure
        # div and inlined JS, never a bare <pre>{json}</pre> dump.
        assert "cgp-figure" in html
        assert "Plotly.newPlot" in html
        assert "<pre>" not in html

    def test_report_failed_surfaces_the_subprocess_stderr_tail(self, tmp_path, monkeypatch):
        server, session_id = self._stopped_session(
            tmp_path, report_python=sys.executable, report_script=_CGPROFILE_PY,
        )
        seen_kwargs: Dict[str, Any] = {}

        def fake_run(command, **kwargs):
            seen_kwargs.update(kwargs)
            return subprocess.CompletedProcess(
                command, returncode=1, stdout="", stderr="boom\nlast line\n"
            )

        monkeypatch.setattr(serve.subprocess, "run", fake_run)
        resp = server._dispatch({"verb": "report", "session": session_id})
        assert resp["ok"] is False
        assert resp["error"]["code"] == "report-failed"
        assert resp["error"]["message"] == "last line"
        # capture_output/text must both be True -- a real real subprocess
        # run with either False would not give `proc.stdout`/`.stderr` as
        # captured strings at all (None, or inherited fds, or bytes), which
        # the mocked fake_run above cannot itself catch since it ignores
        # kwargs entirely.
        assert seen_kwargs["capture_output"] is True
        assert seen_kwargs["text"] is True

    def test_report_failed_surfaces_stdout_when_stderr_is_empty(self, tmp_path, monkeypatch):
        # `(proc.stderr or proc.stdout or "")` has TWO `or`s -- a stderr-
        # truthy fixture (the sibling test above) can only ever exercise
        # the FIRST one, short-circuiting before the second is evaluated.
        # This drives stderr empty/stdout non-empty specifically to reach
        # the second `or`, whose own `or`->`and` mutant collapses straight
        # to "" (stdout and "" is always falsy) regardless of real content.
        server, session_id = self._stopped_session(
            tmp_path, report_python=sys.executable, report_script=_CGPROFILE_PY,
        )

        def fake_run(command, **kwargs):
            return subprocess.CompletedProcess(
                command, returncode=1, stdout="from stdout\nlast stdout line\n", stderr="",
            )

        monkeypatch.setattr(serve.subprocess, "run", fake_run)
        resp = server._dispatch({"verb": "report", "session": session_id})
        assert resp["error"]["code"] == "report-failed"
        assert resp["error"]["message"] == "last stdout line"

    def test_report_failed_with_no_output_at_all_names_the_exit_code(self, tmp_path, monkeypatch):
        server, session_id = self._stopped_session(
            tmp_path, report_python=sys.executable, report_script=_CGPROFILE_PY,
        )

        def fake_run(command, **kwargs):
            return subprocess.CompletedProcess(command, returncode=7, stdout="", stderr="")

        monkeypatch.setattr(serve.subprocess, "run", fake_run)
        resp = server._dispatch({"verb": "report", "session": session_id})
        assert resp["error"]["code"] == "report-failed"
        assert resp["error"]["message"] == "report subprocess exited 7"

    def test_report_failed_when_exit_0_but_no_file_was_written(self, tmp_path, monkeypatch):
        server, session_id = self._stopped_session(
            tmp_path, report_python=sys.executable, report_script=_CGPROFILE_PY,
        )

        def fake_run(command, **kwargs):
            return subprocess.CompletedProcess(command, returncode=0, stdout="", stderr="")

        monkeypatch.setattr(serve.subprocess, "run", fake_run)
        resp = server._dispatch({"verb": "report", "session": session_id})
        assert resp["ok"] is False
        assert resp["error"]["code"] == "report-failed"

    def test_report_times_out(self, tmp_path, monkeypatch):
        server, session_id = self._stopped_session(
            tmp_path, report_python=sys.executable, report_script=_CGPROFILE_PY,
            report_timeout=0.01,
        )

        def fake_run(command, **kwargs):
            raise subprocess.TimeoutExpired(cmd=command, timeout=kwargs.get("timeout", 0.01))

        monkeypatch.setattr(serve.subprocess, "run", fake_run)
        resp = server._dispatch({"verb": "report", "session": session_id})
        assert resp["ok"] is False
        assert resp["error"]["code"] == "report-failed"
        assert "timed out" in resp["error"]["message"]


# ── host snapshot, exact reproduction of host-v1.json ──────────────────

def test_host_snapshot_matches_host_v1(tmp_path):
    frame4 = FRAMES_DIR / "4"
    server = serve.SessionServer(
        sessions_dir=str(tmp_path / "sessions"),
        cgroup_root=str(frame4),
        proc_root=str(frame4 / "proc"),
        clock=lambda: EPOCH_END,
    )
    resp = server._dispatch({"verb": "host"})
    golden = json.loads((FIXTURES / "host-v1.json").read_text())
    assert resp == golden


# ── restart recovery ─────────────────────────────────────────────────────

class TestRestartRecovery:
    def _write_manifest(self, session_dir: Path, **overrides) -> None:
        manifest = {
            "session": "s-20260101T000000Z-aaaa", "status": "live", "scope": "container",
            "container_id": "a" * 64, "cgroup": "/dev.slice/x.scope", "token": None,
            "slice_name": None, "interval_seconds": 1.0, "started_at": "2026-01-01T00:00:00Z",
            "ended_at": None, "damon_enabled": False, "damon_kdamond": None,
            "damon_thresholds": None, "damon_unavailable_reason": None, "meta": {},
            "aborted_reason": None,
        }
        manifest.update(overrides)
        session_dir.mkdir(parents=True, exist_ok=True)
        (session_dir / "manifest.json").write_text(json.dumps(manifest))

    def test_a_stray_file_in_sessions_dir_is_ignored(self, tmp_path):
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()
        (sessions_dir / "not-a-dir.txt").write_text("hello")
        serve.SessionServer(sessions_dir=str(sessions_dir))  # must not raise

    def test_a_session_dir_without_a_manifest_is_ignored(self, tmp_path):
        sessions_dir = tmp_path / "sessions"
        (sessions_dir / "s-20260101T000000Z-aaaa").mkdir(parents=True)
        serve.SessionServer(sessions_dir=str(sessions_dir))  # must not raise

    def test_a_manifest_with_bad_json_is_ignored(self, tmp_path):
        sessions_dir = tmp_path / "sessions"
        session_dir = sessions_dir / "s-20260101T000000Z-aaaa"
        session_dir.mkdir(parents=True)
        (session_dir / "manifest.json").write_text("{not json")
        serve.SessionServer(sessions_dir=str(sessions_dir))  # must not raise

    def test_a_finished_session_is_left_alone(self, tmp_path):
        sessions_dir = tmp_path / "sessions"
        session_dir = sessions_dir / "s-20260101T000000Z-aaaa"
        self._write_manifest(session_dir, status="finished")
        serve.SessionServer(sessions_dir=str(sessions_dir))
        manifest = json.loads((session_dir / "manifest.json").read_text())
        assert manifest["status"] == "finished"

    def test_a_live_session_with_no_samples_is_dropped_without_a_summary(self, tmp_path):
        sessions_dir = tmp_path / "sessions"
        session_dir = sessions_dir / "s-20260101T000000Z-aaaa"
        self._write_manifest(session_dir)
        serve.SessionServer(sessions_dir=str(sessions_dir), clock=lambda: EPOCH_END)
        manifest = json.loads((session_dir / "manifest.json").read_text())
        assert manifest["status"] == "aborted"
        assert manifest["aborted_reason"] == "daemon-restarted"
        assert not (session_dir / "summary.json").exists()

    def test_a_live_session_with_samples_is_finalized_with_a_summary(self, tmp_path):
        sessions_dir = tmp_path / "sessions"
        session_dir = sessions_dir / "s-20260101T000000Z-aaaa"
        self._write_manifest(session_dir)
        session_dir.mkdir(parents=True, exist_ok=True)
        # RW-14: "cg" is keyed by cgroup path (matching the manifest's own
        # "cgroup": "/dev.slice/x.scope") — the same shape `_on_session_sample`
        # writes, so orphan replay (`_finalize_orphan`) can unwrap it the same
        # way a live session's own disk record would be read back.
        # `SummaryAccumulator.add_sample(cgroup=...)` expects the ALREADY-
        # PARSED nested shape `lib.summary.sample_target_cgroup` produces
        # (`{"mem": {"current": ..., "peak": ...}, ...}`), never the raw
        # cgroupfs file-content dict `cgroup_files()` returns — that raw
        # shape has no "mem" key at all, so every `_dig(s, "mem", ...)`
        # read silently comes back None regardless of what was fed in.
        (session_dir / "samples.jsonl").write_text(
            json.dumps({"cg": {"/dev.slice/x.scope": {"mem": {"current": 100, "peak": 100}}},
                       "pids": [1]}) + "\n"
        )
        (session_dir / "host.jsonl").write_text(
            json.dumps({"host": {"psi": {"memory": {"some_avg10": 12.5}}}, "slice": None}) + "\n"
        )
        serve.SessionServer(sessions_dir=str(sessions_dir), clock=lambda: EPOCH_END)
        manifest = json.loads((session_dir / "manifest.json").read_text())
        assert manifest["status"] == "aborted"
        assert (session_dir / "summary.json").exists()
        summary_doc = json.loads((session_dir / "summary.json").read_text())
        assert summary_doc["session"] == "s-20260101T000000Z-aaaa"
        # RW-14 replay unwraps three separate `X or <empty-default>`
        # fallbacks (cg / host / pids) feeding `add_sample` — each is
        # truthy/present here, so a broken `or`->`and` on any one of them
        # would silently substitute the empty default for the real data.
        # `memory.current`=100 pins the cg one (an `and`-mutated cgroup
        # would be `{}`, reading no memory data at all);
        # `target.targets_seen`=1 pins the pids one (`and`-mutated pids
        # would be `[]`, unioning nothing); the host psi value pins the
        # host one.
        assert summary_doc["memory"]["peak_bytes"] == 100
        assert summary_doc["target"]["targets_seen"] == 1
        assert summary_doc["host"]["start"]["memory_pressure"]["some_avg10"] == 12.5

    def test_a_live_orphan_replays_damon_payload_after_removing_sequence(self, tmp_path):
        sessions_dir = tmp_path / "sessions"
        session_dir = sessions_dir / "s-20260101T000000Z-aaaa"
        self._write_manifest(
            session_dir,
            damon_enabled=True,
            damon_kdamond=0,
            damon_thresholds={"hot_rate_pct": 5},
        )
        (session_dir / "samples.jsonl").write_text(
            json.dumps({
                "seq": 0,
                "cg": {"/dev.slice/x.scope": {"mem": {"current": 100, "peak": 100}}},
                "pids": [1],
                "mono": 0.0,
            }) + "\n"
        )
        (session_dir / "host.jsonl").write_text(
            json.dumps({"host": {}, "slice": None}) + "\n"
        )
        (session_dir / "damon.jsonl").write_text(
            json.dumps({"_seq": 0, "hot": 10, "warm": 20, "cold": 30, "idle": 40}) + "\n"
        )
        serve.SessionServer(sessions_dir=str(sessions_dir), clock=lambda: EPOCH_END)
        summary_doc = json.loads((session_dir / "summary.json").read_text())
        assert summary_doc["damon"]["samples"] == 1
        assert summary_doc["damon"]["hot_bytes"]["peak"] == 10

    def test_a_live_orphan_skips_non_dict_damon_rows(self, tmp_path):
        sessions_dir = tmp_path / "sessions"
        session_dir = sessions_dir / "s-20260101T000000Z-aaaa"
        self._write_manifest(session_dir, damon_enabled=True, damon_kdamond=0)
        (session_dir / "samples.jsonl").write_text(
            json.dumps({
                "seq": 0,
                "cg": {"/dev.slice/x.scope": {"mem": {"current": 100, "peak": 100}}},
                "pids": [1],
                "mono": 0.0,
            }) + "\n"
        )
        (session_dir / "host.jsonl").write_text(
            json.dumps({"host": {}, "slice": None}) + "\n"
        )
        (session_dir / "damon.jsonl").write_text("null\n")
        serve.SessionServer(sessions_dir=str(sessions_dir), clock=lambda: EPOCH_END)
        manifest = json.loads((session_dir / "manifest.json").read_text())
        summary_doc = json.loads((session_dir / "summary.json").read_text())
        assert manifest["status"] == "aborted"
        assert summary_doc["damon"]["samples"] == 0

    def test_finalize_orphan_never_conjures_the_run_dir_into_existence(self, tmp_path, monkeypatch):
        # `_finalize_orphan` passes `create=False` to `store.RunDir` (its own
        # docstring: "for read-only consumers ... that must not conjure a run
        # directory into existence just by looking for one") -- this session's
        # directory already exists (found via the on-disk scan itself), so a
        # `create=False` -> `create=True` mutant is unobservable via the
        # directory's end state (`os.makedirs(..., exist_ok=True)` on an
        # already-existing dir is a harmless no-op); assert on the CALL
        # itself instead, which is the actual contract being protected.
        sessions_dir = tmp_path / "sessions"
        session_dir = sessions_dir / "s-20260101T000000Z-aaaa"
        self._write_manifest(session_dir)
        session_dir.mkdir(parents=True, exist_ok=True)
        (session_dir / "samples.jsonl").write_text(
            json.dumps({"cg": {"/dev.slice/x.scope": cgroup_files(memory_current=100)},
                       "pids": [1]}) + "\n"
        )
        (session_dir / "host.jsonl").write_text(
            json.dumps({"host": {"psi": {"memory": {}}}, "slice": None}) + "\n"
        )
        real_makedirs = os.makedirs
        calls: List[str] = []

        def spy_makedirs(path, *a, **kw):
            calls.append(str(path))
            return real_makedirs(path, *a, **kw)

        monkeypatch.setattr(os, "makedirs", spy_makedirs)
        serve.SessionServer(sessions_dir=str(sessions_dir), clock=lambda: EPOCH_END)
        assert str(session_dir) not in calls

    def test_a_live_orphan_with_a_damon_unavailable_reason_carries_it_into_the_summary(self, tmp_path):
        sessions_dir = tmp_path / "sessions"
        session_dir = sessions_dir / "s-20260101T000000Z-aaaa"
        self._write_manifest(
            session_dir, damon_enabled=True, damon_unavailable_reason="no pids to monitor yet",
        )
        (session_dir / "samples.jsonl").write_text(
            json.dumps({"cg": {"/dev.slice/x.scope": cgroup_files(memory_current=100)},
                       "pids": [1]}) + "\n"
        )
        (session_dir / "host.jsonl").write_text(
            json.dumps({"host": {"psi": {"memory": {}}}, "slice": None}) + "\n"
        )
        serve.SessionServer(sessions_dir=str(sessions_dir), clock=lambda: EPOCH_END)
        summary_doc = json.loads((session_dir / "summary.json").read_text())
        assert summary_doc["damon"]["status"] == "unavailable"
        assert summary_doc["damon"]["reason"] == "no pids to monitor yet"

    def test_recover_orphans_swallows_a_listdir_oserror(self, tmp_path, monkeypatch):
        server = serve.SessionServer(sessions_dir=str(tmp_path / "sessions"))
        monkeypatch.setattr(
            serve.os, "listdir", lambda p: (_ for _ in ()).throw(OSError("boom"))
        )
        server._recover_orphans()  # must not raise


# ── gc / retention ───────────────────────────────────────────────────────

class TestRetention:
    def _finished(self, sessions_dir: Path, session_id: str, ended_at: Optional[str]) -> None:
        session_dir = sessions_dir / session_id
        session_dir.mkdir(parents=True)
        (session_dir / "manifest.json").write_text(json.dumps({
            "session": session_id, "status": "finished", "ended_at": ended_at,
        }))

    def test_keeps_newest_n_and_drops_the_rest(self, tmp_path):
        sessions_dir = tmp_path / "sessions"
        for i in range(5):
            self._finished(sessions_dir, f"s-2026010{i}T000000Z-aaaa", "2026-01-01T00:00:00Z")
        server = serve.SessionServer(
            sessions_dir=str(sessions_dir), keep_sessions=2, keep_days=3650,
            clock=lambda: EPOCH_START,
        )
        removed, kept = server._run_retention()
        assert kept == 2
        assert len(removed) == 3
        remaining = sorted(p.name for p in sessions_dir.iterdir())
        assert remaining == ["s-20260103T000000Z-aaaa", "s-20260104T000000Z-aaaa"]

    def test_drops_sessions_older_than_keep_days_even_if_newest(self, tmp_path):
        sessions_dir = tmp_path / "sessions"
        self._finished(sessions_dir, "s-20200101T000000Z-aaaa", "2020-01-01T00:00:00Z")
        server = serve.SessionServer(
            sessions_dir=str(sessions_dir), keep_sessions=200, keep_days=1,
            clock=lambda: EPOCH_START,
        )
        removed, kept = server._run_retention()
        assert removed == ["s-20200101T000000Z-aaaa"]
        assert kept == 0

    def test_keep_days_boundary_is_kept_exactly_at_the_cutoff(self, tmp_path):
        # `ended_epoch < cutoff` must be a STRICT less-than -- a session
        # that ended exactly `keep_days` ago is kept, not dropped. A
        # `<` -> `<=` mutant is invisible to the sibling test above (which
        # uses an ended_at far older than the cutoff, not exactly AT it).
        sessions_dir = tmp_path / "sessions"
        self._finished(sessions_dir, "s-20260911T101500Z-aaaa", "2026-09-11T10:15:00Z")
        server = serve.SessionServer(
            sessions_dir=str(sessions_dir), keep_sessions=200, keep_days=1,
            clock=lambda: EPOCH_START,   # EPOCH_START is exactly 2026-09-12T10:15:00Z
        )
        removed, kept = server._run_retention()
        assert removed == []
        assert kept == 1

    def test_never_prunes_a_live_session(self, simple_server):
        resp = simple_server._dispatch(_start_req())
        session_id = resp["session"]
        simple_server.keep_sessions = 0
        simple_server.keep_days = 0
        removed, kept = simple_server._run_retention()
        assert removed == []
        assert os.path.isdir(os.path.join(simple_server.sessions_dir, session_id))
        sess = simple_server._sessions[session_id]
        sess.stop_event.set()
        sess.thread.join(timeout=5.0)

    def test_remove_session_dir_passes_ignore_errors_true(self, tmp_path, monkeypatch):
        # `_remove_session_dir`'s `shutil.rmtree(path, ignore_errors=True)`
        # must swallow a real removal failure rather than let it propagate
        # out of `_run_retention` (uncaught) -- assert on the actual kwarg
        # passed, since a spy is the only way to see it (a real failure
        # would only be OBSERVABLE as "did retention crash or not", which
        # is a much heavier test to construct reliably).
        sessions_dir = tmp_path / "sessions"
        self._finished(sessions_dir, "s-20260101T000000Z-aaaa", None)
        server = serve.SessionServer(
            sessions_dir=str(sessions_dir), keep_sessions=0, keep_days=3650,
            clock=lambda: EPOCH_START,
        )
        seen_kwargs: Dict[str, Any] = {}
        real_rmtree = shutil.rmtree

        def spy_rmtree(path, **kwargs):
            seen_kwargs.update(kwargs)
            return real_rmtree(path, **kwargs)

        monkeypatch.setattr(serve.shutil, "rmtree", spy_rmtree)
        server._run_retention()
        assert seen_kwargs["ignore_errors"] is True

    def test_gc_verb_returns_removed_and_kept(self, tmp_path):
        sessions_dir = tmp_path / "sessions"
        self._finished(sessions_dir, "s-20260101T000000Z-aaaa", None)
        server = serve.SessionServer(
            sessions_dir=str(sessions_dir), keep_sessions=0, keep_days=3650,
            clock=lambda: EPOCH_START,
        )
        resp = server._dispatch({"verb": "gc"})
        assert resp == {"ok": True, "contract": 1, "removed": ["s-20260101T000000Z-aaaa"], "kept": 0}

    def test_retention_with_no_sessions_dir_yet(self, tmp_path):
        # __init__ always creates sessions_dir, so the only way _run_retention
        # ever sees it missing is if something removed it after construction.
        server = serve.SessionServer(sessions_dir=str(tmp_path / "gone"))
        shutil.rmtree(server.sessions_dir)
        removed, kept = server._run_retention()
        assert (removed, kept) == ([], 0)

    def test_retention_skips_a_manifest_that_says_live_but_is_untracked(self, tmp_path):
        # A manifest that still says "live" but was written after this
        # server's own __init__ (so _recover_orphans never saw it) and is not
        # in server._sessions either — defensive: never touched either way.
        server = serve.SessionServer(
            sessions_dir=str(tmp_path / "sessions"), keep_sessions=0, keep_days=3650,
        )
        session_dir = Path(server.sessions_dir) / "s-20260101T000000Z-aaaa"
        session_dir.mkdir(parents=True)
        (session_dir / "manifest.json").write_text(json.dumps(
            {"session": "s-20260101T000000Z-aaaa", "status": "live", "ended_at": None}
        ))
        removed, kept = server._run_retention()
        assert (removed, kept) == ([], 0)
        assert session_dir.exists()

    def test_retention_skips_a_manifest_with_bad_json(self, tmp_path):
        sessions_dir = tmp_path / "sessions"
        bad_dir = sessions_dir / "s-20260101T000000Z-aaaa"
        bad_dir.mkdir(parents=True)
        (bad_dir / "manifest.json").write_text("{not json")
        server = serve.SessionServer(sessions_dir=str(sessions_dir), keep_sessions=0)
        removed, kept = server._run_retention()
        assert (removed, kept) == ([], 0)
        assert bad_dir.exists()


# ── caps.TempCaps must never be imported by this module ─────────────────

def test_serve_module_never_imports_caps():
    """Prose mentioning ``lib.caps.TempCaps`` (explaining the safety
    property) is fine and expected; an actual ``import`` of the module is
    not — checked via the AST so a docstring never trips this test."""
    import ast

    tree = ast.parse(Path(serve.__file__).read_text(encoding="utf-8"), filename=serve.__file__)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            imported.update(alias.name for alias in node.names)
    assert "caps" not in imported


# ── the full lifecycle: reproduce summary-v1.json / summary-container-v1.json

class _FrameDamonSession:
    """Stands in for lib.damon.DamonSession: `.collect()` returns the
    CURRENT frame's `damon.json` classified bytes directly, so this test
    proves lib.serve wires DAMON output into SummaryAccumulator correctly
    without re-deriving DAMON's own classification math (tests/test_damon.py
    already does that in isolation)."""

    def __init__(self, targets, frame_box: Dict[str, int], **_kw) -> None:
        self.targets = targets
        self._frame_box = frame_box
        self.kdamond_idx = 1
        self.last_class_bytes: Dict[str, int] = {}

    @property
    def thresholds(self) -> Dict[str, Any]:
        return {"hot_rate_pct": 5, "warm_rate_pct": 1, "cold_age_s": 30, "idle_age_s": 120}

    def __enter__(self) -> "_FrameDamonSession":
        return self

    def __exit__(self, *exc: Any) -> None:
        return None

    def collect(self) -> List[Dict[str, Any]]:
        path = FRAMES_DIR / str(self._frame_box["n"]) / "damon.json"
        self.last_class_bytes = json.loads(path.read_text())
        return []

    def recommit_targets(self, pids: List[int]) -> None:
        self.targets = pids


def _frame_paths(n: int):
    frame = FRAMES_DIR / str(n)
    return frame, frame / "proc"


def _run_full_lifecycle(scope: str, monkeypatch, tmp_path) -> Dict[str, Any]:
    n_frames = 5
    frame_box = {"n": 0}
    root_link = tmp_path / "cgroup_root"
    proc_link = tmp_path / "proc_root"
    frame0, proc0 = _frame_paths(0)
    root_link.symlink_to(frame0)
    proc_link.symlink_to(proc0)

    session_box: Dict[str, Any] = {}

    def advance(_seconds: float) -> None:
        frame_box["n"] += 1
        idx = frame_box["n"]
        if idx < n_frames:
            frame, proc = _frame_paths(idx)
            root_link.unlink()
            root_link.symlink_to(frame)
            proc_link.unlink()
            proc_link.symlink_to(proc)
        else:
            session_box["sess"].stop_event.set()

    mono_box = {"t": 0.0}

    def sampler_clock() -> float:
        value = mono_box["t"]
        mono_box["t"] += 1.0
        return value

    def fake_damon_session(targets, **kw):
        return _FrameDamonSession(targets, frame_box, **kw)

    monkeypatch.setattr(damon_mod, "DamonSession", fake_damon_session)

    clock_box = {"t": EPOCH_START}
    server = serve.SessionServer(
        sessions_dir=str(tmp_path / "sessions"),
        cgroup_root=str(root_link),
        proc_root=str(proc_link),
        clock=lambda: clock_box["t"],
        sampler_clock=sampler_clock,
        sampler_sleep=advance,
        session_id_fn=lambda: SESSION_ID,
    )

    # Built via _create_session_locked directly, NOT server._dispatch({"verb":
    # "start", ...}) / handle_start — handle_start spawns a real background
    # thread running _session_loop(sess) for this same session, which would
    # race against the synchronous _session_loop(sess) call below (two
    # concurrent loops fighting over the same fake-clock/frame-advancing
    # `sampler_sleep`, corrupting the frame sequence non-deterministically).
    # This test drives the loop from exactly one place, per the module
    # docstring's "never via a real threading.Thread" for this tier.
    resolved_cgroup = targets_mod.find_container_cgroup(CONTAINER_ID, root=server.cgroup_root)
    with server._lock:
        sess = server._create_session_locked(
            container_id=CONTAINER_ID, cgroup=resolved_cgroup, scope=scope, token=None,
            interval=1.0, damon_req="on",
            meta={"lane": "l", "project": "p", "worktree": "w", "commit": None,
                  "run_gate_revision": 0, "kind": "command", "expected": None},
        )
        server._sessions[sess.session_id] = sess
        server._by_target[(CONTAINER_ID, None)] = sess.session_id

    assert sess.baseline_memory_bytes == 524288000
    assert sess.pids_at_start == 3

    session_box["sess"] = sess
    server._session_loop(sess)  # runs synchronously: no real thread involved

    clock_box["t"] = EPOCH_END
    stop_resp = server._dispatch({"verb": "stop", "session": sess.session_id})
    assert stop_resp["ok"] is True
    return stop_resp["summary"]


def _canon(doc: Dict[str, Any]) -> str:
    return json.dumps(doc, sort_keys=True, indent=2)


class TestFullLifecycleGoldenReproduction:
    def test_container_shared_scope_matches_summary_v1(self, monkeypatch, tmp_path):
        result = _run_full_lifecycle("container-shared", monkeypatch, tmp_path)
        golden = json.loads((FIXTURES / "summary-v1.json").read_text())
        assert _canon(result) == _canon(golden)

    def test_container_scope_matches_summary_container_v1(self, monkeypatch, tmp_path):
        result = _run_full_lifecycle("container", monkeypatch, tmp_path)
        golden = json.loads((FIXTURES / "summary-container-v1.json").read_text())
        assert _canon(result) == _canon(golden)


# ── a session-loop crash is caught, not left live forever ───────────────

def test_session_loop_error_finalizes_the_session_as_aborted(simple_server, monkeypatch):
    resp = simple_server._dispatch(_start_req())
    session_id = resp["session"]
    sess = simple_server._sessions[session_id]
    sess.thread.join(timeout=5.0) if sess.stop_event.is_set() else None
    sess.stop_event.set()
    sess.thread.join(timeout=5.0)  # let the real (empty) loop finish first

    # Re-drive _session_loop by hand with a Sampler.run that raises, proving
    # the except-and-finalize path without racing a live background thread.
    sess.finished = False
    sess.stop_event = threading.Event()

    def boom(self, should_stop, on_sample, on_topology):
        raise RuntimeError("synthetic session failure")

    monkeypatch.setattr("lib.sampler.Sampler.run", boom)
    simple_server._session_loop(sess)
    assert sess.finished is True
    assert sess.error == "synthetic session failure"
    manifest = json.loads((Path(simple_server.sessions_dir) / session_id / "manifest.json").read_text())
    assert manifest["aborted_reason"] == "session-error:synthetic session failure"

    # A second crash on an ALREADY-finished session must not re-finalize it
    # (the `if not sess.finished` guard inside the except handler).
    sess.stop_event = threading.Event()
    simple_server._session_loop(sess)
    assert sess.error == "synthetic session failure"


def test_manifest_target_follow_children_is_false(simple_server):
    # `_manifest_for`'s one target entry is never a `@follow` group (that is
    # the collector-tier `cgprofile attach` mode's own thing, DESIGN.md
    # §4.4 -- a daemon session's single subject target never expands into
    # a group), so `follow_children` is always written `False`.
    resp = simple_server._dispatch(_start_req())
    session_id = resp["session"]
    manifest = json.loads((Path(simple_server.sessions_dir) / session_id / "manifest.json").read_text())
    assert manifest["targets"][0]["follow_children"] is False
    sess = simple_server._sessions[session_id]
    sess.stop_event.set()
    sess.thread.join(timeout=5.0)


def test_finalize_session_locked_never_joins_its_own_thread(simple_server):
    # `_finalize_session_locked` runs from INSIDE the session's own
    # sampling thread on the session-error path (`_session_loop`'s own
    # `except Exception` calls it directly, synchronously, no dispatch
    # through a second thread) -- `sess.thread is not threading.current_
    # thread()` must stay False in exactly that case, or `.join()` raises
    # `RuntimeError: cannot join current thread`. A `is not` -> `is`
    # mutant on the SECOND `is not` in that guard (there are two on the
    # same line; the first is pinned by other tests) flips exactly this.
    resp = simple_server._dispatch(_start_req())
    session_id = resp["session"]
    sess = simple_server._sessions[session_id]
    real_thread = sess.thread
    sess.stop_event.set()
    real_thread.join(timeout=5.0)   # let the real background thread actually finish
    sess.thread = threading.current_thread()   # simulate calling FROM that thread
    with simple_server._lock:
        simple_server._finalize_session_locked(sess, aborted_reason="test")   # must not raise
    assert sess.finished is True


# ── SIGTERM/SIGINT: every live session is finalized as daemon-stopped ────

def test_stop_all_sessions_marks_every_live_session_daemon_stopped(simple_server):
    resp = simple_server._dispatch(_start_req())
    session_id = resp["session"]
    simple_server._stop_all_sessions(aborted_reason="daemon-stopped")
    manifest = json.loads((Path(simple_server.sessions_dir) / session_id / "manifest.json").read_text())
    assert manifest["status"] == "aborted"
    assert manifest["aborted_reason"] == "daemon-stopped"
    assert simple_server._sessions[session_id].finished is True
    # `_manifest_for`'s "duration" is computed only once BOTH started/ended
    # epochs are present -- `simple_server`'s fixed clock makes them equal
    # (a real, non-null 0.0), which is still enough to catch an `is not
    # None` -> `is None` mutant on either half of that guard (the mutant
    # would report `duration: null` in the completely ordinary case where
    # both epochs are real values, not just some unreachable edge case).
    assert manifest["duration"] == 0.0


def test_serve_forever_installs_signal_handlers_that_request_shutdown(tmp_path, monkeypatch):
    server = serve.SessionServer(sessions_dir=str(tmp_path / "sessions"))
    installed: Dict[int, Any] = {}

    def fake_signal(sig, handler):
        installed[sig] = handler
        return signal.SIG_DFL

    monkeypatch.setattr(signal, "signal", fake_signal)
    monkeypatch.setattr(server, "_bind", lambda: setattr(server, "_stopping", True))
    monkeypatch.setattr(server, "_close_socket", lambda: None)
    server.serve_forever()
    assert signal.SIGTERM in installed and signal.SIGINT in installed
    server._stopping = False
    installed[signal.SIGTERM](signal.SIGTERM, None)
    assert server._stopping is True


# ── _create_session_locked edge cases (slice/DAMON branches) ────────────

def test_create_session_with_no_slice_ancestor_leaves_slice_cgroup_none(simple_server):
    with simple_server._lock:
        sess = simple_server._create_session_locked(
            container_id=SIMPLE_CONTAINER_ID, cgroup="/docker-noslice.scope", scope="container",
            token=None, interval=1.0, damon_req="off",
            meta={"lane": "x", "project": "p", "worktree": "w", "commit": None,
                  "run_gate_revision": 1, "kind": "command", "expected": None},
        )
    assert sess.slice_name is None
    assert sess.slice_cgroup is None


def test_damon_unavailable_reason_reaches_the_stopped_summary(simple_server):
    # `_create_session_locked` calls `acc.mark_damon_unavailable(reason)`
    # only when a reason was actually set -- an `is not None` -> `is None`
    # mutant there would either call it wrongly (reason=None, a no-op-ish
    # value) or, for the genuinely-unavailable case this test drives, skip
    # it outright, leaving `SummaryAccumulator._damon_unavailable_reason`
    # None and `finalize()`'s damon block reporting status "on" instead of
    # "unavailable". `sess.damon_status`/`sess.damon_unavailable_reason`
    # (asserted by the sibling test below) are set independently of `acc`,
    # so only a real stop's own summary catches this.
    resp = simple_server._dispatch(
        _start_req(scope="container", token=None, damon="on")
    )
    session_id = resp["session"]
    assert resp["damon"] == "unavailable:no pids to monitor yet"
    sess = simple_server._sessions[session_id]
    sess.stop_event.set()
    sess.thread.join(timeout=5.0)
    stop_resp = simple_server._dispatch({"verb": "stop", "session": session_id})
    assert stop_resp["summary"]["damon"]["status"] == "unavailable"
    assert stop_resp["summary"]["damon"]["reason"] == "no pids to monitor yet"


def test_create_session_damon_on_with_no_pids_is_unavailable(simple_server):
    resolved = targets_mod.find_container_cgroup(SIMPLE_CONTAINER_ID, root=simple_server.cgroup_root)
    with simple_server._lock:
        sess = simple_server._create_session_locked(
            container_id=SIMPLE_CONTAINER_ID, cgroup=resolved, scope="container", token=None,
            interval=1.0, damon_req="on",
            meta={"lane": "x", "project": "p", "worktree": "w", "commit": None,
                  "run_gate_revision": 1, "kind": "command", "expected": None},
        )
    assert sess.damon_status == "unavailable:no pids to monitor yet"
    assert sess.damon_unavailable_reason == "no pids to monitor yet"
    assert sess.damon_session is None


def test_damon_collect_failure_degrades_only_that_session(simple_server, monkeypatch):
    scope_path = (
        Path(simple_server.cgroup_root)
        / f"dev.slice/dev-background.slice/docker-{SIMPLE_CONTAINER_ID}.scope"
    )
    (scope_path / "cgroup.procs").write_text("123\n")
    closed: List[bool] = []

    class BrokenDamonSession:
        kdamond_idx = 7
        thresholds = {"hot_rate_pct": 50, "warm_rate_pct": 5, "cold_age_s": 30, "idle_age_s": 120}

        def __init__(self, targets, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            closed.append(True)

        def collect(self):
            raise RuntimeError("synthetic collect failure")

    monkeypatch.setattr(damon_mod, "DamonSession", BrokenDamonSession)
    start_resp = simple_server._dispatch(_start_req(damon="on"))
    assert start_resp["damon"] == "unavailable:RuntimeError: synthetic collect failure"
    sess = simple_server._sessions[start_resp["session"]]
    sess.stop_event.set()
    sess.thread.join(timeout=5.0)
    stop_resp = simple_server._dispatch({"verb": "stop", "session": sess.session_id})
    assert stop_resp["summary"]["damon"]["status"] == "unavailable"
    assert stop_resp["series"]["damon"] is None
    assert closed == [True]


def test_create_session_damon_on_with_pids_but_damon_unavailable(tmp_path, monkeypatch):
    # Forces damon_mod.available() False regardless of what this host's own
    # /sys/kernel/mm/damon actually exposes, so DamonSession.__enter__()
    # raises DamonSessionError deterministically on any machine.
    monkeypatch.setattr(damon_mod, "SysfsInterface", None)

    root = tmp_path / "cgroup"
    write_cgroup(root, "", cgroup_files())
    write_cgroup(root, "dev.slice", cgroup_files())
    write_cgroup(root, "dev.slice/dev-background.slice", cgroup_files())
    scope_rel = f"dev.slice/dev-background.slice/docker-{SIMPLE_CONTAINER_ID}.scope"
    write_cgroup(root, scope_rel, cgroup_files())
    (root / scope_rel / "cgroup.procs").write_text("123\n")

    proc = tmp_path / "proc"
    proc.mkdir()
    server = serve.SessionServer(
        sessions_dir=str(tmp_path / "sessions"), cgroup_root=str(root), proc_root=str(proc),
    )
    resolved = targets_mod.find_container_cgroup(SIMPLE_CONTAINER_ID, root=server.cgroup_root)
    with server._lock:
        sess = server._create_session_locked(
            container_id=SIMPLE_CONTAINER_ID, cgroup=resolved, scope="container", token=None,
            interval=1.0, damon_req="on",
            meta={"lane": "x", "project": "p", "worktree": "w", "commit": None,
                  "run_gate_revision": 1, "kind": "command", "expected": None},
        )
        server._sessions[sess.session_id] = sess
    assert sess.damon_status.startswith("unavailable:")
    assert sess.damon_requested_on is True
    assert sess.damon_session is None
    entry = server._status_entry(sess)
    assert entry["live"]["damon"] == {"status": "unavailable", "hot_bytes_recent": None}


# ── subtree discovery cadence inside _on_session_sample ──────────────────

class _StubDamonSession:
    """Bare enough to drive _on_session_sample's DAMON-present branches
    without any real kernel/sysfs interaction."""

    def __init__(self) -> None:
        self.kdamond_idx = 0
        self.recommit_calls: List[List[int]] = []
        self.last_class_bytes = {"hot": 1, "warm": 1, "cold": 1, "idle": 1}

    def collect(self) -> List[Dict[str, Any]]:
        return []

    def recommit_targets(self, pids: List[int]) -> None:
        self.recommit_calls.append(list(pids))

    @property
    def thresholds(self) -> Dict[str, Any]:
        return {"hot_rate_pct": 5, "warm_rate_pct": 1, "cold_age_s": 30, "idle_age_s": 120}


def test_on_session_sample_discovery_due_and_not_due_and_status_damon_on(simple_server):
    resolved = targets_mod.find_container_cgroup(SIMPLE_CONTAINER_ID, root=simple_server.cgroup_root)
    with simple_server._lock:
        sess = simple_server._create_session_locked(
            container_id=SIMPLE_CONTAINER_ID, cgroup=resolved, scope="container-shared",
            token="a-real-token-99", interval=1.0, damon_req="off",
            meta={"lane": "x", "project": "p", "worktree": "w", "commit": None,
                  "run_gate_revision": 1, "kind": "command", "expected": None},
        )
        simple_server._sessions[sess.session_id] = sess

    stub = _StubDamonSession()
    sess.damon_session = stub
    abs_target = os.path.join(simple_server.cgroup_root, sess.cgroup.lstrip("/"))

    simple_server._on_session_sample(
        sess, {"mono": 0.0, "cg": {sess.cgroup: {}}, "host": {}}, abs_target, None
    )
    assert sess.last_discovery_mono == 0.0
    assert stub.recommit_calls == [[]]

    simple_server._on_session_sample(
        sess, {"mono": 1.0, "cg": {sess.cgroup: {}}, "host": {}}, abs_target, None
    )
    assert sess.last_discovery_mono == 0.0  # not due yet (delta 1.0 < 2.0)
    assert len(stub.recommit_calls) == 1

    simple_server._on_session_sample(
        sess, {"mono": 3.0, "cg": {sess.cgroup: {}}, "host": {}}, abs_target, None
    )
    assert sess.last_discovery_mono == 3.0  # due again (delta 3.0 >= 2.0)
    assert len(stub.recommit_calls) == 2

    status_resp = simple_server._dispatch({"verb": "status", "session": sess.session_id})
    assert status_resp["ok"] is True
    assert status_resp["session"]["live"]["damon"] == {"status": "on", "hot_bytes_recent": 1}

    # A discovery-due tick with NO damon session attached (damon off, or not
    # yet entered) must skip recommit_targets entirely rather than crash.
    sess.damon_session = None
    simple_server._on_session_sample(
        sess, {"mono": 6.0, "cg": {sess.cgroup: {}}, "host": {}}, abs_target, None
    )
    assert sess.last_discovery_mono == 6.0

    # `sess.rundir.append("damon", damon_bytes)` is guarded by the same
    # `damon_bytes is not None` shape -- one real entry per tick where a
    # damon session was attached (the first three ticks above), and NONE
    # for the last (damon_session=None) tick; an `is not None` -> `is None`
    # mutant there would invert this exactly (append a null damon record
    # for the damon-off tick, and skip every real one).
    damon_records = list(sess.rundir.read("damon"))
    assert damon_records == [stub.last_class_bytes] * 3


def test_on_session_sample_discovery_due_at_the_exact_interval_boundary(simple_server):
    # `(mono - last_discovery_mono) >= DISCOVERY_INTERVAL_SECONDS` must
    # trigger AT the boundary, not only strictly past it -- a `>=` -> `>`
    # mutant is invisible to the existing due/not-due test above (it only
    # exercises delta=1.0 < 2.0 and delta=3.0 > 2.0, never delta==2.0
    # exactly).
    resolved = targets_mod.find_container_cgroup(SIMPLE_CONTAINER_ID, root=simple_server.cgroup_root)
    with simple_server._lock:
        sess = simple_server._create_session_locked(
            container_id=SIMPLE_CONTAINER_ID, cgroup=resolved, scope="container-shared",
            token="a-real-token-99", interval=1.0, damon_req="off",
            meta={"lane": "x", "project": "p", "worktree": "w", "commit": None,
                  "run_gate_revision": 1, "kind": "command", "expected": None},
        )
        simple_server._sessions[sess.session_id] = sess
    abs_target = os.path.join(simple_server.cgroup_root, sess.cgroup.lstrip("/"))

    simple_server._on_session_sample(
        sess, {"mono": 0.0, "cg": {sess.cgroup: {}}, "host": {}}, abs_target, None
    )
    assert sess.last_discovery_mono == 0.0
    simple_server._on_session_sample(
        sess, {"mono": serve.DISCOVERY_INTERVAL_SECONDS, "cg": {sess.cgroup: {}}, "host": {}},
        abs_target, None,
    )
    assert sess.last_discovery_mono == serve.DISCOVERY_INTERVAL_SECONDS  # due exactly AT the boundary


def test_on_session_sample_no_token_path_recommits_only_on_pid_set_change(simple_server):
    """RW-15: the no-token path re-discovers `cgroup.procs` on the SAME
    discovery cadence as the token path (not every tick) and recommits
    DAMON targets only when the pid set actually changed — mirrors the
    token-path test above (`test_on_session_sample_discovery_due_and_not_
    due_and_status_damon_on`) one level down, since there is no
    SubtreeResolver here to own the cache (`sess.no_token_pids` is it)."""
    scope_path = (
        Path(simple_server.cgroup_root)
        / f"dev.slice/dev-background.slice/docker-{SIMPLE_CONTAINER_ID}.scope"
    )
    (scope_path / "cgroup.procs").write_text("100\n")
    resolved = targets_mod.find_container_cgroup(SIMPLE_CONTAINER_ID, root=simple_server.cgroup_root)
    with simple_server._lock:
        sess = simple_server._create_session_locked(
            container_id=SIMPLE_CONTAINER_ID, cgroup=resolved, scope="container",
            token=None, interval=1.0, damon_req="off",
            meta={"lane": "x", "project": "p", "worktree": "w", "commit": None,
                  "run_gate_revision": 1, "kind": "command", "expected": None},
        )
        simple_server._sessions[sess.session_id] = sess
    assert sess.no_token_pids == [100]  # captured atomically as sample zero

    stub = _StubDamonSession()
    sess.damon_session = stub
    abs_target = os.path.join(simple_server.cgroup_root, sess.cgroup.lstrip("/"))

    # mono=0.0: the pid set was already captured for sample zero, so this
    # discovery confirms [100] without an unnecessary recommit.
    simple_server._on_session_sample(
        sess, {"mono": 0.0, "cg": {sess.cgroup: {}}, "host": {}}, abs_target, None
    )
    assert sess.last_discovery_mono == 0.0
    assert sess.no_token_pids == [100]
    assert stub.recommit_calls == []

    # mono=1.0: not due yet (delta 1.0 < 2.0) => pids reused from cache,
    # cgroup.procs is not even re-read, no recommit.
    simple_server._on_session_sample(
        sess, {"mono": 1.0, "cg": {sess.cgroup: {}}, "host": {}}, abs_target, None
    )
    assert sess.last_discovery_mono == 0.0
    assert len(stub.recommit_calls) == 0

    # mono=3.0: due again (delta 3.0 >= 2.0); the pid set really changed
    # (100 -> 100,200) => recommit with the new set.
    (scope_path / "cgroup.procs").write_text("100\n200\n")
    simple_server._on_session_sample(
        sess, {"mono": 3.0, "cg": {sess.cgroup: {}}, "host": {}}, abs_target, None
    )
    assert sess.last_discovery_mono == 3.0
    assert sess.no_token_pids == [100, 200]
    assert len(stub.recommit_calls) == 1

    # mono=5.0: due again, but cgroup.procs is UNCHANGED => no recommit,
    # even though this tick did re-read the file (RW-15's whole point: cheap
    # to re-check, but never recommit DAMON for a no-op discovery).
    simple_server._on_session_sample(
        sess, {"mono": 5.0, "cg": {sess.cgroup: {}}, "host": {}}, abs_target, None
    )
    assert sess.last_discovery_mono == 5.0
    assert len(stub.recommit_calls) == 1

    # A discovery-due tick with NO damon session attached must skip
    # recommit_targets entirely rather than crash — the no-token twin of
    # the token-path test's own final case.
    sess.damon_session = None
    (scope_path / "cgroup.procs").write_text("100\n200\n300\n")
    simple_server._on_session_sample(
        sess, {"mono": 7.0, "cg": {sess.cgroup: {}}, "host": {}}, abs_target, None
    )
    assert sess.last_discovery_mono == 7.0
    assert sess.no_token_pids == [100, 200, 300]


def test_on_session_sample_treats_a_partial_cpu_baseline_as_unreadable(simple_server):
    # The two previous CPU fields are a pair.  If an interrupted or restored
    # session has only the counter but no timestamp, it is not a usable rate
    # baseline and must be carried forward without attempting `mono - None`.
    resolved = targets_mod.find_container_cgroup(SIMPLE_CONTAINER_ID, root=simple_server.cgroup_root)
    with simple_server._lock:
        sess = simple_server._create_session_locked(
            container_id=SIMPLE_CONTAINER_ID, cgroup=resolved, scope="container-shared",
            token=None, interval=1.0, damon_req="off",
            meta={"lane": "x", "project": "p", "worktree": "w", "commit": None,
                  "run_gate_revision": 1, "kind": "command", "expected": None},
        )
        simple_server._sessions[sess.session_id] = sess
    sess._prev_cpu_usage_usec = 1_000_000
    sess._prev_mono = None
    abs_target = os.path.join(simple_server.cgroup_root, sess.cgroup.lstrip("/"))

    simple_server._on_session_sample(
        sess, {"mono": 1.0, "cg": {sess.cgroup: {}}, "host": {}}, abs_target, None
    )

    assert sess.live_cpu_cores_recent is None

# ── host snapshot: observe_slices and non-slice children ─────────────────

def test_host_snapshot_includes_observe_slices_and_skips_non_slice_children(tmp_path):
    root = tmp_path / "cgroup"
    write_cgroup(root, "", cgroup_files())
    write_cgroup(root, "dev.slice", cgroup_files())
    write_cgroup(root, "dev.slice/dev-background.slice", cgroup_files())
    write_cgroup(root, "dev.slice/some-container.scope", cgroup_files())  # not a .slice
    write_cgroup(root, "game.slice", cgroup_files())  # only reachable via --observe-slices

    proc = tmp_path / "proc"
    proc.mkdir()
    (proc / "loadavg").write_text("1 1 1 1/1 1\n")
    (proc / "meminfo").write_text("MemTotal: 1 kB\n")
    pressure = proc / "pressure"
    pressure.mkdir()
    for name in ("cpu", "memory", "io"):
        (pressure / name).write_text(
            "some avg10=0 avg60=0 avg300=0 total=0\nfull avg10=0 avg60=0 avg300=0 total=0\n"
        )

    server = serve.SessionServer(
        sessions_dir=str(tmp_path / "sessions"), cgroup_root=str(root), proc_root=str(proc),
        # "dev-background.slice" is ALREADY discovered as a child of dev.slice —
        # naming it again in --observe-slices must be a harmless no-op (the
        # `name not in slice_names` skip branch), not a duplicate entry.
        observe_slices=["game.slice", "dev-background.slice"],
    )
    snapshot = server._host_snapshot()
    assert set(snapshot["slices"]) == {"dev.slice", "dev-background.slice", "game.slice"}


# ── malformed session ids on stop/report ──────────────────────────────────

def test_stop_with_a_malformed_session_id(simple_server):
    resp = simple_server._dispatch({"verb": "stop", "session": "not-a-session-id"})
    assert resp["error"]["code"] == "unknown-session"
    # Same shape as the status test above: must be rejected by the first
    # (regex) guard, not fall through to the second (`on_disk is None`) --
    # an `or` -> `and` mutant there is only visible via the message's
    # repr-quoting.
    assert resp["error"]["message"] == "no session 'not-a-session-id' is live or on record"


def test_report_with_a_malformed_session_id(simple_server):
    resp = simple_server._dispatch({"verb": "report", "session": "not-a-session-id"})
    assert resp["error"]["code"] == "unknown-session"
    # handle_report's fallback message is worded entirely differently
    # ("no finished session ... on record", no "is live or", no repr) --
    # an `or` -> `and` mutant on the first guard falls through to it.
    assert resp["error"]["message"] == "no session 'not-a-session-id' is live or on record"


# ── socket plumbing: stale files, unlink failures, short reads, accept() ──

def test_bind_removes_a_stale_socket_file(tmp_path):
    socket_path = str(tmp_path / "ctl.sock")
    Path(socket_path).write_text("stale")
    server = serve.SessionServer(sessions_dir=str(tmp_path / "sessions"), socket_path=socket_path)
    server._bind()
    try:
        assert os.path.exists(socket_path)
    finally:
        server._close_socket()


def test_bind_with_a_bare_relative_socket_path_skips_the_dirname_makedirs(tmp_path, monkeypatch):
    # os.path.dirname("ctl.sock") == "" — the `if socket_dir:` guard's False
    # branch, exercised only by a socket path with no directory component.
    monkeypatch.chdir(tmp_path)
    server = serve.SessionServer(sessions_dir=str(tmp_path / "sessions"), socket_path="ctl.sock")
    server._bind()
    try:
        assert os.path.exists(tmp_path / "ctl.sock")
    finally:
        server._close_socket()


def test_close_socket_is_a_noop_when_never_bound(tmp_path):
    server = serve.SessionServer(sessions_dir=str(tmp_path / "sessions"))
    server._close_socket()  # must not raise; socket_path never existed


def test_close_socket_swallows_an_unlink_oserror(tmp_path):
    socket_path = tmp_path / "ctl.sock"
    socket_path.mkdir()  # a directory at this path: os.unlink() on it raises
    server = serve.SessionServer(sessions_dir=str(tmp_path / "sessions"), socket_path=str(socket_path))
    server._close_socket()  # must not raise
    assert socket_path.exists()


class _FakeConn:
    def __init__(self, chunks: List[bytes]) -> None:
        self._chunks = list(chunks)
        self.sent: List[bytes] = []
        self.closed = False

    def settimeout(self, value: float) -> None:
        pass

    def recv(self, n: int) -> bytes:
        return self._chunks.pop(0) if self._chunks else b""

    def sendall(self, data: bytes) -> None:
        self.sent.append(data)

    def close(self) -> None:
        self.closed = True


def test_handle_connection_breaks_on_a_short_read_with_no_trailing_newline(tmp_path):
    server = serve.SessionServer(sessions_dir=str(tmp_path / "sessions"))
    conn = _FakeConn([b"not json, no newline"])
    server._handle_connection(conn)
    assert conn.closed is True
    assert len(conn.sent) == 1
    resp = json.loads(conn.sent[0].decode("utf-8"))
    assert resp["error"]["code"] == "bad-argument"


def test_handle_connection_survives_an_unhandled_dispatch_exception(tmp_path, monkeypatch, capsys):
    # RG-55 live acceptance (2026-09-12): a bug in a handler must not crash
    # `_accept_loop`/`serve_forever` — the whole daemon process, taking
    # every OTHER live session down with it. Found live: a raw OSError from
    # `DamonSession.__enter__()` (since fixed at its source) escaped
    # `_dispatch`'s narrower `except RequestError` uncaught. This test
    # forces an unrelated, unanticipated exception straight out of
    # `_dispatch` as a defense-in-depth check on `_handle_connection` itself
    # — the connection must close with NO reply (never a malformed one) and
    # the failure must be visible on stderr, not silently swallowed.
    server = serve.SessionServer(sessions_dir=str(tmp_path / "sessions"))

    def _boom(req):
        raise ValueError("synthetic unanticipated handler bug")

    monkeypatch.setattr(server, "_dispatch", _boom)
    conn = _FakeConn([b'{"verb": "version"}\n'])
    server._handle_connection(conn)
    assert conn.closed is True
    assert conn.sent == []  # no reply at all — never a malformed one
    err = capsys.readouterr().err
    assert "version" in err
    assert "ValueError" in err
    assert "synthetic unanticipated handler bug" in err


def test_handle_connection_dispatch_exception_flushes_stderr_immediately(tmp_path, monkeypatch):
    # `flush=True` on this exact print is deliberate: an unhandled handler
    # bug can mean the daemon process is about to exit (a wrapping restart,
    # or the very crash this defense-in-depth line exists to survive) --
    # a buffered write lost on exit would erase the one diagnostic naming
    # what blew up. `capsys` (the sibling test above) proves the TEXT
    # eventually appears but not that it was flushed rather than buffered
    # -- spy on the real `print(...)` call's kwargs instead.
    server = serve.SessionServer(sessions_dir=str(tmp_path / "sessions"))

    def _boom(req):
        raise ValueError("synthetic unanticipated handler bug")

    monkeypatch.setattr(server, "_dispatch", _boom)

    seen_kwargs: Dict[str, Any] = {}
    real_print = print

    def _spy_print(*args, **kwargs):
        seen_kwargs.update(kwargs)
        return real_print(*args, **kwargs)

    monkeypatch.setattr("builtins.print", _spy_print)
    conn = _FakeConn([b'{"verb": "version"}\n'])
    server._handle_connection(conn)
    assert seen_kwargs.get("flush") is True


def test_accept_loop_survives_a_handler_bug_and_keeps_serving(tmp_path, monkeypatch):
    # Same failure, one layer up: prove `_accept_loop` itself does not
    # propagate the exception and die — a second, healthy connection must
    # still be served afterward.
    server = serve.SessionServer(sessions_dir=str(tmp_path / "sessions"))
    calls = {"n": 0}
    real_dispatch = server._dispatch

    def _flaky(req):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ValueError("synthetic unanticipated handler bug")
        return real_dispatch(req)

    monkeypatch.setattr(server, "_dispatch", _flaky)

    conns = [
        _FakeConn([b'{"verb": "version"}\n']),
        _FakeConn([b'{"verb": "version"}\n']),
    ]

    class _FakeSock:
        def __init__(self, conns):
            self._conns = list(conns)

        def accept(self):
            if not self._conns:
                raise OSError("no more fake connections")
            return self._conns.pop(0), ("fake",)

        def close(self) -> None:
            pass

    monkeypatch.setattr(server, "_bind", lambda: setattr(server, "_sock", _FakeSock(conns)))
    server._accept_loop()
    assert conns[0].sent == []  # the crashing request: no reply
    assert len(conns[1].sent) == 1  # the next request: served normally
    resp = json.loads(conns[1].sent[0].decode("utf-8"))
    assert resp["ok"] is True
    assert "cgprofile" in resp


def test_accept_loop_breaks_on_a_non_timeout_oserror(tmp_path, monkeypatch):
    server = serve.SessionServer(sessions_dir=str(tmp_path / "sessions"))

    class _FakeSock:
        def accept(self):
            raise OSError("boom")

    monkeypatch.setattr(server, "_bind", lambda: setattr(server, "_sock", _FakeSock()))
    calls: List[str] = []
    monkeypatch.setattr(server, "_close_socket", lambda: calls.append("closed"))
    server._accept_loop()
    assert calls == ["closed"]


# ── a real Unix socket round trip ────────────────────────────────────────

def test_real_socket_round_trip_version_and_bad_request(tmp_path):
    socket_path = str(tmp_path / "ctl.sock")
    server = serve.SessionServer(
        sessions_dir=str(tmp_path / "sessions"), socket_path=socket_path,
        accept_timeout=0.05, clock=lambda: EPOCH_START,
    )
    thread = threading.Thread(target=server._accept_loop, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 5.0
        while not os.path.exists(socket_path) and time.monotonic() < deadline:
            time.sleep(0.01)
        assert os.path.exists(socket_path)

        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(5.0)
            client.connect(socket_path)
            client.sendall(b'{"verb": "version"}\n')
            data = client.recv(65536)
        resp = json.loads(data.decode("utf-8"))
        assert resp["ok"] is True
        assert resp["contract"] == 1
        assert resp["cgprofile"] == serve.CGPROFILE_VERSION

        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(5.0)
            client.connect(socket_path)
            client.sendall(b"not json at all\n")
            data = client.recv(65536)
        bad_resp = json.loads(data.decode("utf-8"))
        assert bad_resp == {"ok": False, "contract": 1,
                             "error": {"code": "bad-argument",
                                       "message": "request was not valid JSON"}}

        for interval in ("not-a-number", "NaN", "inf"):
            request = {
                "verb": "start", "target": f"containerid:{SIMPLE_CONTAINER_ID}",
                "scope": "container", "token": None, "damon": "off",
                "interval": interval, "meta": {},
            }
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                client.settimeout(5.0)
                client.connect(socket_path)
                client.sendall((json.dumps(request) + "\n").encode("utf-8"))
                interval_resp = json.loads(client.recv(65536).decode("utf-8"))
            assert interval_resp["ok"] is False
            assert interval_resp["contract"] == 1
            assert interval_resp["error"]["code"] == "bad-argument"

        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(5.0)
            client.connect(socket_path)
            client.sendall(b"\n")  # blank request: server closes with no reply
            assert client.recv(65536) == b""
    finally:
        server.request_shutdown()
        thread.join(timeout=5.0)
        assert not os.path.exists(socket_path)


@pytest.mark.parametrize("reply", [
    b'{"ok": true, "contract": 2, "cgprofile": "1.0.0", "daemon": {}}\n',
    b'[]\n',
    b'{"contract": 1, "cgprofile": "1.0.0", "daemon": {}}\n',
])
def test_ctl_rejects_wrong_major_list_and_missing_ok_fake_socket(tmp_path, reply, capsys):
    # This fake Unix peer isolates the CLI acceptance gate from the live
    # daemon. A reachable socket is not sufficient evidence of a compatible
    # contract response.
    socket_path = str(tmp_path / "ctl.sock")
    peer = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    peer.bind(socket_path)
    peer.listen(1)

    def serve_reply():
        try:
            conn, _ = peer.accept()
            with conn:
                conn.recv(65536)
                conn.sendall(reply)
        finally:
            peer.close()

    thread = threading.Thread(target=serve_reply, daemon=True)
    thread.start()
    try:
        args = cg.build_parser().parse_args(["ctl", "--socket", socket_path, "version"])
        assert cg.cmd_ctl(args) == 3
        captured = capsys.readouterr()
        assert captured.out == ""
        assert "invalid response" in captured.err
    finally:
        thread.join(timeout=5.0)


def _ctl_response_fixture(verb: str) -> Dict[str, Any]:
    if verb == "version":
        name = "version-v1.json"
    elif verb == "start":
        name = "start-v1.json"
    elif verb == "status":
        name = "status-v1.json"
    elif verb == "host":
        name = "host-v1.json"
    elif verb == "stop":
        name = "stop-v1.json"
    else:
        raise AssertionError(f"no fixture for {verb}")
    return json.loads((FIXTURES / name).read_text())


def _ctl_args(verb: str) -> Any:
    argv = ["ctl", "--socket", "/tmp/unused-cgprofile-test.sock", verb]
    if verb == "start":
        argv += [
            "--target", f"containerid:{SIMPLE_CONTAINER_ID}",
            "--scope", "container", "--meta", "{}",
        ]
    elif verb in ("stop", "report"):
        argv.append("s-20260912T101500Z-9f01")
    return cg.build_parser().parse_args(argv)


def _run_ctl_with_response(monkeypatch, capsys, verb: str, response: Any):
    monkeypatch.setattr(cg, "_ctl_roundtrip", lambda *args: response)
    result = cg.cmd_ctl(_ctl_args(verb))
    return result, capsys.readouterr()


@pytest.mark.parametrize("verb,response", [
    ("status", "session"),
    ("report", "report"),
    ("gc", "gc"),
])
def test_ctl_accepts_the_remaining_contract_success_shapes(monkeypatch, capsys, verb, response):
    if response == "session":
        valid = _ctl_response_fixture("status")
        valid["session"] = valid.pop("sessions")[0]
    elif response == "report":
        valid = {"ok": True, "contract": 1, "path": "/sessions/s-1/report.html"}
    else:
        valid = {"ok": True, "contract": 1, "removed": [], "kept": 0}

    rc, captured = _run_ctl_with_response(monkeypatch, capsys, verb, valid)

    assert rc == 0
    assert json.loads(captured.out) == valid
    assert captured.err == ""


@pytest.mark.parametrize("verb,mutator", [
    ("version", lambda response: response.pop("cgprofile")),
    ("report", lambda response: response.pop("path")),
    ("gc", lambda response: response.pop("removed")),
])
def test_ctl_rejects_success_responses_missing_required_fields(
    monkeypatch, capsys, verb, mutator,
):
    if verb == "version":
        response = _ctl_response_fixture(verb)
    elif verb == "report":
        response = {"ok": True, "contract": 1, "path": "/report.html"}
    else:
        response = {"ok": True, "contract": 1, "removed": [], "kept": 0}
    mutator(response)

    rc, captured = _run_ctl_with_response(monkeypatch, capsys, verb, response)

    assert rc == 3
    assert captured.out == ""
    assert "invalid response" in captured.err


@pytest.mark.parametrize("response", [
    {"ok": False, "contract": 1, "error": {}},
    {"ok": True, "contract": 1, "cgprofile": "1.0.0", "daemon": []},
    {
        "ok": True, "contract": 1, "cgprofile": "1.0.0",
        "daemon": {
            "name": "", "started_at": "now", "damon": "off",
            "damon_default": "off", "sessions_live": 0, "max_sessions": 0,
        },
    },
    {
        "ok": True, "contract": 1, "cgprofile": "",
        "daemon": {
            "name": "cgprofile-host-daemon", "started_at": "now", "damon": "off",
            "damon_default": "off", "sessions_live": 0, "max_sessions": 0,
        },
    },
    {
        "ok": True, "contract": 1, "cgprofile": "1.0.0",
        "daemon": {
            "name": "cgprofile-host-daemon", "started_at": "now", "damon": "off",
            "damon_default": "off", "sessions_live": -1, "max_sessions": 0,
        },
    },
])
def test_ctl_fails_closed_on_invalid_version_payloads(monkeypatch, capsys, response):
    rc, captured = _run_ctl_with_response(monkeypatch, capsys, "version", response)

    assert rc == 3
    assert captured.out == ""
    assert "invalid response" in captured.err


def test_ctl_validator_falls_through_only_for_an_unknown_internal_verb():
    # `build_parser()` exposes only the closed ctl vocabulary, but this
    # direct envelope check keeps the validator's final dispatch branch
    # honest if an internal caller ever supplies a new verb first.
    assert cg._validate_ctl_response("unknown-test-verb", {"ok": True, "contract": 1}) is None


@pytest.mark.parametrize("field,value", [
    ("at", None),
    ("loadavg", {}),
    ("meminfo", []),
    ("slices", []),
])
def test_ctl_fails_closed_on_invalid_host_payloads(monkeypatch, capsys, field, value):
    response = _ctl_response_fixture("host")
    if value is None:
        response["host"].pop(field)
    else:
        response["host"][field] = value

    rc, captured = _run_ctl_with_response(monkeypatch, capsys, "host", response)

    assert rc == 3
    assert captured.out == ""
    assert "invalid response" in captured.err


@pytest.mark.parametrize("mutator", [
    lambda entry: entry.pop("session"),
    lambda entry: entry.__setitem__("scope", "host"),
    lambda entry: entry.__setitem__("elapsed_seconds", "2"),
    lambda entry: entry.__setitem__("elapsed_seconds", float("nan")),
    lambda entry: entry.__setitem__("target", []),
])
def test_ctl_fails_closed_on_invalid_status_session_entries(monkeypatch, capsys, mutator):
    response = _ctl_response_fixture("status")
    response["session"] = response.pop("sessions")[0]
    mutator(response["session"])

    rc, captured = _run_ctl_with_response(monkeypatch, capsys, "status", response)

    assert rc == 3
    assert captured.out == ""
    assert "invalid response" in captured.err


@pytest.mark.parametrize("sessions", [
    {},
    [{}],
])
def test_ctl_fails_closed_on_invalid_status_session_lists(monkeypatch, capsys, sessions):
    response = _ctl_response_fixture("status")
    response["sessions"] = sessions

    rc, captured = _run_ctl_with_response(monkeypatch, capsys, "status", response)

    assert rc == 3
    assert captured.out == ""
    assert "invalid response" in captured.err


@pytest.mark.parametrize("mutator", [
    lambda response: response.__setitem__("scope", "host"),
    lambda response: response.__setitem__("damon", "maybe"),
    lambda response: response.__setitem__("damon", 1),
    lambda response: response.__setitem__("reused", 1),
    lambda response: response.__setitem__("interval_seconds", 0.1),
    lambda response: response.__setitem__("interval_seconds", float("nan")),
    lambda response: response["target"].__setitem__("pids_at_start", "3"),
    lambda response: response["target"].__setitem__("pids_at_start", -1),
])
def test_ctl_fails_closed_on_invalid_start_payloads(monkeypatch, capsys, mutator):
    response = _ctl_response_fixture("start")
    mutator(response)

    rc, captured = _run_ctl_with_response(monkeypatch, capsys, "start", response)

    assert rc == 3
    assert captured.out == ""
    assert "invalid response" in captured.err


@pytest.mark.parametrize("mutator", [
    lambda response: response.__setitem__("already_stopped", 1),
    lambda response: response["summary"].__setitem__("schema", 2),
    lambda response: response.__setitem__("session_dir", ""),
    lambda response: response.__setitem__("series", []),
    lambda response: response["series"].__setitem__("damon", 1),
])
def test_ctl_fails_closed_on_invalid_stop_payloads(monkeypatch, capsys, mutator):
    response = _ctl_response_fixture("stop")
    mutator(response)

    rc, captured = _run_ctl_with_response(monkeypatch, capsys, "stop", response)

    assert rc == 3
    assert captured.out == ""
    assert "invalid response" in captured.err


@pytest.mark.parametrize("removed,kept", [
    ("not-a-list", 0),
    ([1], 0),
    ([], "0"),
    ([], -1),
])
def test_ctl_fails_closed_on_invalid_gc_payloads(monkeypatch, capsys, removed, kept):
    response = {"ok": True, "contract": 1, "removed": removed, "kept": kept}

    rc, captured = _run_ctl_with_response(monkeypatch, capsys, "gc", response)

    assert rc == 3
    assert captured.out == ""
    assert "invalid response" in captured.err


# ── CLI golden round-trip: `cgprofile ctl` over a real socket (C5) ────────
#
# Reuses the exact frame sequence `_run_full_lifecycle` drives synchronously
# (single-threaded, no socket at all) — this suite instead runs the SAME
# five frames through a REAL background sampling thread and a REAL
# accept-loop thread, with `cgprofile ctl` (`cg.main`, this package's actual
# CLI entry point) as the client under test, because that is the only way
# to prove the wire protocol works end to end rather than merely that
# `_dispatch()` does (the handoff's own read-list item 5 asks for exactly
# this). `ctl status` needs to land *mid-session* (frame 2, matching
# status-v1.json) — something a synchronous `_session_loop` call cannot do,
# since that blocks the calling thread for the whole session — so the fake
# `sampler_sleep` below is a two-event lockstep: it pauses the REAL
# sampling thread right after the chosen tick and waits for the test's own
# thread to release it once its `ctl` call has landed.

class _LockstepAdvance:
    """`_run_full_lifecycle`'s own frame-advance-or-stop `advance()`, plus
    one pause point the test thread controls via two `threading.Event`s.
    """

    def __init__(self, frame_box: Dict[str, int], root_link: Path, proc_link: Path,
                 n_frames: int, pause_after_tick: int) -> None:
        self.frame_box = frame_box
        self.root_link = root_link
        self.proc_link = proc_link
        self.n_frames = n_frames
        self.pause_after_tick = pause_after_tick
        self.tick_done = threading.Event()
        self.resume = threading.Event()
        self.sess: Any = None  # set by the test thread once `start` returns

    def __call__(self, _seconds: float) -> None:
        k = self.frame_box["n"]
        if k == self.pause_after_tick:
            self.tick_done.set()
            self.resume.wait(timeout=5.0)
        idx = k + 1
        if idx < self.n_frames:
            frame, proc = _frame_paths(idx)
            self.root_link.unlink()
            self.root_link.symlink_to(frame)
            self.proc_link.unlink()
            self.proc_link.symlink_to(proc)
            self.frame_box["n"] = idx
        else:
            self.sess.stop_event.set()


def _counting_sampler_clock() -> Callable[[], float]:
    box = {"t": 0.0}

    def clock() -> float:
        value = box["t"]
        box["t"] += 1.0
        return value

    return clock


def _wait_for_socket(socket_path: str) -> None:
    deadline = time.monotonic() + 5.0
    while not os.path.exists(socket_path) and time.monotonic() < deadline:
        time.sleep(0.01)
    assert os.path.exists(socket_path)


def test_cli_golden_round_trip_version_start_status_stop(monkeypatch, tmp_path, capsys):
    n_frames = 5
    frame_box = {"n": 0}
    root_link = tmp_path / "cgroup_root"
    proc_link = tmp_path / "proc_root"
    frame0, proc0 = _frame_paths(0)
    root_link.symlink_to(frame0)
    proc_link.symlink_to(proc0)

    monkeypatch.setattr(damon_mod, "available", lambda: True)
    monkeypatch.setattr(
        damon_mod, "DamonSession",
        lambda targets, **kw: _FrameDamonSession(targets, frame_box, **kw),
    )

    lockstep = _LockstepAdvance(frame_box, root_link, proc_link, n_frames, pause_after_tick=2)
    server_start_epoch = datetime(2026, 9, 12, 9, 0, 0, tzinfo=timezone.utc).timestamp()
    clock_box = {"t": server_start_epoch}
    socket_path = str(tmp_path / "ctl.sock")
    server = serve.SessionServer(
        sessions_dir=str(tmp_path / "sessions"), socket_path=socket_path,
        cgroup_root=str(root_link), proc_root=str(proc_link),
        clock=lambda: clock_box["t"], sampler_clock=_counting_sampler_clock(),
        sampler_sleep=lockstep, session_id_fn=lambda: SESSION_ID, accept_timeout=0.05,
    )
    accept_thread = threading.Thread(target=server._accept_loop, daemon=True)
    accept_thread.start()
    try:
        _wait_for_socket(socket_path)

        clock_box["t"] = EPOCH_START
        # These exact values (not just any well-formed meta) because
        # status-v1.json's fixture echoes them back verbatim in the "meta"
        # block of the mid-session status snapshot below.
        meta = json.dumps({
            "lane": "rg55-fixture-lane", "project": "run-gate-project",
            "worktree": "/workspaces/vbpub",
            "commit": "0123456789abcdef0123456789abcdef01234567",
            "run_gate_revision": 41, "kind": "command", "expected": None,
        })
        rc = cg.main([
            "ctl", "--socket", socket_path, "start",
            "--target", f"containerid:{CONTAINER_ID}", "--scope", "container-shared",
            "--damon", "on", "--interval", "1.0", "--meta", meta,
        ])
        start_out = json.loads(capsys.readouterr().out)
        assert rc == 0
        assert _canon(start_out) == _canon(json.loads((FIXTURES / "start-v1.json").read_text()))

        lockstep.sess = server._sessions[SESSION_ID]

        # -- version, while exactly one session is live --
        rc = cg.main(["ctl", "--socket", socket_path, "version"])
        version_out = json.loads(capsys.readouterr().out)
        assert rc == 0
        assert _canon(version_out) == _canon(json.loads((FIXTURES / "version-v1.json").read_text()))

        # -- status, paused mid-session at frame 2 --
        assert lockstep.tick_done.wait(timeout=5.0)
        clock_box["t"] = EPOCH_START + 2.0
        rc = cg.main(["ctl", "--socket", socket_path, "status"])
        status_out = json.loads(capsys.readouterr().out)
        assert rc == 0
        assert _canon(status_out) == _canon(json.loads((FIXTURES / "status-v1.json").read_text()))
        lockstep.resume.set()

        # -- stop, once the session has run to completion (frame 4) --
        lockstep.sess.thread.join(timeout=5.0)
        clock_box["t"] = EPOCH_END
        rc = cg.main(["ctl", "--socket", socket_path, "stop", SESSION_ID])
        stop_out = json.loads(capsys.readouterr().out)
        assert rc == 0
        golden_stop = json.loads((FIXTURES / "stop-v1.json").read_text())
        # session_dir is this test's own tmp_path, never the production
        # default the fixture was hand-written against — asserted precisely,
        # then normalized before the rest of the document is compared
        # byte-for-byte (everything else, including the whole "summary"
        # object, must still match exactly).
        expected_dir = os.path.join(str(tmp_path / "sessions"), SESSION_ID)
        assert stop_out["session_dir"] == expected_dir
        stop_out["session_dir"] = golden_stop["session_dir"]
        assert _canon(stop_out) == _canon(golden_stop)
    finally:
        server.request_shutdown()
        accept_thread.join(timeout=5.0)


def test_cli_error_v1_unknown_session(tmp_path, capsys):
    socket_path = str(tmp_path / "ctl.sock")
    server = serve.SessionServer(
        sessions_dir=str(tmp_path / "sessions"), socket_path=socket_path, accept_timeout=0.05,
    )
    accept_thread = threading.Thread(target=server._accept_loop, daemon=True)
    accept_thread.start()
    try:
        _wait_for_socket(socket_path)
        rc = cg.main(["ctl", "--socket", socket_path, "status", "s-20260912T090000Z-0000"])
        out = json.loads(capsys.readouterr().out)
        assert rc == 2
        assert _canon(out) == _canon(json.loads((FIXTURES / "error-v1.json").read_text()))
    finally:
        server.request_shutdown()
        accept_thread.join(timeout=5.0)


def test_cli_host_v1(tmp_path, capsys):
    frame4 = FRAMES_DIR / "4"
    socket_path = str(tmp_path / "ctl.sock")
    server = serve.SessionServer(
        sessions_dir=str(tmp_path / "sessions"), socket_path=socket_path,
        cgroup_root=str(frame4), proc_root=str(frame4 / "proc"),
        clock=lambda: EPOCH_END, accept_timeout=0.05,
    )
    accept_thread = threading.Thread(target=server._accept_loop, daemon=True)
    accept_thread.start()
    try:
        _wait_for_socket(socket_path)
        rc = cg.main(["ctl", "--socket", socket_path, "host"])
        out = json.loads(capsys.readouterr().out)
        assert rc == 0
        assert _canon(out) == _canon(json.loads((FIXTURES / "host-v1.json").read_text()))
    finally:
        server.request_shutdown()
        accept_thread.join(timeout=5.0)
