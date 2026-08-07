"""O4 — the BASE_IS_HEAD guard fires before any diff is parsed, while a
clean, committed, docs-only change (an empty delta under the source roots)
clears both measurability guards.

Negative: deleting the base==HEAD equality check lets a vacuous base reach
evaluation; an over-eager guard that treats a clean tree with no *interesting*
delta as unmeasurable would reject the docs-only fixture even though nothing
is dirty and base genuinely differs from HEAD — see this module's mutation
evidence in the package LOG.
"""

from __future__ import annotations

import pytest

from assay.errors import AssayError, Outcome, ReasonCode
from assay.measurability import ResolvedBase, check_base_is_head, check_dirty_tree


def test_base_resolving_to_head_raises_before_any_diff_is_parsed(git_repo):
    # `git_repo` has one commit (seed) on main; "main" resolves to HEAD
    # itself, the textbook vacuous case (DESIGN-GUIDE §6).
    with pytest.raises(AssayError) as excinfo:
        check_base_is_head(git_repo.path, "main")

    assert excinfo.value.outcome is Outcome.NO_MEASUREMENT
    assert excinfo.value.reason_code is ReasonCode.BASE_IS_HEAD


def test_a_real_delta_against_an_ancestor_base_clears_the_guard(git_repo):
    git_repo.write("src/foo.py", "x = 1\n")
    git_repo.commit_all("add foo")
    git_repo.git("branch", "before-foo", "HEAD~1")

    result = check_base_is_head(git_repo.path, "before-foo")

    assert isinstance(result, ResolvedBase)
    assert result.head_rev == git_repo.head()
    assert result.base_rev != result.head_rev


def test_clean_docs_only_commit_with_empty_source_delta_clears_both_guards(
    git_repo,
):
    root = (git_repo.path / "src").resolve()
    git_repo.write("src/.gitkeep", "")
    git_repo.commit_all("create the source root")
    git_repo.git("branch", "before-docs", "HEAD")

    git_repo.write("docs/notes.md", "just docs\n")  # outside the source root
    git_repo.commit_all("docs: add a note")

    check_dirty_tree(git_repo.path, [root])  # nothing under src/ is dirty
    result = check_base_is_head(git_repo.path, "before-docs")

    assert result.base_rev != result.head_rev
    assert result.head_rev == git_repo.head()
