from __future__ import annotations

import subprocess
import shutil
from dataclasses import FrozenInstanceError
from datetime import timedelta
from pathlib import Path

import pytest

from worktree import (
    WorkspaceCollisionError,
    acquire_lease,
    create_workspace,
    ensure_workspace,
    find_workspace,
    list_git_worktrees,
    physical_path,
    read_record,
    remove_workspace,
    release_lease,
    workspace_id_for_path,
)
import worktree.core as core


def _git(path: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=path, text=True, capture_output=True, check=True
    )
    return result.stdout.strip()


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    repo = tmp_path / "source"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "test@example.invalid")
    _git(repo, "config", "user.name", "Workspace Test")
    (repo / "README").write_text("source\n", encoding="utf-8")
    _git(repo, "add", "README")
    _git(repo, "commit", "-m", "initial")
    return repo


def test_identity_is_six_lowercase_base36(tmp_path: Path) -> None:
    value = workspace_id_for_path(tmp_path / "checkout")
    assert len(value) == 6
    assert value == value.lower()
    assert all(char in "0123456789abcdefghijklmnopqrstuvwxyz" for char in value)


def test_find_workspace_matches_exact_path_and_distinguishes_absence(repository, tmp_path):
    context = create_workspace(
        repository,
        tmp_path / "linked",
        branch="find/by-path",
        base="HEAD",
    )

    record = find_workspace(context.git_common_dir, context.worktree_path)

    assert record is not None
    assert record.context() == context
    assert find_workspace(context.git_common_dir, tmp_path / "not-registered") is None
    alternate_spelling = context.worktree_path.parent / "absent" / ".." / context.worktree_path.name
    assert find_workspace(context.git_common_dir, alternate_spelling) is None
    with pytest.raises(core.WorkspaceError, match="must be absolute"):
        find_workspace(context.git_common_dir, Path("relative/worktree"))
    remove_workspace(context)


def test_find_workspace_refuses_duplicate_record_ownership(monkeypatch, repository, tmp_path):
    context = create_workspace(
        repository,
        tmp_path / "linked",
        branch="find/duplicate",
        base="HEAD",
    )
    record = read_record(context.record_path)

    with monkeypatch.context() as scoped:
        scoped.setattr(core, "list_workspaces", lambda _common: [record, record])
        with pytest.raises(
            core.WorkspaceError, match="multiple shared workspace records"
        ) as exc:
            find_workspace(context.git_common_dir, context.worktree_path)

    assert exc.value.category == "invalid-record"
    remove_workspace(context)


def test_list_workspaces_refuses_duplicate_path_ownership(monkeypatch, repository, tmp_path):
    context = create_workspace(
        repository,
        tmp_path / "linked",
        branch="find/list-duplicate",
        base="HEAD",
    )
    record = read_record(context.record_path)

    with monkeypatch.context() as scoped:
        scoped.setattr(core, "_records", lambda _common: [record, record])
        with pytest.raises(
            core.WorkspaceError, match="multiple shared workspace records"
        ) as exc:
            core.list_workspaces(context.git_common_dir)

    assert exc.value.category == "invalid-record"
    remove_workspace(context)


def test_git_inventory_is_primary_first_and_preserves_opaque_paths(repository, tmp_path):
    odd_path = tmp_path / "linked with space\nand\ttab"
    _git(repository, "worktree", "add", "--detach", str(odd_path), "HEAD")
    _git(repository, "worktree", "lock", "--reason", "inventory test", str(odd_path))
    inventory = list_git_worktrees(repository)

    assert len(inventory) == 2
    assert inventory[0].path == repository
    assert inventory[0].is_primary
    assert inventory[0].branch == "main"
    assert not inventory[0].is_detached
    assert inventory[0].head == _git(repository, "rev-parse", "HEAD")
    assert inventory[1].path == odd_path
    assert not inventory[1].is_primary
    assert inventory[1].branch is None
    assert inventory[1].is_detached
    assert inventory[1].is_locked
    assert inventory[1].head == inventory[0].head
    _git(repository, "worktree", "unlock", str(odd_path))
    _git(repository, "worktree", "remove", "--force", str(odd_path))


def test_git_inventory_uses_git_prunable_fact_without_statting_recorded_path(
    repository, tmp_path, monkeypatch
):
    linked = tmp_path / "linked"
    _git(repository, "worktree", "add", "-b", "inventory/prunable", str(linked))
    shutil.rmtree(linked)
    real_stat = core.os.stat

    def forbid_recorded_path_stat(path, *args, **kwargs):
        if Path(path) == linked:
            pytest.fail(f"inventory must not stat the Git path {path}")
        return real_stat(path, *args, **kwargs)

    monkeypatch.setattr(
        core.os,
        "stat",
        forbid_recorded_path_stat,
    )
    inventory = list_git_worktrees(repository)
    assert len(inventory) == 2
    assert inventory[1].path == linked
    assert inventory[1].is_prunable
    _git(repository, "worktree", "prune", "--expire", "now")


def test_git_prunable_marker_does_not_mean_checkout_directory_is_missing(repository, tmp_path):
    linked = tmp_path / "linked-with-broken-gitfile"
    _git(repository, "worktree", "add", "-b", "inventory/broken-gitfile", str(linked))
    head = _git(repository, "rev-parse", "inventory/broken-gitfile")

    # Leave the checkout directory in place but break its administrative
    # back-link. Git reports a prunable record even though this path exists.
    (linked / ".git").unlink()
    assert linked.is_dir()

    entry = next(
        item for item in list_git_worktrees(repository)
        if item.path == linked
    )
    assert entry.is_prunable
    assert entry.head == head


def test_git_inventory_marks_bare_repository_without_primary(tmp_path):
    bare = tmp_path / "bare.git"
    bare.mkdir()
    _git(bare, "init", "--bare", "-q")

    inventory = list_git_worktrees(bare)

    assert len(inventory) == 1
    assert inventory[0].path == bare
    assert inventory[0].is_bare
    assert not inventory[0].is_primary
    assert inventory[0].head is None


def test_git_inventory_accepts_head_on_bare_repository():
    head = b"a" * 40

    inventory = core._parse_git_worktrees(
        b"worktree /repo.git\0bare\0HEAD " + head + b"\0\0"
    )

    assert inventory[0].is_bare
    assert inventory[0].head == head.decode()


def test_git_inventory_accepts_a_file_inside_the_worktree(repository):
    assert list_git_worktrees(repository / "README")[0].path == repository


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (b"", "empty or unterminated"),
        (b"HEAD " + b"a" * 40 + b"\0\0", "before its worktree path"),
        (b"worktree\0\0", "malformed worktree path field"),
        (b"worktree relative\0bare\0\0", "non-absolute path"),
        (
            b"worktree /repo\0worktree /second\0\0",
            "malformed worktree path field",
        ),
        (
            b"worktree /repo\0HEAD " + b"a" * 40 + b"\0HEAD " + b"a" * 40 + b"\0\0",
            "duplicate 'HEAD' field",
        ),
        (b"worktree /repo\0HEAD bad\0branch refs/heads/main\0\0", "invalid HEAD"),
        (
            b"worktree /repo\0HEAD " + b"a" * 40 + b"\0branch refs/heads/\0\0",
            "invalid branch",
        ),
        (
            b"worktree /repo\0HEAD " + b"a" * 40 + b"\0branch other\0\0",
            "invalid branch",
        ),
        (
            b"worktree /repo\0HEAD " + b"a" * 40 + b"\0branch refs/heads/main\0detached\0\0",
            "inconsistent branch state",
        ),
        (b"worktree /repo\0bare\0detached\0\0", "invalid bare-worktree record"),
        (b"worktree /repo.git\0bare\0HEAD bad\0\0", "invalid HEAD for bare repository"),
        (b"\0\0", "incomplete or empty inventory"),
    ],
)
def test_git_inventory_rejects_malformed_porcelain(payload, message):
    with pytest.raises(core.WorkspaceError, match=message):
        core._parse_git_worktrees(payload)


@pytest.mark.parametrize(
    "payload",
    [
        b"worktree\0\0",
        b"worktree \0\0",
    ],
)
def test_git_inventory_rejects_each_malformed_path_predicate(payload):
    with pytest.raises(core.WorkspaceError, match="malformed worktree path field"):
        core._parse_git_worktrees(payload)


def test_git_inventory_rejects_each_invalid_bare_record_axis():
    head = b"a" * 40
    normal = b"worktree /repo\0HEAD " + head + b"\0branch refs/heads/main\0\0"
    second_bare = normal + b"worktree /bare.git\0bare\0\0"
    with pytest.raises(core.WorkspaceError, match="invalid bare-worktree record"):
        core._parse_git_worktrees(second_bare)

    with pytest.raises(core.WorkspaceError, match="invalid bare-worktree record"):
        core._parse_git_worktrees(b"worktree /bare.git\0bare\0detached\0\0")


def test_git_inventory_tracks_bare_record_state():
    head = b"a" * 40
    payload = b"worktree /first.git\0bare\0HEAD " + head + b"\0\0"
    inventory = core._parse_git_worktrees(payload)
    assert inventory[0].is_bare is True


def test_git_inventory_requires_record_terminator():
    with pytest.raises(core.WorkspaceError, match="empty or unterminated"):
        core._parse_git_worktrees(b"worktree /repo\0")


def test_git_inventory_surfaces_git_and_startup_failures(monkeypatch, tmp_path):
    calls = []

    def failing_run(*args, **kwargs):
        calls.append(kwargs)
        return subprocess.CompletedProcess(
            args[0], 128, stdout=b"", stderr=b"not a repository"
        )

    monkeypatch.setattr(
        core.subprocess,
        "run", failing_run,
    )
    with pytest.raises(core.WorkspaceError, match="not a repository"):
        list_git_worktrees(tmp_path)
    assert calls[0]["check"] is False

    monkeypatch.setattr(
        core.subprocess,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("git missing")),
    )
    with pytest.raises(core.WorkspaceError, match="could not start.*git missing"):
        list_git_worktrees(tmp_path)


def test_physical_translation_does_not_require_target_namespace(tmp_path: Path) -> None:
    translated = physical_path(
        "/logical/repo/nested", logical_root="/logical/repo", physical_root="/host/repo"
    )
    assert translated == Path("/host/repo/nested")


def test_allocate_resume_lease_and_remove(repository: Path, tmp_path: Path) -> None:
    target = tmp_path / "checkout"
    context = create_workspace(
        repository,
        target,
        branch="workspace/test",
        purpose="test",
    )
    record = read_record(context.record_path)
    with pytest.raises(FrozenInstanceError):
        record.branch = "mutated"
    assert record.workspace_id == context.workspace_id
    assert ensure_workspace(record).worktree_path == target.resolve()

    leased = acquire_lease(record, holder="pytest", ttl=timedelta(minutes=5))
    assert leased.lease is not None
    assert release_lease(leased).lease is None

    remove_workspace(context)
    assert not target.exists()
    assert not context.record_path.exists()


def test_explicit_identity_path_is_durable_and_rechecked(repository: Path, tmp_path: Path) -> None:
    target = tmp_path / "checkout"
    identity_path = tmp_path / "allocation-seed"
    context = create_workspace(
        repository,
        target,
        branch="workspace/explicit-identity",
        purpose="test",
        identity_path=identity_path,
    )
    record = read_record(context.record_path)
    original_metadata = dict(record.metadata)
    assert context.workspace_id == workspace_id_for_path(identity_path)
    assert record.metadata["workspace.identity_path"] == str(identity_path.resolve())
    assert ensure_workspace(record).workspace_id == context.workspace_id

    updated = ensure_workspace(record, metadata={"adapter": "value"})
    assert updated.workspace_id == context.workspace_id
    assert read_record(context.record_path).metadata["workspace.identity_path"] == str(
        identity_path.resolve()
    )

    with pytest.raises(core.WorkspaceError, match="cannot replace durable"):
        ensure_workspace(
            record,
            metadata={"workspace.identity_path": str(tmp_path / "different-seed")},
        )

    record.metadata["workspace.identity_path"] = str(tmp_path / "different-seed")
    core.write_record(record)
    with pytest.raises(core.WorkspaceError, match="identity does not match"):
        ensure_workspace(record)
    core.write_record(core.replace(record, metadata=original_metadata))
    remove_workspace(context)


@pytest.mark.parametrize(
    "identity_path",
    [None, "", 7, "relative/seed", "/tmp/seed/../canonicalized"],
    ids=("null", "empty", "wrong-type", "relative", "not-normalized"),
)
def test_malformed_persisted_identity_refuses_resume_inspect_and_remove(
    repository: Path, tmp_path: Path, identity_path
) -> None:
    context = create_workspace(
        repository, tmp_path / "checkout", branch="workspace/bad-identity", purpose="test"
    )
    record = read_record(context.record_path)
    malformed_metadata = dict(record.metadata)
    malformed_metadata["workspace.identity_path"] = identity_path
    malformed = core.replace(record, metadata=malformed_metadata)
    core.write_record(malformed)
    cleanup_calls = []

    for operation in (
        lambda: ensure_workspace(context),
        lambda: core.inspect_workspace(context),
        lambda: remove_workspace(
            context,
            cleanup=lambda _context: cleanup_calls.append("ran"),
            force=True,
        ),
    ):
        with pytest.raises(core.WorkspaceError, match="workspace.identity_path"):
            operation()
    assert cleanup_calls == []
    assert context.worktree_path.is_dir()
    core.write_record(core.replace(record, metadata=dict(record.metadata)))
    remove_workspace(context, force=True)


def test_identity_collision_names_both_paths(repository: Path, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(core, "workspace_id_for_path", lambda _path: "abc123")
    first = create_workspace(repository, tmp_path / "one", branch="one", purpose="test")
    with pytest.raises(WorkspaceCollisionError) as raised:
        create_workspace(repository, tmp_path / "two", branch="two", purpose="test")
    assert "one" in str(raised.value)
    assert "two" in str(raised.value)
    remove_workspace(first)
