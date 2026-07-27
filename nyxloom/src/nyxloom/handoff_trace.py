"""Per-handoff processing trace. PACKAGE B26 (nyxloom-trove/4-backlog.md).

Reconstructs one task's leg-by-leg processing history (implement / review /
gate / merge, plus blocks, scope-amendment re-carves, and retries -- visible
here as repeated legs of the same kind) ENTIRELY from events already
durably recorded in the event log (storage.py). READ-ONLY: this module
performs no I/O of its own and defines no new EventType. The sole entry
point, `build_trace`, is a PURE function: it takes a task_id and an
already-loaded list of events (the caller -- e.g. render.py -- is the I/O
boundary that reads storage.iter_events) and returns a `HandoffTrace`,
trivially unit-testable with synthetic event lists, no fixtures, no disk.

Design: rather than trying to correlate each attempt with the transition
that followed it (fragile -- multiple legal orderings exist, e.g. a shared
wave review binds ONE attempt_id to MANY task_ids' TASK_TRANSITIONED
events), a `HandoffTrace` is simply every leg-shaped event for this
task_id, ordered by the log's own monotonic `sequence`. A leg's own
free-text "summary" is whatever untrusted, potentially agent-authored text
the ALREADY-recorded payload carries for that leg kind
(Receipt.blocked_reason, a gate's `output_tail`, a transition's
`notes`/blocker `detail`, a scope-amendment `reason`) -- never invented,
never fetched from a report file on disk (that would be new I/O, out of
scope for this trace). Free text absent from the log renders as None; the
caller decides how to display that ("unknown"/"-").

Leg kinds (`TraceLeg.kind`):
  created          TASK_CREATED -- the handoff's origin point.
  attempt          One dispatched agent leg (ATTEMPT_CREATED..EXITED/FAILED
                    for one attempt_id, grouped -- the payload is always
                    the FULL, latest Attempt dict per storage.py's
                    projection contract, so the LAST event for an
                    attempt_id is authoritative; the leg is anchored at the
                    FIRST event's sequence). stage = Role.value
                    (implementer/self-review/review-independent/carver).
  review           REVIEW_RECORDED -- a reviewer's structured verdict
                    (approved/rejected/incomplete) + reject_class, if any.
  gate             GATE_FINISHED -- one gate run; summary is its
                    output_tail (bounded diagnostic text the daemon
                    already truncates on failure).
  merge            MERGE_RECORDED -- the merge commit.
  transition       TASK_TRANSITIONED / TASK_BLOCKED / TASK_SUPERSEDED /
                    TASK_CANCELLED -- state milestones, including rejects
                    (-> REVIEW_REJECTED) and re-carve supersessions.
  scope-amendment  SCOPE_AMENDMENT_REQUESTED / _APPROVED -- a mid-flight
                    scope widening (a retry variant, B21/D-R16).

Every other EventType (WAVE_OPENED, DECISION_*, FINDING_RECORDED, ...) is
out of this task's leg history and produces no leg.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .types import Attempt, Event, EventType, GateResult, iso

_ATTEMPT_PREFIX = "ATTEMPT_"

_TRANSITION_TYPES = frozenset({
    EventType.TASK_TRANSITIONED, EventType.TASK_BLOCKED,
    EventType.TASK_SUPERSEDED, EventType.TASK_CANCELLED,
})

_SCOPE_AMENDMENT_TYPES = frozenset({
    EventType.SCOPE_AMENDMENT_REQUESTED, EventType.SCOPE_AMENDMENT_APPROVED,
})

# Fixed target label for the three EventTypes whose destination state is
# NOT a free `payload["to"]` parameter -- mirrors storage.py's own
# apply_event/_transition_target resolvers, duplicated here in label form
# only (this module never imports storage, staying dependency-free/pure by
# construction rather than by discipline).
_FIXED_TRANSITION_TARGET = {
    EventType.TASK_BLOCKED: "BLOCKED",
    EventType.TASK_SUPERSEDED: "SUPERSEDED",
    EventType.TASK_CANCELLED: "CANCELLED",
}


@dataclass(frozen=True)
class TraceLeg:
    """One leg of a handoff's processing history. `detail` carries a small
    bag of kind-specific extras for a drill-down view; every other field is
    common across kinds so a single table can render any leg uniformly."""
    sequence: int           # originating event's sequence -- the sort/order key
    kind: str                # created|attempt|review|gate|merge|transition|scope-amendment
    stage: str               # role/phase/fixed label -- the at-a-glance column
    outcome: str | None      # result/verdict/pass-fail/target-state; None if unknown
    started: str | None      # ISO-8601
    ended: str | None        # ISO-8601, or None if still open/instantaneous
    actor: str               # "<ActorKind.value>:<Actor.id>" of the emitting event
    summary: str | None      # untrusted free text already in the log, or None
    attempt_id: str | None = None
    gate_id: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class HandoffTrace:
    """The ordered leg history for one task_id. `legs` is empty for an
    unknown/never-created task_id -- absence, not an error (see
    build_trace)."""
    task_id: str
    legs: list[TraceLeg]


def _actor_label(ev: Event) -> str:
    return f"{ev.actor.kind.value}:{ev.actor.id}"


def _created_leg(ev: Event) -> TraceLeg:
    statefile = ev.payload.get("statefile") or {}
    return TraceLeg(
        sequence=ev.sequence, kind="created", stage="created", outcome=None,
        started=iso(ev.timestamp), ended=None, actor=_actor_label(ev),
        summary=None, detail={"handoff_path": statefile.get("handoff_path") or ""},
    )


def _attempt_leg(evs: list[Event]) -> TraceLeg:
    """`evs` is one attempt_id's raw ATTEMPT_* events, in log order. Per
    storage.py's projection contract every ATTEMPT_* payload carries the
    FULL, latest Attempt dict -- so the LAST event is authoritative for the
    attempt's current state/receipt, while the FIRST anchors this leg's
    position in the trace (when the agent was dispatched)."""
    first_ev, latest_ev = evs[0], evs[-1]
    attempt = Attempt.from_dict(latest_ev.payload["attempt"])
    receipt = attempt.receipt
    outcome = receipt.result.value if receipt is not None else attempt.state.value
    summary = receipt.blocked_reason if receipt is not None else None
    detail = {
        "attempt_state": attempt.state.value,
        "route_id": attempt.route.route_id,
        "model": attempt.route.model,
        "cli": attempt.route.cli,
    }
    return TraceLeg(
        sequence=first_ev.sequence, kind="attempt", stage=attempt.role.value,
        outcome=outcome, started=iso(attempt.started),
        ended=iso(attempt.ended) if attempt.ended is not None else None,
        actor=_actor_label(first_ev), summary=summary,
        attempt_id=attempt.attempt_id, detail=detail,
    )


def _review_leg(ev: Event) -> TraceLeg:
    payload = ev.payload
    detail: dict[str, Any] = {}
    if "reject_class" in payload:
        detail["reject_class"] = payload["reject_class"]
    if payload.get("repair_invalidated"):
        detail["repair_invalidated"] = True
    return TraceLeg(
        sequence=ev.sequence, kind="review", stage="review-verdict",
        outcome=payload.get("result"), started=iso(ev.timestamp), ended=None,
        actor=_actor_label(ev), summary=None, attempt_id=ev.attempt_id,
        detail=detail,
    )


def _gate_leg(ev: Event) -> TraceLeg:
    gr = GateResult.from_dict(ev.payload["gate_result"])
    outcome = "pass" if gr.exit_code == 0 else "fail"
    return TraceLeg(
        sequence=ev.sequence, kind="gate", stage=gr.phase, outcome=outcome,
        started=iso(gr.started), ended=iso(gr.ended), actor=_actor_label(ev),
        summary=gr.output_tail or None, gate_id=gr.gate_id,
        detail={"commit": gr.commit, "exit_code": gr.exit_code,
                "environment": gr.environment or ""},
    )


def _merge_leg(ev: Event) -> TraceLeg:
    payload = ev.payload
    return TraceLeg(
        sequence=ev.sequence, kind="merge", stage="merge", outcome="merged",
        started=iso(ev.timestamp), ended=None, actor=_actor_label(ev),
        summary=None,
        detail={"merge_commit": payload.get("merge_commit", ""),
                "source_kind": payload.get("source_kind", "")},
    )


def _transition_leg(ev: Event) -> TraceLeg:
    payload = ev.payload
    fixed = _FIXED_TRANSITION_TARGET.get(ev.type)
    outcome = fixed if fixed is not None else payload.get("to")
    summary = payload.get("notes")
    detail: dict[str, Any] = {"from": payload.get("from", "")}
    if ev.type is EventType.TASK_BLOCKED:
        blocker = payload.get("blocker") or {}
        detail["blocker_type"] = blocker.get("type", "")
        detail["unblock_condition"] = blocker.get("unblock_condition", "")
        if summary is None:
            summary = blocker.get("detail")
    return TraceLeg(
        sequence=ev.sequence, kind="transition", stage="transition",
        outcome=outcome, started=iso(ev.timestamp), ended=None,
        actor=_actor_label(ev), summary=summary, detail=detail,
    )


def _scope_amendment_leg(ev: Event) -> TraceLeg:
    phase = ("requested" if ev.type is EventType.SCOPE_AMENDMENT_REQUESTED
              else "approved")
    payload = ev.payload
    return TraceLeg(
        sequence=ev.sequence, kind="scope-amendment", stage="scope-amendment",
        outcome=phase, started=iso(ev.timestamp), ended=None,
        actor=_actor_label(ev), summary=payload.get("reason"),
        detail={"file": payload.get("file", "")},
    )


def build_trace(task_id: str, events: list[Event]) -> HandoffTrace:
    """PURE: no I/O, no mutation of `events`. `events` may be any project's
    full event stream (unfiltered) or an already task-scoped subset -- this
    filters to `task_id` itself, so callers never need to pre-filter."""
    task_events = sorted(
        (ev for ev in events if ev.task_id == task_id), key=lambda ev: ev.sequence,
    )
    legs: list[TraceLeg] = []
    attempt_events: dict[str, list[Event]] = {}
    attempt_order: list[str] = []

    for ev in task_events:
        t = ev.type
        if t is EventType.TASK_CREATED:
            legs.append(_created_leg(ev))
        elif t.value.startswith(_ATTEMPT_PREFIX):
            aid = ev.attempt_id
            if aid is None:
                continue  # malformed/legacy record: no attempt_id to group under
            if aid not in attempt_events:
                attempt_order.append(aid)
                attempt_events[aid] = []
            attempt_events[aid].append(ev)
        elif t is EventType.REVIEW_RECORDED:
            legs.append(_review_leg(ev))
        elif t is EventType.GATE_FINISHED:
            legs.append(_gate_leg(ev))
        elif t is EventType.MERGE_RECORDED:
            legs.append(_merge_leg(ev))
        elif t in _TRANSITION_TYPES:
            legs.append(_transition_leg(ev))
        elif t in _SCOPE_AMENDMENT_TYPES:
            legs.append(_scope_amendment_leg(ev))
        # else: out of scope for this trace (WAVE_OPENED, DECISION_*, ...)

    for aid in attempt_order:
        legs.append(_attempt_leg(attempt_events[aid]))

    legs.sort(key=lambda leg: leg.sequence)
    return HandoffTrace(task_id=task_id, legs=legs)
