"""W2-PKG1 section C: transaction.py translates domain failures, not bugs.

Every former ``except Exception`` that flattened a library/OS failure into a
``RuntimeError`` now catches only the operational error types.  A programming
error (``KeyError`` here) must keep its type and traceback; an operational
error (``RuntimeError``/``OSError``) is still translated to the clean message.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from cmru import transaction


def _worktree_raising(exc):
    def discover(_path):
        raise exc

    return SimpleNamespace(discover_git_context=discover)


@pytest.fixture()
def release_dir(tmp_path):
    path = tmp_path / "cmru-release-x"
    path.mkdir()
    return path


def test_domain_errors_tuple_is_the_operational_set():
    assert set(transaction._DOMAIN_ERRORS) == {
        RuntimeError, OSError, ValueError, transaction.subprocess.SubprocessError,
    }


@pytest.mark.parametrize("exc", [RuntimeError("boom"), OSError("boom"), ValueError("boom")])
def test_resume_workspace_translates_operational_failure(monkeypatch, tmp_path, release_dir, exc):
    monkeypatch.setattr(transaction, "_shared_worktree", lambda: _worktree_raising(exc))
    with pytest.raises(RuntimeError, match="is not a worktree: boom"):
        transaction.resume_workspace(tmp_path, release_dir)


def test_resume_workspace_does_not_flatten_a_programming_error(monkeypatch, tmp_path, release_dir):
    monkeypatch.setattr(transaction, "_shared_worktree", lambda: _worktree_raising(KeyError("k")))
    with pytest.raises(KeyError):
        transaction.resume_workspace(tmp_path, release_dir)


def test_read_release_scope_for_path_translates_and_propagates(monkeypatch, release_dir):
    monkeypatch.setattr(transaction, "_shared_worktree", lambda: _worktree_raising(OSError("io")))
    with pytest.raises(RuntimeError, match="is not a readable Git worktree: io"):
        transaction.read_release_scope_for_path(release_dir)
    monkeypatch.setattr(transaction, "_shared_worktree", lambda: _worktree_raising(KeyError("k")))
    with pytest.raises(KeyError):
        transaction.read_release_scope_for_path(release_dir)


def test_project_git_family_groups_translates_and_propagates(monkeypatch, tmp_path):
    project = SimpleNamespace(project_root=tmp_path)
    monkeypatch.setattr(transaction, "_shared_worktree", lambda: _worktree_raising(ValueError("v")))
    with pytest.raises(RuntimeError, match="not inside a usable Git worktree: v"):
        transaction.project_git_family_groups(tmp_path, [project])
    monkeypatch.setattr(transaction, "_shared_worktree", lambda: _worktree_raising(KeyError("k")))
    with pytest.raises(KeyError):
        transaction.project_git_family_groups(tmp_path, [project])


def test_project_git_family_groups_empty_selection_translates_and_propagates(monkeypatch, tmp_path):
    monkeypatch.setattr(transaction, "_shared_worktree", lambda: _worktree_raising(OSError("o")))
    with pytest.raises(RuntimeError, match="has no selected project Git family: o"):
        transaction.project_git_family_groups(tmp_path, [])
    monkeypatch.setattr(transaction, "_shared_worktree", lambda: _worktree_raising(KeyError("k")))
    with pytest.raises(KeyError):
        transaction.project_git_family_groups(tmp_path, [])


def test_remove_workspace_translates_and_propagates(monkeypatch):
    def worktree(exc):
        def remove(_context):
            raise exc

        return SimpleNamespace(remove_workspace=remove)

    workspace = SimpleNamespace(context=object())
    monkeypatch.setattr(transaction, "_shared_worktree", lambda: worktree(OSError("gone")))
    with pytest.raises(RuntimeError, match="gone"):
        transaction.remove_workspace(workspace)
    monkeypatch.setattr(transaction, "_shared_worktree", lambda: worktree(KeyError("k")))
    with pytest.raises(KeyError):
        transaction.remove_workspace(workspace)
