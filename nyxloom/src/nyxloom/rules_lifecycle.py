"""Lifecycle planning rules (CR-06a): module contract items 1, 2, 10, 11, 13.

These are the rules that move a task through its states without contending for
any arbitrated resource. Every one of them is SELF-LIMITING in the same way:
the transition it plans moves the task off the state the rule matches on, so
it fires once per occasion rather than every tick. That is the property that
lets them be small pure functions with no memory -- there is nothing to
remember, because the state machine remembers.

They are also mutually exclusive by construction: each keys on a different
``TaskState``, so at most one of them plans anything for a given task in a
given pass. The kernel runs them inside ONE shared pass over the handoffs
(``RuleScope.TASK``), which is what preserves the per-task interleaving of
actions and breadcrumbs the monolith produced.

CR-06a is a decomposition, not a redesign: every guard, dedup flag and
incident note below travels verbatim with the code it explains.
"""

from __future__ import annotations

from .planning import PlanContext, PlanTask, RuleEmitter
from .reconcile import (
    AutoMergeTask, CreateTask, LaunchGateDiagnosis, RunPostMergeGate,
    Transition, _premise_drifted, attempts_used,
)
from .types import TaskState


def new_handoffs(ctx: PlanContext, emit: RuleEmitter, task: PlanTask) -> None:
    """Contract item 1: a frontmatter id with no statefile -> CreateTask."""
    if task.state is None:
        emit(CreateTask(task_id=task.task_id, fm=task.fm,
                        handoff_path=task.handoff_path))


def queue_admission(ctx: PlanContext, emit: RuleEmitter, task: PlanTask) -> None:
    """Contract item 1's second half: CARVED -> QUEUED once lint is clean."""
    if task.state is None:
        return
    if task.state.state == TaskState.CARVED and ctx.inp.lint_clean.get(task.task_id, False):
        emit(Transition(task_id=task.task_id, to=TaskState.QUEUED, notes=None))
        emit.note("state-transition", task.task_id, "CARVED->QUEUED")


def decision_hold(ctx: PlanContext, emit: RuleEmitter, task: PlanTask) -> None:
    """Contract item 2: a QUEUED task with an unresolved D-dep parks in
    NEEDS_DECISION; a NEEDS_DECISION task whose D-deps all resolved returns to
    QUEUED. The notes name the D-ids (enum/id vocabulary only -- module
    contract item 8's payload-injection rule).

    SELF-CORRECT (found scoping CR-11b, 2026-08-04): the release condition
    used to be a bare ``not open_d_deps`` -- true both when this task's
    declared D-deps are now all resolved (the genuine "hold released" case)
    AND when the task never declared any D-dep at all. ``fm.decision_deps()``
    is static frontmatter, read fresh every pass and never mutated by any
    transition, so a task with NO D-dep declared could never have been
    parked here by THIS rule's own entry branch (which requires
    ``open_d_deps`` to be non-empty to fire) -- it must have arrived via a
    DIFFERENT rule. The only other producer of a `NEEDS_DECISION` transition
    is ``reject_triage``'s human-judgment escalations ("product" / an
    architectural-or-drift rejection with no carve stage / attempts
    exhausted with no carve stage) -- and the old bare condition silently
    reversed every one of them on the VERY NEXT pass, re-dispatching work a
    reviewer explicitly said needed a human decision, with no operator
    visibility and no way to have prevented it (those escalations never
    declare a D-dep, so there was nothing to keep them parked). Requiring
    ``task.fm.decision_deps()`` to be non-empty restricts release to tasks
    THIS rule genuinely parked -- a reason-less park (reject_triage's shape)
    now stays parked, correctly requiring an operator to act on it rather
    than being silently, mechanically undone."""
    if task.state is None:
        return
    declared_d_deps = task.fm.decision_deps()
    open_d_deps = [d for d in declared_d_deps if d in ctx.inp.decisions_open]

    if task.state.state == TaskState.QUEUED and open_d_deps:
        notes = ", ".join(open_d_deps)
        emit(Transition(task_id=task.task_id, to=TaskState.NEEDS_DECISION, notes=notes))
        emit.note("state-transition", task.task_id, "QUEUED->NEEDS_DECISION")
    elif (task.state.state == TaskState.NEEDS_DECISION and declared_d_deps
          and not open_d_deps):
        emit(Transition(task_id=task.task_id, to=TaskState.QUEUED, notes=None))
        emit.note("state-transition", task.task_id, "NEEDS_DECISION->QUEUED")


def reject_triage(ctx: PlanContext, emit: RuleEmitter, task: PlanTask) -> None:
    """Contract item 10: what happens to a REVIEW_REJECTED task.

    SELF-CORRECT 2026-07-16 (bug 2 of the review-verdict + reject-loop
    package): REVIEW_REJECTED had NO handler at all. The state machine permits
    REVIEW_REJECTED->QUEUED (types.py TASK_TRANSITIONS) and the reject CLI/UI
    imply re-work, but nothing here ever planned it, so a rejected task
    STRANDED forever (required a manual re-queue by an operator). Mirrors the
    attempts-budget accounting dispatch_eligible already uses (exclude LIMIT
    receipts, same formula as the daemon's own ERROR-path count) -- attempts
    remaining -> re-queue for another implementer pass; this is self-limiting
    the same way CARVED->QUEUED is: once applied, tsf.state is QUEUED and this
    branch no longer matches on the next pass, so it fires once per rejection,
    not every tick.

    P45 2026-07-19 (closes the KNOWN GAP the pre-P45 code left here): the
    exhausted-budget half of this handoff's original contract asked for
    REVIEW_REJECTED -> BLOCKED with a typed blocker (mirroring the
    INTERRUPTED-exhausted typed-blocker path elsewhere in the planner). That
    specific transition is NOT legal: types.py's
    TASK_TRANSITIONS[REVIEW_REJECTED] is {QUEUED, READY_TO_CARVE,
    NEEDS_DECISION, SUPERSEDED, CANCELLED} -- BLOCKED is absent -- so planning
    it would raise TransitionError the moment the daemon executed it
    (check_task_transition runs for BOTH TASK_TRANSITIONED and TASK_BLOCKED
    events -- storage.apply_event, no bypass; verified empirically), and
    types.py is FROZEN CORE. READY_TO_CARVE IS a legal edge in that same
    frozen table, and the two absence bugs turned out to be the same gap:
    routing the exhausted case there, and giving READY_TO_CARVE a real handler
    (module contract item 12) that re-dispatches the SAME single strategic
    carver, closes both at once with zero new dispatch machinery.
    """
    if task.state is None or task.state.state != TaskState.REVIEW_REJECTED:
        return
    fm_id, fm, tsf = task.task_id, task.fm, task.state

    # F019 P1b 2026-07-25 (gate-failure diagnosis routing): a pre-merge/
    # mutation gate failure ALSO lands a task here (daemon.py "... gate
    # failed; not published"), but carries no reviewer class, so without
    # this it always falls into the blind mechanical-retry branch below.
    # When the daemon flags this task gate-diagnosis-pending (gate-caused,
    # unclassified, streak >= policy.gate_diagnosis_after_failures, none
    # in flight), dispatch the warm reviewer in gate-diagnosis mode to
    # CLASSIFY the failure and SUPPRESS the blind retry this pass (the
    # early return) -- once the reviewer's REVIEW_RECORDED{reject_class}
    # lands, the task re-enters this rule UNflagged and the triage table
    # below routes it exactly as a review rejection already is. Skipped
    # under drain-agents (no new agent process of any kind, mirroring the
    # wave-review pause rule); the task parks REVIEW_REJECTED for a later
    # run/drain-handoffs pass. The task NEVER leaves REVIEW_REJECTED during
    # diagnosis -- un-gated code can never reach the merge path.
    if fm_id in ctx.inp.gate_diagnosis_pending:
        if not ctx.drain_agents:
            emit(LaunchGateDiagnosis(task_id=fm_id))
            emit.note("gate-diagnosis", fm_id, "dispatch")
        else:
            emit.note("gate-diagnosis", fm_id, "skip:drain-agents")
        return
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
    tclass = ctx.inp.triage_class.get(fm_id)
    has_carve = ctx.carve_stage_present
    drifted = _premise_drifted(fm.input_revision, ctx.inp.head_revision)
    if tclass == "product":
        emit(Transition(
            task_id=fm_id, to=TaskState.NEEDS_DECISION,
            notes="review rejected -- triage: product decision required; escalating to operator",
        ))
    elif drifted and has_carve:
        emit(Transition(
            task_id=fm_id, to=TaskState.READY_TO_CARVE,
            notes=(f"review rejected -- stale premise (input_revision "
                   f"{fm.input_revision} != main {ctx.inp.head_revision[:7]}); routed for re-carve"),
        ))
    elif tclass in ("architectural", "incapable") and has_carve:
        emit(Transition(
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
        emit(Transition(
            task_id=fm_id, to=TaskState.NEEDS_DECISION,
            notes=f"review rejected -- triage: {reason}; no carve stage, escalating to operator",
        ))
    # P60 (M8): attempts_used counts only IMPLEMENTER attempts -- the
    # old role-blind formula here counted the review attempt too, so a
    # reject cycle burned 2 units and exhausted the task a rejection early.
    elif attempts_used(tsf) < ctx.inp.cfg.policy.max_attempts_per_task:
        emit(Transition(
            task_id=fm_id, to=TaskState.QUEUED,
            notes="review rejected -- re-queued for re-work (attempt budget remains)",
        ))
    elif has_carve:
        # Attempts exhausted, and the pipeline has a carve stage: route
        # to READY_TO_CARVE so item 12's handler re-dispatches it to the
        # single strategic carver for a fresh, re-scoped package instead
        # of stranding it forever.
        emit(Transition(
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
        emit(Transition(
            task_id=fm_id, to=TaskState.NEEDS_DECISION,
            notes="review rejected -- attempt budget exhausted; no carve stage, escalating to operator",
        ))


def post_merge_validation(ctx: PlanContext, emit: RuleEmitter, task: PlanTask) -> None:
    """Contract item 11: MERGED -> VALIDATING is pure bookkeeping
    (self-limiting, same pattern as CARVED->QUEUED); VALIDATING itself just
    re-emits the trigger every pass until the daemon's execution of
    RunPostMergeGate transitions the task onward to COMPLETED or BLOCKED.

    D-060 (B2, docs/spec-flow-stages.md): the VALIDATING handler honours the
    composed pipeline. `post_merge_gate` in the pipeline (the default, and
    every project with no `pipeline` key -> parity) -> RunPostMergeGate exactly
    as before. A pipeline that omits the gate stage (e.g. the `lean` preset)
    auto-advances VALIDATING -> COMPLETED immediately -- the frozen graph
    already permits that edge, and MERGED still transits through VALIDATING
    first (never MERGED -> COMPLETED directly), so no new transition edge is
    introduced."""
    if task.state is None:
        return
    if task.state.state == TaskState.MERGED:
        emit(Transition(task_id=task.task_id, to=TaskState.VALIDATING,
                        notes="post-merge validation started"))
    elif task.state.state == TaskState.VALIDATING:
        if "post_merge_gate" in ctx.inp.cfg.pipeline:
            emit(RunPostMergeGate(task_id=task.task_id))
        else:
            emit(Transition(task_id=task.task_id, to=TaskState.COMPLETED,
                            notes="no post_merge_gate stage -- auto-validated"))


def guarded_merge(ctx: PlanContext, emit: RuleEmitter, task: PlanTask) -> None:
    """Contract item 13 (P48 2026-07-19): a MERGE_READY task only ever gets
    here via an 'approved' review verdict (daemon.py's REVIEW_RECORDED-
    consuming branch is the sole writer of this transition), so no separate
    verdict check is needed -- reaching this state already IS the guard. The
    remaining guard this module CAN express purely is project_paused: if the
    runaway watchdog (or an operator) has paused the project for ANY reason
    (drain-handoffs or drain-agents), guarded-automatic must not merge blind --
    unattended merging during a flagged condition is exactly backwards from
    the safety intent. Self-limiting like every other item here: the daemon's
    execution of AutoMergeTask transitions the task off MERGE_READY (to MERGED
    on success, to a NEEDS_OPERATOR escalation on a real git conflict -- see
    daemon.py's _execute_auto_merge), so this does not refire for the same task
    once acted on."""
    if task.state is None:
        return
    if (task.state.state == TaskState.MERGE_READY
            and ctx.inp.cfg.policy.merge_mode == "guarded-automatic"
            and not ctx.inp.project_paused):
        emit(AutoMergeTask(task_id=task.task_id))
        emit.note("merge", task.task_id, "auto-merge")
