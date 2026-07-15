"""Pure reconcile planner (SPEC §5, §8, §9). PACKAGE P02.

The daemon (or `tick --once`) builds a ReconcileInput snapshot from disk,
calls plan_project(), and EXECUTES the returned actions. This module is pure:
NO filesystem, NO subprocess, NO storage imports beyond types — everything it
needs arrives in the snapshot. That purity is what makes the scheduler
property-testable.

INTERFACE CONTRACT (frozen). Semantics:

1. NEW HANDOFFS: a frontmatter id with no statefile -> CreateTask (initial
   state CARVED), then (same or later pass) CARVED -> QUEUED via Transition
   when lint_clean[id] is True.
2. DECISION HOLDS: QUEUED task with an unresolved D-dep (decisions_open
   contains it) -> Transition to NEEDS_DECISION (notes name the D-id);
   NEEDS_DECISION task whose D-deps are all resolved -> Transition QUEUED.
3. DISPATCH (SPEC §5.7): QUEUED, not paused (task or project), all task deps
   COMPLETED (statefile) or their branch in merged_branches, active count <
   policy.max_active_tasks, attempts count < policy.max_attempts_per_task,
   budget_remaining is None or > 0, mutex leases all available
   (leases_free), and a healthy route exists for fm.tier (routes order,
   skipping provider_ok[route_id] is False) -> DispatchImplementer with the
   first healthy route. Emit at most (max_active - active) dispatches per
   pass, in sorted task-id order (determinism).
4. ATTEMPT RECEIPTS (amended 2026-07-15 after the E2E smoke found the
   original RUNNING-only wording left tasks stuck ACTIVE when the wrapper
   emitted its own ATTEMPT_EXITED): a receipt (receipts[attempt_id] not
   None) triggers EmitAttemptExit when the attempt is RUNNING/PREFLIGHTING/
   STALLED, or when it is already EXITED but the task is still ACTIVE (only
   the task transition remains; the daemon's execution is idempotent) ->
     (daemon appends ATTEMPT_EXITED with the receipt/usage merged in and
     transitions the task: receipt.result done -> AWAITING_REVIEW; blocked ->
     BLOCKED (blocker type contract, unblock 'triage BLOCKED reason');
     limit -> QUEUED (attempt does not count toward max_attempts; also
     ProviderPause(route_id)); error -> QUEUED if attempts remain else
     BLOCKED (environment)).
   - no receipt, pid dead, attempt RUNNING/PREFLIGHTING/STALLED ->
     MarkInterrupted (daemon emits ATTEMPT_INTERRUPTED)
     then next pass ResumeAttempt (INTERRUPTED attempts with attempts-budget
     left AND a resume handle -> ResumeAttempt; P14 2026-07-15 fix -- no
     handle, OR attempts exhausted -> Transition(task, BLOCKED) with a typed
     environment blocker (unblock 'operator: inspect attempts'); the prior
     "fresh DispatchImplementer counting a new attempt" wording was never
     actually implemented and left the task ACTIVE forever with zero
     events -- a live-incident silent dead-end). P15 2026-07-15: when
     inp.pause_mode == "drain-agents", ResumeAttempt (a new agent process)
     is skipped entirely for this pass -- the attempt is left parked
     INTERRUPTED (NOT transitioned to BLOCKED; that would misrepresent a
     temporary drain as a dead end) until a later pass observes run or
     drain-handoffs.
   - no receipt, pid alive, elapsed since attempt.started exceeds the
     wall-clock cap (fm.budget.max_wall_seconds if set, else
     inp.attempt_max_wall_seconds, P14 2026-07-15 item 6) -> InterruptAttempt
     UNCONDITIONALLY -- bypasses the log-quiet/stall-confirm gate below
     entirely (an attempt can run forever with a perfectly fresh, chatty log
     and still needs a hard cap).
   - no receipt, pid alive, under the wall-clock cap, log quiet >
     policy.stall_log_quiet_seconds -> StallCheck (tier 2 evidence gathering
     is the daemon's job; the planner only flags QUIET). If
     stall_confirmed[attempt_id] is True -> MarkStalled (P14 2026-07-15 item
     2: daemon appends ATTEMPT_STALLED, state STALLED -- a confirmed stall
     must be VISIBLE, not just interrupted silently); once the attempt is
     STALLED (a later pass observes the persisted state) -> InterruptAttempt
     (daemon kills pgid; wrapper writes interrupted receipt).
5. REVIEW WAVES (SPEC §7): tasks AWAITING_REVIEW with wave_id None, batch in
   sorted order into OpenWave(task_ids up to policy.wave_max_diffs); a wave
   opens when >= wave_max_diffs are waiting OR the oldest has waited >
   wave_open_after_seconds (input field). For an open wave whose review
   attempt is not yet running -> LaunchReview(wave_id, task_ids).

   P15 2026-07-15 (factory-state pause MODES, user directive): inp.pause_mode
   is "run" (default; everything below unaffected), "drain-handoffs", or
   "drain-agents". `project_paused` (unchanged, still gates DISPATCH inside
   dispatch_eligible only) is True for BOTH drain modes -- new dispatch
   never starts under either. The distinction only matters for the two
   OTHER "new agent process" starts: ResumeAttempt (item 4 below) and
   LaunchReview (this item) are additionally skipped when pause_mode is
   "drain-agents" (no new agent process of ANY kind); they still fire under
   "drain-handoffs" (in-flight handoffs run their full pipeline to
   completion) exactly as under "run". OpenWave itself is pure bookkeeping
   (no process start) and is never gated.
6. PROGRESS RATCHET (SPEC §8): if the last
   policy.max_consecutive_zero_progress_merges merges (merge_history, most
   recent first: list of (task_id, progress_unit_count, source_kind)) all
   have 0 units AND all have source_kind 'review' -> SpecAttention('ratchet',
   ...) once (dedupe via ratchet_already_open flag in input).
7. SPEC HEALTH (SPEC §9 triggers 1-3): carve_outcomes input carries recent
   CARVE_OUTCOME payloads -> SpecAttention for SPEC_GAP/DECISION_REQUIRED;
   review_rejections_by_area counts -> SpecAttention when >=2 in one area;
   blocked_underspecified_count >= 3 in window -> SpecAttention.
8. Actions NEVER embed prose from handoff bodies (payload injection rule) —
   only ids, enum values, and short fixed strings.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import ProjectConfig, RouteDef, Routes
from .types import (
    Blocker, BlockerType, Frontmatter, TaskState, TaskStateFile, AttemptState,
    ReceiptResult, Role, TERMINAL_ATTEMPT_STATES, TERMINAL_TASK_STATES
)

# P14 2026-07-15 item 6: per-attempt wall-clock cap default (3h). Policy is
# frozen for this package (only the NotifyConfig.push_classes entry may be
# edited -- see handoff/P14-stall-hardening.md rules), so this lives here as
# a ReconcileInput field/module default rather than config.Policy; the
# daemon reads cfg.policy.attempt_max_wall_seconds via getattr with this as
# the fallback, so a future Policy field (if config.py is ever unfrozen for
# it) would take effect with zero code change here.
DEFAULT_ATTEMPT_MAX_WALL_SECONDS = 10800


# --- actions (daemon executes; tests assert on these) ----------------------

@dataclass
class Action:
    task_id: str | None = None


@dataclass
class CreateTask(Action):
    fm: Frontmatter | None = None
    handoff_path: str | None = None


@dataclass
class Transition(Action):
    to: TaskState | None = None
    notes: str | None = None
    # P14 2026-07-15 item 4: set only for to=BLOCKED transitions that need a
    # typed blocker (the daemon emits TASK_BLOCKED instead of a plain
    # TASK_TRANSITIONED when this is not None). Enum + short fixed strings
    # only -- never handoff-body prose (payload injection rule).
    blocker: Blocker | None = None


@dataclass
class DispatchImplementer(Action):
    route_id: str | None = None


@dataclass
class ResumeAttempt(Action):
    attempt_id: str | None = None


@dataclass
class InterruptAttempt(Action):
    attempt_id: str | None = None


@dataclass
class MarkInterrupted(Action):
    attempt_id: str | None = None


@dataclass
class StallCheck(Action):
    attempt_id: str | None = None


@dataclass
class MarkStalled(Action):
    """P14 2026-07-15 item 2: tier-2 confirmed a stall -- make it VISIBLE
    (ATTEMPT_STALLED, state STALLED) before ever interrupting. The prior
    behaviour fed stall_confirmed straight into InterruptAttempt with no
    event in between; a confirmed stall was invisible until the wrapper's
    own ATTEMPT_INTERRUPTED landed."""
    attempt_id: str | None = None


@dataclass
class EmitAttemptExit(Action):
    attempt_id: str | None = None


@dataclass
class ProviderPause(Action):
    route_id: str | None = None


@dataclass
class OpenWave(Action):
    task_ids: list[str] = field(default_factory=list)


@dataclass
class LaunchReview(Action):
    wave_id: str | None = None
    task_ids: list[str] = field(default_factory=list)


@dataclass
class SpecAttention(Action):
    reason: str | None = None       # 'ratchet'|'carve-outcome'|'rejections'|'blocked-underspecified'
    detail: str | None = None


# --- input snapshot ---------------------------------------------------------

@dataclass
class ReconcileInput:
    now: datetime
    cfg: ProjectConfig
    routes: Routes
    states: dict[str, TaskStateFile]
    frontmatters: dict[str, tuple[Frontmatter, str]]   # id -> (fm, relpath)
    lint_clean: dict[str, bool]
    project_paused: bool
    decisions_open: set[str]                            # D-ids currently OPEN
    merged_branches: set[str]
    leases_free: dict[str, bool]                        # lease name -> available
    provider_ok: dict[str, bool]                        # route_id -> preflight ok
    log_quiet_seconds: dict[str, float | None]          # attempt_id -> seconds since log mtime
    pid_alive: dict[str, bool]
    receipts: dict[str, dict | None]                    # attempt_id -> receipt dict
    stall_confirmed: dict[str, bool] = field(default_factory=dict)
    budget_remaining: float | None = None
    wave_open_after_seconds: int = 1800
    merge_history: list[tuple[str, int, str]] = field(default_factory=list)
    ratchet_already_open: bool = False
    carve_outcomes: list[dict] = field(default_factory=list)
    review_rejections_by_area: dict[str, int] = field(default_factory=dict)
    blocked_underspecified_count: int = 0
    # P14 2026-07-15 item 6: default per-attempt wall-clock cap (seconds);
    # a task's own fm.budget.max_wall_seconds overrides this when set.
    attempt_max_wall_seconds: int = DEFAULT_ATTEMPT_MAX_WALL_SECONDS
    # P15 2026-07-15: factory-state pause MODE ("run"|"drain-handoffs"|
    # "drain-agents"). Purely additive -- `project_paused` above is left
    # untouched (still True for either drain mode; dispatch_eligible's
    # 'paused' check is unchanged) so every pre-existing test that only sets
    # project_paused keeps its old semantics (no gate on resume/review) via
    # this field's "run" default. Only ResumeAttempt/LaunchReview consult it.
    pause_mode: str = "run"


def plan_project(inp: ReconcileInput) -> list[Action]:
    """Deterministic action plan for one project (see module contract).

    Output order: task lifecycle actions (sorted by task id), then attempt
    actions, then waves, then SpecAttention — so tests can assert exactly.
    """
    # === Task lifecycle actions (sorted by task_id) ===
    lifecycle_by_id: dict[str, list[Action]] = {}

    # 1. NEW HANDOFFS: frontmatter id absent from states -> CreateTask
    for fm_id, (fm, handoff_path) in inp.frontmatters.items():
        if fm_id not in inp.states:
            lifecycle_by_id.setdefault(fm_id, []).append(
                CreateTask(task_id=fm_id, fm=fm, handoff_path=handoff_path)
            )

    # 2. Existing tasks: process state transitions
    for fm_id, (fm, handoff_path) in inp.frontmatters.items():
        if fm_id not in inp.states:
            continue

        tsf = inp.states[fm_id]
        task_actions: list[Action] = lifecycle_by_id.setdefault(fm_id, [])

        # CARVED -> QUEUED transition: check lint_clean
        if tsf.state == TaskState.CARVED and inp.lint_clean.get(fm_id, False):
            task_actions.append(Transition(task_id=fm_id, to=TaskState.QUEUED, notes=None))

        # Decision hold logic
        d_deps = fm.decision_deps()
        open_d_deps = [d for d in d_deps if d in inp.decisions_open]

        if tsf.state == TaskState.QUEUED and open_d_deps:
            # Transition to NEEDS_DECISION
            notes = ", ".join(open_d_deps)
            task_actions.append(Transition(task_id=fm_id, to=TaskState.NEEDS_DECISION, notes=notes))
        elif tsf.state == TaskState.NEEDS_DECISION and not open_d_deps:
            # Transition back to QUEUED
            task_actions.append(Transition(task_id=fm_id, to=TaskState.QUEUED, notes=None))

    # 3. Dispatch eligible QUEUED tasks (with capacity limit)
    # Count current active tasks
    active_count = sum(
        1 for tsf in inp.states.values()
        if tsf.state in (TaskState.ACTIVE, TaskState.AWAITING_REVIEW)
    )
    dispatch_capacity = inp.cfg.policy.max_active_tasks - active_count

    # Find all eligible QUEUED tasks, sorted by task_id
    queued_tasks = []
    for fm_id, (fm, handoff_path) in inp.frontmatters.items():
        if fm_id not in inp.states:
            continue
        tsf = inp.states[fm_id]
        if tsf.state == TaskState.QUEUED:
            queued_tasks.append((fm_id, fm, tsf))
    queued_tasks.sort(key=lambda x: x[0])  # Sort by task_id

    # Dispatch up to capacity
    dispatched = 0
    for fm_id, fm, tsf in queued_tasks:
        if dispatched >= dispatch_capacity:
            break

        eligible, reason = dispatch_eligible(fm, tsf, inp)
        if eligible:
            # Find first healthy route
            routes_for_tier = inp.routes.for_tier(fm.tier)
            for route_def in routes_for_tier:
                if inp.provider_ok.get(route_def.route_id, False):
                    lifecycle_by_id[fm_id].append(
                        DispatchImplementer(task_id=fm_id, route_id=route_def.route_id)
                    )
                    dispatched += 1
                    break

    # === Attempt actions (no specific sort within category) ===
    attempt_actions: list[Action] = []
    _INTERRUPTIBLE_STATES = (AttemptState.RUNNING, AttemptState.PREFLIGHTING, AttemptState.STALLED)

    for task_id, tsf in inp.states.items():
        # 2026-07-15: never apply attempt lifecycle logic (stall, dead-end
        # BLOCKED, wall-clock cap) to a terminal task — a lingering
        # INTERRUPTED attempt on a COMPLETED task was emitting a
        # COMPLETED->BLOCKED transition every pass (guard rejects it, but
        # it spammed TICK_ERROR).
        if tsf.state in TERMINAL_TASK_STATES:
            continue
        fm_entry = inp.frontmatters.get(task_id)
        fm_for_task = fm_entry[0] if fm_entry is not None else None

        for attempt in tsf.attempts:
            has_receipt = inp.receipts.get(attempt.attempt_id) is not None
            alive = inp.pid_alive.get(attempt.attempt_id, False)

            # Receipt handling: RUNNING/PREFLIGHTING/STALLED with receipt (or
            # an already-EXITED attempt whose task transition is pending) ->
            # EmitAttemptExit.
            if (has_receipt
                    and (attempt.state in _INTERRUPTIBLE_STATES
                         or (attempt.state == AttemptState.EXITED
                             and tsf.state == TaskState.ACTIVE
                             and attempt.role == Role.IMPLEMENTER)
                         # 2026-07-15 live deadlock: a FINISHED frontier
                         # review receipt was never consumed (only
                         # implementer receipts were mapped) — the task sat
                         # in AWAITING_REVIEW forever while the wave guard
                         # correctly refused to relaunch.
                         or (attempt.state == AttemptState.EXITED
                             and tsf.state == TaskState.AWAITING_REVIEW
                             and attempt.role == Role.FRONTIER_REVIEW))):
                # Receipt present and either the attempt record hasn't caught
                # up (wrapper died pre-event, or an event race) or the wrapper
                # emitted EXITED itself and only the TASK transition remains.
                attempt_actions.append(EmitAttemptExit(task_id=task_id, attempt_id=attempt.attempt_id))

            # No receipt, pid dead -> MarkInterrupted
            elif attempt.state in _INTERRUPTIBLE_STATES and not alive:
                attempt_actions.append(MarkInterrupted(task_id=task_id, attempt_id=attempt.attempt_id))

            # P14 2026-07-15 item 6: no receipt, pid alive, but the attempt
            # has been running longer than its wall-clock cap -> interrupt
            # UNCONDITIONALLY, regardless of log activity (bypasses the
            # log-quiet/stall-confirm gate below entirely).
            elif (attempt.state in _INTERRUPTIBLE_STATES and alive
                  and _wall_clock_cap_exceeded(attempt, fm_for_task, inp)):
                attempt_actions.append(InterruptAttempt(task_id=task_id, attempt_id=attempt.attempt_id))

            # Already tier-2-confirmed stalled (ATTEMPT_STALLED already
            # emitted a prior pass) and still no receipt -> interrupt now.
            elif attempt.state == AttemptState.STALLED:
                attempt_actions.append(InterruptAttempt(task_id=task_id, attempt_id=attempt.attempt_id))

            # Stall handling: no receipt, pid alive, log quiet > threshold
            elif attempt.state == AttemptState.RUNNING and alive:
                log_quiet = inp.log_quiet_seconds.get(attempt.attempt_id)
                if log_quiet is not None and log_quiet > inp.cfg.policy.stall_log_quiet_seconds:
                    if inp.stall_confirmed.get(attempt.attempt_id, False):
                        # P14 2026-07-15 item 2: make the confirmed stall
                        # VISIBLE (ATTEMPT_STALLED) before ever interrupting;
                        # InterruptAttempt now only fires once the attempt's
                        # persisted state is actually STALLED (branch above).
                        attempt_actions.append(MarkStalled(task_id=task_id, attempt_id=attempt.attempt_id))
                    else:
                        attempt_actions.append(StallCheck(task_id=task_id, attempt_id=attempt.attempt_id))

            # INTERRUPTED attempt handling
            elif attempt.state == AttemptState.INTERRUPTED and inp.pause_mode == "drain-agents":
                # P15 2026-07-15: draining agents -- no NEW agent process may
                # start (a resume IS a new process). Leave the attempt
                # parked INTERRUPTED; do not transition to BLOCKED either,
                # since that would misrepresent a temporary drain as a
                # genuine dead end. A later pass (run/drain-handoffs)
                # re-evaluates normally.
                pass

            elif attempt.state == AttemptState.INTERRUPTED:
                # Attempts budget left?
                attempts_count = sum(1 for a in tsf.attempts if a.state in TERMINAL_ATTEMPT_STATES
                                    and a.receipt and a.receipt.result != ReceiptResult.LIMIT)
                if attempts_count < inp.cfg.policy.max_attempts_per_task and attempt.session_handle:
                    attempt_actions.append(ResumeAttempt(task_id=task_id, attempt_id=attempt.attempt_id))
                else:
                    # P14 2026-07-15 item 4 (silent-dead-end fix): no resume
                    # handle, or the attempt budget is exhausted -- either
                    # way there is no path forward. The prior code silently
                    # did nothing here ("handled in lifecycle" was never
                    # true: lifecycle only dispatches QUEUED tasks, and
                    # nothing ever requeued this one) leaving the task
                    # ACTIVE forever with zero events. Surface it.
                    blocker = Blocker(
                        type=BlockerType.ENVIRONMENT,
                        unblock_condition="operator: inspect attempts",
                        detail="interrupted attempt has no resume handle or attempts are exhausted",
                    )
                    attempt_actions.append(Transition(task_id=task_id, to=TaskState.BLOCKED,
                                                       notes="interrupted-dead-end", blocker=blocker))

    # === Waves ===
    wave_actions: list[Action] = []
    awaiting_review = [
        (task_id, tsf) for task_id, tsf in inp.states.items()
        if tsf.state == TaskState.AWAITING_REVIEW and tsf.wave_id is None
    ]
    awaiting_review.sort(key=lambda x: x[0])  # Sort by task_id

    if awaiting_review:
        # Batch into waves
        wave_max = inp.cfg.policy.wave_max_diffs
        task_ids_to_batch = [tid for tid, _ in awaiting_review]
        now_timestamp = inp.now.timestamp()

        # Check if we should open a wave
        should_open = len(task_ids_to_batch) >= wave_max
        if not should_open and task_ids_to_batch:
            oldest_task_id = task_ids_to_batch[0]
            oldest_since = inp.states[oldest_task_id].since.timestamp()
            age = now_timestamp - oldest_since
            if age > inp.wave_open_after_seconds:
                should_open = True

        if should_open:
            batched = task_ids_to_batch[:wave_max]
            wave_actions.append(OpenWave(task_ids=batched))

    # Check for LaunchReview for already-open waves.
    # 2026-07-15 live incident: checking only RUNNING re-launched the review
    # every pass while the fresh attempt sat in CREATED/PREFLIGHTING (five
    # duplicate Opus launches in three minutes). ANY non-terminal
    # frontier-review attempt means the wave's review is in flight; a
    # terminal-but-INTERRUPTED one is retried via the normal resume path,
    # so it too must not trigger a duplicate cold launch here.
    for task_id, tsf in inp.states.items():
        if tsf.state == TaskState.AWAITING_REVIEW and tsf.wave_id is not None:
            if inp.pause_mode == "drain-agents":
                # P15 2026-07-15: no new agent process (a review launch IS
                # one) while draining agents; the task stays parked
                # AWAITING_REVIEW until a later pass sees run/drain-handoffs.
                continue
            has_review_in_flight = any(
                a.role == Role.FRONTIER_REVIEW
                and (
                    a.state in (AttemptState.CREATED, AttemptState.PREFLIGHTING,
                                AttemptState.RUNNING, AttemptState.STALLED,
                                AttemptState.EXITED)
                    or (a.state == AttemptState.INTERRUPTED
                        and a.session_handle is not None)
                )
                for a in tsf.attempts
            )
            if not has_review_in_flight:
                wave_actions.append(LaunchReview(wave_id=tsf.wave_id, task_ids=[task_id]))

    # === Spec attention ===
    spec_actions: list[Action] = []

    # Ratchet check
    if not inp.ratchet_already_open and inp.merge_history:
        # Get last N merges where N = max_consecutive_zero_progress_merges
        n = inp.cfg.policy.max_consecutive_zero_progress_merges
        recent_merges = inp.merge_history[:n]
        if len(recent_merges) == n:
            all_zero_review = all(
                units == 0 and source == 'review'
                for _, units, source in recent_merges
            )
            if all_zero_review:
                spec_actions.append(SpecAttention(reason='ratchet', detail=None))

    # Spec health: carve outcomes
    for outcome in inp.carve_outcomes:
        outcome_type = outcome.get('outcome')
        if outcome_type == 'SPEC_GAP':
            spec_actions.append(SpecAttention(reason='carve-outcome', detail=None))
            break

    # Spec health: review rejections
    for area, count in inp.review_rejections_by_area.items():
        if count >= 2:
            spec_actions.append(SpecAttention(reason='rejections', detail=None))
            break

    # Spec health: blocked underspecified
    if inp.blocked_underspecified_count >= 3:
        spec_actions.append(SpecAttention(reason='blocked-underspecified', detail=None))

    # === Combine results in order ===
    actions = []
    for task_id in sorted(lifecycle_by_id.keys()):
        actions.extend(lifecycle_by_id[task_id])
    actions.extend(attempt_actions)
    actions.extend(wave_actions)
    actions.extend(spec_actions)

    return actions


def _wall_clock_cap_exceeded(attempt, fm: Frontmatter | None, inp: ReconcileInput) -> bool:
    """P14 2026-07-15 item 6: per-attempt wall-clock cap = fm.budget.
    max_wall_seconds if set, else inp.attempt_max_wall_seconds."""
    cap = inp.attempt_max_wall_seconds
    if fm is not None and fm.budget is not None and fm.budget.max_wall_seconds:
        cap = fm.budget.max_wall_seconds
    elapsed = (inp.now - attempt.started).total_seconds()
    return elapsed > cap


def dispatch_eligible(fm: Frontmatter, tsf: TaskStateFile, inp: ReconcileInput) -> tuple[bool, str]:
    """(eligible, reason-if-not). Reasons are short fixed strings:
    'paused', 'deps-unmerged:<id>', 'decision-hold:<D-id>', 'wip-cap',
    'attempts-exhausted', 'budget-exhausted', 'lease-unavailable:<name>',
    'no-healthy-route'. First failing check wins (checked in that order)."""

    # 1. paused check (task or project)
    if tsf.paused or inp.project_paused:
        return (False, 'paused')

    # 2. deps check
    task_deps = fm.task_deps()
    for dep_id in task_deps:
        dep_tsf = inp.states.get(dep_id)
        if dep_tsf is None:
            return (False, f'deps-unmerged:{dep_id}')
        if dep_tsf.state != TaskState.COMPLETED:
            # Check if branch is merged
            if dep_id not in inp.merged_branches and dep_tsf.state != TaskState.COMPLETED:
                return (False, f'deps-unmerged:{dep_id}')

    # 3. decision-hold check
    d_deps = fm.decision_deps()
    for d_id in d_deps:
        if d_id in inp.decisions_open:
            return (False, f'decision-hold:{d_id}')

    # 4. wip-cap check
    active_count = sum(
        1 for tid, st in inp.states.items()
        if st.state in (TaskState.ACTIVE, TaskState.AWAITING_REVIEW)
    )
    if active_count >= inp.cfg.policy.max_active_tasks:
        return (False, 'wip-cap')

    # 5. attempts-exhausted check (exclude limit attempts)
    attempts_count = sum(
        1 for a in tsf.attempts
        if a.receipt and a.receipt.result != ReceiptResult.LIMIT
    )
    if attempts_count >= inp.cfg.policy.max_attempts_per_task:
        return (False, 'attempts-exhausted')

    # 6. budget-exhausted check
    if inp.budget_remaining is not None and inp.budget_remaining <= 0.0:
        return (False, 'budget-exhausted')

    # 7. lease-unavailable check
    for mutex_name in fm.effective_mutexes():
        if mutex_name in inp.cfg.mutexes:
            lease_name = inp.cfg.mutexes[mutex_name].lease_name(inp.cfg.project_id)
            if not inp.leases_free.get(lease_name, True):
                return (False, f'lease-unavailable:{lease_name}')

    # 8. no-healthy-route check
    routes_for_tier = inp.routes.for_tier(fm.tier)
    has_healthy = any(inp.provider_ok.get(r.route_id, False) for r in routes_for_tier)
    if not has_healthy:
        return (False, 'no-healthy-route')

    return (True, '')
