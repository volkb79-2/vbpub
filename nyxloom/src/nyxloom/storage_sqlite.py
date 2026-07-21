"""SQLite backend for the event/state store. PACKAGE SP01
(docs/plan-state-integrity.md Part A).

Selected dark, behind `storage.py`'s `NYXLOOM_STATE_BACKEND=sqlite` selector
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
import sqlite3
from pathlib import Path
from typing import Any, Iterator

from . import paths
from .storage import SCHEMA_VERSION, apply_event, _validate_before_append
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


def load_state(project: str, task_id: str) -> TaskStateFile | None:
    conn = _connect(project)
    try:
        cur = conn.execute("SELECT data FROM states WHERE task_id = ?", (task_id,))
        row = cur.fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    return TaskStateFile.from_dict(json.loads(row[0]))


def save_state(state: TaskStateFile) -> None:
    """Standalone write (no event) -- used by doctor's `rebuild(write=True)`
    recovery path, matching the file backend's `save_state`."""
    conn = _connect(state.project)
    try:
        conn.execute("BEGIN IMMEDIATE")
        _upsert_state_row(conn, state)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


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


def append_and_apply(
    project: str,
    states: dict[str, TaskStateFile],
    **kwargs: Any,
) -> Event:
    """THE canonical mutation, atomically: validate -> BEGIN IMMEDIATE ->
    INSERT the event -> UPSERT every affected task's projection row ->
    COMMIT. Any exception in that block rolls back the whole transaction --
    neither the event nor the projection change persists (A.2)."""
    _validate_before_append(states, **kwargs)
    conn = _connect(project)
    try:
        conn.execute("BEGIN IMMEDIATE")
        ev = _insert_event(conn, project, **kwargs)
        for tid in apply_event(states, ev):
            _upsert_state_row(conn, states[tid])
        conn.commit()
        return ev
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def replay(project: str) -> dict[str, TaskStateFile]:
    """Rebuild the full projection from the event log alone -- an audit that,
    thanks to A.2's atomicity, can never diverge from `list_states()`."""
    states: dict[str, TaskStateFile] = {}
    for ev in iter_events(project):
        apply_event(states, ev)
    return states
