import errno
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


def test_process_parser_treats_esrch_as_vanished(tmp_path):
    fake_process(tmp_path, 10, 3, start=900)

    def gone(path):
        raise ProcessLookupError(errno.ESRCH, "process vanished")

    assert children.read_process(10, tmp_path, reader=gone) is None


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


def test_eacces_for_unrelated_hidden_process_is_skipped(tmp_path, monkeypatch):
    fake_process(tmp_path, 10, 3)

    def denied(path):
        raise PermissionError(errno.EACCES, "hidden by procfs", str(path))

    def not_our_child(idtype, pid, options):
        raise ChildProcessError(errno.ECHILD, "not a child")

    monkeypatch.setattr(children.os, "waitid", not_our_child)
    assert children.direct_children(3, tmp_path, reader=denied) == {}


def test_eacces_for_our_credential_changed_child_fails_loudly(tmp_path, monkeypatch):
    fake_process(tmp_path, 10, 3)

    def denied(path):
        raise PermissionError(errno.EACCES, "hidden by procfs", str(path))

    def is_our_child(idtype, pid, options):
        return object()

    monkeypatch.setattr(children.os, "waitid", is_our_child)
    with pytest.raises(children.ProcessGuardError, match="our child"):
        children.direct_children(3, tmp_path, reader=denied)


def test_eacces_with_failed_ownership_check_refuses(tmp_path, monkeypatch):
    fake_process(tmp_path, 10, 3)

    def denied(path):
        raise PermissionError(errno.EACCES, "hidden by procfs", str(path))

    def failed_probe(idtype, pid, options):
        raise OSError(errno.EIO, "waitid failed")

    monkeypatch.setattr(children.os, "waitid", failed_probe)
    with pytest.raises(children.ProcessGuardError, match="cannot verify whether process 10 is our child"):
        children.direct_children(3, tmp_path, reader=denied)


def test_settle_skips_an_already_reported_identity(tmp_path):
    fake_process(tmp_path, 10, 3, start=100)
    ignored = {(10, 100)}

    current = children.wait_for_new_children(
        {},
        settle_seconds=10,
        root_pid=3,
        proc_root=tmp_path,
        ignore=ignored,
        clock=lambda: 0,
        sleeper=lambda seconds: pytest.fail("an ignored child must not hold up a later cleanup pass"),
    )

    assert current == {}


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
    now = [0.0]

    def clock():
        return now[0]

    def sleeper(seconds):
        now[0] += seconds
        (tmp_path / "999999999" / "stat").unlink()

    guard = children.ChildGuard(
        root_pid=3,
        proc_root=tmp_path,
        settle_seconds=0.01,
        clock=clock,
        sleeper=sleeper,
    )
    fake_process(tmp_path, 999_999_999, 3)
    assert guard.check() == []


def test_child_guard_reports_the_cleanup_pass_bound(tmp_path):
    guard = children.ChildGuard(root_pid=3, proc_root=tmp_path, settle_seconds=0)
    fake_process(tmp_path, 999_999_999, 3)
    leaked = guard.check()
    assert len(leaked) == 1
    assert "reached the 10-pass bound" in leaked[0]
    assert "remaining new children: pid 999999999" in leaked[0]


def test_child_guard_reports_unreaped_identity_once_and_tracks_reused_pid(tmp_path, monkeypatch):
    guard = children.ChildGuard(root_pid=3, proc_root=tmp_path, settle_seconds=0)
    stuck = children.ProcessRecord(10, 3, "D", "blocked", 100)
    reused = children.ProcessRecord(10, 3, "S", "replacement", 101)
    observations = iter(
        (
            {stuck.identity: stuck},
            {stuck.identity: stuck, reused.identity: reused},
            {},
        )
    )
    seen_ignored = []
    removed = []

    def observe(*args, ignore=(), **kwargs):
        ignored = set(ignore)
        seen_ignored.append(ignored)
        current = next(observations)
        return {identity: record for identity, record in current.items() if identity not in ignored}

    def remove(record, **kwargs):
        removed.append(record.identity)
        if record.identity == stuck.identity:
            return "pid 10 comm blocked (unreaped after SIGKILL)"
        return "pid 10 comm replacement (alive; SIGKILL sent and reaped)"

    monkeypatch.setattr(children, "wait_for_new_children", observe)
    monkeypatch.setattr(children, "remove_if_still_ours", remove)

    leaked = guard.check()

    assert leaked == [
        "pid 10 comm blocked (unreaped after SIGKILL)",
        "pid 10 comm replacement (alive; SIGKILL sent and reaped)",
    ]
    assert removed == [stuck.identity, reused.identity]
    assert stuck.identity in seen_ignored[1]


def test_child_guard_omits_pass_bound_when_no_children_remain(tmp_path, monkeypatch):
    guard = children.ChildGuard(root_pid=3, proc_root=tmp_path, settle_seconds=0)
    records = [children.ProcessRecord(pid, 3, "S", f"worker-{pid}", pid * 100) for pid in range(10, 20)]
    observations = iter({record.identity: record} for record in records)
    monkeypatch.setattr(children, "wait_for_new_children", lambda *args, **kwargs: next(observations))
    monkeypatch.setattr(
        children,
        "remove_if_still_ours",
        lambda record, **kwargs: f"pid {record.pid} comm {record.command} (alive; SIGKILL sent and reaped)",
    )
    monkeypatch.setattr(children, "new_children", lambda *args, **kwargs: {})

    leaked = guard.check()

    assert len(leaked) == 10
    assert all("pass bound" not in item for item in leaked)


def test_missing_pidfd_is_treated_as_gone():
    record = children.ProcessRecord(999_999_999, 0, "S", "gone", 1)
    assert children.remove_if_still_ours(record) is None


def test_live_child_is_pidfd_killed_reaped_and_reported():
    guard = children.ChildGuard(settle_seconds=0)
    proc = subprocess.Popen(["/bin/sleep", "300"])
    leaked = guard.check()
    assert any(f"pid {proc.pid} comm sleep (alive; SIGKILL sent and reaped)" == item for item in leaked)
    assert children.read_process(proc.pid) is None


def test_bounded_reap_returns_unreaped_without_hanging():
    proc = subprocess.Popen(["/bin/sleep", "300"])
    record = children.read_process(proc.pid)
    assert record is not None
    now = [0.0]
    calls = [0]

    def clock():
        return now[0]

    def sleeper(seconds):
        now[0] += seconds

    def never_ready(*args):
        calls[0] += 1
        if calls[0] > 10:
            raise AssertionError("bounded reap kept polling past its deadline")
        return None

    outcome = children.remove_if_still_ours(
        record,
        reap_timeout=0.02,
        clock=clock,
        sleeper=sleeper,
        waiter=never_ready,
    )
    assert outcome is not None and "unreaped after SIGKILL" in outcome
    assert 1 <= calls[0] <= 3
    assert proc.wait(timeout=120) == -9


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


def test_allowlisted_zombie_reaper_reaps_only_new_zombies():
    old_zombie = subprocess.Popen(["/bin/true"])
    wait_for_state(old_zombie.pid, "Z")
    before = children.direct_children()

    new_zombie = subprocess.Popen(["/bin/true"])
    wait_for_state(new_zombie.pid, "Z")
    live = subprocess.Popen(["/bin/sleep", "300"])
    try:
        assert children.reap_new_zombies(before) == 1
        assert children.read_process(new_zombie.pid) is None
        assert children.read_process(old_zombie.pid) is not None
        assert children.read_process(live.pid) is not None
    finally:
        live_record = children.read_process(live.pid)
        if live_record is not None:
            children.remove_if_still_ours(live_record)
        assert old_zombie.wait(timeout=120) == 0


def test_allowlisted_zombie_reaper_ignores_a_pidfd_that_vanished(tmp_path):
    fake_process(tmp_path, 10, 3, state="Z")

    def gone(pid):
        raise ProcessLookupError(errno.ESRCH, "process vanished")

    pidfd_ops = children.PidfdOps(open=gone, close=lambda pidfd: None)
    assert children.reap_new_zombies({}, root_pid=3, proc_root=tmp_path, pidfd_ops=pidfd_ops) == 0


def test_allowlisted_zombie_reaper_rechecks_process_identity(tmp_path):
    fake_process(tmp_path, 10, 3, state="Z", start=100)
    original = (tmp_path / "10" / "stat").read_text()
    fake_process(tmp_path, 10, 3, state="Z", start=101)
    changed = (tmp_path / "10" / "stat").read_text()
    responses = iter((original, changed))

    closed = []
    pidfd_ops = children.PidfdOps(open=lambda pid: 73, close=closed.append)
    assert (
        children.reap_new_zombies(
            {},
            root_pid=3,
            proc_root=tmp_path,
            reader=lambda path: next(responses),
            pidfd_ops=pidfd_ops,
        )
        == 0
    )
    assert closed == [73]


def test_allowlisted_zombie_reaper_tolerates_not_yet_reaped(tmp_path):
    fake_process(tmp_path, 10, 3, state="Z")

    closed = []
    pidfd_ops = children.PidfdOps(open=lambda pid: 73, close=closed.append)
    assert (
        children.reap_new_zombies(
            {},
            root_pid=3,
            proc_root=tmp_path,
            waiter=lambda *args: None,
            pidfd_ops=pidfd_ops,
        )
        == 0
    )
    assert closed == [73]


def source_on_subprocess_path(monkeypatch):
    # pytest.ini `pythonpath = src` reaches only this process; a subprocess run needs it in PYTHONPATH.
    src = str(Path(children.__file__).resolve().parents[1])
    existing = os.environ.get("PYTHONPATH")
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join([src, existing]) if existing else src)


def test_plugin_session_is_off_by_default_and_opt_in_enables_subreaper(pytester, monkeypatch):
    source_on_subprocess_path(monkeypatch)
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


def test_every_xdist_worker_enables_its_own_subreaper(pytester, monkeypatch):
    source_on_subprocess_path(monkeypatch)
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
