"""Behavioral oracles for the CMRU R2 shard s13 survivors."""
from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from cmru import transaction


def _completed(returncode: int) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], returncode, "", "")


def test_cleanup_preview_rejects_a_different_recorded_workspace_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo_root = tmp_path / "repo"
    worktree = repo_root / ".worktrees" / "cmru-build-identity"
    worktree.mkdir(parents=True)
    (repo_root / ".git").mkdir()

    branch = "cmru/build/identity"
    base = "a" * 40
    preview_context = SimpleNamespace(workspace_id="preview-workspace")
    current_context = SimpleNamespace(workspace_id="current-workspace")
    shared = SimpleNamespace(
        discover_git_context=lambda _path: (repo_root, repo_root / ".git", branch, base),
        ensure_workspace=lambda _record: current_context,
    )
    monkeypatch.setattr(transaction, "_common_git_dir", lambda _path: repo_root / ".git")
    monkeypatch.setattr(
        transaction,
        "_git",
        lambda _path, *args, **_kwargs: (
            branch if args == ("branch", "--show-current") else base
        ),
    )
    monkeypatch.setattr(transaction, "_shared_worktree", lambda: shared)
    monkeypatch.setattr(
        transaction,
        "_shared_workspace_record",
        lambda *_args: SimpleNamespace(purpose="cmru-build"),
    )
    monkeypatch.setattr(transaction, "remove_workspace", lambda _workspace: None)

    preview = transaction.ReleaseWorkspace(
        repo_root, worktree, branch, base, context=preview_context,
    )

    with pytest.raises(RuntimeError, match="identity changed after cleanup preview"):
        transaction.discard_build_workspace(
            repo_root, worktree, dry_run=False, expected_workspace=preview,
        )

    assert worktree.is_dir()


@pytest.mark.parametrize(
    ("touched_count", "expected_suffix"),
    [(5, "cmru/e.py). Review"), (6, "cmru/e.py...). Review")],
    ids=["five-without-ellipsis", "six-with-ellipsis"],
)
def test_release_promotion_truncates_the_touched_file_list_at_six(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    touched_count: int,
    expected_suffix: str,
) -> None:
    worktree = tmp_path / "candidate"
    worktree.mkdir()
    workspace = transaction.ReleaseWorkspace(
        tmp_path, worktree, "cmru/release/touched", "a" * 40,
    )
    origin_main = "b" * 40
    merge_base = "c" * 40
    touched = "\n".join(f"cmru/{letter}.py" for letter in "abcdef"[:touched_count])

    monkeypatch.setattr(
        transaction,
        "run_remote_git",
        lambda *_args, **_kwargs: _completed(0),
    )
    monkeypatch.setattr(
        transaction,
        "run_local_git",
        lambda *_args, **_kwargs: _completed(1),
    )

    def fake_git(_path: Path, *args: str, **_kwargs: object) -> str:
        if args == ("rev-parse", "origin/main"):
            return origin_main
        if args[:1] == ("merge-base",):
            return merge_base
        if args[:1] == ("diff",):
            return touched
        raise AssertionError(f"unexpected git call: {args}")

    monkeypatch.setattr(transaction, "_git", fake_git)

    with pytest.raises(RuntimeError) as raised:
        transaction._merge_origin_main_into_candidate(
            workspace,
            git_auth=None,
            project_paths=("cmru",),
            release_label="cmru-v7.2.5",
        )

    message = str(raised.value)
    assert "changed the released project's own paths" in message
    assert expected_suffix in message


@pytest.mark.parametrize(
    ("abort_returncode", "expected_reason", "unexpected_reason"),
    [
        (None, "automatic rebase cleanup failed", "rebase was aborted"),
        (0, "rebase was aborted and local main was left untouched", "cleanup failed"),
    ],
    ids=["abort-not-attempted", "successful-abort"],
)
def test_cleanup_conflict_classifies_an_absent_abort_result_separately_from_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    abort_returncode: int | None,
    expected_reason: str,
    unexpected_reason: str,
) -> None:
    after_failure_active = abort_returncode is not None
    rebase_states = iter([False, after_failure_active])

    monkeypatch.setattr(
        transaction,
        "run_remote_git",
        lambda *_args, **_kwargs: _completed(0),
    )

    def fake_git(_path: Path, *args: str, **_kwargs: object) -> str:
        if args == ("branch", "--show-current"):
            return "main"
        if args == ("status", "--porcelain", "--untracked-files=all"):
            return ""
        if args == ("ls-files", "-u"):
            return "conflicted.txt\n"
        raise AssertionError(f"unexpected git call: {args}")

    monkeypatch.setattr(transaction, "_git", fake_git)
    monkeypatch.setattr(
        transaction,
        "_ignored_paths_origin_would_overwrite",
        lambda _root: [],
    )
    monkeypatch.setattr(
        transaction,
        "_rebase_in_progress",
        lambda _root: next(rebase_states),
    )

    def fake_local_git(_root: Path, *args: str, **_kwargs: object):
        if args == ("rebase", "origin/main"):
            return _completed(1)
        if args == ("rebase", "--abort"):
            return _completed(abort_returncode or 0)
        raise AssertionError(f"unexpected local git call: {args}")

    monkeypatch.setattr(transaction, "run_local_git", fake_local_git)

    result = transaction._sync_local_main_result(tmp_path)

    assert result.ok is False
    assert expected_reason in result.reason
    assert unexpected_reason not in result.reason
