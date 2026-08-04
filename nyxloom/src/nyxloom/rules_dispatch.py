"""Implementer dispatch (CR-06b): module contract item 3.

ONE rule: the QUEUED-task dispatch loop. It is not the only producer of
``DispatchImplementer`` -- the attempt ladder's P34 fresh-start re-cuts a
resume-poisoned attempt the same way -- but it is the only one that spends the
pass's dispatch CAPACITY, and that is what makes it PLAN-scoped rather than
TASK-scoped, and its own module rather than a sixth entry in
``rules_lifecycle.py``. The budget is shared ACROSS tasks; a per-task rule
would have to carry the running count somewhere between invocations, and
"somewhere" -- a hoisted local in a 1,160-line function -- is exactly what
CR-06 exists to remove.

WHAT IS SHARED AND WHERE IT LIVES. The capacity this rule spends
(:attr:`PlanContext.dispatch_capacity`) is derived ONCE, in the pass context,
from the implement stage's effective concurrency less the tasks already
occupying a slot. The eligibility predicate it applies
(:func:`reconcile.dispatch_eligible`) is the frozen contract's own nine-check
ladder, unchanged and still shared with the daemon's own checks. This module
adds no third copy of either.

DETERMINISM. Contract item 3 ends "in sorted task-id order (determinism)", and
that is a promise about the PLAN, not about the snapshot: ``inp.frontmatters``
is built by scanning a directory, so a rule that spent capacity in map order
would hand the pass's scarce implement slots out by filesystem accident. The
sort here is the frozen behaviour, preserved verbatim.

THE SKIP BREADCRUMB IS LOAD-BEARING. Every task the rule refuses records a
``dispatch-skip`` note carrying ``dispatch_eligible``'s own short reason
string. The CR-00 characterization corpus classifies waits from those strings
verbatim (``tests/test_core_characterization.py``), so the vocabulary is an
observable, not a debug aid.

CR-11b (2026-08-04) widens this rule's ``emits`` to include ``Transition``:
a QUEUED task ``dispatch_eligible`` refuses for ``'premise-drifted'`` (critique
I4 -- its stamped ``input_revision`` no longer matches current main) is parked
in NEEDS_DECISION rather than left silently waiting where every OTHER
ineligibility reason leaves it. Every other refusal reason is a condition that
resolves on its own (a pause lifts, a lease frees, budget resets, wip drains) --
``input_revision`` never spontaneously updates and ``main`` only ever advances,
so a silent skip here would refuse the SAME task every single pass forever
(the exact permanent-livelock CR-11a's own scoping named as why premise-drift
could not reuse its self-healing effect-boundary shape). Kept inside THIS
rule rather than a new lifecycle rule (module contract item 2's own DECISION
HOLDS text is D-dep-specific and stays untouched) because the precedence
falls out for free: ``dispatch_eligible``'s decision-hold check (3) runs
BEFORE its premise-drift check (4), so a task with an open D-dep reports
``'decision-hold:<D-id>'`` here, never ``'premise-drifted'`` -- item 2's own
rule already owns that task this pass, and this rule never double-transitions
it. No synthetic D-dep is declared: this is the same reason-less
NEEDS_DECISION park ``reject_triage``'s human-judgment escalations already
produce, which ``decision_hold``'s corrected release condition (2026-08-04)
leaves parked pending an explicit operator action, surfaced by ``doctor.py``'s
``decision-hold-unresolved`` check. Deliberately scoped to QUEUED only, not
the symmetric ACTIVE-resume case (rules_attempts.py's attempt ladder) -- an
in-flight attempt is not merely admission-gated, and reject_triage's existing
REVIEW_REJECTED-scoped drift check already catches it once that attempt
concludes; touching the resume ladder is its own, separately-scoped
follow-up.
"""

from __future__ import annotations

from .config import healthy_routes, undeclined_routes
from .planning import PlanContext, RuleEmitter
from .reconcile import DispatchImplementer, Transition, dispatch_eligible
from .types import TaskState


def implementer_dispatch(ctx: PlanContext, emit: RuleEmitter) -> None:
    """Contract item 3: dispatch eligible QUEUED tasks, up to capacity.

    The capacity budget is spent in sorted task-id order and stops at the
    first task that would exceed it -- NOT skipping over an expensive task to
    find a cheaper one, because "stop when full" is what makes the remaining
    queue's order meaningful on the next pass. A drifted task queued past the
    capacity cutoff is therefore not parked THIS pass either -- it is caught
    once the loop actually reaches it, the same eventual-consistency the
    capacity cutoff already gives every other eligibility reason.

    An eligible task with no healthy route in its tier plans nothing and
    records nothing: ``dispatch_eligible``'s check 9 already refuses that case,
    so reaching the route loop with no healthy route is unreachable in
    practice. The loop is written as a search for the FIRST healthy route
    because route order within a tier is the operator's declared preference.
    """
    inp = ctx.inp

    # Find all eligible QUEUED tasks, sorted by task_id
    queued_tasks = []
    for fm_id, (fm, handoff_path) in inp.frontmatters.items():
        if fm_id not in inp.states:
            continue
        tsf = inp.states[fm_id]
        if tsf.state == TaskState.QUEUED:
            queued_tasks.append((fm_id, fm, tsf))
    queued_tasks.sort(key=lambda x: x[0])  # Sort by task_id

    # Dispatch up to capacity (resolved once, in the pass context: the
    # implement stage's own concurrency -- a [stage.implement] override, else
    # policy.max_active_tasks -> parity -- less the tasks already occupying a
    # slot).
    dispatched = 0
    for fm_id, fm, tsf in queued_tasks:
        if dispatched >= ctx.dispatch_capacity:
            break

        eligible, reason = dispatch_eligible(fm, tsf, inp)
        if eligible:
            healthy = healthy_routes(inp.routes.for_tier(fm.tier), inp.provider_ok)
            # CR-09a: never reselect a route a reviewer already declared
            # incapable of this task's origin, even if it also appears in
            # this (bumped) tier's route list -- dispatch_eligible's check 9
            # applies the identical filter, so an eligible task's loop is
            # never emptied by this alone.
            healthy = undeclined_routes(healthy, inp.declined_routes.get(fm_id, frozenset()))
            if healthy:
                route_def = healthy[0]
                emit(DispatchImplementer(task_id=fm_id, route_id=route_def.route_id))
                dispatched += 1
                emit.note("dispatch", fm_id, f"route:{route_def.route_id}")
        else:
            emit.note("dispatch-skip", fm_id, reason)
            # CR-11b: premise-drift is the one ineligibility reason that
            # never self-clears (see module docstring) -- park it visibly
            # instead of silently re-refusing it every pass forever.
            if reason == 'premise-drifted':
                notes = (f"premise drifted: input_revision {fm.input_revision} "
                         f"!= main {(inp.head_revision or '')[:7]}; awaiting operator re-scope")
                emit(Transition(task_id=fm_id, to=TaskState.NEEDS_DECISION, notes=notes))
                emit.note("state-transition", fm_id, "QUEUED->NEEDS_DECISION")
