"""Tests for the SQLite storage backend. PACKAGE SP01
(docs/plan-state-integrity.md Part A / A.3).

Independent of `tests/test_storage.py` (scope.touch for this handoff does not
include modifying that file) but exercises the SAME public API
(`storage.append_and_apply`/`load_state`/`list_states`/`iter_events`/
`append_event`/`save_state`/`replay`) with `NYXLOOM_STATE_BACKEND=sqlite` set,
so every test here goes through `storage.py`'s dark-flag selector into
`storage_sqlite.py`, never calling `storage_sqlite` functions directly except
where the oracle itself requires reaching into the backend (the atomicity
injection seam, and the raw second connection for the WAL-concurrency check).

Oracles (SP01):
  1. API parity -- append_and_apply -> load_state reflects the transition;
     list_states; iter_events order/since=; append_event (no projection
     effect); save_state (standalone write, no event).
  2. Atomicity -- the whole point. A failure injected into the projection
     UPSERT, AFTER the event INSERT but inside the same transaction, leaves
     NEITHER the event NOR the projection change persisted.
  3. `seq` is gap-free monotonic across a sequence of appends, INCLUDING
     across a rolled-back attempt (a failed append must not consume a seq).
  4. `replay()` rebuilds the identical projection to the one built
     incrementally via `append_and_apply` (the divergence audit that can now
     never diverge).
  5. Concurrent writer+reader under WAL: a reader opened while a write
     transaction is open and uncommitted sees the PRIOR, consistent
     snapshot -- no torn/partial read -- and sees the new snapshot only
     after commit.
"""

from __future__ import annotations

import sqlite3

import pytest

from nyxloom import storage, storage_sqlite
from nyxloom.types import (
    Actor, ActorKind, EventType, TaskState, TaskStateFile, utc_now,
)

ACTOR = Actor(kind=ActorKind.TICK, id="test")


@pytest.fixture()
def sqlite_backend(tmp_state, monkeypatch):
    """Isolated XDG state root (tmp_state) PLUS the SQLite backend dark flag
    enabled for the duration of one test."""
    monkeypatch.setenv("NYXLOOM_STATE_BACKEND", "sqlite")
    return tmp_state


def _seed(project: str, task_id: str, state: TaskState) -> dict:
    """Seed a single task's projection via TASK_CREATED; returns the live
    `states` map used by append_and_apply (mirrors test_storage.py's _seed)."""
    states: dict = {}
    tsf = TaskStateFile(schema_version=storage.SCHEMA_VERSION, task_id=task_id,
                         project=project, state=state, since=utc_now())
    storage.append_and_apply(
        project, states, actor=ACTOR, type=EventType.TASK_CREATED,
        payload={"statefile": tsf.to_dict()}, task_id=task_id,
    )
    return states


# ---------------------------------------------------------------------------
# Oracle 1: API parity

def test_append_and_apply_updates_load_state(sqlite_backend):
    project = "sp01-parity"
    task_id = "t-parity"
    states = _seed(project, task_id, TaskState.QUEUED)

    storage.append_and_apply(
        project, states, actor=ACTOR, type=EventType.TASK_TRANSITIONED,
        payload={"from": "QUEUED", "to": "ACTIVE", "notes": None}, task_id=task_id,
    )

    assert states[task_id].state is TaskState.ACTIVE
    on_disk = storage.load_state(project, task_id)
    assert on_disk is not None
    assert on_disk.state is TaskState.ACTIVE


def test_load_state_missing_task_returns_none(sqlite_backend):
    assert storage.load_state("sp01-missing", "no-such-task") is None


def test_list_states_returns_all_tasks_sorted_by_task_id(sqlite_backend):
    project = "sp01-list"
    _seed(project, "t-b", TaskState.QUEUED)
    _seed(project, "t-a", TaskState.QUEUED)

    out = storage.list_states(project)
    assert set(out) == {"t-a", "t-b"}
    assert list(out) == ["t-a", "t-b"]
    assert out["t-a"].state is TaskState.QUEUED


def test_iter_events_orders_by_sequence_and_since_filters(sqlite_backend):
    project = "sp01-iter"
    task_id = "t-iter"
    _seed(project, task_id, TaskState.QUEUED)  # seq 1
    storage.append_event(project, actor=ACTOR, type=EventType.PROGRESS_RECORDED,
                          payload={"units": ["a"]}, task_id=task_id)  # seq 2
    storage.append_event(project, actor=ACTOR, type=EventType.PROGRESS_RECORDED,
                          payload={"units": ["b"]}, task_id=task_id)  # seq 3

    evs = list(storage.iter_events(project))
    assert [e.sequence for e in evs] == [1, 2, 3]
    assert [e.type for e in evs] == [
        EventType.TASK_CREATED, EventType.PROGRESS_RECORDED, EventType.PROGRESS_RECORDED,
    ]
    assert evs[0].project == project
    assert evs[0].actor.kind is ActorKind.TICK

    evs_since = list(storage.iter_events(project, since=1))
    assert [e.sequence for e in evs_since] == [2, 3]


def test_append_event_standalone_has_no_projection_effect(sqlite_backend):
    """append_event alone (no `states` arg) must not create/alter any
    projection row -- matches the file backend exactly."""
    project = "sp01-standalone-event"
    ev = storage.append_event(project, actor=ACTOR, type=EventType.PROJECT_REGISTERED,
                               payload={})
    assert ev.sequence == 1
    assert storage.list_states(project) == {}


def test_save_state_standalone_write_appends_no_event(sqlite_backend):
    """save_state (the doctor rebuild(write=True) recovery path) persists a
    TaskStateFile directly, bypassing the event log entirely."""
    project = "sp01-save-state"
    task_id = "t-save"
    tsf = TaskStateFile(schema_version=storage.SCHEMA_VERSION, task_id=task_id,
                         project=project, state=TaskState.QUEUED, since=utc_now())
    storage.save_state(tsf)

    loaded = storage.load_state(project, task_id)
    assert loaded is not None
    assert loaded.state is TaskState.QUEUED
    assert list(storage.iter_events(project)) == []


# ---------------------------------------------------------------------------
# Oracle 2: atomicity -- the whole point

def test_atomicity_upsert_failure_rolls_back_event_and_projection(sqlite_backend, monkeypatch):
    project = "sp01-atomic"
    task_id = "t-atomic"
    states = _seed(project, task_id, TaskState.QUEUED)

    events_before = [e.sequence for e in storage.iter_events(project)]
    assert storage.load_state(project, task_id).state is TaskState.QUEUED

    def _boom(conn, state):
        raise RuntimeError("simulated failure mid-transaction (projection UPSERT)")

    monkeypatch.setattr(storage_sqlite, "_upsert_state_row", _boom)

    with pytest.raises(RuntimeError, match="simulated failure"):
        storage.append_and_apply(
            project, states, actor=ACTOR, type=EventType.TASK_TRANSITIONED,
            payload={"from": "QUEUED", "to": "ACTIVE", "notes": None}, task_id=task_id,
        )

    # Fresh reads from the DB (not the caller's possibly-mutated in-memory
    # `states` dict) confirm the WHOLE transaction rolled back: the event
    # insert that happened before the injected failure did not survive, and
    # the on-disk projection is untouched.
    events_after = [e.sequence for e in storage.iter_events(project)]
    assert events_after == events_before  # no new event row committed

    state_after = storage.load_state(project, task_id)
    assert state_after.state is TaskState.QUEUED  # projection unchanged on disk


def test_atomicity_validate_before_append_still_blocks_illegal_transitions(sqlite_backend):
    """The SQLite backend still runs `_validate_before_append` before ever
    touching the DB -- an illegal transition raises with zero side effects,
    matching the file backend's P36 guarantee."""
    project = "sp01-validate"
    task_id = "t-validate"
    states = _seed(project, task_id, TaskState.QUEUED)

    from nyxloom.types import TransitionError

    with pytest.raises(TransitionError):
        storage.append_and_apply(
            project, states, actor=ACTOR, type=EventType.TASK_TRANSITIONED,
            payload={"from": "QUEUED", "to": "MERGED", "notes": None}, task_id=task_id,
        )

    assert [e.type for e in storage.iter_events(project)] == [EventType.TASK_CREATED]
    assert storage.load_state(project, task_id).state is TaskState.QUEUED


# ---------------------------------------------------------------------------
# Oracle 3: seq is gap-free monotonic

def test_seq_gap_free_monotonic_across_appends(sqlite_backend):
    project = "sp01-seq"
    task_id = "t-seq"
    _seed(project, task_id, TaskState.QUEUED)  # seq 1

    seqs = [1]
    for i in range(5):
        ev = storage.append_event(
            project, actor=ACTOR, type=EventType.PROGRESS_RECORDED,
            payload={"units": [str(i)]}, task_id=task_id,
        )
        seqs.append(ev.sequence)

    assert seqs == list(range(1, 7))


def test_seq_no_gap_after_rolled_back_attempt(sqlite_backend, monkeypatch):
    """A failed (rolled-back) append_and_apply must not consume a seq value
    -- the next successful append gets the value the failed one would have
    used. This falls directly out of the AUTOINCREMENT bump living inside
    the same rolled-back transaction as the event INSERT."""
    project = "sp01-seq-rollback"
    task_id = "t-seq-rollback"
    states = _seed(project, task_id, TaskState.QUEUED)  # consumes seq 1

    original = storage_sqlite._upsert_state_row

    def _boom(conn, state):
        raise RuntimeError("simulated failure")

    monkeypatch.setattr(storage_sqlite, "_upsert_state_row", _boom)
    with pytest.raises(RuntimeError):
        storage.append_and_apply(
            project, states, actor=ACTOR, type=EventType.TASK_TRANSITIONED,
            payload={"from": "QUEUED", "to": "ACTIVE", "notes": None}, task_id=task_id,
        )
    monkeypatch.setattr(storage_sqlite, "_upsert_state_row", original)

    ev = storage.append_event(
        project, actor=ACTOR, type=EventType.PROGRESS_RECORDED,
        payload={"units": ["x"]}, task_id=task_id,
    )
    assert ev.sequence == 2  # the rolled-back attempt did not consume seq 2


# ---------------------------------------------------------------------------
# Oracle 4: replay() audit matches the incrementally-applied projection

def test_replay_matches_incrementally_applied_states(sqlite_backend):
    project = "sp01-replay"
    task_id = "t-replay"
    states = _seed(project, task_id, TaskState.CARVED)
    storage.append_and_apply(
        project, states, actor=ACTOR, type=EventType.TASK_TRANSITIONED,
        payload={"from": "CARVED", "to": "QUEUED", "notes": None}, task_id=task_id,
    )
    storage.append_and_apply(
        project, states, actor=ACTOR, type=EventType.TASK_TRANSITIONED,
        payload={"from": "QUEUED", "to": "ACTIVE", "notes": None}, task_id=task_id,
    )
    storage.append_and_apply(
        project, states, actor=ACTOR, type=EventType.PROGRESS_RECORDED,
        payload={"units": ["done-part-1"]}, task_id=task_id,
    )

    replayed = storage.replay(project)
    on_disk = storage.list_states(project)

    assert replayed.keys() == on_disk.keys()
    assert replayed[task_id].to_dict() == on_disk[task_id].to_dict()
    assert replayed[task_id].state is TaskState.ACTIVE
    assert replayed[task_id].progress_units == ["done-part-1"]


# ---------------------------------------------------------------------------
# Oracle 5: concurrent writer + reader under WAL

def test_concurrent_reader_sees_consistent_prior_snapshot_under_wal(sqlite_backend):
    project = "sp01-wal"
    task_id = "t-wal"
    _seed(project, task_id, TaskState.QUEUED)

    baseline = storage.load_state(project, task_id)
    assert baseline.state is TaskState.QUEUED

    updated = TaskStateFile.from_dict(baseline.to_dict())
    updated.state = TaskState.ACTIVE

    writer = sqlite3.connect(str(storage_sqlite.db_path(project)),
                              isolation_level=None, timeout=5.0)
    writer.execute("PRAGMA busy_timeout=5000")
    try:
        writer.execute("BEGIN IMMEDIATE")
        storage_sqlite._insert_event(
            writer, project, actor=ACTOR, type=EventType.PROGRESS_RECORDED,
            payload={"units": ["mid-write"]}, task_id=task_id,
        )
        storage_sqlite._upsert_state_row(writer, updated)

        # Readers opened WHILE the writer's transaction is open and
        # uncommitted must see the prior, consistent snapshot -- no
        # torn/partial read of the in-flight write.
        reader_state = storage.load_state(project, task_id)
        reader_events = list(storage.iter_events(project))
        assert reader_state.state is TaskState.QUEUED
        assert len(reader_events) == 1

        writer.commit()
    finally:
        writer.close()

    # After commit, a fresh read sees the new snapshot.
    after_state = storage.load_state(project, task_id)
    after_events = list(storage.iter_events(project))
    assert after_state.state is TaskState.ACTIVE
    assert len(after_events) == 2
