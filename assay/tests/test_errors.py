"""The closed outcome / reason-code vocabulary (A-021, A-022, A-050, A-051).

These assertions are transcribed from DESIGN-GUIDE §6's two tables. They exist
so that a later package that needs a code which is not there fails a test rather
than inventing one.
"""

from __future__ import annotations

import pytest

from assay.errors import (
    EXIT_CODES,
    REASON_CODES,
    AssayError,
    LaneConfigError,
    Outcome,
    ReasonCode,
)

#: DESIGN-GUIDE §6, "Six outcomes", transcribed.
EXPECTED_EXIT_CODES = {
    "PASS": 0,
    "FAIL": 1,
    "ERROR": 2,
    "NO_MEASUREMENT": 3,
    "BUDGET_EXCEEDED": 4,
    "INCONCLUSIVE": 5,
}

#: DESIGN-GUIDE §6, the `reason_code` table, transcribed. A-050 closed the
#: BUDGET_EXCEEDED row (`LANE_TIMEOUT`), which the guide's table omitted.
EXPECTED_REASON_CODES = {
    "PASS": set(),
    "FAIL": {
        "UNCOVERED_LINES",
        # wave-1 §4/A-258: a floor missed purely because of branches, ranked
        # identically to UNCOVERED_LINES but never the same sentence.
        "UNCOVERED_BRANCHES",
        "EXCLUDED_LINES",
        "UNCLASSIFIED_LINES",
        "MUTANTS_SURVIVED",
        "CANARY_SURVIVED",
        "COMMAND_FAILED",
    },
    "ERROR": {
        "GIT_FAILED",
        "UNREADABLE_ARTIFACT",
        "FORMAT_MISMATCH",
        "BAD_LANE_CONFIG",
        "EXEC_FAILED",
        # P21: assay cannot write its OWN output (A-O14/A-181), and a
        # syntax-aware discovery BOUNDARY failed (A-171).
        "OUTPUT_WRITE_FAILED",
        "MUTATION_DISCOVERY_FAILED",
    },
    "NO_MEASUREMENT": {
        # wave-1 §4/A-259: judge.require_branch=true over an "unavailable"
        # branch capability.
        "BRANCH_UNAVAILABLE",
        # wave-1 §5/A-260: B005's anti-vacuity guard -- a whole_target target
        # absent from the artifact, or measured with zero executable lines.
        "TARGET_NOT_MEASURED",
        "DIRTY_TREE",
        # P21/A-178: a clean tree whose HEAD moved is not dirty.
        "HEAD_CHANGED",
        "BASE_IS_HEAD",
        "EMPTY_COVERAGE",
        "MISSING_ATTESTATION",
        "STALE_ATTESTATION",
        # P21/A-163, reserved for P27's first real external-tool preflight.
        "MISSING_EXTERNAL_TOOL",
    },
    "BUDGET_EXCEEDED": {
        "LANE_TIMEOUT",
        # P21/A-163: a refusal BEFORE submission, and P22's snapshot bound.
        "MUTANT_LIMIT_EXCEEDED",
        "SNAPSHOT_LIMIT_EXCEEDED",
    },
    "INCONCLUSIVE": {
        "NO_MUTANTS",
        # P21/A-183: the adapter has no mutation engine at all -- payload-
        # free, and deliberately NOT NO_MUTANTS, which asserts that a
        # supported analysis ran and observed nothing.
        "MUTATION_UNSUPPORTED",
        # P33/A-223d: every attempted mutant was PROVEN inert. Deliberately
        # neither PASS (a run in which nothing could have been caught proves
        # nothing about the tests -- A-026/A-035's 0/0-is-100% bug one layer
        # down) nor NO_MUTANTS (candidates were found AND run).
        "ALL_MUTANTS_EQUIVALENT",
        "CANARY_INCONCLUSIVE",
    },
}


def test_six_outcomes_carry_exactly_the_declared_exit_codes():
    assert {o.value: o.exit_code for o in Outcome} == EXPECTED_EXIT_CODES
    assert {o.value: EXIT_CODES[o] for o in Outcome} == EXPECTED_EXIT_CODES


def test_every_outcome_but_pass_blocks_a_merge():
    not_a_pass = [o for o in Outcome if o is not Outcome.PASS]
    assert len(not_a_pass) == 5
    assert all(o.exit_code != 0 for o in not_a_pass)


def test_reason_code_table_matches_the_design_guide():
    actual = {
        outcome.value: {code.value for code in codes}
        for outcome, codes in REASON_CODES.items()
    }
    assert actual == EXPECTED_REASON_CODES


def test_pass_has_no_reason_code_at_all():
    # A-051: omitted, never null. An empty allowed-set is how that is enforced.
    assert REASON_CODES[Outcome.PASS] == frozenset()


def test_every_reason_code_belongs_to_exactly_one_outcome():
    seen: dict[ReasonCode, Outcome] = {}
    for outcome, codes in REASON_CODES.items():
        for code in codes:
            assert code not in seen, f"{code} claimed by {seen.get(code)} and {outcome}"
            seen[code] = outcome
    assert set(seen) == set(ReasonCode)


def test_error_cannot_carry_outcome_pass():
    with pytest.raises(ValueError, match="cannot carry Outcome.PASS"):
        AssayError(
            "green", outcome=Outcome.PASS, reason_code=ReasonCode.UNCOVERED_LINES
        )


def test_error_rejects_a_reason_code_from_another_outcome():
    with pytest.raises(ValueError, match="not valid for outcome"):
        AssayError(
            "wrong", outcome=Outcome.ERROR, reason_code=ReasonCode.DIRTY_TREE
        )


def test_error_accepts_a_matching_pair_and_exposes_the_exit_code():
    err = AssayError(
        "tree is dirty",
        outcome=Outcome.NO_MEASUREMENT,
        reason_code=ReasonCode.DIRTY_TREE,
    )
    assert err.outcome is Outcome.NO_MEASUREMENT
    assert err.reason_code is ReasonCode.DIRTY_TREE
    assert err.exit_code == 3
    assert str(err) == "tree is dirty"
    assert err.message == "tree is dirty"


def test_lane_config_error_is_error_bad_lane_config():
    err = LaneConfigError("assay.toml: lane 'x': missing required field 'budget'")
    assert isinstance(err, AssayError)
    assert err.outcome is Outcome.ERROR
    assert err.reason_code is ReasonCode.BAD_LANE_CONFIG
    assert err.exit_code == 2
    assert "budget" in str(err)
