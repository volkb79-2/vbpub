"""SQLite backend for the event/state store. PACKAGE SP01
(docs/plan-state-integrity.md Part A).

THE store implementation (CR-04b removed the selector and the file backend)
(unset / any other value keeps the file backend the default). Implements the
SAME public functions as `storage.py`'s file backend
(`append_event`/`iter_events`/`load_state`/`save_state`/`list_states`/
`append_and_apply`/`replay`) against a per-project SQLite database file, so
callers see an identical interface -- this module is a backend swap, not a
new API.

`apply_event` and `_validate_before_append` (the pure event->projection logic
and the pre-append transition guard) are reused UNCHANGED from `storage.py` --
they operate purely on an in-memory `dict[str, TaskStateFile]` + `Event`, no
I/O, so there is nothing backend-specific about them.

Schema (A.1), one documented extension:
  events(seq PK AUTOINCREMENT, schema_version, ts, actor_kind, actor_id, type,
         payload JSON, task_id, attempt_id, wave_id, decision_id)
    + indexes on task_id and type. `project` is deliberately NOT a column: one
    DB file per project (A.0), so it is supplied by the caller's own
    `project` argument when reconstructing an `Event`, not persisted.
  states(task_id PK, project, state, since, handoff_path, notes,
         attempts JSON, schema_version, data JSON)
    The literal A.1 columns are all present and populated, but
    `TaskStateFile` has more fields than A.1's literal list (wave_id, paused,
    blocker, gate_results, leases_held, progress_units, merge_commit). Rather
    than hand-roll a second copy of every nested dataclass's (de)serialization
    across individual SQL columns, `data` holds the full canonical
    `TaskStateFile.to_dict()` JSON blob, which is what `load_state`/
    `list_states` actually reconstruct from -- the other columns stay
    populated for cheap ops/grep queries but are not the read path.
  meta(k PK, v) -- reserved for a future db schema_version marker; unused by
    SP01 (no schema migrations exist yet).

A.2 -- the transactional mutation: `append_and_apply` runs the event INSERT
and every affected task's projection UPSERT inside ONE `BEGIN IMMEDIATE ...
COMMIT` transaction. A failure anywhere between the INSERT and the COMMIT
rolls back the whole transaction -- the event never lands and the projection
never changes, atomically. This is the whole point of the migration: the
file backend's event-append-then-statefile-save is two separate durable
writes that CAN diverge on a crash; here they cannot.
"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any, Iterator

from . import paths
from .projection import (
    SCHEMA_VERSION, apply_event, _trace, _validate_before_append,
)
from .types import Actor, ActorKind, Event, EventType, TaskStateFile, iso, parse_iso, utc_now

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS events (
  seq INTEGER PRIMARY KEY AUTOINCREMENT,
  schema_version INTEGER NOT NULL,
  ts TEXT NOT NULL,
  actor_kind TEXT NOT NULL,
  actor_id TEXT NOT NULL,
  type TEXT NOT NULL,
  payload TEXT NOT NULL,
  task_id TEXT,
  attempt_id TEXT,
  wave_id TEXT,
  decision_id TEXT
);
CREATE INDEX IF NOT EXISTS events_task ON events(task_id);
CREATE INDEX IF NOT EXISTS events_type ON events(type);
CREATE TABLE IF NOT EXISTS states (
  task_id TEXT PRIMARY KEY,
  project TEXT NOT NULL,
  state TEXT NOT NULL,
  since TEXT,
  handoff_path TEXT,
  notes TEXT,
  attempts TEXT NOT NULL,
  schema_version INTEGER NOT NULL,
  data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
"""


# ---------------------------------------------------------------------------
# connection management

def db_path(project: str) -> Path:
    """The per-project SQLite database file (A.0: one DB file per project)."""
    return paths.project_dir(project) / "state.db"


def _connect(project: str) -> sqlite3.Connection:
    """Open a connection with WAL pragmas set. Schema DDL runs only on the
    very first connect ever made for this project (guarded by the db file
    not yet existing) so later connects -- same or a different process --
    never re-issue `CREATE TABLE IF NOT EXISTS`, keeping concurrent
    reader/writer access free of incidental schema-lock contention."""
    paths.ensure_layout(project)
    p = db_path(project)
    is_new = not p.exists()
    conn = sqlite3.connect(str(p), timeout=5.0, isolation_level=None)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=5000")
    if is_new:
        conn.executescript(_SCHEMA_SQL)
    return conn


# ---------------------------------------------------------------------------
# row <-> dataclass conversion

def _row_to_event(row: tuple, project: str) -> Event:
    (seq, schema_version, ts, actor_kind, actor_id, type_, payload,
     task_id, attempt_id, wave_id, decision_id) = row
    return Event(
        schema_version=schema_version,
        sequence=seq,
        timestamp=parse_iso(ts),
        project=project,
        actor=Actor(kind=ActorKind(actor_kind), id=actor_id),
        type=EventType(type_),
        payload=json.loads(payload),
        task_id=task_id,
        attempt_id=attempt_id,
        wave_id=wave_id,
        decision_id=decision_id,
    )


def _insert_event(
    conn: sqlite3.Connection,
    project: str,
    *,
    actor: Actor,
    type: EventType,
    payload: dict[str, Any],
    task_id: str | None = None,
    attempt_id: str | None = None,
    wave_id: str | None = None,
    decision_id: str | None = None,
    timestamp=None,
) -> Event:
    """INSERT one event row on an already-open connection/transaction and
    return the resulting `Event` (with its assigned `seq`). Does not commit
    -- the caller controls the transaction boundary."""
    ts_dt = timestamp or utc_now()
    cur = conn.execute(
        "INSERT INTO events "
        "(schema_version, ts, actor_kind, actor_id, type, payload, "
        " task_id, attempt_id, wave_id, decision_id) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            SCHEMA_VERSION, iso(ts_dt), actor.kind.value, actor.id, type.value,
            json.dumps(payload, separators=(",", ":"), sort_keys=True),
            task_id, attempt_id, wave_id, decision_id,
        ),
    )
    # CR-04b (D-L3 oracle 4): the event-append TRACE moved here with the
    # append itself. It lived on the deleted file backend, so removing that
    # backend would have silently dropped a logging contract the daemon's
    # diagnostics depend on -- the kind of loss a "just delete the old path"
    # change makes invisibly. Only reachable on the LIVE append path; replay
    # never calls this, so a replay stays silent.
    # NB: "event_type", not "event" -- structlog's bound-logger methods take
    # their message as `event`, so a same-named kwarg collides.
    _trace("event-append", project=project, event_type=type.value,
           task=task_id, attempt=attempt_id, sequence=cur.lastrowid)
    return Event(
        schema_version=SCHEMA_VERSION,
        sequence=cur.lastrowid,
        timestamp=ts_dt,
        project=project,
        actor=actor,
        type=type,
        payload=payload,
        task_id=task_id,
        attempt_id=attempt_id,
        wave_id=wave_id,
        decision_id=decision_id,
    )


def _upsert_state_row(conn: sqlite3.Connection, state: TaskStateFile) -> None:
    """UPSERT one task's projection row on an already-open
    connection/transaction. This is the atomicity oracle's injection seam:
    a test monkeypatches this function to simulate a failure that happens
    AFTER the event INSERT but before the projection change is durable."""
    d = state.to_dict()
    conn.execute(
        "INSERT INTO states "
        "(task_id, project, state, since, handoff_path, notes, attempts, "
        " schema_version, data) "
        "VALUES (?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(task_id) DO UPDATE SET "
        "project=excluded.project, state=excluded.state, since=excluded.since, "
        "handoff_path=excluded.handoff_path, notes=excluded.notes, "
        "attempts=excluded.attempts, schema_version=excluded.schema_version, "
        "data=excluded.data",
        (
            state.task_id, state.project, d["state"], d.get("since"),
            d.get("handoff_path"), d.get("notes"),
            json.dumps(d.get("attempts") or [], separators=(",", ":"), sort_keys=True),
            d["schema_version"],
            json.dumps(d, separators=(",", ":"), sort_keys=True),
        ),
    )


# ---------------------------------------------------------------------------
# public API (mirrors storage.py's file-backend surface)

def append_event(
    project: str,
    *,
    actor: Actor,
    type: EventType,
    payload: dict[str, Any],
    task_id: str | None = None,
    attempt_id: str | None = None,
    wave_id: str | None = None,
    decision_id: str | None = None,
    timestamp=None,
) -> Event:
    """Append one event in its own transaction. No projection effect --
    matches the file backend's `append_event`, which likewise never touches
    statefiles."""
    conn = _connect(project)
    try:
        conn.execute("BEGIN IMMEDIATE")
        ev = _insert_event(
            conn, project, actor=actor, type=type, payload=payload,
            task_id=task_id, attempt_id=attempt_id, wave_id=wave_id,
            decision_id=decision_id, timestamp=timestamp,
        )
        conn.commit()
        return ev
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def iter_events(project: str, since: int = 0) -> Iterator[Event]:
    conn = _connect(project)
    try:
        cur = conn.execute(
            "SELECT seq, schema_version, ts, actor_kind, actor_id, type, "
            "payload, task_id, attempt_id, wave_id, decision_id "
            "FROM events WHERE seq > ? ORDER BY seq",
            (since,),
        )
        rows = cur.fetchall()
    finally:
        conn.close()
    for row in rows:
        yield _row_to_event(row, project)


#: `meta` key holding the per-project reconcile deadman heartbeat (CR-16).
_HEARTBEAT_KEY = "reconcile_heartbeat"


def record_heartbeat(project: str) -> None:
    """Stamp "a reconcile pass completed for this project, now" (CR-16).

    A GAUGE, not an event: exactly one row in `meta`, overwritten in place.
    The heartbeat is written once per reconcile pass -- every
    `reconcile_interval_seconds` (30 by default), forever, per project --
    so recording it as an event would append ~2,880 rows per project per
    day against an organic rate of ~70-110/day measured on the live
    stores. That is not a log any full-log reader survives, and `run_pass`
    itself re-reads the WHOLE log every pass as an authoritative snapshot
    input. Same database file, same durability, same restart-safety, same
    "a separate process can read it with the daemon dead" property --
    without turning the event log into a heartbeat log.
    """
    conn = _connect(project)
    try:
        # The writer guarantees the table: `_connect` only runs the schema
        # DDL for a brand-new database, and `meta` has been in that schema
        # since SP01 but was never written to, so this costs one no-op
        # statement and removes a whole class of "reserved, therefore
        # never actually exercised" surprise.
        conn.execute("CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT)")
        conn.execute(
            "INSERT INTO meta(k, v) VALUES (?, ?) "
            "ON CONFLICT(k) DO UPDATE SET v = excluded.v",
            (_HEARTBEAT_KEY, iso(utc_now())),
        )
    finally:
        conn.close()


def read_heartbeat(project: str):
    """The last `record_heartbeat` stamp, or None if none was ever written."""
    conn = _connect(project)
    try:
        row = conn.execute(
            "SELECT v FROM meta WHERE k = ?", (_HEARTBEAT_KEY,)).fetchone()
    finally:
        conn.close()
    return parse_iso(row[0]) if row else None


def load_state(project: str, task_id: str) -> TaskStateFile | None:
    conn = _connect(project)
    try:
        cur = conn.execute("SELECT data FROM states WHERE task_id = ?", (task_id,))
        row = cur.fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    _trace("statefile-read", project=project, task=task_id)
    return TaskStateFile.from_dict(json.loads(row[0]))


def save_state(state: TaskStateFile) -> None:
    """Standalone write (no event) -- used by doctor's `rebuild(write=True)`
    recovery path, matching the file backend's `save_state`."""
    conn = _connect(state.project)
    try:
        conn.execute("BEGIN IMMEDIATE")
        _upsert_state_row(conn, state)
        conn.commit()
        _trace("statefile-write", project=state.project, task=state.task_id)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def backup(project: str, suffix: str = "bak") -> Path:
    """A CONSISTENT copy of the whole store, taken while writers may be live.

    CR-04b (contract item 4). Uses sqlite3's own online-backup API rather than
    copying the file: under WAL a plain file copy can capture a database whose
    committed data still lives in a `-wal` sidecar the copy does not include,
    producing a backup that restores to a state that never existed. The API
    walks the pages under a read lock instead, so the copy is a real point in
    time.

    Returns the backup path. Replaces `doctor`'s per-statefile `.bak` copies,
    which backed up an artifact the file backend wrote and this store does not
    -- deleting that backend without this would have quietly removed the
    "back up before you repair" step from the one command whose whole job is
    repairing a divergent projection.
    """
    src = db_path(project)
    dest = src.with_name(src.name + "." + suffix)
    conn = _connect(project)
    try:
        target = sqlite3.connect(str(dest))
        try:
            with target:
                conn.backup(target)
        finally:
            target.close()
    finally:
        conn.close()
    return dest


def restore(project: str, backup_path: Path) -> None:
    """Replace the store with a backup taken by :func:`backup`.

    Deliberately a whole-file replace rather than a merge: a partial restore
    would leave events from two different histories in one log, and there is
    no rule that could reconcile them afterwards.
    """
    if not backup_path.exists():
        raise FileNotFoundError(f"no such backup: {backup_path}")
    dest = db_path(project)
    paths.ensure_layout(project)
    tmp = dest.with_name(dest.name + ".restore-tmp")
    conn = sqlite3.connect(str(backup_path))
    try:
        target = sqlite3.connect(str(tmp))
        try:
            with target:
                conn.backup(target)
        finally:
            target.close()
    finally:
        conn.close()
    os.replace(tmp, dest)
    # The WAL/SHM sidecars belong to the REPLACED database; leaving them would
    # let SQLite apply another history's pages over the restored one.
    for sidecar in (dest.with_name(dest.name + "-wal"), dest.with_name(dest.name + "-shm")):
        sidecar.unlink(missing_ok=True)


def export_jsonl(project: str) -> str:
    """The event log as JSONL, one event per line, in sequence order.

    The interchange format: it is what a human reads, what a bug report
    attaches, and what :func:`import_jsonl` re-imports. Deterministic
    (sorted keys, no incidental whitespace) so two exports of the same history
    are byte-identical and a diff of them means something.
    """
    lines = [json.dumps(ev.to_dict(), separators=(",", ":"), sort_keys=True)
             for ev in iter_events(project)]
    return "\n".join(lines) + ("\n" if lines else "")


def import_jsonl(project: str, text: str) -> int:
    """Load an exported log into an EMPTY store; returns the event count.

    Refuses a non-empty store rather than appending: an import that merged
    into existing history would produce a log with two origins and sequence
    numbers that mean nothing, which is unrecoverable rather than merely
    wrong. Rebuilds the projection by replay, so the imported store is
    reachable from its own events.
    """
    conn = _connect(project)
    try:
        existing = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    finally:
        conn.close()
    if existing:
        raise ValueError(
            f"refusing to import into a non-empty store for {project!r} "
            f"({existing} events already present)")

    count = 0
    conn = _connect(project)
    try:
        conn.execute("BEGIN IMMEDIATE")
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            ev = Event.from_dict(json.loads(line))
            _insert_event(
                conn, project, actor=ev.actor, type=ev.type, payload=ev.payload,
                task_id=ev.task_id, attempt_id=ev.attempt_id, wave_id=ev.wave_id,
                decision_id=ev.decision_id, timestamp=ev.timestamp,
            )
            count += 1
        conn.commit()
    except Exception:  # census: cleanup/containment (CR-04b)
        conn.rollback()
        raise
    finally:
        conn.close()

    # Rebuild the projection from the imported log, in one transaction.
    states = replay(project)
    conn = _connect(project)
    try:
        conn.execute("BEGIN IMMEDIATE")
        for state in states.values():
            _upsert_state_row(conn, state)
        conn.commit()
    except Exception:  # census: cleanup/containment (CR-04b)
        conn.rollback()
        raise
    finally:
        conn.close()
    return count


def list_states(project: str) -> dict[str, TaskStateFile]:
    conn = _connect(project)
    try:
        cur = conn.execute("SELECT data FROM states ORDER BY task_id")
        rows = cur.fetchall()
    finally:
        conn.close()
    out: dict[str, TaskStateFile] = {}
    for (data,) in rows:
        tsf = TaskStateFile.from_dict(json.loads(data))
        out[tsf.task_id] = tsf
    return out


def _committed_states(conn: sqlite3.Connection) -> dict[str, TaskStateFile]:
    """The projection as COMMITTED, read on an already-open transaction."""
    out: dict[str, TaskStateFile] = {}
    for (data,) in conn.execute("SELECT data FROM states ORDER BY task_id"):
        tsf = TaskStateFile.from_dict(json.loads(data))
        out[tsf.task_id] = tsf
    return out


def append_and_apply(
    project: str,
    states: dict[str, TaskStateFile],
    **kwargs: Any,
) -> Event:
    """THE canonical mutation, atomically: BEGIN IMMEDIATE -> read committed
    projection -> validate -> INSERT the event -> UPSERT every affected row ->
    COMMIT. Any exception rolls the whole transaction back: neither the event
    nor the projection change persists (A.2).

    CR-04 (DR-09; amendment section 3.1). The event is now validated and
    applied against the state COMMITTED INSIDE THIS TRANSACTION, never
    against the caller's `states` argument.

    Before this, the caller supplied the projection the store wrote back. The
    reconcile loop and each HTTP handler thread hold their own snapshot, so a
    UI write landing during a reconcile pass was overwritten by that pass's
    stale copy -- and because the event and the projection were written in one
    transaction, the result was atomic and wrong, which is worse than a
    visible race. The plan's own atomicity promise is unreachable while the
    projection comes from a caller.

    `states` is now a caller CACHE rather than the source of truth: it is
    refreshed from the committed result after COMMIT, so existing callers
    keep working and observe the merged state instead of their own. That is
    why the signature is unchanged -- 188 call sites do not have to be
    rewritten to get the fix, and none of them can opt out of it.

    The whole projection is read per write rather than just the affected
    rows: `apply_event` decides which tasks an event touches by looking at
    the map, so narrowing the read would mean predicting the answer before
    asking the question. BEGIN IMMEDIATE already serializes writers, and a
    project's task table is small.
    """
    conn = _connect(project)
    try:
        conn.execute("BEGIN IMMEDIATE")
        committed = _committed_states(conn)
        _validate_before_append(committed, **kwargs)
        ev = _insert_event(conn, project, **kwargs)
        affected = apply_event(committed, ev)
        for tid in affected:
            _upsert_state_row(conn, committed[tid])
        conn.commit()
        for tid in affected:
            _trace("statefile-write", project=project, task=tid)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    # Refresh the caller's cache from committed truth. Done AFTER commit so a
    # rolled-back mutation never leaves the caller believing it happened.
    for tid in affected:
        states[tid] = committed[tid]
    return ev


def replay(project: str) -> dict[str, TaskStateFile]:
    """Rebuild the full projection from the event log alone -- an audit that,
    thanks to A.2's atomicity, can never diverge from `list_states()`."""
    states: dict[str, TaskStateFile] = {}
    for ev in iter_events(project):
        apply_event(states, ev)
    return states
