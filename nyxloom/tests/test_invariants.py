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
    FIXED 2026-07-19 (nyxloom-P45-ready-to-carve-triage package): reconcile.
    py's module contract item 10 (REJECT LOOP) now routes a REVIEW_REJECTED
    task with attempts exhausted INTO READY_TO_CARVE (previously a silent
    no-op, itself a documented KNOWN GAP), and the new item 12 gives
    READY_TO_CARVE a real handler OUT: re-dispatch the single strategic
    carver (reconcile.CarveDispatch / daemon._execute_carve_dispatch, the
    SAME mechanism item 9's automatic headroom-refill trigger already uses
    -- no second carve authority), then Transition(SUPERSEDED) in the same
    pass (self-limiting). See tests/test_no_dead_end_ready_to_carve below
    (now plain, non-xfail) and tests/test_reconcile.py's O1/O3/O4 tests.
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

FIXED 2026-08-04 (CR-07d): DRAFT was the remaining gap, pinned below (in the
git history of this file) as a KNOWN_STATE_GAPS entry with a dedicated
xfail(strict) test proving it stranded on real code. Removed rather than
implemented -- nothing ever assigns a task to DRAFT (confirmed at the time:
"daemon.py's CreateTask hardcodes CARVED"), so the fix is that it is no
longer a constructible TaskState at all. `types.TaskState._missing_` keeps a
pre-CR-07d persisted `"DRAFT"` value loading (mapped to NEEDS_DECISION)
rather than raising. See the KNOWN_STATE_GAPS declaration below, now empty.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

import pytest

from nyxloom import storage
from nyxloom.config import MutexDef, Policy, ProjectConfig, RouteDef, Routes
from nyxloom.reconcile import (
    Action, DispatchImplementer, EmitAttemptExit, LaunchSelfReview, OpenWave,
    ReconcileInput, RunPostMergeGate, Transition, plan_project,
)
from nyxloom.types import (
    TASK_TRANSITIONS, TERMINAL_TASK_STATES, RESERVED_ROLES,
    Actor, ActorKind, Attempt, AttemptState, CarverStatus, Event, EventType, Frontmatter,
    Receipt, ReceiptResult, Role, Route, Scope, Source, TaskState,
    TaskStateFile,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
RECONCILE_SRC = (REPO_ROOT / "src" / "nyxloom" / "reconcile.py").read_text()
# CR-06a: the planner is no longer one file. The lifecycle, review and
# attention concerns moved to `rules_*.py` and the pass machinery to
# `planning.py`, so a scanner that reads only `reconcile.py` would stop seeing
# the MERGED / VALIDATING / REVIEW_REJECTED branches and report them as
# unplanned dead-ends. Same fix, and same reason, as CR-04b's `projection.py`
# note above and the `effects*.py` glob further down: read the modules that
# DEFINE the thing, and glob them so a concern that moves between rule modules
# (CR-06b and CR-06c still have three to move) does not silently drop out.
PLANNER_SRC = "\n".join(
    p.read_text(encoding="utf-8")
    for p in sorted(
        [REPO_ROOT / "src" / "nyxloom" / "reconcile.py",
         REPO_ROOT / "src" / "nyxloom" / "planning.py",
         *(REPO_ROOT / "src" / "nyxloom").glob("rules_*.py")]
    )
)
# CR-04b: `apply_event` and the transition tuple moved to projection.py when
# the pure functions were extracted to break the store's import cycle. This
# scanner reads the module that DEFINES them; reading storage.py would now
# find nothing and the invariant would pass vacuously -- which is exactly
# how an "every EventType is handled" check stops checking.
STORAGE_SRC = (REPO_ROOT / "src" / "nyxloom" / "projection.py").read_text()
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
    test_types.py): the set of TaskStates that the planner has SOME branch
    keyed on. Scans the whole planner surface (CR-06a) -- see PLANNER_SRC."""
    names = set(_TASKSTATE_REF_RE.findall(PLANNER_SRC))
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
#
# TaskState.READY_TO_CARVE REMOVED 2026-07-19 (nyxloom-P45-ready-to-carve-
# triage): reconcile.py's module contract item 10 (REJECT LOOP) now routes
# a REVIEW_REJECTED task with attempts exhausted to READY_TO_CARVE, and item
# 12 (new) gives READY_TO_CARVE a real handler (re-dispatches the single
# strategic carver, then Transition(SUPERSEDED)) -- genuinely `planned` now,
# same reasoning as the MERGED/VALIDATING removal above. See
# test_no_dead_end_ready_to_carve below (no longer xfail).
#
# TaskState.DRAFT REMOVED 2026-08-04 (CR-07d): not fixed by giving it a
# reconcile.py branch, but by deleting it as a constructible TaskState --
# see the module docstring. There is nothing left to track here.
KNOWN_STATE_GAPS: frozenset[TaskState] = frozenset()


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


def test_known_state_gaps_is_empty():
    """CR-07d closed the last tracked gap (DRAFT) by removing the state
    rather than by planning or documenting it -- so there is nothing left
    for this set to legitimately hold. A future gap should reopen this as a
    real member with a citation, not silently repopulate it."""
    assert KNOWN_STATE_GAPS == frozenset()


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


def test_no_dead_end_self_reviewing():
    """B5 2026-07-20: SELF_REVIEWING is a new non-terminal state -- it must not
    be a dead end. A task there (its implementer attempt EXITED, warm session
    ready to borrow) progresses: reconcile plans a LaunchSelfReview naming the
    source implementer attempt. Without the planner branch this would strand,
    exactly the failure class this invariant file exists to catch."""
    att = _attempt(attempt_id="att-impl-1", state=AttemptState.EXITED,
                   role=Role.IMPLEMENTER,
                   receipt=Receipt(result=ReceiptResult.DONE, exit_code=0))
    tsf = _tsf(task_id="INV-01", state=TaskState.SELF_REVIEWING, attempts=[att])
    inp = _base_input("INV-01", TaskState.SELF_REVIEWING, tsf=tsf)
    actions = plan_project(inp)
    assert any(isinstance(a, LaunchSelfReview) and _action_touches_task(a, "INV-01")
               and a.source_attempt_id == "att-impl-1" for a in actions)


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


# FIXED 2026-08-04 (CR-07d). Used to be xfail(strict=True)-pinned here: "no
# code anywhere ever assigns DRAFT as a task's live state... a task manually
# placed in DRAFT would strand forever". The fix is not a reconcile.py branch
# (nothing ever legitimately produces a DRAFT task) but removing the state:
# DRAFT is no longer a member of TaskState at all, so the dead end it could
# strand in no longer exists to strand in.
def test_draft_is_no_longer_a_constructible_taskstate():
    assert not hasattr(TaskState, "DRAFT")
    assert "DRAFT" not in TaskState.__members__


def test_a_legacy_draft_value_reads_as_needs_decision_not_a_crash():
    """types.TaskState._missing_'s read-compat contract: a pre-CR-07d
    persisted `"DRAFT"` value must not raise ValueError, and must not
    silently resurrect a removed state -- it lands on NEEDS_DECISION, one of
    DRAFT's own former edges, instead."""
    assert TaskState("DRAFT") is TaskState.NEEDS_DECISION


def test_a_legacy_draft_derived_task_releases_to_queued_next_pass():
    """The read-compat contract is NOT "forces human review" -- independent
    review traced rules_lifecycle.decision_hold and found that a
    NEEDS_DECISION task with no open D-dep releases straight back to QUEUED
    on the very next reconcile pass, and a legacy DRAFT row -- never having
    existed on real data -- would carry no D-dep. Pinned here so that
    behavioral consequence is proven rather than assumed from the enum
    mapping alone."""
    inp = _base_input("LEG-1", TaskState.NEEDS_DECISION)
    actions = plan_project(inp)
    assert any(isinstance(a, Transition) and a.to is TaskState.QUEUED
               and _action_touches_task(a, "LEG-1") for a in actions)


# FIXED 2026-07-19 (nyxloom-P45-ready-to-carve-triage package). Used to be
# xfail(strict=True)-pinned: TaskState.READY_TO_CARVE has outgoing
# TASK_TRANSITIONS edges and a STATE_LEGEND entry ('waiting to become a real
# task (CARVED)') implying automatic progression -- but across the ENTIRE
# src/ tree it was referenced ONLY in types.py (the graph itself) and
# render.py (the legend); no code path ever assigned it as a transition
# target, and reconcile.py never checked for a task already in this state
# either. Now fixed: reconcile.py's module contract item 10 (REJECT LOOP)
# routes a REVIEW_REJECTED task with attempts exhausted INTO
# READY_TO_CARVE, and item 12 gives READY_TO_CARVE a real handler OUT --
# re-dispatch the single strategic carver (reconcile.CarveDispatch /
# daemon._execute_carve_dispatch, the SAME mechanism item 9's automatic
# headroom-refill trigger already uses), then Transition(SUPERSEDED) in the
# same pass. This is now a plain (non-xfail) behavioral proof, matching this
# file's other now-fixed dead-end tests (test_no_dead_end_merged/
# test_no_dead_end_validating above) -- a passing strict-xfail test is
# itself a failure (XPASS), so the marker was REMOVED, not merely loosened.
#
# The route override below (frontier-review tier, unlike _base_input's
# default flash-high-only Routes) is required because this handler's
# in-flight/route-health guard specifically checks the 'frontier-review'
# tier (module contract item 9's own predicate, reused verbatim) --
# _base_input's default fixture has no such tier registered.
def test_no_dead_end_ready_to_carve():
    routes = _routes(tier="frontier-review")
    inp = _base_input("INV-01", TaskState.READY_TO_CARVE, routes=routes)
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

# CR-05b: the control plane's DISPATCH SURFACE is no longer one file. Effect
# execution now lives in `effects*.py` modules, and more move there with each
# CR-05 sub-package -- so the scan globs them rather than listing them, and a
# family that moves does not silently drop out of this census.
CONTROL_PLANE_SRC = "\n".join(
    p.read_text(encoding="utf-8")
    for p in sorted((REPO_ROOT / "src" / "nyxloom").glob("effects*.py"))
)

_ROLE_DISPATCH_RE = re.compile(r"role=Role\.(\w+)")


def _dispatched_roles() -> frozenset[Role]:
    names = (set(_ROLE_DISPATCH_RE.findall(DAEMON_SRC))
             | set(_ROLE_DISPATCH_RE.findall(PLANNER_SRC))
             | set(_ROLE_DISPATCH_RE.findall(CONTROL_PLANE_SRC)))
    return frozenset(Role[n] for n in names)


def test_every_role_is_dispatched_or_reserved():
    dispatched = _dispatched_roles()
    assert dispatched.isdisjoint(RESERVED_ROLES)
    assert set(Role) == dispatched | RESERVED_ROLES


def test_implementer_role_is_dispatched_not_reserved():
    """Non-hollow anchor: proves a real dispatch site is found."""
    assert Role.IMPLEMENTER in _dispatched_roles()
    assert Role.IMPLEMENTER not in RESERVED_ROLES


def test_self_review_role_is_dispatched_not_reserved():
    """B5 2026-07-20: inverts the pre-B5 anchor. The self_review leg wired
    Role.SELF_REVIEW into daemon.py's LaunchSelfReview executor, so the scan now
    finds it at a real `role=Role.SELF_REVIEW` dispatch site and it is no longer
    reserved -- the defined-but-dead -> wired transition RESERVED_ROLES tracks."""
    assert Role.SELF_REVIEW in _dispatched_roles()
    assert Role.SELF_REVIEW not in RESERVED_ROLES


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
    # B21 2026-07-23 (D-R16 §3): scope-amendment events are pass-through --
    # daemon.py counts them by scanning storage.iter_events directly (no
    # TaskStateFile projection), so apply_event intentionally ignores them
    # (same no-op shape as DECISION_OPENED/RESOLVED above). Registered here so
    # the projection-guard invariant does not read them as silently-dropped.
    EventType.SCOPE_AMENDMENT_REQUESTED,
    EventType.SCOPE_AMENDMENT_APPROVED,
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
    # F (factory-hardening) 2026-07-25: MERGE_REVERTED is an audit-only marker
    # emitted when the daemon auto-reverts a published-but-failing merge (CAS
    # update-ref back to the merge commit's parent). Its real state consequence
    # arrives via the SEPARATE, properly-handled TASK_BLOCKED transition on the
    # same pass -- so the projection ignores it (same no-op shape as the other
    # audit markers here). See daemon._run_post_merge_gate + reference/LESSONS.md L4.
    EventType.MERGE_REVERTED,
    # FN-1 2026-07-24: findings are advisory, projection-less informational
    # events (same no-op shape as ARTIFACT_REGISTERED). See findings.py.
    EventType.FINDING_RECORDED,
    EventType.DAEMON_STARTED,
    EventType.DAEMON_STOPPED,
    EventType.TICK_ERROR,
    EventType.CONFIG_CHANGED,
    # CR-15: security/control-plane audit markers are deliberately outside
    # the task projection.  Consumers query them from the event ledger.
    EventType.CONTROL_MUTATION_REFUSED,
    EventType.CONTROL_CREDENTIAL_ROTATED,
    EventType.DECISION_REPLY_RECORDED,
    EventType.INTAKE_REPLY_RECORDED,
    EventType.FINDING_PROMOTED,
    # F018 P1 2026-07-24 (plan-long-running-carver.md §2.2): audit-only carver
    # session events — no TaskStateFile projection. Consumed by the pure
    # carver_session.project_session projector, never by storage.apply_event.
    # See carver_session.py.
    EventType.CARVER_SESSION_STARTED,
    EventType.CARVER_SESSION_RESUMED,
    EventType.CARVER_CONTEXT_CONSUMED,
    EventType.CARVER_PROPOSAL_RECORDED,
    # F018 P3b 2026-07-25 (AD1 fix): the durable admission marker (plan
    # §4.2 step 2) -- audit-only, same no-op shape as its CARVER_* siblings
    # above. daemon._validated_carve_proposals scans for it directly (no
    # TaskStateFile projection needed). See daemon.py's
    # _proposal_already_admitted.
    EventType.CARVER_PROPOSAL_ADMITTED,
    EventType.CARVER_COMPACTION_REQUESTED,
    EventType.CARVER_COMPACTION_FINISHED,
    EventType.CARVER_SESSION_ROTATED,
    EventType.CARVER_SESSION_DEGRADED,
    # GA4 2026-07-25 (module contract item 16): the gate-verify cadence's
    # audit-only completion marker -- no TaskStateFile projection (same
    # no-op shape as the CARVER_* family above). See gate_canary.py /
    # daemon._drain_gate_verify_results.
    EventType.GATE_VERIFY_RECORDED,
    # CR-02a 2026-08-03 (authoritative snapshot fail-closed audit): the
    # snapshot fan-in's two durable verdict records. Audit-only by
    # construction -- a snapshot fault must NEVER mutate task state, because
    # "we could not read the world" is precisely the situation in which the
    # projection must not move. Their whole job is to make the fault visible
    # and replayable while the pass performs zero effects. See snapshot.py.
    EventType.SNAPSHOT_UNAVAILABLE,
    EventType.SNAPSHOT_DEGRADED,
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

def test_task_transition_graph_fully_reachable_from_carved():
    """Every TaskState must be reachable from CARVED (the conceptual entry
    point since CR-07d removed DRAFT -- daemon.py's CreateTask hardcodes
    CARVED, so it is where a task's tracked life actually begins, not a
    theoretical stand-in the way DRAFT was) via TASK_TRANSITIONS edges. An
    unreachable state would be dead code in the enum (declared, never
    enterable) -- a milder cousin of the dead-end-state bug Invariant 1
    catches (those are ENTERABLE-but-stuck; this is NEVER-enterable). All 14
    other TaskStates are in fact reachable from CARVED today (this test
    passes); the graph edges are fully connected."""
    seen = {TaskState.CARVED}
    frontier = [TaskState.CARVED]
    while frontier:
        cur = frontier.pop()
        for nxt in TASK_TRANSITIONS[cur]:
            if nxt not in seen:
                seen.add(nxt)
                frontier.append(nxt)
    assert seen == set(TaskState), f"unreachable from CARVED: {set(TaskState) - seen}"


def test_task_transition_targets_are_all_taskstate_members():
    """No transition source or target may fall outside the TaskState enum
    (guards against e.g. a raw string slipping into a frozenset by typo --
    cheap insurance, but the kind of thing that reads as impossible right
    up until it happens)."""
    for cur, targets in TASK_TRANSITIONS.items():
        assert isinstance(cur, TaskState)
        for t in targets:
            assert isinstance(t, TaskState), f"{cur}: non-TaskState target {t!r}"
