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

import subprocess

import pytest

from conftest import cut_snapshot_history, prepared_snapshot
from assay.errors import AssayError, Outcome, ReasonCode
from assay.measurability import (
    ResolvedBase,
    check_base_is_head,
    check_dirty_tree,
    check_resolved_base_is_head,
)


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


def test_a_snapshot_guard_uses_the_carried_resolution_not_a_missing_symbolic_ref(
    git_repo, tmp_path
):
    git_repo.write("src/foo.py", "x = 1\n")
    base = git_repo.commit_all("add foo")
    git_repo.git("tag", "declared-base", base)
    git_repo.write("src/foo.py", "x = 1\ny = 2\n")
    head = git_repo.commit_all("add y")
    scratch = tmp_path / "scratch"
    scratch.mkdir()

    with prepared_snapshot(
        git_repo, scratch_root=scratch, resolved_base=base
    ) as prepared:
        with prepared.materialize(timeout=60) as snapshot:
            assert not (snapshot.root / ".git" / "refs" / "tags" / "declared-base").exists()
            with pytest.raises(AssayError) as excinfo:
                check_base_is_head(snapshot.root, "declared-base")
            assert excinfo.value.reason_code is ReasonCode.GIT_FAILED

            result = check_resolved_base_is_head(snapshot.root, base)

    assert result == ResolvedBase(base_rev=base, head_rev=head)


def test_a_snapshot_guard_still_refuses_a_carried_head_base(git_repo, tmp_path):
    head = git_repo.head()
    git_repo.git("tag", "declared-head", head)
    scratch = tmp_path / "scratch"
    scratch.mkdir()

    with prepared_snapshot(git_repo, scratch_root=scratch) as prepared:
        with prepared.materialize(timeout=60) as snapshot:
            with pytest.raises(AssayError) as excinfo:
                check_resolved_base_is_head(snapshot.root, head)

    assert excinfo.value.outcome is Outcome.NO_MEASUREMENT
    assert excinfo.value.reason_code is ReasonCode.BASE_IS_HEAD


def test_a_history_cut_snapshot_defeats_re_resolution_but_not_the_carried_guard(
    git_repo, tmp_path, monkeypatch
):
    """B101 P1: the carried guard is history-independent. With the snapshot's
    ancestry cut at {seed commit, carried base} (what B101's shallow seed
    records), the resolving guard can no longer answer even when handed the
    full OID -- merge-base finds no common ancestor -- while the carried
    guard, and the diff it feeds, still produce the pre-snapshot answer.
    The first assertion is what makes this non-tautological: it proves the
    cut is real before the carried path is credited with surviving it.
    """
    git_repo.write("src/foo.py", "x = 1\n")
    base = git_repo.commit_all("add foo")
    for n in range(2, 5):
        git_repo.write("src/foo.py", "".join(f"x{i} = {i}\n" for i in range(1, n + 1)))
        head = git_repo.commit_all(f"grow foo to {n}")
    probes = cut_snapshot_history(monkeypatch, carried_base=base)
    scratch = tmp_path / "scratch"
    scratch.mkdir()

    with prepared_snapshot(
        git_repo, scratch_root=scratch, resolved_base=base
    ) as prepared:
        with prepared.materialize(timeout=60) as snapshot:
            with pytest.raises(AssayError) as excinfo:
                check_base_is_head(snapshot.root, base)
            assert excinfo.value.reason_code is ReasonCode.GIT_FAILED

            result = check_resolved_base_is_head(snapshot.root, base)
            diff = subprocess.run(
                ["git", "-C", str(snapshot.root), "diff", "--unified=0",
                 result.base_rev, result.head_rev],
                capture_output=True, text=True, check=True,
            ).stdout

    assert [p.merge_base_returncode != 0 for p in probes] == [True]
    assert result == ResolvedBase(base_rev=base, head_rev=head)
    assert "+x4 = 4" in diff


@pytest.mark.parametrize(
    "spelling", ["main", "HEAD~1", "declared-base", "abc123", None, "A" * 40]
)
def test_the_carried_guard_refuses_anything_but_a_full_commit_oid(git_repo, spelling):
    """A declared spelling handed to the carried guard is a caller bug: it
    would bypass the pre-snapshot resolution (and its first-parent rule)
    and leave a later `git diff` to resolve it inside the snapshot after
    all. Refused before any git call, never silently compared."""
    git_repo.git("tag", "declared-base", git_repo.head())

    with pytest.raises(ValueError, match="full commit OID"):
        check_resolved_base_is_head(git_repo.path, spelling)


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
