"""Invariant / property tests over the TaskState machine and the dispatch
enums (F0 package, 2026-07-17).

WHY THIS FILE EXISTS: two shipped bugs were ABSENCES, not wrong code --
(a) TaskState.REVIEW_REJECTED had NO reconcile handler, so rejected tasks
stranded forever (fixed by the 2026-07-16 self-correct package; see
reconcile.py module contract item 10 and test_reconcile.py's
test_review_rejected_with_budget_remaining_requeues); (b) Role.SELF_REVIEW
was defined in the enum + schema but never dispatched (fixed by P43's guard
in test_types.py, generalized here). You cannot test code you forgot to
write, so this file enumerates the FULL state/enum space and asserts
coverage invariants -- absence of handling is the failure mode, not a
specific wrong value.

NEW FINDINGS (discovered while writing this suite, not previously tracked
in nyxloom-trove/backlog.md): the SAME "graph edge with no planner branch"
bug class exists for FOUR MORE TaskStates that reconcile.py never
references at all:

  - TaskState.DRAFT: has outgoing TASK_TRANSITIONS edges and a STATE_LEGEND
    UI entry ("Freshly authored idea; not yet triaged into a task") in
    render.py implying it's a real, reachable, intended state -- but
    reconcile.py has zero branch for it, and no code anywhere ever assigns
    it as a task's live state either (daemon.py's CreateTask hardcodes
    CARVED). A manually-placed DRAFT task would strand forever.
  - TaskState.READY_TO_CARVE: has outgoing edges and a STATE_LEGEND entry
    ("waiting to become a real task (CARVED)") -- but across the entire
    src/ tree it is referenced ONLY in types.py (the graph itself) and
    render.py (the legend); nothing ever transitions a task INTO or OUT OF
    it (not even reconcile.py's own NEEDS_DECISION handler, which
    unconditionally targets QUEUED, never READY_TO_CARVE, despite the
    graph technically allowing it).
  - TaskState.MERGED / TaskState.VALIDATING: render.py documents a
    "post-merge validation" pipeline ("Merged; awaiting post-merge
    validation before COMPLETED") and daemon.py's own carve-retirement
    comment references it as a real mechanism ("COMPLETED requires the
    full MERGED->VALIDATING pipeline") -- but it is entirely unimplemented.
    cli.py's cmd_merge transitions a task to MERGED and stops; nothing in
    reconcile.py, daemon.py, or cli.py ever transitions MERGED->VALIDATING
    or VALIDATING->COMPLETED; GateResult.phase's "post-merge" value is
    declared (config.py/types.py) but never checked anywhere in daemon.py
    (`grep "phase ==" src/nyxloom/daemon.py` is empty). Practical
    consequence: TaskState.COMPLETED -- the terminal SUCCESS state -- is
    UNREACHABLE in the shipped codebase today.
    FIXED 2026-07-17 (nyxloom-post-merge-validation package): reconcile.py's
    module contract item 11 now plans MERGED->VALIDATING and a
    RunPostMergeGate trigger for VALIDATING; daemon.py's
    _run_post_merge_gate runs the declared gate (or the implementation gate
    as the documented default, or a no-op pass if none is declared) against
    the merged default branch and transitions onward to COMPLETED or
    BLOCKED. See tests/test_post_merge.py for the end-to-end proof and this
    file's test_no_dead_end_merged/test_no_dead_end_validating (now plain,
    non-xfail tests -- the pinned xfail(strict) markers were REMOVED, since
    a passing strict-xfail test is itself a failure (XPASS)).

The remaining two (DRAFT, READY_TO_CARVE) are still pinned below as
KNOWN_STATE_GAPS with dedicated xfail(strict) tests (Oracle O1: each
demonstrably fails TODAY, on real code, proving the guard is not a
tautology) rather than silently fixed -- production changes for those two
are out of scope for THIS package (see CLAUDE.md handoff scope). Report to
the project owner: these still need a backlog item and a real fix.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

import pytest

from nyxloom import storage
from nyxloom.config import MutexDef, Policy, ProjectConfig, RouteDef, Routes
from nyxloom.reconcile import (
    Action, DispatchImplementer, EmitAttemptExit, OpenWave, ReconcileInput,
    RunPostMergeGate, Transition, plan_project,
)
from nyxloom.types import (
    TASK_TRANSITIONS, TERMINAL_TASK_STATES, RESERVED_ROLES,
    Actor, ActorKind, Attempt, AttemptState, Event, EventType, Frontmatter,
    Receipt, ReceiptResult, Role, Route, Scope, Source, TaskState,
    TaskStateFile,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
RECONCILE_SRC = (REPO_ROOT / "src" / "nyxloom" / "reconcile.py").read_text()
STORAGE_SRC = (REPO_ROOT / "src" / "nyxloom" / "storage.py").read_text()
RENDER_SRC = (REPO_ROOT / "src" / "nyxloom" / "render.py").read_text()
DAEMON_SRC = (REPO_ROOT / "src" / "nyxloom" / "daemon.py").read_text()


# ---------------------------------------------------------------------------
# minimal, self-contained ReconcileInput factories (read-only test helpers;
# deliberately NOT importing tests/test_reconcile.py's helpers so this file
# has zero cross-file collection-order coupling)

def _utc(y: int, m: int, d: int, hh: int = 0, mm: int = 0) -> datetime:
    return datetime(y, m, d, hh, mm, tzinfo=timezone.utc)


def _config(max_active_tasks: int = 2, max_attempts_per_task: int = 3) -> ProjectConfig:
    return ProjectConfig(
        project_id="inv",
        root=Path("/inv"),
        default_branch="main",
        worktree_root=".worktrees",
        handoff_globs=["handoff/*.md"],
        gates={},
        mutexes={"stack": MutexDef(name="stack", scope="project", capacity=1)},
        policy=Policy(
            max_active_tasks=max_active_tasks,
            max_attempts_per_task=max_attempts_per_task,
            max_consecutive_zero_progress_merges=3,
            wave_max_diffs=3,
            carve_ahead_target=5,
            carve_authority="branch",
            headroom_warn=5,
            max_resume_failures=2,
            resume_progress_grace_seconds=120,
        ),
    )


def _routes(tier: str = "flash-high") -> Routes:
    return Routes(
        revision="test",
        tiers={tier: ["route-1"]},
        routes={"route-1": RouteDef(route_id="route-1", cli="fake", model="fake-model")},
    )


def _fm(task_id: str = "INV-01", tier: str = "flash-high") -> Frontmatter:
    return Frontmatter(
        schema_version=1, id=task_id, project="inv", title="t", tier=tier,
        input_revision="abc", source=Source(kind="roadmap"), scope=Scope(touch=["x"]),
        oracles=[], gates=[], escalate_if=[],
    )


def _tsf(task_id: str = "INV-01", state: TaskState = TaskState.QUEUED,
         since: datetime | None = None, attempts: list[Attempt] | None = None) -> TaskStateFile:
    return TaskStateFile(
        schema_version=1, task_id=task_id, project="inv", state=state,
        since=since or _utc(2026, 7, 15), attempts=attempts or [],
    )


def _attempt(attempt_id: str = "att-1", state: AttemptState = AttemptState.RUNNING,
             role: Role = Role.IMPLEMENTER, receipt: Receipt | None = None) -> Attempt:
    return Attempt(
        attempt_id=attempt_id, role=role, state=state,
        route=Route(route_id="route-1", cli="fake", model="fake-model"),
        started=_utc(2026, 7, 15), receipt=receipt,
    )


def _base_input(task_id: str, state: TaskState, **overrides) -> ReconcileInput:
    """A maximally favorable ReconcileInput for one task -- every transient
    guard (paused/decisions/leases/budget/routes) is wide open, so the ONLY
    thing that can suppress a planned action is plan_project genuinely
    having no branch for this state at all."""
    tsf = overrides.pop("tsf", None) or _tsf(task_id=task_id, state=state)
    fm = overrides.pop("fm", None) or _fm(task_id=task_id)
    kwargs = dict(
        now=_utc(2026, 7, 15),
        cfg=_config(),
        routes=_routes(),
        states={task_id: tsf},
        frontmatters={task_id: (fm, "h.md")},
        lint_clean={task_id: True},
        project_paused=False,
        decisions_open=set(),
        merged_branches=set(),
        leases_free={},
        provider_ok={"route-1": True},
        log_quiet_seconds={},
        pid_alive={},
        receipts={},
    )
    kwargs.update(overrides)
    return ReconcileInput(**kwargs)


def _action_touches_task(action: Action, task_id: str) -> bool:
    """True if `action` is plan_project progressing/escalating THIS task.
    Handles both Action.task_id (Transition, DispatchImplementer, ... --
    most actions) and the task_ids list actions (OpenWave/LaunchReview,
    which batch several tasks and so leave the single task_id field at its
    default None)."""
    if getattr(action, "task_id", None) == task_id:
        return True
    return task_id in (getattr(action, "task_ids", None) or [])


def _event(ev_type: EventType, task_id: str | None = None) -> Event:
    return Event(
        schema_version=1, sequence=1, timestamp=_utc(2026, 7, 15), project="inv",
        actor=Actor(kind=ActorKind.TICK, id="inv-test"), type=ev_type, payload={},
        task_id=task_id,
    )


# ===========================================================================
# INVARIANT 1: no dead-end TaskStates
#
# Every non-terminal TaskState must (a) have outgoing edges in
# TASK_TRANSITIONS (see Invariant 4 below) AND (b) either be planned by
# plan_project under a favorable input, or be explicitly documented in
# render.py's STATE_LEGEND as requiring manual/operator resolution (the
# ONLY legitimate reason a state may never be auto-progressed), or be a
# tracked KNOWN_STATE_GAPS entry (a real, currently-unfixed absence, pinned
# below rather than silently missed).
# ===========================================================================

NON_TERMINAL_STATES: frozenset[TaskState] = frozenset(TaskState) - TERMINAL_TASK_STATES

_TASKSTATE_REF_RE = re.compile(r"TaskState\.(\w+)")


def _states_referenced_in_reconcile() -> frozenset[TaskState]:
    """Grep-based coverage scan (mirrors P43's Role dispatch scan in
    test_types.py): the set of TaskStates that reconcile.py's plan_project
    has SOME branch keyed on."""
    names = set(_TASKSTATE_REF_RE.findall(RECONCILE_SRC))
    return frozenset(TaskState[n] for n in names if n in TaskState.__members__)


_MANUAL_HINT_RE = re.compile(r"manual|operator must", re.IGNORECASE)


def _manual_documented_states() -> frozenset[TaskState]:
    """States render.py's STATE_LEGEND explicitly documents as requiring a
    human/operator step (never automatic) -- e.g. MERGE_READY ("Merge is a
    MANUAL operator step, never automatic"), BLOCKED ("an operator must
    resolve by hand"). Scanned from source text, not hand-picked, so this
    tracks render.py rather than silently drifting from it."""
    block_match = re.search(r"STATE_LEGEND:\s*dict\[TaskState,\s*str\]\s*=\s*\{.*?\n\}", RENDER_SRC, re.S)
    assert block_match, "could not locate STATE_LEGEND dict literal in render.py"
    block = block_match.group(0)
    out = set()
    for name, text in re.findall(r'TaskState\.(\w+):\s*\(?\s*"([^"]*)"', block):
        if _MANUAL_HINT_RE.search(text):
            out.add(TaskState[name])
    return frozenset(out)


# See module docstring for the evidence behind each of these two. Pinned
# here (not silently missed) per the "GAP -> xfail/documented test, not a
# silent no-op" rule -- fixing reconcile.py is out of scope for this
# test-only package.
#
# TaskState.MERGED / TaskState.VALIDATING were ALSO tracked here originally
# (see module docstring below, still describing the original finding for
# the historical record) but are FIXED as of the nyxloom-post-merge-
# validation package (2026-07-17): reconcile.py's module contract item 11
# now plans MERGED->VALIDATING and RunPostMergeGate for VALIDATING (see
# daemon.py's _run_post_merge_gate for the actual gate execution). Both
# states are now genuinely `planned`, so they were REMOVED from this set --
# leaving them in would fail test_every_nonterminal_taskstate_is_planned_
# manual_or_tracked_gap's own isdisjoint(planned) assertion below.
KNOWN_STATE_GAPS: frozenset[TaskState] = frozenset({
    TaskState.DRAFT,
    TaskState.READY_TO_CARVE,
})


def test_every_nonterminal_taskstate_is_planned_manual_or_tracked_gap():
    """The umbrella coverage assertion: every non-terminal TaskState must
    land in exactly one of {planned by reconcile.py, documented manual in
    render.py, tracked KNOWN_STATE_GAPS}. A state in NONE of these is a
    brand-new, completely unnoticed dead-end risk -- this is the tripwire
    that would have caught the pre-fix REVIEW_REJECTED bug (it was in none
    of the three buckets until the 2026-07-16 self-correct package added
    its reconcile.py branch)."""
    planned = _states_referenced_in_reconcile()
    manual = _manual_documented_states()
    assert planned.isdisjoint(KNOWN_STATE_GAPS), (
        f"a KNOWN_STATE_GAPS member is ALSO referenced in reconcile.py -- "
        f"looks fixed, remove from KNOWN_STATE_GAPS: {planned & KNOWN_STATE_GAPS}")
    assert manual.isdisjoint(KNOWN_STATE_GAPS), (
        f"a KNOWN_STATE_GAPS member is ALSO documented manual in render.py's "
        f"STATE_LEGEND -- remove from KNOWN_STATE_GAPS: {manual & KNOWN_STATE_GAPS}")
    accounted = planned | manual | KNOWN_STATE_GAPS
    missing = NON_TERMINAL_STATES - accounted
    assert not missing, (
        f"non-terminal TaskState(s) with NO plan_project branch, NOT "
        f"documented manual, and NOT in KNOWN_STATE_GAPS: {missing} -- new "
        f"reconcile dead-end risk; wire it, document it manual in "
        f"render.py, or add it to KNOWN_STATE_GAPS with a citation")


def test_queued_is_planned_not_a_tracked_gap():
    """Non-hollow anchor (O2): proves the grep scan finds a REAL handled
    state, not just that KNOWN_STATE_GAPS happens to cover the rest."""
    assert TaskState.QUEUED in _states_referenced_in_reconcile()
    assert TaskState.QUEUED not in KNOWN_STATE_GAPS


def test_blocked_and_merge_ready_are_manual_not_tracked_gaps():
    """Non-hollow anchor: proves the STATE_LEGEND scan finds the two real
    manual-operator entries, not just that KNOWN_STATE_GAPS is a subset of
    something vacuous."""
    manual = _manual_documented_states()
    assert {TaskState.BLOCKED, TaskState.MERGE_READY} <= manual
    assert not ({TaskState.BLOCKED, TaskState.MERGE_READY} & KNOWN_STATE_GAPS)


def test_draft_is_a_tracked_gap_not_planned_or_manual():
    """Non-hollow anchor: proves the scan genuinely finds DRAFT absent from
    BOTH buckets, not just that KNOWN_STATE_GAPS is asserted by fiat."""
    assert TaskState.DRAFT not in _states_referenced_in_reconcile()
    assert TaskState.DRAFT not in _manual_documented_states()
    assert TaskState.DRAFT in KNOWN_STATE_GAPS


# --- behavioral proofs: construct the favorable input, call plan_project,
# assert a progressing action was actually produced (or, for the four
# tracked gaps, xfail because none is) --------------------------------------

def test_no_dead_end_needs_decision():
    inp = _base_input("INV-01", TaskState.NEEDS_DECISION)
    actions = plan_project(inp)
    assert any(isinstance(a, Transition) and a.to is TaskState.QUEUED
               and _action_touches_task(a, "INV-01") for a in actions)


def test_no_dead_end_carved():
    inp = _base_input("INV-01", TaskState.CARVED)
    actions = plan_project(inp)
    assert any(isinstance(a, Transition) and a.to is TaskState.QUEUED
               and _action_touches_task(a, "INV-01") for a in actions)


def test_no_dead_end_queued():
    inp = _base_input("INV-01", TaskState.QUEUED)
    actions = plan_project(inp)
    assert any(isinstance(a, DispatchImplementer) and _action_touches_task(a, "INV-01")
               for a in actions)


def test_no_dead_end_active_with_receipted_attempt():
    att = _attempt(state=AttemptState.RUNNING)
    tsf = _tsf(task_id="INV-01", state=TaskState.ACTIVE, attempts=[att])
    inp = _base_input("INV-01", TaskState.ACTIVE, tsf=tsf, receipts={"att-1": {"result": "done"}})
    actions = plan_project(inp)
    assert any(isinstance(a, EmitAttemptExit) and _action_touches_task(a, "INV-01")
               for a in actions)


def test_no_dead_end_awaiting_review_opens_wave():
    tsf = _tsf(task_id="INV-01", state=TaskState.AWAITING_REVIEW, since=_utc(2026, 6, 1))
    inp = _base_input("INV-01", TaskState.AWAITING_REVIEW, tsf=tsf)
    actions = plan_project(inp)
    assert any(isinstance(a, OpenWave) and _action_touches_task(a, "INV-01") for a in actions)


def test_no_dead_end_review_rejected_requeues():
    """O1: this is the EXACT bug the 2026-07-16 self-correct package fixed
    (reconcile.py module contract item 10). Proves the guard is real by
    pinning the fix behaviorally, independent of the grep scan above --
    if the reconcile.py branch were ever deleted, this test would catch it
    even if the grep-based scan somehow still found a stray textual
    reference."""
    att = _attempt(state=AttemptState.EXITED, receipt=Receipt(result=ReceiptResult.DONE, exit_code=0))
    tsf = _tsf(task_id="INV-01", state=TaskState.REVIEW_REJECTED, attempts=[att])
    inp = _base_input("INV-01", TaskState.REVIEW_REJECTED, tsf=tsf)
    actions = plan_project(inp)
    assert any(isinstance(a, Transition) and a.to is TaskState.QUEUED
               and _action_touches_task(a, "INV-01") for a in actions)


_DRAFT_GAP_REASON = (
    "ABSENCE bug found while writing this invariant suite (2026-07-17): "
    "TaskState.DRAFT has outgoing edges in TASK_TRANSITIONS and a "
    "STATE_LEGEND UI entry in render.py implying it is a real, reachable "
    "state, but reconcile.py's plan_project has ZERO branch keyed on "
    "TaskState.DRAFT, and no code anywhere ever assigns DRAFT as a task's "
    "live state either (daemon.py's CreateTask hardcodes CARVED). A task "
    "manually placed in DRAFT would strand forever -- the same dead-end "
    "class as the pre-fix REVIEW_REJECTED bug. No backlog item tracks this "
    "yet; flagged here for triage, not fixed (production out of scope)."
)


@pytest.mark.xfail(strict=True, reason=_DRAFT_GAP_REASON)
def test_no_dead_end_draft():
    inp = _base_input("INV-01", TaskState.DRAFT)
    actions = plan_project(inp)
    assert any(_action_touches_task(a, "INV-01") for a in actions), (
        "DRAFT task got zero planned actions -- reconcile.py has no handler for it"
    )


_READY_TO_CARVE_GAP_REASON = (
    "ABSENCE bug found while writing this invariant suite (2026-07-17): "
    "TaskState.READY_TO_CARVE has outgoing TASK_TRANSITIONS edges and a "
    "STATE_LEGEND entry ('waiting to become a real task (CARVED)') "
    "implying automatic progression -- but across the ENTIRE src/ tree it "
    "is referenced ONLY in types.py (the graph itself) and render.py (the "
    "legend). No code path ever assigns it as a transition target (not "
    "even reconcile.py's own NEEDS_DECISION handler, which unconditionally "
    "targets QUEUED, never READY_TO_CARVE, though the graph allows it), "
    "and reconcile.py never checks for a task already in this state "
    "either. No backlog item tracks this; flagged here for triage."
)


@pytest.mark.xfail(strict=True, reason=_READY_TO_CARVE_GAP_REASON)
def test_no_dead_end_ready_to_carve():
    inp = _base_input("INV-01", TaskState.READY_TO_CARVE)
    actions = plan_project(inp)
    assert any(_action_touches_task(a, "INV-01") for a in actions), (
        "READY_TO_CARVE task got zero planned actions -- reconcile.py has no handler for it"
    )


# FIXED 2026-07-17 (nyxloom-post-merge-validation package). These two used
# to be xfail(strict=True)-pinned: the MERGED -> VALIDATING -> COMPLETED
# "post-merge validation" pipeline that render.py documents ('Merged;
# awaiting post-merge validation before COMPLETED') and daemon.py's own
# carve-retirement comment treated as a real mechanism ('COMPLETED requires
# the full MERGED->VALIDATING pipeline') was ENTIRELY UNIMPLEMENTED: cli.py's
# cmd_merge transitioned a task to MERGED and stopped there; nothing in
# reconcile.py, daemon.py, or cli.py ever transitioned MERGED->VALIDATING or
# VALIDATING->COMPLETED; GateResult.phase's declared 'post-merge' value was
# never checked anywhere in daemon.py. Practical consequence: TaskState.
# COMPLETED -- the terminal SUCCESS state -- was UNREACHABLE in the shipped
# codebase. Now fixed: reconcile.py's module contract item 11 plans
# MERGED->Transition(VALIDATING) and VALIDATING->RunPostMergeGate; daemon.py's
# _run_post_merge_gate executes the gate and transitions onward. These are
# now plain (non-xfail) behavioral proofs, matching this file's other
# non-hollow dead-end tests -- a strict xfail on a now-passing test is an
# XPASS failure, so the markers were removed, not merely loosened.
def test_no_dead_end_merged():
    inp = _base_input("INV-01", TaskState.MERGED)
    actions = plan_project(inp)
    assert any(isinstance(a, Transition) and a.to is TaskState.VALIDATING
               and _action_touches_task(a, "INV-01") for a in actions), (
        "MERGED task got no Transition(VALIDATING) -- the post-merge pipeline regressed"
    )


def test_no_dead_end_validating():
    inp = _base_input("INV-01", TaskState.VALIDATING)
    actions = plan_project(inp)
    assert any(isinstance(a, RunPostMergeGate) and _action_touches_task(a, "INV-01")
               for a in actions), (
        "VALIDATING task got no RunPostMergeGate -- the post-merge pipeline regressed"
    )


# ===========================================================================
# INVARIANT 2: every Role wired-or-reserved (generalizes P43)
#
# tests/test_types.py already carries the FULL P43 guard (including the
# backlog-citation check that a reserved role must cite a live backlog
# item). This is a self-contained restatement for this invariants file per
# the handoff's ask -- not a replacement for test_types.py's fuller guard.
# ===========================================================================

_ROLE_DISPATCH_RE = re.compile(r"role=Role\.(\w+)")


def _dispatched_roles() -> frozenset[Role]:
    names = set(_ROLE_DISPATCH_RE.findall(DAEMON_SRC)) | set(_ROLE_DISPATCH_RE.findall(RECONCILE_SRC))
    return frozenset(Role[n] for n in names)


def test_every_role_is_dispatched_or_reserved():
    dispatched = _dispatched_roles()
    assert dispatched.isdisjoint(RESERVED_ROLES)
    assert set(Role) == dispatched | RESERVED_ROLES


def test_implementer_role_is_dispatched_not_reserved():
    """Non-hollow anchor: proves a real dispatch site is found."""
    assert Role.IMPLEMENTER in _dispatched_roles()
    assert Role.IMPLEMENTER not in RESERVED_ROLES


def test_self_review_role_is_reserved_not_dispatched():
    """Non-hollow anchor: proves the scan finds SELF_REVIEW genuinely
    absent from dispatch sites (the P43 bug this generalizes)."""
    assert Role.SELF_REVIEW in RESERVED_ROLES
    assert Role.SELF_REVIEW not in _dispatched_roles()


# ===========================================================================
# INVARIANT 3: every EventType handled-or-known-ignored
#
# storage.py's own module docstring: "everything else -- no projection
# effect" for a long list of pure audit/notification event types (read
# directly off events.jsonl by notify.py/render.py, never replayed into a
# TaskStateFile). That is a deliberate, documented design -- but it must
# stay a CLOSED list: a new EventType added without being wired into
# apply_event AND without being added here would be silently dropped by
# the projection with nobody noticing.
# ===========================================================================

def _apply_event_body() -> str:
    # Start at `_TRANSITION_EVENT_TYPES = (` (module-level, just above
    # apply_event) rather than `def apply_event(` itself: apply_event's own
    # body only references that tuple BY NAME (`if t in
    # _TRANSITION_EVENT_TYPES`), never the literal `EventType.TASK_
    # TRANSITIONED` etc. -- those literals live in the tuple's own
    # definition, so a scan starting at the `def` line alone would miss
    # them (confirmed: this was a real bug in a first draft of this
    # scanner, caught by test_task_transitioned_is_handled_not_ignored
    # below going red).
    start = STORAGE_SRC.index("_TRANSITION_EVENT_TYPES = (")
    func_start = STORAGE_SRC.index("def apply_event(")
    tail = STORAGE_SRC[func_start + len("def apply_event("):]
    m = re.search(r"\ndef ", tail)
    end = func_start + len("def apply_event(") + (m.start() if m else len(tail))
    return STORAGE_SRC[start:end]


_APPLY_EVENT_BODY = _apply_event_body()
_EVENTTYPE_REF_RE = re.compile(r"EventType\.(\w+)")


def _apply_event_handled_types() -> frozenset[EventType]:
    names = set(_EVENTTYPE_REF_RE.findall(_APPLY_EVENT_BODY))
    handled = {EventType[n] for n in names if n in EventType.__members__}
    # ATTEMPT_* events are matched by a prefix check (`t.value.startswith
    # ("ATTEMPT_")`), not a literal `EventType.ATTEMPT_X` reference, so the
    # regex scan above cannot see them directly -- detect the prefix branch
    # itself and expand to every EventType member it actually covers.
    if 'startswith("ATTEMPT_")' in _APPLY_EVENT_BODY:
        handled |= {e for e in EventType if e.value.startswith("ATTEMPT_")}
    return frozenset(handled)


# See module docstring: these are pure audit/notification log entries with
# NO statefile projection BY DESIGN. A member here must be a genuine no-op
# in apply_event -- see test_known_ignored_event_types_are_true_noops.
KNOWN_IGNORED_EVENT_TYPES: frozenset[EventType] = frozenset({
    EventType.PROJECT_REGISTERED,
    EventType.DOCTOR_FINDING,
    EventType.CARVE_OUTCOME,
    EventType.DECISION_OPENED,
    EventType.DECISION_RESOLVED,
    EventType.PROVIDER_STATE_CHANGED,
    # GATE_STARTED/REVIEW_RECORDED/EVIDENCE_RECORDED/WAVE_CLOSED: audit-only
    # markers whose real state consequence (if any) arrives via a SEPARATE,
    # properly-handled event (e.g. a review verdict's TASK_TRANSITIONED to
    # MERGE_READY/REVIEW_REJECTED) -- storage.py's own docstring lists only
    # GATE_FINISHED and WAVE_OPENED as projection-affecting among this
    # family, everything else in it is explicitly "no projection effect".
    EventType.GATE_STARTED,
    EventType.REVIEW_RECORDED,
    EventType.EVIDENCE_RECORDED,
    EventType.WAVE_CLOSED,
    EventType.SPEC_ATTENTION,
    EventType.NEEDS_OPERATOR,
    EventType.NOTIFICATION_REQUESTED,
    EventType.NOTIFICATION_DELIVERED,
    EventType.NOTIFICATION_FAILED,
    EventType.BUDGET_WARNING,
    EventType.BUDGET_EXHAUSTED,
    EventType.ARTIFACT_REGISTERED,
    EventType.DAEMON_STARTED,
    EventType.DAEMON_STOPPED,
    EventType.TICK_ERROR,
    EventType.CONFIG_CHANGED,
})


def test_every_event_type_handled_or_known_ignored():
    handled = _apply_event_handled_types()
    assert handled.isdisjoint(KNOWN_IGNORED_EVENT_TYPES)
    missing = set(EventType) - (handled | KNOWN_IGNORED_EVENT_TYPES)
    assert not missing, (
        f"EventType(s) neither handled in apply_event nor in "
        f"KNOWN_IGNORED_EVENT_TYPES -- a new event type can be silently "
        f"dropped by the projection: {missing}")


def test_task_transitioned_is_handled_not_ignored():
    """Non-hollow anchor: proves the scan finds a real handled type."""
    assert EventType.TASK_TRANSITIONED in _apply_event_handled_types()
    assert EventType.TASK_TRANSITIONED not in KNOWN_IGNORED_EVENT_TYPES


def test_attempt_started_is_handled_via_prefix_branch():
    """Non-hollow anchor specifically for the ATTEMPT_ prefix-branch
    detection (not just the literal EventType.X regex path)."""
    assert EventType.ATTEMPT_STARTED in _apply_event_handled_types()


def test_config_changed_is_known_ignored_not_handled():
    """Non-hollow anchor: proves a real ignored type is found genuinely
    absent from the handled set, not just that KNOWN_IGNORED_EVENT_TYPES
    is asserted by fiat."""
    assert EventType.CONFIG_CHANGED in KNOWN_IGNORED_EVENT_TYPES
    assert EventType.CONFIG_CHANGED not in _apply_event_handled_types()


@pytest.mark.parametrize("ev_type", sorted(KNOWN_IGNORED_EVENT_TYPES, key=lambda e: e.value))
def test_known_ignored_event_types_are_true_noops(ev_type):
    """Behavioral proof (not just textual grep): applying a KNOWN-IGNORED
    event against a KNOWN, present task must be a genuine no-op --
    statefile unchanged, zero affected task_ids -- not silently corrupt
    state. (test_properties.py's test_apply_event_unknown_task_is_noop
    already covers the weaker "task_id absent from states" case for nearly
    all event types; this covers the stronger "task IS present" case
    specifically for the types this file declares ignored.)"""
    states = {"t1": _tsf(task_id="t1", state=TaskState.QUEUED)}
    before = states["t1"].to_dict()
    ev = _event(ev_type, task_id="t1")
    affected = storage.apply_event(states, ev)
    assert affected == []
    assert states["t1"].to_dict() == before


# ===========================================================================
# INVARIANT 4: transition-graph well-formedness
#
# test_properties.py::test_task_transition_graph_shape already asserts
# terminal states have empty outgoing sets and non-terminal states don't.
# This section adds the two checks that file doesn't cover: full
# reachability, and target-type safety.
# ===========================================================================

def test_task_transition_graph_fully_reachable_from_draft():
    """Every TaskState must be reachable from DRAFT (the conceptual entry
    point) via TASK_TRANSITIONS edges. An unreachable state would be dead
    code in the enum (declared, never enterable) -- a milder cousin of the
    dead-end-state bug Invariant 1 catches (those are ENTERABLE-but-stuck;
    this is NEVER-enterable). All 15 TaskStates are in fact reachable from
    DRAFT today (this test passes) -- DRAFT/READY_TO_CARVE/MERGED/
    VALIDATING being unreachable IN PRACTICE (no code ever assigns them) is
    a *dispatch-code* gap (Invariant 1), not a *graph* gap; the graph edges
    themselves are fully connected."""
    seen = {TaskState.DRAFT}
    frontier = [TaskState.DRAFT]
    while frontier:
        cur = frontier.pop()
        for nxt in TASK_TRANSITIONS[cur]:
            if nxt not in seen:
                seen.add(nxt)
                frontier.append(nxt)
    assert seen == set(TaskState), f"unreachable from DRAFT: {set(TaskState) - seen}"


def test_task_transition_targets_are_all_taskstate_members():
    """No transition source or target may fall outside the TaskState enum
    (guards against e.g. a raw string slipping into a frozenset by typo --
    cheap insurance, but the kind of thing that reads as impossible right
    up until it happens)."""
    for cur, targets in TASK_TRANSITIONS.items():
        assert isinstance(cur, TaskState)
        for t in targets:
            assert isinstance(t, TaskState), f"{cur}: non-TaskState target {t!r}"
