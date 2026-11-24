import errno
import shutil

import pytest

from estate_test_policy import containment


def reasons(result):
    return [failure.reason for failure in result.refusals]


def check(roots, **kw):
    return containment.check(
        proc_root=roots.proc,
        cgroup_root=roots.cgroup,
        settle_seconds=0,
        **kw,
    )


def test_capped_private_tree_passes(fake_estate):
    fake_estate.private()
    result = check(fake_estate)
    assert result.contained and result.message == ""


def test_leaf_max_uses_a_finite_parent_limit(fake_estate):
    fake_estate.private(memory="max", pids="max")
    fake_estate.private()
    fake_estate.limit("memory.max", "1073741824")
    fake_estate.limit("pids.max", "700")
    leaf = fake_estate.cgroup / "other"
    leaf.mkdir()
    fake_estate.limit("memory.max", "max", directory=leaf)
    fake_estate.limit("pids.max", "max", directory=leaf)
    (fake_estate.proc / "self" / "cgroup").write_text("0::/other\n")
    (leaf / "cgroup.controllers").write_text("memory pids\n")
    assert check(fake_estate).contained


def test_finite_zero_is_a_finite_limit(fake_estate):
    fake_estate.private(memory="0", pids="0")
    assert check(fake_estate).contained


def test_uncapped_limits_are_both_reported(fake_estate):
    fake_estate.private(memory="max", pids="max")
    result = check(fake_estate)
    assert reasons(result) == ["memory.max", "pids.max"]
    assert "*" not in result.message
    assert "memory.max: no finite effective limit" in result.message
    assert "pids.max: no finite effective limit" in result.message


def test_malformed_or_absent_limits_refuse(fake_estate):
    fake_estate.private()
    fake_estate.limit("memory.max", "not-a-limit")
    (fake_estate.cgroup / "pids.max").unlink()
    result = check(fake_estate)
    assert reasons(result) == ["memory.max", "pids.max"]
    assert "expected a non-negative integer or `max`" in result.message


def test_unreadable_and_negative_limits_refuse(fake_estate):
    fake_estate.private()
    (fake_estate.cgroup / "memory.max").unlink()
    (fake_estate.cgroup / "memory.max").mkdir()
    fake_estate.limit("pids.max", "-1")
    result = check(fake_estate)
    assert reasons(result) == ["memory.max", "pids.max"]
    assert "cannot evaluate the effective limit" in result.message
    assert "expected a non-negative integer or `max`" in result.message


@pytest.mark.parametrize("line", ["1:name=systemd:/\n", "0::/\n1:name=systemd:/\n", "0::relative\n"])
def test_v1_hybrid_and_relative_cgroup_paths_refuse(fake_estate, line):
    fake_estate.private()
    (fake_estate.proc / "self" / "cgroup").write_text(line)
    result = check(fake_estate)
    assert "cgroup-v2" in reasons(result)


def test_dotdot_cgroup_path_refuses(fake_estate):
    fake_estate.private()
    (fake_estate.proc / "self" / "cgroup").write_text("0::/../../escape\n")
    result = check(fake_estate)
    assert "cgroup-v2" in reasons(result)
    assert "`..` component" in result.message


def test_missing_cgroup_leaf_and_controllers_refuse(fake_estate):
    fake_estate.private(cgroup_path="/parent/leaf")
    shutil.rmtree(fake_estate.cgroup / "parent")
    assert "cgroup-v2" in reasons(check(fake_estate))

    fake_estate.private()
    (fake_estate.cgroup / "cgroup.controllers").unlink()
    assert "cgroup-v2" in reasons(check(fake_estate))

    (fake_estate.cgroup / "cgroup.controllers").mkdir()
    assert "cgroup-v2" in reasons(check(fake_estate))


def test_missing_root_controllers_refuses_even_with_leaf_controllers(fake_estate):
    fake_estate.private(cgroup_path="/parent/leaf")
    (fake_estate.cgroup / "cgroup.controllers").unlink()
    result = check(fake_estate)
    assert "cgroup-v2" in reasons(result)
    assert str(fake_estate.cgroup / "cgroup.controllers") in result.message


def test_unreadable_cgroup_and_process_files_refuse(fake_estate):
    fake_estate.private()
    (fake_estate.proc / "self" / "cgroup").unlink()
    assert "cgroup-v2" in reasons(check(fake_estate))

    fake_estate.private()
    (fake_estate.proc / "self" / "stat").unlink()
    assert "pid-namespace-entry" in reasons(check(fake_estate))


@pytest.mark.parametrize(
    "options",
    ["hidepid=invisible", "hidepid=noaccess", "hidepid=ptraceable", "hidepid=1", "hidepid=2", "subset=pid"],
)
def test_hidden_or_subset_proc_mount_refuses(fake_estate, options):
    fake_estate.private(mount_options=options)
    result = check(fake_estate)
    assert "proc-hidepid" in reasons(result)


@pytest.mark.parametrize("options", ["hidepid=0", "hidepid=off"])
def test_zero_and_off_hidepid_pass(fake_estate, options):
    fake_estate.private(mount_options=options)
    assert check(fake_estate).contained


def test_absent_or_malformed_proc_mount_refuses(fake_estate):
    fake_estate.private()
    (fake_estate.proc / "self" / "mountinfo").unlink()
    (fake_estate.proc / "self" / "mountinfo").mkdir()
    assert "proc-hidepid" in reasons(check(fake_estate))

    shutil.rmtree(fake_estate.proc / "self" / "mountinfo")
    fake_estate.private()
    (fake_estate.proc / "self" / "mountinfo").write_text("")
    assert "proc-hidepid" in reasons(check(fake_estate))

    fake_estate.private()
    (fake_estate.proc / "self" / "mountinfo").write_text(
        "\n20 1 8:1 / / ext4 rw - ext4 /dev/x rw\n"
        "1 2 - proc proc rw\n"
        "36 29 0:32 / /proc rw - proc proc rw\n"
    )
    assert "proc-hidepid" in reasons(check(fake_estate))


def test_nonpid_subset_option_is_not_a_refusal(fake_estate):
    fake_estate.private(super_options="subset=cgroup")
    assert check(fake_estate).contained


def test_exec_entry_chain_refuses(fake_estate):
    fake_estate.private()
    fake_estate.process(2, 0, command="exec-session")
    result = check(fake_estate)
    assert "pid-namespace-entry" in reasons(result)
    assert "ppid-0 exec session" in result.message


def test_missing_ancestor_and_cycle_refuse(fake_estate):
    fake_estate.private()
    (fake_estate.proc / "2" / "stat").unlink()
    assert "pid-namespace-entry" in reasons(check(fake_estate))

    fake_estate.private()
    fake_estate.process(2, 3, command="top")
    assert "process-tree ancestor cycle" in check(fake_estate).message


def test_shared_namespace_groups_entries_and_pid1_children(fake_estate):
    fake_estate.private()
    fake_estate.process(5, 1, command="tmux: server")
    fake_estate.process(6, 0, command="sh")
    fake_estate.process(7, 99, command="helper")
    result = check(fake_estate)
    assert "pid-namespace-shared" in reasons(result)
    assert "ppid 0 entry (exec session): pid 6 sh" in result.message
    assert "PID 1 child: pid 5 tmux: server" in result.message
    assert "child of pid 99: pid 7 helper" in result.message


def test_all_pid1_foreign_processes_are_labelled_orphans(fake_estate):
    fake_estate.private()
    fake_estate.process(5, 1, command="sleep")
    result = check(fake_estate)
    assert "pid-namespace-shared" in reasons(result)
    assert "orphan, likely leaked by an earlier test: pid 5 sleep" in result.message


def test_malformed_and_vanished_process_entries(fake_estate):
    fake_estate.private()
    (fake_estate.proc / "99").mkdir()
    (fake_estate.proc / "99" / "stat").write_text("not a stat record\n")
    result = check(fake_estate)
    assert "pid-namespace-shared" in reasons(result)
    assert "process table could not be read completely" in result.message

    (fake_estate.proc / "99" / "stat").unlink()
    assert check(fake_estate).contained


@pytest.mark.parametrize(
    "text,fragment",
    [
        ("99 (bad) S\n", "malformed process stat file"),
        ("99 (bad) S nope " + "0 " * 17 + "900\n", "malformed process identity"),
        ("98 (bad) S 3 " + "0 " * 17 + "900\n", "malformed process identity"),
    ],
)
def test_malformed_numeric_process_record_refuses(fake_estate, text, fragment):
    fake_estate.private()
    (fake_estate.proc / "99").mkdir()
    (fake_estate.proc / "99" / "stat").write_text(text)
    result = check(fake_estate)
    assert "pid-namespace-shared" in reasons(result)
    assert fragment in result.message


def test_unreadable_self_stat_and_process_directory_refuse(fake_estate):
    fake_estate.private()
    (fake_estate.proc / "self" / "stat").unlink()
    (fake_estate.proc / "self" / "stat").mkdir()
    assert "cannot read" in check(fake_estate).message

    records, errors = containment._process_tree(fake_estate.proc / "missing")
    assert records == {} and "cannot enumerate" in errors[0]


def test_containment_reader_treats_esrch_as_vanished(fake_estate):
    fake_estate.private()

    def gone(path):
        raise ProcessLookupError(errno.ESRCH, "process vanished")

    assert containment._read_record(fake_estate.proc / "self" / "stat", reader=gone) is None


def test_transient_foreign_process_disappears_during_settle(fake_estate):
    fake_estate.private()
    fake_estate.process(5, 1, command="sampler")
    now = [0.0]

    def clock():
        return now[0]

    def sleeper(seconds):
        now[0] += seconds
        (fake_estate.proc / "5" / "stat").unlink()

    result = containment.check(
        proc_root=fake_estate.proc,
        cgroup_root=fake_estate.cgroup,
        settle_seconds=0.05,
        clock=clock,
        sleeper=sleeper,
    )
    assert result.contained


def test_persistent_foreign_process_refuses_after_settle(fake_estate):
    fake_estate.private()
    fake_estate.process(5, 1, command="sampler")
    now = [0.0]

    def clock():
        return now[0]

    def sleeper(seconds):
        now[0] += seconds

    result = containment.check(
        proc_root=fake_estate.proc,
        cgroup_root=fake_estate.cgroup,
        settle_seconds=0.11,
        clock=clock,
        sleeper=sleeper,
    )
    assert "pid-namespace-shared" in reasons(result)
    assert now[0] >= 0.11
