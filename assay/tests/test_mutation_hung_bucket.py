"""B091/RW-33, P7 A3 -- the `hung` bucket's own classification and closed-
vocabulary threading: :func:`~assay.mutation._classify_mutant_result` and
:func:`~assay.mutation._classify_mutant_result_with_equivalence` must map a
`BUDGET_EXCEEDED` result with `reason_code = CANDIDATE_HUNG` to `"hung"`, and
`LANE_TIMEOUT` (or any other `BUDGET_EXCEEDED` reason) to the unchanged
`"budget_exceeded"` -- the exact `LivenessHungExpired`-vs-plain-
`TimeoutExpired` distinction `runner._execute_plan_inner` makes, one layer
up.

Kept as a direct UNIT test against the two classifier functions (never a
real subprocess, never a real mutation sweep -- that belongs to
`test_cli_run.py`'s real end-to-end fixture) so this module answers exactly
one question: given a `CommandResult`, which bucket name comes out.
"""

from __future__ import annotations

from datetime import datetime, timezone

from conftest import make_lane, make_plan

from assay.errors import Outcome, ReasonCode
from assay.mutation import _classify_mutant_result, _classify_mutant_result_with_equivalence
from assay.runner import CommandResult

_MOMENT = datetime(2026, 9, 12, 0, 0, 0, tzinfo=timezone.utc)


def _result(outcome: Outcome, reason_code: ReasonCode | None) -> CommandResult:
    lane = make_lane()
    plan = make_plan(lane, project_prefix=".")
    return CommandResult(
        plan=plan,
        outcome=outcome,
        reason_code=reason_code,
        returncode=None,
        started=_MOMENT.isoformat(),
        ended=_MOMENT.isoformat(),
    )


# --------------------------------------------------------------------------
# _classify_mutant_result
# --------------------------------------------------------------------------


def test_candidate_hung_reason_code_classifies_as_hung() -> None:
    result = _result(Outcome.BUDGET_EXCEEDED, ReasonCode.CANDIDATE_HUNG)
    assert _classify_mutant_result(result) == "hung"


def test_lane_timeout_reason_code_still_classifies_as_budget_exceeded() -> None:
    """The OTHER optional value at its default (BRIEF-1's own lesson): a
    genuine elapsed-budget expiry (`LANE_TIMEOUT`, the reason every
    `BUDGET_EXCEEDED` result carried before this session) must NOT be
    reclassified -- the direct regression test for "did the `hung` branch
    accidentally swallow the pre-existing case".
    """
    result = _result(Outcome.BUDGET_EXCEEDED, ReasonCode.LANE_TIMEOUT)
    assert _classify_mutant_result(result) == "budget_exceeded"


def test_other_outcomes_unaffected_by_the_hung_branch() -> None:
    assert _classify_mutant_result(_result(Outcome.PASS, None)) == "survived"
    assert _classify_mutant_result(_result(Outcome.FAIL, ReasonCode.COMMAND_FAILED)) == "killed"
    assert _classify_mutant_result(_result(Outcome.ERROR, ReasonCode.EXEC_FAILED)) == "crashed"


# --------------------------------------------------------------------------
# _classify_mutant_result_with_equivalence -- same split, equivalence-blind
# --------------------------------------------------------------------------


def test_candidate_hung_with_equivalence_lane_still_classifies_as_hung() -> None:
    result = _result(Outcome.BUDGET_EXCEEDED, ReasonCode.CANDIDATE_HUNG)
    bucket = _classify_mutant_result_with_equivalence(
        result, equivalence_bytes=b"whatever", baseline_equivalence=b"whatever"
    )
    assert bucket == "hung"


def test_lane_timeout_with_equivalence_lane_still_budget_exceeded() -> None:
    result = _result(Outcome.BUDGET_EXCEEDED, ReasonCode.LANE_TIMEOUT)
    bucket = _classify_mutant_result_with_equivalence(
        result, equivalence_bytes=None, baseline_equivalence=b"x"
    )
    assert bucket == "budget_exceeded"
