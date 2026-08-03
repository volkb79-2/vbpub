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
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from .carver_session import (
    CarveRepairRequest, CarverFeed, CarverSessionSnapshot, CarverStatus,
    HumanIntake, ValidatedCarveProposal,
)
from . import snapshot
from .config import ProjectConfig, RouteDef, Routes
from .stages import effective_concurrency, stage_context
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


@dataclass(frozen=True)
class ReconcileTrace:
    """Pure data (D-L5): an ordered list of breadcrumbs recording WHY
    plan_project made each decision this pass. No clock, no I/O -- `note()`
    is a plain in-memory list append (legal on a frozen dataclass: frozen
    blocks reassigning `self.breadcrumbs`, not mutating the list object it
    already points at)."""
    breadcrumbs: list[TraceNote] = field(default_factory=list)

    def note(self, kind: str, task_id: str | None, detail: str) -> None:
        self.breadcrumbs.append(TraceNote(kind=kind, task_id=task_id, detail=detail))


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


# F018 P2b-A1 (plan-long-running-carver.md §6.2): pure compaction-trigger
# defaults, mirroring DEFAULT_ATTEMPT_MAX_WALL_SECONDS's getattr-fallback
# convention above. `config.CarveStageConfig` (shipped inert in P1, see
# `[stage.carve]`) already carries exactly these three knobs
# (`compact_context_ratio`, `compact_after_turns`, `compact_hard_after_
# turns`) plus the DEGRADED bounded-recovery budget (`max_resume_
# failures`) with the SAME §6.2-recommended defaults, so `_compaction_due`
# below reads `inp.cfg.carve.*` directly (always present -- a dataclass
# field with a default_factory, never missing); these module constants are
# a defensive getattr fallback only, never a second source of truth to
# drift from config.py's. DRIFT and OPERATOR triggers are out of scope for
# this package -- both need impure inputs (a spine-revision diff, an
# audited operator request) that only the daemon (A2+) can supply.
DEFAULT_CARVER_COMPACTION_TURN_THRESHOLD = 24
DEFAULT_CARVER_COMPACTION_SIZE_RATIO = 0.70
DEFAULT_CARVER_COMPACTION_HARD_FALLBACK_TURNS = 32


def _compaction_due(snapshot: CarverSessionSnapshot, cfg: ProjectConfig) -> str | None:
    """Pure §6.2 compaction-trigger predicate for a WARM carver session.
    Reads ONLY the durable snapshot + policy -- no clock, no I/O. Returns
    the trigger name ("size"|"turns"|"turns-fallback") or None.

    Ratio-known regime (`measured_context_ratio is not None` -- §6.2 "use
    usage only when the route's usage_source is verified"): the SIZE
    threshold (>= 70% of context window) is the primary signal, checked
    first; the ordinary 24-turn threshold is a secondary safety net for
    when size looks fine but the turn count is climbing regardless.

    Ratio-untrusted regime (`measured_context_ratio is None` -- §6.2 "if
    the harness exposes no trustworthy context occupancy, rely on the turn
    threshold"): the size check cannot run at all, so the more
    conservative 32-turn HARD FALLBACK is the sole applicable check. It
    deliberately does NOT reuse the 24-turn threshold here -- an
    unconditional 24-turn check would fire first in every ratio-None case
    (24 < 32), making the dedicated 32-turn hard-fallback trigger name
    unreachable dead code; scoping the ordinary 24-turn threshold to the
    ratio-known branch is what keeps both threshold constants -- and both
    trigger names -- independently observable, matching this package's
    oracle ("hard-fallback (32 with ratio None)" as its own scenario,
    distinct from "turn-threshold (24)")."""
    turns = snapshot.successful_turns_since_compaction
    ratio = snapshot.measured_context_ratio
    size_ratio = getattr(cfg.carve, "compact_context_ratio", DEFAULT_CARVER_COMPACTION_SIZE_RATIO)
    turn_threshold = getattr(cfg.carve, "compact_after_turns", DEFAULT_CARVER_COMPACTION_TURN_THRESHOLD)
    hard_fallback_turns = getattr(
        cfg.carve, "compact_hard_after_turns", DEFAULT_CARVER_COMPACTION_HARD_FALLBACK_TURNS)

    if ratio is not None:
        if ratio >= size_ratio:
            return "size"
        if turns >= turn_threshold:
            return "turns"
        return None
    if turns >= hard_fallback_turns:
        return "turns-fallback"
    return None


def plan_project(inp: ReconcileInput) -> PlanResult:
    """Deterministic action plan for one project (see module contract).

    Output order: task lifecycle actions (sorted by task id), then attempt
    actions, then waves, then SpecAttention — so tests can assert exactly.

    Returns a `PlanResult` (P03, D-L5): IS the actions list (back-compat --
    see PlanResult's own docstring) and additionally carries `.trace`, a
    pure `ReconcileTrace` of breadcrumbs recording WHY key decisions were
    made this pass. Building the trace is itself pure in-memory bookkeeping
    -- no clock, no I/O, no logger import -- so this function's purity is
    unchanged; only the daemon ever flushes `.trace` to the logger.
    """
    trace = ReconcileTrace()

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
            trace.note("state-transition", fm_id, "CARVED->QUEUED")

        # Decision hold logic
        d_deps = fm.decision_deps()
        open_d_deps = [d for d in d_deps if d in inp.decisions_open]

        if tsf.state == TaskState.QUEUED and open_d_deps:
            # Transition to NEEDS_DECISION
            notes = ", ".join(open_d_deps)
            task_actions.append(Transition(task_id=fm_id, to=TaskState.NEEDS_DECISION, notes=notes))
            trace.note("state-transition", fm_id, "QUEUED->NEEDS_DECISION")
        elif tsf.state == TaskState.NEEDS_DECISION and not open_d_deps:
            # Transition back to QUEUED
            task_actions.append(Transition(task_id=fm_id, to=TaskState.QUEUED, notes=None))
            trace.note("state-transition", fm_id, "NEEDS_DECISION->QUEUED")

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
            # F019 P1b 2026-07-25 (gate-failure diagnosis routing): a pre-merge/
            # mutation gate failure ALSO lands a task here (daemon.py "... gate
            # failed; not published"), but carries no reviewer class, so without
            # this it always falls into the blind mechanical-retry branch below.
            # When the daemon flags this task gate-diagnosis-pending (gate-caused,
            # unclassified, streak >= policy.gate_diagnosis_after_failures, none
            # in flight), dispatch the warm reviewer in gate-diagnosis mode to
            # CLASSIFY the failure and SUPPRESS the blind retry this pass (the
            # `continue`) -- once the reviewer's REVIEW_RECORDED{reject_class}
            # lands, the task re-enters this branch UNflagged and the triage table
            # below routes it exactly as a review rejection already is. Skipped
            # under drain-agents (no new agent process of any kind, mirroring the
            # wave-review pause rule); the task parks REVIEW_REJECTED for a later
            # run/drain-handoffs pass. The task NEVER leaves REVIEW_REJECTED during
            # diagnosis -- un-gated code can never reach the merge path.
            if fm_id in inp.gate_diagnosis_pending:
                if inp.pause_mode != "drain-agents":
                    task_actions.append(LaunchGateDiagnosis(task_id=fm_id))
                    trace.note("gate-diagnosis", fm_id, "dispatch")
                else:
                    trace.note("gate-diagnosis", fm_id, "skip:drain-agents")
                continue
            # B4b 2026-07-20 (D-060 triage stage; critique CRITIQUE.md:207 two
            # tiers + the {infra, stale-premise, fixable, architectural, product}
            # matrix). The infra class ("incomplete" leg failure) is already split
            # off upstream (A4/M7: a non-DONE review receipt records "incomplete",
            # never "rejected", so it carries no triage_class and never reaches the
            # semantic routing here). The remaining four are decided in a fixed
            # PRECEDENCE, most-terminal first, so a task that trips several signals
            # takes the safest single route:
            #   product        -> NEEDS_DECISION  (a human must decide direction;
            #                      no retry or re-carve resolves a product question)
            #   stale-premise  -> READY_TO_CARVE  (I4: input_revision drifted from
            #                      main; re-work would build on a moved base)
            #   architectural  -> READY_TO_CARVE  (design/scope wrong; same-base
            #                      retries cannot fix it -> re-scope via the carver)
            #   incapable      -> READY_TO_CARVE  (D-R3, 2026-07-26 refined: scope was
            #                      fine, the MODEL wasn't capable of it -- same
            #                      destination as architectural, but a DISTINCT
            #                      transition note so the daemon-side rescope-dict
            #                      builder can tell which one fired and bump the
            #                      tier instead of re-scoping; see
            #                      daemon._carve_packet_body_lines)
            #   fixable / none -> the mechanical attempt-budget path (below), a
            #                      TARGETED re-queue whose re-dispatch packet embeds
            #                      the review verdict (daemon build_dispatch
            #                      prior_verdict) so it is never the bare
            #                      context-free same-model retry the critique bans.
            # Drift & architectural/incapable all re-carve, so in a carve-less
            # pipeline (gated/lean) they share the exhausted case's terminating
            # escalation to NEEDS_DECISION -- never a dead-end in READY_TO_CARVE
            # with no owner.
            tclass = inp.triage_class.get(fm_id)
            has_carve = "carve" in inp.cfg.pipeline
            drifted = _premise_drifted(fm.input_revision, inp.head_revision)
            if tclass == "product":
                task_actions.append(Transition(
                    task_id=fm_id, to=TaskState.NEEDS_DECISION,
                    notes="review rejected -- triage: product decision required; escalating to operator",
                ))
            elif drifted and has_carve:
                task_actions.append(Transition(
                    task_id=fm_id, to=TaskState.READY_TO_CARVE,
                    notes=(f"review rejected -- stale premise (input_revision "
                           f"{fm.input_revision} != main {inp.head_revision[:7]}); routed for re-carve"),
                ))
            elif tclass in ("architectural", "incapable") and has_carve:
                task_actions.append(Transition(
                    task_id=fm_id, to=TaskState.READY_TO_CARVE,
                    notes=f"review rejected -- triage: {tclass}; routed for re-scope",
                ))
            elif (drifted or tclass in ("architectural", "incapable")) and not has_carve:
                # Stale-premise, architectural, or incapable, but the composed
                # pipeline has no carve stage to re-scope the work (gated/lean).
                # Escalate to a human -- same terminating logic B4a uses for the
                # exhausted case, so the reject loop still closes rather than
                # dead-ending in READY_TO_CARVE. When not drifted, this branch's
                # own condition guarantees tclass is one of the two carve-routed
                # classes, so using it directly (rather than hardcoding
                # "architectural") preserves that class's own name in the note.
                reason = "stale premise" if drifted else tclass
                task_actions.append(Transition(
                    task_id=fm_id, to=TaskState.NEEDS_DECISION,
                    notes=f"review rejected -- triage: {reason}; no carve stage, escalating to operator",
                ))
            # P60 (M8): attempts_used counts only IMPLEMENTER attempts -- the
            # old role-blind formula here counted the review attempt too, so a
            # reject cycle burned 2 units and exhausted the task a rejection early.
            elif attempts_used(tsf) < inp.cfg.policy.max_attempts_per_task:
                task_actions.append(Transition(
                    task_id=fm_id, to=TaskState.QUEUED,
                    notes="review rejected -- re-queued for re-work (attempt budget remains)",
                ))
            elif "carve" in inp.cfg.pipeline:
                # Attempts exhausted, and the pipeline has a carve stage: route
                # to READY_TO_CARVE so item 12's handler (below) re-dispatches
                # it to the single strategic carver for a fresh, re-scoped
                # package instead of stranding it forever.
                task_actions.append(Transition(
                    task_id=fm_id, to=TaskState.READY_TO_CARVE,
                    notes="review rejected -- attempt budget exhausted; routed for re-carve",
                ))
            else:
                # B4a/D-060: attempts exhausted and the composed pipeline has NO
                # carve stage to re-scope the work (the `gated`/`lean` presets).
                # Escalate to a human decision instead -- NEEDS_DECISION is a
                # legal REVIEW_REJECTED edge and a lifecycle state, so the reject
                # loop still terminates rather than dead-ending in READY_TO_CARVE
                # with no owner. This is exactly what makes a carve-less pipeline
                # safe (and is why stages.validate_pipeline can now accept one).
                task_actions.append(Transition(
                    task_id=fm_id, to=TaskState.NEEDS_DECISION,
                    notes="review rejected -- attempt budget exhausted; no carve stage, escalating to operator",
                ))

        # POST-MERGE VALIDATION (module contract item 11): MERGED ->
        # VALIDATING is pure bookkeeping (self-limiting, same pattern as
        # CARVED->QUEUED above); VALIDATING itself just re-emits the trigger
        # every pass until the daemon's execution of RunPostMergeGate
        # transitions the task onward to COMPLETED or BLOCKED.
        #
        # D-060 (B2, docs/spec-flow-stages.md): the VALIDATING handler honours
        # the composed pipeline. `post_merge_gate` in the pipeline (the default,
        # and every project with no `pipeline` key -> parity) -> RunPostMergeGate
        # exactly as before. A pipeline that omits the gate stage (e.g. the
        # `lean` preset) auto-advances VALIDATING -> COMPLETED immediately -- the
        # frozen graph already permits that edge, and MERGED still transits
        # through VALIDATING first (never MERGED -> COMPLETED directly), so no
        # new transition edge is introduced.
        if tsf.state == TaskState.MERGED:
            task_actions.append(Transition(
                task_id=fm_id, to=TaskState.VALIDATING,
                notes="post-merge validation started",
            ))
        elif tsf.state == TaskState.VALIDATING:
            if "post_merge_gate" in inp.cfg.pipeline:
                task_actions.append(RunPostMergeGate(task_id=fm_id))
            else:
                task_actions.append(Transition(
                    task_id=fm_id, to=TaskState.COMPLETED,
                    notes="no post_merge_gate stage -- auto-validated",
                ))

        # GUARDED-AUTOMATIC MERGE (module contract item 13, P48 2026-07-19):
        # a MERGE_READY task only ever gets here via an 'approved' review
        # verdict (daemon.py's REVIEW_RECORDED-consuming branch is the sole
        # writer of this transition), so no separate verdict check is
        # needed here -- reaching this state already IS the guard. The
        # remaining guard this module CAN express purely is project_paused:
        # if the runaway watchdog (or an operator) has paused the project
        # for ANY reason (drain-handoffs or drain-agents), guarded-automatic
        # must not merge blind -- unattended merging during a flagged
        # condition is exactly backwards from the safety intent. Self-
        # limiting like every other item here: the daemon's execution of
        # AutoMergeTask transitions the task off MERGE_READY (to MERGED on
        # success, back to REVIEW_REJECTED-adjacent NEEDS_OPERATOR escalation
        # on a real git conflict -- see daemon.py's _execute_auto_merge), so
        # this does not refire for the same task once acted on.
        elif (tsf.state == TaskState.MERGE_READY
              and inp.cfg.policy.merge_mode == "guarded-automatic"
              and not inp.project_paused):
            task_actions.append(AutoMergeTask(task_id=fm_id))
            trace.note("merge", fm_id, "auto-merge")

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
    frontier_routes = inp.routes.for_role(Role.REVIEW_INDEPENDENT.value)
    frontier_route_available = any(
        inp.provider_ok.get(r.route_id, False) for r in frontier_routes
    )
    # Reviewer addendum (2026-07-19, post-P45 merge review): also hoisted
    # here and shared, so the READY_TO_CARVE handler respects the same
    # budget-exhaustion stop every other dispatch path in this module
    # already honors (dispatch_eligible's own check 5, and item 9's
    # untargeted trigger below) -- a rejected task's re-carve is not exempt
    # from "no budget left, no new agent process starts."
    budget_allows = inp.budget_remaining is None or inp.budget_remaining > 0
    # True once EITHER trigger has planned the pass's single CarveDispatch
    # -- the operator's explicit ask: the single strategic carver remains
    # the sole carve authority, so at most one CarveDispatch is ever planned
    # in a pass no matter which of the two triggers wants one.
    carve_dispatch_planned = False
    # F018 P2b-A1: `carve_actions` moved up from its old position (just
    # before item 15, module contract item 9's comment block) so slot 6
    # below (queued human intake -- planned AFTER item 12 but BEFORE item
    # 15/9) can share the same list; still appended to ONLY by item 12 (via
    # lifecycle_by_id, unaffected), item 15, and item 9, exactly as before
    # this package.
    carve_actions: list[Action] = []
    # F018 P2b-A1 (plan §4.2, §3.3): the new-vocabulary carver-session
    # actions this pass may plan (Admit/Start/Resume/Compact). Always empty
    # when inp.carver_session is None or the pipeline has no carve stage --
    # the MASTER GATE (byte-identical-when-off invariant: a pass with no
    # carver_session falls through to the exact pre-P2b CarveDispatch paths
    # below, unchanged).
    carver_actions: list[Action] = []
    # F018 P2b-A1: pipeline-carve-stage gate, reusing the EXACT predicate
    # the REVIEW_REJECTED triage branch (module contract item 10, above)
    # already uses for "does this project's pipeline include a carve
    # stage" -- never a second, independently-drifting copy. gated/lean
    # (no carve stage) must never plan carver-session work even if a
    # carver_session were somehow present.
    carver_pipeline_gate = "carve" in inp.cfg.pipeline

    # === Carver-session ladder, slots 1-4 (plan §3.3 priority 1-4): finish/
    # admit an already-produced proposal; bootstrap/recover the session;
    # ingest pending merge digests; perform due compaction. Evaluated
    # BEFORE item 12's READY_TO_CARVE re-scope -- plan §3.3: "never before
    # a pending merge feed that may invalidate its premise." Shares
    # carve_in_flight/frontier_route_available/budget_allows/
    # project_paused with items 9 & 12 above (same hoisted guards, never a
    # second copy) and the SAME carve_dispatch_planned mutex: a new-
    # vocabulary carver action and an old CarveDispatch are mutually
    # exclusive in one pass -- whichever this ladder or item 12/15/9
    # selects first wins.
    if (inp.carver_session is not None and carver_pipeline_gate
            and not inp.project_paused and not carve_in_flight
            and frontier_route_available and budget_allows):
        snap = inp.carver_session
        status = snap.status

        if status in (CarverStatus.STARTING, CarverStatus.COMPACTING):
            # §2.4: "planner emits no second carver turn" (STARTING) / "all
            # intake/feed/carve work waits" (COMPACTING) -- a turn is
            # already effectively in flight for this generation, so this
            # pass's single carver slot is considered consumed: neither a
            # new-vocabulary action NOR the legacy CarveDispatch triggers
            # (item 12/15/9 below) fire this pass.
            carve_dispatch_planned = True
            trace.note("carver", None, f"wait:{status.value.lower()}")
        elif inp.validated_carve_proposals:
            # Slot 1 (highest priority): finish/admit an already-produced
            # proposal. Deterministically sorted so a pass with several
            # validated proposals always picks the same one.
            chosen = sorted(inp.validated_carve_proposals, key=lambda p: p.proposal_id)[0]
            carver_actions.append(AdmitCarveProposal(
                project=inp.cfg.project_id,
                proposal_id=chosen.proposal_id,
                artifact_ids=tuple(sorted(chosen.artifact_ids)),
            ))
            carve_dispatch_planned = True
            trace.note("carver", None, "admit")
        elif status in (CarverStatus.ABSENT, CarverStatus.COLD, CarverStatus.ROTATING):
            # Slot 2: cold bootstrap / new-generation launch.
            carver_actions.append(StartCarverSession(project=inp.cfg.project_id))
            carve_dispatch_planned = True
            trace.note("carver", None, "start")
        elif status is CarverStatus.DEGRADED:
            # Slot 2 (companion, §2.4 "bounded recovery"): a resume, only
            # while the recovery budget is not exhausted. Reuses
            # CarveStageConfig.max_resume_failures (the same §2.2 knob P1
            # already shipped for this exact concept), never a second,
            # parallel budget field.
            if snap.resume_failures < inp.cfg.carve.max_resume_failures:
                carver_actions.append(ResumeCarverSession(
                    project=inp.cfg.project_id, mode="recover",
                    generation=snap.generation,
                ))
                carve_dispatch_planned = True
                trace.note("carver", None, "recover")
            # else: recovery budget exhausted -- leave DEGRADED for the
            # daemon/operator (A2+ territory); this package plans no
            # action and does not consume the mutex, so item 12/15/9 may
            # still run this pass.
        elif status is CarverStatus.WARM:
            if inp.pending_carve_repairs:
                # Slot (F018 AD3): repair a structurally-invalid proposal BEFORE
                # ingesting any new merge feed -- a broken premise is fixed
                # first (plan §3.3's "never before a pending merge feed that may
                # invalidate its premise" applies doubly to a proposal already
                # known invalid). NO source_ids -> this is NOT a feed -> the P4a
                # merge-feed shared cursor (last_consumed_event_sequence) is
                # untouched. The daemon re-derives the invalid proposal ids for
                # the packet (keeps this action minimal and the planner pure).
                # Composes with P3b's escalation: the daemon only populates
                # pending_carve_repairs while `1 <= invalid < max_proposal_
                # repairs`, so at/above the ceiling this slot is empty and P3b's
                # NEEDS_OPERATOR escalation fires instead (mutually exclusive by
                # a single `<` vs `>=` boundary).
                carver_actions.append(ResumeCarverSession(
                    project=inp.cfg.project_id, mode="repair-proposal",
                    generation=snap.generation,
                ))
                carve_dispatch_planned = True
                trace.note("carver", None, "repair-proposal")
            elif inp.pending_carver_feeds:
                # Slot 3: ingest pending merge digests. Plan §3.2: "preserving
                # event order" -- sort by event_sequence (arrival order), NOT
                # digest_id (a "merge:{project}:{commit_sha}" string whose sort
                # order is an arbitrary commit hash, unrelated to when each
                # merge happened).
                # source_ids= is §4.2's authoritative arg name (plan §3.2's
                # "source_sequences" is a stale cross-reference -- do not
                # rename toward it).
                ids = tuple(f.digest_id for f in
                            sorted(inp.pending_carver_feeds, key=lambda f: f.event_sequence))
                carver_actions.append(ResumeCarverSession(
                    project=inp.cfg.project_id, mode="merge-feed",
                    source_ids=ids, generation=snap.generation,
                ))
                carve_dispatch_planned = True
                trace.note("carver", None, "merge-feed")
            else:
                trigger = _compaction_due(snap, inp.cfg)
                if trigger is not None:
                    # Slot 4: due compaction.
                    carver_actions.append(CompactCarverSession(
                        project=inp.cfg.project_id, generation=snap.generation,
                        trigger=trigger,
                    ))
                    carve_dispatch_planned = True
                    trace.note("carver", None, f"compact:{trigger}")
            # WARM with no pending feed and no compaction due plans nothing
            # here -- item 12's READY_TO_CARVE re-scope (slot 5) gets its
            # chance next, exactly as before this package.

    # 12. READY_TO_CARVE handler (P45 2026-07-19): see module contract item
    # 12 docstring above for the full rationale. Sorted task-id order for
    # determinism (mirrors item 3's dispatch-capacity loop below) -- if
    # multiple tasks are simultaneously READY_TO_CARVE, only the lowest
    # task_id gets this pass's single carve slot; the rest stay in
    # READY_TO_CARVE, picked up on a later pass.
    #
    # P52 2026-07-19 (live incident, dstdns): neither this handler nor item
    # 9's untargeted trigger below ever checked project_paused -- a
    # "paused" project (drain-handoffs OR drain-agents) still got a NEW
    # carve dispatch fired against it every pass ready_count sat below
    # carve_ahead_target, completely bypassing the pause. 4 unauthorized
    # carve dispatches fired against dstdns in ~15 minutes before this was
    # caught live. dispatch_eligible (regular implementer dispatch) and
    # item 13 (guarded-automatic merge) already both consult
    # project_paused; carving was the one dispatch path in this whole
    # module that never did. Added to the shared guard (not two
    # independently-drifting copies), same convention as carve_in_flight/
    # frontier_route_available/budget_allows above.
    #
    # F018 P2b-A1: `not carve_dispatch_planned` added to this guard --
    # slots 1-4 above may already have used this pass's single carver slot
    # (an admission, bootstrap, recovery, feed, or compaction outranks a
    # re-scope in plan §3.3's priority order), so this legacy trigger must
    # now also respect the shared mutex it previously always set first.
    if (not carve_dispatch_planned and not inp.project_paused and not carve_in_flight
            and frontier_route_available and budget_allows):
        ready_to_carve_ids = sorted(
            task_id for task_id, tsf in inp.states.items()
            if tsf.state == TaskState.READY_TO_CARVE
        )
        if ready_to_carve_ids:
            chosen_id = ready_to_carve_ids[0]
            chosen_actions = lifecycle_by_id.setdefault(chosen_id, [])
            # B7 2026-07-20 (P75, critique A10/M20): plan ONLY the CarveDispatch
            # here. The origin task's SUPERSEDED transition is NO LONGER a separate
            # planned action -- daemon._execute_carve_dispatch emits it atomically,
            # and ONLY after the re-scope carve actually launches (with the RESCOPED
            # outcome + the origin's handoff/verdict/drift folded into the carve
            # packet). Splitting them here was the M20 bug: per-action isolation
            # (A10/M12) runs each action independently, so the supersede fired even
            # when the CarveDispatch early-returned (admission refused / no route),
            # silently dropping the rejected task's re-carve. Coupling the supersede
            # to the launch is the only real atomicity. task_id=chosen_id now DRIVES
            # that path (the origin whose context seeds the re-scope packet), where
            # for item 9's untargeted trigger it stays None.
            chosen_actions.append(
                CarveDispatch(project=inp.cfg.project_id, task_id=chosen_id)
            )
            carve_dispatch_planned = True
            trace.note("carve", chosen_id, "ready-to-carve")

    # === Carver-session ladder, slot 6 (plan §3.3 priority 6): queued
    # human intake -- evaluated AFTER item 12's re-scope but BEFORE item 15
    # (test-health) and item 9 (headroom refill), matching plan §3.3's
    # explicit order exactly. Same hoisted guards + shared mutex as slots
    # 1-4 above; `not carve_dispatch_planned` is REQUIRED here (unlike
    # slots 1-4, which run first and only ever set the flag) because item
    # 12 immediately above may have just used this pass's single slot.
    if (inp.carver_session is not None and carver_pipeline_gate
            and not carve_dispatch_planned
            and not inp.project_paused and not carve_in_flight
            and frontier_route_available and budget_allows
            and inp.carver_session.status is CarverStatus.WARM
            and inp.pending_human_intakes):
        # Arrival (event_sequence) order, not intake_id sort order -- same
        # rationale as slot 3's merge-feed ordering above. source_ids= is
        # §4.2's authoritative arg name (plan §3.2's "source_sequences" is a
        # stale cross-reference -- do not rename toward it).
        ids = tuple(i.intake_id for i in
                    sorted(inp.pending_human_intakes, key=lambda i: i.event_sequence))
        carver_actions.append(ResumeCarverSession(
            project=inp.cfg.project_id, mode="targeted-intake",
            source_ids=ids, generation=inp.carver_session.generation,
        ))
        carve_dispatch_planned = True
        trace.note("carver", None, "targeted-intake")

    # 3. Dispatch eligible QUEUED tasks (with capacity limit)
    # Count current active tasks
    active_count = sum(
        1 for tsf in inp.states.values()
        if tsf.state in (TaskState.ACTIVE, TaskState.AWAITING_REVIEW)
    )
    # B3/P71: the implement stage's concurrency (a per-project [stage.implement]
    # override, else policy.max_active_tasks by default -> parity).
    implement_cap = effective_concurrency(
        "implement", inp.cfg.stage_overrides, inp.cfg.policy.max_active_tasks)
    dispatch_capacity = implement_cap - active_count

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
                    trace.note("dispatch", fm_id, f"route:{route_def.route_id}")
                    break
        else:
            trace.note("dispatch-skip", fm_id, reason)

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
                             and attempt.role == Role.REVIEW_INDEPENDENT)
                         # F019 P1b 2026-07-25: a GATE-DIAGNOSIS reviewer leg is a
                         # REVIEW_INDEPENDENT attempt on a task STILL in
                         # REVIEW_REJECTED (not AWAITING_REVIEW). If the wrapper
                         # pre-emitted its EXITED, none of the clauses above match
                         # and the diagnosis verdict would strand -- the exact gap
                         # the AWAITING_REVIEW clause above closes for normal
                         # reviews. Gate on the daemon-precomputed unconsumed set
                         # (a REVIEW_INDEPENDENT attempt with no REVIEW_RECORDED
                         # bound) so an already-consumed normal review -- which
                         # also lands in REVIEW_REJECTED but carries a binding --
                         # is NOT re-consumed here (O5).
                         or (attempt.state == AttemptState.EXITED
                             and attempt.attempt_id in inp.gate_diagnosis_attempts)
                         # P32 2026-07-16: a carver's EXITED attempt whose
                         # live pass was missed (daemon restart landing on
                         # the exit) left the synthetic carve task ACTIVE
                         # forever, permanently eating a wip slot — the
                         # daemon's _consume_carve_exit handler already
                         # retires it to SUPERSEDED, but only ever ran off
                         # this same re-scan, which didn't cover CARVER.
                         or (attempt.state == AttemptState.EXITED
                             and tsf.state == TaskState.ACTIVE
                             and attempt.role == Role.CARVER)
                         # B5 2026-07-20: a self_review attempt's EXITED receipt
                         # is consumed ONLY while the task is SELF_REVIEWING. The
                         # state scope (not the role alone) is load-bearing: a
                         # SELF_REVIEW attempt lingering in any other state (e.g.
                         # ACTIVE) stays unconsumed, so every non-SELF_REVIEWING
                         # path is byte-identical to today (see
                         # test_non_carver_exited_active_task_no_exit).
                         or (attempt.state == AttemptState.EXITED
                             and tsf.state == TaskState.SELF_REVIEWING
                             and attempt.role == Role.SELF_REVIEW))):
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

            # P54 2026-07-19 (M2, CRITICAL -- attempt-state closure). A FAILED
            # attempt is TERMINAL: the wrapper writes ATTEMPT_FAILED for a
            # lease-lost-race (exit 75) or a spawn failure -- neither ran real
            # work -- but the loop had NO branch for FAILED, so the task was
            # never moved off its non-terminal state. Consequence: an
            # IMPLEMENTER task stuck ACTIVE forever (eats a wip slot); a CARVER
            # synthetic task stuck ACTIVE forever -> carve_in_flight permanently
            # True (any non-terminal task carrying a CARVER attempt) -> ALL
            # carving deadlocks silently, with nothing in the log but one
            # ATTEMPT_FAILED. And the planner CREATES same-pass capacity-1 lease
            # collisions itself (it plans dispatches against the snapshot
            # leases_free and never decrements it while planning), so a FAILED
            # race-loser is routine operation, not adversarial input. Guard on
            # is-latest so an older FAILED attempt behind a newer one is
            # ignored; self-limiting (the transition moves the task off its
            # current state, so this does not refire next pass).
            elif (attempt.state == AttemptState.FAILED
                  and bool(tsf.attempts) and tsf.attempts[-1].attempt_id == attempt.attempt_id
                  and tsf.state not in TERMINAL_TASK_STATES):
                if attempt.role == Role.CARVER:
                    # Free the single carve slot; a later untargeted trigger
                    # re-carves if still below carve_ahead_target.
                    attempt_actions.append(Transition(
                        task_id=task_id, to=TaskState.SUPERSEDED,
                        notes="carve attempt failed (lease-race/spawn) -- freeing carve slot"))
                elif attempt.role == Role.IMPLEMENTER and tsf.state == TaskState.ACTIVE:
                    # The attempt never ran real work -> re-queue to re-dispatch
                    # once the transient lease/spawn condition clears. (The
                    # FAILED receipt still counts toward max_attempts today;
                    # excluding never-ran races is A8/M8's unified accessor.)
                    attempt_actions.append(Transition(
                        task_id=task_id, to=TaskState.QUEUED,
                        notes="implementer attempt failed to start (lease-race/spawn) -- re-queued"))

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
                # B24 2026-07-23 (D-R17, D-B24-2): a TRANSIENT-classified
                # attempt's backoff-not-yet-elapsed reading is a THIRD case
                # here -- neither "resume now" nor "genuine dead end" -- so
                # it must gate BOTH branches below, not just the
                # ResumeAttempt one: gating only the resume line would let a
                # merely-still-backing-off attempt fall through to the
                # BLOCKED branch and misreport a transient wait as "no
                # resume handle or attempts exhausted." Missing entries
                # default to True (today's ordinary INTERRUPTED attempts are
                # unaffected). Once the daemon's own resume count for this
                # attempt reaches MAX_TRANSIENT_RESUMES, the helper returns
                # False FOREVER -- daemon.py's _transient_escalate is what
                # actually moves the task off this dead attempt (pause
                # provider + requeue); this branch just correctly parks
                # meanwhile instead of BLOCKing a task that is really just
                # waiting its turn / about to be requeued.
                ready = inp.transient_backoff_ready.get(attempt.attempt_id, True)
                if not poisoned:
                    # unchanged (O2): today's ResumeAttempt-or-BLOCKED branch.
                    if (attempts_used(tsf) < inp.cfg.policy.max_attempts_per_task
                            and attempt.session_handle and ready):  # P60 (M8): was this same formula inline
                        attempt_actions.append(ResumeAttempt(task_id=task_id, attempt_id=attempt.attempt_id))
                    elif tsf.state == TaskState.ACTIVE and ready:
                        # P14 2026-07-15 item 4 (silent-dead-end fix): no resume
                        # handle, or the attempt budget is exhausted -- either
                        # way there is no path forward. The prior code silently
                        # did nothing here ("handled in lifecycle" was never
                        # true: lifecycle only dispatches QUEUED tasks, and
                        # nothing ever requeued this one) leaving the task
                        # ACTIVE forever with zero events. Surface it.
                        #
                        # P62 2026-07-20 (A10, M10): SCOPED to ACTIVE (implementer
                        # dead-end) only. For an AWAITING_REVIEW task whose review
                        # attempt is INTERRUPTED with no handle, the WAVE loop
                        # already plans a relaunch (has_review_in_flight is False
                        # for a no-handle INTERRUPTED review) -- emitting BLOCKED
                        # here too made a single pass plan BOTH a dead-end AND a
                        # LaunchReview for the same task, so execution blocked it
                        # and then wasted a frontier session on the now-BLOCKED
                        # task whose exit receipt is never consumed. Deferring to
                        # the wave loop (park here) removes the contradiction; a
                        # genuinely unrecoverable review still surfaces via the
                        # review budget / smart-triage, not a spurious block.
                        blocker = Blocker(
                            type=BlockerType.ENVIRONMENT,
                            unblock_condition="operator: inspect attempts",
                            detail="interrupted attempt has no resume handle or attempts are exhausted",
                        )
                        attempt_actions.append(Transition(task_id=task_id, to=TaskState.BLOCKED,
                                                           notes="interrupted-dead-end", blocker=blocker))
                    # else (e.g. AWAITING_REVIEW, or a transient attempt still
                    # backing off / at its resume cap awaiting the daemon's own
                    # escalation): park -- no dead-end here (M10 contradiction
                    # fix; B24 backoff/cap parking).
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
            # P65 2026-07-20 (M11): the age trigger must read the age of the
            # GENUINELY oldest waiting task. task_ids_to_batch is sorted by
            # task_id, so [0] is the lexicographically-first task -- which need
            # not be the oldest. An old task sorted after a fresh one would
            # otherwise never trip the timeout, stranding a review unboundedly
            # under low throughput. Take the minimum `since` across the batch.
            oldest_since = min(
                inp.states[t].since.timestamp() for t in task_ids_to_batch)
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
    # for ANY REVIEW_INDEPENDENT attempt in these states, including terminal
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
    # P61 2026-07-20 (A9, M3 -- real wave batching). A wave's review is ONE
    # frontier session over ALL its members, not one session per task. The
    # per-task in-flight recency check below is unchanged (it still decides,
    # task by task, "does THIS task still need a review launched") -- but the
    # tasks that pass it are GROUPED BY wave_id and launched together, so a
    # 3-task wave pays the ~35-40k frontier startup tax ONCE, not three times,
    # and (with the executor recording the shared attempt on every member) each
    # member's LATEST attempt is that review, so the next pass sees it in
    # flight for all of them and does not relaunch. The reject-loop still works
    # per task: a rejected member re-queues, gets a fresh implementer attempt
    # (its new LATEST, role IMPLEMENTER), and on returning to AWAITING_REVIEW
    # is the sole member of its own re-review launch -- preserving the
    # 2026-07-17 recency fix.
    needs_review_by_wave: dict[str, list[str]] = {}
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
                and latest.role == Role.REVIEW_INDEPENDENT
                and (
                    latest.state in (AttemptState.CREATED, AttemptState.PREFLIGHTING,
                                      AttemptState.RUNNING, AttemptState.STALLED,
                                      AttemptState.EXITED)
                    or (latest.state == AttemptState.INTERRUPTED
                        and latest.session_handle is not None)
                )
            )
            if not has_review_in_flight:
                needs_review_by_wave.setdefault(tsf.wave_id, []).append(task_id)

    # B6/P74 (D-R10): reviewer session-reuse. If the review_independent stage's
    # context declares "session-reuse", every review launched THIS pass resumes
    # the warmest prior review session -- the most-recent EXITED REVIEW_INDEPENDENT
    # attempt that captured a session_handle (max by `started`). A cold first wave
    # (no such prior) leaves this None -> the executor cold-dispatches, exactly as
    # before. Resuming an old/cache-expired session is harmless (a cache MISS, the
    # pre-B6 cost), and A7 binding is preserved by the executor stamping a fresh
    # attempt id -- so the only precondition is "a resumable review session
    # exists", nothing about which wave it reviewed. review_independent is serial, so
    # in practice at most one review is in flight and a second wave planned the
    # same pass simply shares the same warm handle (each still gets its own fresh
    # attempt id downstream).
    resume_session: str | None = None
    if needs_review_by_wave and "session-reuse" in stage_context("review_independent"):
        prior_review_sessions = [
            a for tsf in inp.states.values() for a in tsf.attempts
            if a.role == Role.REVIEW_INDEPENDENT
            and a.state == AttemptState.EXITED
            and a.session_handle
        ]
        if prior_review_sessions:
            resume_session = max(
                prior_review_sessions, key=lambda a: a.started).session_handle

    # One LaunchReview per wave, carrying all its members (sorted for a
    # deterministic plan / stable first_task anchor).
    for wave_id in sorted(needs_review_by_wave):
        wave_actions.append(
            LaunchReview(wave_id=wave_id, task_ids=sorted(needs_review_by_wave[wave_id]),
                         resume_session=resume_session))

    # === Self-review dispatch (B5 2026-07-20) ===
    # A SELF_REVIEWING task needs its self_review leg launched ONCE: a warm
    # resume of the implementer's session under Role.SELF_REVIEW. Mirrors the
    # wave review's in-flight recency guard -- a SELF_REVIEW attempt that is the
    # task's LATEST and still live/pending (or EXITED-pending-consumption) means
    # the leg is already running, so do not relaunch. The daemon reads the source
    # implementer attempt's session_handle; if absent it degrades to
    # AWAITING_REVIEW (skip). Inert unless the `self_review` stage is composed
    # into the pipeline -- nothing routes a task into SELF_REVIEWING otherwise, so
    # a pipeline without self_review plans byte-identically to today.
    self_review_actions: list[Action] = []
    for task_id, tsf in inp.states.items():
        if tsf.state != TaskState.SELF_REVIEWING:
            continue
        if inp.pause_mode == "drain-agents":
            # no NEW agent process while draining (a self-review IS one); the
            # task parks in SELF_REVIEWING until a later run/drain-handoffs pass.
            continue
        latest = tsf.attempts[-1] if tsf.attempts else None
        self_review_in_flight = (
            latest is not None
            and latest.role == Role.SELF_REVIEW
            and (
                latest.state in (AttemptState.CREATED, AttemptState.PREFLIGHTING,
                                 AttemptState.RUNNING, AttemptState.STALLED,
                                 AttemptState.EXITED)
                or (latest.state == AttemptState.INTERRUPTED
                    and latest.session_handle is not None)
            )
        )
        if self_review_in_flight:
            continue
        # the implementer attempt whose done-exit routed the task here -- its
        # warm session_handle is what the self-review borrows.
        source = next(
            (a for a in reversed(tsf.attempts) if a.role == Role.IMPLEMENTER), None)
        if source is None:
            continue  # unreachable in practice: SELF_REVIEWING is only entered via implement-done
        self_review_actions.append(
            LaunchSelfReview(task_id=task_id, source_attempt_id=source.attempt_id))

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
    # is True if that handler (or F018 P2b-A1's carver-session ladder,
    # slots 1-4/6 above) already used this pass's single carve slot --
    # P45 2026-07-19: at most one CarveDispatch is ever planned in a pass,
    # shared across both triggers (the single strategic carver is the sole
    # carve authority). P52 2026-07-19: not inp.project_paused added here
    # too -- see item 12's guard above for the live incident this closes.
    # (`carve_actions` itself is declared once, earlier, above item 12 --
    # F018 P2b-A1 -- so slot 6 can append to the same list.)

    # === Test-health carve (D-065, module contract item 15, B63 2026-07-20) ===
    # Evaluated BEFORE item 9's headroom refill DELIBERATELY: item 9's condition
    # (ready_count < carve_ahead_target) holds on essentially every pass of an
    # actively-draining project, so a seldom-run trigger placed after it would
    # lose the pass's single carve slot forever and its cadence would silently
    # never fire. Item 12's re-scope still precedes both (finishing started work
    # outranks starting new work of either kind). Same shared guards as item 9 --
    # never a second, independently-drifting copy of pause/in-flight/budget/route.
    test_health_interval = inp.cfg.policy.test_health_interval_days
    if (test_health_interval > 0
            and not inp.project_paused
            and not carve_in_flight
            and not carve_dispatch_planned
            and budget_allows
            and frontier_route_available):
        age = inp.days_since_test_health_carve
        if age is None or age >= test_health_interval:
            carve_actions.append(
                CarveDispatch(project=inp.cfg.project_id, kind="test-health")
            )
            carve_dispatch_planned = True

    # === Gap-audit carve (F007, module contract item 17, 2026-07-27) ===
    # Evaluated BEFORE item 9's headroom refill DELIBERATELY: same rationale as
    # item 15 (test-health). Item 9's condition (ready_count < carve_ahead_target)
    # holds on essentially every pass of an actively-draining project, so a
    # seldom-run trigger placed after it would lose the pass's single carve slot
    # forever and its cadence would silently never fire.
    gap_threshold = inp.cfg.policy.gap_audit_after_changed_lines
    if (gap_threshold > 0
            and not inp.project_paused
            and not carve_in_flight
            and not carve_dispatch_planned
            and budget_allows
            and frontier_route_available):
        changed = inp.changed_lines_since_gap_audit
        if changed is None or changed >= gap_threshold:
            carve_actions.append(
                CarveDispatch(project=inp.cfg.project_id, kind="gap-audit")
            )
            carve_dispatch_planned = True
            trace.note("carve", None, "gap-audit")

    # P03 (D-L5): the shared guard is restructured into an if/elif chain
    # SOLELY to attribute a carve-skip breadcrumb to its actual reason
    # (paused / in-flight) -- the overall truth table (enter the ready_count
    # computation iff paused is False AND carve_in_flight is False AND
    # carve_dispatch_planned is False) is byte-identical to the original
    # single `and`-chained condition it replaces.
    if inp.project_paused:
        trace.note("carve-skip", None, "paused")
    elif carve_in_flight:
        trace.note("carve-skip", None, "in-flight")
    elif not carve_dispatch_planned:
        ready_states = (TaskState.CARVED, TaskState.QUEUED, TaskState.NEEDS_DECISION)
        ready_count = 0
        for fm_id, (fm, _handoff_path) in inp.frontmatters.items():
            tsf = inp.states.get(fm_id)
            if tsf is None or tsf.state not in ready_states:
                continue
            if any(d in inp.decisions_open for d in fm.decision_deps()):
                trace.note("guard-exclude", fm_id, "decision-held")
                continue  # decision-held -- not admissible ready work
            ready_count += 1

        has_nonterminal_task = any(
            tsf.state not in TERMINAL_TASK_STATES for tsf in inp.states.values()
        )
        milestone_admits_work = has_nonterminal_task or (
            inp.cfg.policy.carve_ahead_target > 0 and not inp.roadmap_exhausted_open
        )
        # budget_allows computed once, shared with item 12 above.

        if (ready_count < inp.cfg.policy.carve_ahead_target
                and milestone_admits_work
                and budget_allows
                and frontier_route_available):
            carve_actions.append(CarveDispatch(project=inp.cfg.project_id))
            carve_dispatch_planned = True
            trace.note("carve", None, "headroom")

    # === Gate verify cadence (GA4 2026-07-25, module contract item 16) ===
    # Mirrors item 15's cadence SHAPE (interval > 0, not paused,
    # days_since_* None-or->=interval fires) but deliberately OUTSIDE the
    # single-carve-authority mutex above: a gate verify runs a subprocess
    # against a few disposable canary commits, needs no LLM frontier route
    # and no carve slot, so it never reads/sets carve_dispatch_planned and
    # never consults carve_in_flight / frontier_route_available /
    # budget_allows -- only project_paused gates it (a paused project starts
    # no new process of any kind, the same invariant item 14 closed for
    # carving). The daemon's executor is idempotent while a verify is
    # already running for this project (see daemon._execute_verify_gate), so
    # replanning the identical VerifyGate every pass until the drain step
    # appends GATE_VERIFY_RECORDED is harmless, not a runaway.
    gate_verify_actions: list[Action] = []
    gate_verify_interval = inp.cfg.policy.gate_verify_interval_days
    if gate_verify_interval > 0:
        if inp.project_paused:
            trace.note("gate-verify", None, "paused")
        else:
            gv_age = inp.days_since_gate_verify
            if gv_age is None or gv_age >= gate_verify_interval:
                gate_verify_actions.append(VerifyGate(project=inp.cfg.project_id))
                trace.note("gate-verify", None, "fire")

    # === Combine results in order ===
    actions = []
    for task_id in sorted(lifecycle_by_id.keys()):
        actions.extend(lifecycle_by_id[task_id])
    actions.extend(attempt_actions)
    actions.extend(wave_actions)
    actions.extend(self_review_actions)
    actions.extend(spec_actions)
    actions.extend(carve_actions)
    # F018 P2b-A1: new-vocabulary carver-session actions (mutually exclusive
    # with carve_actions by construction -- the shared carve_dispatch_planned
    # mutex guarantees at most one of the two lists is ever non-empty).
    # Always empty when carver_session is None (MASTER GATE), so this line
    # is a byte-identical no-op for every pre-P2b plan.
    actions.extend(carver_actions)
    # GA4 2026-07-25: the gate-verify cadence action, always empty when
    # policy.gate_verify_interval_days == 0 (the default) -- byte-identical
    # to every pre-GA4 plan whenever the feature is off.
    actions.extend(gate_verify_actions)

    # P62 2026-07-20 (A10, M10): whole-plan consistency guard (defense in depth
    # alongside the source fix in the INTERRUPTED branch). A single pass must
    # NEVER both dead-end a task (Transition -> BLOCKED) AND launch an agent for
    # it: executing both blocks the task and then wastes an agent session on the
    # now-BLOCKED task whose exit receipt is never consumed (an orphan attempt).
    # If any launch targets a task this same plan is blocking, drop that task
    # from the launch (and drop a launch left empty); keep the dead-end -- it is
    # the inspectable half. This guarantees the invariant regardless of which
    # producing branch introduced the contradiction.
    blocked_here = {
        a.task_id for a in actions
        if isinstance(a, Transition) and a.to == TaskState.BLOCKED
    }
    if blocked_here:
        deconflicted: list[Action] = []
        for a in actions:
            if isinstance(a, (DispatchImplementer, ResumeAttempt, LaunchSelfReview)) and a.task_id in blocked_here:
                continue  # drop: this task is being dead-ended this pass
            if isinstance(a, LaunchReview):
                keep = [t for t in a.task_ids if t not in blocked_here]
                if not keep:
                    continue  # every member was being blocked -> drop the launch
                if len(keep) != len(a.task_ids):
                    a = LaunchReview(wave_id=a.wave_id, task_ids=keep)
            deconflicted.append(a)
        actions = deconflicted

    return PlanResult(actions, trace=trace)


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
