"""Domain types, state machines, and JSON serde. FROZEN CORE (SPEC §4).

Every persisted object round-trips through `to_dict()` / `from_dict()` with
plain-JSON types only (str/int/float/bool/None/list/dict). Enums serialize as
their `.value`; datetimes as UTC ISO-8601 with explicit offset. Unknown keys
in `from_dict` input are REJECTED (ValueError) — schema drift must be loud.
"""

from __future__ import annotations

import enum
import uuid
from dataclasses import dataclass, field, fields as dc_fields
from datetime import datetime, timezone
from typing import Any


# ---------------------------------------------------------------------------
# time helpers

def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        raise ValueError("naive datetime rejected; use UTC-aware")
    return dt.astimezone(timezone.utc).isoformat()


def parse_iso(s: str) -> datetime:
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        raise ValueError(f"naive datetime rejected: {s!r}")
    return dt.astimezone(timezone.utc)


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


# ---------------------------------------------------------------------------
# enums

class TaskState(enum.Enum):
    DRAFT = "DRAFT"
    NEEDS_DECISION = "NEEDS_DECISION"
    READY_TO_CARVE = "READY_TO_CARVE"
    CARVED = "CARVED"
    QUEUED = "QUEUED"
    ACTIVE = "ACTIVE"
    AWAITING_REVIEW = "AWAITING_REVIEW"
    REVIEW_REJECTED = "REVIEW_REJECTED"
    MERGE_READY = "MERGE_READY"
    MERGED = "MERGED"
    VALIDATING = "VALIDATING"
    COMPLETED = "COMPLETED"
    BLOCKED = "BLOCKED"
    SUPERSEDED = "SUPERSEDED"
    CANCELLED = "CANCELLED"


TERMINAL_TASK_STATES = frozenset(
    {TaskState.COMPLETED, TaskState.SUPERSEDED, TaskState.CANCELLED}
)

# Normative transition graph (draft 1 SPEC §4 + draft 2 SPEC §4).
TASK_TRANSITIONS: dict[TaskState, frozenset[TaskState]] = {
    TaskState.DRAFT: frozenset({TaskState.NEEDS_DECISION, TaskState.READY_TO_CARVE,
                                TaskState.SUPERSEDED, TaskState.CANCELLED}),
    TaskState.NEEDS_DECISION: frozenset({TaskState.READY_TO_CARVE, TaskState.QUEUED,
                                         TaskState.SUPERSEDED, TaskState.CANCELLED}),
    TaskState.READY_TO_CARVE: frozenset({TaskState.CARVED, TaskState.NEEDS_DECISION,
                                         TaskState.SUPERSEDED, TaskState.CANCELLED}),
    TaskState.CARVED: frozenset({TaskState.QUEUED, TaskState.NEEDS_DECISION,
                                 TaskState.SUPERSEDED, TaskState.CANCELLED}),
    TaskState.QUEUED: frozenset({TaskState.ACTIVE, TaskState.BLOCKED, TaskState.NEEDS_DECISION,
                                 TaskState.SUPERSEDED, TaskState.CANCELLED}),
    TaskState.ACTIVE: frozenset({TaskState.AWAITING_REVIEW, TaskState.BLOCKED, TaskState.QUEUED,
                                 TaskState.SUPERSEDED, TaskState.CANCELLED}),
    TaskState.AWAITING_REVIEW: frozenset({TaskState.REVIEW_REJECTED, TaskState.MERGE_READY,
                                          TaskState.BLOCKED, TaskState.SUPERSEDED,
                                          TaskState.CANCELLED}),
    TaskState.REVIEW_REJECTED: frozenset({TaskState.QUEUED, TaskState.READY_TO_CARVE,
                                          TaskState.NEEDS_DECISION, TaskState.SUPERSEDED,
                                          TaskState.CANCELLED}),
    TaskState.MERGE_READY: frozenset({TaskState.MERGED, TaskState.SUPERSEDED,
                                      TaskState.CANCELLED}),
    TaskState.MERGED: frozenset({TaskState.VALIDATING}),
    TaskState.VALIDATING: frozenset({TaskState.COMPLETED, TaskState.BLOCKED}),
    TaskState.BLOCKED: frozenset({TaskState.QUEUED, TaskState.NEEDS_DECISION,
                                  TaskState.READY_TO_CARVE, TaskState.VALIDATING,
                                  TaskState.SUPERSEDED, TaskState.CANCELLED}),
    TaskState.COMPLETED: frozenset(),
    TaskState.SUPERSEDED: frozenset(),
    TaskState.CANCELLED: frozenset(),
}


class AttemptState(enum.Enum):
    CREATED = "CREATED"
    PREFLIGHTING = "PREFLIGHTING"
    RUNNING = "RUNNING"
    STALLED = "STALLED"
    INTERRUPTED = "INTERRUPTED"
    EXITED = "EXITED"
    FAILED = "FAILED"
    ABANDONED = "ABANDONED"


TERMINAL_ATTEMPT_STATES = frozenset(
    {AttemptState.EXITED, AttemptState.FAILED, AttemptState.ABANDONED}
)

ATTEMPT_TRANSITIONS: dict[AttemptState, frozenset[AttemptState]] = {
    AttemptState.CREATED: frozenset({AttemptState.PREFLIGHTING, AttemptState.FAILED,
                                     AttemptState.ABANDONED}),
    AttemptState.PREFLIGHTING: frozenset({AttemptState.RUNNING, AttemptState.FAILED,
                                          AttemptState.ABANDONED}),
    AttemptState.RUNNING: frozenset({AttemptState.STALLED, AttemptState.INTERRUPTED,
                                     AttemptState.EXITED, AttemptState.FAILED}),
    AttemptState.STALLED: frozenset({AttemptState.RUNNING, AttemptState.INTERRUPTED,
                                     AttemptState.EXITED, AttemptState.FAILED,
                                     AttemptState.ABANDONED}),
    AttemptState.INTERRUPTED: frozenset({AttemptState.RUNNING, AttemptState.ABANDONED}),
    AttemptState.EXITED: frozenset(),
    AttemptState.FAILED: frozenset(),
    AttemptState.ABANDONED: frozenset(),
}


class Role(enum.Enum):
    IMPLEMENTER = "implementer"
    SELF_REVIEW = "self-review"
    FRONTIER_REVIEW = "frontier-review"
    CARVER = "carver"


class ReceiptResult(enum.Enum):
    DONE = "done"
    BLOCKED = "blocked"
    LIMIT = "limit"
    ERROR = "error"


class Basis(enum.Enum):
    ACTUAL = "actual"
    ESTIMATED = "estimated"
    UNKNOWN = "unknown"


class BlockerType(enum.Enum):
    CONTRACT = "contract"
    ENVIRONMENT = "environment"
    PROVIDER = "provider"
    DECISION = "decision"
    EXTERNAL = "external"
    BUDGET = "budget"


class EventType(enum.Enum):
    PROJECT_REGISTERED = "PROJECT_REGISTERED"
    DOCTOR_FINDING = "DOCTOR_FINDING"
    TASK_CREATED = "TASK_CREATED"
    TASK_TRANSITIONED = "TASK_TRANSITIONED"
    TASK_BLOCKED = "TASK_BLOCKED"
    TASK_SUPERSEDED = "TASK_SUPERSEDED"
    TASK_CANCELLED = "TASK_CANCELLED"
    CARVE_OUTCOME = "CARVE_OUTCOME"
    DECISION_OPENED = "DECISION_OPENED"
    DECISION_RESOLVED = "DECISION_RESOLVED"
    ATTEMPT_CREATED = "ATTEMPT_CREATED"
    ATTEMPT_PREFLIGHTED = "ATTEMPT_PREFLIGHTED"
    ATTEMPT_STARTED = "ATTEMPT_STARTED"
    ATTEMPT_STALLED = "ATTEMPT_STALLED"
    ATTEMPT_INTERRUPTED = "ATTEMPT_INTERRUPTED"
    ATTEMPT_RESUMED = "ATTEMPT_RESUMED"
    ATTEMPT_EXITED = "ATTEMPT_EXITED"
    ATTEMPT_FAILED = "ATTEMPT_FAILED"
    PROVIDER_STATE_CHANGED = "PROVIDER_STATE_CHANGED"
    LEASE_ACQUIRED = "LEASE_ACQUIRED"
    LEASE_RELEASED = "LEASE_RELEASED"
    GATE_STARTED = "GATE_STARTED"
    GATE_FINISHED = "GATE_FINISHED"
    EVIDENCE_RECORDED = "EVIDENCE_RECORDED"
    REVIEW_RECORDED = "REVIEW_RECORDED"
    MERGE_RECORDED = "MERGE_RECORDED"
    PROGRESS_RECORDED = "PROGRESS_RECORDED"
    WAVE_OPENED = "WAVE_OPENED"
    WAVE_CLOSED = "WAVE_CLOSED"
    SPEC_ATTENTION = "SPEC_ATTENTION"
    PAUSE_SET = "PAUSE_SET"
    PAUSE_CLEARED = "PAUSE_CLEARED"
    NEEDS_OPERATOR = "NEEDS_OPERATOR"
    NOTIFICATION_REQUESTED = "NOTIFICATION_REQUESTED"
    NOTIFICATION_DELIVERED = "NOTIFICATION_DELIVERED"
    NOTIFICATION_FAILED = "NOTIFICATION_FAILED"
    BUDGET_WARNING = "BUDGET_WARNING"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
    ARTIFACT_REGISTERED = "ARTIFACT_REGISTERED"
    DAEMON_STARTED = "DAEMON_STARTED"
    DAEMON_STOPPED = "DAEMON_STOPPED"
    TICK_ERROR = "TICK_ERROR"
    CONFIG_CHANGED = "CONFIG_CHANGED"


class ActorKind(enum.Enum):
    TICK = "tick"          # reconcile pass (in-daemon or --once)
    WRAPPER = "wrapper"
    OPERATOR = "operator"
    DOCTOR = "doctor"
    GATE = "gate"
    NOTIFIER = "notifier"
    FRONTIER_SESSION = "frontier-session"


# ---------------------------------------------------------------------------
# generic serde machinery

def _enc(v: Any) -> Any:
    if isinstance(v, enum.Enum):
        return v.value
    if isinstance(v, datetime):
        return iso(v)
    if isinstance(v, list):
        return [_enc(x) for x in v]
    if isinstance(v, dict):
        return {k: _enc(x) for k, x in v.items()}
    if hasattr(v, "to_dict"):
        return v.to_dict()
    return v


class _Serde:
    """Mixin: to_dict via dataclass fields; from_dict via _FIELD_TYPES hints.

    Subclasses declare `_FIELD_TYPES: dict[str, callable]` ONLY for fields
    that need construction (enums, datetimes, nested dataclasses, lists of
    those); plain-JSON fields pass through. `from_dict` rejects unknown keys.
    """

    _FIELD_TYPES: dict[str, Any] = {}

    def to_dict(self) -> dict[str, Any]:
        return {f.name: _enc(getattr(self, f.name)) for f in dc_fields(self)}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Any:
        names = {f.name for f in dc_fields(cls)}
        unknown = set(d) - names
        if unknown:
            raise ValueError(f"{cls.__name__}: unknown keys {sorted(unknown)}")
        kw: dict[str, Any] = {}
        for k, v in d.items():
            conv = cls._FIELD_TYPES.get(k)
            kw[k] = conv(v) if (conv is not None and v is not None) else v
        return cls(**kw)


def _list_of(conv: Any):
    return lambda v: [conv(x) for x in v]


def _opt(conv: Any):
    return lambda v: None if v is None else conv(v)


# ---------------------------------------------------------------------------
# frontmatter (the handoff contract's machine half — schema mirror)

@dataclass
class Oracle(_Serde):
    id: str
    observable: str
    negative: str
    gate: str


@dataclass
class Scope(_Serde):
    touch: list[str]
    forbid: list[str] = field(default_factory=list)


@dataclass
class Source(_Serde):
    kind: str                      # review|backlog|roadmap|product-goal|user|spec-gap
    ref: str | None = None


@dataclass
class Base(_Serde):
    branch: str
    after: str | None = None


@dataclass
class Budget(_Serde):
    max_attempts: int | None = None
    max_wall_seconds: int | None = None
    max_cost: float | None = None
    currency: str | None = None


@dataclass
class Frontmatter(_Serde):
    schema_version: int
    id: str
    project: str
    title: str
    tier: str
    input_revision: str
    source: Source
    scope: Scope
    oracles: list[Oracle]
    gates: list[str]
    escalate_if: list[str]
    stack: str = "none"
    mutexes: list[str] = field(default_factory=list)
    depends_on: list[str] = field(default_factory=list)
    base: Base | None = None
    session: str = "fresh"
    advances: list[str] = field(default_factory=list)
    budget: Budget | None = None
    carve_affinity: str | None = None

    _FIELD_TYPES = {
        "source": Source.from_dict,
        "scope": Scope.from_dict,
        "oracles": _list_of(Oracle.from_dict),
        "base": _opt(Base.from_dict),
        "budget": _opt(Budget.from_dict),
    }

    def decision_deps(self) -> list[str]:
        return [d for d in self.depends_on if d.startswith("D-")]

    def task_deps(self) -> list[str]:
        return [d for d in self.depends_on if not d.startswith("D-")]

    def effective_mutexes(self) -> list[str]:
        m = list(self.mutexes)
        if self.stack == "exclusive" and "stack" not in m:
            m.append("stack")
        return m


# ---------------------------------------------------------------------------
# runtime records

@dataclass
class Usage(_Serde):
    basis: Basis
    tokens_in: int | None = None
    tokens_out: int | None = None
    cached_in: int | None = None
    cost: float | None = None
    currency: str | None = None
    price_rev: str | None = None

    _FIELD_TYPES = {"basis": Basis}


@dataclass
class OracleResult(_Serde):
    id: str
    result: str                    # pass|fail|not-run


@dataclass
class Receipt(_Serde):
    result: ReceiptResult
    exit_code: int
    oracles: list[OracleResult] = field(default_factory=list)
    blocked_reason: str | None = None
    files_touched: list[str] = field(default_factory=list)
    head_commit: str | None = None

    _FIELD_TYPES = {"result": ReceiptResult, "oracles": _list_of(OracleResult.from_dict)}


@dataclass
class Route(_Serde):
    route_id: str
    cli: str
    model: str
    variant: str | None = None
    effort: str | None = None
    routes_rev: str | None = None


@dataclass
class Attempt(_Serde):
    attempt_id: str
    role: Role
    state: AttemptState
    route: Route
    started: datetime
    ended: datetime | None = None
    worktree: str | None = None
    branch: str | None = None
    base_commit: str | None = None
    pid: int | None = None
    pgid: int | None = None
    log_path: str | None = None
    session_handle: str | None = None
    receipt: Receipt | None = None
    usage: Usage | None = None
    wave_id: str | None = None

    _FIELD_TYPES = {
        "role": Role,
        "state": AttemptState,
        "route": Route.from_dict,
        "started": parse_iso,
        "ended": _opt(parse_iso),
        "receipt": _opt(Receipt.from_dict),
        "usage": _opt(Usage.from_dict),
    }


@dataclass
class GateResult(_Serde):
    gate_id: str
    phase: str                     # implementation|review|pre-merge|post-merge
    commit: str
    exit_code: int
    started: datetime
    ended: datetime
    environment: str | None = None
    artifacts: list[str] = field(default_factory=list)

    _FIELD_TYPES = {"started": parse_iso, "ended": parse_iso}


@dataclass
class Blocker(_Serde):
    type: BlockerType
    unblock_condition: str
    detail: str | None = None

    _FIELD_TYPES = {"type": BlockerType}


@dataclass
class TaskStateFile(_Serde):
    schema_version: int
    task_id: str
    project: str
    state: TaskState
    since: datetime
    handoff_path: str | None = None
    wave_id: str | None = None
    paused: bool = False
    blocker: Blocker | None = None
    attempts: list[Attempt] = field(default_factory=list)
    gate_results: list[GateResult] = field(default_factory=list)
    leases_held: list[str] = field(default_factory=list)
    progress_units: list[str] = field(default_factory=list)
    merge_commit: str | None = None
    notes: str | None = None

    _FIELD_TYPES = {
        "state": TaskState,
        "since": parse_iso,
        "blocker": _opt(Blocker.from_dict),
        "attempts": _list_of(Attempt.from_dict),
        "gate_results": _list_of(GateResult.from_dict),
    }

    def current_attempt(self) -> Attempt | None:
        """Most recent non-terminal attempt, else None."""
        for a in reversed(self.attempts):
            if a.state not in TERMINAL_ATTEMPT_STATES:
                return a
        return None

    def attempt_by_id(self, attempt_id: str) -> Attempt | None:
        for a in self.attempts:
            if a.attempt_id == attempt_id:
                return a
        return None


@dataclass
class Actor(_Serde):
    kind: ActorKind
    id: str

    _FIELD_TYPES = {"kind": ActorKind}


@dataclass
class Event(_Serde):
    schema_version: int
    sequence: int                  # per-project monotonic; storage assigns
    timestamp: datetime
    project: str
    actor: Actor
    type: EventType
    payload: dict[str, Any]
    task_id: str | None = None
    attempt_id: str | None = None
    wave_id: str | None = None
    decision_id: str | None = None

    _FIELD_TYPES = {
        "timestamp": parse_iso,
        "actor": Actor.from_dict,
        "type": EventType,
    }


# ---------------------------------------------------------------------------
# findings

@dataclass
class LintFinding(_Serde):
    rule: str                      # "L1".."L12"
    severity: str                  # "error" | "warning"
    message: str
    path: str
    line: int | None = None


@dataclass
class DoctorFinding(_Serde):
    kind: str                      # short kebab-case class
    severity: str                  # "critical" | "error" | "warning" | "info"
    message: str
    project: str | None = None
    refs: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# transition validation

class TransitionError(Exception):
    pass


def check_task_transition(cur: TaskState, new: TaskState) -> None:
    if new not in TASK_TRANSITIONS[cur]:
        raise TransitionError(f"task transition {cur.value} -> {new.value} not allowed")


def check_attempt_transition(cur: AttemptState, new: AttemptState) -> None:
    if new not in ATTEMPT_TRANSITIONS[cur]:
        raise TransitionError(f"attempt transition {cur.value} -> {new.value} not allowed")
