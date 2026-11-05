"""Fail-closed boundary coverage for the neutral workspace substrate."""
from __future__ import annotations

import json
import inspect
import os
import shutil
import stat
import subprocess
from contextlib import nullcontext
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

import worktree.core as core
from worktree import (
    Lease,
    WorkspaceContext,
    WorkspaceError,
    acquire_lease,
    adopt_workspace,
    create_workspace,
    ensure_workspace,
    finish_workspace_removal,
    inspect_workspace,
    list_workspaces,
    remove_unrecorded_workspace,
    remove_workspace,
    release_lease,
    workspace_lock,
)


def _git(path: Path, *args: str, check: bool = True) -> str:
    result = subprocess.run(["git", *args], cwd=path, text=True, capture_output=True)
    if check and result.returncode:
        raise AssertionError(result.stderr)
    return result.stdout.strip()


def _branch_exists(repository: Path, branch: str) -> bool:
    result = subprocess.run(
        ["git", "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"],
        cwd=repository,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode not in (0, 1):
        raise AssertionError(result.stderr)
    return result.returncode == 0


def _add_merged_linked_checkout(repository: Path, target: Path, branch: str) -> None:
    _git(repository, "worktree", "add", "-q", "-b", branch, str(target), "HEAD")
    (target / "README").write_text(f"{branch}\n", encoding="utf-8")
    _git(target, "commit", "-q", "-am", f"commit {branch}")
    _git(repository, "merge", "-q", "--ff-only", branch)


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "test@example.invalid")
    _git(repo, "config", "user.name", "worktree tests")
    (repo / "README").write_text("source\n", encoding="utf-8")
    _git(repo, "add", "README")
    _git(repo, "commit", "-q", "-m", "initial")
    return repo


def test_value_objects_and_lease_validation():
    namespace = core.ResourceNamespace("abc123", {"role": "test"})
    assert namespace.as_dict() == {"workspace_id": "abc123", "labels": {"role": "test"}}
    with pytest.raises(WorkspaceError, match="holder"):
        Lease("", "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z", None, "perpetual")
    with pytest.raises(WorkspaceError, match="unknown lease mode"):
        Lease("x", "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z", None, "other")
    with pytest.raises(WorkspaceError, match="held leases"):
        Lease("x", "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z", None, "held")
    with pytest.raises(WorkspaceError, match="perpetual leases"):
        Lease("x", "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z", "2026-01-02T00:00:00Z", "perpetual")
    assert Lease("x", "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z", None, "perpetual").to_dict()["mode"] == "perpetual"
    context = WorkspaceContext(
        Path("/source"), Path("/worktree"), Path("/common"), Path("/physical"),
        "abc123", "main", "head", Path("/record"), namespace,
    )
    assert context.logical_worktree_path == Path("/worktree")
    invocation = core.InvocationContext(Path("/invoke"))
    git_worktree = core.GitWorktree(Path("/checkout"), "head", "main", False)
    assert git_worktree.is_detached is False
    assert git_worktree.is_bare is False
    assert git_worktree.is_prunable is False
    assert git_worktree.is_locked is False
    for value, attribute, replacement in (
        (namespace, "workspace_id", "changed"),
        (Lease("x", "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z", None, "perpetual"), "holder", "y"),
        (context, "branch", "changed"),
        (invocation, "invocation_dir", Path("/changed")),
        (git_worktree, "branch", "changed"),
    ):
        with pytest.raises(FrozenInstanceError):
            setattr(value, attribute, replacement)


def test_path_math_git_errors_and_invocation_resolution(monkeypatch, tmp_path):
    assert core.physical_path("/outside", logical_root="/logical", physical_root="/host") == Path("/outside")
    with pytest.raises(ValueError, match="non-negative"):
        core._base36(-1)
    assert core._base36(0) == "0"
    assert core._absolute_lexical_path("relative/path").is_absolute()
    monkeypatch.setattr(
        core.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 1, stdout="", stderr="bad git"),
    )
    with pytest.raises(WorkspaceError, match="bad git"):
        core._git(tmp_path, "status")

    file_path = tmp_path / "file"
    file_path.write_text("x")
    calls = []

    def fake_git(cwd, *args, **kwargs):
        calls.append((cwd, args))
        if args == ("rev-parse", "--show-toplevel"):
            return str(tmp_path)
        if args == ("rev-parse", "--git-common-dir"):
            return ".git"
        if args == ("branch", "--show-current"):
            return ""
        return "head"

    monkeypatch.setattr(core, "_git", fake_git)
    top, common, branch, head = core.discover_git_context(file_path)
    assert top == tmp_path and common == tmp_path / ".git" and branch == "HEAD" and head == "head"
    monkeypatch.undo()
    with pytest.raises(WorkspaceError, match="could not start"):
        core.discover_git_context(tmp_path / "missing" / "nested")
    selected_paths = []

    def discover(selected):
        selected_paths.append(selected)
        return tmp_path, tmp_path / ".git", "main", "head"

    monkeypatch.setattr(core, "discover_git_context", discover)
    invocation = core.resolve_invocation(tmp_path / "nested", root_folder=tmp_path)
    assert invocation.invocation_dir == (tmp_path / "nested").resolve()
    assert invocation.worktree_path == tmp_path
    assert selected_paths[-1] == tmp_path.resolve()


def test_git_root_discovery_does_not_require_a_commit(tmp_path):
    repo = tmp_path / "unborn"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")

    assert core.discover_git_root(repo) == (repo.resolve(), (repo / ".git").resolve())
    with pytest.raises(WorkspaceError, match="rev-parse HEAD failed"):
        core.discover_git_context(repo)


def test_physical_translation_and_identity_never_resolve_namespace_paths(
    monkeypatch, tmp_path
):
    def forbidden_resolve(*_args, **_kwargs):
        pytest.fail("namespace path was probed through Path.resolve")

    monkeypatch.setattr(Path, "resolve", forbidden_resolve)
    translated = core.physical_path(
        "/logical/repo/link/../child",
        logical_root="/logical/repo",
        physical_root="/host/root/../repo",
    )
    assert translated == Path("/host/repo/child")
    assert core.workspace_id_for_path(tmp_path / "a" / ".." / "b") == (
        core.workspace_id_for_path(tmp_path / "b")
    )


def test_lock_and_timestamp_validation(monkeypatch, tmp_path):
    with workspace_lock(tmp_path / ".git"):
        assert (tmp_path / ".git" / core._LOCK_NAME).exists()

    nested_git = tmp_path / "nested" / ".git"
    with workspace_lock(nested_git):
        assert (nested_git / core._LOCK_NAME).exists()

    monkeypatch.setattr(core.fcntl, "flock", lambda *_args: (_ for _ in ()).throw(OSError("locked")))
    with pytest.raises(WorkspaceError, match="cannot acquire"):
        with workspace_lock(tmp_path / "lock-git"):
            pass
    with pytest.raises(WorkspaceError, match=r"lease x is not a timestamp$"):
        core._parse_timestamp(None, label="x")
    with pytest.raises(WorkspaceError, match=r"lease x is not a timestamp$"):
        core._parse_timestamp("", label="x")
    with pytest.raises(WorkspaceError, match="timestamp"):
        core._parse_timestamp("not-a-date", label="x")
    with pytest.raises(WorkspaceError, match="no UTC offset"):
        core._parse_timestamp("2026-01-01T00:00:00", label="x")


def test_record_parser_rejects_every_structural_corruption(repository, tmp_path):
    context = create_workspace(repository, tmp_path / "checkout", branch="test/record", purpose="test")
    record = core.read_record(context.record_path)
    raw = record.as_dict()
    cases = [
        ([], "one JSON object"),
        ({**raw, "extra": 1}, "unexpected workspace-record keys"),
        ({**raw, "record_version": 9}, "unsupported workspace record"),
        ({**raw, "record_version": True}, "unsupported workspace record"),
        ({**raw, "workspace_id": "bad"}, "invalid workspace_id"),
        ({**raw, "labels": []}, "labels.*must be an object"),
        ({**raw, "source_git_root": ""}, "source_git_root.*must be a path"),
        ({**raw, "branch": []}, "branch.*must be a non-empty string"),
        ({**raw, "created_at_utc": "not-a-date"}, "created_at_utc.*timestamp"),
        ({**raw, "lease": []}, "lease must be an object"),
        ({**raw, "lease": {"holder": "x"}}, "lease keys"),
    ]
    for candidate, message in cases:
        with pytest.raises(WorkspaceError, match=message):
            core._record(candidate, context.record_path)
    broken_lease = {
        "holder": "x", "acquired_at_utc": "bad", "renewed_at_utc": "2026-01-01T00:00:00Z",
        "expires_at_utc": None, "mode": "perpetual",
    }
    with pytest.raises(WorkspaceError, match="timestamp"):
        core._lease(broken_lease)
    assert list_workspaces(record.git_common_dir)
    remove_workspace(context)


def test_record_and_lease_boundary_predicates_are_independent(repository, tmp_path):
    context = create_workspace(repository, tmp_path / "checkout", branch="test/predicates", purpose="test")
    record = core.read_record(context.record_path)
    for value in (None, ""):
        with pytest.raises(WorkspaceError, match="timestamp"):
            core._parse_timestamp(value, label="boundary")
    malformed = record.as_dict()
    malformed["branch"] = []
    with pytest.raises(WorkspaceError, match="branch"):
        core._record(malformed, context.record_path)
    malformed["branch"] = ""
    with pytest.raises(WorkspaceError, match="branch"):
        core._record(malformed, context.record_path)
    malformed["branch"] = ["non-empty wrong type"]
    with pytest.raises(WorkspaceError, match="branch"):
        core._record(malformed, context.record_path)
    remove_workspace(context)


def test_record_io_and_handle_coercion_errors(repository, tmp_path, monkeypatch):
    context = create_workspace(repository, tmp_path / "checkout", branch="test/io", purpose="test")
    record = core.read_record(context.record_path)
    assert core._coerce_record(context).workspace_id == record.workspace_id
    assert core._coerce_record(context.record_path).workspace_id == record.workspace_id
    with pytest.raises(WorkspaceError, match="does not exist"):
        core.read_record(tmp_path / "missing.json")
    invalid = tmp_path / "invalid.json"
    invalid.write_text("not-json")
    with pytest.raises(WorkspaceError, match="unreadable"):
        core.read_record(invalid)
    mismatched = dict(record.as_dict())
    mismatched["workspace_id"] = "zzzzzz"
    context.record_path.write_text(json.dumps(mismatched), encoding="utf-8")
    with pytest.raises(WorkspaceError, match="does not match"):
        core.read_record(context.record_path)
    misplaced = tmp_path / "misplaced.json"
    misplaced.write_text(json.dumps(record.as_dict()), encoding="utf-8")
    with pytest.raises(WorkspaceError, match="does not match its Git-family identity"):
        core.read_record(misplaced)
    monkeypatch.setattr(core.os, "replace", lambda *_args: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(WorkspaceError, match="could not write"):
        core.write_record(record)
    monkeypatch.undo()
    core.write_record(record)
    remove_workspace(context, force=True)


def test_write_record_uses_nested_parent_sorted_json_and_tolerant_cleanup(repository, tmp_path, monkeypatch):
    context = create_workspace(repository, tmp_path / "checkout", branch="test/write-contract", purpose="test")
    record = core.read_record(context.record_path)
    nested_common = tmp_path / "new" / ".git"
    nested_record = core.replace(record, git_common_dir=nested_common)
    unlink_calls = []
    real_unlink = Path.unlink

    def observe_unlink(path, *args, **kwargs):
        unlink_calls.append(kwargs.get("missing_ok"))
        if Path(path).name.startswith(".") and kwargs.get("missing_ok") is False:
            raise AssertionError("temporary cleanup must tolerate an absent file")
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", observe_unlink)
    path = core.write_record(nested_record)
    assert path.parent == nested_common / core.WORKSPACE_RECORD_DIR
    assert path.read_text(encoding="utf-8") == json.dumps(
        nested_record.as_dict(), indent=2, sort_keys=True
    ) + "\n"
    # Exercise the cleanup flag directly as part of the write failure contract.
    monkeypatch.setattr(core.os, "replace", lambda *_args: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(WorkspaceError, match="could not write"):
        core.write_record(nested_record)
    assert True in unlink_calls
    monkeypatch.undo()
    remove_workspace(context, force=True)


def test_path_exists_refuses_filesystem_indeterminacy(monkeypatch, tmp_path):
    monkeypatch.setattr(
        core.os,
        "lstat",
        lambda _path: (_ for _ in ()).throw(PermissionError("injected EACCES")),
    )
    with pytest.raises(WorkspaceError, match="could not inspect path.*EACCES"):
        core._path_exists(tmp_path / "unreadable")


def test_workspace_record_enumeration_distinguishes_absent_from_unreadable(
    repository, tmp_path, monkeypatch
):
    common = repository / ".git"
    assert list_workspaces(common) == []
    context = create_workspace(
        repository, tmp_path / "checkout", branch="test/scan", purpose="test"
    )
    records_dir = common / core.WORKSPACE_RECORD_DIR
    real_scandir = core.os.scandir

    def unreadable(path):
        if Path(path) == records_dir:
            raise PermissionError("injected EACCES")
        return real_scandir(path)

    monkeypatch.setattr(core.os, "scandir", unreadable)
    with pytest.raises(WorkspaceError, match="could not enumerate workspace records"):
        list_workspaces(common)
    monkeypatch.undo()
    remove_workspace(context, force=True)


def test_branch_and_collision_helpers(repository, tmp_path):
    context = create_workspace(repository, tmp_path / "checkout", branch="test/helpers", purpose="test")
    record = core.read_record(context.record_path)
    for branch in ("", "/absolute", "bad/../branch", "bad space"):
        with pytest.raises(WorkspaceError, match="invalid branch"):
            core._validate_branch(branch)
    with pytest.raises(WorkspaceError, match="physical workspace path"):
        core._check_collision([record], "other1", record.physical_worktree_path)
    with pytest.raises(core.WorkspaceCollisionError, match="identity collision"):
        core._check_collision([record], record.workspace_id, tmp_path / "other")
    nonmatching = SimpleNamespace(physical_worktree_path=tmp_path / "elsewhere", workspace_id="other1")
    core._check_collision([nonmatching], "target1", tmp_path / "target")
    assert core._record_for_path([record], record.worktree_path) is record
    assert core._record_for_path([record], tmp_path / "other") is None
    remove_workspace(context)


def test_create_workspace_input_and_git_failure_branches(repository, tmp_path, monkeypatch):
    with pytest.raises(WorkspaceError, match="invalid workspace purpose"):
        create_workspace(repository, tmp_path / "bad", branch="ok", purpose="bad space")
    with pytest.raises(WorkspaceError, match="physical_target must not be empty"):
        create_workspace(
            repository, tmp_path / "empty-physical", branch="empty-physical",
            physical_target="", purpose="test",
        )
    with pytest.raises(WorkspaceError, match="identity_path must not be empty"):
        create_workspace(
            repository, tmp_path / "empty-identity", branch="empty-identity",
            identity_path="", purpose="test",
        )
    with pytest.raises(WorkspaceError, match="controlled by identity_path"):
        core._new_record(
            source_git_root=repository,
            worktree_path=tmp_path / "target",
            physical_worktree_path=tmp_path / "target",
            git_common_dir=repository / ".git",
            branch="new-record",
            base_commit="head",
            purpose="test",
            labels={},
            metadata={"workspace.identity_path": "forbidden"},
        )
    common = repository / ".git"
    monkeypatch.setattr(core, "discover_git_context", lambda _path: (tmp_path / "top", common, "main", "head"))
    monkeypatch.setattr(core, "_workspace_lock", lambda _common: nullcontext())
    monkeypatch.setattr(core, "_records", lambda _common: [])
    monkeypatch.setattr(core.subprocess, "run", lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, stdout="", stderr="branch exists"))
    with pytest.raises(WorkspaceError, match="branch already exists"):
        create_workspace(repository, tmp_path / "branch", branch="new", purpose="test")

    monkeypatch.setattr(core.subprocess, "run", lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, stdout="", stderr=""))
    existing = tmp_path / "existing"
    existing.mkdir()
    with pytest.raises(WorkspaceError, match="path already exists"):
        create_workspace(repository, existing, branch="new", purpose="test")

    target = tmp_path / "recorded"
    recorded = SimpleNamespace(physical_worktree_path=tmp_path / "physical", workspace_id="other1", worktree_path=target)
    monkeypatch.setattr(core, "_records", lambda _common: [recorded])
    with pytest.raises(WorkspaceError, match="workspace path is already recorded"):
        create_workspace(repository, target, branch="recorded", purpose="test")

    monkeypatch.setattr(core, "_records", lambda _common: [])
    def fail_add(argv, **kwargs):
        if argv[1:4] == ["show-ref", "--verify", "--quiet"]:
            return subprocess.CompletedProcess(argv, 1, stdout="", stderr="")
        return subprocess.CompletedProcess(argv, 2, stdout="", stderr="add failed")

    monkeypatch.setattr(core.subprocess, "run", fail_add)
    with pytest.raises(WorkspaceError, match="worktree add failed"):
        create_workspace(repository, tmp_path / "add-fail", branch="new2", purpose="test")


def test_create_workspace_preserves_git_root_and_subprocess_contract(repository, tmp_path, monkeypatch):
    calls = []
    original = core.subprocess.run

    def observe(argv, **kwargs):
        calls.append((argv, kwargs))
        return original(argv, **kwargs)

    monkeypatch.setattr(core.subprocess, "run", observe)
    context = create_workspace(
        repository / "README", tmp_path / "new" / "nested" / "checkout",
        branch="test/subprocess-contract", purpose="test",
    )
    assert context.source_git_root == repository.resolve()
    show_ref = next(kwargs for argv, kwargs in calls if argv[1:4] == ["show-ref", "--verify", "--quiet"])
    add = next(kwargs for argv, kwargs in calls if argv[1:3] == ["worktree", "add"])
    assert show_ref == {"cwd": repository.resolve(), "text": True, "capture_output": True, "check": False}
    assert add == {"cwd": repository.resolve(), "text": True, "capture_output": True, "check": False}
    remove_workspace(context)


def test_create_workspace_reports_stdout_when_git_add_has_no_stderr(repository, tmp_path, monkeypatch):
    original = core.subprocess.run

    def fail_add(argv, **kwargs):
        if argv[1:4] == ["show-ref", "--verify", "--quiet"]:
            return subprocess.CompletedProcess(argv, 1, stdout="", stderr="")
        if argv[1:3] == ["worktree", "add"]:
            return subprocess.CompletedProcess(argv, 2, stdout="add stdout", stderr="")
        return original(argv, **kwargs)

    monkeypatch.setattr(core.subprocess, "run", fail_add)
    with pytest.raises(WorkspaceError, match="add stdout"):
        create_workspace(repository, tmp_path / "add-stdout", branch="test/add-stdout", purpose="test")


def test_create_workspace_record_rollback_and_identity_guards(repository, tmp_path, monkeypatch):
    common = repository / ".git"
    monkeypatch.setattr(core, "discover_git_context", lambda path: (Path(path), common, "main", "head"))
    monkeypatch.setattr(core, "_workspace_lock", lambda _common: nullcontext())
    monkeypatch.setattr(core, "_records", lambda _common: [])
    monkeypatch.setattr(core, "_git", lambda *_args, **_kwargs: "head")
    run_calls = []
    def successful_git_setup(argv, **kwargs):
        assert kwargs["check"] is False
        run_calls.append(argv)
        if argv[1:4] == ["show-ref", "--verify", "--quiet"]:
            return subprocess.CompletedProcess(argv, 1, stdout="", stderr="")
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(core.subprocess, "run", successful_git_setup)
    monkeypatch.setattr(core, "write_record", lambda _record: (_ for _ in ()).throw(OSError("write")))
    with pytest.raises(OSError, match="write"):
        create_workspace(repository, tmp_path / "rollback", branch="rollback", purpose="test")
    assert any(argv[1:3] == ["worktree", "remove"] for argv in run_calls)

    values = iter(["first1", "second2"])
    monkeypatch.setattr(core, "workspace_id_for_path", lambda _path: next(values))
    with pytest.raises(WorkspaceError, match="identity changed"):
        create_workspace(repository, tmp_path / "identity", branch="identity", purpose="test")


def test_adopt_workspace_validates_family_and_records_existing_checkout(repository, tmp_path):
    target = tmp_path / "adopted"
    _git(repository, "worktree", "add", "-q", "-b", "adopted", str(target), "HEAD")
    context = adopt_workspace(repository, target, purpose="test", identity_path=tmp_path / "seed")
    assert context.worktree_path == target.resolve()
    record = core.read_record(context.record_path)
    assert record.state == "adopted"
    remove_workspace(context)


def test_adopt_workspace_rejects_the_primary_checkout(repository):
    with pytest.raises(WorkspaceError, match="primary checkout"):
        adopt_workspace(repository, repository)


def test_remove_unrecorded_workspace_deletes_merged_branch_by_default(repository, tmp_path):
    target = tmp_path / "legacy"
    _add_merged_linked_checkout(repository, target, "legacy")
    with pytest.raises(WorkspaceError, match="branch changed"):
        remove_unrecorded_workspace(repository, target, expected_branch="wrong")
    assert target.is_dir()

    remove_unrecorded_workspace(
        repository, target, expected_branch="legacy", purpose="legacy-cleanup"
    )
    assert not target.exists()
    assert not _branch_exists(repository, "legacy")


def test_remove_unrecorded_workspace_can_keep_merged_branch(repository, tmp_path):
    target = tmp_path / "legacy-kept"
    _add_merged_linked_checkout(repository, target, "legacy-kept")

    remove_unrecorded_workspace(repository, target, delete_branch=False)

    assert not target.exists()
    assert _branch_exists(repository, "legacy-kept")


def test_adopt_workspace_rejects_wrong_top_and_family(monkeypatch, repository, tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    common = repository / ".git"
    monkeypatch.setattr(core, "discover_git_context", lambda _path: (tmp_path / "other", common, "main", "head"))
    with pytest.raises(WorkspaceError, match="worktree top level"):
        adopt_workspace(repository, target)
    monkeypatch.setattr(core, "discover_git_context", lambda path: (
        target if Path(path) == target else repository,
        tmp_path / ("other-common" if Path(path) == target else ".git"),
        "main", "head",
    ))
    with pytest.raises(WorkspaceError, match="different Git worktree family"):
        adopt_workspace(repository, target)

    monkeypatch.setattr(core, "discover_git_context", lambda path: (
        target if Path(path) == target else repository,
        repository / ".git", "main", "head",
    ))
    existing = SimpleNamespace(
        physical_worktree_path=tmp_path / "different-physical",
        workspace_id="other1", worktree_path=target,
    )
    monkeypatch.setattr(core, "_records", lambda _common: [existing])
    monkeypatch.setattr(core, "_workspace_lock", lambda _common: nullcontext())
    with pytest.raises(WorkspaceError, match="workspace path is already recorded"):
        adopt_workspace(repository, target)


def test_ensure_and_inspect_cover_stale_and_refresh_paths(repository, tmp_path, monkeypatch):
    context = create_workspace(repository, tmp_path / "checkout", branch="test/ensure", purpose="test")
    record = core.read_record(context.record_path)
    stale = core.replace(record, worktree_path=tmp_path / "other")
    with pytest.raises(WorkspaceError, match="record path changed"):
        ensure_workspace(stale)
    shutil.rmtree(context.worktree_path)
    with pytest.raises(WorkspaceError, match="checkout is missing"):
        ensure_workspace(record)
    context.worktree_path.mkdir()
    def mismatching_context(path):
        if Path(path) == record.source_git_root:
            return record.source_git_root, record.git_common_dir, "main", "head"
        return context.worktree_path, record.git_common_dir, "different", "head"

    monkeypatch.setattr(core, "discover_git_context", mismatching_context)
    with pytest.raises(WorkspaceError, match="no longer matches"):
        ensure_workspace(record)

    def matching_context(path):
        if Path(path) == record.source_git_root:
            return record.source_git_root, record.git_common_dir, "main", "head"
        return context.worktree_path, record.git_common_dir, record.branch, "head"

    monkeypatch.setattr(core, "discover_git_context", matching_context)
    updated = ensure_workspace(record, labels={"new": "label"}, metadata={"changed": True})
    assert updated.namespace.labels == {"new": "label"}
    inspected = inspect_workspace(updated)
    assert inspected["git"]["matches_record"] is True
    shutil.rmtree(context.worktree_path)
    assert inspect_workspace(updated)["git"]["state"] == "missing"
    cleanup_calls = []
    with pytest.raises(WorkspaceError, match="checkout is missing"):
        remove_workspace(
            context,
            cleanup=lambda _context: cleanup_calls.append("ran"),
            force=True,
        )
    assert cleanup_calls == []


def test_ensure_and_inspect_check_each_git_identity_axis(repository, tmp_path, monkeypatch):
    context = create_workspace(repository, tmp_path / "checkout", branch="test/axes", purpose="test")
    record = core.read_record(context.record_path)

    def contexts(path):
        if Path(path) == record.source_git_root:
            return record.source_git_root, record.git_common_dir, "main", "head"
        return context.worktree_path, record.git_common_dir, record.branch, "head"

    monkeypatch.setattr(core, "discover_git_context", contexts)
    for field, value in (("common", tmp_path / "other.git"), ("source_common", tmp_path / "source.git")):
        def mismatch(path, field=field, value=value):
            if Path(path) == record.source_git_root:
                common = value if field == "source_common" else record.git_common_dir
                return record.source_git_root, common, "main", "head"
            common = value if field == "common" else record.git_common_dir
            return context.worktree_path, common, record.branch, "head"

        monkeypatch.setattr(core, "discover_git_context", mismatch)
        with pytest.raises(WorkspaceError, match="no longer matches"):
            ensure_workspace(record)
    monkeypatch.setattr(core, "discover_git_context", contexts)
    for field, value in (("common", tmp_path / "other.git"), ("branch", "other")):
        def mismatch_inspect(path, field=field, value=value):
            if Path(path) == record.source_git_root:
                return record.source_git_root, record.git_common_dir, "main", "head"
            common = value if field == "common" else record.git_common_dir
            branch = value if field == "branch" else record.branch
            return context.worktree_path, common, branch, "head"

        monkeypatch.setattr(core, "discover_git_context", mismatch_inspect)
        assert inspect_workspace(record)["git"]["matches_record"] is False
    monkeypatch.undo()
    remove_workspace(context, force=True)


def test_inspect_refuses_permission_error_instead_of_reporting_missing(
    repository, tmp_path, monkeypatch
):
    context = create_workspace(
        repository, tmp_path / "checkout", branch="test/inspect", purpose="test"
    )
    real_stat = core.os.stat

    def denied(path, *args, **kwargs):
        if Path(path) == context.worktree_path:
            raise PermissionError("injected EACCES")
        return real_stat(path, *args, **kwargs)

    monkeypatch.setattr(core.os, "stat", denied)
    with pytest.raises(WorkspaceError, match="could not inspect directory"):
        inspect_workspace(context)


def test_lease_expiry_cleanup_and_release_failures(repository, tmp_path, monkeypatch):
    context = create_workspace(repository, tmp_path / "checkout", branch="test/lease", purpose="test")
    record = core.read_record(context.record_path)
    now = datetime.now(timezone.utc)
    assert core._lease_expired(None, now) is False
    perpetual = Lease("x", "2025-01-01T00:00:00Z", "2025-01-01T00:00:00Z", None, "perpetual")
    assert core._lease_expired(perpetual, now) is False
    expired = Lease("x", "2025-01-01T00:00:00Z", "2025-01-01T00:00:00Z", "2025-01-02T00:00:00Z", "held")
    assert core._lease_expired(expired, now) is True
    equal = Lease("x", "2025-01-01T00:00:00Z", "2025-01-01T00:00:00Z", core._stamp(now), "held")
    assert core._lease_expired(equal, now) is True
    held = acquire_lease(record, holder="owner", ttl=timedelta(minutes=5), now=now)
    with pytest.raises(WorkspaceError, match="active lease"):
        remove_workspace(held)
    assert release_lease(held).lease is None
    ran = []
    unlink_calls = []
    real_unlink = Path.unlink

    def observe_unlink(path, *args, **kwargs):
        if Path(path) == context.record_path:
            unlink_calls.append(kwargs.get("missing_ok"))
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", observe_unlink)
    remove_workspace(context, cleanup=lambda _ctx: ran.append(True), delete_branch=False)
    assert ran == [True]
    assert unlink_calls == [True]
    assert inspect.signature(remove_unrecorded_workspace).parameters["force"].default is False


def test_remove_workspace_reports_phases_and_preserves_branch_when_requested(
    repository, tmp_path
):
    context = create_workspace(
        repository, tmp_path / "checkout", branch="test/phase-report", purpose="test"
    )
    phases = []

    remove_workspace(
        context,
        delete_branch=False,
        on_phase=lambda phase, status: phases.append((phase, status)),
    )

    assert phases == [
        ("checkout_deletion", "running"),
        ("checkout_deletion", "complete"),
        ("git_registration", "running"),
        ("git_registration", "complete"),
        ("shared_record_removal", "running"),
        ("shared_record_removal", "complete"),
        ("branch_removal", "kept"),
    ]
    assert _git(repository, "show-ref", "--verify", "refs/heads/test/phase-report")


def test_remove_workspace_reports_record_removal_before_branch_deletion(
    repository, tmp_path
):
    context = create_workspace(
        repository, tmp_path / "checkout", branch="test/phase-delete", purpose="test"
    )
    tip = _git(repository, "rev-parse", "refs/heads/test/phase-delete")
    phases = []

    remove_workspace(
        context,
        expected_branch_tip=tip,
        on_phase=lambda phase, status: phases.append((phase, status)),
    )

    assert phases[-4:] == [
        ("shared_record_removal", "running"),
        ("shared_record_removal", "complete"),
        ("branch_removal", "running"),
        ("branch_removal", "deleted"),
    ]
    assert not context.record_path.exists()
    assert not _branch_exists(repository, "test/phase-delete")


def test_remove_workspace_uses_expected_tip_as_atomic_branch_delete_guard(
    repository, tmp_path, monkeypatch
):
    context = create_workspace(
        repository, tmp_path / "checkout", branch="test/moving-tip", purpose="test"
    )
    expected_tip = _git(repository, "rev-parse", "refs/heads/test/moving-tip")
    tree = _git(repository, "rev-parse", "refs/heads/main^{tree}")
    advanced_tip = _git(
        repository, "commit-tree", tree, "-p", expected_tip, "-m", "advanced tip"
    )
    real_run = core.subprocess.run
    phases = []

    def move_tip_before_compare(argv, *args, **kwargs):
        if argv[:3] == ["git", "update-ref", "-d"]:
            _git(repository, "update-ref", "refs/heads/test/moving-tip", advanced_tip)
        return real_run(argv, *args, **kwargs)

    monkeypatch.setattr(core.subprocess, "run", move_tip_before_compare)
    with pytest.raises(core.WorkspaceError, match="branch removal.*failed"):
        remove_workspace(
            context,
            expected_branch_tip=expected_tip,
            on_phase=lambda phase, status: phases.append((phase, status)),
        )

    assert _git(repository, "rev-parse", "refs/heads/test/moving-tip") == advanced_tip
    assert not context.worktree_path.exists()
    assert not context.record_path.exists()
    assert phases[-2:] == [
        ("branch_removal", "running"),
        ("branch_removal", "failed"),
    ]


def test_remove_workspace_reports_checkout_deletion_failure(repository, tmp_path, monkeypatch):
    context = create_workspace(
        repository, tmp_path / "checkout", branch="test/delete-failure", purpose="test"
    )
    original = core.subprocess.run
    phases = []

    def failed_remove(argv, **kwargs):
        if argv[1:3] == ["worktree", "remove"]:
            return subprocess.CompletedProcess(argv, 1, stdout="", stderr="locked")
        return original(argv, **kwargs)

    monkeypatch.setattr(core.subprocess, "run", failed_remove)
    with pytest.raises(WorkspaceError, match="partially removed.*locked"):
        remove_workspace(
            context, on_phase=lambda phase, status: phases.append((phase, status))
        )
    assert phases == [
        ("checkout_deletion", "running"),
        ("checkout_deletion", "failed"),
    ]
    assert context.worktree_path.is_dir()
    assert context.record_path.is_file()


def test_remove_workspace_retries_only_an_explicit_permission_failure(
    repository, tmp_path, monkeypatch
):
    context = create_workspace(
        repository, tmp_path / "checkout", branch="test/permission-recovery",
        purpose="test",
    )
    protected = context.worktree_path / "cache" / "root-owned.bin"
    protected.parent.mkdir()
    protected.write_bytes(b"state")
    phases = []
    original = core.subprocess.run
    calls = []

    def fail_first_remove(argv, *args, **kwargs):
        if argv[:3] == ["git", "worktree", "remove"] and not calls:
            calls.append("failed")
            return subprocess.CompletedProcess(
                argv, 1, stdout="", stderr="Permission denied"
            )
        return original(argv, *args, **kwargs)

    monkeypatch.setattr(core.subprocess, "run", fail_first_remove)
    recovered = []

    def recover(received_context, git_dir, error):
        assert received_context == context
        assert git_dir.is_dir()
        assert "Permission denied" in str(error)
        recovered.append(True)
        shutil.rmtree(protected.parent)
        return True

    remove_workspace(
        context, checkout_recovery=recover,
        on_phase=lambda phase, status: phases.append((phase, status)),
    )

    assert recovered == [True]
    assert not context.worktree_path.exists()
    assert not context.record_path.exists()
    assert phases == [
        ("checkout_deletion", "running"),
        ("checkout_deletion", "complete"),
        ("git_registration", "running"),
        ("git_registration", "complete"),
        ("shared_record_removal", "running"),
        ("shared_record_removal", "complete"),
        ("branch_removal", "running"),
        ("branch_removal", "deleted"),
    ]


def test_remove_workspace_does_not_offer_recovery_for_non_permission_failure(
    repository, tmp_path, monkeypatch
):
    context = create_workspace(
        repository, tmp_path / "checkout", branch="test/no-permission-recovery",
        purpose="test",
    )
    original = core.subprocess.run
    recovery_calls = []

    def fail_locked_remove(argv, *args, **kwargs):
        if argv[:3] == ["git", "worktree", "remove"]:
            return subprocess.CompletedProcess(argv, 1, stdout="", stderr="locked")
        return original(argv, *args, **kwargs)

    monkeypatch.setattr(core.subprocess, "run", fail_locked_remove)
    with pytest.raises(WorkspaceError, match="locked"):
        remove_workspace(
            context,
            checkout_recovery=lambda *_args: recovery_calls.append(True) or True,
        )
    assert recovery_calls == []
    assert context.worktree_path.is_dir()
    assert context.record_path.is_file()


def test_locked_worktree_and_empty_readonly_directory_do_not_escalate(
    repository, tmp_path
):
    context = create_workspace(
        repository, tmp_path / "checkout", branch="test/locked-empty-readonly",
        purpose="test",
    )
    empty = context.worktree_path / "empty-readonly"
    empty.mkdir()
    empty.chmod(0o500)
    _git(repository, "worktree", "lock", str(context.worktree_path))
    recovery_calls = []

    with pytest.raises(WorkspaceError, match="locked"):
        remove_workspace(
            context,
            checkout_recovery=lambda *_args: recovery_calls.append(True) or True,
        )

    assert recovery_calls == []
    assert empty.is_dir()
    assert context.worktree_path.is_dir()
    assert context.record_path.is_file()


def test_permission_recovery_refuses_replaced_checkout_before_residual_delete(
    repository, tmp_path, monkeypatch
):
    context = create_workspace(
        repository, tmp_path / "checkout", branch="test/replaced-recovery",
        purpose="test",
    )
    record = core.read_record(context.record_path)
    admin, _admin_identity, _gitdir_link = core._workspace_admin_snapshot(record)
    moved = tmp_path / "original-checkout"
    original_run = core.subprocess.run
    calls = []

    def unregister_and_fail(argv, *args, **kwargs):
        if argv[:3] == ["git", "worktree", "remove"] and not calls:
            calls.append(argv)
            shutil.rmtree(admin)
            return subprocess.CompletedProcess(argv, 1, "", "Permission denied")
        return original_run(argv, *args, **kwargs)

    monkeypatch.setattr(core.subprocess, "run", unregister_and_fail)

    def replace_checkout(received_context, _git_admin_dir, _error):
        assert received_context == context
        received_context.worktree_path.rename(moved)
        received_context.worktree_path.mkdir()
        (received_context.worktree_path / "unrelated").write_text("keep", encoding="utf-8")
        return True

    with pytest.raises(WorkspaceError, match="residual checkout path was replaced"):
        remove_workspace(context, checkout_recovery=replace_checkout)

    assert moved.is_dir()
    assert (context.worktree_path / "unrelated").read_text(encoding="utf-8") == "keep"
    assert context.record_path.is_file()


def test_remove_workspace_passes_residual_guard_before_local_recovery_delete(
    repository, tmp_path, monkeypatch
):
    context = create_workspace(
        repository, tmp_path / "checkout", branch="test/residual-guard-plumbing",
        purpose="test",
    )
    record = core.read_record(context.record_path)
    admin, _identity, _reverse = core._workspace_admin_snapshot(record)
    original_run = core.subprocess.run
    attempts = []

    def unregister_and_fail(argv, *args, **kwargs):
        if argv[:3] == ["git", "worktree", "remove"] and not attempts:
            attempts.append(argv)
            shutil.rmtree(admin)
            return subprocess.CompletedProcess(
                argv, 1, stdout="", stderr="Permission denied"
            )
        return original_run(argv, *args, **kwargs)

    monkeypatch.setattr(core.subprocess, "run", unregister_and_fail)
    guard_calls = []

    def guard(path):
        guard_calls.append(path)
        raise WorkspaceError("new adapter state", category="cleanup-refusal")

    with pytest.raises(WorkspaceError, match="permission-specific checkout retry failed.*new adapter state"):
        remove_workspace(
            context,
            checkout_recovery=lambda *_args: True,
            residual_checkout_guard=guard,
        )

    assert attempts
    assert guard_calls == [context.worktree_path]
    assert context.worktree_path.is_dir()
    assert context.record_path.is_file()


def test_finish_workspace_removal_repairs_only_captured_prunable_registration(
    repository, tmp_path
):
    context = create_workspace(
        repository, tmp_path / "checkout", branch="test/finish-removal",
        purpose="test",
    )
    record = core.read_record(context.record_path)
    admin, admin_identity, gitdir_link = core._workspace_admin_snapshot(record)
    expected_tip = _git(repository, "rev-parse", "refs/heads/test/finish-removal")
    _git(repository, "worktree", "remove", "--force", str(context.worktree_path))

    phases = []
    finish_workspace_removal(
        record,
        git_admin_dir=admin,
        expected_admin_identity=admin_identity,
        expected_gitdir_link=gitdir_link,
        expected_branch_tip=expected_tip,
        on_phase=lambda phase, status: phases.append((phase, status)),
    )

    assert not context.record_path.exists()
    assert not _branch_exists(repository, "test/finish-removal")
    assert phases == [
        ("git_registration", "running"),
        ("git_registration", "complete"),
        ("shared_record_removal", "running"),
        ("shared_record_removal", "complete"),
        ("branch_removal", "running"),
        ("branch_removal", "deleted"),
    ]


def test_finish_workspace_removal_tolerates_record_disappearing_after_validation(
    repository, tmp_path
):
    context = create_workspace(
        repository, tmp_path / "checkout", branch="test/finish-record-race",
        purpose="test",
    )
    record = core.read_record(context.record_path)
    admin, identity, reverse = core._workspace_admin_snapshot(record)
    _git(repository, "worktree", "remove", "--force", str(context.worktree_path))
    phases = []

    def remove_record_during_notification(phase, status):
        phases.append((phase, status))
        if (phase, status) == ("shared_record_removal", "running"):
            record.record_path.unlink()

    finish_workspace_removal(
        record,
        git_admin_dir=admin,
        expected_admin_identity=identity,
        expected_gitdir_link=reverse,
        on_phase=remove_record_during_notification,
    )

    assert not record.record_path.exists()
    assert not _branch_exists(repository, record.branch)
    assert phases[-2:] == [
        ("branch_removal", "running"),
        ("branch_removal", "deleted"),
    ]


def test_finish_workspace_removal_refuses_live_checkout_and_changed_admin(
    repository, tmp_path
):
    context = create_workspace(
        repository, tmp_path / "checkout", branch="test/finish-refusal",
        purpose="test",
    )
    record = core.read_record(context.record_path)
    admin, admin_identity, gitdir_link = core._workspace_admin_snapshot(record)

    with pytest.raises(WorkspaceError, match="checkout to be absent"):
        finish_workspace_removal(
            record, git_admin_dir=admin,
            expected_admin_identity=admin_identity,
            expected_gitdir_link=gitdir_link,
        )

    shutil.rmtree(context.worktree_path)
    with pytest.raises(WorkspaceError, match="was replaced"):
        finish_workspace_removal(
            record, git_admin_dir=admin,
            expected_admin_identity=(admin_identity[0], admin_identity[1] + 1),
            expected_gitdir_link=gitdir_link,
        )
    assert context.record_path.is_file()


@pytest.mark.parametrize(
    "field", ["worktree_path", "git_common_dir"],
    ids=["checkout-path", "git-common-directory"],
)
def test_finish_workspace_removal_reports_each_changed_record_identity_field(
    repository, tmp_path, monkeypatch, field
):
    context = create_workspace(
        repository, tmp_path / "checkout", branch=f"test/finish-stale-{field}",
        purpose="test",
    )
    record = core.read_record(context.record_path)
    admin, identity, reverse = core._workspace_admin_snapshot(record)
    shutil.rmtree(context.worktree_path)
    replacement = (
        record.worktree_path.with_name("different-checkout")
        if field == "worktree_path" else
        record.git_common_dir.with_name("different-common-directory")
    )
    monkeypatch.setattr(
        core, "read_record", lambda _path: replace(record, **{field: replacement})
    )

    with pytest.raises(WorkspaceError) as raised:
        finish_workspace_removal(
            record,
            git_admin_dir=admin,
            expected_admin_identity=identity,
            expected_gitdir_link=reverse,
        )

    assert raised.value.category == "stale-record"
    assert str(raised.value) == "workspace record changed before removal recovery"
    assert context.record_path.is_file()
    assert admin.is_dir()


def test_permission_admin_snapshot_refuses_invalid_paths_and_links(
    repository, tmp_path, monkeypatch
):
    context = create_workspace(
        repository, tmp_path / "checkout", branch="test/admin-snapshot-errors",
        purpose="test",
    )
    record = core.read_record(context.record_path)
    admin_root = record.git_common_dir / "worktrees"
    outside = tmp_path / "outside-admin"
    outside.mkdir()
    real_git = core._git

    monkeypatch.setattr(core, "_git", lambda *_args: str(outside))
    with pytest.raises(WorkspaceError, match="outside"):
        core._workspace_admin_snapshot(record)

    non_directory = admin_root / "not-a-directory"
    non_directory.write_text("file", encoding="utf-8")
    monkeypatch.setattr(core, "_git", lambda *_args: str(non_directory))
    with pytest.raises(WorkspaceError, match="not a directory"):
        core._workspace_admin_snapshot(record)

    missing = admin_root / "missing-admin"
    monkeypatch.setattr(core, "_git", lambda *_args: str(missing))
    with pytest.raises(WorkspaceError, match="could not capture"):
        core._workspace_admin_snapshot(record)

    monkeypatch.setattr(core, "_git", real_git)
    admin, _identity, _link = core._workspace_admin_snapshot(record)
    (admin / "gitdir").write_text(str(tmp_path / "wrong"), encoding="utf-8")
    monkeypatch.setattr(core, "_git", lambda *_args: str(admin))
    with pytest.raises(WorkspaceError, match="does not identify"):
        core._workspace_admin_snapshot(record)


def test_permission_admin_snapshot_accepts_relative_reverse_link(
    repository, tmp_path, monkeypatch
):
    context = create_workspace(
        repository, tmp_path / "checkout", branch="test/admin-relative-link",
        purpose="test",
    )
    record = core.read_record(context.record_path)
    admin = core.canonical_path(
        Path(_git(context.worktree_path, "rev-parse", "--absolute-git-dir"))
    )
    # Keep Git's valid inventory before injecting a relative reverse link.
    # Git 2.48 added relative-worktree storage; older Git marks a hand-written
    # relative gitdir link prunable and echoes that unresolved value from
    # `worktree list`. This test exercises snapshot path resolution, not that
    # version-specific inventory behavior.
    inventory = core.list_git_worktrees(record.source_git_root)
    assert any(entry.path == context.worktree_path for entry in inventory)
    relative = os.path.relpath(context.worktree_path / ".git", admin)
    (admin / "gitdir").write_text(relative, encoding="utf-8")

    received_admin, identity, received_link = core._workspace_admin_snapshot(record)

    assert received_admin == admin
    assert identity == (admin.lstat().st_dev, admin.lstat().st_ino)
    assert received_link == relative
    monkeypatch.setattr(core, "list_git_worktrees", lambda _path: inventory)
    core._verify_workspace_admin_snapshot(record, admin, identity, relative)


def test_verify_permission_admin_snapshot_refusal_edges(
    repository, tmp_path, monkeypatch
):
    context = create_workspace(
        repository, tmp_path / "checkout", branch="test/admin-verify-errors",
        purpose="test",
    )
    record = core.read_record(context.record_path)
    admin, identity, reverse = core._workspace_admin_snapshot(record)

    with pytest.raises(WorkspaceError, match="moved outside"):
        core._verify_workspace_admin_snapshot(
            record, tmp_path / "outside-admin", identity, reverse
        )

    with pytest.raises(WorkspaceError, match="was replaced"):
        core._verify_workspace_admin_snapshot(
            record, admin, (identity[0], identity[1] + 1), reverse
        )

    (admin / "gitdir").write_text(str(tmp_path / "wrong"), encoding="utf-8")
    with pytest.raises(WorkspaceError, match="reverse link changed"):
        core._verify_workspace_admin_snapshot(record, admin, identity, reverse)
    (admin / "gitdir").write_text(reverse, encoding="utf-8")

    monkeypatch.setattr(
        core, "list_git_worktrees",
        lambda *_args: [SimpleNamespace(path=record.worktree_path, branch="other")],
    )
    with pytest.raises(WorkspaceError, match="changed branch"):
        core._verify_workspace_admin_snapshot(record, admin, identity, reverse)


def test_verify_permission_admin_snapshot_accepts_missing_admin_and_rejects_unreadable(
    repository, tmp_path
):
    context = create_workspace(
        repository, tmp_path / "checkout", branch="test/admin-verify-missing",
        purpose="test",
    )
    record = core.read_record(context.record_path)
    admin, identity, reverse = core._workspace_admin_snapshot(record)
    (admin / "gitdir").unlink()
    with pytest.raises(WorkspaceError, match="could not recheck"):
        core._verify_workspace_admin_snapshot(record, admin, identity, reverse)
    shutil.rmtree(admin)
    core._verify_workspace_admin_snapshot(record, admin, identity, reverse)


@pytest.mark.parametrize(
    "message",
    ["Permission denied", "Operation not permitted", "EACCES", "EPERM"],
)
def test_permission_denial_matches_only_explicit_permission_markers(message):
    assert core._permission_denial(message)
    assert not core._permission_denial("worktree is locked")


def test_path_present_distinguishes_missing_from_existing(tmp_path):
    existing = tmp_path / "existing"
    existing.write_text("data", encoding="utf-8")
    assert core._path_present(existing)
    assert not core._path_present(tmp_path / "missing")


def test_finish_permission_recovery_refuses_duplicate_and_replaced_targets(
    repository, tmp_path, monkeypatch
):
    context = create_workspace(
        repository, tmp_path / "checkout", branch="test/finish-duplicate",
        purpose="test",
    )
    record = core.read_record(context.record_path)
    admin, identity, reverse = core._workspace_admin_snapshot(record)
    entry = SimpleNamespace(path=context.worktree_path)
    monkeypatch.setattr(core, "_verify_workspace_admin_snapshot", lambda *_args: None)
    monkeypatch.setattr(core, "list_git_worktrees", lambda *_args: [entry, entry])
    with pytest.raises(WorkspaceError, match="multiple Git registrations"):
        core._finish_permission_recovery(record, (admin, identity, reverse), identity)

    monkeypatch.setattr(core, "list_git_worktrees", lambda *_args: [entry])
    with pytest.raises(WorkspaceError, match="checkout path was replaced"):
        core._finish_permission_recovery(
            record, (admin, identity, reverse), (identity[0], identity[1] + 1)
        )


def test_finish_permission_recovery_reports_failed_git_retry(
    repository, tmp_path, monkeypatch
):
    context = create_workspace(
        repository, tmp_path / "checkout", branch="test/finish-git-failure",
        purpose="test",
    )
    record = core.read_record(context.record_path)
    admin, identity, reverse = core._workspace_admin_snapshot(record)
    monkeypatch.setattr(core, "_verify_workspace_admin_snapshot", lambda *_args: None)

    registration_calls = 0

    def current_then_removed(*_args):
        nonlocal registration_calls
        registration_calls += 1
        return (
            [SimpleNamespace(path=context.worktree_path)]
            if registration_calls == 1 else []
        )

    monkeypatch.setattr(
        core, "list_git_worktrees", current_then_removed,
    )
    retry_calls = []

    def fail_retry(argv, **kwargs):
        retry_calls.append((argv, kwargs))
        stdout = "git output" if kwargs.get("text") else b"git output"
        stderr = "still protected" if kwargs.get("text") else b"still protected"
        if not kwargs.get("capture_output"):
            stdout = stderr = None
        if kwargs.get("check"):
            raise subprocess.CalledProcessError(
                1, argv, output=stdout, stderr=stderr,
            )
        return subprocess.CompletedProcess(argv, 1, stdout=stdout, stderr=stderr)

    monkeypatch.setattr(core.subprocess, "run", fail_retry)
    checkout_info = context.worktree_path.lstat()
    checkout_identity = (checkout_info.st_dev, checkout_info.st_ino)

    with pytest.raises(WorkspaceError) as raised:
        core._finish_permission_recovery(
            record, (admin, identity, reverse), checkout_identity
        )

    assert raised.value.category == "checkout-removal"
    assert str(raised.value) == (
        f"unprivileged Git retry still cannot remove {context.worktree_path} "
        "(1): still protected"
    )
    assert retry_calls == [(
        ["git", "worktree", "remove", "--force", str(context.worktree_path)],
        {
            "cwd": repository,
            "text": True,
            "capture_output": True,
            "check": False,
        },
    )]


def test_finish_permission_recovery_removes_residual_checkout_and_admin(
    repository, tmp_path, monkeypatch
):
    context = create_workspace(
        repository, tmp_path / "checkout", branch="test/finish-residual",
        purpose="test",
    )
    record = core.read_record(context.record_path)
    admin, identity, reverse = core._workspace_admin_snapshot(record)
    monkeypatch.setattr(core, "list_git_worktrees", lambda *_args: [])

    core._finish_permission_recovery(
        record, (admin, identity, reverse),
        (context.worktree_path.lstat().st_dev, context.worktree_path.lstat().st_ino),
    )

    assert not context.worktree_path.exists()
    assert not admin.exists()
    assert context.record_path.exists()


def test_finish_permission_recovery_runs_residual_guard_before_unprivileged_rmtree(
    repository, tmp_path, monkeypatch
):
    context = create_workspace(
        repository, tmp_path / "checkout", branch="test/finish-residual-guard",
        purpose="test",
    )
    record = core.read_record(context.record_path)
    admin, identity, reverse = core._workspace_admin_snapshot(record)
    monkeypatch.setattr(core, "list_git_worktrees", lambda *_args: [])
    rmtree_calls = []
    monkeypatch.setattr(
        core.shutil, "rmtree", lambda path, *args, **kwargs: rmtree_calls.append(path)
    )
    guard_calls = []

    def refuse_residual(path):
        guard_calls.append(path)
        raise WorkspaceError(
            "leftover checkout changed after checkpoint", category="cleanup-refusal"
        )

    with pytest.raises(WorkspaceError, match="changed after checkpoint"):
        core._finish_permission_recovery(
            record, (admin, identity, reverse),
            (context.worktree_path.lstat().st_dev, context.worktree_path.lstat().st_ino),
            refuse_residual,
        )

    assert guard_calls == [context.worktree_path]
    assert rmtree_calls == []
    assert context.worktree_path.is_dir()


@pytest.mark.parametrize(
    ("registered", "error_kind"),
    [(False, ValueError), (True, ValueError), (True, WorkspaceError)],
)
def test_finish_permission_recovery_wraps_residual_guard_errors(
    repository, tmp_path, monkeypatch, registered, error_kind
):
    context = create_workspace(
        repository, tmp_path / "checkout", branch=f"test/finish-guard-error-{registered}",
        purpose="test",
    )
    record = core.read_record(context.record_path)
    admin, identity, reverse = core._workspace_admin_snapshot(record)
    entry = SimpleNamespace(path=context.worktree_path)
    monkeypatch.setattr(core, "_verify_workspace_admin_snapshot", lambda *_args: None)
    monkeypatch.setattr(
        core, "list_git_worktrees", lambda *_args: [entry] if registered else []
    )

    original_error = (
        WorkspaceError("original refusal", category="cleanup-refusal")
        if error_kind is WorkspaceError else ValueError("adapter guard failure")
    )

    def fail_guard(_path):
        raise original_error

    expected = "original refusal" if error_kind is WorkspaceError else "adapter guard failure"
    with pytest.raises(WorkspaceError, match=expected) as raised:
        core._finish_permission_recovery(
            record,
            (admin, identity, reverse),
            (context.worktree_path.lstat().st_dev, context.worktree_path.lstat().st_ino),
            fail_guard,
        )

    assert raised.value is original_error if error_kind is WorkspaceError else raised.value.category == "cleanup-refusal"
    assert context.worktree_path.is_dir()


@pytest.mark.parametrize("rmtree_outcome", ["error", "no-op"])
def test_finish_permission_recovery_reports_residual_checkout_failures(
    repository, tmp_path, monkeypatch, rmtree_outcome
):
    context = create_workspace(
        repository, tmp_path / "checkout", branch=f"test/finish-residual-{rmtree_outcome}",
        purpose="test",
    )
    record = core.read_record(context.record_path)
    admin, identity, reverse = core._workspace_admin_snapshot(record)
    monkeypatch.setattr(core, "list_git_worktrees", lambda *_args: [])
    real_rmtree = shutil.rmtree

    def fake_rmtree(path, *args, **kwargs):
        if Path(path) == context.worktree_path:
            if rmtree_outcome == "error":
                raise OSError("blocked residual")
            return None
        return real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(core.shutil, "rmtree", fake_rmtree)
    expected = "could not finish removing residual" if rmtree_outcome == "error" else "checkout remains"
    with pytest.raises(WorkspaceError, match=expected):
        core._finish_permission_recovery(
            record, (admin, identity, reverse),
            (context.worktree_path.lstat().st_dev, context.worktree_path.lstat().st_ino),
        )
    assert context.worktree_path.exists()
    assert context.record_path.exists()


def test_finish_permission_recovery_reports_admin_and_remaining_registration_failures(
    repository, tmp_path, monkeypatch
):
    context = create_workspace(
        repository, tmp_path / "checkout", branch="test/finish-admin-failure",
        purpose="test",
    )
    record = core.read_record(context.record_path)
    admin, identity, reverse = core._workspace_admin_snapshot(record)
    real_rmtree = shutil.rmtree
    monkeypatch.setattr(core, "list_git_worktrees", lambda *_args: [])

    def fail_admin(path, *args, **kwargs):
        if Path(path) == admin:
            raise OSError("admin removal denied")
        return real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(core.shutil, "rmtree", fail_admin)
    with pytest.raises(WorkspaceError, match="could not remove exact Git administration"):
        core._finish_permission_recovery(
            record, (admin, identity, reverse),
            (context.worktree_path.lstat().st_dev, context.worktree_path.lstat().st_ino),
        )
    monkeypatch.setattr(core.shutil, "rmtree", real_rmtree)

    context = create_workspace(
        repository, tmp_path / "second-checkout", branch="test/finish-registration-remains",
        purpose="test",
    )
    record = core.read_record(context.record_path)
    admin, identity, reverse = core._workspace_admin_snapshot(record)
    calls = 0

    def registration_returns_late(*_args):
        nonlocal calls
        calls += 1
        return [] if calls < 4 else [SimpleNamespace(path=context.worktree_path)]

    monkeypatch.setattr(core, "list_git_worktrees", registration_returns_late)
    with pytest.raises(WorkspaceError, match="still registers removed checkout"):
        core._finish_permission_recovery(
            record, (admin, identity, reverse),
            (context.worktree_path.lstat().st_dev, context.worktree_path.lstat().st_ino),
        )


def test_finish_permission_recovery_handles_missing_registered_and_unregistered_paths(
    repository, tmp_path, monkeypatch
):
    context = create_workspace(
        repository, tmp_path / "checkout", branch="test/finish-missing-target",
        purpose="test",
    )
    record = core.read_record(context.record_path)
    admin, identity, reverse = core._workspace_admin_snapshot(record)
    monkeypatch.setattr(core, "_verify_workspace_admin_snapshot", lambda *_args: None)

    registration_calls = 0

    def current_then_removed(*_args):
        nonlocal registration_calls
        registration_calls += 1
        return (
            [SimpleNamespace(path=context.worktree_path)]
            if registration_calls == 1 else []
        )

    monkeypatch.setattr(
        core, "list_git_worktrees", current_then_removed,
    )
    monkeypatch.setattr(core, "_path_present", lambda _path: False)
    monkeypatch.setattr(
        core.subprocess, "run",
        lambda argv, **_kwargs: subprocess.CompletedProcess(argv, 0, "", ""),
    )
    core._finish_permission_recovery(record, (admin, identity, reverse), (1, 2))

    monkeypatch.setattr(core, "list_git_worktrees", lambda *_args: [])
    core._finish_permission_recovery(record, (admin, identity, reverse), (1, 2))


def test_verify_permission_admin_snapshot_handles_other_checkout_links(
    repository, tmp_path, monkeypatch
):
    context = create_workspace(
        repository, tmp_path / "checkout", branch="test/admin-other-link",
        purpose="test",
    )
    record = core.read_record(context.record_path)
    admin, identity, reverse = core._workspace_admin_snapshot(record)
    other = tmp_path / "other"
    other.mkdir()
    (other / ".git").write_text("not a git link", encoding="utf-8")
    monkeypatch.setattr(
        core, "list_git_worktrees",
        lambda *_args: [SimpleNamespace(path=other, branch="other")],
    )
    core._verify_workspace_admin_snapshot(record, admin, identity, reverse)

    relative = os.path.relpath(admin, other)
    (other / ".git").write_text(f"gitdir: {relative}\n", encoding="utf-8")
    with pytest.raises(WorkspaceError, match="owns Git administration"):
        core._verify_workspace_admin_snapshot(record, admin, identity, reverse)

    (other / ".git").write_text(f"gitdir: {tmp_path / 'elsewhere'}\n", encoding="utf-8")
    core._verify_workspace_admin_snapshot(record, admin, identity, reverse)

    (other / ".git").unlink()
    core._verify_workspace_admin_snapshot(record, admin, identity, reverse)


@pytest.mark.parametrize("registration_result", ["error", "still-present"])
@pytest.mark.parametrize("observe_phases", [True, False])
def test_remove_workspace_reports_git_registration_verification_failures(
    repository, tmp_path, monkeypatch, registration_result, observe_phases
):
    context = create_workspace(
        repository, tmp_path / "checkout", branch=f"test/registration-{registration_result}",
        purpose="test",
    )
    phases = []
    if registration_result == "error":
        monkeypatch.setattr(
            core, "list_git_worktrees",
            lambda *_args: (_ for _ in ()).throw(WorkspaceError("list unavailable")),
        )
        expected = "list unavailable"
    else:
        monkeypatch.setattr(
            core, "list_git_worktrees",
            lambda *_args: [SimpleNamespace(path=context.worktree_path)],
        )
        expected = "still registers removed checkout"

    with pytest.raises(WorkspaceError, match=expected):
        remove_workspace(
            context,
            on_phase=(lambda phase, status: phases.append((phase, status)))
            if observe_phases else None,
        )
    if observe_phases:
        assert phases[-2:] == [
            ("git_registration", "running"),
            ("git_registration", "failed"),
        ]
    else:
        assert phases == []
    assert context.record_path.is_file()


@pytest.mark.parametrize("observe_phases", [True, False])
def test_remove_workspace_reports_record_unlink_failure(
    repository, tmp_path, monkeypatch, observe_phases
):
    context = create_workspace(
        repository, tmp_path / "checkout", branch="test/record-unlink-failure", purpose="test"
    )
    phases = []
    original_unlink = Path.unlink

    def fail_record_unlink(path, *args, **kwargs):
        if path == context.record_path:
            raise PermissionError("record locked")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_record_unlink)
    with pytest.raises(PermissionError, match="record locked"):
        remove_workspace(
            context,
            on_phase=(lambda phase, status: phases.append((phase, status)))
            if observe_phases else None,
        )
    if observe_phases:
        assert phases[-2:] == [
            ("shared_record_removal", "running"),
            ("shared_record_removal", "failed"),
        ]
    else:
        assert phases == []


def test_remove_preflights_git_identity_before_adapter_cleanup(repository, tmp_path, monkeypatch):
    context = create_workspace(
        repository, tmp_path / "checkout", branch="test/remove-check", purpose="test"
    )
    _git(repository, "branch", "unrelated/protected")
    record = core.read_record(context.record_path)
    tampered = core.replace(record, branch="unrelated/protected")
    core.write_record(tampered)
    cleanup_calls = []

    with pytest.raises(WorkspaceError, match="does not match record|refusing cleanup"):
        remove_workspace(
            tampered,
            cleanup=lambda _context: cleanup_calls.append("ran"),
            force=True,
        )

    assert cleanup_calls == []
    assert context.worktree_path.is_dir()
    assert _git(repository, "branch", "--list", "unrelated/protected") == "unrelated/protected"
    core.write_record(record)

    def matching(path):
        if Path(path) == record.source_git_root:
            return record.source_git_root, record.git_common_dir, "main", "head"
        return context.worktree_path, record.git_common_dir, record.branch, "head"

    for field, value in (("common", tmp_path / "other.git"), ("source_common", tmp_path / "source.git")):
        def mismatch(path, field=field, value=value):
            if Path(path) == record.source_git_root:
                common = value if field == "source_common" else record.git_common_dir
                return record.source_git_root, common, "main", "head"
            common = value if field == "common" else record.git_common_dir
            return context.worktree_path, common, record.branch, "head"

        monkeypatch.setattr(core, "discover_git_context", mismatch)
        with pytest.raises(WorkspaceError, match="does not match record"):
            remove_workspace(record, force=True)
    monkeypatch.setattr(core, "discover_git_context", matching)
    remove_workspace(context, force=True)


def test_cleanup_git_and_branch_failures_leave_state(repository, tmp_path, monkeypatch):
    context = create_workspace(repository, tmp_path / "checkout", branch="test/fail-clean", purpose="test")
    record = core.read_record(context.record_path)
    original = core.subprocess.run

    def worktree_failure(argv, **kwargs):
        assert kwargs["text"] is True
        assert kwargs["capture_output"] is True
        assert kwargs["check"] is False
        if argv[1:3] == ["worktree", "remove"]:
            return subprocess.CompletedProcess(argv, 1, stdout="remove stdout", stderr="")
        return original(argv, **kwargs)

    monkeypatch.setattr(core.subprocess, "run", worktree_failure)
    with pytest.raises(WorkspaceError, match="remove stdout"):
        remove_workspace(record)
    assert context.record_path.exists()
    monkeypatch.undo()

    def branch_failure(argv, **kwargs):
        if argv[1:3] == ["worktree", "list"]:
            assert kwargs["capture_output"] is True
            assert kwargs["check"] is False
            return original(argv, **kwargs)
        assert kwargs["text"] is True
        assert kwargs["capture_output"] is True
        assert kwargs["check"] is False
        if argv[1:3] == ["worktree", "remove"]:
            return original(argv, **kwargs)
        if argv[1:3] == ["branch", "-D"]:
            return subprocess.CompletedProcess(argv, 1, stdout="branch stdout", stderr="")
        return original(argv, **kwargs)

    monkeypatch.setattr(core.subprocess, "run", branch_failure)
    with pytest.raises(WorkspaceError, match="branch stdout"):
        remove_workspace(record)
    assert not context.record_path.exists()
    assert not context.worktree_path.exists()
    monkeypatch.undo()
    assert _git(repository, "show-ref", "--verify", "refs/heads/test/fail-clean")
    # The branch ref remains available, and no workspace record is stranded.


def test_acquire_lease_rejects_invalid_requests_and_conflicting_holder(repository, tmp_path):
    context = create_workspace(repository, tmp_path / "checkout", branch="test/acquire", purpose="test")
    record = core.read_record(context.record_path)
    for kwargs in ({}, {"ttl": timedelta(minutes=1), "perpetual": True}, {"ttl": timedelta(0)}):
        with pytest.raises(WorkspaceError, match="choose exactly one|positive"):
            acquire_lease(record, holder="owner", **kwargs)
    held = acquire_lease(record, holder="owner", ttl=timedelta(minutes=5))
    with pytest.raises(WorkspaceError, match="already has an active lease"):
        acquire_lease(held, holder="other", ttl=timedelta(minutes=5))
    remove_workspace(held, force=True)


def test_remove_workspace_reports_checkout_identity_capture_failures(
    repository, tmp_path, monkeypatch
):
    context = create_workspace(
        repository, tmp_path / "checkout", branch="test/checkout-lstat-error",
        purpose="test",
    )
    real_lstat = Path.lstat

    def denied_lstat(path):
        if path == context.worktree_path:
            raise PermissionError("identity denied")
        return real_lstat(path)

    monkeypatch.setattr(Path, "lstat", denied_lstat)
    with pytest.raises(WorkspaceError, match="capture checkout identity.*identity denied"):
        remove_workspace(context)


def test_remove_workspace_rejects_non_directory_checkout_identity(
    repository, tmp_path, monkeypatch
):
    context = create_workspace(
        repository, tmp_path / "checkout", branch="test/checkout-not-directory",
        purpose="test",
    )
    real_lstat = Path.lstat

    def report_file(path):
        if path == context.worktree_path:
            return SimpleNamespace(
                st_mode=stat.S_IFREG | 0o600, st_dev=1, st_ino=2,
            )
        return real_lstat(path)

    monkeypatch.setattr(Path, "lstat", report_file)
    with pytest.raises(WorkspaceError, match="not a real directory"):
        remove_workspace(context)


def test_remove_workspace_reports_failed_recovery_phase(
    repository, tmp_path, monkeypatch
):
    context = create_workspace(
        repository, tmp_path / "checkout", branch="test/recovery-phase-failure",
        purpose="test",
    )
    record = core.read_record(context.record_path)
    admin, _identity, _reverse = core._workspace_admin_snapshot(record)
    original_run = core.subprocess.run
    calls = []

    def unregister_and_fail(argv, *args, **kwargs):
        if argv[:3] == ["git", "worktree", "remove"] and not calls:
            calls.append(argv)
            shutil.rmtree(admin)
            return subprocess.CompletedProcess(argv, 1, "", "Permission denied")
        return original_run(argv, *args, **kwargs)

    monkeypatch.setattr(core.subprocess, "run", unregister_and_fail)
    phases = []

    def replace_checkout(received_context, *_args):
        shutil.rmtree(received_context.worktree_path)
        received_context.worktree_path.mkdir()
        return True

    with pytest.raises(WorkspaceError, match="residual checkout path was replaced"):
        remove_workspace(
            context, checkout_recovery=replace_checkout,
            on_phase=lambda phase, status: phases.append((phase, status)),
        )
    assert phases[-1] == ("checkout_deletion", "failed")


@pytest.mark.parametrize("relative_link", [True, False])
def test_finish_workspace_removal_cleans_captured_admin_and_gitdir_link(
    repository, tmp_path, monkeypatch, relative_link
):
    context = create_workspace(
        repository, tmp_path / "checkout", branch="test/finish-admin-present",
        purpose="test",
    )
    record = core.read_record(context.record_path)
    admin, info, _reverse = core._workspace_admin_snapshot(record)
    expected_link = (
        os.path.relpath(context.worktree_path / ".git", admin)
        if relative_link else str(context.worktree_path / ".git")
    )
    (admin / "gitdir").write_text(expected_link, encoding="utf-8")
    shutil.rmtree(context.worktree_path)
    monkeypatch.setattr(core, "list_git_worktrees", lambda *_args: [])
    phases = []

    finish_workspace_removal(
        record, git_admin_dir=admin,
        expected_admin_identity=info,
        expected_gitdir_link=expected_link,
        delete_branch=False,
        on_phase=lambda phase, status: phases.append((phase, status)),
    )

    assert not admin.exists()
    assert not context.record_path.exists()
    assert phases[-1] == ("branch_removal", "kept")


@pytest.mark.parametrize("failure", ["outside", "registration"])
def test_finish_workspace_removal_refuses_unverified_target_state(
    repository, tmp_path, monkeypatch, failure
):
    context = create_workspace(
        repository, tmp_path / "checkout", branch=f"test/finish-refuse-{failure}",
        purpose="test",
    )
    record = core.read_record(context.record_path)
    admin, info, reverse = core._workspace_admin_snapshot(record)
    shutil.rmtree(context.worktree_path)
    if failure == "registration":
        monkeypatch.setattr(
            core, "list_git_worktrees",
            lambda *_args: [SimpleNamespace(
                path=context.worktree_path, is_prunable=False, branch=record.branch,
            )],
        )
        admin_path = admin
        expected = "live or mismatched"
    else:
        admin_path = tmp_path / "outside"
        expected = "outside"
    with pytest.raises(WorkspaceError, match=expected):
        finish_workspace_removal(
            record, git_admin_dir=admin_path,
            expected_admin_identity=info, expected_gitdir_link=reverse,
        )


def test_finish_workspace_removal_refuses_source_record_and_active_lease(
    repository, tmp_path, monkeypatch
):
    context = create_workspace(
        repository, tmp_path / "checkout", branch="test/finish-source-error",
        purpose="test",
    )
    record = core.read_record(context.record_path)
    admin, info, reverse = core._workspace_admin_snapshot(record)
    shutil.rmtree(context.worktree_path)
    monkeypatch.setattr(
        core, "read_record", lambda _path: replace(record, branch="changed")
    )
    with pytest.raises(WorkspaceError, match="record changed"):
        finish_workspace_removal(
            record, git_admin_dir=admin,
            expected_admin_identity=info, expected_gitdir_link=reverse,
        )
    monkeypatch.setattr(core, "read_record", lambda _path: record)
    monkeypatch.setattr(
        core, "discover_git_root",
        lambda _path: (record.source_git_root, record.git_common_dir / "other"),
    )
    with pytest.raises(WorkspaceError, match="no longer belongs"):
        finish_workspace_removal(
            record, git_admin_dir=admin,
            expected_admin_identity=info, expected_gitdir_link=reverse,
        )

    monkeypatch.undo()
    context = create_workspace(
        repository, tmp_path / "lease-checkout", branch="test/finish-active-lease",
        purpose="test",
    )
    lease_record = acquire_lease(
        context, holder="test holder", ttl=timedelta(minutes=10),
    )
    lease_admin, lease_identity, lease_reverse = core._workspace_admin_snapshot(lease_record)
    _git(repository, "worktree", "remove", "--force", str(context.worktree_path))
    with pytest.raises(WorkspaceError, match="active lease"):
        finish_workspace_removal(
            lease_record, git_admin_dir=lease_admin,
            expected_admin_identity=lease_identity,
            expected_gitdir_link=lease_reverse,
        )


def test_finish_workspace_removal_reports_admin_and_record_failures(
    repository, tmp_path, monkeypatch
):
    context = create_workspace(
        repository, tmp_path / "checkout", branch="test/finish-admin-errors",
        purpose="test",
    )
    record = core.read_record(context.record_path)
    admin, identity, reverse = core._workspace_admin_snapshot(record)
    shutil.rmtree(context.worktree_path)
    monkeypatch.setattr(core, "list_git_worktrees", lambda *_args: [])
    real_rmtree = shutil.rmtree

    def fail_admin(path, *args, **kwargs):
        if Path(path) == admin:
            raise OSError("admin removal denied")
        return real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(core.shutil, "rmtree", fail_admin)
    phases = []
    with pytest.raises(WorkspaceError, match="could not remove captured Git administration"):
        finish_workspace_removal(
            record, git_admin_dir=admin,
            expected_admin_identity=identity, expected_gitdir_link=reverse,
            on_phase=lambda phase, status: phases.append((phase, status)),
        )
    assert phases[-1] == ("git_registration", "failed")
    with pytest.raises(WorkspaceError, match="could not remove captured Git administration"):
        finish_workspace_removal(
            record, git_admin_dir=admin,
            expected_admin_identity=identity, expected_gitdir_link=reverse,
        )
    monkeypatch.setattr(core.shutil, "rmtree", real_rmtree)
    real_unlink = Path.unlink

    def fail_record_unlink(path, *args, **kwargs):
        if path == record.record_path:
            raise PermissionError("record removal denied")
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_record_unlink)
    phases = []
    with pytest.raises(PermissionError, match="record removal denied"):
        finish_workspace_removal(
            record, git_admin_dir=admin,
            expected_admin_identity=identity, expected_gitdir_link=reverse,
            on_phase=lambda phase, status: phases.append((phase, status)),
        )
    assert phases[-1] == ("shared_record_removal", "failed")
    with pytest.raises(PermissionError, match="record removal denied"):
        finish_workspace_removal(
            record, git_admin_dir=admin,
            expected_admin_identity=identity, expected_gitdir_link=reverse,
        )


def test_finish_workspace_removal_reports_remaining_registration_and_branch_failure(
    repository, tmp_path, monkeypatch
):
    context = create_workspace(
        repository, tmp_path / "checkout", branch="test/finish-remaining-registration",
        purpose="test",
    )
    record = core.read_record(context.record_path)
    admin, identity, reverse = core._workspace_admin_snapshot(record)
    _git(repository, "worktree", "remove", "--force", str(context.worktree_path))
    calls = 0

    def registration_returns_late(*_args):
        nonlocal calls
        calls += 1
        return [] if calls == 1 else [SimpleNamespace(path=context.worktree_path)]

    monkeypatch.setattr(core, "list_git_worktrees", registration_returns_late)
    phases = []
    with pytest.raises(WorkspaceError, match="still registers removed checkout"):
        finish_workspace_removal(
            record, git_admin_dir=admin,
            expected_admin_identity=identity, expected_gitdir_link=reverse,
            on_phase=lambda phase, status: phases.append((phase, status)),
        )
    assert phases[-1] == ("git_registration", "failed")

    calls = 0

    def registration_stays(*_args):
        nonlocal calls
        calls += 1
        return [] if calls == 1 else [SimpleNamespace(
            path=context.worktree_path, is_prunable=True, branch=record.branch,
        )]

    monkeypatch.setattr(core, "list_git_worktrees", registration_stays)
    with pytest.raises(WorkspaceError, match="still registers removed checkout"):
        finish_workspace_removal(
            record, git_admin_dir=admin,
            expected_admin_identity=identity, expected_gitdir_link=reverse,
        )

    context = create_workspace(
        repository, tmp_path / "branch-checkout", branch="test/finish-branch-failure",
        purpose="test",
    )
    record = core.read_record(context.record_path)
    admin, identity, reverse = core._workspace_admin_snapshot(record)
    expected_tip = _git(repository, "rev-parse", f"refs/heads/{record.branch}")
    _git(repository, "worktree", "remove", "--force", str(context.worktree_path))
    monkeypatch.setattr(core, "list_git_worktrees", lambda *_args: [])
    original_run = core.subprocess.run
    branch_calls = []

    def fail_branch_delete(argv, *args, **kwargs):
        if argv[:3] == ["git", "update-ref", "-d"]:
            branch_calls.append((argv, kwargs))
            stdout = "git output" if kwargs.get("text") else b"git output"
            stderr = "branch changed" if kwargs.get("text") else b"branch changed"
            if not kwargs.get("capture_output"):
                stdout = stderr = None
            if kwargs.get("check"):
                raise subprocess.CalledProcessError(
                    1, argv, output=stdout, stderr=stderr,
                )
            return subprocess.CompletedProcess(argv, 1, stdout, stderr)
        return original_run(argv, *args, **kwargs)

    monkeypatch.setattr(core.subprocess, "run", fail_branch_delete)
    phases = []
    with pytest.raises(WorkspaceError) as raised:
        finish_workspace_removal(
            record, git_admin_dir=admin,
            expected_admin_identity=identity, expected_gitdir_link=reverse,
            expected_branch_tip=expected_tip,
            on_phase=lambda phase, status: phases.append((phase, status)),
        )
    assert raised.value.category == "cleanup-refusal"
    assert str(raised.value) == (
        f"branch removal for {record.branch} failed (1): branch changed"
    )
    assert branch_calls == [(
        ["git", "update-ref", "-d", f"refs/heads/{record.branch}", expected_tip],
        {
            "cwd": repository,
            "text": True,
            "capture_output": True,
            "check": False,
        },
    )]
    assert phases[-1] == ("branch_removal", "failed")


def test_finish_workspace_removal_handles_admin_link_refusals(
    repository, tmp_path, monkeypatch
):
    context = create_workspace(
        repository, tmp_path / "checkout", branch="test/finish-admin-link-errors",
        purpose="test",
    )
    record = core.read_record(context.record_path)
    admin, identity, reverse = core._workspace_admin_snapshot(record)
    shutil.rmtree(context.worktree_path)
    monkeypatch.setattr(core, "list_git_worktrees", lambda *_args: [])

    (admin / "gitdir").unlink()
    with pytest.raises(WorkspaceError, match="could not verify Git administration reverse link"):
        finish_workspace_removal(
            record, git_admin_dir=admin,
            expected_admin_identity=identity, expected_gitdir_link=reverse,
        )
    (admin / "gitdir").write_text(str(tmp_path / "wrong"), encoding="utf-8")
    with pytest.raises(WorkspaceError, match="reverse link changed"):
        finish_workspace_removal(
            record, git_admin_dir=admin,
            expected_admin_identity=identity, expected_gitdir_link=reverse,
        )


@pytest.mark.parametrize(
    "git_link", ["unrelated", "captured-relative", "missing-file", "plain-file"]
)
def test_finish_workspace_removal_checks_registered_checkout_gitlinks(
    repository, tmp_path, monkeypatch, git_link
):
    context = create_workspace(
        repository, tmp_path / "checkout", branch=f"test/finish-other-{git_link}",
        purpose="test",
    )
    record = core.read_record(context.record_path)
    admin, identity, reverse = core._workspace_admin_snapshot(record)
    shutil.rmtree(context.worktree_path)
    other = tmp_path / "other"
    other.mkdir()
    other_git = other / ".git"
    if git_link == "missing-file":
        expected = ""
    elif git_link == "plain-file":
        other_git.write_text("not a git link", encoding="utf-8")
        expected = ""
    elif git_link == "captured-relative":
        other_git.write_text(
            f"gitdir: {os.path.relpath(admin, other)}\n", encoding="utf-8"
        )
        expected = "registered checkout"
    else:
        other_git.write_text(f"gitdir: {tmp_path / 'elsewhere'}\n", encoding="utf-8")
        expected = ""
    monkeypatch.setattr(
        core, "list_git_worktrees",
        lambda *_args: [SimpleNamespace(path=other)],
    )
    if expected:
        with pytest.raises(WorkspaceError, match="owns captured Git administration"):
            finish_workspace_removal(
                record, git_admin_dir=admin,
                expected_admin_identity=identity, expected_gitdir_link=reverse,
            )
    else:
        finish_workspace_removal(
            record, git_admin_dir=admin,
            expected_admin_identity=identity, expected_gitdir_link=reverse,
            delete_branch=False,
        )


def test_finish_workspace_removal_reports_admin_still_present_and_no_phase_observer(
    repository, tmp_path, monkeypatch
):
    context = create_workspace(
        repository, tmp_path / "checkout", branch="test/finish-admin-noop",
        purpose="test",
    )
    record = core.read_record(context.record_path)
    admin, identity, reverse = core._workspace_admin_snapshot(record)
    shutil.rmtree(context.worktree_path)
    monkeypatch.setattr(core, "list_git_worktrees", lambda *_args: [])
    real_rmtree = shutil.rmtree
    monkeypatch.setattr(core.shutil, "rmtree", lambda path, *args, **kwargs: None)
    with pytest.raises(WorkspaceError, match="administration directory remains"):
        finish_workspace_removal(
            record, git_admin_dir=admin,
            expected_admin_identity=identity, expected_gitdir_link=reverse,
        )
    phases = []
    with pytest.raises(WorkspaceError, match="administration directory remains"):
        finish_workspace_removal(
            record, git_admin_dir=admin,
            expected_admin_identity=identity, expected_gitdir_link=reverse,
            on_phase=lambda phase, status: phases.append((phase, status)),
        )
    assert phases[-1] == ("git_registration", "failed")
    monkeypatch.setattr(core.shutil, "rmtree", real_rmtree)

    real_rmtree(admin)
    # An absent captured admin still completes without invoking phase callbacks.
    finish_workspace_removal(
        record, git_admin_dir=admin,
        expected_admin_identity=identity, expected_gitdir_link=reverse,
        delete_branch=False,
    )


def test_finish_workspace_removal_without_phase_callbacks_and_branch_callback_errors(
    repository, tmp_path, monkeypatch
):
    context = create_workspace(
        repository, tmp_path / "checkout", branch="test/finish-no-callback",
        purpose="test",
    )
    record = core.read_record(context.record_path)
    admin, identity, reverse = core._workspace_admin_snapshot(record)
    _git(repository, "worktree", "remove", "--force", str(context.worktree_path))
    finish_workspace_removal(
        record, git_admin_dir=admin,
        expected_admin_identity=identity, expected_gitdir_link=reverse,
    )

    context = create_workspace(
        repository, tmp_path / "branch-checkout", branch="test/finish-branch-no-callback",
        purpose="test",
    )
    record = core.read_record(context.record_path)
    admin, identity, reverse = core._workspace_admin_snapshot(record)
    _git(repository, "worktree", "remove", "--force", str(context.worktree_path))
    monkeypatch.setattr(core, "list_git_worktrees", lambda *_args: [])
    original_run = core.subprocess.run

    def fail_branch(argv, *args, **kwargs):
        if argv[:3] == ["git", "branch", "-D"]:
            return subprocess.CompletedProcess(argv, 1, "", "ref moved")
        return original_run(argv, *args, **kwargs)

    monkeypatch.setattr(core.subprocess, "run", fail_branch)
    with pytest.raises(WorkspaceError, match="branch removal.*ref moved"):
        finish_workspace_removal(
            record, git_admin_dir=admin,
            expected_admin_identity=identity, expected_gitdir_link=reverse,
        )


def test_finish_workspace_removal_reports_detached_branch_as_skipped(
    repository, tmp_path
):
    checkout = tmp_path / "detached"
    _git(repository, "worktree", "add", "--detach", str(checkout), "HEAD")
    context = adopt_workspace(repository, checkout, purpose="test")
    record = core.read_record(context.record_path)
    assert record.branch == "HEAD"
    admin, identity, reverse = core._workspace_admin_snapshot(record)
    _git(repository, "worktree", "remove", "--force", str(checkout))
    phases = []

    finish_workspace_removal(
        record, git_admin_dir=admin,
        expected_admin_identity=identity, expected_gitdir_link=reverse,
        on_phase=lambda phase, status: phases.append((phase, status)),
    )

    assert phases[-1] == ("branch_removal", "skipped-detached")


def test_failed_checkout_deletion_restores_the_git_registration(repository, tmp_path):
    """CIU-182: git drops its admin dir then fails to delete; the library re-registers."""
    if os.geteuid() == 0:
        pytest.skip("root ignores directory permissions")
    context = create_workspace(
        repository, tmp_path / "checkout", branch="test/restore-registration",
        purpose="test",
    )
    blocked = context.worktree_path / "blocked"
    blocked.mkdir()
    (blocked / "file.bin").write_bytes(b"x")
    blocked.chmod(0o500)
    try:
        with pytest.raises(WorkspaceError, match="registration was restored"):
            remove_workspace(context)
        listed = _git(repository, "worktree", "list", "--porcelain")
        assert str(context.worktree_path) in listed
        assert context.record_path.is_file()
        assert not list(Path(_git(repository, "rev-parse", "--git-common-dir")).resolve()
                        .glob(".worktree-admin-backup-*"))
    finally:
        blocked.chmod(0o700)
    remove_workspace(context)
    assert not context.worktree_path.exists()
    assert not context.record_path.exists()


def test_restore_dropped_registration_refusal_edges(repository, tmp_path):
    context = create_workspace(
        repository, tmp_path / "checkout", branch="test/restore-edges", purpose="test"
    )
    admin = Path(_git(context.worktree_path, "rev-parse", "--absolute-git-dir"))
    backup = tmp_path / "backup"
    shutil.copytree(admin, backup, symlinks=True)
    record = core.read_record(context.record_path)
    # admin still present: nothing to restore
    assert core._restore_dropped_registration(record, admin, backup) is False
    # checkout gone: nothing to restore either
    shutil.rmtree(admin)
    moved = tmp_path / "moved"
    context.worktree_path.rename(moved)
    assert core._restore_dropped_registration(record, admin, backup) is False
    moved.rename(context.worktree_path)
    # a backup that cannot be copied leaves no half-restored admin dir
    assert core._restore_dropped_registration(record, admin, tmp_path / "absent") is False
    assert not admin.exists()
    # a restore Git does not accept as a registration is undone
    (backup / "gitdir").write_text(str(tmp_path / "elsewhere" / ".git") + "\n")
    assert core._restore_dropped_registration(record, admin, backup) is False
    assert not admin.exists()
