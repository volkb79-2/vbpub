"""The leftover-children guard in ``conftest.py`` (package P11), tested by running tiny inner
test files in a fresh pytest process (``pytester``) with a copy of the guard as their conftest.

The inner runs are subprocess runs on purpose: an in-process inner run would share this
process's children, so the outer guard would see what the inner one deliberately leaves.
Every inner scenario removes what it starts, so the outer run leaves nothing behind either.
"""
from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import conftest

GUARD_SOURCE = Path(conftest.__file__).read_text(encoding="utf-8")

# Shared inner helpers: wait until a killed child is a zombie (never reaping it) so the
# guard's report ("zombie") is deterministic.
INNER_HEAD = '''\
import os
import subprocess
import time
from pathlib import Path

import pytest


def state_of(pid):
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return None
    return stat.rsplit(")", 1)[1].split()[0]


def become_zombie(process):
    process.kill()
    deadline = time.monotonic() + 30
    while state_of(process.pid) != "Z" and time.monotonic() < deadline:
        time.sleep(0.01)
    assert state_of(process.pid) == "Z"
'''


def _inner(pytester, body: str):
    """Run ``body`` (after the shared head) as an inner test file under a copy of the guard."""
    pytester.makeconftest(GUARD_SOURCE)
    pytester.makepyfile(test_inner=INNER_HEAD + body)
    return pytester.runpytest_subprocess("-p", "no:cacheprovider")


def _pid(pytester) -> int:
    return int((pytester.path / "pid.txt").read_text())


def test_an_unwaited_killed_child_is_a_zombie_leak_the_guard_names_and_reaps(pytester):
    result = _inner(pytester, '''
KEPT = []  # a dropped Popen object reaps its own finished child on garbage collection


def test_leaks_a_zombie():
    process = subprocess.Popen(["sleep", "30"])
    KEPT.append(process)
    Path("pid.txt").write_text(str(process.pid))
    become_zombie(process)


def test_zombie_is_gone_afterwards():
    assert state_of(int(Path("pid.txt").read_text())) is None
''')
    pid = _pid(pytester)
    assert result.ret != 0
    result.assert_outcomes(passed=2, errors=1)
    result.stdout.fnmatch_lines([f"*pid {pid} comm sleep (zombie)*"])
    assert not Path(f"/proc/{pid}").exists()


def test_an_orphaned_grandchild_killed_by_pid_is_a_leak_because_the_runner_is_a_subreaper(pytester):
    result = _inner(pytester, '''
def test_kills_an_orphan_without_reaping_it():
    started = subprocess.run(
        ["bash", "-c", "sleep 30 >/dev/null 2>&1 & echo $!"],
        capture_output=True, text=True, check=True,
    )
    pid = int(started.stdout)
    Path("pid.txt").write_text(str(pid))
    os.kill(pid, 9)
''')
    pid = _pid(pytester)
    assert result.ret != 0
    result.assert_outcomes(passed=1, errors=1)
    # The comm is not asserted: a shell's background child killed between fork and exec is
    # still named "bash" (measured once in a whole-suite run).
    result.stdout.fnmatch_lines([f"*pid {pid} comm *"])
    assert not Path(f"/proc/{pid}").exists()


def test_a_child_that_is_started_and_waited_for_passes(pytester):
    result = _inner(pytester, '''
def test_waits_properly():
    process = subprocess.Popen(["sleep", "0.01"])
    assert process.wait(timeout=30) == 0


def test_kills_and_waits_properly():
    process = subprocess.Popen(["sleep", "30"])
    process.kill()
    process.wait(timeout=30)
''')
    assert result.ret == 0
    result.assert_outcomes(passed=2)


def test_a_live_child_left_behind_is_killed_and_reaped_by_the_guard(pytester):
    result = _inner(pytester, '''
def test_leaves_a_live_child():
    process = subprocess.Popen(["sleep", "30"])
    Path("pid.txt").write_text(str(process.pid))
    assert state_of(process.pid) not in {None, "Z"}


def test_the_live_child_was_killed_and_reaped():
    assert state_of(int(Path("pid.txt").read_text())) is None
''')
    pid = _pid(pytester)
    assert result.ret != 0
    result.assert_outcomes(passed=2, errors=1)
    # status -9: the guard sent SIGKILL (a guard that merely waited would report 0 after 30 s)
    result.stdout.fnmatch_lines([f"*pid {pid} comm sleep (alive, ended with status -9)*"])
    assert not Path(f"/proc/{pid}").exists(), "the guard must kill and reap it, not the exit"


def test_a_child_that_existed_before_the_test_is_never_touched(pytester):
    result = _inner(pytester, '''
@pytest.fixture(scope="session")
def helper():
    process = subprocess.Popen(["sleep", "30"])
    Path("pid.txt").write_text(str(process.pid))
    yield process
    process.kill()
    process.wait(timeout=30)


def test_first_uses_the_helper(helper):
    assert state_of(helper.pid) not in {None, "Z"}


def test_second_still_finds_it_alive(helper):
    assert state_of(helper.pid) not in {None, "Z"}
''')
    assert result.ret == 0
    result.assert_outcomes(passed=2)
    assert not Path(f"/proc/{_pid(pytester)}").exists(), "the session fixture ended it"


def test_allow_children_marks_a_test_the_guard_does_not_fail(pytester):
    result = _inner(pytester, '''
KEPT = []


@pytest.mark.allow_children(reason="keeps a helper for the next test")
def test_keeps_a_child():
    KEPT.append(subprocess.Popen(["sleep", "30"]))
    Path("pid.txt").write_text(str(KEPT[0].pid))


def test_ends_the_kept_child():
    KEPT[0].kill()
    KEPT[0].wait(timeout=30)
''')
    assert result.ret == 0
    result.assert_outcomes(passed=2)
    assert not Path(f"/proc/{_pid(pytester)}").exists()


def _end(process):
    process.kill()
    process.wait(timeout=30)


def test_a_recorded_child_that_is_gone_or_replaced_is_never_signalled(monkeypatch):
    """The snapshot can be stale: another start time, another parent, or a reaped pid."""
    own = subprocess.Popen(["sleep", "30"])
    parent = subprocess.Popen(
        ["bash", "-c", "sleep 30 >/dev/null 2>&1 & echo $!; wait"], stdout=subprocess.PIPE, text=True,
    )
    grandchild = int(parent.stdout.readline())
    done = subprocess.Popen(["true"])
    done.wait(timeout=30)
    calls = []

    def spy(*args):
        calls.append(args)
        raise AssertionError("nothing may be signalled for a stale record")

    try:
        own_start = conftest.read_stat(own.pid)[3]
        grandchild_start = conftest.read_stat(grandchild)[3]
        with monkeypatch.context() as patch:
            patch.setattr(conftest.signal, "pidfd_send_signal", spy)
            patch.setattr(os, "kill", spy)
            patch.setattr(os, "killpg", spy)
            assert conftest.remove_if_still_ours(own.pid, own_start + "0") is None  # start time
            assert conftest.remove_if_still_ours(grandchild, grandchild_start) is None  # not ours
            assert conftest.remove_if_still_ours(done.pid, "0") is None  # reaped elsewhere
            patch.setattr(conftest, "direct_children", lambda: {
                (own.pid, own_start + "0"): ("sleep", "S"), (done.pid, "0"): ("true", "S"),
            })
            assert conftest.remove_new_children(set()) == []  # skipped, so not reported either
        assert not calls
        assert conftest.read_stat(own.pid)[1] != "Z" and conftest.read_stat(grandchild)[1] != "Z"
    finally:
        os.kill(grandchild, 9)  # a process this test started
        parent.stdout.close()
        _end(parent)
        _end(own)


def test_pidfd_send_signal_is_the_only_signalling_call(monkeypatch):
    child = subprocess.Popen(["sleep", "30"])
    sent = []
    real = conftest.signal.pidfd_send_signal

    def record(fd, signum, *rest):
        sent.append(signum)
        return real(fd, signum, *rest)

    def forbidden(*_args):
        raise AssertionError("signal by pid is forbidden: the pid may have been recycled")

    try:
        start = conftest.read_stat(child.pid)[3]
        with monkeypatch.context() as patch:
            patch.setattr(conftest.signal, "pidfd_send_signal", record)
            patch.setattr(os, "kill", forbidden)
            patch.setattr(os, "killpg", forbidden)
            assert conftest.remove_if_still_ours(child.pid, start) == -9
        assert sent == [conftest.signal.SIGKILL]
        assert conftest.read_stat(child.pid) is None, "killed and reaped"
    finally:
        child.wait(timeout=30)  # already reaped by the guard function: returns at once


def test_direct_children_lists_zombies_and_live_children_by_pid_and_state():
    live = subprocess.Popen(["sleep", "30"])
    dying = subprocess.Popen(["sleep", "30"])
    try:
        dying.kill()
        deadline = time.monotonic() + 30
        states = {}
        while time.monotonic() < deadline and states.get(dying.pid, ("", ""))[1] != "Z":
            states = {key[0]: value for key, value in conftest.direct_children().items()}
            time.sleep(0.01)
        assert states[dying.pid] == ("sleep", "Z")
        assert states[live.pid][0] == "sleep" and states[live.pid][1] != "Z"
    finally:
        live.kill()
        live.wait(timeout=30)
        dying.wait(timeout=30)


def test_direct_children_skips_vanished_pids_and_parses_odd_command_names(monkeypatch):
    me = os.getpid()
    odd = "7777777 (a b) (c) S " + f"{me} " + " ".join(["0"] * 17) + " 424242 0"
    real_read_text = Path.read_text

    def read_text(self, *args, **kwargs):
        if self == Path("/proc/8888888/stat"):
            raise FileNotFoundError(self)
        if self == Path("/proc/7777777/stat"):
            return odd
        return real_read_text(self, *args, **kwargs)

    real_listdir = os.listdir
    monkeypatch.setattr(Path, "read_text", read_text)
    monkeypatch.setattr(
        os, "listdir", lambda path: [*real_listdir(path), "8888888", "7777777", "not-a-pid"],
    )
    found = conftest.direct_children()
    assert found[(7777777, "424242")] == ("a b) (c", "S")
    assert not [key for key in found if key[0] == 8888888]
