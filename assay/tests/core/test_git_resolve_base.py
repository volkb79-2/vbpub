"""O2 — base resolution follows the shape of ``HEAD``, and a failing git
command raises ``GIT_FAILED``.

In ``tmp_path`` git repositories: a merge-commit ``HEAD`` (two parents)
resolves to its first parent; a normal (single-parent) ``HEAD`` resolves to
``merge-base(base, HEAD)``. A forced non-zero git invocation raises
:class:`~assay.errors.AssayError` with ``ERROR``/``GIT_FAILED``.

Negative: always using merge-base makes the merge fixture return its fork
point instead of the first parent; swallowing the command's exit code lets a
broken git invocation reach evaluation instead of raising — see this module's
mutation evidence in the package LOG.
"""

from __future__ import annotations

import pytest

from assay.errors import AssayError, Outcome, ReasonCode
from assay.git import base_resolution_mode, head_rev, repo_top, resolve_base, run


def test_normal_head_resolves_to_merge_base_of_base_and_head(git_repo):
    git_repo.git("checkout", "-q", "-b", "main-line")
    git_repo.write("on_main.py", "m = 1\n")
    git_repo.commit_all("main change")

    git_repo.git("checkout", "-q", "-b", "feature", "main-line")
    # move main-line ahead so it is NOT an ancestor of feature's tip, forcing
    # a real merge-base computation rather than a degenerate "base IS an
    # ancestor of HEAD, so merge-base(base, HEAD) == base" coincidence.
    git_repo.git("checkout", "-q", "main-line")
    git_repo.write("more_on_main.py", "m = 2\n")
    git_repo.commit_all("second main change")
    git_repo.git("checkout", "-q", "feature")
    git_repo.write("on_feature.py", "f = 1\n")
    git_repo.commit_all("feature change")

    expected = git_repo.git("merge-base", "main-line", "feature").strip()
    assert expected != git_repo.head()  # the test proves a REAL divergence

    resolved = resolve_base(git_repo.path, "main-line")

    assert resolved == expected
    assert base_resolution_mode(git_repo.path) == "merge-base"


def test_merge_commit_head_resolves_to_its_first_parent_not_merge_base(git_repo):
    git_repo.git("checkout", "-q", "-b", "feature")
    git_repo.write("on_feature.py", "f = 1\n")
    git_repo.commit_all("feature change")

    git_repo.git("checkout", "-q", "main")
    git_repo.write("on_main.py", "m = 1\n")
    main_tip = git_repo.commit_all("main change")

    git_repo.git("merge", "-q", "--no-edit", "feature")
    merge_commit = git_repo.head()
    fork_point = git_repo.git("merge-base", "main", "feature").strip()

    resolved = resolve_base(git_repo.path, "main")

    assert git_repo.head() == merge_commit
    assert resolved == main_tip  # the first parent, i.e. main's pre-merge tip
    assert resolved != fork_point  # proves the merge-base branch was NOT taken
    # (B008) The narrowing is now auditable: a consumer reading the verdict
    # can tell `resolved` is the branch's pre-merge tip, not a fork point.
    assert base_resolution_mode(git_repo.path) == "first-parent"


def _merge_head_with_requested_fork_base(git_repo):
    """A real two-parent merge HEAD whose requested base (the fork point) is
    NOT its first parent: returns ``(first_parent, fork_point, merge_commit)``."""
    fork_point = git_repo.head()
    git_repo.git("checkout", "-q", "-b", "feature")
    git_repo.write("on_feature.py", "f = 1\n")
    git_repo.commit_all("feature change")
    git_repo.git("checkout", "-q", "main")
    git_repo.write("on_main.py", "m = 1\n")
    main_tip = git_repo.commit_all("main change")
    git_repo.git("merge", "-q", "--no-edit", "feature")
    return main_tip, fork_point, git_repo.head()


def test_delegated_base_on_a_merge_head_resolves_the_requested_comparison(git_repo):
    """B552: a delegated (`base_source = "request"`) base is the exact comparison
    the caller asked for, so a merge HEAD resolves merge-base(request, HEAD) --
    here the fork point -- and NOT the first parent that discards the request."""
    first_parent, fork_point, merge_commit = _merge_head_with_requested_fork_base(git_repo)
    assert first_parent != fork_point  # the two policies really disagree here

    resolved = resolve_base(git_repo.path, fork_point, delegated=True)

    assert git_repo.head() == merge_commit
    assert resolved == fork_point
    assert resolved != first_parent
    # the verdict names the rule that actually ran, never "first-parent" for a
    # request that was honoured
    assert base_resolution_mode(git_repo.path, delegated=True) == "merge-base"


def test_non_delegated_base_on_a_merge_head_keeps_first_parent_unchanged(git_repo):
    """B552 control: the same merge HEAD and the same ref, declared by the lane
    itself, still resolves the first parent (B008's policy is untouched)."""
    first_parent, fork_point, _ = _merge_head_with_requested_fork_base(git_repo)

    assert resolve_base(git_repo.path, fork_point) == first_parent
    assert resolve_base(git_repo.path, fork_point, delegated=False) == first_parent
    assert base_resolution_mode(git_repo.path) == "first-parent"


def test_delegated_and_declared_agree_on_a_non_merge_head(git_repo):
    """B552 positive control: on a non-merge HEAD the flag changes nothing."""
    fork_point = git_repo.head()
    git_repo.write("on_main.py", "m = 1\n")
    git_repo.commit_all("main change")

    assert (
        resolve_base(git_repo.path, fork_point, delegated=True)
        == resolve_base(git_repo.path, fork_point)
        == fork_point
    )
    assert base_resolution_mode(git_repo.path, delegated=True) == "merge-base"
    assert base_resolution_mode(git_repo.path) == "merge-base"


def test_a_delegated_unresolvable_ref_still_raises_git_failed(git_repo):
    """B552: honouring the request must not weaken its validation: an
    unresolvable requested ref on a merge HEAD refuses by name, as before it
    would have been silently discarded."""
    _merge_head_with_requested_fork_base(git_repo)

    with pytest.raises(AssayError) as excinfo:
        resolve_base(git_repo.path, "no-such-ref-anywhere", delegated=True)

    assert excinfo.value.reason_code is ReasonCode.GIT_FAILED


def test_a_failing_git_command_raises_git_failed(git_repo):
    with pytest.raises(AssayError) as excinfo:
        run(git_repo.path, "rev-parse", "--verify", "refs/heads/does-not-exist")

    assert excinfo.value.outcome is Outcome.ERROR
    assert excinfo.value.reason_code is ReasonCode.GIT_FAILED
    assert excinfo.value.exit_code == 2
    assert "rev-parse" in str(excinfo.value)


def test_resolve_base_propagates_a_failing_git_command(git_repo):
    with pytest.raises(AssayError) as excinfo:
        resolve_base(git_repo.path, "no-such-ref-anywhere")

    assert excinfo.value.reason_code is ReasonCode.GIT_FAILED


def test_head_rev_matches_an_independent_rev_parse(git_repo):
    expected = git_repo.git("rev-parse", "HEAD").strip()
    assert head_rev(git_repo.path) == expected


def test_repo_top_resolves_from_a_subdirectory(git_repo):
    nested = git_repo.path / "a" / "b"
    nested.mkdir(parents=True)
    git_repo.write("a/b/f.py", "x = 1\n")
    git_repo.commit_all("add nested file")

    assert repo_top(nested) == git_repo.path.resolve()
