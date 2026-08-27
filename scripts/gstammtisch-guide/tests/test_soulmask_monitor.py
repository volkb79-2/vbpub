"""R0 tests for soulmask-monitor.py — fast, mocked: no real docker/nsenter/
network/root. RconRelay is exercised against a FakeProc double with
select.select patched to avoid needing real OS file descriptors.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
MODULE_PATH = HERE.parent / "files" / "usr" / "local" / "sbin" / "soulmask-monitor.py"


def load_module():
    spec = importlib.util.spec_from_file_location("soulmask_monitor", MODULE_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


mon = load_module()


# ── formatting ───────────────────────────────────────────────────────────────

def test_fmt_fps_none_is_dash():
    assert mon.fmt_fps(None) == mon.DASH


def test_fmt_fps_formats_one_decimal():
    assert mon.fmt_fps(29.71) == "29.7"
    assert mon.fmt_fps(0) == "0.0"


def test_fps_column_present_in_table_and_legend():
    assert mon.RCON_COLUMNS == (("fps", "fps", 5),)
    assert "`fps`" in mon.legend_for_width()


# ── env_of ───────────────────────────────────────────────────────────────────

def test_env_of_parses_docker_inspect_output(monkeypatch):
    def fake_run(argv, **kw):
        class R:
            returncode = 0
            stdout = "PATH=/usr/bin\nRCON_PORT=19000\nRCON_PASSWORD=hunter2\n"
        return R()
    monkeypatch.setattr(mon.subprocess, "run", fake_run)
    assert mon.env_of("cid", "RCON_PORT") == "19000"
    assert mon.env_of("cid", "RCON_PASSWORD") == "hunter2"
    assert mon.env_of("cid", "MISSING") == ""


def test_env_of_returns_empty_on_docker_failure(monkeypatch):
    def fake_run(argv, **kw):
        class R:
            returncode = 1
            # Non-empty and matching, so a reader that ignored returncode
            # would incorrectly return "1234" instead of "" — the fixture
            # must give the wrong-path code something real to return.
            stdout = "RCON_PORT=1234\n"
        return R()
    monkeypatch.setattr(mon.subprocess, "run", fake_run)
    assert mon.env_of("cid", "RCON_PORT") == ""


# ── RconRelay against a fake Popen ───────────────────────────────────────────

class FakeProc:
    """Stands in for subprocess.Popen: readline() serves canned lines,
    poll()/terminate()/wait() behave like a real Popen well enough for
    RconRelay's needs. select.select is patched separately since it can't
    operate on a plain Python object."""

    def __init__(self, lines, alive=True):
        self._lines = list(lines)
        self.stdin = self
        self.stdout = self
        self._alive = alive
        self.terminated = False
        self.written = []

    def write(self, data):
        self.written.append(data)

    def flush(self):
        pass

    def readline(self):
        return self._lines.pop(0) if self._lines else ""

    def poll(self):
        return None if self._alive else 1

    def terminate(self):
        self.terminated = True
        self._alive = False

    def kill(self):
        self._alive = False

    def wait(self, timeout=None):
        return 0


@pytest.fixture(autouse=True)
def _select_always_ready(monkeypatch):
    """RconRelay._readline uses select.select on proc.stdout, which requires
    a real fd — FakeProc isn't one. Patch select to always report ready so
    FakeProc's plain readline() drives the timing instead."""
    monkeypatch.setattr(mon.select, "select", lambda r, w, x, timeout: (r, [], []))


@pytest.fixture
def _fake_creds(monkeypatch):
    monkeypatch.setattr(mon, "env_of", lambda cid, key: {
        "RCON_PORT": "19000", "RCON_PASSWORD": "pw"}.get(key, ""))


def test_poll_fps_happy_path(monkeypatch, _fake_creds):
    proc = FakeProc([
        '{"ok": true, "event": "connected"}\n',
        '{"ok": true, "reply": "Average FPS: 42.50"}\n',
    ])
    monkeypatch.setattr(mon.subprocess, "Popen", lambda *a, **kw: proc)
    relay = mon.RconRelay("cid", 111)
    fps = relay.poll_fps(1000.0, 111)
    assert fps == 42.5
    assert proc.written == ["ServerFPS\n"]


def test_poll_fps_missing_pid_returns_none_without_spawning(monkeypatch):
    spawned = []
    monkeypatch.setattr(mon.subprocess, "Popen", lambda *a, **kw: spawned.append(1) or FakeProc([]))
    relay = mon.RconRelay("cid", None)
    assert relay.poll_fps(1000.0, None) is None
    assert spawned == []


def test_poll_fps_no_password_returns_none(monkeypatch):
    monkeypatch.setattr(mon, "env_of", lambda cid, key: "")
    relay = mon.RconRelay("cid", 111)
    assert relay.poll_fps(1000.0, 111) is None


def test_poll_fps_connect_failure_reported_and_backs_off(monkeypatch, _fake_creds):
    proc = FakeProc(['{"ok": false, "error": "authentication failed"}\n'])
    monkeypatch.setattr(mon.subprocess, "Popen", lambda *a, **kw: proc)
    relay = mon.RconRelay("cid", 111)
    assert relay.poll_fps(1000.0, 111) is None
    assert "authentication failed" in relay.last_error

    # A second attempt inside the backoff window must NOT spawn again.
    spawn_calls = []
    monkeypatch.setattr(mon.subprocess, "Popen",
                         lambda *a, **kw: spawn_calls.append(1) or FakeProc([]))
    assert relay.poll_fps(1000.0 + 1, 111) is None
    assert spawn_calls == []

    # Past the backoff window, it tries again.
    proc2 = FakeProc(['{"ok": true, "event": "connected"}\n',
                       '{"ok": true, "reply": "Average FPS: 10.0"}\n'])
    monkeypatch.setattr(mon.subprocess, "Popen", lambda *a, **kw: proc2)
    fps = relay.poll_fps(1000.0 + mon.RCON_RESPAWN_BACKOFF_S + 1, 111)
    assert fps == 10.0


def test_poll_fps_no_reply_within_timeout_is_treated_as_dead(monkeypatch, _fake_creds):
    proc = FakeProc(['{"ok": true, "event": "connected"}\n'])  # no reply queued for the poll itself
    monkeypatch.setattr(mon.subprocess, "Popen", lambda *a, **kw: proc)
    relay = mon.RconRelay("cid", 111)
    assert relay.poll_fps(1000.0, 111) is None
    assert proc.terminated


def test_poll_fps_malformed_line_does_not_crash(monkeypatch, _fake_creds):
    proc = FakeProc(['{"ok": true, "event": "connected"}\n', 'not json at all\n'])
    monkeypatch.setattr(mon.subprocess, "Popen", lambda *a, **kw: proc)
    relay = mon.RconRelay("cid", 111)
    assert relay.poll_fps(1000.0, 111) is None
    assert "unexpected relay output" in relay.last_error


def test_poll_fps_command_error_kills_and_returns_none(monkeypatch, _fake_creds):
    proc = FakeProc(['{"ok": true, "event": "connected"}\n',
                      '{"ok": false, "error": "connection closed by server"}\n'])
    monkeypatch.setattr(mon.subprocess, "Popen", lambda *a, **kw: proc)
    relay = mon.RconRelay("cid", 111)
    assert relay.poll_fps(1000.0, 111) is None
    assert proc.terminated


def test_poll_fps_no_fps_number_in_reply_returns_none(monkeypatch, _fake_creds):
    proc = FakeProc(['{"ok": true, "event": "connected"}\n',
                      '{"ok": true, "reply": "unrecognized text"}\n'])
    monkeypatch.setattr(mon.subprocess, "Popen", lambda *a, **kw: proc)
    relay = mon.RconRelay("cid", 111)
    assert relay.poll_fps(1000.0, 111) is None


def test_poll_fps_pid_change_kills_old_process_and_respawns(monkeypatch, _fake_creds):
    proc1 = FakeProc(['{"ok": true, "event": "connected"}\n',
                       '{"ok": true, "reply": "Average FPS: 1.0"}\n'])
    procs = iter([proc1])
    monkeypatch.setattr(mon.subprocess, "Popen", lambda *a, **kw: next(procs))
    relay = mon.RconRelay("cid", 111)
    assert relay.poll_fps(1000.0, 111) == 1.0

    proc2 = FakeProc(['{"ok": true, "event": "connected"}\n',
                       '{"ok": true, "reply": "Average FPS: 2.0"}\n'])
    monkeypatch.setattr(mon.subprocess, "Popen", lambda *a, **kw: proc2)
    fps = relay.poll_fps(1001.0, 222)  # container restarted -> new PID
    assert proc1.terminated
    assert fps == 2.0


def test_spawn_argv_uses_nsenter_into_the_pid_netns(monkeypatch, _fake_creds):
    captured = {}

    def fake_popen(argv, **kw):
        captured["argv"] = argv
        return FakeProc(['{"ok": true, "event": "connected"}\n',
                          '{"ok": true, "reply": "Average FPS: 5.0"}\n'])
    monkeypatch.setattr(mon.subprocess, "Popen", fake_popen)
    relay = mon.RconRelay("cid", 4242)
    relay.poll_fps(1000.0, 4242)
    argv = captured["argv"]
    assert argv[0] == "nsenter"
    assert argv[1] == "--net=/proc/4242/ns/net"
    assert "--relay" in argv
    assert "19000" in argv
    assert "pw" in argv


def test_close_terminates_running_child(monkeypatch, _fake_creds):
    proc = FakeProc(['{"ok": true, "event": "connected"}\n',
                      '{"ok": true, "reply": "Average FPS: 5.0"}\n'])
    monkeypatch.setattr(mon.subprocess, "Popen", lambda *a, **kw: proc)
    relay = mon.RconRelay("cid", 111)
    relay.poll_fps(1000.0, 111)
    relay.close()
    assert proc.terminated


def test_attach_rcon_respects_no_rcon_flag():
    servers = [{"cid": "a", "pid": 1}, {"cid": "b", "pid": 2}]
    mon.attach_rcon(servers, enabled=False)
    assert servers[0]["rcon"] is None
    assert servers[1]["rcon"] is None

    mon.attach_rcon(servers, enabled=True)
    assert isinstance(servers[0]["rcon"], mon.RconRelay)
    assert servers[0]["rcon"].cid == "a"
    assert servers[0]["rcon"].pid == 1


def test_close_rcon_is_safe_with_none_entries(monkeypatch, _fake_creds):
    proc = FakeProc(['{"ok": true, "event": "connected"}\n'])
    monkeypatch.setattr(mon.subprocess, "Popen", lambda *a, **kw: proc)
    relay = mon.RconRelay("cid", 1)
    relay.proc = proc  # pretend it's already connected
    servers = [{"rcon": relay}, {"rcon": None}]
    mon.close_rcon(servers)  # must not raise on the None entry
    assert proc.terminated


# ── table/header rendering with the fps column ───────────────────────────────

def _server(uuid="s1", pid=111):
    return {
        "cid": "c1", "name": uuid, "uuid": uuid, "pid": pid,
        "metrics_cgroup": "/x", "slice": "/x",
        "controls": {k: "?" for k, _ in mon.CONTROL_COLUMNS},
        "last_controls": {}, "ksm": {}, "last_ksm": {}, "role_label": "",
    }


def test_header_lines_include_fps_column():
    row1, row2, dash = mon.header_lines(1, False, [_server()])
    assert "fps" in row2


def test_table_row_renders_fps_and_dash(monkeypatch):
    server = _server()
    g = {"ram": 0, "anon": 0, "file": 0, "zpool": 0, "zeq": 0, "wra": 0,
         "wrf": 0, "zswpin": 0, "controls": server["controls"], "ksm": {},
         "band": {"min": "0", "high": "max", "writeback": "1"}, "fps": 29.71}
    server["sample"] = g
    server["rates"] = {"rfz": None, "rfd": None, "rff": None}
    row = mon.table_row([server], {}, None, None, None, None, None, None, False, False, False)
    assert "29.7" in row

    g2 = dict(g)
    g2["fps"] = None
    server["sample"] = g2
    row2 = mon.table_row([server], {}, None, None, None, None, None, None, False, False, False)
    assert mon.DASH in row2


def test_server_json_includes_fps():
    server = _server()
    g = {"ram": 0, "anon": 0, "file": 0, "zpool": 0, "zeq": 0,
         "controls": server["controls"], "ksm": {}, "fps": 12.3}
    obj = mon.server_json(server, g, None, None, None)
    assert obj["fps"] == 12.3


# ── --help: verbose, self-sufficient, and free of stale column names ────────

def test_help_description_does_not_require_root_or_docker():
    text = mon.help_description()
    assert "RES" in text and "memory.current" in text
    assert "T0_RAM" in text and "T1_RAM" in text


def test_help_description_has_no_stale_pak_column_names():
    # p_RAM/p_z/p_disk/p_rfz/p_rfd were a pre-tmpfs-split leftover that
    # never matched any real table header or JSON field — see the removed
    # COLUMN_GUIDE note in the source. Regression-guard against it coming
    # back.
    text = mon.help_description()
    for stale in ("p_RAM", "p_z`", "p_disk", "p_rfz", "p_rfd"):
        assert stale not in text, f"stale PAK-era column name resurfaced: {stale!r}"


def test_help_flag_works_as_a_real_subprocess_without_root(monkeypatch):
    # Proves the actual ordering, not just that help_description() returns
    # a string: argparse must exit on -h/--help BEFORE main()'s root/docker
    # checks run, so this must succeed even though the test process itself
    # is not root.
    result = subprocess.run(
        [sys.executable, str(MODULE_PATH), "--help"],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0
    assert "must run as root" not in result.stdout + result.stderr
    assert "T0_RAM" in result.stdout


# ── discover_live_servers / rescan_servers: appearing & disappearing ────────

class _FakeRcon:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


def _live(cid, pid=111, name=None):
    name = name or cid
    return {"cid": cid, "name": name, "pid": pid,
            "metrics_cgroup": f"/x/{cid}", "slice": f"/x/{cid}"}


def _tracked(cid, pid=111, rcon=None, tracker=None):
    return {
        "cid": cid, "name": cid, "uuid": cid, "pid": pid,
        "metrics_cgroup": f"/x/{cid}", "slice": f"/x/{cid}",
        "tracker": tracker if tracker is not None else object(),
        "rcon": rcon,
        "controls": {}, "last_controls": {}, "ksm": {}, "last_ksm": {},
    }


def test_rescan_servers_adds_a_new_server(monkeypatch):
    monkeypatch.setattr(mon, "env_of", lambda cid, key: "")  # no creds -> harmless RconRelay
    servers, changed = mon.rescan_servers([], [_live("new1")], rcon_enabled=True)
    assert changed is True
    assert [s["uuid"] for s in servers] == ["new1"]
    assert isinstance(servers[0]["rcon"], mon.RconRelay)
    assert isinstance(servers[0]["tracker"], mon.RateTracker)


def test_rescan_servers_new_server_respects_no_rcon():
    servers, changed = mon.rescan_servers([], [_live("new1")], rcon_enabled=False)
    assert changed is True
    assert servers[0]["rcon"] is None


def test_rescan_servers_removes_a_gone_server_and_closes_its_rcon():
    rcon = _FakeRcon()
    tracked = [_tracked("gone1", rcon=rcon)]
    servers, changed = mon.rescan_servers(tracked, live=[], rcon_enabled=True)
    assert changed is True
    assert servers == []
    assert rcon.closed is True


def test_rescan_servers_preserves_identity_for_an_unchanged_server():
    rcon = _FakeRcon()
    tracker = object()
    tracked = [_tracked("same1", pid=111, rcon=rcon, tracker=tracker)]
    servers, changed = mon.rescan_servers(tracked, live=[_live("same1", pid=111)], rcon_enabled=True)
    assert changed is False
    assert len(servers) == 1
    assert servers[0]["rcon"] is rcon          # same object — never rebuilt
    assert servers[0]["tracker"] is tracker    # same object — never rebuilt
    assert rcon.closed is False                # never touched


def test_rescan_servers_pid_change_updates_pid_without_rebuilding_rcon():
    rcon = _FakeRcon()
    tracker = object()
    tracked = [_tracked("restarted1", pid=111, rcon=rcon, tracker=tracker)]
    servers, changed = mon.rescan_servers(
        tracked, live=[_live("restarted1", pid=222)], rcon_enabled=True)
    assert changed is True
    assert servers[0]["pid"] == 222
    assert servers[0]["rcon"] is rcon          # RconRelay.poll_fps handles the
    assert servers[0]["tracker"] is tracker    # pid change itself, not a rebuild here
    assert rcon.closed is False


def test_rescan_servers_add_and_remove_together_only_touch_the_changed_ones():
    keep_rcon, gone_rcon = _FakeRcon(), _FakeRcon()
    tracked = [_tracked("keep1", rcon=keep_rcon), _tracked("gone1", rcon=gone_rcon)]
    live = [_live("keep1"), _live("new1")]
    servers, changed = mon.rescan_servers(tracked, live, rcon_enabled=False)
    assert changed is True
    uuids = {s["uuid"] for s in servers}
    assert uuids == {"keep1", "new1"}
    assert gone_rcon.closed is True
    kept = next(s for s in servers if s["uuid"] == "keep1")
    assert kept["rcon"] is keep_rcon


def test_rescan_servers_no_membership_change_reports_unchanged():
    tracked = [_tracked("stable1")]
    servers, changed = mon.rescan_servers(tracked, live=[_live("stable1")], rcon_enabled=False)
    assert changed is False
    assert servers is not tracked or servers == tracked  # list identity not load-bearing, contents are
    assert [s["uuid"] for s in servers] == ["stable1"]


# ── sample_all_servers: per-server isolation ─────────────────────────────────

def _write_fake_cgroup(path):
    path.mkdir()
    (path / "memory.stat").write_text(
        "anon 100\nfile 200\nzswapped 0\nworkingset_refault_anon 0\n"
        "workingset_refault_file 0\nzswpin 0\n")
    (path / "memory.current").write_text("1000\n")
    (path / "memory.zswap.current").write_text("0\n")


def test_sample_all_servers_isolates_one_disappeared_server_from_others(tmp_path):
    good_cg = tmp_path / "good"
    _write_fake_cgroup(good_cg)
    gone_cg = tmp_path / "gone"  # deliberately never created

    rcon = _FakeRcon()
    gone = _tracked("gone1", rcon=rcon, tracker=mon.RateTracker(["wra", "zswpin", "wrf"]))
    gone["metrics_cgroup"] = gone["slice"] = str(gone_cg)
    good = _tracked("good1", rcon=None, tracker=mon.RateTracker(["wra", "zswpin", "wrf"]))
    good["metrics_cgroup"] = good["slice"] = str(good_cg)

    # Failing entry listed FIRST — proves it doesn't abort the rest of the loop.
    result = mon.sample_all_servers([gone, good], 1000.0)

    assert [s["uuid"] for s in result] == ["good1"]
    assert "sample" in good and good["sample"]["ram"] == 1000
    assert rcon.closed is True


def test_sample_all_servers_all_healthy_keeps_everyone(tmp_path):
    cg1, cg2 = tmp_path / "s1", tmp_path / "s2"
    _write_fake_cgroup(cg1)
    _write_fake_cgroup(cg2)
    s1 = _tracked("s1", tracker=mon.RateTracker(["wra", "zswpin", "wrf"]))
    s2 = _tracked("s2", tracker=mon.RateTracker(["wra", "zswpin", "wrf"]))
    s1["metrics_cgroup"] = s1["slice"] = str(cg1)
    s2["metrics_cgroup"] = s2["slice"] = str(cg2)
    result = mon.sample_all_servers([s1, s2], 1000.0)
    assert [s["uuid"] for s in result] == ["s1", "s2"]
