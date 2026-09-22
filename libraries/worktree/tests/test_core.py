from __future__ import annotations

import subprocess
from datetime import timedelta
from pathlib import Path

import pytest

from worktree import (
    WorkspaceCollisionError,
    acquire_lease,
    create_workspace,
    ensure_workspace,
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
