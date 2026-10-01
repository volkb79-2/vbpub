"""Wave-1 (A-257/A-259/A-260, B006(a)/A-269) -- direct MODEL-level tests for
the new construction-time rules on :class:`~assay.verdict.Coverage`'s five
branch fields, :class:`~assay.verdict.JudgmentR1`'s ``mode``/``targets``/
``require_branch``, :class:`~assay.verdict.SnapshotPolicy`, and the two new
:class:`~assay.verdict.Claim`/:class:`~assay.verdict.Verdict` cross-field
rules those additions introduce.

Every negative here is paired with the untouched form building successfully,
the same discipline every other ``test_verdict_*`` module in this suite
applies (a model that refuses everything would pass a bare
``pytest.raises`` alone).
"""

from __future__ import annotations

import pytest

from assay.errors import Outcome, ReasonCode
from assay.verdict import Claim, Coverage, JudgmentR1, SnapshotPolicy, Verdict

# --- Coverage: the branch fields ----------------------------------------------

COVERAGE_BASE = {
    "covered": 1,
    "executable": 2,
    "pct": 50.0,
    "considered": 1,
    "exclusion_capability": "reported",
    "missing_lines": {"src/mod.py": frozenset({3})},
    "files_missing_coverage": (),
}


def test_coverage_with_no_branch_fields_defaults_to_unavailable():
    coverage = Coverage(**COVERAGE_BASE)
    assert coverage.branch_capability == "unavailable"
    assert coverage.branches_covered == 0
    assert coverage.branches_total == 0
    assert coverage.missing_branch_lines == {}
    assert coverage.files_with_missing_branch_lines == ()


def test_coverage_branch_capability_must_be_a_known_value():
    with pytest.raises(ValueError, match="branch_capability must be one of"):
        Coverage(**{**COVERAGE_BASE, "branch_capability": "bogus"})


def test_coverage_branches_covered_cannot_exceed_branches_total():
    with pytest.raises(ValueError, match="branches_covered.*exceeds"):
        Coverage(
            **{
                **COVERAGE_BASE,
                "covered": 2,
                "pct": 100.0 * 3 / 4,
                "branch_capability": "reported",
                "branches_covered": 3,
                "branches_total": 2,
            }
        )


@pytest.mark.parametrize(
    "overrides",
    [
        # Each case stays internally consistent with every OTHER Coverage
        # invariant (pct arithmetic, covered<=total, summary/detail
        # agreement) so the ONLY rule it can trip is "unavailable carries
        # no branch detail" -- never an unrelated earlier check.
        {"branches_total": 2, "pct": 25.0},
        {"branches_covered": 1, "branches_total": 1, "pct": 100.0 * 2 / 3},
        {
            "missing_branch_lines": {"src/mod.py": frozenset({3})},
            "files_with_missing_branch_lines": ("src/mod.py",),
        },
    ],
)
def test_unavailable_branch_capability_carries_no_branch_detail(overrides: dict):
    with pytest.raises(ValueError, match="branch_capability is 'unavailable' but"):
        Coverage(**{**COVERAGE_BASE, "branch_capability": "unavailable", **overrides})


def test_a_reported_branch_capability_with_real_arcs_is_legal():
    coverage = Coverage(
        **{
            **COVERAGE_BASE,
            "covered": 2,
            "missing_lines": {},
            "pct": 100.0 * 3 / 4,
            "branch_capability": "reported",
            "branches_covered": 1,
            "branches_total": 2,
            "missing_branch_lines": {"src/mod.py": frozenset({3})},
            "files_with_missing_branch_lines": ("src/mod.py",),
        }
    )
    assert coverage.branches_total == 2


# --- JudgmentR1: mode/targets/require_branch -----------------------------------

R1_BASE = {
    "coverage_format": "coverage-py-json",
    "coverage_artifact": "cov.json",
    "fail_under": 100.0,
    "allow_excluded": False,
}


def test_judgment_r1_defaults_to_changed_lines_no_targets_no_require_branch():
    r1 = JudgmentR1(**R1_BASE)
    assert r1.mode == "changed_lines"
    assert r1.targets is None
    assert r1.require_branch is False


def test_judgment_r1_mode_must_be_a_known_value():
    with pytest.raises(ValueError, match="mode must be one of"):
        JudgmentR1(**R1_BASE, mode="bogus")


def test_judgment_r1_require_branch_must_be_a_bool():
    with pytest.raises(ValueError, match="require_branch must be a boolean"):
        JudgmentR1(**R1_BASE, require_branch="true")  # type: ignore[arg-type]


def test_judgment_r1_whole_target_requires_a_non_empty_targets_tuple():
    with pytest.raises(ValueError, match="non-empty tuple"):
        JudgmentR1(**R1_BASE, mode="whole_target")


def test_judgment_r1_whole_target_with_an_empty_targets_tuple_is_refused():
    with pytest.raises(ValueError, match="non-empty tuple"):
        JudgmentR1(**R1_BASE, mode="whole_target", targets=())


def test_judgment_r1_whole_target_targets_must_be_unique():
    with pytest.raises(ValueError, match="duplicate"):
        JudgmentR1(
            **R1_BASE, mode="whole_target", targets=("src/a.py", "src/a.py")
        )


def test_judgment_r1_whole_target_targets_must_be_sorted():
    with pytest.raises(ValueError, match="must be sorted"):
        JudgmentR1(
            **R1_BASE, mode="whole_target", targets=("src/b.py", "src/a.py")
        )


def test_judgment_r1_whole_target_with_valid_targets_is_legal():
    r1 = JudgmentR1(
        **R1_BASE, mode="whole_target", targets=("src/a.py", "src/b.py")
    )
    assert r1.targets == ("src/a.py", "src/b.py")


def test_judgment_r1_targets_present_outside_whole_target_mode_is_refused():
    with pytest.raises(ValueError, match="targets describes nothing outside"):
        JudgmentR1(**R1_BASE, mode="changed_lines", targets=("src/a.py",))


# --- SnapshotPolicy -------------------------------------------------------------


def test_snapshot_policy_selection_must_be_a_known_value():
    with pytest.raises(ValueError, match="selection must be one of"):
        SnapshotPolicy(selection="bogus")


def test_snapshot_policy_repository_forbids_omissions():
    with pytest.raises(ValueError, match="forbidden"):
        SnapshotPolicy(selection="repository", unsafe_symlink_omissions=("a",))


def test_snapshot_policy_omission_mode_requires_a_nonempty_tuple():
    with pytest.raises(ValueError, match="must be a tuple of 1..64"):
        SnapshotPolicy(selection="repository-minus-unsafe-symlinks")


def test_snapshot_policy_omission_mode_refuses_a_dot_git_component():
    with pytest.raises(ValueError, match="'.git' component"):
        SnapshotPolicy(
            selection="repository-minus-unsafe-symlinks",
            unsafe_symlink_omissions=("a/.git/b",),
        )


def test_snapshot_policy_omission_entries_must_fit_4096_utf8_bytes():
    with pytest.raises(ValueError, match="4096 UTF-8 bytes"):
        SnapshotPolicy(
            selection="repository-minus-unsafe-symlinks",
            unsafe_symlink_omissions=("a" * 4097,),
        )


def test_snapshot_policy_omissions_must_be_strictly_ascending():
    with pytest.raises(ValueError, match="strictly ascending"):
        SnapshotPolicy(
            selection="repository-minus-unsafe-symlinks",
            unsafe_symlink_omissions=("z", "a"),
        )


def test_snapshot_policy_valid_omissions_round_trip_through_to_dict():
    policy = SnapshotPolicy(
        selection="repository-minus-unsafe-symlinks",
        unsafe_symlink_omissions=("a/b", "z"),
    )
    assert policy.to_dict() == {
        "selection": "repository-minus-unsafe-symlinks",
        "unsafe_symlink_omissions": ["a/b", "z"],
    }


def test_snapshot_policy_repository_to_dict_omits_omissions():
    assert SnapshotPolicy(selection="repository").to_dict() == {
        "selection": "repository"
    }


# --- Claim: BRANCH_UNAVAILABLE/TARGET_NOT_MEASURED are bound to R1 -----------


@pytest.mark.parametrize(
    "reason", [ReasonCode.BRANCH_UNAVAILABLE, ReasonCode.TARGET_NOT_MEASURED]
)
@pytest.mark.parametrize("foreign_rigor", ["R0", "R2", "R3"])
def test_the_two_new_r1_terminals_are_refused_on_a_non_r1_claim(
    reason: ReasonCode, foreign_rigor: str
):
    with pytest.raises(ValueError, match="belongs to the R1 claim"):
        Claim(
            rigor=foreign_rigor,
            source="computed",
            status=Outcome.NO_MEASUREMENT,
            verified_by_assay=True,
            reason_code=reason,
        )


@pytest.mark.parametrize(
    "reason", [ReasonCode.BRANCH_UNAVAILABLE, ReasonCode.TARGET_NOT_MEASURED]
)
def test_the_two_new_r1_terminals_are_legal_on_an_r1_claim(reason: ReasonCode):
    claim = Claim(
        rigor="R1",
        source="computed",
        status=Outcome.NO_MEASUREMENT,
        verified_by_assay=True,
        reason_code=reason,
    )
    assert claim.reason_code is reason


# --- Verdict: snapshot_policy <-> declared_rigor correspondence --------------

VERDICT_BASE = dict(
    lane="package",
    commit="c" * 40,
    assay_version="0.2.0",
    argv_declared=("pytest", "-q"),
    argv_appended=(),
    argv_effective=("pytest", "-q"),
    env_declared={},
    env_effective={},
    scope="S1",
    enforcement="gate",
    declared_evidence=(),
    started="2026-08-09T11:00:00+00:00",
    ended="2026-08-09T11:00:01+00:00",
)

R0_PASS = Claim(rigor="R0", source="computed", status=Outcome.PASS, verified_by_assay=True)


def test_higher_rigor_declared_without_a_snapshot_policy_is_refused():
    r1_no_measurement = Claim(
        rigor="R1",
        source="computed",
        status=Outcome.NO_MEASUREMENT,
        verified_by_assay=True,
        reason_code=ReasonCode.DIRTY_TREE,
    )
    with pytest.raises(ValueError, match="every R1/R2/R3 lane resolves"):
        Verdict(
            **VERDICT_BASE,
            declared_rigor=("R0", "R1"),
            outcome=Outcome.NO_MEASUREMENT,
            reason_code=ReasonCode.DIRTY_TREE,
            claims=(R0_PASS, r1_no_measurement),
        )


def test_r0_only_with_a_snapshot_policy_present_is_refused():
    with pytest.raises(ValueError, match="never selects a snapshot policy"):
        Verdict(
            **VERDICT_BASE,
            declared_rigor=("R0",),
            outcome=Outcome.PASS,
            claims=(R0_PASS,),
            snapshot_policy=SnapshotPolicy(selection="repository"),
        )
