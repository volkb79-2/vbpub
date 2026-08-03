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

import json
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


def test_append_event_standalone_rolls_back_on_failure(sqlite_backend, monkeypatch):
    """append_event's OWN try/except/rollback (distinct from
    append_and_apply's): a failure inside its single-statement transaction
    rolls back cleanly and re-raises -- no event row is left behind."""
    project = "sp01-append-event-rollback"
    original = storage_sqlite._insert_event

    def _boom(conn, project_, **kwargs):
        raise RuntimeError("simulated insert failure")

    monkeypatch.setattr(storage_sqlite, "_insert_event", _boom)
    try:
        with pytest.raises(RuntimeError, match="simulated insert failure"):
            storage.append_event(project, actor=ACTOR, type=EventType.PROJECT_REGISTERED,
                                  payload={})
    finally:
        monkeypatch.setattr(storage_sqlite, "_insert_event", original)

    assert list(storage.iter_events(project)) == []


def test_save_state_standalone_rolls_back_on_failure(sqlite_backend, monkeypatch):
    """save_state's OWN try/except/rollback (the doctor recovery-path write,
    no event involved): a failure inside its transaction rolls back cleanly
    and re-raises -- no projection row is left behind."""
    project = "sp01-save-state-rollback"
    task_id = "t-save-rollback"
    tsf = TaskStateFile(schema_version=storage.SCHEMA_VERSION, task_id=task_id,
                         project=project, state=TaskState.QUEUED, since=utc_now())

    original = storage_sqlite._upsert_state_row

    def _boom(conn, state):
        raise RuntimeError("simulated upsert failure")

    monkeypatch.setattr(storage_sqlite, "_upsert_state_row", _boom)
    try:
        with pytest.raises(RuntimeError, match="simulated upsert failure"):
            storage.save_state(tsf)
    finally:
        monkeypatch.setattr(storage_sqlite, "_upsert_state_row", original)

    assert storage.load_state(project, task_id) is None


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


# ==========================================================================
# CR-04 (DR-09; amendment section 3.1): the projection is derived from
# COMMITTED state, never from the caller's snapshot.
# ==========================================================================


def test_a_write_from_a_stale_snapshot_does_not_lose_the_committed_one(sqlite_backend):
    """THE defect this change exists to remove.

    The reconcile loop and each HTTP handler thread hold their OWN projection
    map. Before this, `append_and_apply` wrote back the map the caller
    supplied, so a write landing during a reconcile pass was overwritten by
    that pass's stale copy -- and because the event and the projection were
    written in one transaction, the loss was ATOMIC, which is worse than a
    visible race because nothing downstream could detect it.

    Two callers hold snapshots taken before either wrote. The first
    transitions the task; the second, still holding QUEUED, appends an
    unrelated event about the SAME task. Under the old contract that second
    write reverted the transition."""
    project, task_id = "sp01-lost-update", "t-race"
    first = _seed(project, task_id, TaskState.QUEUED)
    second = storage.list_states(project)               # snapshot: still QUEUED
    assert second[task_id].state is TaskState.QUEUED

    storage.append_and_apply(
        project, first, actor=ACTOR, type=EventType.TASK_TRANSITIONED,
        payload={"from": "QUEUED", "to": "ACTIVE"}, task_id=task_id)

    # The stale caller now writes an event of its own about the same task.
    storage.append_and_apply(
        project, second, actor=ACTOR, type=EventType.CONFIG_CHANGED,
        payload={"key": "value"}, task_id=task_id)

    committed = storage.load_state(project, task_id)
    assert committed.state is TaskState.ACTIVE, (
        "the stale snapshot's QUEUED overwrote a committed transition")
    assert storage.replay(project)[task_id].state is TaskState.ACTIVE, (
        "the projection and the event log disagree")


def test_the_callers_snapshot_is_refreshed_from_committed_truth(sqlite_backend):
    """`states` is a cache now, not the source. Refreshing it is what lets the
    existing call sites keep working AND makes it impossible for one of them
    to opt out of the fix by holding its own copy: after the call the caller
    observes committed state rather than what it supplied."""
    project, task_id = "sp01-refresh", "t-refresh"
    first = _seed(project, task_id, TaskState.QUEUED)
    stale = storage.list_states(project)

    storage.append_and_apply(
        project, first, actor=ACTOR, type=EventType.TASK_TRANSITIONED,
        payload={"from": "QUEUED", "to": "ACTIVE"}, task_id=task_id)
    assert stale[task_id].state is TaskState.QUEUED, "precondition: stale"

    # The stale caller submits the NEXT legal move from COMMITTED state. It
    # is illegal from the caller's own view (QUEUED has no edge to
    # AWAITING_REVIEW), so this only succeeds because validation reads
    # committed state -- and afterwards the cache must show where the task
    # really is, not where the caller thought it was.
    storage.append_and_apply(
        project, stale, actor=ACTOR, type=EventType.TASK_TRANSITIONED,
        payload={"from": "ACTIVE", "to": "AWAITING_REVIEW"}, task_id=task_id)

    assert stale[task_id].state is TaskState.AWAITING_REVIEW, (
        "the caller's cache was not refreshed from committed truth")
    assert storage.load_state(project, task_id).state is TaskState.AWAITING_REVIEW


def test_a_caller_cannot_smuggle_a_hand_edited_field_through_the_store(sqlite_backend):
    """A strengthening that falls out of deriving the projection from
    committed state: the store now writes what the EVENT says, and nothing
    else. Before, any field a caller mutated on its snapshot was persisted as
    a side effect of the next unrelated append -- a projection change with no
    event behind it, which replay could never reproduce."""
    project, task_id = "sp01-smuggle", "t-smuggle"
    states = _seed(project, task_id, TaskState.QUEUED)
    states[task_id].notes = "invented out of band"

    storage.append_and_apply(
        project, states, actor=ACTOR, type=EventType.CONFIG_CHANGED,
        payload={}, task_id=task_id)

    assert storage.load_state(project, task_id).notes is None
    assert storage.replay(project)[task_id].notes is None


def test_a_transition_is_validated_against_committed_state_not_the_snapshot(sqlite_backend):
    """Validation moved inside the transaction too. A caller holding a
    snapshot from before a transition could otherwise submit an edge that is
    legal from ITS view and illegal from the committed one -- writing an
    event the log can never replay."""
    _seed("demo", "t-validate", TaskState.QUEUED)
    stale = storage_sqlite.list_states("demo")          # QUEUED

    storage_sqlite.append_and_apply(
        "demo", storage_sqlite.list_states("demo"),
        actor=ACTOR,
        type=EventType.TASK_TRANSITIONED,
        payload={"from": "QUEUED", "to": "ACTIVE"}, task_id="t-validate")

    before = len(list(storage_sqlite.iter_events("demo")))
    with pytest.raises(Exception):
        # QUEUED->NEEDS_DECISION reads legal from the stale snapshot; from
        # committed ACTIVE it is not.
        storage_sqlite.append_and_apply(
            "demo", stale, actor=ACTOR, type=EventType.TASK_TRANSITIONED,
            payload={"from": "QUEUED", "to": "NEEDS_DECISION"}, task_id="t-validate")

    assert len(list(storage_sqlite.iter_events("demo"))) == before, (
        "an illegal-from-committed transition still reached the log")


def test_a_rolled_back_mutation_leaves_the_callers_cache_untouched(sqlite_backend):
    """The refresh happens AFTER commit, so a failed write never leaves the
    caller believing it happened -- which would be the same lie as the lost
    update, in the other direction."""
    _seed("demo", "t-rollback", TaskState.QUEUED)
    snapshot = storage_sqlite.list_states("demo")
    original_state = snapshot["t-rollback"].state

    real_upsert = storage_sqlite._upsert_state_row
    try:
        storage_sqlite._upsert_state_row = lambda *a, **k: (_ for _ in ()).throw(
            RuntimeError("projection write failed"))
        with pytest.raises(RuntimeError):
            storage_sqlite.append_and_apply(
                "demo", snapshot, actor=ACTOR,
                type=EventType.TASK_TRANSITIONED,
                payload={"from": "QUEUED", "to": "ACTIVE"}, task_id="t-rollback")
    finally:
        storage_sqlite._upsert_state_row = real_upsert

    assert snapshot["t-rollback"].state is original_state
    assert storage_sqlite.list_states("demo")["t-rollback"].state is original_state


# ==========================================================================
# CR-04b: backup / restore / interchange, and the store fault matrix.
# ==========================================================================


def test_a_backup_taken_during_writes_restores_and_replays_identically(sqlite_backend):
    """Contract acceptance: "backup during writes restores and replays
    identically".

    Taken with sqlite3's online-backup API rather than a file copy: under WAL
    a plain copy can capture a database whose committed data still lives in a
    `-wal` sidecar the copy does not include, producing a backup that restores
    to a state that never existed."""
    project, task_id = "sp04-backup", "t-backup"
    states = _seed(project, task_id, TaskState.QUEUED)
    storage.append_and_apply(
        project, states, actor=ACTOR, type=EventType.TASK_TRANSITIONED,
        payload={"from": "QUEUED", "to": "ACTIVE"}, task_id=task_id)

    backup_path = storage.backup(project)
    at_backup = storage.replay(project)

    # Keep writing AFTER the backup: a backup that silently included later
    # writes would look correct here and be wrong in the one case it exists
    # for.
    storage.append_and_apply(
        project, states, actor=ACTOR, type=EventType.TASK_TRANSITIONED,
        payload={"from": "ACTIVE", "to": "AWAITING_REVIEW"}, task_id=task_id)
    assert storage.load_state(project, task_id).state is TaskState.AWAITING_REVIEW

    storage.restore(project, backup_path)

    assert storage.load_state(project, task_id).state is TaskState.ACTIVE
    restored = storage.replay(project)
    assert {k: v.to_dict() for k, v in restored.items()} == \
           {k: v.to_dict() for k, v in at_backup.items()}, (
        "the restored store does not replay to the state the backup captured")


def test_an_exported_log_reimports_to_a_byte_identical_projection(sqlite_backend):
    """The amendment's added acceptance: "a JSONL export re-imported into an
    empty store replays to a byte-identical projection".

    Byte-identical, not merely equivalent: the export is what a human attaches
    to a bug report and what a rollback replays, so two exports of the same
    history have to be comparable with `diff`."""
    project, task_id = "sp04-export", "t-export"
    states = _seed(project, task_id, TaskState.QUEUED)
    storage.append_and_apply(
        project, states, actor=ACTOR, type=EventType.TASK_TRANSITIONED,
        payload={"from": "QUEUED", "to": "ACTIVE"}, task_id=task_id)
    storage.append_and_apply(
        project, states, actor=ACTOR, type=EventType.PROGRESS_RECORDED,
        payload={"units": ["u1"]}, task_id=task_id)

    text = storage.export_jsonl(project)
    original = json.dumps({k: v.to_dict() for k, v in storage.replay(project).items()},
                          sort_keys=True)

    # Empty the store and re-import into the SAME project. Not a copy under a
    # different name: an event carries the project it belongs to, so a
    # cross-project import is a different history that merely looks similar --
    # and the acceptance is about restoring THIS one.
    db = storage_sqlite.db_path(project)
    for path in (db, db.with_name(db.name + "-wal"), db.with_name(db.name + "-shm")):
        path.unlink(missing_ok=True)
    assert storage.replay(project) == {}

    assert storage.import_jsonl(project, text) == 3

    assert json.dumps({k: v.to_dict() for k, v in storage.replay(project).items()},
                      sort_keys=True) == original
    # ...and the projection the store SERVES matches the one replay derives,
    # so the import rebuilt both halves rather than just the log.
    assert json.dumps({k: v.to_dict() for k, v in storage.list_states(project).items()},
                      sort_keys=True) == original
    # ...and re-exporting is byte-identical to what went in.
    assert storage.export_jsonl(project) == text


def test_importing_into_a_non_empty_store_is_refused(sqlite_backend):
    """An import that merged into existing history would produce a log with
    two origins and sequence numbers that mean nothing -- unrecoverable
    rather than merely wrong. Refused, with the existing store untouched."""
    project = "sp04-nonempty"
    _seed(project, "t-existing", TaskState.QUEUED)
    text = storage.export_jsonl(project)
    before = list(storage.iter_events(project))

    with pytest.raises(ValueError, match="non-empty"):
        storage.import_jsonl(project, text)

    assert [e.sequence for e in storage.iter_events(project)] == \
           [e.sequence for e in before]


def test_an_empty_export_round_trips(sqlite_backend):
    """A project with no history exports to nothing and imports to nothing --
    the degenerate case a naive implementation turns into a stray blank line
    that then fails to re-import."""
    assert storage.export_jsonl("sp04-empty") == ""
    assert storage.import_jsonl("sp04-empty-copy", "") == 0
    assert storage.replay("sp04-empty-copy") == {}


def test_restoring_a_backup_that_is_not_there_is_refused(sqlite_backend):
    project = "sp04-missing-backup"
    _seed(project, "t1", TaskState.QUEUED)
    with pytest.raises(FileNotFoundError):
        storage.restore(project, storage_sqlite.db_path(project).with_name("nope.bak"))
    assert storage.load_state(project, "t1") is not None


# --------------------------------------------------------------------------
# fault matrix: "disk-full, corruption, locked database, invalid event, and
# failed upcast produce no authorizing effect"


def test_an_invalid_event_never_reaches_the_log(sqlite_backend):
    """An illegal transition is refused BEFORE the insert, so the log never
    contains an event replay cannot reproduce. (Pre-P36, the event was
    appended and only then validated, leaving spurious rejected transitions
    in the log that had to be migrated out of two projects by hand.)"""
    project, task_id = "sp04-invalid", "t-invalid"
    states = _seed(project, task_id, TaskState.QUEUED)
    before = len(list(storage.iter_events(project)))

    with pytest.raises(Exception):
        storage.append_and_apply(
            project, states, actor=ACTOR, type=EventType.TASK_TRANSITIONED,
            payload={"from": "QUEUED", "to": "AWAITING_REVIEW"}, task_id=task_id)

    assert len(list(storage.iter_events(project))) == before
    assert storage.load_state(project, task_id).state is TaskState.QUEUED


def test_a_write_failure_leaves_neither_the_event_nor_the_projection(sqlite_backend):
    """Disk-full, in the shape that actually reaches this code: the insert
    succeeds and the projection write raises. Both must vanish -- an event
    without its projection is a log the store disagrees with, and the
    disagreement is invisible until something plans from it."""
    project, task_id = "sp04-diskfull", "t-diskfull"
    states = _seed(project, task_id, TaskState.QUEUED)
    before_events = len(list(storage.iter_events(project)))

    real = storage_sqlite._upsert_state_row
    try:
        storage_sqlite._upsert_state_row = lambda *a, **k: (_ for _ in ()).throw(
            sqlite3.OperationalError("database or disk is full"))
        with pytest.raises(sqlite3.OperationalError):
            storage.append_and_apply(
                project, states, actor=ACTOR, type=EventType.TASK_TRANSITIONED,
                payload={"from": "QUEUED", "to": "ACTIVE"}, task_id=task_id)
    finally:
        storage_sqlite._upsert_state_row = real

    assert len(list(storage.iter_events(project))) == before_events
    assert storage.load_state(project, task_id).state is TaskState.QUEUED
    assert storage.replay(project)[task_id].state is TaskState.QUEUED


def test_a_corrupt_database_raises_rather_than_reading_as_empty(sqlite_backend):
    """The most dangerous failure this store has: an unreadable database that
    answers "no tasks" instead of raising. Every planner rule treats an empty
    projection as a project with nothing to do, so a corrupt store would read
    as a QUIET one -- CR-02's fail-open shape, at the layer underneath it."""
    project = "sp04-corrupt"
    _seed(project, "t-corrupt", TaskState.QUEUED)
    db = storage_sqlite.db_path(project)
    for sidecar in (db.with_name(db.name + "-wal"), db.with_name(db.name + "-shm")):
        sidecar.unlink(missing_ok=True)
    db.write_bytes(b"this is not a database, it is a text file" * 40)

    with pytest.raises(sqlite3.DatabaseError):
        storage.list_states(project)
    with pytest.raises(sqlite3.DatabaseError):
        list(storage.iter_events(project))


def test_a_locked_database_raises_rather_than_silently_skipping_the_write(
        sqlite_backend, monkeypatch):
    """A writer that cannot get the lock must fail loudly. Returning without
    writing would make `append_and_apply` a function that sometimes does
    nothing and says it succeeded, which is the one thing a canonical
    mutation may never be."""
    project, task_id = "sp04-locked", "t-locked"
    states = _seed(project, task_id, TaskState.QUEUED)

    holder = sqlite3.connect(str(storage_sqlite.db_path(project)), timeout=0.1)
    try:
        holder.execute("BEGIN EXCLUSIVE")
        monkeypatch.setattr(storage_sqlite, "_BUSY_TIMEOUT_MS", 100, raising=False)
        with pytest.raises(sqlite3.OperationalError):
            conn = sqlite3.connect(str(storage_sqlite.db_path(project)), timeout=0.1)
            try:
                conn.execute("BEGIN IMMEDIATE")
            finally:
                conn.close()
    finally:
        holder.rollback()
        holder.close()

    # The store is untouched and still usable once the lock clears.
    assert storage.load_state(project, task_id).state is TaskState.QUEUED
    storage.append_and_apply(
        project, states, actor=ACTOR, type=EventType.TASK_TRANSITIONED,
        payload={"from": "QUEUED", "to": "ACTIVE"}, task_id=task_id)
    assert storage.load_state(project, task_id).state is TaskState.ACTIVE


def test_a_blank_line_in_an_export_is_skipped_not_imported(sqlite_backend):
    """Exports are edited by hand -- that is half the reason for having one --
    and an editor adding a trailing blank line must not become an event that
    fails to parse."""
    project = "sp04-blank"
    _seed(project, "t-blank", TaskState.QUEUED)
    text = storage.export_jsonl(project)

    assert storage.import_jsonl("sp04-blank-copy", "\n\n" + text + "\n\n") == 1


def test_a_malformed_line_rolls_the_whole_import_back(sqlite_backend):
    """An import is all-or-nothing. A half-imported log is a history with a
    hole in it, and nothing downstream could tell that from a short one."""
    project = "sp04-partial"
    _seed(project, "t-partial", TaskState.QUEUED)
    good = storage.export_jsonl(project)

    with pytest.raises(Exception):
        storage.import_jsonl("sp04-partial-copy", good + "{ not json\n")

    assert list(storage.iter_events("sp04-partial-copy")) == []
    assert storage.list_states("sp04-partial-copy") == {}


def test_a_projection_failure_during_import_rolls_back_the_projection(sqlite_backend):
    """The import's second transaction. Its rollback matters for the same
    reason the first one's does: a store whose log imported and whose
    projection did not is one that reads as empty while holding a full
    history."""
    project = "sp04-import-proj"
    _seed(project, "t-ip", TaskState.QUEUED)
    text = storage.export_jsonl(project)

    real = storage_sqlite._upsert_state_row
    try:
        storage_sqlite._upsert_state_row = lambda *a, **k: (_ for _ in ()).throw(
            sqlite3.OperationalError("database or disk is full"))
        with pytest.raises(sqlite3.OperationalError):
            storage.import_jsonl("sp04-import-proj-copy", text)
    finally:
        storage_sqlite._upsert_state_row = real

    assert storage.list_states("sp04-import-proj-copy") == {}


def test_a_transition_event_carrying_notes_records_them(sqlite_backend):
    """`notes` on a TASK_TRANSITIONED payload is the operator-visible reason a
    task moved. It is projected from the EVENT (the only way a note can now
    reach a row at all, since CR-04a stopped the store writing back caller
    edits), so nothing else records it if this does not."""
    project, task_id = "sp04-notes", "t-notes"
    states = _seed(project, task_id, TaskState.QUEUED)

    storage.append_and_apply(
        project, states, actor=ACTOR, type=EventType.TASK_TRANSITIONED,
        payload={"from": "QUEUED", "to": "ACTIVE", "notes": "provider throttled"},
        task_id=task_id)

    assert storage.load_state(project, task_id).notes == "provider throttled"
    assert storage.replay(project)[task_id].notes == "provider throttled"


def test_a_reasserted_block_refreshes_its_reason(sqlite_backend):
    """A task already BLOCKED, blocked again with a NEWER reason.

    The transition is a no-op (from == to), but the blocker and its notes are
    still refreshed: the second reason is the current one, and leaving the
    first in place would show an operator a stale explanation for why work
    stopped -- which is the only thing that record is for."""
    project, task_id = "sp04-reblock", "t-reblock"
    states = _seed(project, task_id, TaskState.QUEUED)
    blocker = {"type": "contract", "unblock_condition": "first", "detail": None}

    storage.append_and_apply(
        project, states, actor=ACTOR, type=EventType.TASK_BLOCKED,
        payload={"blocker": blocker, "notes": "first reason"}, task_id=task_id)
    assert storage.load_state(project, task_id).state is TaskState.BLOCKED

    storage.append_and_apply(
        project, states, actor=ACTOR, type=EventType.TASK_BLOCKED,
        payload={"blocker": {"type": "environment", "unblock_condition": "second",
                              "detail": None},
                  "notes": "second reason"}, task_id=task_id)

    current = storage.load_state(project, task_id)
    assert current.state is TaskState.BLOCKED
    assert current.notes == "second reason"
    assert current.blocker.unblock_condition == "second"
    assert storage.replay(project)[task_id].notes == "second reason"
