"""Direct mode (cgprofile.direct): the collector handshake and the command run, with a fake collector."""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from cgprofile import direct
from cgprofile.cli import legacy
from cgprofile.ctlwire import WireFailure


class FakeChild:
    """A collector double: optionally touches the sentinel files when started or stopped."""

    def __init__(self, run_path, *, ready=True, done_on_stop=True, exits=None, wait_timeouts=0):
        self.run_path = run_path
        self.done_on_stop = done_on_stop
        self.exits = exits
        self.wait_timeouts = wait_timeouts
        self.terminated = False
        self.killed = False
        if ready:
            open(os.path.join(run_path, legacy.READY_FILE), "w").close()

    def poll(self):
        if self.done_on_stop and os.path.exists(os.path.join(self.run_path, legacy.STOP_FILE)):
            open(os.path.join(self.run_path, legacy.DONE_FILE), "w").close()
        return self.exits

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True

    def wait(self, timeout=None):
        if self.wait_timeouts:
            self.wait_timeouts -= 1
            raise subprocess.TimeoutExpired("collector", timeout)
        return 0


@pytest.fixture(autouse=True)
def _fast_sleep(monkeypatch):
    monkeypatch.setattr(direct.time, "sleep", lambda s: None)


def test_collector_command_targets_self_follow(tmp_path):
    command = direct.collector_command(str(tmp_path))
    assert command[:4] == [sys.executable, "-m", "cgprofile.cli.legacy", "_collect"]
    assert command[-2:] == ["--target", "self@follow"]
    assert str(tmp_path) in command


def test_note_goes_to_stderr(capsys):
    direct._note("hello")
    assert capsys.readouterr().err == "[cgprofilectl] hello\n"


def test_wait_for_sees_an_existing_file_and_times_out_otherwise(tmp_path):
    present = tmp_path / "ready"
    present.write_text("")
    direct.wait_for(str(present), 1.0, "x")
    with pytest.raises(WireFailure) as caught:
        direct.wait_for(str(tmp_path / "never"), 0.0, "the collector to start")
    assert (caught.value.exit_code, caught.value.code) == (1, "collector-timeout")


def test_wait_for_polls_until_the_file_appears(tmp_path, monkeypatch):
    target = tmp_path / "late"
    calls = []

    def sleeper(seconds):
        calls.append(seconds)
        target.write_text("")

    monkeypatch.setattr(direct.time, "sleep", sleeper)
    direct.wait_for(str(target), 5.0, "x")
    assert calls == [0.05]


def test_terminate_kills_a_child_that_ignores_sigterm(tmp_path):
    stubborn = FakeChild(str(tmp_path), ready=False, wait_timeouts=1)
    direct._terminate(stubborn)
    assert stubborn.terminated and stubborn.killed
    quiet = FakeChild(str(tmp_path), ready=False)
    direct._terminate(quiet)
    assert quiet.terminated and not quiet.killed


def test_start_collector_returns_the_child_when_ready(tmp_path, monkeypatch):
    seen = {}

    def popen(command, **kw):
        seen["command"] = command
        return FakeChild(str(tmp_path))

    monkeypatch.setattr(direct.subprocess, "Popen", popen)
    child = direct.start_collector(str(tmp_path), timeout=1.0)
    assert isinstance(child, FakeChild) and seen["command"][-1] == "self@follow"


def test_start_collector_terminates_a_child_that_never_gets_ready(tmp_path, monkeypatch):
    child = FakeChild(str(tmp_path), ready=False)
    monkeypatch.setattr(direct.subprocess, "Popen", lambda command, **kw: child)
    with pytest.raises(WireFailure) as caught:
        direct.start_collector(str(tmp_path), timeout=0.0)
    assert caught.value.code == "collector-timeout" and child.terminated


def test_stop_collector_waits_for_the_done_file(tmp_path):
    child = FakeChild(str(tmp_path))
    direct.stop_collector(str(tmp_path), child, timeout=5.0)
    assert (tmp_path / legacy.STOP_FILE).exists() and not child.terminated


def test_stop_collector_accepts_a_child_that_already_exited(tmp_path):
    child = FakeChild(str(tmp_path), done_on_stop=False, exits=0)
    direct.stop_collector(str(tmp_path), child, timeout=5.0)
    assert not child.terminated


def test_stop_collector_terminates_after_the_timeout(tmp_path, capsys):
    child = FakeChild(str(tmp_path), done_on_stop=False)
    direct.stop_collector(str(tmp_path), child, timeout=0.0)
    assert child.terminated and "terminating" in capsys.readouterr().err


def test_stop_collector_kills_a_child_that_will_not_exit(tmp_path):
    child = FakeChild(str(tmp_path), wait_timeouts=1)
    direct.stop_collector(str(tmp_path), child, timeout=5.0)
    assert child.killed


def _install_fake_collector(monkeypatch):
    holder = {}

    def start(run_path, timeout=0):
        holder["child"] = FakeChild(run_path)
        return holder["child"]

    monkeypatch.setattr(direct, "start_collector", start)
    return holder


def _marks(run_path):
    with open(os.path.join(run_path, "marks.jsonl"), encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def test_run_command_records_marks_and_exports_the_run_dir(tmp_path, monkeypatch):
    _install_fake_collector(monkeypatch)
    outcome = direct.run_command(
        [sys.executable, "-c", "import os, sys; sys.exit(3 if os.environ['CGPROFILE_RUN_DIR'] else 0)"],
        out_dir=str(tmp_path), phase=None, cgprofilectl_dir="/opt/cgprofilectl-bin")
    assert outcome.exit_status == 3 and os.path.isdir(outcome.run_dir)
    names = [m.get("name") for m in _marks(outcome.run_dir)]
    assert names[0] == os.path.basename(sys.executable) and names[-1].endswith(":done")
    assert os.path.exists(os.path.join(outcome.run_dir, legacy.STOP_FILE))


def test_run_command_puts_cgprofilectl_first_on_path_and_uses_the_phase(tmp_path, monkeypatch):
    _install_fake_collector(monkeypatch)
    probe = "import os, sys; sys.exit(0 if os.environ['PATH'].startswith('/opt/cgprofilectl-bin') else 9)"
    outcome = direct.run_command([sys.executable, "-c", probe], out_dir=str(tmp_path), phase="build",
                                 cgprofilectl_dir="/opt/cgprofilectl-bin", run_id="run-fixed")
    assert outcome.exit_status == 0 and outcome.run_dir.endswith("run-fixed")
    assert _marks(outcome.run_dir)[0]["name"] == "build"


def test_run_command_reports_a_command_that_cannot_start(tmp_path, monkeypatch):
    holder = _install_fake_collector(monkeypatch)
    with pytest.raises(WireFailure) as caught:
        direct.run_command(["/nonexistent/definitely-not-a-binary"], out_dir=str(tmp_path), phase=None,
                           cgprofilectl_dir="/x")
    assert (caught.value.exit_code, caught.value.code) == (1, "run-failed")
    run_dirs = [p for p in os.listdir(tmp_path)]
    assert len(run_dirs) == 1 and os.path.exists(os.path.join(tmp_path, run_dirs[0], legacy.STOP_FILE))
    assert holder["child"] is not None
