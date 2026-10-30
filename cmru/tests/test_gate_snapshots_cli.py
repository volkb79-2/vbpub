"""Behaviour of ``cmru gate-snapshots`` (C-6): listing and safe, repeatable removal."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from cmru import cli, exit_codes, gate_snapshot


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=root, capture_output=True, text=True, check=True,
    )
    return result.stdout.strip()


@pytest.fixture
def repository(tmp_path, monkeypatch):
    """A linked source worktree holding one closed (resumable) gate snapshot."""
    primary = tmp_path / "primary"
    primary.mkdir()
    _git(primary, "init", "--quiet", "--initial-branch=main")
    _git(primary, "config", "user.name", "CMRU Gate Test")
    _git(primary, "config", "user.email", "cmru-gate@example.invalid")
    (primary / "README.md").write_text("tracked fixture\n", encoding="utf-8")
    _git(primary, "add", "README.md")
    _git(primary, "commit", "--quiet", "-m", "fixture")
    source = tmp_path / "source"
    _git(primary, "worktree", "add", "--quiet", "-b", "gate-source", str(source), "HEAD")
    snapshot = gate_snapshot.open_gate_snapshot(source)
    snapshot.run_gate_identity = {"revision": 68, "version": "24.0.0"}
    gate_snapshot.write_snapshot_manifest(snapshot, "failed")
    gate_snapshot.close_gate_snapshot(snapshot)
    monkeypatch.chdir(source)
    return source, snapshot


def test_listing_names_each_snapshot_status_and_reason(repository, capsys):
    source, snapshot = repository
    assert cli.main(["gate-snapshots"]) == 0
    output = capsys.readouterr().out
    assert f"retained: {snapshot.branch}" in output
    assert str(snapshot.worktree_path) in output and str(source) in output
    assert "a retry can resume it" in output
    assert snapshot.worktree_path.is_dir()


def test_json_listing_is_machine_readable(repository, capsys):
    _source, snapshot = repository
    assert cli.main(["gate-snapshots", "--json", "--include-current"]) == 0
    (record,) = json.loads(capsys.readouterr().out)
    assert record["branch"] == snapshot.branch
    assert record["status"] == gate_snapshot.REMOVABLE


def test_remove_keeps_a_resumable_snapshot_unless_asked(repository, capsys):
    _source, snapshot = repository
    assert cli.main(["gate-snapshots", "--remove"]) == 0
    assert snapshot.worktree_path.is_dir()
    assert "Removed" not in capsys.readouterr().out


def test_dry_run_names_the_removal_and_changes_nothing(repository, capsys):
    _source, snapshot = repository
    assert cli.main(["gate-snapshots", "--remove", "--include-current", "--dry-run"]) == 0
    assert f"[DRY RUN] Would remove gate snapshot {snapshot.worktree_path}" in capsys.readouterr().out
    assert snapshot.worktree_path.is_dir()


def test_remove_is_repeatable(repository, capsys):
    source, snapshot = repository
    assert cli.main(["gate-snapshots", "--remove", "--include-current"]) == 0
    assert f"Removed gate snapshot {snapshot.worktree_path}" in capsys.readouterr().out
    assert not snapshot.worktree_path.exists()
    assert _git(source, "branch", "--list", snapshot.branch) == ""
    assert cli.main(["gate-snapshots", "--remove", "--include-current"]) == 0
    assert "No retained CMRU gate snapshots." in capsys.readouterr().out


def test_a_fresh_gate_after_removal_is_not_bound_by_the_removed_snapshots_manifest(
    repository, capsys,
):
    # The fixture's manifest records an incomplete Run-Gate identity, which
    # rightly refuses a resume. Once the operator follows that refusal's own
    # remedy and removes the snapshot, the leftover manifest describes no
    # checkout: the next gate at the same commit must start fresh.
    source, snapshot = repository
    assert cli.main(["gate-snapshots", "--remove", "--include-current"]) == 0
    capsys.readouterr()

    reopened = gate_snapshot.open_gate_snapshot(source)
    try:
        assert reopened.worktree_path == snapshot.worktree_path
        assert reopened.run_gate_identity == {}
    finally:
        gate_snapshot.close_gate_snapshot(reopened)


def test_a_snapshot_that_cannot_be_removed_fails_the_verb_and_names_why(
    repository, monkeypatch, capsys,
):
    _source, snapshot = repository

    def refuse(*_args, **_kwargs):
        raise RuntimeError("lease went live")

    monkeypatch.setattr(gate_snapshot, "remove_gate_snapshot", refuse)
    assert cli.main(["gate-snapshots", "--remove", "--include-current"]) == exit_codes.FAILURE
    error = capsys.readouterr().err
    assert f"could not remove gate snapshot {snapshot.worktree_path}: lease went live" in error
    assert snapshot.worktree_path.is_dir()


def test_inspection_failure_is_reported_not_raised(repository, monkeypatch, capsys):
    def broken(*_args, **_kwargs):
        raise RuntimeError("records unreadable")

    monkeypatch.setattr(gate_snapshot, "inspect_gate_snapshots", broken)
    assert cli.main(["gate-snapshots"]) == exit_codes.FAILURE
    assert "cannot inspect gate snapshots: records unreadable" in capsys.readouterr().err


def test_outside_a_repository_is_a_usage_error(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert cli.main(["gate-snapshots"]) == exit_codes.CONFIG_ERROR
    assert "must run inside the repository" in capsys.readouterr().err


# ─── false "retained": a record whose snapshot is already gone (KI-92) ────────────────

import shutil  # noqa: E402


def _report(source):
    (item,) = gate_snapshot.inspect_gate_snapshots(source, include_current=True)
    return item


def _branch_exists(root, branch) -> bool:
    return bool(_git(root, "branch", "--list", branch))


def test_a_record_whose_directory_and_registration_are_gone_is_stale_not_retained(
        repository, capsys):
    source, snapshot = repository
    _git(source, "worktree", "remove", "--force", str(snapshot.worktree_path))  # the cleanup
    assert not snapshot.worktree_path.exists()
    assert _branch_exists(source, snapshot.branch)  # git leaves the branch behind

    assert cli.main(["gate-snapshots"]) == 0
    output = capsys.readouterr().out
    assert f"stale-record: {snapshot.branch}" in output
    assert "retained:" not in output  # the controlled-wrong status
    assert "no longer registers" in output


def test_remove_dry_run_changes_nothing_then_remove_drops_the_stale_record(repository, capsys):
    source, snapshot = repository
    record_path = _report(source).record.record_path
    _git(source, "worktree", "remove", "--force", str(snapshot.worktree_path))

    assert cli.main(["gate-snapshots", "--remove", "--dry-run"]) == 0
    captured = capsys.readouterr()
    assert "drop the stale record" in captured.err + captured.out
    assert record_path.exists() and _branch_exists(source, snapshot.branch)

    assert cli.main(["gate-snapshots", "--remove"]) == 0
    assert not record_path.exists()
    assert not _branch_exists(source, snapshot.branch)  # it still pointed at the recorded commit
    capsys.readouterr()
    assert cli.main(["gate-snapshots"]) == 0  # repeatable: nothing left to list
    assert "No retained CMRU gate snapshots" in capsys.readouterr().out


def test_a_leftover_branch_that_moved_is_kept_when_the_stale_record_is_dropped(repository):
    source, snapshot = repository
    record_path = _report(source).record.record_path
    _git(source, "worktree", "remove", "--force", str(snapshot.worktree_path))
    _git(source, "commit", "--quiet", "--allow-empty", "-m", "moved")  # advance gate-source
    _git(source, "branch", "-f", snapshot.branch, "HEAD")  # branch no longer at the record's commit

    assert cli.main(["gate-snapshots", "--remove"]) == 0

    assert not record_path.exists()
    assert _branch_exists(source, snapshot.branch)  # not ours any more: never deleted


def test_a_registered_but_missing_directory_is_retained_and_distinct(repository, capsys):
    source, snapshot = repository
    shutil.rmtree(snapshot.worktree_path)  # directory gone, `git worktree` still registers it

    item = _report(source)
    assert item.status == gate_snapshot.RETAINED
    assert "still registers" in item.reason and "git worktree prune" in item.reason
    assert cli.main(["gate-snapshots", "--remove"]) == 0
    assert item.record.record_path.exists()  # --remove did not touch it


def test_an_existing_but_deregistered_directory_is_retained_and_distinct(repository):
    source, snapshot = repository
    common = Path(_git(source, "rev-parse", "--git-common-dir"))
    common = common if common.is_absolute() else source / common
    shutil.rmtree(common / "worktrees" / snapshot.worktree_path.name)  # deregister, keep dir

    item = _report(source)
    assert item.status == gate_snapshot.RETAINED
    assert "exists but Git does not register it" in item.reason
    assert snapshot.worktree_path.is_dir()


def test_a_missing_directory_with_the_branch_at_another_path_is_not_inferred_gone(
        repository, monkeypatch):
    # another mount namespace may spell the same checkout differently: never call it stale
    source, snapshot = repository
    shutil.rmtree(snapshot.worktree_path)
    api = gate_snapshot._worktree_api()
    real = api.list_git_worktrees
    elsewhere = api.GitWorktree(
        path=Path("/host/spelling/of/it"), head=None, branch=f"refs/heads/{snapshot.branch}",
        is_primary=False,
    )
    monkeypatch.setattr(
        api, "list_git_worktrees",
        lambda path: [e for e in real(path) if e.path != snapshot.worktree_path] + [elsewhere],
    )
    item = _report(source)
    assert item.status == gate_snapshot.RETAINED and "another mount namespace" in item.reason


# ─── the remaining KI-92 branches, driven through the production functions ────────────

import dataclasses  # noqa: E402
import errno  # noqa: E402
import os  # noqa: E402


def _lstat_fails_for(monkeypatch, target: Path, *, after: int = 0, code: int = errno.EACCES):
    """Make ``Path.lstat`` raise ``OSError(code)`` for ``target`` only, after ``after`` good calls."""
    real = Path.lstat
    seen = {"calls": 0}

    def lstat(self, *args, **kwargs):
        if self == target:
            seen["calls"] += 1
            if seen["calls"] > after:
                raise OSError(code, os.strerror(code), str(self))
        return real(self, *args, **kwargs)

    monkeypatch.setattr(Path, "lstat", lstat)
    return seen


def test_a_directory_that_cannot_be_inspected_is_unreadable_retained_and_never_removed(
        repository, monkeypatch, capsys):
    source, snapshot = repository
    seen = _lstat_fails_for(monkeypatch, snapshot.worktree_path, after=0)

    item = _report(source)

    assert seen["calls"] >= 1
    assert item.status == gate_snapshot.RETAINED  # not stale, not removable: the fact is unknown
    assert item.reason == (
        f"cannot inspect CMRU gate snapshot {snapshot.worktree_path}: "
        f"[Errno {errno.EACCES}] {os.strerror(errno.EACCES)}: '{snapshot.worktree_path}'"
    )
    assert cli.main(["gate-snapshots", "--remove"]) == 0
    capsys.readouterr()
    assert item.record.record_path.exists()  # --remove left it alone
    assert _branch_exists(source, snapshot.branch)


def test_a_directory_that_becomes_unreadable_between_the_two_checks_is_retained(
        repository, monkeypatch):
    # first lstat (presence) succeeds, the second (checkout validation) fails: a race, not a stale record
    source, snapshot = repository
    seen = _lstat_fails_for(monkeypatch, snapshot.worktree_path, after=1)

    item = _report(source)

    assert seen["calls"] == 2
    assert item.status == gate_snapshot.RETAINED
    assert item.reason.startswith(f"cannot inspect CMRU gate snapshot {snapshot.worktree_path}: ")
    assert os.strerror(errno.EACCES) in item.reason


def test_validate_snapshot_checkout_names_an_uninspectable_directory(repository, monkeypatch):
    source, snapshot = repository
    record = _report(source).record
    _lstat_fails_for(monkeypatch, snapshot.worktree_path, after=0, code=errno.EIO)

    with pytest.raises(RuntimeError) as refused:
        gate_snapshot._validate_snapshot_checkout(record, record.git_common_dir)

    assert str(refused.value).startswith(
        f"cannot inspect CMRU gate snapshot {snapshot.worktree_path}: "
    )
    assert os.strerror(errno.EIO) in str(refused.value)
    assert isinstance(refused.value.__cause__, OSError)


def test_dropping_a_stale_record_does_not_delete_a_branch_that_does_not_exist(
        repository, monkeypatch, capsys):
    source, snapshot = repository
    record_path = _report(source).record.record_path
    _git(source, "worktree", "remove", "--force", str(snapshot.worktree_path))
    _git(source, "branch", "-D", snapshot.branch)  # the leftover branch is already gone
    assert not _branch_exists(source, snapshot.branch)
    calls: list[tuple[str, ...]] = []
    real_git = gate_snapshot._git

    def spy(root, *args, **kwargs):
        calls.append(args)
        return real_git(root, *args, **kwargs)

    monkeypatch.setattr(gate_snapshot, "_git", spy)

    assert cli.main(["gate-snapshots", "--remove"]) == 0
    capsys.readouterr()

    assert not record_path.exists()  # the record was still dropped
    assert any(call[:2] == ("rev-parse", "--verify") for call in calls)  # it looked for the branch
    assert not any(call[:1] == ("branch",) for call in calls)  # and never ran `branch -D`


def test_a_record_that_changed_while_it_was_being_dropped_is_not_removed(repository, monkeypatch):
    source, snapshot = repository
    record = _report(source).record
    _git(source, "worktree", "remove", "--force", str(snapshot.worktree_path))
    api = gate_snapshot._worktree_api()
    changed = dataclasses.replace(record, branch="cmru-gate-someone-elses-branch")
    monkeypatch.setattr(api, "read_record", lambda _path: changed)  # the record moved under the lock

    with pytest.raises(RuntimeError) as refused:
        gate_snapshot._drop_stale_record(record, record.git_common_dir, source)

    assert str(refused.value) == (
        f"CMRU gate snapshot record changed while it was being dropped: {record.record_path}"
    )
    assert record.record_path.exists()  # nothing was unlinked


def test_a_record_that_moved_its_path_while_being_dropped_is_not_removed_either(
        repository, monkeypatch):
    source, snapshot = repository
    record = _report(source).record
    _git(source, "worktree", "remove", "--force", str(snapshot.worktree_path))
    api = gate_snapshot._worktree_api()
    changed = dataclasses.replace(record, worktree_path=record.worktree_path.parent / "elsewhere")
    monkeypatch.setattr(api, "read_record", lambda _path: changed)

    with pytest.raises(RuntimeError, match="changed while it was being dropped"):
        gate_snapshot._drop_stale_record(record, record.git_common_dir, source)

    assert record.record_path.exists()
