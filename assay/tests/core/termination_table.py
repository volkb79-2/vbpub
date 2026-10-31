"""Shared decision rows for termination consumers and verifier checks."""

from __future__ import annotations

import signal
from dataclasses import dataclass

from assay.errors import Outcome, ReasonCode
from assay.process_tree import ProcessTreeEvidence
from assay.resource_limits import CounterDelta, ResourceLimitEvidence
from assay.termination import BLOCKING_JUDGEABLE, Termination


def _tree(
    *, leader_status: str = "exited", outliving_descendants: int = 0
) -> ProcessTreeEvidence:
    return ProcessTreeEvidence(
        leader_status=leader_status,
        group_signalled_before_reap=False,
        outliving_descendants=outliving_descendants,
        descendants_signalled=outliving_descendants,
        cleanup_seconds=0.0,
        clean=True,
    )


def _limits(*, hit: bool = False) -> ResourceLimitEvidence:
    pids = int(hit)
    return ResourceLimitEvidence(
        pids_events_max=CounterDelta(before=0, after=pids, delta=pids),
        memory_events_max=CounterDelta(before=0, after=0, delta=0),
        memory_events_oom=CounterDelta(before=0, after=0, delta=0),
        memory_events_oom_kill=CounterDelta(before=0, after=0, delta=0),
        memory_events_oom_group_kill=CounterDelta(before=0, after=0, delta=0),
    )


LIMITS_HIT = _limits(hit=True)
LIMITS_CLEAR = _limits()


_R2_BUCKET = {
    Termination.PASSED: "survived",
    Termination.FAILED: "killed",
    Termination.TIMED_OUT: "budget_exceeded",
    Termination.HUNG: "hung",
    Termination.NOT_RUN: "crashed",
    Termination.KILLED_EXTERNALLY: "crashed",
    Termination.SELF_SIGNALED: "crashed",
    Termination.LEAKED: "crashed",
    Termination.LIMIT_EVENT: "crashed",
    Termination.UNOBSERVED: "crashed",
    Termination.INCONCLUSIVE: "crashed",
}

_R3_ATTEMPT = {
    # A PASS means the transformed suite accepted the injected defect.
    Termination.PASSED: (Outcome.FAIL, ReasonCode.CANARY_SURVIVED),
    # The FAILED row assumes the expected reason and (for import-break) receipt.
    Termination.FAILED: (Outcome.PASS, None),
    Termination.TIMED_OUT: (Outcome.INCONCLUSIVE, ReasonCode.CANARY_INCONCLUSIVE),
    Termination.HUNG: (Outcome.INCONCLUSIVE, ReasonCode.CANARY_INCONCLUSIVE),
    Termination.NOT_RUN: (Outcome.INCONCLUSIVE, ReasonCode.CANARY_INCONCLUSIVE),
    Termination.KILLED_EXTERNALLY: (
        Outcome.INCONCLUSIVE,
        ReasonCode.CANARY_INCONCLUSIVE,
    ),
    Termination.SELF_SIGNALED: (
        Outcome.INCONCLUSIVE,
        ReasonCode.CANARY_INCONCLUSIVE,
    ),
    Termination.LEAKED: (Outcome.INCONCLUSIVE, ReasonCode.CANARY_INCONCLUSIVE),
    Termination.LIMIT_EVENT: (Outcome.INCONCLUSIVE, ReasonCode.CANARY_INCONCLUSIVE),
    Termination.UNOBSERVED: (Outcome.INCONCLUSIVE, ReasonCode.CANARY_INCONCLUSIVE),
    Termination.INCONCLUSIVE: (
        Outcome.INCONCLUSIVE,
        ReasonCode.CANARY_INCONCLUSIVE,
    ),
}


@dataclass(frozen=True)
class TerminationCase:
    name: str
    inputs: dict[str, object]
    termination: Termination
    r0_outcome: Outcome
    r0_reason_code: ReasonCode | None
    r2_bucket: str
    r3_attempt_outcome: Outcome
    r3_attempt_reason_code: ReasonCode | None


def r0_outcome(case: TerminationCase) -> tuple[Outcome, ReasonCode | None]:
    """Project the shared termination row onto R0's blocking outcome."""
    if case.termination is Termination.PASSED:
        return Outcome.PASS, None
    if case.termination in BLOCKING_JUDGEABLE:
        return Outcome.FAIL, ReasonCode.COMMAND_FAILED
    if case.termination in {Termination.TIMED_OUT, Termination.HUNG}:
        return case.inputs["outcome"], case.inputs["reason_code"]
    if case.termination is Termination.NOT_RUN and case.inputs["outcome"] is Outcome.NO_MEASUREMENT:
        return Outcome.NO_MEASUREMENT, ReasonCode.TARGET_NOT_MEASURED
    return Outcome.ERROR, ReasonCode.EXEC_FAILED


def _case(
    name: str,
    inputs: dict[str, object],
    termination: Termination,
    r0_outcome: Outcome,
    r0_reason_code: ReasonCode | None,
    *,
    r2_bucket_override: str | None = None,
) -> TerminationCase:
    r3_outcome, r3_reason_code = _R3_ATTEMPT[termination]
    return TerminationCase(
        name=name,
        inputs=inputs,
        termination=termination,
        r0_outcome=r0_outcome,
        r0_reason_code=r0_reason_code,
        r2_bucket=(
            _R2_BUCKET[termination]
            if r2_bucket_override is None
            else r2_bucket_override
        ),
        r3_attempt_outcome=r3_outcome,
        r3_attempt_reason_code=r3_reason_code,
    )


_DEFAULTS: dict[str, object] = {
    "reason_code": None,
    "returncode": 0,
    "decided_by_report": False,
    "process_tree": None,
    "limits": None,
    "observation": "not_requested",
    "observation_required": False,
}


def _inputs(**overrides: object) -> dict[str, object]:
    return {**_DEFAULTS, **overrides}


TERMINATION_CASES = (
    _case(
        "passed",
        _inputs(outcome=Outcome.PASS),
        Termination.PASSED,
        Outcome.PASS,
        None,
    ),
    _case(
        "pass-with-signaled-leader-is-crashed",
        _inputs(
            outcome=Outcome.PASS,
            process_tree=_tree(leader_status="signaled"),
            limits=_limits(),
        ),
        Termination.PASSED,
        Outcome.PASS,
        None,
        r2_bucket_override="crashed",
    ),
    _case(
        "failed",
        _inputs(
            outcome=Outcome.FAIL,
            reason_code=ReasonCode.COMMAND_FAILED,
            returncode=1,
            process_tree=_tree(),
            limits=_limits(),
        ),
        Termination.FAILED,
        Outcome.FAIL,
        ReasonCode.COMMAND_FAILED,
    ),
    _case(
        "self-signaled",
        _inputs(
            outcome=Outcome.FAIL,
            reason_code=ReasonCode.COMMAND_FAILED,
            returncode=-signal.SIGSEGV,
        ),
        Termination.SELF_SIGNALED,
        Outcome.FAIL,
        ReasonCode.COMMAND_FAILED,
    ),
    _case(
        "timed-out",
        _inputs(
            outcome=Outcome.BUDGET_EXCEEDED,
            reason_code=ReasonCode.LANE_TIMEOUT,
            returncode=None,
        ),
        Termination.TIMED_OUT,
        Outcome.BUDGET_EXCEEDED,
        ReasonCode.LANE_TIMEOUT,
    ),
    _case(
        "hung",
        _inputs(
            outcome=Outcome.BUDGET_EXCEEDED,
            reason_code=ReasonCode.CANDIDATE_HUNG,
            returncode=None,
        ),
        Termination.HUNG,
        Outcome.BUDGET_EXCEEDED,
        ReasonCode.CANDIDATE_HUNG,
    ),
    _case(
        "not-run-error",
        _inputs(
            outcome=Outcome.ERROR,
            reason_code=ReasonCode.EXEC_FAILED,
            returncode=None,
        ),
        Termination.NOT_RUN,
        Outcome.ERROR,
        ReasonCode.EXEC_FAILED,
    ),
    _case(
        "killed-externally",
        _inputs(
            outcome=Outcome.FAIL,
            reason_code=ReasonCode.COMMAND_FAILED,
            returncode=-signal.SIGKILL,
        ),
        Termination.KILLED_EXTERNALLY,
        Outcome.ERROR,
        ReasonCode.EXEC_FAILED,
    ),
    _case(
        "leaked",
        _inputs(
            outcome=Outcome.FAIL,
            reason_code=ReasonCode.COMMAND_FAILED,
            returncode=1,
            process_tree=_tree(outliving_descendants=1),
        ),
        Termination.LEAKED,
        Outcome.ERROR,
        ReasonCode.EXEC_FAILED,
    ),
    _case(
        "limit-event",
        _inputs(
            outcome=Outcome.FAIL,
            reason_code=ReasonCode.COMMAND_FAILED,
            returncode=1,
            limits=LIMITS_HIT,
        ),
        Termination.LIMIT_EVENT,
        Outcome.ERROR,
        ReasonCode.EXEC_FAILED,
    ),
    _case(
        "unobserved",
        _inputs(
            outcome=Outcome.FAIL,
            reason_code=ReasonCode.COMMAND_FAILED,
            returncode=1,
            observation="unavailable",
            observation_required=True,
        ),
        Termination.UNOBSERVED,
        Outcome.ERROR,
        ReasonCode.EXEC_FAILED,
    ),
    _case(
        "inconclusive",
        _inputs(
            outcome=Outcome.INCONCLUSIVE,
            reason_code=None,
            returncode=1,
        ),
        Termination.INCONCLUSIVE,
        Outcome.ERROR,
        ReasonCode.EXEC_FAILED,
    ),
)


PRECEDENCE_CASES = (
    _case(
        "leak-beats-pass",
        _inputs(
            outcome=Outcome.PASS,
            process_tree=_tree(outliving_descendants=1),
        ),
        Termination.LEAKED,
        Outcome.ERROR,
        ReasonCode.EXEC_FAILED,
    ),
    _case(
        "leak-beats-not-run",
        _inputs(
            outcome=Outcome.NO_MEASUREMENT,
            reason_code=ReasonCode.TARGET_NOT_MEASURED,
            returncode=None,
            process_tree=_tree(outliving_descendants=1),
        ),
        Termination.LEAKED,
        Outcome.ERROR,
        ReasonCode.EXEC_FAILED,
    ),
    _case(
        "leak-beats-timeout",
        _inputs(
            outcome=Outcome.BUDGET_EXCEEDED,
            reason_code=ReasonCode.LANE_TIMEOUT,
            returncode=None,
            process_tree=_tree(
                leader_status="timeout-killed", outliving_descendants=1
            ),
        ),
        Termination.LEAKED,
        Outcome.ERROR,
        ReasonCode.EXEC_FAILED,
    ),
    _case(
        "report-fail-beats-signal",
        _inputs(
            outcome=Outcome.FAIL,
            reason_code=ReasonCode.COMMAND_FAILED,
            returncode=-signal.SIGKILL,
            decided_by_report=True,
        ),
        Termination.FAILED,
        Outcome.FAIL,
        ReasonCode.COMMAND_FAILED,
    ),
    _case(
        "limit-beats-report",
        _inputs(
            outcome=Outcome.FAIL,
            reason_code=ReasonCode.COMMAND_FAILED,
            returncode=1,
            decided_by_report=True,
            limits=LIMITS_HIT,
            process_tree=_tree(),
        ),
        Termination.LIMIT_EVENT,
        Outcome.ERROR,
        ReasonCode.EXEC_FAILED,
    ),
    _case(
        "pass-beats-limit",
        _inputs(
            outcome=Outcome.PASS,
            limits=LIMITS_HIT,
            process_tree=_tree(),
        ),
        Termination.PASSED,
        Outcome.PASS,
        None,
    ),
    _case(
        "pass-beats-unavailable-observation",
        _inputs(
            outcome=Outcome.PASS,
            observation="unavailable",
            observation_required=True,
            process_tree=_tree(),
        ),
        Termination.PASSED,
        Outcome.PASS,
        None,
    ),
    _case(
        "pass-beats-external-signal",
        _inputs(
            outcome=Outcome.PASS,
            returncode=-signal.SIGKILL,
            process_tree=_tree(),
        ),
        Termination.PASSED,
        Outcome.PASS,
        None,
    ),
    _case(
        "pass-with-nonterminal-leader-is-crashed",
        _inputs(
            outcome=Outcome.PASS,
            process_tree=_tree(leader_status="timeout-killed"),
        ),
        Termination.PASSED,
        Outcome.PASS,
        None,
        r2_bucket_override="crashed",
    ),
    _case(
        "sigsegv-is-self-signaled",
        _inputs(
            outcome=Outcome.FAIL,
            reason_code=ReasonCode.COMMAND_FAILED,
            returncode=-signal.SIGSEGV,
        ),
        Termination.SELF_SIGNALED,
        Outcome.FAIL,
        ReasonCode.COMMAND_FAILED,
    ),
    _case(
        "sigkill-is-external",
        _inputs(
            outcome=Outcome.FAIL,
            reason_code=ReasonCode.COMMAND_FAILED,
            returncode=-signal.SIGKILL,
        ),
        Termination.KILLED_EXTERNALLY,
        Outcome.ERROR,
        ReasonCode.EXEC_FAILED,
    ),
    _case(
        "self-signal-beats-tree-signal-status",
        _inputs(
            outcome=Outcome.FAIL,
            reason_code=ReasonCode.COMMAND_FAILED,
            returncode=-signal.SIGSEGV,
            process_tree=_tree(leader_status="signaled"),
        ),
        Termination.SELF_SIGNALED,
        Outcome.FAIL,
        ReasonCode.COMMAND_FAILED,
    ),
    _case(
        "self-signal-with-limit-event-is-infrastructure",
        _inputs(
            outcome=Outcome.FAIL,
            reason_code=ReasonCode.COMMAND_FAILED,
            returncode=-signal.SIGSEGV,
            process_tree=_tree(leader_status="signaled"),
            limits=LIMITS_HIT,
        ),
        Termination.LIMIT_EVENT,
        Outcome.ERROR,
        ReasonCode.EXEC_FAILED,
    ),
    _case(
        "report-pass-stays-pass",
        _inputs(
            outcome=Outcome.PASS,
            decided_by_report=True,
        ),
        Termination.PASSED,
        Outcome.PASS,
        None,
    ),
    _case(
        "required-clear-observation-does-not-block",
        _inputs(
            outcome=Outcome.FAIL,
            reason_code=ReasonCode.COMMAND_FAILED,
            returncode=1,
            observation="clear",
            observation_required=True,
        ),
        Termination.FAILED,
        Outcome.FAIL,
        ReasonCode.COMMAND_FAILED,
    ),
    _case(
        "not-run-beats-limit-and-signal",
        _inputs(
            outcome=Outcome.ERROR,
            reason_code=ReasonCode.EXEC_FAILED,
            returncode=-signal.SIGTERM,
            limits=LIMITS_HIT,
        ),
        Termination.NOT_RUN,
        Outcome.ERROR,
        ReasonCode.EXEC_FAILED,
    ),
    _case(
        "no-measurement-without-leak-is-not-run",
        _inputs(
            outcome=Outcome.NO_MEASUREMENT,
            reason_code=ReasonCode.TARGET_NOT_MEASURED,
            returncode=None,
        ),
        Termination.NOT_RUN,
        Outcome.NO_MEASUREMENT,
        ReasonCode.TARGET_NOT_MEASURED,
    ),
    _case(
        "timeout-beats-limit",
        _inputs(
            outcome=Outcome.BUDGET_EXCEEDED,
            reason_code=ReasonCode.LANE_TIMEOUT,
            returncode=None,
            limits=_limits(hit=True),
        ),
        Termination.TIMED_OUT,
        Outcome.BUDGET_EXCEEDED,
        ReasonCode.LANE_TIMEOUT,
    ),
    _case(
        "limit-beats-external-signal",
        _inputs(
            outcome=Outcome.FAIL,
            reason_code=ReasonCode.COMMAND_FAILED,
            returncode=-signal.SIGTERM,
            limits=_limits(hit=True),
        ),
        Termination.LIMIT_EVENT,
        Outcome.ERROR,
        ReasonCode.EXEC_FAILED,
    ),
    _case(
        "tree-signal-without-returncode-is-external",
        _inputs(
            outcome=Outcome.FAIL,
            reason_code=ReasonCode.COMMAND_FAILED,
            returncode=None,
            process_tree=_tree(leader_status="signaled"),
        ),
        Termination.KILLED_EXTERNALLY,
        Outcome.ERROR,
        ReasonCode.EXEC_FAILED,
    ),
    _case(
        "unavailable-not-required-is-judgeable",
        _inputs(
            outcome=Outcome.FAIL,
            reason_code=ReasonCode.COMMAND_FAILED,
            returncode=1,
            observation="unavailable",
            observation_required=False,
        ),
        Termination.FAILED,
        Outcome.FAIL,
        ReasonCode.COMMAND_FAILED,
    ),
    _case(
        "unobserved-bare-nonzero-fail-is-not-a-kill",
        _inputs(
            outcome=Outcome.FAIL,
            reason_code=ReasonCode.COMMAND_FAILED,
            returncode=1,
            process_tree=_tree(),
            observation="unavailable",
            observation_required=True,
        ),
        Termination.UNOBSERVED,
        Outcome.ERROR,
        ReasonCode.EXEC_FAILED,
    ),
    _case(
        "clean-report-fail-beats-unobserved-window",
        _inputs(
            outcome=Outcome.FAIL,
            reason_code=ReasonCode.COMMAND_FAILED,
            returncode=1,
            decided_by_report=True,
            process_tree=_tree(),
            observation="unavailable",
            observation_required=True,
        ),
        Termination.FAILED,
        Outcome.FAIL,
        ReasonCode.COMMAND_FAILED,
    ),
    _case(
        "unobserved-shell-wrapped-signal-is-not-a-kill",
        _inputs(
            outcome=Outcome.FAIL,
            reason_code=ReasonCode.COMMAND_FAILED,
            returncode=137,
            process_tree=_tree(),
            observation="unavailable",
            observation_required=True,
        ),
        Termination.UNOBSERVED,
        Outcome.ERROR,
        ReasonCode.EXEC_FAILED,
    ),
)


ALL_CASES = TERMINATION_CASES + PRECEDENCE_CASES
