import os
import subprocess
import time
from pathlib import Path

import pytest

from estate_test_policy import children, plugin


def fake_process(root, pid, ppid, *, state="S", start=100):
    path = Path(root) / str(pid)
    path.mkdir(exist_ok=True)
    fields = f"{pid} (worker) {state} {ppid} " + "0 " * 17 + f"{start}\n"
    (path / "stat").write_text(fields)


def wait_for_state(pid, state):
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        record = children.read_process(pid)
        if record is not None and record.state == state:
            return record
        time.sleep(0.001)
    pytest.fail(f"pid {pid} did not reach state {state} before the failsafe deadline")


def test_subreaper_is_enabled_and_readable():
    assert children.enable_subreaper()
    assert children.subreaper_enabled()


def test_non_linux_is_a_noop(monkeypatch):
    monkeypatch.setattr(children, "is_linux", lambda: False)
    assert children.enable_subreaper() is False
    assert children.subreaper_enabled() is False


def test_missing_prctl_symbol_refuses(monkeypatch):
    monkeypatch.setattr(children.ctypes, "CDLL", lambda *args, **kwargs: object())
    with pytest.raises(children.ProcessGuardError, match="does not expose prctl"):
        children._prctl(children.PR_SET_CHILD_SUBREAPER, 1)


def test_prctl_failure_refuses():
    with pytest.raises(children.ProcessGuardError, match="prctl\\(999999\\) failed"):
        children._prctl(999999)


def test_process_parser_and_vanished_entry(tmp_path):
    fake_process(tmp_path, 10, 3, start=900)
    record = children.read_process(10, tmp_path)
    assert record == children.ProcessRecord(10, 3, "S", "worker", 900)
    assert children.read_process(11, tmp_path) is None


@pytest.mark.parametrize(
    "text,fragment",
    [
        ("not a stat record\n", "malformed process stat file"),
        ("10 (worker) S 3\n", "malformed process stat file"),
        ("10 (worker) S nope " + "0 " * 17 + "900\n", "malformed process identity"),
        ("11 (worker) S 3 " + "0 " * 17 + "900\n", "malformed process identity"),
    ],
)
def test_malformed_process_stat_refuses(tmp_path, text, fragment):
    (tmp_path / "10").mkdir()
    (tmp_path / "10" / "stat").write_text(text)
    with pytest.raises(children.ProcessGuardError, match=fragment):
        children.read_process(10, tmp_path)


def test_unreadable_process_and_dir_refuse(tmp_path):
    (tmp_path / "10").mkdir()
    (tmp_path / "10" / "stat").mkdir()
    with pytest.raises(children.ProcessGuardError, match="cannot read"):
        children.read_process(10, tmp_path)
    with pytest.raises(children.ProcessGuardError, match="cannot enumerate"):
        children.direct_children(proc_root=tmp_path / "missing")


def test_direct_children_include_zombies_and_identity_not_pid_alone(tmp_path):
    fake_process(tmp_path, 10, 3, start=100)
    fake_process(tmp_path, 11, 3, state="Z", start=200)
    fake_process(tmp_path, 12, 99, start=300)
    before = children.direct_children(3, tmp_path)
    assert set(before) == {(10, 100), (11, 200)}

    fake_process(tmp_path, 10, 3, start=101)
    fake_process(tmp_path, 13, 3, state="Z", start=400)
    after = children.new_children(before, root_pid=3, proc_root=tmp_path)
    assert set(after) == {(10, 101), (13, 400)}


def test_settle_waits_for_a_transient_child_to_disappear(tmp_path):
    before = {}
    fake_process(tmp_path, 10, 3)
    now = [0.0]

    def clock():
        return now[0]

    def sleeper(seconds):
        now[0] += seconds
        (tmp_path / "10" / "stat").unlink()

    assert children.wait_for_new_children(
        before,
        settle_seconds=0.05,
        root_pid=3,
        proc_root=tmp_path,
        clock=clock,
        sleeper=sleeper,
    ) == {}


def test_settle_returns_a_remaining_zombie_without_waiting(tmp_path):
    before = {}
    fake_process(tmp_path, 10, 3, state="Z")
    called = []

    def sleeper(seconds):
        called.append(seconds)

    current = children.wait_for_new_children(
        before,
        settle_seconds=5,
        root_pid=3,
        proc_root=tmp_path,
        clock=lambda: 0,
        sleeper=sleeper,
    )
    assert list(current.values())[0].state == "Z"
    assert called == []


def test_settle_returns_a_persistent_live_child_at_the_deadline(tmp_path):
    before = {}
    fake_process(tmp_path, 10, 3)
    now = [0.0]

    def clock():
        return now[0]

    def sleeper(seconds):
        now[0] += seconds

    current = children.wait_for_new_children(
        before,
        settle_seconds=0.11,
        root_pid=3,
        proc_root=tmp_path,
        clock=clock,
        sleeper=sleeper,
    )
    assert current[(10, 100)].pid == 10
    assert now[0] >= 0.11


def test_child_guard_does_not_report_a_child_that_vanished_before_removal(tmp_path):
    guard = children.ChildGuard(root_pid=3, proc_root=tmp_path, settle_seconds=0)
    fake_process(tmp_path, 999_999_999, 3)
    assert guard.check() == []


def test_missing_pidfd_is_treated_as_gone():
    record = children.ProcessRecord(999_999_999, 0, "S", "gone", 1)
    assert children.remove_if_still_ours(record) is None


def test_live_child_is_pidfd_killed_reaped_and_reported():
    guard = children.ChildGuard(settle_seconds=0)
    proc = subprocess.Popen(["/bin/sleep", "300"])
    leaked = guard.check()
    assert any(f"pid {proc.pid} comm sleep (alive; SIGKILL sent and reaped)" == item for item in leaked)
    assert children.read_process(proc.pid) is None


def test_zombie_child_is_reported_as_zombie_and_reaped():
    guard = children.ChildGuard(settle_seconds=0)
    proc = subprocess.Popen(["/bin/true"])
    wait_for_state(proc.pid, "Z")
    leaked = guard.check()
    assert any(f"pid {proc.pid} comm true (zombie; reaped)" == item for item in leaked)
    assert children.read_process(proc.pid) is None


def test_identity_recheck_does_not_touch_a_replaced_child():
    proc = subprocess.Popen(["/bin/true"])
    wait_for_state(proc.pid, "Z")
    record = children.read_process(proc.pid)
    assert record is not None
    wrong = children.ProcessRecord(record.pid, record.ppid, record.state, record.command, record.start_time + 1)
    assert children.remove_if_still_ours(wrong) is None
    assert proc.wait(timeout=120) == 0


def test_plugin_session_is_off_by_default_and_opt_in_enables_subreaper(pytester):
    pytester.makeini("[pytest]\n")
    pytester.makepyfile(
        test_sample="""
        from estate_test_policy import children
        def test_state():
            assert not children.subreaper_enabled()
        """
    )
    pytester.runpytest_subprocess("-p", "estate_test_policy.plugin", "-p", "no:cacheprovider").assert_outcomes(
        passed=1
    )

    pytester.makeini("[pytest]\nestate_policy = fail\n")
    pytester.makepyfile(
        test_sample="""
        from estate_test_policy import children
        def test_state():
            assert children.subreaper_enabled()
        """
    )
    pytester.runpytest_subprocess("-p", "estate_test_policy.plugin", "-p", "no:cacheprovider").assert_outcomes(
        passed=1
    )


def test_every_xdist_worker_enables_its_own_subreaper(pytester):
    pytester.makeini("[pytest]\nestate_policy = fail\nestate_policy_settle_seconds = 0\n")
    pytester.makepyfile(
        test_sample="""
        from estate_test_policy import children
        def test_state_one():
            assert children.subreaper_enabled()
        def test_state_two():
            assert children.subreaper_enabled()
        """
    )
    pytester.runpytest_subprocess(
        "-p",
        "estate_test_policy.plugin",
        "-p",
        "no:cacheprovider",
        "-n",
        "2",
    ).assert_outcomes(passed=2)


def test_non_linux_session_records_one_note(pytester, monkeypatch):
    pytester.makeini("[pytest]\nestate_policy = fail\nestate_policy_settle_seconds = 0\n")
    pytester.makepyfile("def test_ok():\n    pass\n")
    monkeypatch.setattr(children, "is_linux", lambda: False)
    with pytest.warns(plugin.EstatePolicyWarning, match="leftover-children guard is inactive on non-Linux") as caught:
        result = pytester.runpytest_inprocess(
            "-p", "estate_test_policy.plugin", "-p", "no:cacheprovider"
        )
    result.assert_outcomes(passed=1)
    assert len(caught) == 1
