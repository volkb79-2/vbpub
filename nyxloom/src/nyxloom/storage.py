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

Backend selector (PACKAGE SP01, docs/plan-state-integrity.md Part A): this
module's implementation below is the FILE backend and stays the default,
unchanged. Setting env var `NYXLOOM_STATE_BACKEND=sqlite` switches every
public function here to delegate to `storage_sqlite.py` instead -- a dark
flag so landing SP01 does not change the running daemon's behavior. Only
`apply_event`/`_validate_before_append` (pure, in-memory, no I/O) are shared
by both backends unchanged.
"""

from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
from typing import Any, Iterator

from . import paths
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


def _trace(msg: str, **kw: Any) -> None:
    """Fire a TRACE record if the CURRENTLY configured logging wrapper class
    supports it -- a silent no-op otherwise. TRACE(5) is nyxloom.log's own
    custom extension (see log.py's ``_make_wrapper_class``); it exists only
    once ``log.configure()``/``set_level()`` has installed that wrapper
    class into structlog's global config. structlog's OWN default
    (never-configured) global state lacks it entirely, so a bare
    ``log.trace(...)`` call would raise ``AttributeError`` the first time
    this hot path (every event append / statefile read-write) runs in any
    process or test that has not called ``log.configure()`` yet. Degrading
    to a no-op here is exactly the same "logging is additive, never load-
    bearing" contract §2 already establishes -- a TRACE record silently
    missing before boot-time configure() is no worse than TRACE being
    filtered out at a higher effective level, which every caller already
    tolerates."""
    trace_fn = getattr(log, "trace", None)
    if trace_fn is not None:
        trace_fn(msg, **kw)


SCHEMA_VERSION = 1

_EARLY_ATTEMPT_STATES = frozenset({AttemptState.CREATED, AttemptState.PREFLIGHTING})


def _sqlite_backend_enabled() -> bool:
    """DARK FLAG (SP01): `NYXLOOM_STATE_BACKEND=sqlite` selects the SQLite
    backend; anything else, including unset, keeps the file backend below as
    the default so merging this does not change the running daemon."""
    return os.environ.get("NYXLOOM_STATE_BACKEND") == "sqlite"


def _attempt_regression(current: AttemptState, incoming: AttemptState) -> bool:
    """True when applying `incoming` over `current` would move an attempt
    backwards in its lifecycle: out of a terminal state, or from a
    post-launch state (RUNNING/STALLED/INTERRUPTED) back to CREATED/
    PREFLIGHTING. Legitimate backward edges (STALLED->RUNNING,
    INTERRUPTED->RUNNING) are NOT regressions."""
    if current in TERMINAL_ATTEMPT_STATES:
        return incoming is not current
    return current not in _EARLY_ATTEMPT_STATES and incoming in _EARLY_ATTEMPT_STATES


# ---------------------------------------------------------------------------
# event log

def _last_sequence(path: Path) -> int:
    if not path.exists() or path.stat().st_size == 0:
        return 0
    with path.open("rb") as f:
        f.seek(0, os.SEEK_END)
        size = f.tell()
        back = min(size, 65536)
        f.seek(size - back)
        chunk = f.read().decode("utf-8", errors="replace")
    lines = [ln for ln in chunk.splitlines() if ln.strip()]
    if not lines:
        return 0
    return int(json.loads(lines[-1])["sequence"])


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
    """Append one event under an exclusive flock; assigns the sequence."""
    if _sqlite_backend_enabled():
        from . import storage_sqlite
        return storage_sqlite.append_event(
            project, actor=actor, type=type, payload=payload, task_id=task_id,
            attempt_id=attempt_id, wave_id=wave_id, decision_id=decision_id,
            timestamp=timestamp,
        )
    paths.ensure_layout(project)
    path = paths.events_path(project)
    with path.open("a+", encoding="utf-8") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        try:
            ev = Event(
                schema_version=SCHEMA_VERSION,
                sequence=_last_sequence(path) + 1,
                timestamp=timestamp or utc_now(),
                project=project,
                actor=actor,
                type=type,
                payload=payload,
                task_id=task_id,
                attempt_id=attempt_id,
                wave_id=wave_id,
                decision_id=decision_id,
            )
            f.write(json.dumps(ev.to_dict(), separators=(",", ":"), sort_keys=True) + "\n")
            f.flush()
            os.fsync(f.fileno())
            # P05a (§6 P05a oracle 4): an event append emits TRACE. Only
            # reachable on the LIVE append path -- replay() never calls
            # append_event, so this can never fire during a replay.
            # NB: field name deliberately "event_type", NOT "event" -- every
            # structlog bound-logger method's own first positional parameter
            # is itself named `event` (the message), so a same-named kwarg
            # collides ("got multiple values for argument 'event'"); see the
            # matching comment on daemon.py's "daemon started" log call for
            # the analogous "level" clash with add_log_level.
            _trace("event-append", project=project, event_type=type.value,
                   task=task_id, attempt=attempt_id, sequence=ev.sequence)
            return ev
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)


def iter_events(project: str, since: int = 0) -> Iterator[Event]:
    if _sqlite_backend_enabled():
        from . import storage_sqlite
        yield from storage_sqlite.iter_events(project, since=since)
        return
    path = paths.events_path(project)
    if not path.exists():
        return
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            ev = Event.from_dict(json.loads(line))
            if ev.sequence > since:
                yield ev


# ---------------------------------------------------------------------------
# statefiles

def load_state(project: str, task_id: str) -> TaskStateFile | None:
    if _sqlite_backend_enabled():
        from . import storage_sqlite
        return storage_sqlite.load_state(project, task_id)
    p = paths.statefile_path(project, task_id)
    if not p.exists():
        return None
    _trace("statefile-read", project=project, task=task_id)
    return TaskStateFile.from_dict(json.loads(p.read_text(encoding="utf-8")))


def save_state(state: TaskStateFile) -> None:
    """Atomic write (tmp + rename) under a per-task flock."""
    if _sqlite_backend_enabled():
        from . import storage_sqlite
        storage_sqlite.save_state(state)
        return
    paths.ensure_layout(state.project)
    p = paths.statefile_path(state.project, state.task_id)
    lock = p.with_suffix(".lock")
    with lock.open("a") as lf:
        fcntl.flock(lf.fileno(), fcntl.LOCK_EX)
        try:
            tmp = p.with_suffix(".tmp")
            tmp.write_text(
                json.dumps(state.to_dict(), indent=1, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            os.replace(tmp, p)
            _trace("statefile-write", project=state.project, task=state.task_id)
        finally:
            fcntl.flock(lf.fileno(), fcntl.LOCK_UN)


def list_states(project: str) -> dict[str, TaskStateFile]:
    if _sqlite_backend_enabled():
        from . import storage_sqlite
        return storage_sqlite.list_states(project)
    d = paths.state_dir(project)
    out: dict[str, TaskStateFile] = {}
    if not d.exists():
        return out
    for p in sorted(d.glob("*.json")):
        tsf = TaskStateFile.from_dict(json.loads(p.read_text(encoding="utf-8")))
        out[tsf.task_id] = tsf
    return out


# ---------------------------------------------------------------------------
# projection

_TRANSITION_EVENT_TYPES = (
    EventType.TASK_TRANSITIONED, EventType.TASK_BLOCKED,
    EventType.TASK_SUPERSEDED, EventType.TASK_CANCELLED,
)


def _transition_target(t: EventType, payload: dict[str, Any]) -> TaskState | None:
    """Resolve the destination TaskState for a transition-shaped event type,
    or None if `t` carries no transition semantics. Shared by apply_event
    (live/replay projection) and append_and_apply's pre-append validation
    guard so the two never drift apart on what counts as "the target"."""
    resolvers = {
        EventType.TASK_TRANSITIONED: lambda: TaskState(payload["to"]),
        EventType.TASK_BLOCKED: lambda: TaskState.BLOCKED,
        EventType.TASK_SUPERSEDED: lambda: TaskState.SUPERSEDED,
        EventType.TASK_CANCELLED: lambda: TaskState.CANCELLED,
    }
    resolver = resolvers.get(t)
    return resolver() if resolver else None


def apply_event(states: dict[str, TaskStateFile], ev: Event) -> list[str]:
    """Apply one event to the projection map. Returns affected task_ids.

    Tolerant on replay (unknown attempt -> upsert; missing task -> skip with
    no error only for genuinely task-less events), strict on semantics
    (task transitions are validated).
    """
    t = ev.type
    affected: list[str] = []

    if t is EventType.TASK_CREATED:
        tsf = TaskStateFile.from_dict(ev.payload["statefile"])
        states[tsf.task_id] = tsf
        return [tsf.task_id]

    if t is EventType.WAVE_OPENED:
        for tid in ev.payload.get("task_ids", []):
            if tid in states:
                states[tid].wave_id = ev.wave_id
                affected.append(tid)
        return affected

    if ev.task_id is None or ev.task_id not in states:
        return affected
    tsf = states[ev.task_id]

    if t in _TRANSITION_EVENT_TYPES:
        to = _transition_target(t, ev.payload)
        if tsf.state == to:
            # P20/P36: application-level idempotency, not a graph edge. Two
            # planning passes racing off a shared state snapshot can both
            # compute the same from==to edge (e.g. both see CARVED and plan
            # CARVED->QUEUED after the first already applied it), and the
            # same from==to event can also already sit in a replayed log.
            # This is not scoped to TASK_TRANSITIONED's free-parameter target:
            # a fixed-target event (TASK_BLOCKED/SUPERSEDED/CANCELLED) hits
            # from==to just as easily when the task is *already* in that
            # state -- e.g. a second TASK_BLOCKED for an already-BLOCKED task,
            # which an append-only log can contain from before reconcile's
            # `!= BLOCKED` re-emit guard existed (P36). Treat it as a silent
            # no-op: skip validation, raise nothing, and do not report the
            # task_id as affected (it is a re-assertion, not a transition).
            # This is the authoritative chokepoint for both live apply and
            # replay; a cheap belt-and-suspenders guard also exists at the
            # daemon layer (Daemon._execute, commit fdff733) that skips
            # constructing the event in the first place — kept intentionally
            # duplicated, not stale.
            #
            # A re-asserted TASK_BLOCKED still refreshes the blocker/notes
            # payload in place (mutating the statefile object already held
            # by the caller's `states` map) so a newer blocker reason wins on
            # replay, even though this no-op does not itself trigger a save.
            if t is EventType.TASK_BLOCKED:
                tsf.blocker = Blocker.from_dict(ev.payload["blocker"])
                if ev.payload.get("notes"):
                    tsf.notes = ev.payload["notes"]
            return affected
        check_task_transition(tsf.state, to)
        tsf.state = to
        tsf.since = ev.timestamp
        if t is EventType.TASK_BLOCKED:
            tsf.blocker = Blocker.from_dict(ev.payload["blocker"])
        elif to not in (TaskState.BLOCKED,):
            tsf.blocker = None
        if ev.payload.get("notes"):
            tsf.notes = ev.payload["notes"]
        affected.append(tsf.task_id)

    elif t.value.startswith("ATTEMPT_"):
        att = Attempt.from_dict(ev.payload["attempt"])
        existing = tsf.attempt_by_id(att.attempt_id)
        if existing is None:
            tsf.attempts.append(att)
        elif _attempt_regression(existing.state, att.state):
            # Monotonic guard: a late-arriving event (e.g. the daemon's
            # PREFLIGHTED racing the wrapper's STARTED/EXITED) must never
            # regress an attempt that already progressed. Ignore it.
            pass
        else:
            tsf.attempts[tsf.attempts.index(existing)] = att
        affected.append(tsf.task_id)

    elif t is EventType.GATE_FINISHED:
        tsf.gate_results.append(GateResult.from_dict(ev.payload["gate_result"]))
        affected.append(tsf.task_id)

    elif t is EventType.MERGE_RECORDED:
        tsf.merge_commit = ev.payload["merge_commit"]
        affected.append(tsf.task_id)

    elif t is EventType.PROGRESS_RECORDED:
        for u in ev.payload.get("units", []):
            if u not in tsf.progress_units:
                tsf.progress_units.append(u)
        affected.append(tsf.task_id)

    elif t is EventType.LEASE_ACQUIRED:
        if ev.payload["lease"] not in tsf.leases_held:
            tsf.leases_held.append(ev.payload["lease"])
        affected.append(tsf.task_id)

    elif t is EventType.LEASE_RELEASED:
        if ev.payload["lease"] in tsf.leases_held:
            tsf.leases_held.remove(ev.payload["lease"])
        affected.append(tsf.task_id)

    elif t is EventType.PAUSE_SET:
        tsf.paused = True
        affected.append(tsf.task_id)

    elif t is EventType.PAUSE_CLEARED:
        tsf.paused = False
        affected.append(tsf.task_id)

    return affected


def _validate_before_append(states: dict[str, TaskStateFile], **kwargs: Any) -> None:
    """Pre-append guard for the canonical mutation path.

    Validate a TASK_TRANSITIONED/BLOCKED/SUPERSEDED/CANCELLED transition
    against the CURRENT projected state BEFORE the event is written to the
    log, so a genuinely illegal transition never lands in events.jsonl.

    Pre-P36 bug this replaces: append_and_apply appended the event first and
    only THEN validated it (inside apply_event); when the transition was
    illegal, apply_event raised after the append already happened, leaving a
    spurious rejected-transition event permanently in the log (8 such events
    had to be migrated out of dstdns/topos by hand). Validating here, before
    `append_event` is ever called, means an illegal transition raises with
    zero side effects on the log.

    Mirrors apply_event's own from==to idempotency exactly (same
    `_transition_target` resolver) so a legal or no-op (from==to)
    transition is never blocked here -- only a genuinely illegal from!=to
    edge raises. Events with no transition semantics, or whose task_id
    isn't present in the caller's `states` map (mirrors apply_event's own
    tolerant skip for genuinely task-less/unknown-task events), are passed
    through unchecked -- there is nothing to validate against.
    """
    to = _transition_target(kwargs.get("type"), kwargs.get("payload") or {})
    if to is None:
        return
    task_id = kwargs.get("task_id")
    if task_id is None or task_id not in states:
        return
    tsf = states[task_id]
    if tsf.state == to:
        return
    check_task_transition(tsf.state, to)


def append_and_apply(
    project: str,
    states: dict[str, TaskStateFile],
    **kwargs: Any,
) -> Event:
    """THE canonical mutation: validate -> append event -> apply -> save
    affected.

    kwargs are `append_event`'s keyword arguments.
    """
    if _sqlite_backend_enabled():
        from . import storage_sqlite
        return storage_sqlite.append_and_apply(project, states, **kwargs)
    _validate_before_append(states, **kwargs)
    ev = append_event(project, **kwargs)
    for tid in apply_event(states, ev):
        save_state(states[tid])
    return ev


def replay(project: str) -> dict[str, TaskStateFile]:
    """Rebuild the full projection from the event log alone."""
    if _sqlite_backend_enabled():
        from . import storage_sqlite
        return storage_sqlite.replay(project)
    states: dict[str, TaskStateFile] = {}
    for ev in iter_events(project):
        apply_event(states, ev)
    return states
