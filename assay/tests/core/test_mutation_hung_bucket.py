"""B091/RW-33, P7 A3 -- the `hung` bucket's own classification and closed-
vocabulary threading: :func:`~assay.mutation._bucket_for_run` and
:func:`~assay.mutation._equivalence_bucket` must map a
`BUDGET_EXCEEDED` result with `reason_code = CANDIDATE_HUNG` to `"hung"`, and
`LANE_TIMEOUT` (or any other `BUDGET_EXCEEDED` reason) to the unchanged
`"budget_exceeded"` -- the exact `LivenessHungExpired`-vs-plain-
`TimeoutExpired` distinction `runner._execute_plan_inner` makes, one layer
up.

Kept as a direct UNIT test against the shared bucket helper (never a
real subprocess, never a real mutation sweep). The liveness monitor's
clock/process/resource decisions are covered by deterministic injected
observations in `test_liveness_runner_monitor.py`; the mutation pipeline's
bucket and evidence persistence are covered by
`test_mutation_hung_evidence_persistence.py`. This module answers exactly
one question: given a `CommandResult`, which bucket name comes out.
"""

from __future__ import annotations

from datetime import datetime, timezone

from conftest import make_lane, make_plan

from assay.errors import Outcome, ReasonCode
from assay.mutation import (
    _MutantRun,
    _bucket_for_run,
    _equivalence_bucket,
)
from assay.process_tree import ProcessTreeEvidence
from assay.resource_limits import CounterDelta, ResourceLimitEvidence
from assay.runner import CommandResult
from assay.termination import Termination

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


def _run(result: CommandResult) -> _MutantRun:
    return _MutantRun(
        result=result,
        resource_limit_evidence=ResourceLimitEvidence(
            pids_events_max=CounterDelta(before=0, after=0, delta=0),
            memory_events_max=CounterDelta(before=0, after=0, delta=0),
            memory_events_oom=CounterDelta(before=0, after=0, delta=0),
            memory_events_oom_kill=CounterDelta(before=0, after=0, delta=0),
            memory_events_oom_group_kill=CounterDelta(before=0, after=0, delta=0),
        ),
        process_tree_evidence=ProcessTreeEvidence(
            leader_status="exited",
            group_signalled_before_reap=False,
            outliving_descendants=0,
            descendants_signalled=0,
            cleanup_seconds=0.0,
            clean=True,
        ),
    )


def _bucket(result: CommandResult) -> str:
    return _bucket_for_run(
        _run(result),
        equivalence_artifact=None,
        baseline_equivalence=None,
        kill_signal_artifact=None,
    )


# --------------------------------------------------------------------------
# _bucket_for_run
# --------------------------------------------------------------------------


def test_candidate_hung_reason_code_classifies_as_hung() -> None:
    result = _result(Outcome.BUDGET_EXCEEDED, ReasonCode.CANDIDATE_HUNG)
    assert _bucket(result) == "hung"


def test_lane_timeout_reason_code_still_classifies_as_budget_exceeded() -> None:
    """The OTHER optional value at its default (BRIEF-1's own lesson): a
    genuine elapsed-budget expiry (`LANE_TIMEOUT`, the reason every
    `BUDGET_EXCEEDED` result carried before this session) must NOT be
    reclassified -- the direct regression test for "did the `hung` branch
    accidentally swallow the pre-existing case".
    """
    result = _result(Outcome.BUDGET_EXCEEDED, ReasonCode.LANE_TIMEOUT)
    assert _bucket(result) == "budget_exceeded"


def test_other_outcomes_unaffected_by_the_hung_branch() -> None:
    assert _bucket(_result(Outcome.PASS, None)) == "survived"
    assert _bucket(_result(Outcome.FAIL, ReasonCode.COMMAND_FAILED)) == "killed"
    assert _bucket(_result(Outcome.ERROR, ReasonCode.EXEC_FAILED)) == "crashed"


# --------------------------------------------------------------------------
# _equivalence_bucket -- same split, equivalence-blind
# --------------------------------------------------------------------------


def test_candidate_hung_with_equivalence_lane_still_classifies_as_hung() -> None:
    bucket = _equivalence_bucket(
        Termination.HUNG,
        equivalence_bytes=b"whatever",
        baseline_equivalence=b"whatever",
    )
    assert bucket == "hung"


def test_lane_timeout_with_equivalence_lane_still_budget_exceeded() -> None:
    bucket = _equivalence_bucket(
        Termination.TIMED_OUT,
        equivalence_bytes=None,
        baseline_equivalence=b"x",
    )
    assert bucket == "budget_exceeded"
