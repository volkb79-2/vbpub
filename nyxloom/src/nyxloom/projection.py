"""Pure event validation and projection (CR-04b, DR-09).

Extracted from `storage.py` so the store implementation and the public store
API can both depend on it without depending on each other. Before this,
`storage_sqlite` imported the projection functions from `storage`, and
`storage` dispatched into `storage_sqlite` -- an import cycle that only held
together because the dispatch was a function-local import inside a selector
branch. Deleting the selector (this package) made the cycle real, which is
why the contract names "remove the environment selector, file writes, AND
import cycle" as one item: they were one structure.

Nothing here does I/O. `apply_event` takes a projection map and an event and
returns the task ids it changed; `validate_before_append` refuses an illegal
transition before anything is written. Both are pure functions over data the
caller supplies, which is what lets the store call them INSIDE a transaction
against committed state (CR-04a) rather than against a caller's snapshot.
"""

from __future__ import annotations

from typing import Any

from .log import get_logger
from .types import (
    Attempt, AttemptState, Blocker, Event, EventType, GateResult,
    TERMINAL_ATTEMPT_STATES, TaskState, TaskStateFile, check_task_transition,
)

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


# ---------------------------------------------------------------------------
# CR-07e: the versioned-event upcaster skeleton
#
# The plan amendment assigned "schema and projection version tables plus
# upcasters" to CR-04; CR-04a/b/c never built it (SCHEMA_VERSION has never
# incremented, so nothing forced the question). This is that mechanism,
# proved with zero real upcasters -- every persisted row today is already at
# SCHEMA_VERSION, so every real read is the empty-loop identity path. It is a
# prerequisite for landing any FUTURE schema change (including CR-07's own
# lifecycle/node schema) safely; it does not itself decide what that schema
# looks like.
#
# Two registries because events and statefiles disagree on axes: an event's
# shape depends on its EventType (a TASK_TRANSITIONED payload and a
# GATE_RESULT payload are unrelated shapes sharing one schema_version), so
# EVENT_UPCASTERS is keyed on (from_version, EventType). A TaskStateFile has
# exactly one shape, so STATE_UPCASTERS is keyed on from_version alone.
#
# Residual, reported rather than solved: TASK_CREATED's payload carries a
# NESTED TaskStateFile dict (apply_event's `TaskStateFile.from_dict(
# ev.payload["statefile"])`) that does NOT separately route through
# STATE_UPCASTERS -- upcast_event_payload hands a registered upcaster the
# WHOLE payload dict, nested "statefile" key included, so a future
# EVENT_UPCASTERS[(v, EventType.TASK_CREATED)] upcaster is responsible for
# transforming that nested shape itself if it changed. Not a bug (no real
# upcaster exists to get this wrong yet) and not solved here: building
# generic recursive-upcast dispatch now, before any real schema change
# names what "nested" even means for it, would be exactly the kind of
# speculative machinery this package's zero-real-upcasters scope avoids.

class UpcastError(Exception):
    """A persisted row's schema_version has no registered path forward to
    SCHEMA_VERSION.

    Raised rather than silently handing back a shape nothing validated --
    the fault-matrix contract `tests/test_storage_sqlite.py` already names:
    "failed upcast produce no authorizing effect". A row this cannot upcast
    must refuse to become a live object, not decay into a partially
    understood one.
    """


#: One upcaster per (from_version, EventType): the payload dict AT
#: from_version -> the payload dict AT from_version + 1. Empty today --
#: nothing to prove except that the DISPATCH mechanism itself works.
EVENT_UPCASTERS: dict[tuple[int, EventType], Any] = {}

#: One upcaster per from_version: a TaskStateFile.to_dict() shape AT
#: from_version -> the shape AT from_version + 1. Keyed by version alone --
#: unlike events, a statefile has no per-type axis to key on.
STATE_UPCASTERS: dict[int, Any] = {}


def upcast_event_payload(schema_version: int, event_type: EventType,
                          payload: dict) -> dict:
    """Walk `payload` forward from `schema_version` to `SCHEMA_VERSION`, one
    registered upcaster per hop.

    A row already at `SCHEMA_VERSION` takes zero hops and returns `payload`
    unchanged -- the path every real row takes today. `Event.schema_version`
    itself is left as the row's ORIGINALLY RECORDED version (a historical
    fact about when the event was written); only the payload SHAPE is
    upcast, to what current code expects.
    """
    version = schema_version
    while version < SCHEMA_VERSION:
        upcaster = EVENT_UPCASTERS.get((version, event_type))
        if upcaster is None:
            raise UpcastError(
                f"no upcaster registered for {event_type.value!r} at schema "
                f"version {version} (need a path to {SCHEMA_VERSION})")
        payload = upcaster(payload)
        version += 1
    return payload


def upcast_state_dict(data: dict) -> dict:
    """Walk `data` (a `TaskStateFile.to_dict()` shape) forward from its own
    recorded `schema_version` to `SCHEMA_VERSION`. Same contract as
    :func:`upcast_event_payload`, mirrored for the one-axis case."""
    version = data.get("schema_version", SCHEMA_VERSION)
    while version < SCHEMA_VERSION:
        upcaster = STATE_UPCASTERS.get(version)
        if upcaster is None:
            raise UpcastError(
                f"no upcaster registered for TaskStateFile at schema "
                f"version {version} (need a path to {SCHEMA_VERSION})")
        data = upcaster(data)
        version += 1
    return data


_EARLY_ATTEMPT_STATES = frozenset({AttemptState.CREATED, AttemptState.PREFLIGHTING})


def _attempt_regression(current: AttemptState, incoming: AttemptState) -> bool:
    """True when applying `incoming` over `current` would move an attempt
    backwards in its lifecycle: out of a terminal state, or from a
    post-launch state (RUNNING/STALLED/INTERRUPTED) back to CREATED/
    PREFLIGHTING. Legitimate backward edges (STALLED->RUNNING,
    INTERRUPTED->RUNNING) are NOT regressions."""
    if current in TERMINAL_ATTEMPT_STATES:
        return incoming is not current
    return current not in _EARLY_ATTEMPT_STATES and incoming in _EARLY_ATTEMPT_STATES




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
            # A re-asserted TASK_BLOCKED refreshes the blocker/notes payload
            # so a newer reason wins -- and REPORTS THE TASK AS AFFECTED so
            # the refreshed row is actually written.
            #
            # CR-04b: it used to refresh the object and return nothing, which
            # was already a divergence and only became visible once the store
            # stopped writing back the caller's map (CR-04a). `replay` applies
            # every event and ends with the newer reason; the served
            # projection kept the older one. Two answers to "why did this task
            # stop", disagreeing, with the operator-facing one stale -- which
            # is the only thing that record exists for.
            if t is EventType.TASK_BLOCKED:
                tsf.blocker = Blocker.from_dict(ev.payload["blocker"])
                if ev.payload.get("notes"):
                    tsf.notes = ev.payload["notes"]
                affected.append(tsf.task_id)
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


