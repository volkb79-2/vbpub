"""Fail-closed boundary coverage for the neutral workspace substrate."""
from __future__ import annotations

import json
import inspect
import shutil
import subprocess
from contextlib import nullcontext
from dataclasses import FrozenInstanceError
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
        repository / "README", tmp_path / "nested" / "checkout",
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


def test_remove_unrecorded_workspace_uses_the_shared_lifecycle(repository, tmp_path):
    target = tmp_path / "legacy"
    _git(repository, "worktree", "add", "-q", "-b", "legacy", str(target), "HEAD")
    with pytest.raises(WorkspaceError, match="branch changed"):
        remove_unrecorded_workspace(repository, target, expected_branch="wrong")
    assert target.is_dir()

    remove_unrecorded_workspace(
        repository, target, expected_branch="legacy", purpose="legacy-cleanup"
    )
    assert not target.exists()
    assert _git(repository, "show-ref", "--verify", "--quiet", "refs/heads/legacy", check=False) == ""


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
        assert kwargs["text"] is True
        assert kwargs["capture_output"] is True
        assert kwargs["check"] is False
        if argv[1:3] == ["worktree", "remove"]:
            return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")
        if argv[1:3] == ["branch", "-D"]:
            return subprocess.CompletedProcess(argv, 1, stdout="branch stdout", stderr="")
        return original(argv, **kwargs)

    monkeypatch.setattr(core.subprocess, "run", branch_failure)
    with pytest.raises(WorkspaceError, match="branch stdout"):
        remove_workspace(record)
    assert context.record_path.exists()
    monkeypatch.undo()
    remove_workspace(context, force=True)


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
