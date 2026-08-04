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
     B24 2026-07-23 (D-R17, transient-failure backoff-resume): an
     INTERRUPTED attempt whose receipt.result is TRANSIENT (a provider
     throttle/outage -- 502/429/ResourceExhausted/idle-timeout/worker
     request-limit, see adapters.classify_log_tail) is gated by
     inp.transient_backoff_ready (attempt_id -> bool, computed by the
     daemon from disk against daemon.py's TRANSIENT_BACKOFF_SCHEDULE;
     missing entries default True, so an ordinary non-transient INTERRUPTED
     attempt is unaffected): not-ready parks BOTH the ResumeAttempt AND the
     dead-end-BLOCKED branch above (a backoff wait is neither "resume now"
     nor "no path forward"); ready resumes normally. Once the daemon's own
     resume count for that attempt reaches MAX_TRANSIENT_RESUMES,
     transient_backoff_ready is False FOREVER for it -- the daemon's
     Daemon._transient_escalate (run before this snapshot is even built,
     see run_pass) is what then actually moves the task (ACTIVE -> QUEUED
     + provider-pause the throttled route with state='throttled'), so the
     ordinary QUEUED-task dispatch loop (item 3 above) re-routes to the
     next healthy route in the tier; the old attempt itself is left
     permanently INTERRUPTED and inert, the same "stale record" shape a
     resume-poisoned attempt already has above. attempts_used excludes a
     TRANSIENT receipt from the budget exactly like LIMIT (D-B24-6).
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
    (sorted) task_id currently in READY_TO_CARVE gets a
    CarveDispatch(project=cfg.project_id, task_id=fm_id) -- re-dispatched
    through the SAME existing carve mechanism as item 9 (daemon.
    _execute_carve_dispatch); item_id is left None.
    B7 2026-07-20 (P75, critique A10/M20): this handler plans ONLY the
    CarveDispatch. The origin task's SUPERSEDED transition is NO LONGER a
    separate action planned here -- the daemon emits it atomically, and only
    AFTER the re-scope carve launches, folding the origin's handoff/verdict/
    input_revision-drift into the carve packet (task_id=fm_id now DRIVES that
    re-scope path, not merely informational). The pre-B7 design planned the
    supersede as an independent same-pass action; under per-action isolation
    (A10/M12) it fired even when the CarveDispatch early-returned (admission
    refused / no route), silently dropping the re-carve -- the M20 gap.
    Coupling the supersede to the launch is the only real atomicity. The task
    still moves off READY_TO_CARVE the same pass on a successful launch, so it
    does not re-trigger a second CarveDispatch; on a failed launch it stays,
    correctly, for the next pass to retry. AT MOST ONE CarveDispatch is
    ever planned in a single pass, shared between this handler and item 9 --
    whichever fires first (this item runs earlier in the function body, so
    it wins ties) consumes the single carve slot for the pass; the operator
    was explicit that the single strategic carver remains the sole carve
    authority, so a second, independent dispatch path is never created here.
13. GUARDED-AUTOMATIC MERGE (P48 2026-07-19): a MERGE_READY task ->
    AutoMergeTask(task_id) only when policy.merge_mode ==
    'guarded-automatic' AND not project_paused. Reaching MERGE_READY at all
    already structurally means a REVIEW_INDEPENDENT attempt's verdict was
    'approved' (the only writer of that transition, in daemon.py's review-
    consumption branch) -- so no separate verdict re-check belongs here.
    project_paused is deliberately consulted (unlike dispatch_eligible's
    narrower reasons for it): if the runaway watchdog or an operator has
    paused the project for ANY reason, unattended merging must not proceed
    -- that is exactly the scenario the pause exists to stop. Self-limiting
    like items 9-12: the daemon's execution of AutoMergeTask (a REAL `git
    merge --no-ff` in a disposable scratch worktree, for genuine 3-way
    conflict detection -- never the surgical commit-tree technique an
    operator uses by hand, which trades away conflict detection for
    monorepo-dirty-state safety, acceptable only under human supervision)
    moves the task off MERGE_READY (to MERGED on a clean merge, or to a
    NEEDS_OPERATOR escalation leaving it at MERGE_READY on a real conflict)
    so this does not refire for the same task next pass. merge_mode
    defaults to 'manual', under which this item never fires at all --
    existing behavior (an operator's own `nyxloom merge` after their own
    real git merge) is completely unchanged.
14. CARVE TRIGGERS RESPECT project_paused (P52 2026-07-19, live incident):
    neither item 9's untargeted headroom-refill trigger nor item 12's
    READY_TO_CARVE handler ever consulted project_paused -- a "paused"
    project (drain-handoffs OR drain-agents) still got a brand-new carve
    dispatch fired against it every pass its ready_count sat below
    carve_ahead_target, completely bypassing the pause. 4 unauthorized
    carve dispatches fired against a paused project (dstdns) in ~15
    minutes before this was caught live -- dispatch_eligible (regular
    implementer dispatch) and item 13 (guarded-automatic merge) already
    both consulted project_paused; carving was the one dispatch path in
    this module that never did. Fixed by adding `not inp.project_paused`
    to the shared guard both items already reuse (carve_in_flight /
    frontier_route_available / budget_allows), so both triggers close in
    one place rather than two independently-drifting copies.
15. TEST-HEALTH CARVE TRIGGER (D-065, B63 2026-07-20): a seldom-run,
    project-WIDE sibling of item 9. Item 9 refills the queue from the
    backlog/roadmap when it runs dry; this one steps back from per-task
    work entirely and asks the strategic carver to evaluate the SUITE's
    standing test debt and carve test-IMPROVEMENT packages -- the test
    analog of the carver reading the north star. It fires when
    policy.test_health_interval_days > 0 (opt-in; 0 disables) AND
    inp.days_since_test_health_carve is None (never run -- enabling the
    knob means "do a pass") or >= that interval, AND every guard item 9
    itself honors (the SHARED not-paused / not-carve-in-flight /
    budget_allows / frontier_route_available set, plus the single-carve-
    authority carve_dispatch_planned flag) -> CarveDispatch(kind=
    'test-health'). Like items 9 and 12 it is purely a WHEN decision:
    plan_project stays pure and never measures coverage itself; the
    daemon's carve packet tells the CARVER agent how to evaluate debt
    (and explicitly authorizes carving NOTHING when the suite is healthy,
    so a periodic trigger cannot manufacture busywork).

    ORDERING (a real design call, not an accident): this trigger is
    evaluated BEFORE item 9's headroom refill, so when both want the
    pass's single carve slot, test-health wins. Item 9's condition
    (ready_count < carve_ahead_target) is true on essentially every pass
    of an actively-draining project, so a rare trigger placed after it
    would lose the slot indefinitely and starve -- the cadence would
    silently never fire. Item 12 (READY_TO_CARVE re-carve) still precedes
    BOTH, because finishing already-started work outranks starting new
    work of either kind.
16. GATE VERIFY CADENCE (GA4 2026-07-25, mirrors item 15's shape): a
    seldom-run maintenance probe that re-runs GA1's `nyxloom gate verify`
    canary check to confirm the project's declared gate STILL rejects a
    known-bad commit -- a gate can quietly stop discriminating (a lint
    exclusion widens, a test gets skipped) with nothing else in this
    module noticing. Fires when policy.gate_verify_interval_days > 0
    (opt-in; 0 disables, the safety default) AND
    inp.days_since_gate_verify is None (never run) or >= that interval ->
    VerifyGate(project=...). UNLIKE item 15 this is deliberately OUTSIDE
    the single-carve-authority mutex: a gate verify runs a subprocess
    against a few disposable canary commits, needs no LLM frontier route
    and no carve slot, so it never sets carve_dispatch_planned and never
    consults carve_in_flight / frontier_route_available / budget_allows --
    it only respects project_paused (a paused project starts no new
    process of any kind, same invariant item 14 closed for carving). The
    daemon executes it on a background thread (a full verify runs several
    real gate invocations, minutes) and is idempotent while one is already
    in flight for the project, so this planner may harmlessly re-plan the
    same VerifyGate every pass until the daemon's drain step appends
    GATE_VERIFY_RECORDED and resets the cadence.
17. GAP-AUDIT CARVE TRIGGER (F007 2026-07-27, gap-engine): a seldom-run,
    project-WIDE sibling of item 9. Item 9 refills the queue from the
    backlog/roadmap when it runs dry; this one evaluates whether features
    the project CLAIMS are shipped are actually backed by code reality,
    surfacing gaps as carve candidates. It fires when
    policy.gap_audit_after_changed_lines > 0 (opt-in; 0 disables) AND
    inp.changed_lines_since_gap_audit is None (never run -- enabling the
    knob means "do a first pass") or >= that threshold (activity-counted,
    NOT time-based), AND every guard item 9 itself honors (the SHARED
    not-paused / not-carve-in-flight / budget_allows / frontier_route_available
    set, plus the single-carve-authority carve_dispatch_planned flag) ->
    CarveDispatch(kind='gap-audit'). Like items 9 and 15 it is purely a
    WHEN decision: plan_project stays pure and never computes git diffs
    itself; the daemon's carve packet tells the CARVER agent how to evaluate
    the product-definition (and explicitly authorizes carving NOTHING when
    every checked feature's acceptance criteria hold, so a periodic trigger
    cannot manufacture busywork).

    ORDERING (a real design call, not an accident): this trigger is evaluated
    AFTER item 15's test-health but BEFORE item 9's headroom refill (same
    rationale as item 15 -- item 9's condition holds on essentially every
    pass of an actively-draining project, so a rare trigger placed after it
    would lose the slot indefinitely and starve). Item 12's re-carve still
    precedes both.

    ACTIVITY-COUNTED CADENCE: UNLIKE test_health_interval_days and
    gate_verify_interval_days, this does NOT fire on a calendar schedule.
    It accumulates changed production lines (git diff --numstat) in the
    scope defined by policy.gap_audit_source_paths since the last gap-audit
    carve, and fires when accumulated changes exceed the threshold. An idle
    project with zero code changes must not accrue carve budget on an
    unchanged codebase -- the rationale is activity-scoped, not time-scoped.

WHERE EACH ITEM LIVES (CR-06a..CR-06c, 2026-08-03). The contract above is
unchanged and still frozen; what changed is that `plan_project` is now a DRIVER
over an ordered table of registered rules rather than a 1,160-line function, so
each numbered item is a named rule you can find -- and as of CR-06c NONE of
them is in this module:

  * items 1, 2, 10, 11, 13 -> `rules_lifecycle.py`
  * item 5 (and the B5 self-review leg) -> `rules_review.py`
  * items 6, 7, 16 -> `rules_attention.py`
  * item 3 -> `rules_dispatch.py`; item 4 -> `rules_attempts.py` (CR-06b)
  * items 9, 12, 14, 15, 17 and the carver-session ladder -> `rules_carve.py`
    (CR-06c), which took `planning.LEGACY_RULE_BUDGET` to 0
  * item 8 (actions never embed prose) is a constraint on every action rather
    than a decision, so no rule implements it.

The ORDER those rules run in -- which the items above argue about at length,
because a rare trigger placed after item 9 would starve -- is data in
`planning.rule_table()`, with the rationale attached to each entry. The single
carve authority items 9/12/14/15/17 share is a RESOURCE the arbiter grants,
not a boolean each of them remembers to check; and a rule that takes that grant
and plans nothing must now SAY it meant to (`planning.GrantIntent`), because a
deliberate block and a forgotten emission starve every rule below identically.

WHAT IS LEFT IN THIS MODULE: the action vocabulary (the frozen wire between
planning and the daemon's executors), the `ReconcileInput` snapshot, the pure
`ReconcileTrace` channel, the eligibility predicates shared with the daemon
(`dispatch_eligible`, `fresh_start_eligible`, `attempts_used`,
`implementer_record_count`), and the `plan_project` driver.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

# Every name below is referenced by a `ReconcileInput` field annotation, which
# is what makes this module the place a rule reads its snapshot vocabulary
# from. `CarverStatus` left with the carver-session ladder (CR-06c): it was a
# BRANCH condition, not a field type, and the only reader was that ladder.
from .carver_session import (
    CarveRepairRequest, CarverFeed, CarverSessionSnapshot,
    HumanIntake, ValidatedCarveProposal,
)
from . import planning, snapshot
from .config import ProjectConfig, RouteDef, Routes, healthy_routes, undeclined_routes
from .planning import PlanContext, RuleMatch
from .stages import effective_concurrency
from .types import (
    Blocker, Frontmatter, TaskState, TaskStateFile,
    ReceiptResult, Role, TERMINAL_ATTEMPT_STATES
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
class LaunchSelfReview(Action):
    """B5 2026-07-20: dispatch the self_review stage for a SELF_REVIEWING task.
    A HYBRID of DispatchImplementer (mint a NEW Attempt, role=Role.SELF_REVIEW --
    the role must live on its own record because Attempt.role is immutable and
    EmitAttemptExit keys on it) and ResumeAttempt (build_resume with the
    IMPLEMENTER's BORROWED session_handle, so the self-review runs in the warm
    session that just produced the diff -- no 35-40k cold-start tax). Only ever
    planned when the `self_review` stage is composed into the pipeline (nothing
    routes a task into SELF_REVIEWING otherwise). source_attempt_id names the
    implementer attempt whose session_handle + worktree/branch the daemon borrows;
    if that handle is absent (capture failed) the daemon degrades gracefully to
    AWAITING_REVIEW (skip self-review, let the frontier reviewer -- the real gate
    -- catch it)."""
    source_attempt_id: str | None = None


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
    # B6/P74 (D-R10 reviewer session-reuse): when set, the daemon dispatches this
    # wave's review as a WARM RESUME of a prior frontier-review session (this
    # handle) via adapters.build_resume instead of a cold adapters.build_dispatch,
    # so the ~35-40k role-contract/orientation prefix replays from prompt cache.
    # None == a cold launch (no prior review session to resume, or the
    # review_independent stage's context does not include "session-reuse"). The
    # daemon still mints a FRESH attempt id and stamps it for A7 verdict-attempt
    # binding on the resumed session too -- reuse is a cache optimization, never a
    # relaxation of the binding that made it safe (see the executor).
    resume_session: str | None = None


@dataclass
class LaunchGateDiagnosis(Action):
    """F019 P1b 2026-07-25 (plan-f019-failure-diagnosis.md §P1): dispatch the
    warm independent reviewer in GATE-DIAGNOSIS mode against a single task that
    a pre-merge/mutation gate failed. Unlike LaunchReview (a wave over
    AWAITING_REVIEW tasks), this targets ONE task that STAYS in REVIEW_REJECTED
    throughout -- the reviewer only CLASSIFIES the gate failure into
    {fixable|architectural|product|transient} (the gate already decided "fail");
    it never approves/merges. The daemon executor builds the classify-only
    packet (gate output tail + failed diff + handoff) and reuses the warm review
    session (D-R10/B6) itself -- mirroring how the self_review executor reads the
    source session handle -- so this action carries only the task id. The emitted
    REVIEW_RECORDED{reject_class} is then consumed by the SAME triage table a
    review rejection already uses (reconcile stays the pure router; the reviewer
    is a classifier, never a controller)."""
    pass


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
    # kind (D-065 2026-07-20, B63): which trigger minted this carve, and so
    # which SOURCE section the daemon's packet builder uses. 'headroom' is the
    # default and covers item 9's untargeted refill, item 12's re-scope
    # (further distinguished by task_id) and the targeted backlog carve
    # (item_id) -- all three read the project's WORK sources. 'test-health' is
    # module contract item 15's project-wide suite-debt pass, whose packet
    # points at the test suite and the gate instead. Packet-shaping ONLY:
    # seq/authority/route/lease semantics are identical across kinds, so the
    # single-strategic-carver invariant holds unchanged.
    kind: str = "headroom"


@dataclass
class VerifyGate(Action):
    """GA4 2026-07-25 (module contract item 16, D-065 cadence mirror): the
    periodic gate re-verification trigger -- re-run GA1's `nyxloom gate
    verify` canary probe to confirm the project's declared gate still
    rejects a known-bad commit. Deliberately NOT a CarveDispatch sibling:
    it never contends for the single-carve-authority slot
    (carve_dispatch_planned), needs no LLM frontier route, and needs no
    carve budget -- it is a subprocess probe, not an agent turn. `project`
    names the project this verify targets, mirroring CarveDispatch's own
    `project`-is-self-describing convention (the daemon already knows the
    project too, via run_pass's per-project loop, but carrying it here
    keeps the action self-describing for tests/logs). task_id (inherited
    from Action) is unused -- always a project-wide action, never
    task-scoped."""
    project: str | None = None


@dataclass
class StartCarverSession(Action):
    """F018 P2b-A1 (plan-long-running-carver.md §4.2, §5.1): cold-bootstrap
    (or new-generation) launch of the persistent strategic carver session.
    Planned only when `ReconcileInput.carver_session.status` is ABSENT,
    COLD, or ROTATING (module contract carver-ladder slot 2) -- mirrors
    CarveDispatch's `project`-is-self-describing convention. `mode` and
    `source_ids` carry the SAME packet-shaping vocabulary CarveDispatch's
    `kind`/`item_id` already do (headroom/targeted-intake/merge-feed, an id
    tuple instead of a single item_id) -- daemon execution (A2) decides the
    actual bootstrap packet; this module stays pure."""
    project: str | None = None
    mode: str = "headroom"
    source_ids: tuple[str, ...] = ()


@dataclass
class ResumeCarverSession(Action):
    """F018 P2b-A1 (plan-long-running-carver.md §4.2, §5.2): a warm-session
    turn -- merge-digest ingestion (`mode="merge-feed"`), targeted human
    intake (`mode="targeted-intake"`), or bounded DEGRADED recovery
    (`mode="recover"`). `generation` pins the turn to the session
    generation the planner observed (mirrors the persistent-session-is-
    never-an-evidence-identity rule -- the daemon mints a fresh attempt/
    turn id at execution regardless)."""
    project: str | None = None
    mode: str = "headroom"
    source_ids: tuple[str, ...] = ()
    generation: int = 0


@dataclass
class CompactCarverSession(Action):
    """F018 P2b-A1 (plan-long-running-carver.md §4.2, §6.2): the ONLY
    action ever planned when the PURE `_compaction_due()` predicate fires
    for a WARM session. `trigger` is one of "size"|"turns"|"turns-fallback"
    (DRIFT and OPERATOR triggers are out of scope for this package -- they
    need impure inputs the daemon has not wired yet). Carries nothing but
    the generation + trigger enum; the actual compaction driver call is the
    daemon's job (§6.1's CompactionDriver -- P3's territory), matching
    RunPostMergeGate/AutoMergeTask's "carries the trigger only" convention."""
    project: str | None = None
    generation: int = 0
    trigger: str = ""


@dataclass
class AdmitCarveProposal(Action):
    """F018 P2b-A1 (plan-long-running-carver.md §4.2, §4.3): admit a
    `ValidatedCarveProposal` that already passed schema/hash/lint/revision
    checks (§4.1) -- the daemon re-checks hashes at the effect boundary
    (§4.2 item 1) and only then creates normal tasks / re-scope-supersedes
    the origin, mirroring CarveDispatch's re-scope atomicity (B7). Highest
    priority in the carver-turn ladder: finishing/admitting already-
    produced work outranks starting any new turn. `artifact_ids` is the
    deterministically-sorted set from the chosen proposal."""
    project: str | None = None
    proposal_id: str = ""
    artifact_ids: tuple[str, ...] = ()


@dataclass
class RunPostMergeGate(Action):
    """Post-merge validation trigger (module contract item 11, 2026-07-17):
    the ONLY action ever planned for a VALIDATING task. Carries nothing but
    task_id -- which gate to run, the {worktree} substitution, and the
    subprocess execution itself are the daemon's job (this module stays
    pure, no subprocess/filesystem); see daemon.py's _run_post_merge_gate.
    """


@dataclass
class AutoMergeTask(Action):
    """Guarded-automatic merge trigger (module contract item 13, P48
    2026-07-19): the ONLY action ever planned for a MERGE_READY task when
    policy.merge_mode == 'guarded-automatic'. Reaching MERGE_READY already
    structurally means a REVIEW_INDEPENDENT attempt returned verdict
    'approved' (daemon.py's review-consumption branch is the only writer of
    this transition) -- so this action carries nothing but task_id, same
    shape as RunPostMergeGate: which branch to merge, the actual git
    operations (a disposable scratch worktree + real `git merge --no-ff`
    for genuine 3-way conflict detection, never the surgical commit-tree
    technique an operator can use by hand -- that technique intentionally
    skips conflict detection, acceptable under human supervision but not
    for unattended merging), and escalation on conflict are all the
    daemon's job (see daemon.py's _execute_auto_merge). This module stays
    pure, no subprocess/filesystem.
    """


# --- reconcile trace (P03, D-L5: pure observability channel) ---------------
#
# `plan_project` stays pure (no clock, no I/O, no `log`/`nyxloom.log` import
# reachable from this module -- a load-bearing invariant the doctor replay-
# divergence guard and this module's property-testability both rely on). It
# ALSO records an ordered `ReconcileTrace` of short, pure breadcrumbs -- WHY
# it made each decision -- as pure in-memory data manipulation (no clock, no
# I/O). The daemon (never this module) flushes `.trace.breadcrumbs` to the
# logger at DEBUG after the pass, inside its own `bind(project=...)` scope
# (see daemon.py's run_pass) -- this module never imports or calls the
# logger itself. Breadcrumb `detail` values are ids/enums/short fixed
# strings ONLY -- never handoff-body prose (payload injection rule, module
# contract item 8 -- the exact same rule Action fields like Transition.notes
# already follow).

TRACE_KINDS = frozenset({
    "dispatch", "dispatch-skip", "carve", "carve-skip", "merge",
    "guard-exclude", "state-transition",
    # F018 P2b-A1: short enum/id breadcrumbs for the new-vocabulary carver-
    # session ladder (admit/start/recover/merge-feed/targeted-intake/
    # compact:<trigger>/wait:<status>) -- same "no prose" rule as every
    # other kind above (plan §4.2).
    "carver",
    # GA4 2026-07-25 (module contract item 16): the gate-verify cadence
    # trigger's own breadcrumb ("fire"/"paused") -- same "no prose" rule.
    "gate-verify",
    # F019 P1b 2026-07-25 (module contract item 10): the gate-failure
    # diagnosis routing's breadcrumb ("dispatch"/"skip:drain-agents").
    #
    # REGISTERED LATE, in CR-06a's review, and the delay is the point: the
    # planner emitted this kind from the day F019 landed while this frozenset
    # -- which the docstring below calls the vocabulary a `kind` is one of --
    # did not list it, and NOTHING checked, because this declaration had no
    # consumer at all. `tests/test_planning.py::
    # test_the_breadcrumb_vocabulary_is_exactly_what_the_rules_can_record`
    # now DERIVES the vocabulary from the rules' own call graphs and requires
    # it to match this set exactly in both directions, so the next kind added
    # to a rule fails here instead of drifting for four months.
    "gate-diagnosis",
})


@dataclass(frozen=True)
class TraceNote:
    """One pure breadcrumb. `kind` is one of TRACE_KINDS; `task_id` is the
    task the decision concerns (None for a project-wide decision like the
    untargeted carve trigger); `detail` is a short id/enum/fixed string (a
    dispatch_eligible reason, a route id, a transition label) -- never
    free-form prose."""
    kind: str
    task_id: str | None
    detail: str
    # CR-06a: the planning rule that recorded this breadcrumb, stamped by the
    # kernel's emitter rather than passed by the rule -- so attribution cannot
    # drift from the rule table. Defaulted, because a breadcrumb constructed
    # outside a pass (a test, the daemon's own trace fixtures) has no rule and
    # every existing reader keys on (kind, task_id, detail) alone.
    rule: str = ""


@dataclass(frozen=True)
class ReconcileTrace:
    """Pure data (D-L5): an ordered list of breadcrumbs recording WHY
    plan_project made each decision this pass. No clock, no I/O -- `note()`
    is a plain in-memory list append (legal on a frozen dataclass: frozen
    blocks reassigning `self.breadcrumbs`, not mutating the list object it
    already points at).

    CR-06a adds `matches`, the STRUCTURED half of the same channel (the
    parent plan's work item 3). A breadcrumb says WHY in a closed vocabulary
    a reader must already know; a `RuleMatch` says which RULE and which
    numbered contract item produced a given action -- the question an operator
    looking at an unexpected plan actually has. Both are appended, never
    restated: every breadcrumb keeps its exact (kind, task_id, detail), which
    is what the CR-00 corpus classifies waits from verbatim."""
    breadcrumbs: list[TraceNote] = field(default_factory=list)
    matches: list[RuleMatch] = field(default_factory=list)

    def note(self, kind: str, task_id: str | None, detail: str,
             rule: str = "") -> None:
        self.breadcrumbs.append(
            TraceNote(kind=kind, task_id=task_id, detail=detail, rule=rule))

    def match(self, match: RuleMatch) -> None:
        self.matches.append(match)


class PlanResult(list):
    """`plan_project`'s return value (D-L5). Chosen over a plain `(actions,
    trace)` tuple specifically to avoid churning the ~50 existing call
    sites across test_reconcile.py and daemon.py: PlanResult subclasses
    `list` and IS the actions list, so every iterate/index/len()/
    isinstance(..., list) call site keeps working byte-identically. It
    additionally carries `.trace`, the pass's ReconcileTrace -- only the
    daemon ever reads `.trace`; everything else keeps treating the return
    value as a plain list of actions, exactly as before this package."""

    def __init__(self, actions: Iterable[Action] = (), trace: ReconcileTrace | None = None) -> None:
        super().__init__(actions)
        self.trace = trace if trace is not None else ReconcileTrace()


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
    # B24 2026-07-23 (D-R17, D-B24-2): attempt_id -> whether a TRANSIENT-
    # classified INTERRUPTED attempt has waited out its exponential backoff
    # (daemon.py's TRANSIENT_BACKOFF_SCHEDULE) and may be resumed again this
    # pass, computed by the daemon from disk (Daemon._transient_backoff_
    # ready). Missing entries default to True at the ResumeAttempt gate
    # below -- an ordinary (non-transient) INTERRUPTED attempt, and every
    # existing test that omits this field, is unaffected.
    transient_backoff_ready: dict[str, bool] = field(default_factory=dict)
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
    # B4b 2026-07-20 (D-060 triage stage; critique I4 drift-guard + Tier-2 matrix):
    # the current default-branch HEAD sha, so the triage stage can detect a
    # REVIEW_REJECTED task whose handoff `input_revision` premise has DRIFTED from
    # main -- re-working against a moved base is exactly P40's root cause, so a
    # stale premise re-carves instead of re-dispatching (critique I4). None when
    # the daemon could not resolve main (git failure); the drift-guard then
    # fail-safes to "no drift" (never re-carve on an unknown base). reconcile
    # stays pure -- the daemon computes this via `git rev-parse` and passes it in,
    # exactly like merged_branches.
    head_revision: str | None = None
    # B4b: per-task LLM triage class for tasks currently in REVIEW_REJECTED,
    # derived by the daemon from the LATEST rejected REVIEW_RECORDED event's
    # `reject_class` payload -- the frontier reviewer self-classifies its genuine
    # rejection in the same pass it already read the diff (D-066: Tier-2 without a
    # new dispatched leg or an inline-completion call the daemon does not have).
    # One of "fixable"|"architectural"|"product". A task ABSENT from this dict is
    # unclassified (older reviewer, an infra "incomplete" leg failure, or no class
    # stamped) and falls back to the mechanical attempt-budget path -- graceful
    # degradation, byte-identical to the pre-B4b routing.
    triage_class: dict[str, str] = field(default_factory=dict)
    # CR-09a: task id -> route ids excluded from IMPLEMENTER selection
    # because a reviewer already stamped `reject_class == "incapable"`
    # against that route for this task's origin (`fm.source.ref`, single-hop
    # -- see `daemon._declined_routes`). reconcile stays pure -- it reads
    # this precomputed dict rather than the event log, exactly like
    # triage_class. A task absent from this dict has no known-declined
    # route, byte-identical to the pre-CR-09a routing.
    declined_routes: dict[str, frozenset[str]] = field(default_factory=dict)
    # F019 P1b 2026-07-25 (plan-f019-failure-diagnosis.md §P1): the two
    # daemon-computed inputs that drive gate-failure diagnosis routing. reconcile
    # stays pure -- it reads these precomputed sets rather than the event log,
    # exactly like triage_class. Both default empty, so a project with no
    # gate-caused rejections plans byte-identically to the pre-F019 router.
    #
    # gate_diagnosis_pending: task ids CURRENTLY in REVIEW_REJECTED whose latest
    # rejection cause is a pre-merge/mutation gate failure (not a reviewer), that
    # carry no reviewer class yet, whose consecutive gate-fail streak has reached
    # policy.gate_diagnosis_after_failures, AND that have no diagnosis attempt
    # already in flight. plan_project dispatches the warm reviewer in
    # gate-diagnosis mode for these and SUPPRESSES the blind mechanical retry
    # until the class arrives.
    gate_diagnosis_pending: frozenset[str] = frozenset()
    # gate_diagnosis_attempts: attempt ids of EXITED, not-yet-consumed
    # gate-diagnosis reviewer legs (a REVIEW_INDEPENDENT attempt on a task still
    # in REVIEW_REJECTED with no REVIEW_RECORDED bound to it). The receipt-exit
    # scan mints an EmitAttemptExit for these so the diagnosis verdict is
    # consumed even when the wrapper pre-emitted ATTEMPT_EXITED -- the same gap
    # the AWAITING_REVIEW+REVIEW_INDEPENDENT EXITED scan clause already closes for
    # normal reviews. Gating on this precomputed set (rather than state+role
    # alone) is what keeps an already-consumed normal review -- which also sits
    # in REVIEW_REJECTED but carries a binding -- out of the diagnosis path (O5).
    gate_diagnosis_attempts: frozenset[str] = frozenset()
    # D-065 (B63 2026-07-20): age in DAYS of the most recent test-health carve
    # (module contract item 15), or None when the project has never had one.
    # The daemon derives it by scanning its own event log for the marker
    # _execute_carve_dispatch stamps on a test-health carve's TASK_CREATED
    # (see daemon._days_since_test_health_carve) -- so the cadence is durable
    # across daemon restarts, and this module stays pure (it never reads a
    # clock or an event log itself; `now` and this age both arrive as input).
    # None deliberately means "fire" rather than "never fired": turning the
    # knob on is itself the request for a first pass.
    days_since_test_health_carve: float | None = None
    # GA4 2026-07-25 (module contract item 16): age in DAYS of the most recent
    # GATE_VERIFY_RECORDED (the daemon's periodic re-run of GA1's `nyxloom gate
    # verify` canary probe), or None when this project has never had one. Same
    # "None means fire" convention as days_since_test_health_carve above --
    # turning the knob on is itself the request for a first pass -- and the
    # daemon derives it the same way (a full log scan, not a recent-window
    # dedup flag, so the cadence survives a daemon restart; see
    # daemon._days_since_gate_verify). This module stays pure: it never reads
    # a clock or an event log itself.
    days_since_gate_verify: float | None = None
    # F007 2026-07-27 (gap-engine, module contract item 17): accumulated changed
    # lines (added+deleted via git diff --numstat) since the most recent gap-audit
    # carve, or None if the project has never had a gap-audit carve. The daemon
    # derives it by scanning its own event log for the marker _execute_carve_dispatch
    # stamps on a gap-audit carve's TASK_CREATED (see daemon._changed_lines_since_gap_audit),
    # so the cadence is durable across daemon restarts, and this module stays pure
    # (it never reads git or an event log itself; this value arrives as input).
    # None deliberately means "fire" rather than "never fired": turning the knob on
    # is itself the request for a first pass, identical to item 15/16.
    changed_lines_since_gap_audit: int | None = None
    # F018 P2b-A1 (plan-long-running-carver.md §4.2): the persistent
    # strategic carver's durable session projection (carver_session.
    # project_session), or None. MASTER GATE -- None means "the long-
    # running carver feature is off for this project"; plan_project emits
    # ZERO new-vocabulary carver actions and falls through to the exact
    # pre-existing CarveDispatch paths, byte-identical to every pre-P2b
    # test that omits this field. The daemon builds this from disk (A2);
    # reconcile stays pure.
    carver_session: CarverSessionSnapshot | None = None
    # F018 P2b-A1 (plan §3.2): bounded, cursor-derived merge digests the
    # carver has not yet acknowledged. Empty tuple (the default) means "no
    # pending feed" -- omitted by every pre-P2b test, so it never changes
    # their plan.
    pending_carver_feeds: tuple[CarverFeed, ...] = ()
    # F018 P2b-A1 (plan §5, §2.5): queued human intake entries (idea/plan/
    # question/feedback) awaiting a targeted carver turn. Same "empty ==
    # off" convention as pending_carver_feeds.
    pending_human_intakes: tuple[HumanIntake, ...] = ()
    # F018 P2b-A1 (plan §4.1-§4.3): proposals that already passed schema/
    # hash/lint/revision validation and are ready for deterministic
    # admission (AdmitCarveProposal, the ladder's highest-priority slot).
    # Same "empty == off" convention.
    validated_carve_proposals: tuple[ValidatedCarveProposal, ...] = ()
    # F018 AD3 (docs/handoff/f018-ad3-carve-repair-proposal.md): structurally-
    # invalid carve proposals for the current generation that the warm session
    # should re-emit correctly BEFORE ingesting new feeds. The daemon computes
    # this (reusing P3b's exact counting) and gates it to
    # `1 <= invalid < max_proposal_repairs` so it COMPOSES with P3b's
    # ceiling escalation (never double-fires). Same "empty == off / nothing to
    # repair" convention as its neighbours -- every pre-AD3 test plans byte-
    # identically. The planner only reads it (stays pure).
    pending_carve_repairs: tuple[CarveRepairRequest, ...] = ()
    # CR-02a 2026-08-03 (authoritative snapshot fail-closed audit; DR-03): the
    # provenance record for THIS snapshot -- which inputs were acquired, in
    # which class, with which status/reason. The planner does not consult it
    # (it stays pure, and by construction it is only ever CALLED with an audit
    # whose authoritative inputs are all OK -- `Daemon.run_pass` refuses to
    # plan otherwise). It rides on the input so that:
    #   * an advisory degradation is visible to a rule that wants to explain
    #     itself ("no route was healthy" reads very differently when the
    #     provider probe itself could not run), and
    #   * CR-05's handler registry and CR-06's planning rules consume ONE
    #     already-classified audit instead of re-deriving authoritative-vs-
    #     advisory policy per call site.
    # Defaults to an empty audit so every pre-CR-02a test still builds a
    # ReconcileInput unchanged.
    snapshot_audit: "snapshot.SnapshotAudit" = field(
        default_factory=lambda: snapshot.SnapshotAudit(()))


# B4b (critique I4): a handoff stamps the main sha it was carved against as
# `input_revision`; if main has since moved, that premise is STALE and the packet
# should be re-carved, never re-dispatched against a base it no longer describes.
_REV_PLACEHOLDER = "0000000"


def _premise_drifted(input_revision: str | None, head_revision: str | None) -> bool:
    """True iff a handoff's stamped `input_revision` is a REAL sha that no longer
    matches the current main HEAD (critique I4 -- stale premise). Fail-safe to
    False (no drift) whenever either side is unknown, so the drift-guard fires
    ONLY on a confident mismatch and never re-carves on missing data:
      * head_revision falsy (daemon could not resolve main) -> no drift.
      * input_revision falsy, the '0000000' placeholder, or all-zero (the carver
        could not infer a base) -> no drift (there is no premise to invalidate).
      * otherwise DRIFT iff neither sha is a prefix of the other -- git shas may
        be abbreviated to different lengths, so an abbreviated input_revision that
        prefixes the full head sha is the SAME commit (not drift), and only a real
        divergence counts."""
    if not head_revision or not input_revision:
        return False
    a = input_revision.strip().lower()
    b = head_revision.strip().lower()
    if not a or not b or a == _REV_PLACEHOLDER or all(c == "0" for c in a):
        return False
    return not (a.startswith(b) or b.startswith(a))


# --- MOVED (CR-06c): carve authority ---------------------------------------
#
# CR-06a decomposed `plan_project` into the kernel (`planning.py`) plus rules
# grouped by concern, and moved the concerns that claim no arbitrated resource;
# CR-06b moved dispatch and the attempt ladder. What was left here was ONE
# concern -- carve authority (items 9, 12, 14, 15, 17 and the carver-session
# ladder) -- and CR-06c moved it to `rules_carve.py`, taking
# `planning.LEGACY_RULE_BUDGET` from 6 to 0.
#
# `_compaction_due` and its three `DEFAULT_CARVER_COMPACTION_*` fallbacks went
# with the ladder: nothing else in the package or the daemon ever called them,
# so leaving them here would have kept a shared-helper shape for a helper that
# is not shared -- which is how this module accreted in the first place. The
# predicates that ARE shared with the daemon (`dispatch_eligible`,
# `fresh_start_eligible`, `attempts_used`, `implementer_record_count`,
# `_wall_clock_cap_exceeded`) stay below, unchanged.
#
# What remains in this module: the action vocabulary the rules emit, the
# `ReconcileInput` snapshot they read, the pure trace channel, the eligibility
# predicates, and `plan_project` -- a DRIVER over `planning.rule_table()`.


# MOVED (CR-06b): implementer dispatch (contract item 3) to `rules_dispatch.py`
# and the attempt-recovery ladder (item 4) to `rules_attempts.py`. The ladder
# moved verbatim -- every guard and every dated incident comment with it -- and
# gained exactly one behavioural repair, declared in the differential's
# `KNOWN_DIVERGENCES`: it walks `PlanContext.sorted_task_ids` rather than
# `inp.states.items()` raw, so the attempt channel no longer orders itself by
# whatever order the daemon's directory scan produced.
#
# MOVED (CR-06a): the wave batching and review launch, the self-review leg and
# the three spec-attention signals used to sit here, between the attempt ladder
# and the carve cadences. They claim no arbitrated resource, so they moved
# whole to `rules_review.py` and `rules_attention.py`; their position in the
# pass is now the rule table's business rather than this file's line order.


def plan_project(inp: ReconcileInput) -> PlanResult:
    """Deterministic action plan for one project (see module contract).

    Output order: task lifecycle actions (sorted by task id), then attempt
    actions, then waves, then SpecAttention -- so tests can assert exactly.

    Returns a `PlanResult` (P03, D-L5): IS the actions list (back-compat --
    see PlanResult's own docstring) and additionally carries `.trace`, a pure
    `ReconcileTrace` of breadcrumbs recording WHY key decisions were made this
    pass, plus (CR-06a) the rule-match explanations behind each action.
    Building both is pure in-memory bookkeeping -- no clock, no I/O, no logger
    import -- so this function's purity is unchanged; only the daemon ever
    flushes `.trace` to the logger.

    CR-06a: this is now a DRIVER, not a decision-maker. It derives the pass
    context once, runs the ordered rule table through the arbiter, assembles
    the channels in the plan's declared output order, and lets the arbiter
    deconflict the result. Every decision it used to make itself is a
    registered rule -- see `planning.rule_table()` for the order and the
    reason for it.
    """
    trace = ReconcileTrace()
    ctx = PlanContext.derive(inp)
    table = planning.rule_table()
    arbiter = planning.PlanArbiter(table)
    channels = planning.run_rules(ctx, table, trace, arbiter)
    return PlanResult(arbiter.deconflict(channels.assemble()), trace=trace)


# MOVED (CR-06a): the gate-verify cadence to `rules_attention.py`, the
# output-order assembly to `planning.PlanChannels`, and the whole-plan
# deconfliction to `planning.PlanArbiter.deconflict` -- the last of these
# because a plan that both dead-ends a task and launches an agent for it is a
# property of the PLAN, so no single rule can own it.


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


def _check_healthy_route(fm: Frontmatter, task_id: str, inp: ReconcileInput) -> tuple[bool, str]:
    healthy = healthy_routes(inp.routes.for_tier(fm.tier), inp.provider_ok)
    # CR-09a: stay in sync with rules_dispatch.py's/rules_attempts.py's
    # selection loops -- a task judged eligible here because SOME healthy
    # route exists must never then find its selection loop empty because
    # that route was the one already declined (undeclined_routes applied
    # identically at both eligibility and selection).
    available = undeclined_routes(healthy, inp.declined_routes.get(task_id, frozenset()))
    if not available:
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

    # 4. wip-cap check (B3/P71: the implement stage's effective concurrency --
    # a [stage.implement] override wins, else policy.max_active_tasks -> parity)
    active_count = sum(
        1 for tid, st in inp.states.items()
        if st.state in (TaskState.ACTIVE, TaskState.AWAITING_REVIEW)
    )
    if active_count >= effective_concurrency(
            "implement", inp.cfg.stage_overrides, inp.cfg.policy.max_active_tasks):
        return (False, 'wip-cap')

    # 5. attempts-exhausted check (P60 M8: single role-filtered accessor --
    # excludes LIMIT attempts AND non-implementer attempts, so a review does
    # not count against the implementer dispatch budget).
    if attempts_used(tsf) >= inp.cfg.policy.max_attempts_per_task:
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
    ok, reason = _check_healthy_route(fm, tsf.task_id, inp)
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
    ok, reason = _check_healthy_route(fm, tsf.task_id, inp)
    if not ok:
        return (ok, reason)
    return (True, '')


def attempts_used(tsf: TaskStateFile) -> int:
    """P60 2026-07-20 (M8, Fable-xhigh critique). THE single receipt-based
    implementer-attempt-budget accessor, replacing three subtly-different
    inline formulas (dispatch_eligible check 5, the REVIEW_REJECTED re-queue
    counter, and the ERROR-path daemon count) plus the resume path's copy.
    Those formulas were role-BLIND -- a reviewer/carver attempt also lands in
    tsf.attempts with a DONE receipt, so a single reject cycle (implement +
    review) counted as 2 units, exhausting a task after ~2 rejections instead
    of policy.max_attempts_per_task implementer tries.

    Counts IMPLEMENTER attempts that reached a terminal state and carry a
    receipt whose result is not LIMIT (a rate-limited attempt does not consume
    the budget -- it is retried for free) and, as of B24 2026-07-23 (D-R17,
    D-B24-6), not TRANSIENT either -- a provider-throttled attempt is
    likewise retried for free, never burning the budget on a 502/429/
    ResourceExhausted hiccup (a TRANSIENT-classified attempt normally stays
    INTERRUPTED, non-terminal, so this exclusion mostly guards a future/
    edge path rather than today's steady-state one -- cheap and correct
    either way, and mirrors LIMIT's existing exclusion exactly). The
    TERMINAL_ATTEMPT_STATES guard matches the resume path's existing
    condition (never counts an in-flight INTERRUPTED attempt against itself)
    and is a no-op for the other sites (a REVIEW_REJECTED / QUEUED / just-
    ERRORed task's counted attempts are all already terminal). Distinct from
    implementer_record_count below, which is the RECORD budget (counts
    receiptless poisoned records too, P34)."""
    return sum(
        1 for a in tsf.attempts
        if a.role is Role.IMPLEMENTER
        and a.state in TERMINAL_ATTEMPT_STATES
        and a.receipt is not None
        and a.receipt.result not in (ReceiptResult.LIMIT, ReceiptResult.TRANSIENT)
    )


def implementer_record_count(tsf: TaskStateFile) -> int:
    """Distinct-record budget (P34 2026-07-16): count of role==IMPLEMENTER
    attempt RECORDS in tsf.attempts, unlike the receipt-based
    attempts_used above -- a poisoned INTERRUPTED record has no receipt
    but still consumes one fresh-start's worth of budget, so this must
    count it (else the fresh-start sequence never terminates, O5)."""
    return sum(1 for a in tsf.attempts if a.role == Role.IMPLEMENTER)
