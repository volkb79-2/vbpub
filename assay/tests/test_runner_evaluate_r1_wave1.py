"""Wave-1 §4/§5 (A-258/A-259/A-260) -- the two additions to
:func:`assay.runner.evaluate_r1`'s own guard sequence this suite's sibling,
``test_runner_evaluate_r1.py``, predates: mode dispatch (``changed_lines`` vs
``whole_target``) and the ``require_branch`` measurability guard.

1. Whole-target dispatch never resolves a base or a diff at all -- proven by
   passing both `on_base_resolved`/`on_added_resolved` callbacks and a
   *base* string that would make a real ``check_base_is_head`` git call
   fail if it were ever reached, and asserting neither callback fires.
2. ``require_branch`` is checked ONCE, before mode dispatch, so it renders
   ``NO_MEASUREMENT``/``BRANCH_UNAVAILABLE`` regardless of which mode was
   declared -- proven for `changed_lines` here (whole_target's own dispatch
   is covered by the first test above; the guard itself does not care which
   mode follows it, per its own placement in the source).
3. `evaluate_targets`'s own anti-vacuity refusal (`TARGET_NOT_MEASURED`)
   propagates through this function's `except AssayError` catch into a
   real `NO_MEASUREMENT` claim, exactly like every other guard here.
"""

from __future__ import annotations

from conftest import FakeAdapter, GitRepo, make_lane, make_r1_judge, write_coverage_json

from assay import runner
from assay.errors import Outcome, ReasonCode

ADAPTER = FakeAdapter()


def _seed_one_commit(repo: GitRepo) -> str:
    repo.write("pkg/mod.zzz", "BASE\nLINE2\n")
    return repo.commit_all("add pkg")


# --- whole-target dispatch: no base, no diff --------------------------------


def test_whole_target_mode_passes_without_ever_resolving_a_base_or_diff(
    git_repo: GitRepo,
):
    _seed_one_commit(git_repo)
    write_coverage_json(
        git_repo.path / "cov.json",
        {"pkg/mod.zzz": {"executed_lines": [1, 2]}},
    )
    judge = make_r1_judge(
        source_root_paths=(git_repo.path / "pkg",),
        mode="whole_target",
        targets=("pkg/mod.zzz",),
    )
    lane = make_lane(rigor=("R0", "R1"), judge=judge)
    base_seen: list[str] = []
    added_seen: list[object] = []

    claim = runner.evaluate_r1(
        lane,
        repo=git_repo.path,
        project_root=git_repo.path,
        # A ref that does not exist: if whole-target dispatch ever called
        # `check_base_is_head` on this, the real `git merge-base`/`rev-
        # parse` call underneath it would fail loudly (GIT_FAILED), not
        # silently succeed -- so a PASS below is itself part of the proof
        # that base resolution never ran.
        base="not-a-real-ref-at-all",
        adapter=ADAPTER,
        on_base_resolved=base_seen.append,
        on_added_resolved=added_seen.append,
    )

    assert claim.status is Outcome.PASS
    assert claim.reason_code is None
    assert base_seen == []
    assert added_seen == []
    assert claim.coverage is not None
    assert claim.coverage.considered == 1  # declared TARGETS judged, §5 rule 6


def test_whole_target_mode_fails_uncovered_lines_from_the_real_target_content(
    git_repo: GitRepo,
):
    _seed_one_commit(git_repo)
    write_coverage_json(
        git_repo.path / "cov.json",
        {"pkg/mod.zzz": {"executed_lines": [1], "missing_lines": [2]}},
    )
    judge = make_r1_judge(
        source_root_paths=(git_repo.path / "pkg",),
        mode="whole_target",
        targets=("pkg/mod.zzz",),
    )
    lane = make_lane(rigor=("R0", "R1"), judge=judge)

    claim = runner.evaluate_r1(
        lane,
        repo=git_repo.path,
        project_root=git_repo.path,
        base=None,
        adapter=ADAPTER,
    )

    assert claim.status is Outcome.FAIL
    assert claim.reason_code is ReasonCode.UNCOVERED_LINES


# --- require_branch: checked once, before mode dispatch ----------------------


def test_require_branch_renders_branch_unavailable_before_mode_dispatch(
    git_repo: GitRepo,
):
    base_rev = _seed_one_commit(git_repo)
    git_repo.write("pkg/mod.zzz", "BASE\nLINE2\nLINE3\n")
    head_rev = git_repo.commit_all("extend pkg")
    # No `executed_branches`/`missing_branches` at all -- `derive_branch_
    # capability` reads this as `"unavailable"` for the whole artifact.
    write_coverage_json(
        git_repo.path / "cov.json",
        {"pkg/mod.zzz": {"executed_lines": [2, 3]}},
    )
    judge = make_r1_judge(
        source_root_paths=(git_repo.path / "pkg",), require_branch=True,
    )
    lane = make_lane(rigor=("R0", "R1"), judge=judge)

    claim = runner.evaluate_r1(
        lane,
        repo=git_repo.path,
        project_root=git_repo.path,
        base=base_rev,
        adapter=ADAPTER,
    )

    assert claim.status is Outcome.NO_MEASUREMENT
    assert claim.reason_code is ReasonCode.BRANCH_UNAVAILABLE
    assert claim.coverage is None  # NO_MEASUREMENT is payload-free (A-025)


def test_require_branch_false_is_unaffected_by_an_unavailable_artifact(
    git_repo: GitRepo,
):
    """The control for the negative above: the identical artifact, with
    `require_branch` merely absent, PASSES on lines alone -- proving the
    guard, not the artifact, is what refused."""
    base_rev = _seed_one_commit(git_repo)
    git_repo.write("pkg/mod.zzz", "BASE\nLINE2\nLINE3\n")
    head_rev = git_repo.commit_all("extend pkg")
    write_coverage_json(
        git_repo.path / "cov.json",
        {"pkg/mod.zzz": {"executed_lines": [2, 3]}},
    )
    judge = make_r1_judge(source_root_paths=(git_repo.path / "pkg",))
    lane = make_lane(rigor=("R0", "R1"), judge=judge)

    claim = runner.evaluate_r1(
        lane,
        repo=git_repo.path,
        project_root=git_repo.path,
        base=base_rev,
        adapter=ADAPTER,
    )

    assert claim.status is Outcome.PASS
    assert claim.coverage is not None
    assert claim.coverage.branch_capability == "unavailable"


# --- TARGET_NOT_MEASURED propagates through the same except AssayError ------


def test_a_target_absent_from_the_artifact_renders_target_not_measured(
    git_repo: GitRepo,
):
    _seed_one_commit(git_repo)
    # A non-empty profile that simply never mentions the declared target --
    # `check_empty_coverage`'s own guard runs BEFORE mode dispatch and would
    # otherwise refuse a wholly empty artifact with EMPTY_COVERAGE first,
    # which would prove nothing about THIS guard.
    write_coverage_json(
        git_repo.path / "cov.json", {"pkg/unrelated.zzz": {"executed_lines": [1]}}
    )
    judge = make_r1_judge(
        source_root_paths=(git_repo.path / "pkg",),
        mode="whole_target",
        targets=("pkg/mod.zzz",),
    )
    lane = make_lane(rigor=("R0", "R1"), judge=judge)

    claim = runner.evaluate_r1(
        lane,
        repo=git_repo.path,
        project_root=git_repo.path,
        base=None,
        adapter=ADAPTER,
    )

    assert claim.status is Outcome.NO_MEASUREMENT
    assert claim.reason_code is ReasonCode.TARGET_NOT_MEASURED
    assert claim.coverage is None
