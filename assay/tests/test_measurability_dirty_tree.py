"""O3 — the DIRTY_TREE guard catches staged, unstaged, and untracked changes
under a resolved source root, and never a sibling directory whose name only
happens to share the root's prefix.

Negative: removing any one porcelain category (staged / unstaged / untracked)
lets that category's dirty fixture slip past the guard; matching by string
prefix instead of resolved filesystem path makes the sibling-prefix fixture
falsely raise — see this module's mutation evidence in the package LOG.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from assay.errors import AssayError, Outcome, ReasonCode
from assay.measurability import check_dirty_tree


@pytest.fixture
def rooted_repo(git_repo):
    """A repo with a real source root (``src/foo``) and a sibling directory
    (``src/foo_evil``) whose name only STARTS WITH the root's name — the
    specimen a naive string-prefix check gets wrong."""
    git_repo.write("src/foo/committed.py", "x = 1\n")
    git_repo.write("src/foo_evil/other.py", "y = 2\n")
    git_repo.commit_all("seed source tree")
    return git_repo


def _root(repo) -> Path:
    return (repo.path / "src" / "foo").resolve()


def test_clean_tree_under_the_root_does_not_raise(rooted_repo):
    check_dirty_tree(rooted_repo.path, [_root(rooted_repo)])  # must not raise


def test_staged_change_under_the_root_raises_dirty_tree(rooted_repo):
    rooted_repo.write("src/foo/committed.py", "x = 2\n")
    rooted_repo.git("add", "src/foo/committed.py")

    with pytest.raises(AssayError) as excinfo:
        check_dirty_tree(rooted_repo.path, [_root(rooted_repo)])

    assert excinfo.value.outcome is Outcome.NO_MEASUREMENT
    assert excinfo.value.reason_code is ReasonCode.DIRTY_TREE
    assert "src/foo/committed.py" in str(excinfo.value)


def test_unstaged_change_under_the_root_raises_dirty_tree(rooted_repo):
    rooted_repo.write("src/foo/committed.py", "x = 3\n")  # not staged

    with pytest.raises(AssayError) as excinfo:
        check_dirty_tree(rooted_repo.path, [_root(rooted_repo)])

    assert excinfo.value.reason_code is ReasonCode.DIRTY_TREE
    assert "src/foo/committed.py" in str(excinfo.value)


def test_untracked_file_under_the_root_raises_dirty_tree(rooted_repo):
    rooted_repo.write("src/foo/new_file.py", "z = 4\n")  # never added

    with pytest.raises(AssayError) as excinfo:
        check_dirty_tree(rooted_repo.path, [_root(rooted_repo)])

    assert excinfo.value.reason_code is ReasonCode.DIRTY_TREE
    assert "src/foo/new_file.py" in str(excinfo.value)


def test_renamed_file_under_the_root_reports_only_the_new_path(rooted_repo):
    rooted_repo.git("mv", "src/foo/committed.py", "src/foo/renamed.py")

    with pytest.raises(AssayError) as excinfo:
        check_dirty_tree(rooted_repo.path, [_root(rooted_repo)])

    message = str(excinfo.value)
    assert "src/foo/renamed.py" in message
    assert "->" not in message


def test_dirty_sibling_directory_outside_the_root_does_not_raise(rooted_repo):
    rooted_repo.write("src/foo_evil/other.py", "y = 3\n")  # unstaged, outside root
    rooted_repo.write("src/foo_evil/new.py", "n = 1\n")  # untracked, outside root

    check_dirty_tree(rooted_repo.path, [_root(rooted_repo)])  # must not raise


def test_dirty_under_any_declared_root_raises(rooted_repo):
    extra_root = rooted_repo.path / "extra"
    extra_root.mkdir()
    rooted_repo.write("extra/keep.py", "k = 1\n")
    rooted_repo.commit_all("add second root")

    rooted_repo.write("extra/keep.py", "k = 2\n")  # dirty under the SECOND root only

    with pytest.raises(AssayError) as excinfo:
        check_dirty_tree(
            rooted_repo.path, [_root(rooted_repo), extra_root.resolve()]
        )

    assert excinfo.value.reason_code is ReasonCode.DIRTY_TREE
    assert "extra/keep.py" in str(excinfo.value)


def test_dirty_outside_every_declared_root_does_not_raise(rooted_repo):
    rooted_repo.write("elsewhere/untouched_by_any_root.py", "e = 1\n")

    check_dirty_tree(rooted_repo.path, [_root(rooted_repo)])  # must not raise
