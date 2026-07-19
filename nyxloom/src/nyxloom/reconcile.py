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
     then next pass, for a NOT-poisoned INTERRUPTED attempt (unchanged
     since P14): attempts-budget left AND a resume handle -> ResumeAttempt;
     no handle, OR attempts exhausted -> Transition(task, BLOCKED) with a
     typed environment blocker (unblock 'operator: inspect attempts'). P15
     2026-07-15: when inp.pause_mode == "drain-agents", ResumeAttempt (a new
     agent process) is skipped entirely for this pass -- the attempt is left
     parked INTERRUPTED (NOT transitioned to BLOCKED; that would
     misrepresent a temporary drain as a dead end) until a later pass
     observes run or drain-handoffs.
     P34 2026-07-16 (resume-safety re-cut, replaces P26 which was reverted):
     an INTERRUPTED attempt is "poisoned" once inp.resume_failures for it
     is >= policy.max_resume_failures (a resumed session that keeps dying,
     detected by the daemon counting aged attempt.resume-N.log files -- see
     daemon.py). A poisoned attempt NEVER resumes again. It is: parked (no
     action) if a newer attempt already exists (tsf.attempts[-1] differs)
     or the task is not ACTIVE (a review or newer dispatch already
     supersedes it); typed BLOCKED (the same dead-end as above) once the
     distinct-record budget (implementer_record_count -- attempt RECORDS,
     not receipts, so a receiptless poisoned record still counts) reaches
     max_attempts_per_task; parked if a transient dispatch guard
     (paused/budget/lease/no-healthy-route -- see fresh_start_eligible)
     currently refuses; otherwise a fresh DispatchImplementer with NO
     session_handle carried, consuming one unit of that record budget so
     the sequence terminates.
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
   CARVE_OUTCOME payloads -> SpecAttention for SPEC_GAP/DECISION_REQUIRED
   (dedupe via carve_outcome_already_open); review_rejections_by_area
   counts -> SpecAttention when >=2 in one area (dedupe via
   rejections_already_open); blocked_underspecified_count >= 3 in window ->
   SpecAttention (dedupe via blocked_underspecified_already_open). P44
   2026-07-16 (anti-runaway self-correction): these three flags follow the
   SAME "already open in the recent window" convention as
   ratchet_already_open/roadmap_exhausted_open above -- before this fix
   they had no dedup at all, so a PERSISTENT condition (e.g.
   review_rejections_by_area staying >= 2 forever, since that count itself
   never used to decrease either) re-emitted SpecAttention every single
   reconcile pass, storming notifications (the 2026-07-16 prod incident).
   The daemon computes each flag the same way as ratchet_already_open: a
   recent-window scan for a SPEC_ATTENTION event already carrying that
   reason (see daemon.py _spec_attention_recently_emitted, which doubles as
   both the source of these flags now AND a belt-and-braces backstop at
   emission time).
8. Actions NEVER embed prose from handoff bodies (payload injection rule) —
   only ids, enum values, and short fixed strings.
9. CARVE TRIGGER (P16 2026-07-15, v2 §8 stop policy): count admissible ready
   tasks -- frontmatters whose statefile is in {CARVED, QUEUED,
   NEEDS_DECISION} AND that are not currently decision-held (an open D-dep
   in inp.decisions_open excludes a task from the count regardless of its
   nominal state, so a QUEUED task about to be moved to NEEDS_DECISION this
   same pass is never counted as "ready"). If that count is <
   policy.carve_ahead_target AND an active milestone admits work (proxy:
   at least one non-terminal task exists, OR policy.carve_ahead_target > 0
   AND inp.roadmap_exhausted_open is False) AND no carver attempt is
   already in flight (any non-terminal task carrying an Attempt with
   role CARVER -- a carve slot, mirroring the single-wave-review-in-flight
   pattern in item 5 above) AND budget allows (inp.budget_remaining is
   None or > 0, same rule as dispatch_eligible) AND a healthy 'frontier-
   review' tier route exists (inp.provider_ok, item 3's own no-healthy-
   route rule applied to the carver's own tier -- a project with no
   review/carve infrastructure configured must never spuriously dispatch,
   the same reasoning dispatch_eligible already applies to ordinary
   implementer dispatch) -> emit CarveDispatch(project=cfg.project_id). At
   most one per pass (the in-flight check makes this self-limiting pass
   over pass too). Appended LAST in the returned action list (after
   SpecAttention). P45 2026-07-19: the in-flight and route-health guards are
   now computed ONCE and shared with item 12's READY_TO_CARVE handler below
   -- see that item's note for why AT MOST ONE CarveDispatch is ever planned
   in a pass across BOTH triggers (the single strategic carver is the sole
   carve authority).
10. REJECT LOOP (self-correct package, 2026-07-16; exhausted-budget half
    closed by P45 2026-07-19): REVIEW_REJECTED with attempts remaining (same
    accounting as dispatch_eligible check 5: receipted attempts excluding
    'limit' results, < policy.max_attempts_per_task) -> Transition to
    QUEUED (re-work). Self-limiting like item 2's CARVED->QUEUED (the
    transition moves the task out of REVIEW_REJECTED, so it does not refire
    next pass). Attempts exhausted: the original handoff wanted
    REVIEW_REJECTED->BLOCKED (mirroring item 4's INTERRUPTED-exhausted
    typed-blocker path), but that edge is absent from types.py's
    TASK_TRANSITIONS[REVIEW_REJECTED] (FROZEN CORE, out of scope) --
    planning it would raise TransitionError when the daemon executes it.
    REVIEW_REJECTED->READY_TO_CARVE IS legal in that same frozen table, so
    P45 routes the exhausted case there instead of leaving it a silent
    no-op: Transition to READY_TO_CARVE (self-limiting, same reasoning as
    the attempts-remaining branch). See item 12 below for what happens to a
    task once it is in READY_TO_CARVE.
11. POST-MERGE VALIDATION (nyxloom-post-merge-validation package, 2026-07-17;
    fixes the "TaskState.COMPLETED is unreachable" gap pinned by
    tests/test_invariants.py's now-removed MERGED/VALIDATING xfails):
    MERGED task -> Transition(VALIDATING) unconditionally (pure bookkeeping,
    self-limiting like item 2's CARVED->QUEUED: the state moves off MERGED
    so this does not refire). VALIDATING task -> RunPostMergeGate(task_id)
    every pass until the daemon's execution of that action transitions the
    task onward (COMPLETED on a passing gate, BLOCKED with a typed CONTRACT
    blocker on a failing/erroring/timed-out one) -- see daemon.py's
    _run_post_merge_gate for the actual gate selection, {worktree}
    substitution, and subprocess execution (this module stays pure; it only
    ever emits the trigger action, exactly like item 9's CarveDispatch).
    types.py's TASK_TRANSITIONS[BLOCKED] already permits BLOCKED->VALIDATING
    (frozen, pre-existing edge) -- an operator who fixes the underlying
    cause manually re-queues a blocked post-merge task back to VALIDATING to
    retry, the same "operator must resolve BLOCKED by hand" convention
    documented in render.py's STATE_LEGEND (no new code needed for that
    recovery path).
12. READY_TO_CARVE handler (P45 2026-07-19, closes the dead-end pinned by
    tests/test_invariants.py's test_no_dead_end_ready_to_carve -- a task in
    READY_TO_CARVE had NO handler at all before this package, reached only
    via item 10's exhausted-budget re-route): when no carver attempt is
    already in flight (the EXACT SAME predicate item 9 uses, computed once
    and shared -- not a second, independently-drifting version) and a
    healthy 'frontier-review' route exists (ditto), the single lowest
    (sorted) task_id currently in READY_TO_CARVE gets BOTH a
    CarveDispatch(project=cfg.project_id, task_id=fm_id) -- re-dispatched
    through the SAME existing carve mechanism as item 9 (daemon.
    _execute_carve_dispatch); item_id is left None, so the untargeted
    carve-packet path fires, which already embeds recent REVIEW_RECORDED
    follow-ups (this rejection's own context, for free) -- AND a
    Transition(task_id=fm_id, to=SUPERSEDED) in the SAME pass, self-limiting
    like every other bookkeeping transition in this module (the task moves
    off READY_TO_CARVE so it does not re-trigger a second CarveDispatch once
    the carve slot frees up on a later pass). AT MOST ONE CarveDispatch is
    ever planned in a single pass, shared between this handler and item 9 --
    whichever fires first (this item runs earlier in the function body, so
    it wins ties) consumes the single carve slot for the pass; the operator
    was explicit that the single strategic carver remains the sole carve
    authority, so a second, independent dispatch path is never created here.
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
    reason: str | None = None       # 'ratchet'|'carve-outcome'|'rejections'|'blocked-underspecified'|
                                     # 'headroom-low'|'roadmap-exhausted' (P16 2026-07-15, daemon-emitted)
    detail: str | None = None


@dataclass
class CarveDispatch(Action):
    """P16 2026-07-15 (module contract item 9): the carve-automation
    trigger. task_id is unused for item 9's own untargeted headroom-refill
    trigger (inherited from Action, left None there); `project` names the
    project this carve targets -- the daemon already knows it too (run_
    pass's own per-project loop), but carrying it here keeps the action
    self-describing for tests/logs, matching ProviderPause's route_id
    style. P45 2026-07-19: item 12's READY_TO_CARVE handler sets task_id to
    the triggering task's own id (fm_id) so the action is task-attributable
    in tests/logs; _execute_carve_dispatch itself never reads task_id (the
    carve still mints its own fresh synthetic carve-<project>-<seq> task,
    same as item 9) -- it is informational only, not a second execution
    path.

    item_id (P41 2026-07-16): None for the untargeted headroom-refill
    trigger below (module contract item 9, unchanged). daemon.
    dispatch_targeted_carve builds this action directly (outside
    plan_project) with item_id set, so _execute_carve_dispatch seeds the
    carver with exactly that one backlog item's intake brief instead of the
    general review/backlog/roadmap source list."""
    project: str | None = None
    item_id: str | None = None


@dataclass
class RunPostMergeGate(Action):
    """Post-merge validation trigger (module contract item 11, 2026-07-17):
    the ONLY action ever planned for a VALIDATING task. Carries nothing but
    task_id -- which gate to run, the {worktree} substitution, and the
    subprocess execution itself are the daemon's job (this module stays
    pure, no subprocess/filesystem); see daemon.py's _run_post_merge_gate.
    """


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
    # P34 2026-07-16 (resume-safety re-cut): attempt_id -> count of aged
    # (older than policy.resume_progress_grace_seconds) attempt.resume-N.log
    # files for that attempt, computed by the daemon from disk. An
    # INTERRUPTED attempt is "poisoned" once this reaches
    # policy.max_resume_failures. Missing entries default to 0 (not
    # poisoned) so existing tests that omit this still build.
    resume_failures: dict[str, int] = field(default_factory=dict)
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
    # P16 2026-07-15: True once a carver outcome ROADMAP_EXHAUSTED has been
    # observed and not yet superseded (the daemon scans recent SPEC_ATTENTION
    # events for reason == 'roadmap-exhausted', mirroring
    # ratchet_already_open's convention) -- reconcile.py stays pure, so this
    # arrives as a precomputed bool rather than being derived from events
    # here. See module contract item 9 (carve trigger).
    roadmap_exhausted_open: bool = False
    # P44 2026-07-16 (anti-runaway self-correction): "already open in the
    # recent window" dedup flags for the three SpecAttention branches that,
    # unlike ratchet_already_open/roadmap_exhausted_open above, previously
    # had none -- see module contract item 7. Defaults False so every
    # pre-existing test that omits them keeps today's (now-fixed) semantics.
    rejections_already_open: bool = False
    carve_outcome_already_open: bool = False
    blocked_underspecified_already_open: bool = False


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

        # SELF-CORRECT 2026-07-16 (bug 2 of the review-verdict + reject-loop
        # package): REVIEW_REJECTED had NO handler at all. The state machine
        # permits REVIEW_REJECTED->QUEUED (types.py TASK_TRANSITIONS) and the
        # reject CLI/UI imply re-work, but nothing here ever planned it, so a
        # rejected task STRANDED forever (required a manual re-queue by an
        # operator). Mirrors the attempts-budget accounting dispatch_eligible
        # already uses below (exclude LIMIT receipts, same formula as the
        # daemon's own ERROR-path count) -- attempts remaining -> re-queue
        # for another implementer pass; this is self-limiting the same way
        # CARVED->QUEUED above is: once applied, tsf.state is QUEUED and this
        # branch no longer matches on the next pass, so it fires once per
        # rejection, not every tick.
        #
        # P45 2026-07-19 (closes the KNOWN GAP the pre-P45 code left here):
        # the exhausted-budget half of this handoff's original contract
        # asked for REVIEW_REJECTED -> BLOCKED with a typed blocker
        # (mirroring the INTERRUPTED-exhausted typed-blocker path elsewhere
        # in this module). That specific transition is NOT legal:
        # types.py's TASK_TRANSITIONS[REVIEW_REJECTED] is {QUEUED,
        # READY_TO_CARVE, NEEDS_DECISION, SUPERSEDED, CANCELLED} -- BLOCKED
        # is absent -- so planning it would raise TransitionError the moment
        # the daemon executed it (check_task_transition runs for BOTH
        # TASK_TRANSITIONED and TASK_BLOCKED events -- storage.apply_event,
        # no bypass; verified empirically), and types.py is FROZEN CORE /
        # out of scope for this package. READY_TO_CARVE IS a legal edge in
        # that same frozen table, and the two absence bugs turned out to be
        # the same gap: routing the exhausted case there, and giving
        # READY_TO_CARVE a real handler (module contract item 12, below)
        # that re-dispatches the SAME single strategic carver, closes both
        # at once with zero new dispatch machinery. Self-limiting the same
        # way the attempts-remaining branch above is.
        if tsf.state == TaskState.REVIEW_REJECTED:
            rejected_attempts_count = sum(
                1 for a in tsf.attempts
                if a.receipt is not None and a.receipt.result != ReceiptResult.LIMIT
            )
            if rejected_attempts_count < inp.cfg.policy.max_attempts_per_task:
                task_actions.append(Transition(
                    task_id=fm_id, to=TaskState.QUEUED,
                    notes="review rejected -- re-queued for re-work (attempt budget remains)",
                ))
            else:
                # Attempts exhausted: no more re-work is possible with this
                # task's own budget -- route it to READY_TO_CARVE so item
                # 12's handler (below) re-dispatches it to the single
                # strategic carver for a fresh, re-scoped package instead of
                # stranding it forever.
                task_actions.append(Transition(
                    task_id=fm_id, to=TaskState.READY_TO_CARVE,
                    notes="review rejected -- attempt budget exhausted; routed for re-carve",
                ))

        # POST-MERGE VALIDATION (module contract item 11): MERGED ->
        # VALIDATING is pure bookkeeping (self-limiting, same pattern as
        # CARVED->QUEUED above); VALIDATING itself just re-emits the trigger
        # every pass until the daemon's execution of RunPostMergeGate
        # transitions the task onward to COMPLETED or BLOCKED.
        if tsf.state == TaskState.MERGED:
            task_actions.append(Transition(
                task_id=fm_id, to=TaskState.VALIDATING,
                notes="post-merge validation started",
            ))
        elif tsf.state == TaskState.VALIDATING:
            task_actions.append(RunPostMergeGate(task_id=fm_id))

    # Shared single-carve-authority guard (module contract items 9 & 12,
    # P45 2026-07-19): computed ONCE, reused verbatim by BOTH item 12's
    # READY_TO_CARVE handler (immediately below) and item 9's untargeted
    # headroom-refill trigger (further down, past dispatch/attempts/waves/
    # spec) -- never two independently-drifting copies of either check.
    carve_in_flight = any(
        tsf.state not in TERMINAL_TASK_STATES
        and any(a.role is Role.CARVER for a in tsf.attempts)
        for tsf in inp.states.values()
    )
    frontier_routes = inp.routes.for_tier("frontier-review")
    frontier_route_available = any(
        inp.provider_ok.get(r.route_id, False) for r in frontier_routes
    )
    # True once EITHER trigger has planned the pass's single CarveDispatch
    # -- the operator's explicit ask: the single strategic carver remains
    # the sole carve authority, so at most one CarveDispatch is ever planned
    # in a pass no matter which of the two triggers wants one.
    carve_dispatch_planned = False

    # 12. READY_TO_CARVE handler (P45 2026-07-19): see module contract item
    # 12 docstring above for the full rationale. Sorted task-id order for
    # determinism (mirrors item 3's dispatch-capacity loop below) -- if
    # multiple tasks are simultaneously READY_TO_CARVE, only the lowest
    # task_id gets this pass's single carve slot; the rest stay in
    # READY_TO_CARVE, picked up on a later pass.
    if not carve_in_flight and frontier_route_available:
        ready_to_carve_ids = sorted(
            task_id for task_id, tsf in inp.states.items()
            if tsf.state == TaskState.READY_TO_CARVE
        )
        if ready_to_carve_ids:
            chosen_id = ready_to_carve_ids[0]
            chosen_actions = lifecycle_by_id.setdefault(chosen_id, [])
            chosen_actions.append(
                CarveDispatch(project=inp.cfg.project_id, task_id=chosen_id)
            )
            chosen_actions.append(Transition(
                task_id=chosen_id, to=TaskState.SUPERSEDED,
                notes="ready-to-carve: dispatched to the strategic carver",
            ))
            carve_dispatch_planned = True

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
                             and attempt.role == Role.FRONTIER_REVIEW)
                         # P32 2026-07-16: a carver's EXITED attempt whose
                         # live pass was missed (daemon restart landing on
                         # the exit) left the synthetic carve task ACTIVE
                         # forever, permanently eating a wip slot — the
                         # daemon's _consume_carve_exit handler already
                         # retires it to SUPERSEDED, but only ever ran off
                         # this same re-scan, which didn't cover CARVER.
                         or (attempt.state == AttemptState.EXITED
                             and tsf.state == TaskState.ACTIVE
                             and attempt.role == Role.CARVER))):
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
                # start (a resume IS a new process, and so is the P34
                # fresh-start below). Leave the attempt parked INTERRUPTED;
                # do not transition to BLOCKED either, since that would
                # misrepresent a temporary drain as a genuine dead end. A
                # later pass (run/drain-handoffs) re-evaluates normally.
                pass

            elif attempt.state == AttemptState.INTERRUPTED and tsf.state != TaskState.BLOCKED:
                # (2026-07-15) the `!= BLOCKED` guard: once a dead-end has
                # already blocked the task, don't re-emit BLOCKED->BLOCKED
                # every pass (TICK_ERROR spam). A BLOCKED task leaves via the
                # QUEUED re-dispatch path, not here.
                #
                # P34 2026-07-16 (resume-safety re-cut, decision table in
                # nyxloom-trove/handoffs/nyxloom-P34-resume-safety-guarded.md):
                # a "poisoned" attempt (resume_failures at/over
                # max_resume_failures) never resumes again -- it is either
                # parked, typed-BLOCKED on a spent record budget, or
                # fresh-started through the ordinary dispatch guards.
                poisoned = (inp.resume_failures.get(attempt.attempt_id, 0)
                            >= inp.cfg.policy.max_resume_failures)
                if not poisoned:
                    # unchanged (O2): today's ResumeAttempt-or-BLOCKED branch.
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
                else:
                    is_latest = bool(tsf.attempts) and tsf.attempts[-1].attempt_id == attempt.attempt_id
                    if not is_latest:
                        # park -- a newer attempt already supersedes this one
                        pass
                    elif tsf.state != TaskState.ACTIVE:
                        # park -- e.g. AWAITING_REVIEW: a review is in flight
                        pass
                    elif implementer_record_count(tsf) >= inp.cfg.policy.max_attempts_per_task:
                        # distinct-record budget gone -- the same typed
                        # dead-end as the non-poisoned branch above (O6).
                        blocker = Blocker(
                            type=BlockerType.ENVIRONMENT,
                            unblock_condition="operator: inspect attempts",
                            detail="resume-poisoned attempt has no attempts budget remaining",
                        )
                        attempt_actions.append(Transition(task_id=task_id, to=TaskState.BLOCKED,
                                                           notes="interrupted-dead-end", blocker=blocker))
                    elif fm_for_task is None:
                        # park -- no frontmatter to dispatch from; retry next pass
                        pass
                    else:
                        eligible, _reason = fresh_start_eligible(fm_for_task, tsf, inp)
                        if not eligible:
                            # park -- a transient guard (paused/budget/lease/
                            # route) refused; retry next pass.
                            pass
                        else:
                            # fresh DispatchImplementer, no session_handle
                            # carried -- mirrors the lifecycle dispatch's
                            # "first healthy route" selection (item 3 above).
                            routes_for_tier = inp.routes.for_tier(fm_for_task.tier)
                            for route_def in routes_for_tier:
                                if inp.provider_ok.get(route_def.route_id, False):
                                    attempt_actions.append(
                                        DispatchImplementer(task_id=task_id, route_id=route_def.route_id)
                                    )
                                    break

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
    #
    # 2026-07-17 fix (stale-wave_id strand, second review cycle never
    # relaunches): the check above previously scanned ALL of tsf.attempts
    # for ANY FRONTIER_REVIEW attempt in these states, including terminal
    # EXITED -- with no scoping to "is this attempt still current". Once a
    # task's first review attempt reaches EXITED (approved OR rejected),
    # it stayed in tsf.attempts forever, so has_review_in_flight was
    # permanently True from that point on. Combined with the reject-loop
    # (REVIEW_REJECTED -> QUEUED -> a fresh implementer -> AWAITING_REVIEW
    # a SECOND time), that meant a second review could never be launched --
    # the task silently stranded AWAITING_REVIEW forever.
    #
    # Scoping by tsf.wave_id doesn't help on its own: tsf.wave_id is set
    # once by OpenWave/WAVE_OPENED and never reset (REVIEW_RECORDED is a
    # true audit-only no-op in storage.apply_event -- see
    # test_invariants.py's test_known_ignored_event_types_are_true_noops --
    # so it cannot clear it, and every review attempt for this task is
    # dispatched with that SAME wave_id forever). The actual discriminator
    # is RECENCY: only the task's LATEST attempt can meaningfully be "the
    # review in flight" -- a stale EXITED review from a prior cycle that
    # has since been superseded by a fresh implementer attempt (the
    # reject-loop's re-work) is provably no longer in flight, because
    # something newer already ran after it. This mirrors the is_latest
    # check already used above (interrupted-poisoned handling).
    for task_id, tsf in inp.states.items():
        if tsf.state == TaskState.AWAITING_REVIEW and tsf.wave_id is not None:
            if inp.pause_mode == "drain-agents":
                # P15 2026-07-15: no new agent process (a review launch IS
                # one) while draining agents; the task stays parked
                # AWAITING_REVIEW until a later pass sees run/drain-handoffs.
                continue
            latest = tsf.attempts[-1] if tsf.attempts else None
            has_review_in_flight = (
                latest is not None
                and latest.role == Role.FRONTIER_REVIEW
                and (
                    latest.state in (AttemptState.CREATED, AttemptState.PREFLIGHTING,
                                      AttemptState.RUNNING, AttemptState.STALLED,
                                      AttemptState.EXITED)
                    or (latest.state == AttemptState.INTERRUPTED
                        and latest.session_handle is not None)
                )
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

    # Spec health: carve outcomes (P44 2026-07-16: dedup via
    # carve_outcome_already_open -- see module contract item 7)
    if not inp.carve_outcome_already_open:
        for outcome in inp.carve_outcomes:
            outcome_type = outcome.get('outcome')
            if outcome_type == 'SPEC_GAP':
                spec_actions.append(SpecAttention(reason='carve-outcome', detail=None))
                break

    # Spec health: review rejections (P44 2026-07-16: dedup via
    # rejections_already_open -- this was the actual notification-storm
    # root cause: review_rejections_by_area never decreased AND this
    # branch never deduped, so 2 rejections re-emitted every pass forever)
    if not inp.rejections_already_open:
        for area, count in inp.review_rejections_by_area.items():
            if count >= 2:
                spec_actions.append(SpecAttention(reason='rejections', detail=None))
                break

    # Spec health: blocked underspecified (P44 2026-07-16: dedup via
    # blocked_underspecified_already_open -- see module contract item 7)
    if not inp.blocked_underspecified_already_open and inp.blocked_underspecified_count >= 3:
        spec_actions.append(SpecAttention(reason='blocked-underspecified', detail=None))

    # === Carve dispatch (P16 2026-07-15, module contract item 9) ===
    # carve_in_flight / frontier_route_available computed once, above
    # (shared with item 12's READY_TO_CARVE handler); carve_dispatch_planned
    # is True if that handler already used this pass's single carve slot --
    # P45 2026-07-19: at most one CarveDispatch is ever planned in a pass,
    # shared across both triggers (the single strategic carver is the sole
    # carve authority).
    carve_actions: list[Action] = []
    if not carve_in_flight and not carve_dispatch_planned:
        ready_states = (TaskState.CARVED, TaskState.QUEUED, TaskState.NEEDS_DECISION)
        ready_count = 0
        for fm_id, (fm, _handoff_path) in inp.frontmatters.items():
            tsf = inp.states.get(fm_id)
            if tsf is None or tsf.state not in ready_states:
                continue
            if any(d in inp.decisions_open for d in fm.decision_deps()):
                continue  # decision-held -- not admissible ready work
            ready_count += 1

        has_nonterminal_task = any(
            tsf.state not in TERMINAL_TASK_STATES for tsf in inp.states.values()
        )
        milestone_admits_work = has_nonterminal_task or (
            inp.cfg.policy.carve_ahead_target > 0 and not inp.roadmap_exhausted_open
        )
        budget_allows = inp.budget_remaining is None or inp.budget_remaining > 0

        if (ready_count < inp.cfg.policy.carve_ahead_target
                and milestone_admits_work
                and budget_allows
                and frontier_route_available):
            carve_actions.append(CarveDispatch(project=inp.cfg.project_id))
            carve_dispatch_planned = True

    # === Combine results in order ===
    actions = []
    for task_id in sorted(lifecycle_by_id.keys()):
        actions.extend(lifecycle_by_id[task_id])
    actions.extend(attempt_actions)
    actions.extend(wave_actions)
    actions.extend(spec_actions)
    actions.extend(carve_actions)

    return actions


def _wall_clock_cap_exceeded(attempt, fm: Frontmatter | None, inp: ReconcileInput) -> bool:
    """P14 2026-07-15 item 6: per-attempt wall-clock cap = fm.budget.
    max_wall_seconds if set, else inp.attempt_max_wall_seconds."""
    cap = inp.attempt_max_wall_seconds
    if fm is not None and fm.budget is not None and fm.budget.max_wall_seconds:
        cap = fm.budget.max_wall_seconds
    elapsed = (inp.now - attempt.started).total_seconds()
    return elapsed > cap


def _check_paused(tsf: TaskStateFile, inp: ReconcileInput) -> tuple[bool, str]:
    if tsf.paused or inp.project_paused:
        return (False, 'paused')
    return (True, '')


def _check_budget(inp: ReconcileInput) -> tuple[bool, str]:
    if inp.budget_remaining is not None and inp.budget_remaining <= 0.0:
        return (False, 'budget-exhausted')
    return (True, '')


def _check_lease(fm: Frontmatter, inp: ReconcileInput) -> tuple[bool, str]:
    for mutex_name in fm.effective_mutexes():
        if mutex_name in inp.cfg.mutexes:
            lease_name = inp.cfg.mutexes[mutex_name].lease_name(inp.cfg.project_id)
            if not inp.leases_free.get(lease_name, True):
                return (False, f'lease-unavailable:{lease_name}')
    return (True, '')


def _check_healthy_route(fm: Frontmatter, inp: ReconcileInput) -> tuple[bool, str]:
    routes_for_tier = inp.routes.for_tier(fm.tier)
    has_healthy = any(inp.provider_ok.get(r.route_id, False) for r in routes_for_tier)
    if not has_healthy:
        return (False, 'no-healthy-route')
    return (True, '')


def dispatch_eligible(fm: Frontmatter, tsf: TaskStateFile, inp: ReconcileInput) -> tuple[bool, str]:
    """(eligible, reason-if-not). Reasons are short fixed strings:
    'paused', 'deps-unmerged:<id>', 'decision-hold:<D-id>', 'wip-cap',
    'attempts-exhausted', 'budget-exhausted', 'lease-unavailable:<name>',
    'no-healthy-route'. First failing check wins (checked in that order)."""

    # 1. paused check (task or project)
    ok, reason = _check_paused(tsf, inp)
    if not ok:
        return (ok, reason)

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
    ok, reason = _check_budget(inp)
    if not ok:
        return (ok, reason)

    # 7. lease-unavailable check
    ok, reason = _check_lease(fm, inp)
    if not ok:
        return (ok, reason)

    # 8. no-healthy-route check
    ok, reason = _check_healthy_route(fm, inp)
    if not ok:
        return (ok, reason)

    return (True, '')


def fresh_start_eligible(fm: Frontmatter, tsf: TaskStateFile, inp: ReconcileInput) -> tuple[bool, str]:
    """(eligible, reason-if-not) for re-cutting a poisoned INTERRUPTED
    attempt as a fresh DispatchImplementer (P34 2026-07-16). Reuses
    dispatch_eligible's checks 1 (paused), 6 (budget), 7 (lease) and 8
    (healthy route) -- these are the transient conditions the decision
    table's 'park, retry next pass' row exists for. Deliberately EXCLUDES
    check 4 (wip-cap: the task's own ACTIVE state already trips it) and
    check 5 (attempts-exhausted: replaced by the caller's distinct-record
    budget, which counts receiptless records this check cannot see)."""
    ok, reason = _check_paused(tsf, inp)
    if not ok:
        return (ok, reason)
    ok, reason = _check_budget(inp)
    if not ok:
        return (ok, reason)
    ok, reason = _check_lease(fm, inp)
    if not ok:
        return (ok, reason)
    ok, reason = _check_healthy_route(fm, inp)
    if not ok:
        return (ok, reason)
    return (True, '')


def implementer_record_count(tsf: TaskStateFile) -> int:
    """Distinct-record budget (P34 2026-07-16): count of role==IMPLEMENTER
    attempt RECORDS in tsf.attempts, unlike the receipt-based
    attempts_count above -- a poisoned INTERRUPTED record has no receipt
    but still consumes one fresh-start's worth of budget, so this must
    count it (else the fresh-start sequence never terminates, O5)."""
    return sum(1 for a in tsf.attempts if a.role == Role.IMPLEMENTER)
