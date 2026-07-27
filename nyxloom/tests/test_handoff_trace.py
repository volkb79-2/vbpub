"""B26: per-handoff processing trace (nyxloom-trove/4-backlog.md).

Pure builder tests only -- no I/O, no fixtures, synthetic Event lists built
directly (mirrors the payload shapes storage.py's INTERFACE CONTRACT and
daemon.py's real emitters use, so a shape drift there would be caught here
too)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from nyxloom.handoff_trace import HandoffTrace, TraceLeg, build_trace
from nyxloom.types import (
    Actor, ActorKind, Attempt, AttemptState, Blocker, BlockerType, Event,
    EventType, GateResult, Receipt, ReceiptResult, Role, Route, TaskState,
    TaskStateFile,
)

PROJECT = "demo"
BASE_TS = datetime(2026, 7, 27, 12, 0, 0, tzinfo=timezone.utc)


def _ts(offset_seconds: int) -> datetime:
    return BASE_TS + timedelta(seconds=offset_seconds)


def _actor() -> Actor:
    return Actor(ActorKind.TICK, "nyxloomd")


def _ev(seq: int, type_: EventType, payload: dict, *, task_id: str = "demo-T1",
        attempt_id: str | None = None, ts: datetime | None = None) -> Event:
    return Event(
        schema_version=1, sequence=seq, timestamp=ts or _ts(seq),
        project=PROJECT, actor=_actor(), type=type_, payload=payload,
        task_id=task_id, attempt_id=attempt_id,
    )


# ---------------------------------------------------------------------------
# happy path: created -> implement -> review(approved) -> gate(pass) -> merge

def test_happy_path_multi_leg_trace():
    task_id = "demo-T1"
    events: list[Event] = []

    tsf = TaskStateFile(schema_version=1, task_id=task_id, project=PROJECT,
                        state=TaskState.QUEUED, since=_ts(0),
                        handoff_path="handoff/demo-T1.md")
    events.append(_ev(1, EventType.TASK_CREATED, {"statefile": tsf.to_dict()}, ts=_ts(0)))

    impl_route = Route(route_id="fake-cli", cli="fake", model="fake-model")
    impl_created = Attempt(attempt_id="att-1", role=Role.IMPLEMENTER,
                            state=AttemptState.CREATED, route=impl_route, started=_ts(1))
    events.append(_ev(2, EventType.ATTEMPT_CREATED, {"attempt": impl_created.to_dict()},
                      attempt_id="att-1", ts=_ts(1)))
    impl_done = Attempt(attempt_id="att-1", role=Role.IMPLEMENTER, state=AttemptState.EXITED,
                        route=impl_route, started=_ts(1), ended=_ts(30),
                        receipt=Receipt(result=ReceiptResult.DONE, exit_code=0))
    events.append(_ev(3, EventType.ATTEMPT_EXITED, {"attempt": impl_done.to_dict()},
                      attempt_id="att-1", ts=_ts(30)))

    events.append(_ev(4, EventType.TASK_TRANSITIONED,
                      {"from": "ACTIVE", "to": "AWAITING_REVIEW"}, ts=_ts(31)))

    rev_route = Route(route_id="frontier", cli="fake", model="frontier-model")
    rev_created = Attempt(attempt_id="att-2", role=Role.REVIEW_INDEPENDENT,
                          state=AttemptState.CREATED, route=rev_route, started=_ts(32))
    events.append(_ev(5, EventType.ATTEMPT_CREATED, {"attempt": rev_created.to_dict()},
                      attempt_id="att-2", ts=_ts(32)))
    rev_done = Attempt(attempt_id="att-2", role=Role.REVIEW_INDEPENDENT,
                       state=AttemptState.EXITED, route=rev_route, started=_ts(32), ended=_ts(60),
                       receipt=Receipt(result=ReceiptResult.DONE, exit_code=0))
    events.append(_ev(6, EventType.ATTEMPT_EXITED, {"attempt": rev_done.to_dict()},
                      attempt_id="att-2", ts=_ts(60)))

    events.append(_ev(7, EventType.REVIEW_RECORDED, {"result": "approved"},
                      attempt_id="att-2", ts=_ts(61)))
    events.append(_ev(8, EventType.TASK_TRANSITIONED,
                      {"from": "AWAITING_REVIEW", "to": "MERGE_READY"}, ts=_ts(62)))

    gate_result = GateResult(gate_id="pytest-q", phase="pre-merge", commit="c" * 40,
                             exit_code=0, started=_ts(63), ended=_ts(90), environment="local")
    events.append(_ev(9, EventType.GATE_FINISHED, {"gate_result": gate_result.to_dict()}, ts=_ts(90)))

    events.append(_ev(10, EventType.MERGE_RECORDED,
                      {"merge_commit": "d" * 40, "source_kind": "review"}, ts=_ts(91)))
    events.append(_ev(11, EventType.TASK_TRANSITIONED,
                      {"from": "VALIDATING", "to": "COMPLETED"}, ts=_ts(92)))

    trace = build_trace(task_id, events)

    assert isinstance(trace, HandoffTrace)
    assert trace.task_id == task_id
    kinds = [leg.kind for leg in trace.legs]
    assert kinds == ["created", "attempt", "transition", "attempt", "review",
                     "transition", "gate", "merge", "transition"]
    # Exact sequences, not `== sorted(itself)`: comparing a list to its own
    # sorted copy only proves sorting sorts, and it passes even if the sort is
    # removed whenever append order happens to match. Hardcoding pins BOTH the
    # ordering AND the attempt-grouping anchor: att-1 spans events 2-3 and att-2
    # spans 5-6, and each must collapse to ONE leg anchored at the group's FIRST
    # sequence (2 and 5) -- so 3 and 6 must be absent, and a regression that
    # anchored on the last event would show up as [.. 3 .. 6 ..] here.
    assert [leg.sequence for leg in trace.legs] == [1, 2, 4, 5, 7, 8, 9, 10, 11]

    created_leg = trace.legs[0]
    assert created_leg.detail["handoff_path"] == "handoff/demo-T1.md"
    assert created_leg.outcome is None

    impl_leg = trace.legs[1]
    assert impl_leg.stage == "implementer"
    assert impl_leg.outcome == "done"
    assert impl_leg.attempt_id == "att-1"
    assert impl_leg.started == impl_done.started.isoformat()
    assert impl_leg.ended == _ts(30).isoformat()
    assert impl_leg.summary is None  # DONE receipts carry no blocked_reason

    review_attempt_leg = trace.legs[3]
    assert review_attempt_leg.kind == "attempt"
    assert review_attempt_leg.stage == "review-independent"
    assert review_attempt_leg.attempt_id == "att-2"

    review_leg = trace.legs[4]
    assert review_leg.kind == "review"
    assert review_leg.outcome == "approved"
    assert review_leg.attempt_id == "att-2"
    assert review_leg.detail == {}  # no reject_class on an approval

    gate_leg = trace.legs[6]
    assert gate_leg.kind == "gate"
    assert gate_leg.outcome == "pass"
    assert gate_leg.stage == "pre-merge"
    assert gate_leg.gate_id == "pytest-q"
    assert gate_leg.summary is None  # empty output_tail on a passing gate

    merge_leg = trace.legs[7]
    assert merge_leg.outcome == "merged"
    assert merge_leg.detail["merge_commit"] == "d" * 40
    assert merge_leg.detail["source_kind"] == "review"


# ---------------------------------------------------------------------------
# rejected + retried: two implementer legs, a rejected review, an approved retry

def test_rejected_and_retried_handoff():
    task_id = "demo-T2"
    events: list[Event] = []
    seq = 1

    def add(type_: EventType, payload: dict, attempt_id: str | None = None) -> None:
        nonlocal seq
        events.append(_ev(seq, type_, payload, task_id=task_id, attempt_id=attempt_id, ts=_ts(seq)))
        seq += 1

    impl_route = Route(route_id="fake-cli", cli="fake", model="m")
    rev_route = Route(route_id="frontier", cli="fake", model="m2")

    a1 = Attempt(attempt_id="att-r1", role=Role.IMPLEMENTER, state=AttemptState.EXITED,
                route=impl_route, started=_ts(1), ended=_ts(2),
                receipt=Receipt(result=ReceiptResult.DONE, exit_code=0))
    add(EventType.ATTEMPT_CREATED, {"attempt": a1.to_dict()}, "att-r1")
    add(EventType.ATTEMPT_EXITED, {"attempt": a1.to_dict()}, "att-r1")
    add(EventType.TASK_TRANSITIONED, {"from": "ACTIVE", "to": "AWAITING_REVIEW"})

    r1 = Attempt(attempt_id="att-rv1", role=Role.REVIEW_INDEPENDENT, state=AttemptState.EXITED,
                route=rev_route, started=_ts(3), ended=_ts(4),
                receipt=Receipt(result=ReceiptResult.DONE, exit_code=0))
    add(EventType.ATTEMPT_CREATED, {"attempt": r1.to_dict()}, "att-rv1")
    add(EventType.ATTEMPT_EXITED, {"attempt": r1.to_dict()}, "att-rv1")
    add(EventType.REVIEW_RECORDED, {"result": "rejected", "reject_class": "fixable"}, "att-rv1")
    add(EventType.TASK_TRANSITIONED,
        {"from": "AWAITING_REVIEW", "to": "REVIEW_REJECTED",
         "notes": "review verdict: rejected (receipt: done)"})

    a2 = Attempt(attempt_id="att-r2", role=Role.IMPLEMENTER, state=AttemptState.EXITED,
                route=impl_route, started=_ts(5), ended=_ts(6),
                receipt=Receipt(result=ReceiptResult.DONE, exit_code=0))
    add(EventType.ATTEMPT_CREATED, {"attempt": a2.to_dict()}, "att-r2")
    add(EventType.ATTEMPT_EXITED, {"attempt": a2.to_dict()}, "att-r2")
    add(EventType.TASK_TRANSITIONED, {"from": "QUEUED", "to": "AWAITING_REVIEW"})

    r2 = Attempt(attempt_id="att-rv2", role=Role.REVIEW_INDEPENDENT, state=AttemptState.EXITED,
                route=rev_route, started=_ts(7), ended=_ts(8),
                receipt=Receipt(result=ReceiptResult.DONE, exit_code=0))
    add(EventType.ATTEMPT_CREATED, {"attempt": r2.to_dict()}, "att-rv2")
    add(EventType.ATTEMPT_EXITED, {"attempt": r2.to_dict()}, "att-rv2")
    add(EventType.REVIEW_RECORDED,
        {"result": "approved", "reject_class": "fixable", "repair_invalidated": True}, "att-rv2")

    trace = build_trace(task_id, events)

    attempt_legs = [leg for leg in trace.legs if leg.kind == "attempt"]
    assert [leg.attempt_id for leg in attempt_legs] == ["att-r1", "att-rv1", "att-r2", "att-rv2"]
    assert [leg.stage for leg in attempt_legs] == [
        "implementer", "review-independent", "implementer", "review-independent"]

    review_legs = [leg for leg in trace.legs if leg.kind == "review"]
    assert len(review_legs) == 2
    assert review_legs[0].outcome == "rejected"
    assert review_legs[0].detail["reject_class"] == "fixable"
    assert "repair_invalidated" not in review_legs[0].detail
    assert review_legs[1].detail["repair_invalidated"] is True

    reject_transition = next(
        leg for leg in trace.legs if leg.kind == "transition" and leg.outcome == "REVIEW_REJECTED")
    assert reject_transition.summary == "review verdict: rejected (receipt: done)"


# ---------------------------------------------------------------------------
# gate-failed handoff

def test_gate_failed_handoff():
    task_id = "demo-T3"
    gate_result = GateResult(
        gate_id="pytest-q", phase="pre-merge", commit="e" * 40, exit_code=1,
        started=_ts(1), ended=_ts(5), environment="local",
        output_tail="FAILED tests/test_x.py::test_y - AssertionError",
    )
    events = [
        _ev(1, EventType.GATE_FINISHED, {"gate_result": gate_result.to_dict()},
           task_id=task_id, ts=_ts(5)),
        _ev(2, EventType.TASK_TRANSITIONED,
           {"from": "MERGE_READY", "to": "REVIEW_REJECTED",
            "notes": "pre-merge gate failed (exit 1); not published"},
           task_id=task_id, ts=_ts(6)),
    ]

    trace = build_trace(task_id, events)

    gate_leg = trace.legs[0]
    assert gate_leg.kind == "gate"
    assert gate_leg.outcome == "fail"
    assert gate_leg.summary == gate_result.output_tail
    assert gate_leg.detail["exit_code"] == 1
    assert gate_leg.detail["commit"] == "e" * 40

    transition_leg = trace.legs[1]
    assert transition_leg.outcome == "REVIEW_REJECTED"
    assert "gate failed" in transition_leg.summary


# ---------------------------------------------------------------------------
# incomplete / in-flight handoff

def test_in_flight_attempt_has_no_receipt_outcome_yet():
    task_id = "demo-T4"
    running = Attempt(attempt_id="att-live", role=Role.IMPLEMENTER, state=AttemptState.RUNNING,
                      route=Route(route_id="fake-cli", cli="fake", model="m"), started=_ts(1))
    events = [
        _ev(1, EventType.ATTEMPT_CREATED, {"attempt": running.to_dict()},
           task_id=task_id, attempt_id="att-live", ts=_ts(1)),
    ]

    trace = build_trace(task_id, events)

    assert len(trace.legs) == 1
    leg = trace.legs[0]
    assert leg.kind == "attempt"
    assert leg.outcome == "RUNNING"  # falls back to AttemptState: no receipt yet
    assert leg.ended is None
    assert leg.summary is None


# ---------------------------------------------------------------------------
# empty / unknown task

def test_unknown_task_returns_empty_trace_not_an_error():
    events = [_ev(1, EventType.TASK_CREATED, {"statefile": {}}, task_id="some-other-task")]

    trace = build_trace("does-not-exist", events)

    assert trace.task_id == "does-not-exist"
    assert trace.legs == []


def test_no_events_at_all_returns_empty_trace():
    trace = build_trace("demo-T5", [])
    assert trace.legs == []


# ---------------------------------------------------------------------------
# defensive: a malformed ATTEMPT_* record with no attempt_id is skipped

def test_malformed_attempt_event_without_attempt_id_is_skipped():
    task_id = "demo-T6"
    events = [
        _ev(1, EventType.ATTEMPT_STARTED, {"attempt": {}}, task_id=task_id,
           attempt_id=None, ts=_ts(1)),
    ]

    trace = build_trace(task_id, events)

    assert trace.legs == []


# ---------------------------------------------------------------------------
# blocked transitions: notes wins over blocker.detail when both are present;
# blocker.detail is the fallback when notes is absent

def test_blocked_transition_summary_falls_back_to_blocker_detail():
    task_id = "demo-T7"
    blocker = Blocker(type=BlockerType.CONTRACT, unblock_condition="triage BLOCKED reason",
                      detail="agent reported: cannot resolve dependency X")
    events = [
        _ev(1, EventType.TASK_BLOCKED, {"from": "ACTIVE", "blocker": blocker.to_dict()},
           task_id=task_id, ts=_ts(1)),
    ]

    trace = build_trace(task_id, events)

    leg = trace.legs[0]
    assert leg.kind == "transition"
    assert leg.outcome == "BLOCKED"
    assert leg.summary == "agent reported: cannot resolve dependency X"
    assert leg.detail["blocker_type"] == "contract"
    assert leg.detail["unblock_condition"] == "triage BLOCKED reason"


def test_blocked_transition_prefers_notes_over_blocker_detail():
    task_id = "demo-T8"
    blocker = Blocker(type=BlockerType.ENVIRONMENT, unblock_condition="x", detail="raw detail")
    events = [
        _ev(1, EventType.TASK_BLOCKED,
           {"from": "ACTIVE", "blocker": blocker.to_dict(), "notes": "operator note wins"},
           task_id=task_id, ts=_ts(1)),
    ]

    trace = build_trace(task_id, events)

    assert trace.legs[0].summary == "operator note wins"


# ---------------------------------------------------------------------------
# scope-amendment (a mid-flight retry variant, B21/D-R16)

def test_scope_amendment_requested_and_approved_legs():
    task_id = "demo-T9"
    events = [
        _ev(1, EventType.SCOPE_AMENDMENT_REQUESTED,
           {"file": "libs/common/extra.py", "reason": "need shared helper"},
           task_id=task_id, ts=_ts(1)),
        _ev(2, EventType.SCOPE_AMENDMENT_APPROVED,
           {"file": "libs/common/extra.py", "reason": "need shared helper"},
           task_id=task_id, ts=_ts(2)),
    ]

    trace = build_trace(task_id, events)

    assert [leg.kind for leg in trace.legs] == ["scope-amendment", "scope-amendment"]
    assert [leg.outcome for leg in trace.legs] == ["requested", "approved"]
    assert trace.legs[0].summary == "need shared helper"
    assert trace.legs[0].detail["file"] == "libs/common/extra.py"


# ---------------------------------------------------------------------------
# re-carve: TASK_SUPERSEDED / TASK_CANCELLED fixed-target transitions

def test_superseded_and_cancelled_transition_legs():
    task_id = "demo-T10"
    events = [
        _ev(1, EventType.TASK_SUPERSEDED,
           {"from": "READY_TO_CARVE", "notes": "re-carved: scope stale"},
           task_id=task_id, ts=_ts(1)),
        _ev(2, EventType.TASK_CANCELLED, {"from": "BLOCKED"}, task_id=task_id, ts=_ts(2)),
    ]

    trace = build_trace(task_id, events)

    assert [leg.outcome for leg in trace.legs] == ["SUPERSEDED", "CANCELLED"]
    assert trace.legs[0].summary == "re-carved: scope stale"
    assert trace.legs[1].summary is None
