"""Classify how a measured command ended from its recorded evidence."""

from __future__ import annotations

import enum
import signal
from typing import Literal

from .errors import Outcome, ReasonCode
from .process_tree import ProcessTreeEvidence
from .resource_limits import ResourceLimitEvidence


class Termination(enum.Enum):
    PASSED = "passed"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    HUNG = "hung"
    NOT_RUN = "not_run"
    KILLED_EXTERNALLY = "killed_externally"
    LEAKED = "leaked"
    LIMIT_EVENT = "limit_event"
    UNOBSERVED = "unobserved"


JUDGEABLE = frozenset({Termination.PASSED, Termination.FAILED})
EXTERNAL_SIGNALS = frozenset(
    {signal.SIGKILL, signal.SIGTERM, signal.SIGINT, signal.SIGHUP}
)

_R2_BUCKETS = {
    Termination.PASSED: "survived",
    Termination.FAILED: "killed",
    Termination.TIMED_OUT: "budget_exceeded",
    Termination.HUNG: "hung",
    Termination.NOT_RUN: "crashed",
    Termination.KILLED_EXTERNALLY: "crashed",
    Termination.LEAKED: "crashed",
    Termination.LIMIT_EVENT: "crashed",
    Termination.UNOBSERVED: "crashed",
}


def classify(
    *,
    outcome: Outcome,
    reason_code: ReasonCode | None,
    returncode: int | None,
    decided_by_report: bool,
    process_tree: ProcessTreeEvidence | None,
    limits: ResourceLimitEvidence | None,
    observation: Literal["clear", "unavailable", "not_requested"],
    observation_required: bool,
) -> Termination:
    """Return the highest-precedence explanation supported by the evidence."""
    leaked = process_tree is not None and process_tree.outliving_descendants > 0
    if outcome in {Outcome.ERROR, Outcome.NO_MEASUREMENT} and not leaked:
        return Termination.NOT_RUN
    if leaked:
        return Termination.LEAKED
    if outcome is Outcome.BUDGET_EXCEEDED:
        if reason_code is ReasonCode.CANDIDATE_HUNG:
            return Termination.HUNG
        return Termination.TIMED_OUT
    if outcome is Outcome.PASS:
        return Termination.PASSED
    if decided_by_report and outcome is Outcome.FAIL:
        return Termination.FAILED
    if returncode is not None and returncode < 0:
        if -returncode in EXTERNAL_SIGNALS:
            return Termination.KILLED_EXTERNALLY
        return Termination.FAILED
    if process_tree is not None and process_tree.leader_status == "signaled":
        return Termination.KILLED_EXTERNALLY
    if limits is not None and limits.limit_hit:
        return Termination.LIMIT_EVENT
    if observation_required and observation != "clear":
        return Termination.UNOBSERVED
    return Termination.FAILED


def r2_bucket(termination: Termination) -> str:
    """Map a termination result onto the native mutation result buckets."""
    return _R2_BUCKETS[termination]


def detail(
    termination: Termination,
    limits: ResourceLimitEvidence | None = None,
    observation_reasons: tuple[str, ...] = (),
    returncode: int | None = None,
) -> str | None:
    """Render deterministic refusal detail for infrastructure terminations."""
    if termination is Termination.KILLED_EXTERNALLY:
        if returncode is not None and returncode < 0:
            return f"killed-by-signal:{-returncode}"
        return "killed-by-signal:unknown"
    if termination is Termination.LEAKED:
        return "leaked-process-tree"
    if termination is Termination.LIMIT_EVENT:
        events = () if limits is None else tuple(
            f"{name}={counter.delta}"
            for name, counter in (
                ("pids_events_max", limits.pids_events_max),
                ("memory_events_oom", limits.memory_events_oom),
                ("memory_events_oom_kill", limits.memory_events_oom_kill),
                (
                    "memory_events_oom_group_kill",
                    limits.memory_events_oom_group_kill,
                ),
            )
            if counter.delta > 0
        )
        if events:
            return f"resource-limit-event:{','.join(events)}"
        return "resource-limit-event"
    if termination is Termination.UNOBSERVED:
        if observation_reasons:
            return f"resource-observation-unavailable:{'; '.join(observation_reasons)}"
        return "resource-observation-unavailable"
    return None
