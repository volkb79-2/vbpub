"""Event log + statefile store. FROZEN CORE (SPEC §2, §5.6, §12).

Authority model: `events.jsonl` (append-only, per project) is the runtime
truth; statefiles under `state/` are projections and MUST be reproducible by
`replay()`. The ONE canonical mutation path is `append_and_apply()` — append
the event, apply it to the in-memory projection, save the affected
statefile(s) atomically. A crash between append and save is healed by replay
(the event wins).

Projection contract (what emitters MUST put in payloads):

  TASK_CREATED          payload["statefile"] = full TaskStateFile dict
  TASK_TRANSITIONED     payload {"from": str, "to": str, "notes": str|None}
  TASK_BLOCKED          payload {"from": str, "blocker": Blocker dict, "notes"?}
  TASK_SUPERSEDED /
  TASK_CANCELLED        payload {"from": str, "notes"?}
  ATTEMPT_*             payload["attempt"] = FULL updated Attempt dict (upsert
                        by attempt_id; CREATED appends, others replace)
  GATE_FINISHED         payload["gate_result"] = GateResult dict
  MERGE_RECORDED        payload {"merge_commit": str}
  PROGRESS_RECORDED     payload {"units": [str]}
  LEASE_ACQUIRED/-RELEASED  payload {"lease": str}   (task-scoped only)
  PAUSE_SET/-CLEARED    with task_id -> statefile.paused (project-level pause
                        is the flag file in paths.py, not a statefile field)
  WAVE_OPENED           payload {"task_ids": [str]} -> sets wave_id on each
  everything else       no projection effect

All other modules MUST NOT write events.jsonl or statefiles directly.

Store layering (CR-04b, DR-09). There is ONE store. This module is the public
API, `storage_sqlite.py` is the implementation, and `projection.py` holds the
pure validation/projection both need. The three-way split is what removed the
import cycle: `storage_sqlite` used to import the pure functions from here
while this module dispatched into it, which only held together because the
dispatch was a function-local import inside a selector branch.

The environment-variable store selector and the file backend are GONE. SQLite has
been the sole live store since 2026-07-21 (every registered project has a
`state.db`; `events.jsonl` was retired to `.pre-sqlite`), so the selector had
been dead configuration guarding a code path nothing ran -- while still being
the thing a reader had to reason about to know what the daemon does.
"""

from __future__ import annotations

from typing import Any, Iterator

from . import paths, storage_sqlite
# Re-exported so the ~190 existing `storage.<name>` call sites keep working:
# this module is the PUBLIC store API, `projection.py` is where the pure
# functions live, and `storage_sqlite.py` is the implementation. Splitting the
# three is what removed the import cycle (see projection.py's docstring).
from .projection import (                                    # noqa: F401
    SCHEMA_VERSION, apply_event, _attempt_regression, _transition_target,
    _validate_before_append, _trace, _EARLY_ATTEMPT_STATES,
    _TRANSITION_EVENT_TYPES,
)
from .log import get_logger
from .types import (
    ActorKind, Actor, Attempt, AttemptState, Event, EventType, GateResult,
    Blocker, TERMINAL_ATTEMPT_STATES, TaskState, TaskStateFile,
    check_task_transition, iso, parse_iso, utc_now,
)

# P05a (docs/plan-logging.md §5, §2 "replay is silent"): TRACE-only, and
# ONLY on the LIVE append/save paths below (append_event, save_state) --
# NEVER inside apply_event/replay, which is the shared projection function
# both the live path (via append_and_apply) AND a full replay() call. A
# restart replays the whole event history through apply_event alone
# (append_event/save_state are never called by replay()), so keeping every
# log call out of apply_event/replay is what makes replay silent by
# construction rather than by a level check that could be flipped on by
# accident.
log = get_logger("storage")


# ---------------------------------------------------------------------------
# event log

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
    """Append one event; the store assigns the sequence."""
    return storage_sqlite.append_event(
        project, actor=actor, type=type, payload=payload, task_id=task_id,
        attempt_id=attempt_id, wave_id=wave_id, decision_id=decision_id,
        timestamp=timestamp,
    )


def iter_events(project: str, since: int = 0) -> Iterator[Event]:
    yield from storage_sqlite.iter_events(project, since=since)


# ---------------------------------------------------------------------------
# liveness deadman gauge (CR-16, RISK-007)

def record_heartbeat(project: str) -> None:
    """Stamp "a reconcile pass completed for this project, now".

    Deliberately NOT an event: see `storage_sqlite.record_heartbeat`. One
    overwritten row, so a per-pass heartbeat costs the event log nothing.
    """
    storage_sqlite.record_heartbeat(project)


def read_heartbeat(project: str):
    """The last heartbeat stamp (aware datetime), or None if never written."""
    return storage_sqlite.read_heartbeat(project)


# ---------------------------------------------------------------------------
# statefiles

def load_state(project: str, task_id: str) -> TaskStateFile | None:
    return storage_sqlite.load_state(project, task_id)


def save_state(state: TaskStateFile) -> None:
    """Persist one task's projection row.

    CR-04b: this is the LAST caller-supplied projection write left in the
    public API, and it is kept only for the replay/repair paths that legitimately
    rebuild a row from the event log. Ordinary mutation goes through
    `append_and_apply`, which derives the row from committed state inside the
    transaction (CR-04a) -- a caller reaching for this to record an ordinary
    change is writing a projection with no event behind it, which replay can
    never reproduce.
    """
    storage_sqlite.save_state(state)


def list_states(project: str) -> dict[str, TaskStateFile]:
    return storage_sqlite.list_states(project)


# ---------------------------------------------------------------------------
# backup / interchange (CR-04b, contract item 4)

def backup(project: str, suffix: str = "bak"):
    """A consistent copy of the whole store; returns its path."""
    return storage_sqlite.backup(project, suffix=suffix)


def restore(project: str, backup_path) -> None:
    """Replace the store with a backup taken by `backup`."""
    storage_sqlite.restore(project, backup_path)


def export_jsonl(project: str) -> str:
    """The event log as deterministic JSONL, in sequence order."""
    return storage_sqlite.export_jsonl(project)


def import_jsonl(project: str, text: str) -> int:
    """Load an exported log into an EMPTY store; returns the event count."""
    return storage_sqlite.import_jsonl(project, text)


# ---------------------------------------------------------------------------
# projection

def append_and_apply(
    project: str,
    states: dict[str, TaskStateFile],
    **kwargs: Any,
) -> Event:
    """THE canonical mutation: one transaction that reads committed state,
    validates, appends the event and updates the projection (CR-04a).

    `states` is a caller CACHE, refreshed from the committed result -- never
    the source the projection is written from. kwargs are `append_event`'s
    keyword arguments.
    """
    return storage_sqlite.append_and_apply(project, states, **kwargs)


def replay(project: str) -> dict[str, TaskStateFile]:
    """Rebuild the full projection from the event log alone."""
    return storage_sqlite.replay(project)
