"""Whole-source R1 closure for ``assay.cli``, ``assay.safeio`` and ``assay.verdict``.

Every test pins one refusal / branch with a specific message, exit code or
returned value.  Nothing here touches docker, the network or a cgroup.
"""

from __future__ import annotations

import dataclasses
import io
import os
import signal
import stat
from pathlib import Path

import pytest
from conftest import R0_LANE, make_lane, native_outcome

from assay import cli, safeio
from assay.errors import AssayError, LaneConfigError, ReasonCode


# --------------------------------------------------------------- safeio / verdict


def test_proc_fd_root_that_is_not_openable_is_refused(tmp_path):
    plain = tmp_path / "file"
    plain.write_text("x")
    descriptor = os.open(plain, os.O_RDONLY)
    try:
        with pytest.raises(AssayError, match="is not an openable directory") as info:
            safeio._open_root(Path(f"/proc/self/fd/{descriptor}"))
    finally:
        os.close(descriptor)
    assert info.value.reason_code is ReasonCode.UNREADABLE_ARTIFACT


def test_proc_fd_root_that_fstat_reports_as_non_directory_is_refused(tmp_path, monkeypatch):
    descriptor = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    real_fstat = os.fstat
    monkeypatch.setattr(
        safeio.os, "fstat", lambda fd: real_fstat(os.open(__file__, os.O_RDONLY))
    )
    try:
        with pytest.raises(AssayError, match="is not a directory"):
            safeio._open_root(Path(f"/proc/self/fd/{descriptor}"))
    finally:
        os.close(descriptor)


@pytest.mark.parametrize("suffix", ["a//b", "a/./b", "a/../b"])
def test_proc_fd_root_with_unsafe_suffix_is_refused(tmp_path, suffix):
    descriptor = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        with pytest.raises(AssayError, match="has an unsafe suffix"):
            safeio._open_root(f"/proc/self/fd/{descriptor}/{suffix}")
    finally:
        os.close(descriptor)


def _valid_native_outcome():
    return native_outcome(
        path="a.py",
        lineno=1,
        start_byte=0,
        end_byte=1,
        replacement_sha256="b" * 64,
        operator="python:compare-swap",
        description="changed comparison",
    )


def test_mutant_outcome_refuses_untyped_process_tree_evidence():
    with pytest.raises(ValueError, match="process_tree_evidence has the wrong type"):
        dataclasses.replace(_valid_native_outcome(), process_tree_evidence=object())


def test_ledger_outcome_cannot_carry_process_tree_evidence():
    from assay.verdict import MutationExecution

    outcome = _valid_native_outcome()
    with pytest.raises(ValueError, match="ledger execution cannot carry process-tree evidence"):
        dataclasses.replace(
            outcome,
            execution=MutationExecution(mode="ledger", anchor="audit"),
            resource_limit_evidence=None,
        )


def test_native_executed_outcome_requires_process_tree_evidence():
    with pytest.raises(ValueError, match="requires process_tree_evidence"):
        dataclasses.replace(_valid_native_outcome(), process_tree_evidence=None)


def test_ingested_outcome_cannot_carry_process_tree_evidence():
    with pytest.raises(ValueError, match="ingested MutantOutcome cannot carry process_tree_evidence"):
        dataclasses.replace(
            _valid_native_outcome(),
            candidate_id=None,
            source_sha256=None,
            mutated_file_sha256=None,
            execution=None,
            resource_limit_evidence=None,
        )


# ------------------------------------------------------- termination signal handler


def _handler_stubs(monkeypatch, *, first_request=True, request_error=None,
                   terminate_error=None, write_error=None):
    calls: list[tuple] = []

    def request_termination():
        calls.append(("request",))
        if request_error is not None:
            raise request_error
        return first_request

    def terminate():
        calls.append(("terminate",))
        if terminate_error is not None:
            raise terminate_error

    def write(fd, data):
        calls.append(("write", fd, data))
        if write_error is not None:
            raise write_error
        return len(data)

    monkeypatch.setattr(cli.runner, "request_termination", request_termination)
    monkeypatch.setattr(cli.liveness, "terminate_live_process_groups", terminate)
    monkeypatch.setattr(cli.os, "write", write)
    return calls


def test_signal_handler_kills_children_and_announces_on_first_request(monkeypatch):
    calls = _handler_stubs(monkeypatch)
    cli._termination_signal_handler(signal.SIGTERM, None)
    assert calls[:2] == [("request",), ("terminate",)]
    assert calls[2][:2] == ("write", 2)
    assert b"termination requested (signal 15)" in calls[2][2]


def test_signal_handler_is_silent_on_a_repeat_request(monkeypatch):
    calls = _handler_stubs(monkeypatch, first_request=False)
    cli._termination_signal_handler(signal.SIGINT, None)
    assert calls == [("request",)]


def test_signal_handler_treats_a_failing_request_as_first_and_swallows_errors(monkeypatch):
    calls = _handler_stubs(
        monkeypatch,
        request_error=RuntimeError("boom"),
        terminate_error=RuntimeError("boom"),
        write_error=OSError("closed"),
    )
    cli._termination_signal_handler(signal.SIGHUP, None)
    assert [call[0] for call in calls] == ["request", "terminate", "write"]


# ------------------------------------------- signal installation and main() guards


def test_install_handlers_off_the_main_thread_installs_nothing(monkeypatch):
    seen: list = []
    monkeypatch.setattr(cli.signal, "signal", lambda *a: seen.append(a))
    monkeypatch.setattr(cli.threading, "current_thread", lambda: object())
    restore = cli._install_termination_handlers()
    restore()
    assert seen == []


def test_install_handlers_rolls_back_partial_installation_and_restore_is_idempotent(monkeypatch):
    previous = {
        signal.SIGTERM: "term-old",
        signal.SIGINT: "int-old",
        signal.SIGHUP: "hup-old",
    }
    calls: list = []

    def fake_signal(signum, handler):
        calls.append((signum, handler))
        if signum == signal.SIGHUP and handler is cli._termination_signal_handler:
            raise ValueError("cannot install")

    monkeypatch.setattr(cli.signal, "getsignal", previous.__getitem__)
    monkeypatch.setattr(cli.signal, "signal", fake_signal)
    with pytest.raises(ValueError, match="cannot install"):
        cli._install_termination_handlers()
    assert calls[-2:] == [(signal.SIGINT, "int-old"), (signal.SIGTERM, "term-old")]

    calls.clear()
    monkeypatch.setattr(cli.signal, "signal", lambda s, h: calls.append((s, h)))
    restore = cli._install_termination_handlers()
    calls.clear()
    restore()
    assert sorted(calls) == sorted(previous.items())
    restore()
    assert len(calls) == 3


def test_main_refuses_when_sigchld_state_cannot_be_read(monkeypatch):
    def fail():
        raise cli.process_tree.ProcessTreeError("no procfs")

    monkeypatch.setattr(cli.process_tree, "_sigchld_is_ignored", fail)
    err = io.StringIO()
    assert cli.main(["lanes"], stdout=io.StringIO(), stderr=err) == 2
    assert "cannot verify child exit-status handling: no procfs" in err.getvalue()


def test_main_refuses_ignored_sigchld_from_a_non_main_thread(monkeypatch):
    monkeypatch.setattr(cli.process_tree, "_sigchld_is_ignored", lambda: True)
    monkeypatch.setattr(cli.threading, "current_thread", lambda: object())
    err = io.StringIO()
    assert cli.main(["lanes"], stdout=io.StringIO(), stderr=err) == 2
    assert "SIGCHLD ignored from a non-main thread" in err.getvalue()


def test_main_restores_default_sigchld_when_it_is_ignored_on_the_main_thread(monkeypatch):
    installed: list = []
    monkeypatch.setattr(cli.process_tree, "_sigchld_is_ignored", lambda: True)
    monkeypatch.setattr(cli.signal, "signal", lambda s, h: installed.append((s, h)))
    monkeypatch.setattr(cli, "_run_analyze", lambda argv, out, err: 17)
    assert cli.main(["analyze"], stdout=io.StringIO(), stderr=io.StringIO()) == 17
    assert installed == [(signal.SIGCHLD, signal.SIG_DFL)]


def test_main_skips_the_sigchld_probe_off_linux(monkeypatch):
    def boom():
        raise AssertionError("probe must not run")

    monkeypatch.setattr(cli.process_tree, "_sigchld_is_ignored", boom)
    monkeypatch.setattr(cli.sys, "platform", "darwin")
    monkeypatch.setattr(cli, "_run_analyze", lambda argv, out, err: 5)
    assert cli.main(["analyze"], stdout=io.StringIO(), stderr=io.StringIO()) == 5


# ------------------------------------------------------------- pilot argument parsing


def test_candidates_file_read_refusal_is_wrapped(monkeypatch, tmp_path):
    def refuse(*args, **kwargs):
        raise AssayError("unreadable", outcome=cli.Outcome.ERROR,
                         reason_code=ReasonCode.UNREADABLE_ARTIFACT)

    monkeypatch.setattr(cli.safeio, "read_bounded_input", refuse)
    with pytest.raises(LaneConfigError, match="cannot read --candidates-file .*: unreadable"):
        cli._parse_candidates_file(tmp_path / "ids", max_mutants=3)


@pytest.mark.parametrize("raw", ["1", "0001"])
def test_pilot_jobs_accepts_only_one(raw):
    assert cli._parse_pilot_jobs(raw, candidates_file=True) == 1


# --------------------------------------------------------------- PILOT-STATE reading


def _root_fd(path: Path) -> int:
    return os.open(path, os.O_RDONLY | os.O_DIRECTORY)


@pytest.fixture
def state_root(tmp_path):
    root = tmp_path / "state"
    root.mkdir()
    descriptor = _root_fd(root)
    yield root, descriptor
    os.close(descriptor)


def test_pilot_state_symlink_is_refused_not_treated_as_absent(state_root):
    root, descriptor = state_root
    (root / "target").write_text("{}")
    (root / "PILOT-STATE").symlink_to(root / "target")
    with pytest.raises(cli.PilotStateError, match="PILOT-STATE cannot be read safely"):
        cli._read_pilot_state_document(root, descriptor, require_sentinel_for_records=True)


def test_pilot_state_with_two_links_is_refused(state_root):
    root, descriptor = state_root
    (root / "other").write_text("{}")
    os.link(root / "other", root / "PILOT-STATE")
    with pytest.raises(cli.PilotStateError, match="not a single-link regular file"):
        cli._read_pilot_state_document(root, descriptor, require_sentinel_for_records=True)


def test_pilot_state_larger_than_the_limit_is_refused(state_root):
    root, descriptor = state_root
    (root / "PILOT-STATE").write_bytes(b"x" * (cli._PILOT_STATE_LIMIT + 1))
    with pytest.raises(cli.PilotStateError, match="exceeds the 4096-byte limit"):
        cli._read_pilot_state_document(root, descriptor, require_sentinel_for_records=True)


def test_pilot_state_that_grows_after_fstat_is_refused_by_the_read_loop(state_root, monkeypatch):
    root, descriptor = state_root
    (root / "PILOT-STATE").write_bytes(b"x" * (cli._PILOT_STATE_LIMIT + 1))
    real_fstat = os.fstat

    def small(fd):
        info = real_fstat(fd)
        return type("S", (), {"st_mode": info.st_mode, "st_nlink": 1, "st_size": 1})()

    monkeypatch.setattr(cli.os, "fstat", small)
    with pytest.raises(cli.PilotStateError, match="exceeds the 4096-byte limit"):
        cli._read_pilot_state_document(root, descriptor, require_sentinel_for_records=True)


def test_pilot_state_read_error_is_wrapped(state_root, monkeypatch):
    root, descriptor = state_root
    (root / "PILOT-STATE").write_text("{}")

    def broken(fd, size):
        raise OSError("io error")

    monkeypatch.setattr(cli.os, "read", broken)
    with pytest.raises(cli.PilotStateError, match="PILOT-STATE cannot be read safely: io error"):
        cli._read_pilot_state_document(root, descriptor, require_sentinel_for_records=True)


# ------------------------------------------------------------------- state locking


def _fstat_overrides(monkeypatch, overrides: dict[int, dict]):
    """Make the Nth ``os.fstat`` call report altered fields."""
    real = os.fstat
    count = {"n": 0}

    def fake(fd):
        count["n"] += 1
        info = real(fd)
        fields = {
            name: getattr(info, name)
            for name in ("st_mode", "st_uid", "st_nlink", "st_dev", "st_ino", "st_size")
        }
        fields.update(overrides.get(count["n"], {}))
        return type("S", (), fields)()

    monkeypatch.setattr(cli.os, "fstat", fake)


def test_path_lock_refuses_a_lock_directory_that_is_not_private(tmp_path, monkeypatch):
    _fstat_overrides(monkeypatch, {1: {"st_mode": stat.S_IFDIR | 0o755}})
    with pytest.raises(cli.PilotStateError, match="is not a private directory owned by this user"):
        cli._acquire_state_path_lock(tmp_path / "s")


def test_path_lock_refuses_a_lock_file_that_is_not_private(tmp_path, monkeypatch):
    _fstat_overrides(monkeypatch, {2: {"st_mode": stat.S_IFREG | 0o644}})
    with pytest.raises(cli.PilotStateError, match="is not a private regular file owned by this user"):
        cli._acquire_state_path_lock(tmp_path / "s")


def test_path_lock_reports_contention_with_another_run(tmp_path):
    first = cli._acquire_state_path_lock(tmp_path / "s")
    try:
        with pytest.raises(cli.PilotStateError, match="already in use by another assay run"):
            cli._acquire_state_path_lock(tmp_path / "s")
    finally:
        os.close(first)


def test_path_lock_contention_before_the_lock_file_exists_still_refuses(tmp_path, monkeypatch):
    real_open = os.open

    def contended(path, flags, *args, **kwargs):
        if str(path).endswith(".lock"):
            raise BlockingIOError("busy")
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(cli.os, "open", contended)
    with pytest.raises(cli.PilotStateError, match="already in use by another assay run"):
        cli._acquire_state_path_lock(tmp_path / "s")


def test_path_lock_wraps_a_flock_failure_after_the_file_is_open(tmp_path, monkeypatch):
    def broken(fd, op):
        raise OSError("flock broke")

    monkeypatch.setattr(cli.fcntl, "flock", broken)
    with pytest.raises(cli.PilotStateError, match="cannot lock requested mutation state path .*flock broke"):
        cli._acquire_state_path_lock(tmp_path / "s")


def test_path_lock_wraps_an_open_failure_before_any_descriptor_exists(tmp_path, monkeypatch):
    real_open = os.open
    temp_root = Path("/tmp").resolve()

    def failing(path, flags, *args, **kwargs):
        if Path(path) == temp_root:
            raise PermissionError("no tmp")
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(cli.os, "open", failing)
    with pytest.raises(cli.PilotStateError, match="cannot lock requested mutation state path .*no tmp"):
        cli._acquire_state_path_lock(tmp_path / "s")


def test_directory_lock_over_a_pinned_proc_fd_parent_reuses_an_existing_child(tmp_path):
    (tmp_path / "state").mkdir()
    parent = _root_fd(tmp_path)
    try:
        root_fd, lock_fds, root = cli._acquire_state_directory_lock(
            Path(f"/proc/self/fd/{parent}/state")
        )
        try:
            assert str(root) == f"/proc/self/fd/{parent}/state"
            assert os.path.samestat(os.fstat(root_fd), os.stat(tmp_path / "state"))
        finally:
            cli._release_state_directory_lock(root_fd, lock_fds)
    finally:
        os.close(parent)


def test_directory_lock_refuses_a_filesystem_root():
    with pytest.raises(cli.PilotStateError, match="must not be a filesystem root"):
        cli._acquire_state_directory_lock(Path("/"))


def test_directory_lock_wraps_an_open_failure(tmp_path):
    occupied = tmp_path / "file"
    occupied.write_text("x")
    with pytest.raises(cli.PilotStateError, match="cannot open mutation state directory"):
        cli._acquire_state_directory_lock(occupied)


@pytest.mark.parametrize("close_error", [cli.PilotStateError("late"), OSError("late")])
def test_directory_lock_closes_a_fresh_descriptor_when_the_proc_fd_parent_close_fails(
    tmp_path, monkeypatch, close_error
):
    (tmp_path / "state").mkdir()
    parent = _root_fd(tmp_path)
    real_close = os.close
    real_open_root = safeio._open_root
    state = {"armed": True, "opened": None}

    def open_root(path):
        state["opened"] = real_open_root(path)
        return state["opened"]

    def close(fd):
        real_close(fd)
        if fd == state["opened"] and state["armed"]:
            state["armed"] = False
            raise close_error

    monkeypatch.setattr(cli.safeio, "_open_root", open_root)

    monkeypatch.setattr(cli.os, "close", close)
    expected = "late" if isinstance(close_error, cli.PilotStateError) else "cannot open mutation state directory"
    try:
        with pytest.raises(cli.PilotStateError, match=expected):
            cli._acquire_state_directory_lock(Path(f"/proc/self/fd/{parent}/state"))
    finally:
        state["armed"] = False


def _flock_fails_on_second_call(monkeypatch, error):
    real_flock = cli.fcntl.flock
    count = {"n": 0}

    def flock(fd, op):
        count["n"] += 1
        if count["n"] == 2:
            raise error
        return real_flock(fd, op)

    monkeypatch.setattr(cli.fcntl, "flock", flock)


@pytest.mark.parametrize(
    ("error", "message"),
    [
        (BlockingIOError("busy"), "is already in use by another assay run"),
        (cli.PilotStateError("lock policy"), "lock policy"),
        (OSError("flock broke"), "cannot lock mutation state directory .*flock broke"),
    ],
)
def test_directory_lock_flock_failures_are_pilot_state_refusals(tmp_path, monkeypatch, error, message):
    _flock_fails_on_second_call(monkeypatch, error)
    with pytest.raises(cli.PilotStateError, match=message):
        cli._acquire_state_directory_lock(tmp_path / "state")


def test_directory_lock_releases_descriptors_on_a_non_oserror_interrupt(tmp_path, monkeypatch):
    _flock_fails_on_second_call(monkeypatch, KeyboardInterrupt())
    with pytest.raises(KeyboardInterrupt):
        cli._acquire_state_directory_lock(tmp_path / "state")
    # the path lock was released with the interrupt: it can be taken again
    root_fd, lock_fds, _ = cli._acquire_state_directory_lock(tmp_path / "state")
    cli._release_state_directory_lock(root_fd, lock_fds)


# ------------------------------------------------- preflight / sentinel creation


def _write_sentinel(root: Path, *, lane="package", selection="a" * 64):
    import json

    (root / "PILOT-STATE").write_text(
        json.dumps({"schema": "assay-pilot-state/1", "selection_sha256": selection, "lane": lane})
    )


def test_preflight_without_a_held_lock_acquires_its_own_and_accepts_a_matching_sentinel(tmp_path):
    root = tmp_path / "state"
    root.mkdir()
    _write_sentinel(root)
    cli._preflight_state_directory(root, lane="package", pilot=True)
    # the lock was released on the way out
    fd, locks, _ = cli._acquire_state_directory_lock(root)
    cli._release_state_directory_lock(fd, locks)


def test_preflight_refuses_a_sentinel_belonging_to_another_lane(tmp_path):
    root = tmp_path / "state"
    root.mkdir()
    _write_sentinel(root, lane="other")
    with pytest.raises(cli.PilotStateError, match="belongs to lane 'other', not 'package'"):
        cli._preflight_state_directory(root, lane="package", pilot=True)


def test_preflight_wraps_an_os_error_while_inspecting(tmp_path, monkeypatch):
    root = tmp_path / "state"
    root.mkdir()

    def broken(*args, **kwargs):
        raise OSError("inspect failed")

    monkeypatch.setattr(cli, "_read_pilot_state_document", broken)
    with pytest.raises(cli.PilotStateError, match="cannot inspect pilot state directory .*inspect failed"):
        cli._preflight_state_directory(root, lane="package", pilot=True)


def test_ensure_pilot_state_creates_the_missing_directory_when_the_caller_holds_the_lock(tmp_path):
    root = tmp_path / "fresh" / "state"
    cli._ensure_pilot_state(
        root, lane="package", selection_sha256="b" * 64, lock_held=True, locked_root_fd=None
    )
    assert cli._read_pilot_state_document(
        root, _root_fd(root), require_sentinel_for_records=True
    ) == {"schema": "assay-pilot-state/1", "selection_sha256": "b" * 64, "lane": "package"}


def test_ensure_pilot_state_wraps_an_unopenable_state_directory(tmp_path):
    occupied = tmp_path / "file"
    occupied.write_text("x")
    with pytest.raises(cli.PilotStateError, match="cannot open pilot state directory"):
        cli._ensure_pilot_state(
            occupied, lane="package", selection_sha256="b" * 64, lock_held=True
        )


def test_ensure_pilot_state_refuses_a_sentinel_that_appears_during_creation(tmp_path, monkeypatch):
    root = tmp_path / "state"
    root.mkdir()
    real_fsync = os.fsync

    def racing_fsync(fd):
        real_fsync(fd)
        (root / "PILOT-STATE").write_text("raced")

    monkeypatch.setattr(cli.os, "fsync", racing_fsync)
    with pytest.raises(cli.PilotStateError, match="appeared while the pilot state was being created"):
        cli._ensure_pilot_state(root, lane="package", selection_sha256="b" * 64)
    assert [p.name for p in root.iterdir()] == ["PILOT-STATE"]  # temp file removed


@pytest.mark.parametrize("temp_vanished", [False, True])
def test_ensure_pilot_state_wraps_a_failed_publish_and_cleans_its_temp_file(
    tmp_path, monkeypatch, temp_vanished
):
    root = tmp_path / "state"
    root.mkdir()

    def broken_replace(source, destination, *, src_dir_fd, dst_dir_fd):
        if temp_vanished:
            os.unlink(source, dir_fd=src_dir_fd)
        raise OSError("replace failed")

    monkeypatch.setattr(cli.os, "replace", broken_replace)
    with pytest.raises(cli.PilotStateError, match="cannot create PILOT-STATE safely: replace failed"):
        cli._ensure_pilot_state(root, lane="package", selection_sha256="b" * 64)
    assert list(root.iterdir()) == []


# ----------------------------------------------------------- pilot judge capture


def test_budget_record_lookup_without_a_valid_judge_digest_is_false(tmp_path):
    assert cli._pilot_has_budget_record(
        tmp_path, object(), judge_sha256=None, campaign_deadline_sha256=None, cold_witness=False
    ) is False
    assert cli._pilot_has_budget_record(
        tmp_path, object(), judge_sha256="nothex", campaign_deadline_sha256=None, cold_witness=False
    ) is False


def test_capture_pilot_judge_identity_tracks_agreement_and_conflict():
    captured: list[str | None] = [None]
    forwarded: list[dict] = []
    observe = cli._capture_pilot_judge_identity(forwarded.append, captured)
    first, second = "a" * 64, "b" * 64
    observe({"event": "candidates", "judge_sha256": first})
    assert captured == [first]
    observe({"event": "candidates", "judge_sha256": first})
    assert captured == [first]
    observe({"event": "other", "judge_sha256": second})
    assert captured == [first]
    observe({"event": "candidates", "judge_sha256": second})
    assert captured == [""]
    assert len(forwarded) == 4


def test_capture_pilot_judge_identity_rejects_a_malformed_digest_without_a_sink():
    captured: list[str | None] = [None]
    cli._capture_pilot_judge_identity(None, captured)({"event": "candidates", "judge_sha256": "zz"})
    assert captured == [""]


def test_preflight_treats_a_missing_unlocked_state_directory_as_nothing_to_check(tmp_path):
    assert cli._preflight_state_directory(
        tmp_path / "absent", lane="package", pilot=True, lock_held=True
    ) is None


def test_preflight_wraps_an_unopenable_state_directory(tmp_path):
    occupied = tmp_path / "file"
    occupied.write_text("x")
    with pytest.raises(cli.PilotStateError, match="cannot open pilot state directory"):
        cli._preflight_state_directory(occupied, lane="package", pilot=True, lock_held=True)


# ----------------------------------------------------------- campaign deadline files


def test_campaign_deadline_read_refusal_is_wrapped(tmp_path, monkeypatch):
    def refuse(*args, **kwargs):
        raise AssayError("unreadable", outcome=cli.Outcome.ERROR,
                         reason_code=ReasonCode.UNREADABLE_ARTIFACT)

    monkeypatch.setattr(cli.safeio, "read_bounded_input", refuse)
    with pytest.raises(LaneConfigError, match="cannot trust campaign deadline .*: unreadable"):
        cli._parse_campaign_deadline(tmp_path / "d.json", lane="package")


def test_missing_campaign_deadline_is_refused(tmp_path):
    with pytest.raises(LaneConfigError, match="campaign deadline does not exist"):
        cli._parse_campaign_deadline(tmp_path / "d.json", lane="package")


def test_campaign_git_identity_refuses_a_dirty_worktree(tmp_path, monkeypatch):
    lane = make_lane()
    lane_file = _lane_file(tmp_path, {"package": lane})
    monkeypatch.setattr(cli.git, "head_rev", lambda root, remaining: "c" * 40)
    monkeypatch.setattr(cli.git, "run", lambda root, *args, remaining: "t" * 40 + "\n")
    monkeypatch.setattr(cli.git, "repo_top", lambda root, remaining: root)

    def dirty(**kwargs):
        raise AssayError("dirty", outcome=cli.Outcome.NO_MEASUREMENT,
                         reason_code=ReasonCode.DIRTY_TREE)

    monkeypatch.setattr(cli.runner, "_resolve_snapshot_worktree_integrity", dirty)
    with pytest.raises(LaneConfigError, match="clean worktree under the lane's declared dirty-ignore policy: dirty"):
        cli._campaign_git_identity(lane_file, lane)


def test_campaign_state_directory_must_be_a_directory(tmp_path):
    occupied = tmp_path / "state"
    occupied.write_text("x")
    with pytest.raises(LaneConfigError, match="campaign state directory is not a directory"):
        cli._campaign_state_records_match(occupied, "d" * 64)


def test_campaign_state_directory_listing_failure_is_wrapped(tmp_path, monkeypatch):
    def broken(self):
        raise PermissionError("denied")

    monkeypatch.setattr(Path, "iterdir", broken)
    with pytest.raises(LaneConfigError, match="cannot inspect campaign state directory .*denied"):
        cli._campaign_state_records_match(tmp_path, "d" * 64)


def test_campaign_state_scan_skips_foreign_names_and_vanished_records(tmp_path, monkeypatch):
    (tmp_path / "notes.txt").write_text("not a record")
    vanished = tmp_path / ("e" * 64 + ".json")
    vanished.write_text("{}")
    monkeypatch.setattr(cli.safeio, "read_bounded_input", lambda *a, **k: None)
    assert cli._campaign_state_records_match(tmp_path, "d" * 64) is None


def test_campaign_state_record_read_refusal_is_wrapped(tmp_path, monkeypatch):
    (tmp_path / ("e" * 64 + ".json")).write_text("{}")

    def refuse(*args, **kwargs):
        raise AssayError("unreadable", outcome=cli.Outcome.ERROR,
                         reason_code=ReasonCode.UNREADABLE_ARTIFACT)

    monkeypatch.setattr(cli.safeio, "read_bounded_input", refuse)
    with pytest.raises(LaneConfigError, match="cannot trust state record .*: unreadable"):
        cli._campaign_state_records_match(tmp_path, "d" * 64)


# ---------------------------------------------------------------- campaign init


def _lane_file(root: Path, lanes: dict):
    from assay.config import load_lane_file

    root.mkdir(parents=True, exist_ok=True)
    toml = root / "assay.toml"
    toml.write_text(R0_LANE)
    return dataclasses.replace(load_lane_file(toml), lanes=lanes)


def _campaign_args(**overrides):
    import argparse

    values = dict(
        campaign="camp",
        hours=1.0,
        expires_at=None,
        wheel_sha256=None,
        lane=["package"],
        file=None,
        out=None,
        state_dir=[],
    )
    values.update(overrides)
    return argparse.Namespace(**values)


@pytest.fixture
def campaign(tmp_path, monkeypatch):
    """A campaign-init world with the git/discovery seams stubbed."""
    from types import SimpleNamespace

    world = SimpleNamespace(identities=[("c" * 40, "t" * 40)], discovered=None,
                            adapter=object(), lane_file=None)

    def identity(lane_file, lane):
        return world.identities[0] if len(world.identities) == 1 else world.identities.pop(0)

    monkeypatch.setattr(cli, "_campaign_git_identity", identity)
    monkeypatch.setattr(cli, "_resolve_declared_adapters", lambda lane: world.adapter)
    monkeypatch.setattr(cli.runner, "resolve_base_declaration", lambda lane, base: None)
    monkeypatch.setattr(cli, "_discover_plan_jobs", lambda *a, **k: world.discovered)

    def install(*lanes):
        world.lane_file = _lane_file(tmp_path / "project", {lane.name: lane for lane in lanes})
        monkeypatch.setattr(cli, "_resolve_lane_file", lambda path: world.lane_file)
        return world.lane_file

    world.install = install
    world.root = tmp_path / "project"
    return world


def _r2_lane(*, max_mutants=5):
    from assay.config import MutationConfig
    from conftest import make_r2_judge

    config = MutationConfig(jobs=1, max_mutants=max_mutants, operators=("python:compare-swap",))
    return make_lane(
        rigor=("R0", "R2"),
        judge=make_r2_judge(source_root_paths=(Path("src"),), mutation=config),
    )


def _init(args, tmp_path):
    out, err = io.StringIO(), io.StringIO()
    code = cli._cmd_campaign_init(args, out, err)
    return code, out.getvalue(), err.getvalue()


def test_campaign_init_refuses_r2_without_a_mutation_configuration(campaign, tmp_path):
    campaign.install(make_lane(rigor=("R0", "R2"), judge=None))
    with pytest.raises(LaneConfigError, match="declares R2 without a mutation configuration"):
        _init(_campaign_args(), tmp_path)


def test_campaign_init_refuses_a_lane_with_no_mutation_adapter(campaign, tmp_path):
    campaign.install(_r2_lane())
    campaign.adapter = None
    with pytest.raises(LaneConfigError, match="resolves no mutation adapter"):
        _init(_campaign_args(), tmp_path)


@pytest.mark.parametrize(
    ("discovered", "message"),
    [
        (dict(commit="other", tree="t" * 40, jobs=()), "commit or tree changed while campaign plans"),
        (dict(commit="c" * 40, tree="t" * 40, jobs="UNSUPPORTED"), "has no supported full mutation plan to bind"),
        (dict(commit="c" * 40, tree="t" * 40, jobs=(1, 2)), "exceeds max_mutants"),
    ],
)
def test_campaign_init_refuses_unbindable_plans(campaign, tmp_path, discovered, message):
    from types import SimpleNamespace

    campaign.install(_r2_lane(max_mutants=1))
    if discovered["jobs"] == "UNSUPPORTED":
        discovered["jobs"] = cli.mutation.UNSUPPORTED
    campaign.discovered = SimpleNamespace(**discovered)
    with pytest.raises(LaneConfigError, match=message):
        _init(_campaign_args(), tmp_path)


def test_campaign_init_refuses_a_worktree_that_changed_after_planning(campaign, tmp_path):
    campaign.install(make_lane())
    campaign.identities = [("c" * 40, "t" * 40), ("c" * 40, "moved" * 8)]
    with pytest.raises(LaneConfigError, match="the worktree changed while campaign plans were discovered"):
        _init(_campaign_args(), tmp_path)


def test_campaign_init_writes_to_the_default_location_and_rounds_expiry_up(campaign, tmp_path, monkeypatch):
    from datetime import datetime, timezone
    import json

    campaign.install(make_lane())
    now = datetime(2026, 10, 7, 12, 0, 0, tzinfo=timezone.utc)

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return now

    monkeypatch.setattr(cli, "datetime", FixedDatetime)
    (campaign.root / ".assay").mkdir()
    code, out, _ = _init(_campaign_args(), tmp_path)
    target = campaign.root / ".assay" / "campaign-deadline-camp.json"
    assert code == 0
    assert out.strip() == str(target)
    document = json.loads(target.read_text())
    # a whole-second "now" needs no round-up: exactly one hour later
    assert document["expires_at_utc"] == "2026-10-07T13:00:00Z"


def test_campaign_init_refuses_an_unreadable_existing_deadline(campaign, tmp_path, monkeypatch):
    campaign.install(make_lane())

    def refuse(*args, **kwargs):
        raise AssayError("unreadable", outcome=cli.Outcome.ERROR,
                         reason_code=ReasonCode.UNREADABLE_ARTIFACT)

    monkeypatch.setattr(cli.safeio, "read_bounded_input", refuse)
    with pytest.raises(LaneConfigError, match="cannot trust existing campaign deadline .*: unreadable"):
        _init(_campaign_args(out=tmp_path / "deadline.json"), tmp_path)


def test_campaign_init_refuses_a_state_dir_the_tree_would_show(campaign, tmp_path, monkeypatch):
    campaign.install(make_lane())
    probes: list = []
    monkeypatch.setattr(cli, "_containments", lambda target, root: [(root, root / "state")])
    monkeypatch.setattr(
        cli, "_refuse_a_visible_store_inside_the_tree",
        lambda raw, **kwargs: probes.append((raw, kwargs)),
    )
    _init(_campaign_args(out=tmp_path / "deadline.json", state_dir=[str(tmp_path / "state")]), tmp_path)
    assert len(probes) == 1
    raw, kwargs = probes[0]
    assert kwargs["flag"] == "--state-dir"
    assert kwargs["probe"] == campaign.root / "state" / ("0" * 64 + ".json")


@pytest.mark.parametrize("vanished", [False, True])
def test_campaign_init_cleans_its_temp_file_when_publication_fails(campaign, tmp_path, monkeypatch, vanished):
    campaign.install(make_lane())

    def broken(source, destination):
        if vanished:
            os.unlink(source)
        raise OSError("replace failed")

    monkeypatch.setattr(cli.os, "replace", broken)
    target = tmp_path / "out" / "deadline.json"
    target.parent.mkdir()
    with pytest.raises(OSError, match="replace failed"):
        _init(_campaign_args(out=target), tmp_path)
    assert list(target.parent.iterdir()) == []


# ------------------------------------------------- progress-vs-state collisions


def _state_and_outside(tmp_path):
    state = tmp_path / "state"
    outside = tmp_path / "outside"
    state.mkdir()
    outside.mkdir()
    return state, outside


def test_progress_destination_that_cannot_be_statted_is_refused(tmp_path):
    state, outside = _state_and_outside(tmp_path)
    (outside / "blocker").write_text("x")
    state_fd = _root_fd(state)
    try:
        with pytest.raises(LaneConfigError, match="cannot be checked against mutation state"):
            cli._refuse_progress_state_collision(
                str(outside / "blocker" / "progress.jsonl"), state, state_fd
            )
    finally:
        os.close(state_fd)


def test_progress_that_is_a_hard_link_to_a_state_file_is_refused_and_listing_races_are_skipped(
    tmp_path, monkeypatch
):
    state, outside = _state_and_outside(tmp_path)
    (state / "record.json").write_text("{}")
    (state / "other.json").write_text("{}")
    progress = outside / "progress.jsonl"
    os.link(state / "record.json", progress)
    real_listdir = os.listdir
    monkeypatch.setattr(
        cli.os, "listdir", lambda fd: ["ghost.json", *sorted(real_listdir(fd))]
    )
    state_fd = _root_fd(state)
    try:
        with pytest.raises(LaneConfigError, match="another link to mutation-state file 'record.json'"):
            cli._refuse_progress_state_collision(str(progress), state, state_fd)
    finally:
        os.close(state_fd)


def test_progress_parent_inside_the_state_directory_is_refused(tmp_path):
    state, _ = _state_and_outside(tmp_path)
    (state / "sub").mkdir()
    state_fd, parent_fd = _root_fd(state), _root_fd(state / "sub")
    try:
        with pytest.raises(LaneConfigError, match="resolves inside the mutation state directory"):
            cli._refuse_progress_parent_in_state("p", state, parent_fd, state_fd)
    finally:
        os.close(state_fd)
        os.close(parent_fd)


def test_progress_parent_outside_the_state_directory_is_accepted(tmp_path):
    state, outside = _state_and_outside(tmp_path)
    state_fd, parent_fd = _root_fd(state), _root_fd(outside)
    try:
        assert cli._refuse_progress_parent_in_state("p", state, parent_fd, state_fd) is None
    finally:
        os.close(state_fd)
        os.close(parent_fd)


def test_progress_parent_walk_that_never_terminates_is_refused(tmp_path, monkeypatch):
    from types import SimpleNamespace

    state, outside = _state_and_outside(tmp_path)
    state_fd, parent_fd = _root_fd(state), _root_fd(outside)
    inode = iter(range(1, 10_000))
    monkeypatch.setattr(
        cli.os, "fstat", lambda fd: SimpleNamespace(st_dev=0, st_ino=next(inode))
    )
    try:
        with pytest.raises(LaneConfigError, match="cannot prove that --progress 'p' is outside"):
            cli._refuse_progress_parent_in_state("p", state, parent_fd, state_fd)
    finally:
        monkeypatch.undo()
        os.close(state_fd)
        os.close(parent_fd)


def test_progress_parent_walk_failure_is_wrapped(tmp_path, monkeypatch):
    state, outside = _state_and_outside(tmp_path)
    state_fd, parent_fd = _root_fd(state), _root_fd(outside)

    def broken(path, flags, **kwargs):
        raise PermissionError("no parent")

    monkeypatch.setattr(cli.os, "open", broken)
    try:
        with pytest.raises(LaneConfigError, match="cannot check the opened --progress parent for 'p': no parent"):
            cli._refuse_progress_parent_in_state("p", state, parent_fd, state_fd)
    finally:
        monkeypatch.undo()
        os.close(state_fd)
        os.close(parent_fd)


def test_progress_fd_collision_refuses_a_non_regular_progress_file(tmp_path):
    state, outside = _state_and_outside(tmp_path)
    state_fd, parent_fd = _root_fd(state), _root_fd(outside)
    try:
        with pytest.raises(LaneConfigError, match="did not open a regular file"):
            cli._refuse_progress_state_fd_collision("p", state, parent_fd, parent_fd, state_fd)
    finally:
        os.close(state_fd)
        os.close(parent_fd)


def test_progress_fd_collision_detects_a_state_file_link_and_skips_listing_races(tmp_path, monkeypatch):
    state, outside = _state_and_outside(tmp_path)
    (state / "record.json").write_text("{}")
    progress = outside / "progress.jsonl"
    os.link(state / "record.json", progress)
    real_listdir = os.listdir
    monkeypatch.setattr(cli.os, "listdir", lambda fd: ["ghost.json", *sorted(real_listdir(fd))])
    state_fd, parent_fd = _root_fd(state), _root_fd(outside)
    progress_fd = os.open(progress, os.O_RDONLY)
    try:
        with pytest.raises(LaneConfigError, match="another link to mutation-state file 'record.json'"):
            cli._refuse_progress_state_fd_collision("p", state, parent_fd, progress_fd, state_fd)
    finally:
        os.close(state_fd)
        os.close(parent_fd)
        os.close(progress_fd)


def test_progress_fd_collision_accepts_an_unrelated_regular_file(tmp_path):
    state, outside = _state_and_outside(tmp_path)
    (state / "record.json").write_text("{}")
    (outside / "progress.jsonl").write_text("")
    state_fd, parent_fd = _root_fd(state), _root_fd(outside)
    progress_fd = os.open(outside / "progress.jsonl", os.O_RDONLY)
    try:
        assert cli._refuse_progress_state_fd_collision("p", state, parent_fd, progress_fd, state_fd) is None
    finally:
        os.close(state_fd)
        os.close(parent_fd)
        os.close(progress_fd)


# ------------------------------------------------------- run-time campaign checks


def _deadline_document(**overrides):
    document = {
        "schema": "assay-campaign-deadline/1",
        "campaign": "camp",
        "commit": "a" * 40,
        "git_tree": "b" * 40,
        "lanes": ["package"],
        "assay_version": cli.__version__,
        "wheel_sha256": None,
        "plan_sha256": {"package": None},
        "created_at_utc": "2026-10-07T12:00:00Z",
        "expires_at_utc": "2999-01-01T00:00:00Z",
    }
    document.update(overrides)
    return document


def _run_main(argv):
    out, err = io.StringIO(), io.StringIO()
    code = cli.main(argv, stdout=out, stderr=err)
    return code, out.getvalue(), err.getvalue()


def test_run_refuses_a_campaign_whose_plan_binding_disagrees_with_the_lane_rigor(tmp_path):
    import json

    config = tmp_path / "assay.toml"
    config.write_text(R0_LANE)
    deadline = tmp_path / "deadline.json"
    deadline.write_text(json.dumps(_deadline_document(plan_sha256={"package": "f" * 64})))
    code, _, err = _run_main(
        ["run", "package", "--file", str(config), "--campaign-deadline", str(deadline)]
    )
    assert code == cli.Outcome.ERROR.exit_code
    assert "does not match whether the lane declares R2" in err


@pytest.fixture
def campaign_run(git_repo, tmp_path):
    """A real committed R0 lane plus a deadline file whose identity can be edited."""
    import json

    marker = tmp_path / "lane-ran"
    config = git_repo.write(
        "assay.toml",
        R0_LANE.replace(
            'argv = ["pytest", "tests/unit", "-q"]', f'argv = ["/bin/sh", "-c", "touch {marker}"]'
        ),
    )
    git_repo.commit_all("add lane")
    deadline = tmp_path / "deadline.json"

    def run(**overrides):
        deadline.write_text(json.dumps(_deadline_document(**overrides)))
        verdict = tmp_path / "verdict.json"
        code, out, err = _run_main(
            ["run", "package", "--file", str(config), "--campaign-deadline", str(deadline),
             "--verdict-json", str(verdict)]
        )
        document = json.loads(verdict.read_text()) if verdict.exists() else None
        return code, err, document

    run.marker = marker
    return run


def test_run_reports_commit_and_tree_mismatches_before_launching_the_lane(campaign_run):
    code, err, verdict = campaign_run()
    assert code == cli.Outcome.ERROR.exit_code
    assert verdict["reason_code"] == ReasonCode.BAD_LANE_CONFIG.value
    assert f"commit expected {'a' * 40}, observed " in err
    assert f"git_tree expected {'b' * 40}, observed " in err
    assert not campaign_run.marker.exists()


def _git_run_failing_on_tree(monkeypatch, error):
    real = cli.git.run

    def run(root, *args, **kwargs):
        if args and args[0] == "rev-parse" and str(args[1]).endswith("^{tree}"):
            raise error
        return real(root, *args, **kwargs)

    monkeypatch.setattr(cli.git, "run", run)


def test_run_turns_a_tree_lookup_timeout_into_a_timeout_verdict(campaign_run, monkeypatch):
    _git_run_failing_on_tree(
        monkeypatch,
        AssayError("tree lookup timed out", outcome=cli.Outcome.BUDGET_EXCEEDED,
                   reason_code=ReasonCode.LANE_TIMEOUT),
    )
    code, err, verdict = campaign_run()
    assert code == cli.Outcome.BUDGET_EXCEEDED.exit_code
    assert verdict["reason_code"] == ReasonCode.LANE_TIMEOUT.value
    assert "tree lookup timed out" in err
    assert not campaign_run.marker.exists()


def test_run_does_not_launder_a_non_timeout_tree_lookup_failure(campaign_run, monkeypatch):
    _git_run_failing_on_tree(
        monkeypatch,
        AssayError("tree lookup broke", outcome=cli.Outcome.ERROR, reason_code=ReasonCode.GIT_FAILED),
    )
    code, err, verdict = campaign_run()
    assert code == cli.Outcome.ERROR.exit_code
    assert verdict is None
    assert "tree lookup broke" in err
    assert not campaign_run.marker.exists()


# ------------------------------------------------------------ shards, rows, rendering


def test_parse_mutation_shard_defaults_and_refusals():
    assert cli._parse_mutation_shard(None) == (0, 1)
    assert cli._parse_mutation_shard("1/3") == (1, 3)
    with pytest.raises(LaneConfigError, match="--shard must have the form INDEX/COUNT"):
        cli._parse_mutation_shard("one-of-three")
    with pytest.raises(LaneConfigError, match=r"--shard '3/3': "):
        cli._parse_mutation_shard("3/3")


def test_select_plan_jobs_checks_the_deadline_around_every_candidate(monkeypatch):
    checks: list[int] = []
    monkeypatch.setattr(cli.mutation, "candidate_id", lambda job: f"{job:064x}")
    jobs = [0, 1, 2, 3]
    everything = cli._select_plan_jobs(jobs, None, deadline_check=lambda: checks.append(1) or 0.0)
    assert everything == (0, 1, 2, 3)
    assert len(checks) == 2

    checks.clear()
    selected = cli._select_plan_jobs(jobs, "0/2", deadline_check=lambda: checks.append(1) or 0.0)
    assert set(selected) < set(jobs) and selected
    assert len(checks) >= 2 * len(jobs) + 1

    assert cli._select_plan_jobs(jobs, None) == (0, 1, 2, 3)
    assert set(cli._select_plan_jobs(jobs, "0/2")) == set(selected)


def test_plan_rows_check_the_deadline_around_every_row(monkeypatch):
    from types import SimpleNamespace

    checks: list[int] = []
    site = SimpleNamespace(operator="op", start_byte=1, end_byte=2, lineno=3, description="d")
    job = SimpleNamespace(path="a.py", site=site)
    monkeypatch.setattr(
        cli.mutation, "candidate_identity_fields",
        lambda item: {"source_sha256": "s", "mutated_file_sha256": "m"},
    )
    monkeypatch.setattr(cli, "_plan_candidate_id", lambda item: "id")
    rows = cli._plan_rows_from_jobs([job, job], deadline_check=lambda: checks.append(1) or 0.0)
    assert [row["id"] for row in rows] == ["id", "id"]
    assert rows[0]["source_sha256"] == "s" and rows[0]["mutated_file_sha256"] == "m"
    assert len(checks) == 5


def test_discover_plan_jobs_refuses_when_head_moved_during_pilot_preparation(tmp_path, monkeypatch):
    lane = make_lane()
    lane_file = _lane_file(tmp_path, {"package": lane})
    monkeypatch.setattr(cli.git, "head_rev", lambda root, remaining: "n" * 40)
    with pytest.raises(AssayError, match="HEAD changed while Assay prepared the pilot candidate selection") as info:
        cli._discover_plan_jobs(
            lane_file, lane, adapter=object(), base_declaration=None, operators=(),
            allow_dirty=False, resolve_reuse_command=False, expected_commit="o" * 40,
        )
    assert info.value.outcome is cli.Outcome.NO_MEASUREMENT


def _plan_payload(**overrides):
    payload = {
        "status": "ok", "lane": "package", "commit": "c", "tree": "t", "candidate_count": 2,
        "max_mutants": 9, "jobs": 1, "shard": None, "budget_per_candidate": "30s",
        "estimated_serial_seconds": 1.0, "estimated_wall_seconds": 1.0,
        "by_operator": {}, "by_file": {}, "candidates": [],
    }
    payload.update(overrides)
    return payload


def _render(payload):
    out = io.StringIO()
    cli._render_plan_text(payload, out)
    return out.getvalue()


def test_plan_text_for_a_refused_plan_states_the_reason_and_stops():
    text = _render({"status": "refused", "lane": "package", "reason": "no adapter"})
    assert text.splitlines() == ["Mutation plan for package", "Status: refused", "Reason: no adapter"]


def test_plan_text_shows_shard_cold_witness_and_reuse_summaries():
    eligible = _render(_plan_payload(shard="1/2", cold_witness={"eligible": True, "refusal": None}))
    assert "Shard: 1/2" in eligible and "Cold witness: eligible" in eligible
    refused = _render(_plan_payload(cold_witness={"eligible": False, "refusal": "not sequential"}))
    assert "Cold witness: refused (not sequential)" in refused
    reuse = _render(_plan_payload(reuse_from={"classification_counts": {"reusable": 2, "stale": 1}}))
    assert "Prior-verdict classifications: reusable=2, stale=1" in reuse
    empty = _render(_plan_payload(reuse_from={"classification_counts": {}}))
    assert "Prior-verdict classifications: none" in empty
    assert "Use --json" not in empty
    assert "Use --json" in _render(_plan_payload(candidates=[{}]))


def _preview(lane, *, adapter, command_plan, cwd):
    return cli._cold_witness_plan_preview(lane, adapter=adapter, command_plan=command_plan, cwd=cwd)


def test_cold_witness_preview_refusals(tmp_path):
    from types import SimpleNamespace

    lane = _r2_lane()
    python = SimpleNamespace(name="python")
    plain = _preview(make_lane(), adapter=python, command_plan=None, cwd=tmp_path)
    assert plain == {"eligible": False, "refusal": "cold witness needs a native Python R2 lane",
                     "argv_transformed": None}
    unresolved = _preview(lane, adapter=python, command_plan=None, cwd=tmp_path)
    assert unresolved["refusal"] == "command environment could not be resolved"
    bad_cov = SimpleNamespace(argv_declared=("pytest", "--cov", "pkg"), argv_appended=(), env_effective={})
    refused = _preview(lane, adapter=python, command_plan=bad_cov, cwd=tmp_path)
    assert refused["refusal"] == "unrecognized coverage option --cov"
    assert refused["argv_transformed"] is None
    shaped = SimpleNamespace(argv_declared=("pytest", "tests"), argv_appended=(), env_effective={})
    outcome = _preview(lane, adapter=python, command_plan=shaped, cwd=tmp_path)
    assert outcome["refusal"] == "not a sequential pytest command"
    assert outcome["argv_transformed"] == ["pytest", "tests"]


def test_pilot_summary_of_a_completed_run_carries_no_claim_diagnostics():
    from types import SimpleNamespace

    candidate = "c" * 64
    r2_claim = SimpleNamespace(
        rigor="R2", mutation=SimpleNamespace(**{b: () for b in cli.MUTATION_BUCKETS}),
        status=cli.Outcome.PASS, reason_code=None, detail="d", detail_dropped_bytes=0,
    )
    verdict = SimpleNamespace(claims=(r2_claim,), judgment=None, outcome=cli.Outcome.PASS,
                              reason_code=None, commit="a" * 40)
    lane = SimpleNamespace(name="package", judge=SimpleNamespace(mutation=SimpleNamespace(jobs=1)))
    summary = cli._build_pilot_summary(
        verdict, lane=lane, selected_ids=frozenset({candidate}), selection_order=(candidate,),
        selection_sha256="b" * 64, candidates_file_sha256="d" * 64, state_dir=Path("state"),
        pilot_jobs={}, judge_sha256="e" * 64, campaign_deadline_sha256=None, cold_witness=False,
        state_root_fd=None, completed=True,
    )
    assert "claim_diagnostics" not in summary
    assert summary["unresolved"] == [candidate]


def test_plan_rows_without_a_deadline_hook_and_for_no_jobs(monkeypatch):
    from types import SimpleNamespace

    site = SimpleNamespace(operator="op", start_byte=1, end_byte=2, lineno=3, description="d")
    job = SimpleNamespace(path="a.py", site=site)
    monkeypatch.setattr(
        cli.mutation, "candidate_identity_fields",
        lambda item: {"source_sha256": "s", "mutated_file_sha256": "m"},
    )
    monkeypatch.setattr(cli, "_plan_candidate_id", lambda item: "id")
    assert [row["path"] for row in cli._plan_rows_from_jobs([job])] == ["a.py"]
    assert cli._plan_rows_from_jobs([], deadline_check=lambda: 0.0) == []


def test_plan_text_reports_an_unavailable_resource_observer():
    text = _render(_plan_payload(resource_observation={"available": False, "reason": "no cgroup"}))
    assert "Resource observation: unavailable (no cgroup)" in text
    assert "Resource observation" not in _render(_plan_payload(resource_observation={"available": True}))


# ------------------------------------------------- real plan / pilot over a git repo


def _seed_r2_repo(repo, *, argv=None):
    repo.write("src/mod.py", "def base():\n    return 0\n")
    base = repo.commit_all("add base source")
    repo.write("src/mod.py", "def a():\n    return a > 0\n\ndef b():\n    return b > 0\n")
    argv = argv or ["/bin/sh", "-c", "grep -Fqx '    return a > 0' src/mod.py"]
    import json

    config = repo.write(
        "assay.toml",
        f"""\
schema_version = 2

[lanes.package]
scope = "S1"
rigor = ["R0", "R2"]
enforcement = "gate"
argv = {json.dumps(argv)}
env = {{}}
env_passthrough = ["PATH"]
budget = "2m"
allow_argv_append = false

[lanes.package.isolation]
snapshot_selection = "repository"

[lanes.package.judge]
language = "python"
source_roots = ["src"]
base = "{base}"

[lanes.package.judge.mutation]
jobs = 1
max_mutants = 32
operators = ["python:compare-swap"]
budget_per_candidate = "30s"
""",
    )
    repo.commit_all("add lane")
    return config


def test_plan_with_cold_witness_adds_the_static_eligibility_preview(git_repo):
    import json

    config = _seed_r2_repo(git_repo, argv=["/bin/true"])
    code, out, err = _run_main(["plan", "--json", "package", "--file", str(config), "--cold-witness"])
    assert code == 0, err
    preview = json.loads(out)["cold_witness"]
    assert preview["eligible"] is False
    assert preview["refusal"] == "not a sequential pytest command"


def _pilot_args(git_repo, tmp_path, config, *, ids=None):
    import json

    code, out, err = _run_main(["plan", "--json", "package", "--file", str(config)])
    assert code == 0, err
    chosen = ids if ids is not None else [json.loads(out)["candidates"][0]["id"]]
    candidates = tmp_path / "ids.txt"
    candidates.write_text("\n".join(chosen) + "\n")
    return [
        "run", "package", "--file", str(config), "--candidates-file", str(candidates),
        "--state-dir", str(tmp_path / "state"),
    ]


def _wrap_run_reserved(monkeypatch, **forced):
    real = cli._run_reserved

    def wrapper(args, lane, *rest, **kwargs):
        lane = forced.get("lane", lambda value: value)(lane)
        kwargs.update({k: v for k, v in forced.items() if k != "lane"})
        return real(args, lane, *rest, **kwargs)

    monkeypatch.setattr(cli, "_run_reserved", wrapper)


@pytest.mark.parametrize(
    ("discovered", "message"),
    [
        ("unsupported", "cannot enumerate candidates for this pilot"),
        ("oversized", "exceeds judge.mutation.max_mutants"),
    ],
)
def test_pilot_refuses_a_plan_it_cannot_select_from(git_repo, tmp_path, monkeypatch, discovered, message):
    from types import SimpleNamespace

    config = _seed_r2_repo(git_repo)
    argv = _pilot_args(git_repo, tmp_path, config)
    jobs = cli.mutation.UNSUPPORTED if discovered == "unsupported" else tuple(range(40))
    monkeypatch.setattr(cli, "_discover_plan_jobs", lambda *a, **k: SimpleNamespace(jobs=jobs))
    code, out, err = _run_main(argv)
    assert code == 2
    assert message in err


def test_pilot_refuses_a_lane_whose_mutation_policy_vanished_before_selection(git_repo, tmp_path, monkeypatch):
    config = _seed_r2_repo(git_repo)
    argv = _pilot_args(git_repo, tmp_path, config)
    _wrap_run_reserved(
        monkeypatch,
        lane=lambda lane: dataclasses.replace(lane, judge=dataclasses.replace(lane.judge, mutation=None)),
    )
    # the summary builder reads the (now absent) policy; the refusal itself is what is under test
    monkeypatch.setattr(cli, "_build_pilot_summary", lambda *a, **k: {"completed": False, "unresolved": [], "claim_diagnostics": {}})
    code, out, err = _run_main(argv)
    assert code == 2
    assert '"completed": false' in out
    assert "--candidates-file requires a native R2 mutation lane" in err


def test_pilot_requires_its_digest_and_state_directory_at_the_reserved_run(git_repo, tmp_path, monkeypatch):
    config = _seed_r2_repo(git_repo)
    argv = _pilot_args(git_repo, tmp_path, config)
    _wrap_run_reserved(monkeypatch, candidates_file_sha256=None)
    with pytest.raises(ValueError, match="pilot selection requires its file digest and resolved state directory"):
        _run_main(argv)


def test_pilot_without_a_state_lock_guard_or_preflight_hook_still_completes(git_repo, tmp_path, monkeypatch):
    import json

    config = _seed_r2_repo(git_repo)
    argv = _pilot_args(git_repo, tmp_path, config)
    _wrap_run_reserved(monkeypatch, state_lock_guard=None)
    code, out, err = _run_main(argv)
    summary = json.loads(out)
    assert summary["completed"] is True, err
    assert (tmp_path / "state" / "PILOT-STATE").exists()
