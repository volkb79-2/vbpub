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
4. RUNNING ATTEMPTS:
   - receipt file present (receipts[attempt_id] not None) -> EmitAttemptExit
     (daemon appends ATTEMPT_EXITED with the receipt/usage merged in and
     transitions the task: receipt.result done -> AWAITING_REVIEW; blocked ->
     BLOCKED (blocker type contract, unblock 'triage BLOCKED reason');
     limit -> QUEUED (attempt does not count toward max_attempts; also
     ProviderPause(route_id)); error -> QUEUED if attempts remain else
     BLOCKED (environment)).
   - no receipt, pid dead -> MarkInterrupted (daemon emits ATTEMPT_INTERRUPTED)
     then next pass ResumeAttempt (INTERRUPTED attempts with attempts-budget
     left and a resume handle -> ResumeAttempt; without handle -> fresh
     DispatchImplementer counting a new attempt).
   - no receipt, pid alive, log quiet > policy.stall_log_quiet_seconds ->
     StallCheck (tier 2 evidence gathering is the daemon's job; the planner
     only flags QUIET). If stall_confirmed[attempt_id] is True ->
     InterruptAttempt (daemon kills pgid; wrapper writes interrupted receipt).
5. REVIEW WAVES (SPEC §7): tasks AWAITING_REVIEW with wave_id None, batch in
   sorted order into OpenWave(task_ids up to policy.wave_max_diffs); a wave
   opens when >= wave_max_diffs are waiting OR the oldest has waited >
   wave_open_after_seconds (input field). For an open wave whose review
   attempt is not yet running -> LaunchReview(wave_id, task_ids).
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
    Frontmatter, TaskState, TaskStateFile, AttemptState,
    ReceiptResult, Role, TERMINAL_ATTEMPT_STATES
)


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

    for task_id, tsf in inp.states.items():
        for attempt in tsf.attempts:
            # Receipt handling: RUNNING with receipt -> EmitAttemptExit
            if attempt.state == AttemptState.RUNNING and inp.receipts.get(attempt.attempt_id) is not None:
                attempt_actions.append(EmitAttemptExit(task_id=task_id, attempt_id=attempt.attempt_id))

            # No receipt, pid dead -> MarkInterrupted
            elif attempt.state == AttemptState.RUNNING and not inp.pid_alive.get(attempt.attempt_id, False):
                if inp.receipts.get(attempt.attempt_id) is None:
                    attempt_actions.append(MarkInterrupted(task_id=task_id, attempt_id=attempt.attempt_id))

            # Stall handling: no receipt, pid alive, log quiet > threshold
            elif attempt.state == AttemptState.RUNNING and inp.pid_alive.get(attempt.attempt_id, False):
                if inp.receipts.get(attempt.attempt_id) is None:
                    log_quiet = inp.log_quiet_seconds.get(attempt.attempt_id)
                    if log_quiet is not None and log_quiet > inp.cfg.policy.stall_log_quiet_seconds:
                        if inp.stall_confirmed.get(attempt.attempt_id, False):
                            attempt_actions.append(InterruptAttempt(task_id=task_id, attempt_id=attempt.attempt_id))
                        else:
                            attempt_actions.append(StallCheck(task_id=task_id, attempt_id=attempt.attempt_id))

            # INTERRUPTED attempt handling
            elif attempt.state == AttemptState.INTERRUPTED:
                # Attempts budget left?
                attempts_count = sum(1 for a in tsf.attempts if a.state in TERMINAL_ATTEMPT_STATES
                                    and a.receipt and a.receipt.result != ReceiptResult.LIMIT)
                if attempts_count < inp.cfg.policy.max_attempts_per_task:
                    if attempt.session_handle:
                        attempt_actions.append(ResumeAttempt(task_id=task_id, attempt_id=attempt.attempt_id))
                    else:
                        # Fresh dispatch (handled in lifecycle)
                        pass

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

    # Check for LaunchReview for already-open waves
    for task_id, tsf in inp.states.items():
        if tsf.state == TaskState.AWAITING_REVIEW and tsf.wave_id is not None:
            # Check if there's a FRONTIER_REVIEW attempt RUNNING
            has_running_review = any(
                a.state == AttemptState.RUNNING and a.role == Role.FRONTIER_REVIEW
                for a in tsf.attempts
            )
            if not has_running_review:
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
