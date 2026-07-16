"""Tests for handoffctl.storage. PACKAGE P20.

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

from handoffctl import storage
from handoffctl.types import (
    Actor, ActorKind, EventType, TaskState, TaskStateFile, TransitionError,
    utc_now,
)

ACTOR = Actor(kind=ActorKind.TICK, id="test")


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
