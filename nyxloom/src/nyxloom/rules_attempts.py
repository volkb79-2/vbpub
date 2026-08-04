"""The attempt-recovery ladder (CR-06b): module contract item 4.

THE MOST INCIDENT-DENSE CODE IN THE PLANNER, and the reason it moves as a
whole rather than being reshaped on the way. Its branches are not a taxonomy
somebody designed; they are the sediment of named production incidents -- P14,
P15, P32, P34, P54, P60, P62/M10, B5, B24, F019 P1b -- and every guard below
carries the dated comment that says which one taught it. Those comments travel
with the code they explain; a guard whose reason has been separated from it is
a guard the next author deletes.

WHAT THE LADDER IS. For each attempt of each non-terminal task, exactly one
branch fires, in this fixed precedence:

1. a RECEIPT exists and the attempt (or the task) has not caught up with it
   -> ``EmitAttemptExit``. The clause list is long because "which receipts are
   consumable" was widened five separate times by five separate strandings;
2. no receipt and the pid is DEAD -> ``MarkInterrupted``;
3. no receipt, pid alive, past the WALL-CLOCK CAP -> ``InterruptAttempt``,
   unconditionally, bypassing the stall gate entirely (P14 item 6);
4. already STALLED -> ``InterruptAttempt``;
5. RUNNING with a quiet log -> ``StallCheck``, or ``MarkStalled`` once the
   daemon's tier-2 evidence confirms it (P14 item 2);
6. FAILED and latest -> the task is moved off its non-terminal state (P54);
7. INTERRUPTED -> the P34 poisoned-resume decision table, gated by P15's
   drain-agents park and B24's transient backoff.

Precedence is expressed as an ``if``/``elif`` chain over one attempt, so it is
total and mutually exclusive by construction -- which is the property that
makes "exactly one branch fires" checkable by reading rather than by trusting.

SORTED TASK-ID ORDER (CR-06b, the repair this package owns).
``plan_project``'s contract promises determinism and contract item 3 spells it
"sorted task-id order"; the ladder was the one rule that walked
``inp.states.items()`` RAW. The daemon builds ``inp.states`` by scanning a
directory, so the attempt channel's action order followed the filesystem.
CR-06a pinned that gap rather than closing it, because closing it moves real
corpus plans; CR-06b closes it and DECLARES the divergence
(``tests/test_planner_differential.py``'s ``KNOWN_DIVERGENCES``), where it is
pinned as exactly a within-channel block reordering that preserves the action
multiset. Nothing else about the ladder changed: sorting reorders the per-task
BLOCKS and never the decisions inside one.

The order comes from :attr:`PlanContext.sorted_task_ids` -- the pass's one
shared sort, not a second copy of it.
"""

from __future__ import annotations

from .config import healthy_routes
from .planning import PlanContext, RuleEmitter
from .reconcile import (
    DispatchImplementer, EmitAttemptExit, InterruptAttempt, MarkInterrupted,
    MarkStalled, ResumeAttempt, StallCheck, Transition, _wall_clock_cap_exceeded,
    attempts_used, fresh_start_eligible, implementer_record_count,
)
from .types import (
    AttemptState, Blocker, BlockerType, Role, TaskState, TERMINAL_TASK_STATES,
)

#: Attempt states in which the attempt is still nominally executing, so a
#: receipt, a dead pid, a blown wall-clock cap or a quiet log each mean
#: something. Named once because five branches key on it.
_INTERRUPTIBLE_STATES = (
    AttemptState.RUNNING, AttemptState.PREFLIGHTING, AttemptState.STALLED,
)


def attempt_ladder(ctx: PlanContext, emit: RuleEmitter) -> None:
    """Contract item 4: the attempt-recovery ladder, in sorted task-id order.

    PLAN-scoped: it reads every task's attempts, and its BLOCKED transitions
    are what the arbiter's deconfliction step later reconciles against every
    launch the same pass planned (P62 2026-07-20, A10/M10).
    """
    inp = ctx.inp

    for task_id in ctx.sorted_task_ids:
        tsf = inp.states[task_id]
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
                emit(EmitAttemptExit(task_id=task_id, attempt_id=attempt.attempt_id))

            # No receipt, pid dead -> MarkInterrupted
            elif attempt.state in _INTERRUPTIBLE_STATES and not alive:
                emit(MarkInterrupted(task_id=task_id, attempt_id=attempt.attempt_id))

            # P14 2026-07-15 item 6: no receipt, pid alive, but the attempt
            # has been running longer than its wall-clock cap -> interrupt
            # UNCONDITIONALLY, regardless of log activity (bypasses the
            # log-quiet/stall-confirm gate below entirely).
            elif (attempt.state in _INTERRUPTIBLE_STATES and alive
                  and _wall_clock_cap_exceeded(attempt, fm_for_task, inp)):
                emit(InterruptAttempt(task_id=task_id, attempt_id=attempt.attempt_id))

            # Already tier-2-confirmed stalled (ATTEMPT_STALLED already
            # emitted a prior pass) and still no receipt -> interrupt now.
            elif attempt.state == AttemptState.STALLED:
                emit(InterruptAttempt(task_id=task_id, attempt_id=attempt.attempt_id))

            # Stall handling: no receipt, pid alive, log quiet > threshold
            elif attempt.state == AttemptState.RUNNING and alive:
                log_quiet = inp.log_quiet_seconds.get(attempt.attempt_id)
                if log_quiet is not None and log_quiet > inp.cfg.policy.stall_log_quiet_seconds:
                    if inp.stall_confirmed.get(attempt.attempt_id, False):
                        # P14 2026-07-15 item 2: make the confirmed stall
                        # VISIBLE (ATTEMPT_STALLED) before ever interrupting;
                        # InterruptAttempt now only fires once the attempt's
                        # persisted state is actually STALLED (branch above).
                        emit(MarkStalled(task_id=task_id, attempt_id=attempt.attempt_id))
                    else:
                        emit(StallCheck(task_id=task_id, attempt_id=attempt.attempt_id))

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
                    emit(Transition(
                        task_id=task_id, to=TaskState.SUPERSEDED,
                        notes="carve attempt failed (lease-race/spawn) -- freeing carve slot"))
                elif attempt.role == Role.IMPLEMENTER and tsf.state == TaskState.ACTIVE:
                    # The attempt never ran real work -> re-queue to re-dispatch
                    # once the transient lease/spawn condition clears. (The
                    # FAILED receipt still counts toward max_attempts today;
                    # excluding never-ran races is A8/M8's unified accessor.)
                    emit(Transition(
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
                        emit(ResumeAttempt(task_id=task_id, attempt_id=attempt.attempt_id))
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
                        emit(Transition(task_id=task_id, to=TaskState.BLOCKED,
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
                        emit(Transition(task_id=task_id, to=TaskState.BLOCKED,
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
                            healthy = healthy_routes(
                                inp.routes.for_tier(fm_for_task.tier), inp.provider_ok)
                            if healthy:
                                emit(DispatchImplementer(
                                    task_id=task_id, route_id=healthy[0].route_id))
