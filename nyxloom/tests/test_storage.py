"""Tests for nyxloom.storage. PACKAGE P20.

Oracles from handoff/P20-transition-idempotency.md:
  1. Applying a TASK_TRANSITIONED event with from==to via append_and_apply
     returns cleanly, leaves state unchanged, raises nothing, and appends
     no spurious event.
  2. replay() over a log containing a from==to TASK_TRANSITIONED event
     reconstructs state without raising.
  3. A genuinely invalid transition (e.g. QUEUED->MERGED) still raises
     TransitionError -- the no-op tolerance is from==to only.
"""

from __future__ import annotations

import pytest

from nyxloom import storage
from nyxloom.types import (
    Actor, ActorKind, Blocker, BlockerType, EventType, TaskState, TaskStateFile,
    TransitionError, utc_now,
)

ACTOR = Actor(kind=ActorKind.TICK, id="test")


def _blocker(reason: str) -> Blocker:
    return Blocker(type=BlockerType.EXTERNAL, unblock_condition=reason)


def _seed(project: str, task_id: str, state: TaskState) -> dict:
    """Seed a single task's projection via TASK_CREATED; returns the
    live `states` map used by append_and_apply."""
    states: dict = {}
    tsf = TaskStateFile(schema_version=storage.SCHEMA_VERSION, task_id=task_id,
                         project=project, state=state, since=utc_now())
    storage.append_and_apply(
        project, states, actor=ACTOR, type=EventType.TASK_CREATED,
        payload={"statefile": tsf.to_dict()}, task_id=task_id,
    )
    return states


# ---------------------------------------------------------------------------
# Oracle 1: live from==to apply is a silent no-op

def test_from_equals_to_apply_is_silent_noop(tmp_state):
    project = "p20-live"
    task_id = "t-live"
    states = _seed(project, task_id, TaskState.QUEUED)

    ev = storage.append_and_apply(
        project, states, actor=ACTOR, type=EventType.TASK_TRANSITIONED,
        payload={"from": "QUEUED", "to": "QUEUED", "notes": None}, task_id=task_id,
    )

    assert ev.type is EventType.TASK_TRANSITIONED  # the event itself is still logged
    assert states[task_id].state is TaskState.QUEUED  # unchanged

    on_disk = storage.load_state(project, task_id)
    assert on_disk.state is TaskState.QUEUED

    # No spurious extra event was appended by the apply path itself: exactly
    # TASK_CREATED + the one TASK_TRANSITIONED we just appended.
    types = [e.type for e in storage.iter_events(project)]
    assert types == [EventType.TASK_CREATED, EventType.TASK_TRANSITIONED]


def test_from_equals_to_apply_does_not_overwrite_notes_or_since(tmp_state):
    """A no-op must leave the statefile genuinely untouched, not just the
    state field -- notes/since from a prior real transition survive."""
    project = "p20-untouched"
    task_id = "t-untouched"
    states = _seed(project, task_id, TaskState.QUEUED)
    before = states[task_id].since

    storage.append_and_apply(
        project, states, actor=ACTOR, type=EventType.TASK_TRANSITIONED,
        payload={"from": "QUEUED", "to": "QUEUED", "notes": "should not stick"},
        task_id=task_id,
    )

    assert states[task_id].since == before
    assert states[task_id].notes is None


# ---------------------------------------------------------------------------
# Oracle 2: replay() over a log containing a from==to event does not raise

def test_replay_tolerates_from_equals_to_event_in_log(tmp_state):
    project = "p20-replay"
    task_id = "t-replay"
    _seed(project, task_id, TaskState.CARVED)

    # A real transition, then a from==to event landing in the log (as would
    # happen from a racing double-dispatch that both computed CARVED->QUEUED
    # and the second call's payload recorded from==to==QUEUED).
    storage.append_event(
        project, actor=ACTOR, type=EventType.TASK_TRANSITIONED,
        payload={"from": "CARVED", "to": "QUEUED", "notes": None}, task_id=task_id,
    )
    storage.append_event(
        project, actor=ACTOR, type=EventType.TASK_TRANSITIONED,
        payload={"from": "QUEUED", "to": "QUEUED", "notes": None}, task_id=task_id,
    )

    replayed = storage.replay(project)  # must not raise
    assert replayed[task_id].state is TaskState.QUEUED


# ---------------------------------------------------------------------------
# Oracle 3: a genuinely invalid transition still raises

def test_invalid_transition_still_raises(tmp_state):
    project = "p20-invalid"
    task_id = "t-invalid"
    states = _seed(project, task_id, TaskState.QUEUED)

    with pytest.raises(TransitionError):
        storage.append_and_apply(
            project, states, actor=ACTOR, type=EventType.TASK_TRANSITIONED,
            payload={"from": "QUEUED", "to": "MERGED", "notes": None}, task_id=task_id,
        )

    # state must not have moved
    assert states[task_id].state is TaskState.QUEUED
    assert storage.load_state(project, task_id).state is TaskState.QUEUED


def test_invalid_transition_still_raises_on_replay(tmp_state):
    """The no-op tolerance is scoped to from==to; an invalid from!=to event
    sitting in a log must still surface on replay (never silently swallowed)."""
    project = "p20-invalid-replay"
    task_id = "t-invalid-replay"
    _seed(project, task_id, TaskState.QUEUED)
    storage.append_event(
        project, actor=ACTOR, type=EventType.TASK_TRANSITIONED,
        payload={"from": "QUEUED", "to": "MERGED", "notes": None}, task_id=task_id,
    )

    with pytest.raises(TransitionError):
        storage.replay(project)


# ---------------------------------------------------------------------------
# P36 oracles: fixed-target events (BLOCKED/SUPERSEDED/CANCELLED) get the
# same from==to idempotency as TASK_TRANSITIONED, since their target can
# equal the current state whenever the task is already there.

# Oracle O1: a duplicate TASK_BLOCKED for an already-BLOCKED task is a
# silent no-op -- no exception, state stays BLOCKED, no task_id affected.

def test_blocked_reassert_apply_is_silent_noop(tmp_state):
    project = "p36-blocked-noop"
    task_id = "t-blocked-noop"
    states = _seed(project, task_id, TaskState.QUEUED)

    storage.append_and_apply(
        project, states, actor=ACTOR, type=EventType.TASK_BLOCKED,
        payload={"from": "QUEUED", "blocker": _blocker("first").to_dict(), "notes": None},
        task_id=task_id,
    )
    assert states[task_id].state is TaskState.BLOCKED

    ev = storage.append_event(
        project, actor=ACTOR, type=EventType.TASK_BLOCKED,
        payload={"from": "BLOCKED", "blocker": _blocker("second").to_dict(), "notes": None},
        task_id=task_id,
    )
    affected = storage.apply_event(states, ev)  # must not raise

    assert affected == []
    assert states[task_id].state is TaskState.BLOCKED


# Oracle O2: replay() over a log with a duplicate TASK_BLOCKED for an
# already-BLOCKED task completes and the projection carries the LATEST
# blocker payload.

def test_replay_tolerates_duplicate_blocked_and_keeps_latest_blocker(tmp_state):
    project = "p36-blocked-replay"
    task_id = "t-blocked-replay"
    _seed(project, task_id, TaskState.QUEUED)

    storage.append_event(
        project, actor=ACTOR, type=EventType.TASK_BLOCKED,
        payload={"from": "QUEUED", "blocker": _blocker("first").to_dict(), "notes": None},
        task_id=task_id,
    )
    storage.append_event(
        project, actor=ACTOR, type=EventType.TASK_BLOCKED,
        payload={"from": "BLOCKED", "blocker": _blocker("second").to_dict(), "notes": None},
        task_id=task_id,
    )

    replayed = storage.replay(project)  # must not raise

    assert replayed[task_id].state is TaskState.BLOCKED
    assert replayed[task_id].blocker.unblock_condition == "second"


# Oracle O4: this relaxation is scoped to from==to only -- a genuinely
# illegal fixed-target transition (distinct from/to) still raises.

def test_invalid_fixed_target_transition_still_raises(tmp_state):
    project = "p36-invalid-fixed"
    task_id = "t-invalid-fixed"
    states = _seed(project, task_id, TaskState.COMPLETED)

    with pytest.raises(TransitionError):
        storage.append_and_apply(
            project, states, actor=ACTOR, type=EventType.TASK_BLOCKED,
            payload={"from": "COMPLETED", "blocker": _blocker("x").to_dict(), "notes": None},
            task_id=task_id,
        )

    # state must not have moved
    assert states[task_id].state is TaskState.COMPLETED
    assert storage.load_state(project, task_id).state is TaskState.COMPLETED


# The same no-op scoping also applies to TASK_SUPERSEDED / TASK_CANCELLED
# (fixed targets, no blocker payload to refresh).

@pytest.mark.parametrize("event_type,target", [
    (EventType.TASK_SUPERSEDED, TaskState.SUPERSEDED),
    (EventType.TASK_CANCELLED, TaskState.CANCELLED),
])
def test_superseded_cancelled_reassert_apply_is_silent_noop(tmp_state, event_type, target):
    project = f"p36-{target.value.lower()}-noop"
    task_id = "t-noop"
    states = _seed(project, task_id, TaskState.QUEUED)

    storage.append_and_apply(
        project, states, actor=ACTOR, type=event_type,
        payload={"from": "QUEUED", "notes": None}, task_id=task_id,
    )
    assert states[task_id].state is target

    ev = storage.append_event(
        project, actor=ACTOR, type=event_type,
        payload={"from": target.value, "notes": None}, task_id=task_id,
    )
    affected = storage.apply_event(states, ev)  # must not raise

    assert affected == []
    assert states[task_id].state is target


# ---------------------------------------------------------------------------
# Oracle O1 (append-doctor-hardening handoff): append_and_apply MUST validate
# a TASK_TRANSITIONED transition BEFORE appending, so an illegal transition
# never leaves a spurious rejected-transition event in the log.
#
# Pre-P36-hardening bug this guards against: append_and_apply used to call
# append_event() (writing the line to events.jsonl) FIRST, and only THEN
# apply_event() -- which is where check_task_transition() actually lived.
# When the transition was illegal, apply_event raised, but the event had
# ALREADY been appended: the log gained a permanent spurious entry for a
# transition that was never actually valid (8 such events had to be migrated
# out of dstdns/topos by hand). Validating in append_and_apply itself, before
# append_event is ever called, means the illegal-transition case has zero
# side effects on the log.

def test_illegal_transition_via_append_and_apply_appends_no_event(tmp_state):
    project = "p-validate-before-append-illegal"
    task_id = "t-illegal"
    states = _seed(project, task_id, TaskState.QUEUED)

    before = [e.type for e in storage.iter_events(project)]
    assert before == [EventType.TASK_CREATED]

    with pytest.raises(TransitionError):
        storage.append_and_apply(
            project, states, actor=ACTOR, type=EventType.TASK_TRANSITIONED,
            payload={"from": "QUEUED", "to": "MERGED", "notes": None}, task_id=task_id,
        )

    # No spurious event was appended by the rejected attempt: the log is
    # byte-for-byte the same length as before, still just TASK_CREATED.
    after = [e.type for e in storage.iter_events(project)]
    assert after == before

    # Neither the in-memory projection nor the on-disk statefile moved.
    assert states[task_id].state is TaskState.QUEUED
    assert storage.load_state(project, task_id).state is TaskState.QUEUED


def test_legal_transition_via_append_and_apply_still_appends_normally(tmp_state):
    """The validate-before-append guard must not interfere with the legal
    path: a valid transition still appends exactly one event and updates
    both the projection and the on-disk statefile."""
    project = "p-validate-before-append-legal"
    task_id = "t-legal"
    states = _seed(project, task_id, TaskState.QUEUED)

    ev = storage.append_and_apply(
        project, states, actor=ACTOR, type=EventType.TASK_TRANSITIONED,
        payload={"from": "QUEUED", "to": "ACTIVE", "notes": None}, task_id=task_id,
    )

    assert ev.type is EventType.TASK_TRANSITIONED
    types = [e.type for e in storage.iter_events(project)]
    assert types == [EventType.TASK_CREATED, EventType.TASK_TRANSITIONED]

    assert states[task_id].state is TaskState.ACTIVE
    assert storage.load_state(project, task_id).state is TaskState.ACTIVE


def test_illegal_fixed_target_transition_via_append_and_apply_appends_no_event(tmp_state):
    """Same validate-before-append guarantee for the fixed-target event
    types (TASK_BLOCKED/SUPERSEDED/CANCELLED), not just TASK_TRANSITIONED."""
    project = "p-validate-before-append-fixed"
    task_id = "t-fixed"
    states = _seed(project, task_id, TaskState.COMPLETED)  # terminal: no outgoing edges

    before = [e.type for e in storage.iter_events(project)]

    with pytest.raises(TransitionError):
        storage.append_and_apply(
            project, states, actor=ACTOR, type=EventType.TASK_BLOCKED,
            payload={"from": "COMPLETED", "blocker": _blocker("x").to_dict(), "notes": None},
            task_id=task_id,
        )

    after = [e.type for e in storage.iter_events(project)]
    assert after == before
    assert states[task_id].state is TaskState.COMPLETED
    assert storage.load_state(project, task_id).state is TaskState.COMPLETED
