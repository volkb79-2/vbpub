"""R1 tests for soulmask-monitor.py's RCON integration.

More real than test_soulmask_monitor.py: RconRelay spawns the REAL
`soulmask_rcon.py --relay` as a genuine child subprocess (real pipes, real
select()-based reads, real JSON framing) talking to a real local
FakeRconServer over 127.0.0.1. The only thing faked is the `nsenter`
network-namespace hop itself — this devcontainer cannot join a sibling
container's netns (confirmed earlier this session: nsenter fails with
"cannot open /proc/<pid>/ns/net" here since this shell doesn't share the
host's PID namespace), so subprocess.Popen is patched to strip the leading
["nsenter", "--net=...", "--"] prefix RconRelay builds and run the real
command directly against 127.0.0.1 — soulmask_rcon.py's default --host.
This still exercises 100% of RconRelay's own logic and the real relay
subprocess/protocol; only the OS-level namespace join needs a real-host
smoke test (see README.md caveat).
"""
from __future__ import annotations

import importlib.util
import shutil
import sys
import time
from pathlib import Path

import pytest

from fake_rcon_server import FakeRconServer

HERE = Path(__file__).resolve().parent
MODULE_PATH = HERE.parent / "files" / "usr" / "local" / "sbin" / "soulmask-monitor.py"


def load_module():
    spec = importlib.util.spec_from_file_location("soulmask_monitor_r1", MODULE_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


mon = load_module()


def _strip_nsenter(argv):
    assert argv[0] == "nsenter", argv
    idx = argv.index("--")
    return argv[idx + 1:]


@pytest.fixture
def real_popen_without_nsenter(monkeypatch):
    real_popen = mon.subprocess.Popen

    def patched(argv, **kw):
        if argv and argv[0] == "nsenter":
            argv = _strip_nsenter(argv)
        return real_popen(argv, **kw)

    monkeypatch.setattr(mon.subprocess, "Popen", patched)


@pytest.fixture
def rcon_server():
    s = FakeRconServer(password="pw123")
    yield s
    s.stop()


def _creds(monkeypatch, server, password="pw123"):
    monkeypatch.setattr(mon, "env_of", lambda cid, key: {
        "RCON_PORT": str(server.port), "RCON_PASSWORD": password}.get(key, ""))


def test_real_relay_subprocess_reports_fps(monkeypatch, rcon_server, real_popen_without_nsenter):
    rcon_server.responder = lambda cmd: "Average FPS: 42.00\r\nPerformance level: Normal" if cmd == "ServerFPS" else "?"
    _creds(monkeypatch, rcon_server)
    relay = mon.RconRelay("fakecid", 12345)
    try:
        fps = relay.poll_fps(time.time(), 12345)
        assert fps == 42.0
    finally:
        relay.close()


def test_real_relay_reuses_one_connection_across_multiple_polls(monkeypatch, rcon_server, real_popen_without_nsenter):
    connects = []
    fps_value = [10.0]

    def responder(cmd):
        return f"Average FPS: {fps_value[0]:.2f}"

    rcon_server.responder = responder
    _creds(monkeypatch, rcon_server)
    relay = mon.RconRelay("fakecid", 12345)
    try:
        f1 = relay.poll_fps(time.time(), 12345)
        proc_after_first = relay.proc
        fps_value[0] = 20.0
        f2 = relay.poll_fps(time.time(), 12345)
        assert f1 == 10.0
        assert f2 == 20.0
        # Same child process reused for both polls — no respawn per poll.
        assert relay.proc is proc_after_first
    finally:
        relay.close()


def test_real_relay_wrong_password_reported_without_crashing(monkeypatch, rcon_server, real_popen_without_nsenter):
    _creds(monkeypatch, rcon_server, password="WRONG")
    relay = mon.RconRelay("fakecid", 12345)
    try:
        fps = relay.poll_fps(time.time(), 12345)
        assert fps is None
        assert "authentication failed" in relay.last_error
    finally:
        relay.close()


def test_real_relay_recovers_after_server_drops_connection(monkeypatch, rcon_server, real_popen_without_nsenter):
    # drop_after=1: the fake server closes right after replying to the 1st
    # command on a connection. soulmask_rcon.py's --relay reconnects
    # internally on the NEXT command, but that command still surfaces as an
    # {"ok": false} reply for the tick that hit the drop (the reconnect
    # only prepares the socket for what comes after, it doesn't retry the
    # failed command) — and RconRelay treats any {"ok": false} as fatal for
    # that child, killing and backing off rather than trying to reuse a
    # relay that just proved unreliable. So: one lost sample, one respawn,
    # then recovery — never a crash, never an infinite respawn loop.
    rcon_server.drop_after = 1
    rcon_server.responder = lambda cmd: "Average FPS: 33.00"
    _creds(monkeypatch, rcon_server)
    relay = mon.RconRelay("fakecid", 12345)
    try:
        ts = time.time()
        f1 = relay.poll_fps(ts, 12345)
        assert f1 == 33.0
        first_child = relay.proc

        f2 = relay.poll_fps(ts + 0.1, 12345)
        assert f2 is None
        assert relay.proc is None  # killed after the {"ok": false} reply
        assert first_child.poll() is not None

        # Still within backoff: no respawn attempted yet.
        f3 = relay.poll_fps(ts + 0.2, 12345)
        assert f3 is None
        assert relay.proc is None

        # Past the backoff window, a fresh relay child recovers.
        f4 = relay.poll_fps(ts + mon.RCON_RESPAWN_BACKOFF_S + 1, 12345)
        assert f4 == 33.0
    finally:
        relay.close()


def test_real_relay_close_terminates_child_process(monkeypatch, rcon_server, real_popen_without_nsenter):
    _creds(monkeypatch, rcon_server)
    relay = mon.RconRelay("fakecid", 12345)
    relay.poll_fps(time.time(), 12345)
    proc = relay.proc
    assert proc is not None
    relay.close()
    assert proc.poll() is not None  # process has actually exited


def test_rescan_servers_preserves_the_real_relay_connection_across_a_rescan(
        monkeypatch, rcon_server, real_popen_without_nsenter):
    # The R0 suite proves object-identity preservation with a fake rcon
    # double. This proves it end-to-end with the REAL soulmask_rcon.py
    # --relay subprocess and a real socket: a rescan that sees the same
    # server again must not cause a second connect+auth handshake.
    rcon_server.responder = lambda cmd: "Average FPS: 7.00" if cmd == "ServerFPS" else "?"
    _creds(monkeypatch, rcon_server)
    live = [{"cid": "fakecid", "name": "fakecid", "pid": 12345,
             "metrics_cgroup": "/x", "slice": "/x"}]

    servers, changed = mon.rescan_servers([], live, rcon_enabled=True)
    assert changed is True
    relay = servers[0]["rcon"]
    try:
        fps = relay.poll_fps(time.time(), 12345)
        assert fps == 7.0
        proc_after_connect = relay.proc
        assert proc_after_connect is not None

        # Rescan again with the identical live snapshot — nothing changed.
        servers, changed = mon.rescan_servers(servers, live, rcon_enabled=True)
        assert changed is False
        assert servers[0]["rcon"] is relay

        fps2 = relay.poll_fps(time.time(), 12345)
        assert fps2 == 7.0
        assert relay.proc is proc_after_connect  # same child — no reconnect
    finally:
        relay.close()


@pytest.mark.skipif(shutil.which("nsenter") is None, reason="nsenter not installed")
def test_nsenter_binary_exists_but_real_netns_join_needs_a_real_host():
    """Documents the known environment limitation rather than asserting
    anything about nsenter's behavior here: this devcontainer cannot join a
    sibling container's network namespace (no shared host PID namespace),
    confirmed live against the real Soulmask container this session
    ("cannot open /proc/<pid>/ns/net: No such file or directory"). The
    nsenter invocation RconRelay builds must be smoke-tested on the actual
    deployment host, not from here — see README.md."""
    assert shutil.which("nsenter") is not None
