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
    NEEDS_DECISION = "NEEDS_DECISION"
    READY_TO_CARVE = "READY_TO_CARVE"
    CARVED = "CARVED"
    QUEUED = "QUEUED"
    ACTIVE = "ACTIVE"
    SELF_REVIEWING = "SELF_REVIEWING"
    AWAITING_REVIEW = "AWAITING_REVIEW"
    REVIEW_REJECTED = "REVIEW_REJECTED"
    MERGE_READY = "MERGE_READY"
    MERGED = "MERGED"
    VALIDATING = "VALIDATING"
    COMPLETED = "COMPLETED"
    BLOCKED = "BLOCKED"
    SUPERSEDED = "SUPERSEDED"
    CANCELLED = "CANCELLED"

    @classmethod
    def _missing_(cls, value):
        # CR-07d read-compat: DRAFT was removed as a constructible, executable
        # state -- pinned inert since 2026-07-17 (no code ever assigned a task
        # to it; daemon.py's CreateTask hardcodes CARVED). Map the legacy value
        # so TaskStateFile.from_dict (_FIELD_TYPES {"state": TaskState} ->
        # TaskState(value)) keeps loading statefiles/events written before the
        # removal, rather than raising ValueError on a pre-CR-07d row. Routed
        # to NEEDS_DECISION -- one of DRAFT's own former edges -- rather than
        # silently reviving a removed state or a default nobody vouched for.
        # NOT a guarantee of human review: rules_lifecycle.decision_hold
        # releases NEEDS_DECISION straight back to QUEUED on the very next
        # reconcile pass unless the task's frontmatter names an open D-dep,
        # which a legacy DRAFT row -- never having existed on real data --
        # would not carry. The read-compat contract is "does not crash and
        # does not resurrect a removed state", not "forces triage".
        if value == "DRAFT":
            return cls.NEEDS_DECISION
        return None


TERMINAL_TASK_STATES = frozenset(
    {TaskState.COMPLETED, TaskState.SUPERSEDED, TaskState.CANCELLED}
)

# Normative transition graph (draft 1 SPEC §4 + draft 2 SPEC §4).
# CR-07d: DRAFT removed as a constructible state (see TaskState._missing_) --
# it is no longer a key here, and it never appears as anyone else's target.
TASK_TRANSITIONS: dict[TaskState, frozenset[TaskState]] = {
    TaskState.NEEDS_DECISION: frozenset({TaskState.READY_TO_CARVE, TaskState.QUEUED,
                                         TaskState.SUPERSEDED, TaskState.CANCELLED}),
    TaskState.READY_TO_CARVE: frozenset({TaskState.CARVED, TaskState.NEEDS_DECISION,
                                         TaskState.SUPERSEDED, TaskState.CANCELLED}),
    TaskState.CARVED: frozenset({TaskState.QUEUED, TaskState.NEEDS_DECISION,
                                 TaskState.SUPERSEDED, TaskState.CANCELLED}),
    TaskState.QUEUED: frozenset({TaskState.ACTIVE, TaskState.BLOCKED, TaskState.NEEDS_DECISION,
                                 TaskState.SUPERSEDED, TaskState.CANCELLED}),
    TaskState.ACTIVE: frozenset({TaskState.AWAITING_REVIEW, TaskState.SELF_REVIEWING,
                                 TaskState.BLOCKED, TaskState.QUEUED,
                                 TaskState.SUPERSEDED, TaskState.CANCELLED}),
    # SELF_REVIEWING (B5, 2026-07-20): the self_review stage's owned state. The
    # implementer's warm session reviews its own diff before the expensive
    # frontier reviewer sees it -- approved -> AWAITING_REVIEW; rejected -> QUEUED
    # (a fresh fix attempt, budget-bounded, mirroring the proven frontier reject
    # loop's ACTIVE-scoped stale-receipt protection -- see D-063 in
    # docs/spec-flow-stages.md); BLOCKED is the typed dead-end escape. Only
    # reachable when the `self_review` stage is composed into the pipeline, so a
    # pipeline without it is byte-identical to today (the state is unreachable).
    TaskState.SELF_REVIEWING: frozenset({TaskState.AWAITING_REVIEW, TaskState.QUEUED,
                                         TaskState.BLOCKED, TaskState.SUPERSEDED,
                                         TaskState.CANCELLED}),
    TaskState.AWAITING_REVIEW: frozenset({TaskState.REVIEW_REJECTED, TaskState.MERGE_READY,
                                          TaskState.BLOCKED, TaskState.SUPERSEDED,
                                          TaskState.CANCELLED}),
    TaskState.REVIEW_REJECTED: frozenset({TaskState.QUEUED, TaskState.READY_TO_CARVE,
                                          TaskState.NEEDS_DECISION, TaskState.SUPERSEDED,
                                          TaskState.CANCELLED}),
    TaskState.MERGE_READY: frozenset({TaskState.MERGED, TaskState.REVIEW_REJECTED,
                                      TaskState.SUPERSEDED, TaskState.CANCELLED}),
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
    REVIEW_INDEPENDENT = "review-independent"
    CARVER = "carver"

    @classmethod
    def _missing_(cls, value):
        # D-CORRECT-2 read-compat: this role was serialized as "frontier-review"
        # (a model-tier name) before the rename. Map the legacy value so
        # Attempt.from_dict (_FIELD_TYPES {"role": Role} -> Role(value)) keeps
        # loading attempts from statefiles/events written before the rename.
        if value == "frontier-review":
            return cls.REVIEW_INDEPENDENT
        return None


# Roles intentionally defined but not yet dispatched. Each member here must be
# justified by a backlog item tracking the deferred wiring decision, so a
# defined-but-dead role is TRACKED future work, not a silent stub (see
# tests/test_types.py for the guard that enforces this partition).
# Empty as of B5 (2026-07-20): every Role is now dispatched. SELF_REVIEW was the
# last reserved role -- the self_review stage wired it into daemon.py's
# LaunchSelfReview execution (a real `role=Role.SELF_REVIEW` dispatch site) plus
# reconcile's SELF_REVIEWING planning, so it is no longer a defined-but-dead stub.
# A future defined-but-unwired role goes back in here WITH a trailing
# `# nyxloom-trove/backlog.md: <B-id>` citation (the test_types.py guard enforces
# both the citation and a live backlog item), keeping the partition honest.
RESERVED_ROLES: frozenset[Role] = frozenset()


class ReceiptResult(enum.Enum):
    DONE = "done"
    BLOCKED = "blocked"
    LIMIT = "limit"
    ERROR = "error"
    # B21 2026-07-23 (D-R16 §3, scope-amendment escalation): a mid-flight
    # "I need file X outside my scope.touch allowlist" request, recognized by
    # adapters.classify_log_tail's SCOPE_AMENDMENT_REQUEST: marker and
    # translated here by wrapper.py -- a DISTINCT outcome from BLOCKED (the
    # daemon's EmitAttemptExit branches on it separately: approve-and-requeue
    # under the per-task cap, else fall through to the same hard-BLOCK).
    # Additive only -- DONE/BLOCKED/LIMIT/ERROR are unchanged.
    SCOPE_AMENDMENT = "scope_amendment"
    # B24 2026-07-23 (D-R17, transient-failure backoff-resume): a PROVIDER-
    # side throttle/outage (502/429/ResourceExhausted/idle-timeout/worker
    # request-limit), recognized by adapters.classify_log_tail's "transient"
    # classification and translated here by wrapper.py. UNLIKE every other
    # non-DONE result, this pairs with EventType.ATTEMPT_INTERRUPTED +
    # AttemptState.INTERRUPTED (NOT ATTEMPT_EXITED/EXITED, see wrapper.py) --
    # an EXITED attempt can never be revived (TERMINAL_ATTEMPT_STATES,
    # ATTEMPT_TRANSITIONS[EXITED] == frozenset()), so classifying a merely-
    # throttled leg as EXITED would silently strand it. The daemon's
    # EXISTING ResumeAttempt path (already built for INTERRUPTED attempts)
    # then retries it unmodified, gated by a fresh backoff schedule
    # (daemon.py's TRANSIENT_BACKOFF_SCHEDULE) and bounded by
    # MAX_TRANSIENT_RESUMES (daemon.py's _transient_escalate). Additive
    # only -- DONE/BLOCKED/LIMIT/ERROR/SCOPE_AMENDMENT are unchanged.
    TRANSIENT = "transient"


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


class CarverStatus(enum.Enum):
    """F018 P1 (plan-long-running-carver.md §2.4): durable session status."""
    ABSENT = "ABSENT"
    COLD = "COLD"
    STARTING = "STARTING"
    WARM = "WARM"
    COMPACTING = "COMPACTING"
    DEGRADED = "DEGRADED"
    ROTATING = "ROTATING"


class EventType(enum.Enum):
    PROJECT_REGISTERED = "PROJECT_REGISTERED"
    DOCTOR_FINDING = "DOCTOR_FINDING"
    TASK_CREATED = "TASK_CREATED"
    TASK_TRANSITIONED = "TASK_TRANSITIONED"
    TASK_BLOCKED = "TASK_BLOCKED"
    # B21 2026-07-23 (D-R16 §3, scope-amendment escalation). No TaskStateFile
    # projection (storage.apply_event does not special-case either -- same
    # shape as other pass-through event types like DECISION_OPENED): daemon.py
    # counts prior SCOPE_AMENDMENT_APPROVED events for a task by scanning
    # storage.iter_events directly (mirrors _ratchet_already_open's own
    # event-log scan), not via the projected tsf. REQUESTED is the raw ask
    # (file/reason); APPROVED is emitted only when the per-task cap
    # (daemon.MAX_SCOPE_AMENDMENTS_PER_TASK) is not yet reached, and its
    # count IS the cap's enforcement mechanism.
    SCOPE_AMENDMENT_REQUESTED = "SCOPE_AMENDMENT_REQUESTED"
    SCOPE_AMENDMENT_APPROVED = "SCOPE_AMENDMENT_APPROVED"
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
    MERGE_REVERTED = "MERGE_REVERTED"
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
    # FN-1 2026-07-24 (findings channel, option C): an advisory, one-way
    # system->user insight. No TaskStateFile projection (informational only,
    # same shape as ARTIFACT_REGISTERED) -> registered in
    # test_invariants.KNOWN_IGNORED_EVENT_TYPES. See findings.py.
    FINDING_RECORDED = "FINDING_RECORDED"
    DAEMON_STARTED = "DAEMON_STARTED"
    DAEMON_STOPPED = "DAEMON_STOPPED"
    TICK_ERROR = "TICK_ERROR"
    CONFIG_CHANGED = "CONFIG_CHANGED"
    # CR-15 (RISK-005): projection-free control-plane audit events.  A
    # refused mutation is recorded in the instance-global control ledger
    # before any request body or target identifier is inspected.  Successful
    # decision/intake/finding HTTP mutations get their own typed marker so
    # their authenticated operator identity is durable even when the
    # downstream chat turn does not resolve a domain decision.
    CONTROL_MUTATION_REFUSED = "CONTROL_MUTATION_REFUSED"
    CONTROL_CREDENTIAL_ROTATED = "CONTROL_CREDENTIAL_ROTATED"
    DECISION_REPLY_RECORDED = "DECISION_REPLY_RECORDED"
    INTAKE_REPLY_RECORDED = "INTAKE_REPLY_RECORDED"
    FINDING_PROMOTED = "FINDING_PROMOTED"
    # F018 P1 2026-07-24 (plan-long-running-carver.md §2.2): audit-only carver
    # session events — no TaskStateFile projection (see
    # KNOWN_IGNORED_EVENT_TYPES in test_invariants.py). Consumed by the pure
    # carver_session.project_session projector, never by storage.apply_event.
    CARVER_SESSION_STARTED = "CARVER_SESSION_STARTED"
    CARVER_SESSION_RESUMED = "CARVER_SESSION_RESUMED"
    CARVER_CONTEXT_CONSUMED = "CARVER_CONTEXT_CONSUMED"
    CARVER_PROPOSAL_RECORDED = "CARVER_PROPOSAL_RECORDED"
    # F018 P3b 2026-07-25 (plan-long-running-carver.md §4.2 step 2, AD1 fix):
    # durable admission marker for a validated carve proposal -- the
    # exclusion cursor daemon._validated_carve_proposals uses to stop
    # re-selecting an already-admitted proposal_id (a structural "all
    # artifact_ids already in states" check is NOT sufficient: the ordinary
    # 'new handoffs' scan discovers the same on-disk artifact independently
    # and races admission to create the task first, which would otherwise
    # make the proposal look pre-consumed and skip step-4 re-scope
    # supersession entirely). Audit-only, same shape as the other CARVER_*
    # members above.
    CARVER_PROPOSAL_ADMITTED = "CARVER_PROPOSAL_ADMITTED"
    CARVER_COMPACTION_REQUESTED = "CARVER_COMPACTION_REQUESTED"
    CARVER_COMPACTION_FINISHED = "CARVER_COMPACTION_FINISHED"
    CARVER_SESSION_ROTATED = "CARVER_SESSION_ROTATED"
    CARVER_SESSION_DEGRADED = "CARVER_SESSION_DEGRADED"
    # GA4 2026-07-25 (docs plan-gate-adoption.md, module contract item 16):
    # audit-only completion marker for a periodic `nyxloom gate verify`
    # re-run -- no TaskStateFile projection (same no-op shape as the CARVER_*
    # members above; see KNOWN_IGNORED_EVENT_TYPES in test_invariants.py).
    # Its timestamp is the durable cadence anchor daemon._days_since_gate_
    # verify scans the log for; its payload also carries the rendered
    # verdict (TRUSTWORTHY/LAUNDERS/BROKEN/INCONCLUSIVE/NO_GATE) and which
    # gate_id was probed.
    GATE_VERIFY_RECORDED = "GATE_VERIFY_RECORDED"
    # CR-02a 2026-08-03 (authoritative snapshot fail-closed audit; DR-03):
    # the two durable records of the snapshot fan-in's verdict. Audit-only,
    # no TaskStateFile projection (same no-op shape as the CARVER_* members
    # above; registered in test_invariants.KNOWN_IGNORED_EVENT_TYPES). They
    # are the ONLY thing a fail-closed pass emits, so they must be durable
    # rather than a log line: an operator has to be able to see, after the
    # fact and after replay, exactly which authoritative source was missing
    # when the factory stopped making progress.
    #
    # SNAPSHOT_UNAVAILABLE: at least one AUTHORITATIVE input was
    # unavailable/malformed/stale, so the pass performed zero launch, merge,
    # gate-authorizing or other irreversible effect. Emitted exactly ONCE per
    # affected reconcile pass, regardless of how many sources failed -- the
    # payload lists them all, deterministically ordered (snapshot.py's
    # SnapshotAudit.event_payload).
    SNAPSHOT_UNAVAILABLE = "SNAPSHOT_UNAVAILABLE"
    # SNAPSHOT_DEGRADED: the ADVISORY degradation set CHANGED. Allowed
    # progress continues, but the degradation is visible and replayable.
    # De-duplicated on the audit digest so a persistent degradation records
    # once (onset), once on any change, and once on recovery (an empty
    # `degraded` list) -- never one per pass, which is the notification-storm
    # shape reference/DOCTRINE.md and watchdog.py both exist to prevent.
    SNAPSHOT_DEGRADED = "SNAPSHOT_DEGRADED"
    # CR-16 2026-08-03 (liveness, channel health, silent-failure detection;
    # RISK-007) deliberately adds NO event type. Its durable per-project
    # deadman heartbeat fires once per reconcile pass, forever -- ~2,880 a day
    # per project at the default 30s interval, against a measured organic rate
    # of ~70-110 -- so it is a GAUGE (one overwritten row in the store's `meta`
    # table; storage.record_heartbeat / read_heartbeat), not an event. This
    # enum is the vocabulary of things that HAPPENED and is read back in full
    # by run_pass, render, rebuild, and export; a per-tick liveness stamp
    # belongs in none of them.


class ActorKind(enum.Enum):
    TICK = "tick"          # reconcile pass (in-daemon or --once)
    WRAPPER = "wrapper"
    OPERATOR = "operator"
    DOCTOR = "doctor"
    GATE = "gate"
    NOTIFIER = "notifier"
    FRONTIER_SESSION = "frontier-session"
    RESYNC = "resync"      # PACKAGE RP02 (docs/plan-state-integrity.md B.4):
                           # `nyxloom resync --apply`'s audited ground-truth
                           # re-baseline transitions (resync.resync_apply).


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
    component: str | None = None
    # D1 factory-hardening (2026-07-25, plan-factory-hardening.md §D part 1):
    # optional carve-authored "adversarially check these" hints for the
    # independent reviewer. Plain list[str] (like escalate_if/advances) --
    # no _FIELD_TYPES converter needed. Defaults to [] so every pre-D1
    # handoff (which never sets this key) parses identically.
    review_focus: list[str] = field(default_factory=list)

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
    # B21 2026-07-23 (D-R16 §3): populated by wrapper.py only when
    # result is SCOPE_AMENDMENT -- {"file": <path>, "reason": <str>} parsed
    # from the agent's SCOPE_AMENDMENT_REQUEST: marker line. None for every
    # other result (additive field; a plain dict passes through _Serde's
    # generic to_dict/from_dict with no _FIELD_TYPES converter needed, same
    # as payload dicts elsewhere in this module).
    amendment_request: dict | None = None

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
    phase: str                     # implementation|review|pre-merge|post-merge|mutation
    commit: str
    exit_code: int
    started: datetime
    ended: datetime
    environment: str | None = None
    artifacts: list[str] = field(default_factory=list)
    # F019 P1a: a bounded tail of the gate's stdout+stderr, so a gate FAILURE
    # is diagnosable (the reviewer-diagnosis routing reads it) and a re-queue can
    # embed the real failure instead of retrying context-free. Empty by default:
    # back-compat for events serialized before this field existed, and the tail
    # is only worth persisting on a non-zero exit (populated by the daemon).
    output_tail: str = ""

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
