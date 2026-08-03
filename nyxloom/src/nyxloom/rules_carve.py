"""Carve authority (CR-06c): module contract items 9, 12, 14, 15, 17.

THE ONE EFFECT WHOSE OUTPUT IS A PLANNER INPUT, which is why every guard below
is about AUTHORITY rather than correctness. A dispatch that runs twice wastes
one agent turn on one task. A carve that runs twice corrupts the QUEUE: the
strategic carver mints new handoffs, so a second dispatch in the same pass
produces a second, independently-scoped set of tasks that the next pass then
plans against as if a human had written them. The operator was explicit that
the single strategic carver is the SOLE carve authority, and that is what the
six rules here contend for.

WHAT IS IN HERE, AND IN WHAT ORDER (the order is `planning.rule_table()`'s, not
this file's -- reading order below matches it only as a courtesy):

1. :func:`carver_session_ladder` -- F018 plan §3.3 priority 1-4 plus the
   already-in-flight reading. Admitting an already-produced proposal, or
   bootstrapping / recovering / repairing / feeding / compacting the persistent
   session, outranks starting any new carve;
2. :func:`ready_to_carve` -- contract item 12. Finishing already-started work
   (a rejected task the carver re-scopes) outranks starting new work;
3. :func:`carver_human_intake` -- plan §3.3 priority 6, queued human intake;
4. :func:`test_health_carve` -- contract item 15, the suite-debt cadence;
5. :func:`gap_audit_carve` -- contract item 17, the gap-engine cadence;
6. :func:`headroom_carve` -- contract items 9 and 14, the untargeted refill.

ORDER IS A REAL DESIGN CALL HERE, AND IT IS THE ONE THE CONTRACT ARGUES.
Item 9's condition -- the ready queue is below its target -- is true on
essentially every pass of an actively-draining project. So a seldom-run trigger
placed after it loses the pass's single carve slot indefinitely, and its
cadence silently never fires: not a delayed probe, a dead one. Items 15 and 17
each carry their own ORDERING paragraph saying so, and the headroom refill is
last for exactly that reason. Reordering the table is now a one-line reviewable
diff; before CR-06a it was 700 lines of function body.

MOVED VERBATIM. Every guard, every dedup flag and every incident-dated comment
travels with the code it explains. Two of those dates are load-bearing:

* item 14 exists because FOUR unauthorized carve dispatches fired against a
  PAUSED project (dstdns) in ~15 minutes on 2026-07-19. Carving was the one
  dispatch path in the planner that never consulted ``project_paused``;
* item 15's ordering exists because of the starvation argument above, found
  when the cadence was written rather than after it had failed to fire.

A guard whose reason has been separated from it is a guard the next author
deletes.

SINGLE CARVE AUTHORITY IS STRUCTURAL, NOT REMEMBERED. It used to be EIGHT
hand-written copies of ``and not carve_dispatch_planned``, so a ninth producer
that forgot the check would silently double-dispatch and no test could have
been written for a rule that did not exist yet. Now the ``carve-slot`` is an
exclusive resource the arbiter GRANTS: the six rules here declare the claim,
:data:`planning.EXCLUSIVE_ACTIONS` binds every carve-family action class to it,
and :meth:`planning.RuleEmitter.__call__` refuses the emission from a rule that
does not hold the grant. A second grant is unrepresentable.

CLAIM VERSUS RESERVE (CR-06c, the footgun this package owns). The ladder's
``STARTING``/``COMPACTING`` branch takes the slot and plans NOTHING, on
purpose: a carver turn is already in flight for this generation, so the pass's
carve authority is spent and every rule below must be blocked. That is correct,
so the arbiter cannot forbid it -- but it was, until this package, structurally
identical to a rule that took the grant and forgot to fire, which starves every
rule below it just as thoroughly and says nothing. The branch now calls
:meth:`planning.RuleEmitter.reserve` with its reason in writing; a rule that
:meth:`~planning.RuleEmitter.claim`\\ s and emits nothing fails the pass with
:class:`planning.UnusedGrant`.

WHY ``ready_to_carve`` IS ON THE LIFECYCLE CHANNEL AND ITS FIVE SIBLINGS ARE
NOT. Its ``CarveDispatch`` is attributed to the ORIGIN task whose context seeds
the re-scope packet (``task_id=chosen_id``, which since B7 DRIVES the daemon's
re-scope path rather than being informational), so it groups with that task's
other lifecycle actions. The other three ``CarveDispatch`` producers are
project-wide and carry ``task_id=None``. That difference is load-bearing:
``planning.PlanChannels.add`` keys the LIFECYCLE channel on ``task_id`` and
raises :class:`planning.TaskLessLifecycleAction` for a task-less action, so the
channel assignment is correct only because this rule emits ONLY inside the
branch that has a chosen id. The channel is declared per RULE and the property
that makes it safe is per EMISSION -- the two cannot be made to coincide by
declaration, so ``test_planning.py`` pins it instead.
"""

from __future__ import annotations

from .carver_session import CarverSessionSnapshot, CarverStatus
from .config import ProjectConfig
from .planning import PlanContext, RuleEmitter
from .reconcile import (
    Action, AdmitCarveProposal, CarveDispatch, CompactCarverSession,
    ResumeCarverSession, StartCarverSession,
)
from .types import TaskState, TERMINAL_TASK_STATES

# F018 P2b-A1 (plan-long-running-carver.md §6.2): pure compaction-trigger
# defaults, mirroring DEFAULT_ATTEMPT_MAX_WALL_SECONDS's getattr-fallback
# convention in `reconcile.py`. `config.CarveStageConfig` (shipped inert in P1,
# see `[stage.carve]`) already carries exactly these three knobs
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
    distinct from "turn-threshold (24)").

    MOVED with the ladder (CR-06c). It was the ladder's alone in
    `reconcile.py` too -- nothing else in the package, and nothing in the
    daemon, ever called it -- so it moves rather than staying behind as a
    shared helper that is not shared, which is how `reconcile.py` accreted
    in the first place.
    """
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


def carver_session_ladder(ctx: PlanContext, emit: RuleEmitter) -> None:
    """Carver-session ladder slots 1-4 (F018 P2b-A1, plan §3.3 priority 1-4).

    The FIRST claimant of the pass's carve slot: finishing or admitting an
    already-produced proposal, and bootstrapping, recovering, repairing,
    feeding or compacting the persistent strategic session, all outrank
    starting any new carve.

    Evaluated BEFORE item 12's READY_TO_CARVE re-scope -- plan §3.3: "never
    before a pending merge feed that may invalidate its premise."
    """
    # `inp` is the name the moved body uses; the shared guards are fields of
    # the pass context rather than hoisted locals of a 1,160-line function, so
    # "the EXACT SAME predicate item 9 uses, computed once and shared" is
    # structural instead of a request in the contract's prose.
    inp = ctx.inp
    # WHAT THIS RULE DECIDED, kept in two locals instead of the monolith's one
    # `carve_dispatch_planned` boolean. The flag could not tell "a turn is
    # already in flight, so the slot is deliberately spent" from "an action was
    # planned", and it could LEAK into other rules; these cannot, and the
    # epilogue turns each into the arbiter call that says which one it was.
    carver_actions: list[Action] = []
    reserved: str | None = None

    # Shares carve_in_flight / frontier_route_available / budget_allows /
    # project_paused with items 9, 12, 15 and 17 (same context fields, never a
    # second copy) and the SAME single carve authority: a new-vocabulary carver
    # action and an old CarveDispatch are mutually exclusive in one pass --
    # whichever rule the table reaches first wins.
    if (inp.carver_session is not None and ctx.carve_stage_present
            and not inp.project_paused and not ctx.carve_in_flight
            and ctx.frontier_route_available and ctx.budget_allows):
        snap = inp.carver_session
        status = snap.status

        if status in (CarverStatus.STARTING, CarverStatus.COMPACTING):
            # §2.4: "planner emits no second carver turn" (STARTING) / "all
            # intake/feed/carve work waits" (COMPACTING) -- a turn is
            # already effectively in flight for this generation, so this
            # pass's single carver slot is considered consumed: neither a
            # new-vocabulary action NOR the legacy CarveDispatch triggers
            # (item 12/15/17/9 below) fire this pass.
            #
            # THE DELIBERATE claim-and-plan-nothing, and the only one in the
            # planner. It is a RESERVATION (CR-06c), so the arbiter's ledger
            # records the slot as consumed-on-purpose with this reason -- where
            # a rule that merely forgot to fire records an unspent promise and
            # fails the pass.
            reserved = f"wait:{status.value.lower()}"
            emit.note("carver", None, reserved)
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
            emit.note("carver", None, "admit")
        elif status in (CarverStatus.ABSENT, CarverStatus.COLD, CarverStatus.ROTATING):
            # Slot 2: cold bootstrap / new-generation launch.
            carver_actions.append(StartCarverSession(project=inp.cfg.project_id))
            emit.note("carver", None, "start")
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
                emit.note("carver", None, "recover")
            # else: recovery budget exhausted -- leave DEGRADED for the
            # daemon/operator (A2+ territory); this package plans no
            # action and does NOT consume the slot (neither a claim nor a
            # reservation), so item 12/15/17/9 may still run this pass.
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
                emit.note("carver", None, "repair-proposal")
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
                emit.note("carver", None, "merge-feed")
            else:
                trigger = _compaction_due(snap, inp.cfg)
                if trigger is not None:
                    # Slot 4: due compaction.
                    carver_actions.append(CompactCarverSession(
                        project=inp.cfg.project_id, generation=snap.generation,
                        trigger=trigger,
                    ))
                    emit.note("carver", None, f"compact:{trigger}")
            # WARM with no pending repair/feed and no compaction due plans
            # nothing here and consumes nothing -- item 12's READY_TO_CARVE
            # re-scope (slot 5) gets its chance next.

    # --- arbitration epilogue ----------------------------------------------
    # The ladder is the FIRST claimant in the rule table, so neither call can
    # be refused today -- but both still go through the arbiter, because the
    # emitter refuses any carve-family action from a rule that does not hold
    # the grant, and because WHICH of the two calls was made is the record
    # that tells a reader whether this pass's slot was spent on an action or
    # spent on purpose.
    if reserved is not None:
        emit.reserve("carve-slot", reserved)
    elif carver_actions:
        emit.claim("carve-slot")
        for action in carver_actions:
            emit(action)


def ready_to_carve(ctx: PlanContext, emit: RuleEmitter) -> None:
    """Module contract item 12 (P45 2026-07-19): the READY_TO_CARVE handler.

    Sorted task-id order for determinism -- if multiple tasks are
    simultaneously READY_TO_CARVE, only the lowest task_id gets this pass's
    single carve slot; the rest stay in READY_TO_CARVE, picked up on a later
    pass.

    P52 2026-07-19 (live incident, dstdns): neither this handler nor item 9's
    untargeted trigger ever checked project_paused -- a "paused" project
    (drain-handoffs OR drain-agents) still got a NEW carve dispatch fired
    against it every pass ready_count sat below carve_ahead_target, completely
    bypassing the pause. 4 unauthorized carve dispatches fired against dstdns
    in ~15 minutes before this was caught live. dispatch_eligible (regular
    implementer dispatch) and item 13 (guarded-automatic merge) already both
    consult project_paused; carving was the one dispatch path in this module
    that never did. It reads the SHARED guard (never a second,
    independently-drifting copy) -- which is now a field of the pass context
    rather than a local of a 1,160-line function.

    F018 P2b-A1: the carver-session ladder above may already have used this
    pass's single carver slot (an admission, bootstrap, recovery, feed or
    compaction outranks a re-scope in plan §3.3's priority order), so this
    trigger asks the arbiter whether the slot is still free -- the check that
    used to be `not carve_dispatch_planned`.

    THE ONE CARVE RULE ON THE LIFECYCLE CHANNEL. It emits ONLY inside the
    `if ready_to_carve_ids:` branch, so its CarveDispatch always names the
    origin task -- which is what makes a channel grouped and sorted by task id
    a legal home for it. See the module docstring.
    """
    inp = ctx.inp
    if (emit.available("carve-slot") and not inp.project_paused
            and not ctx.carve_in_flight
            and ctx.frontier_route_available and ctx.budget_allows):
        ready_to_carve_ids = [
            task_id for task_id in ctx.sorted_task_ids
            if inp.states[task_id].state == TaskState.READY_TO_CARVE
        ]
        if ready_to_carve_ids:
            chosen_id = ready_to_carve_ids[0]
            emit.claim("carve-slot")
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
            emit(CarveDispatch(project=inp.cfg.project_id, task_id=chosen_id))
            emit.note("carve", chosen_id, "ready-to-carve")


def carver_human_intake(ctx: PlanContext, emit: RuleEmitter) -> None:
    """Carver-session ladder slot 6 (plan §3.3 priority 6): queued human
    intake.

    Evaluated AFTER item 12's re-scope but BEFORE item 15 (test-health),
    item 17 (gap-audit) and item 9 (headroom refill), matching plan §3.3's
    explicit order exactly. Same shared guards as slots 1-4; the
    slot-availability check is REQUIRED here (unlike slots 1-4, which run
    first and only ever take the grant) because item 12 immediately above may
    have just used this pass's single slot.
    """
    inp = ctx.inp
    if (inp.carver_session is not None and ctx.carve_stage_present
            and emit.available("carve-slot")
            and not inp.project_paused and not ctx.carve_in_flight
            and ctx.frontier_route_available and ctx.budget_allows
            and inp.carver_session.status is CarverStatus.WARM
            and inp.pending_human_intakes):
        # Arrival (event_sequence) order, not intake_id sort order -- same
        # rationale as slot 3's merge-feed ordering above. source_ids= is
        # §4.2's authoritative arg name (plan §3.2's "source_sequences" is a
        # stale cross-reference -- do not rename toward it).
        ids = tuple(i.intake_id for i in
                    sorted(inp.pending_human_intakes, key=lambda i: i.event_sequence))
        emit.claim("carve-slot")
        emit(ResumeCarverSession(
            project=inp.cfg.project_id, mode="targeted-intake",
            source_ids=ids, generation=inp.carver_session.generation,
        ))
        emit.note("carver", None, "targeted-intake")


def test_health_carve(ctx: PlanContext, emit: RuleEmitter) -> None:
    """Module contract item 15 (D-065, B63 2026-07-20): the test-health carve
    cadence.

    A seldom-run, project-WIDE sibling of item 9: it steps back from per-task
    work entirely and asks the strategic carver to evaluate the SUITE's
    standing test debt.

    ORDER: evaluated BEFORE item 9's headroom refill, DELIBERATELY. Item 9's
    condition (ready_count < carve_ahead_target) holds on essentially every
    pass of an actively-draining project, so a seldom-run trigger placed after
    it would lose the pass's single carve slot forever and its cadence would
    silently never fire. That argument now lives on this rule's table entry as
    well, where it can be reviewed without reading this body.
    """
    inp = ctx.inp
    test_health_interval = inp.cfg.policy.test_health_interval_days
    if (test_health_interval > 0
            and not inp.project_paused
            and not ctx.carve_in_flight
            and emit.available("carve-slot")
            and ctx.budget_allows
            and ctx.frontier_route_available):
        age = inp.days_since_test_health_carve
        if age is None or age >= test_health_interval:
            emit.claim("carve-slot")
            emit(CarveDispatch(project=inp.cfg.project_id, kind="test-health"))


def gap_audit_carve(ctx: PlanContext, emit: RuleEmitter) -> None:
    """Module contract item 17 (F007 2026-07-27, gap-engine): the gap-audit
    carve cadence.

    Evaluates whether features the project CLAIMS are shipped are actually
    backed by code reality, surfacing gaps as carve candidates. ACTIVITY-
    counted, not time-counted: an idle project with zero code changes must not
    accrue carve budget on an unchanged codebase.

    ORDER: after item 15's test-health and before item 9's headroom refill --
    the same starvation argument item 15's docstring makes.
    """
    inp = ctx.inp
    gap_threshold = inp.cfg.policy.gap_audit_after_changed_lines
    if (gap_threshold > 0
            and not inp.project_paused
            and not ctx.carve_in_flight
            and emit.available("carve-slot")
            and ctx.budget_allows
            and ctx.frontier_route_available):
        changed = inp.changed_lines_since_gap_audit
        if changed is None or changed >= gap_threshold:
            emit.claim("carve-slot")
            emit(CarveDispatch(project=inp.cfg.project_id, kind="gap-audit"))
            emit.note("carve", None, "gap-audit")


def headroom_carve(ctx: PlanContext, emit: RuleEmitter) -> None:
    """Module contract items 9 and 14: the untargeted headroom-refill carve.

    Counts ADMISSIBLE ready work -- a task with an open D-dep is decision-held
    and is not ready work regardless of its nominal state -- and asks the
    carver to refill when the queue is below target.

    ORDER: the LAST claimant of the carve slot, and that is load-bearing
    rather than incidental. Its condition holds on essentially every pass of
    an actively-draining project, so every rarer trigger has to precede it or
    starve.

    P03 (D-L5): the shared guard is an if/elif chain SOLELY to attribute a
    carve-skip breadcrumb to its actual reason (paused / in-flight) -- the
    overall truth table (enter the ready_count computation iff paused is False
    AND carve_in_flight is False AND the carve slot is still free) is
    byte-identical to the single `and`-chained condition it replaces.

    THE `guard-exclude` BREADCRUMBS ARE NOT SORTED, and must not be. They are
    recorded in `inp.frontmatters` iteration order -- the daemon's directory
    scan -- while every ACTION this rule can plan is order-independent
    (`ready_count` is a sum). CR-06c leaves it exactly as found: the
    differential's breadcrumb check is a SUBSEQUENCE check against the frozen
    baseline, so re-ordering this stream would drop breadcrumbs relative to it
    and fail as a lost trace. It is a real (minor) order-dependence in the
    TRACE and it is reported rather than repaired here, because repairing it
    in isolation buys a channel nobody reads a determinism nobody asked for at
    the cost of a declared divergence.
    """
    inp = ctx.inp
    if inp.project_paused:
        emit.note("carve-skip", None, "paused")
    elif ctx.carve_in_flight:
        emit.note("carve-skip", None, "in-flight")
    elif emit.available("carve-slot"):
        ready_states = (TaskState.CARVED, TaskState.QUEUED, TaskState.NEEDS_DECISION)
        ready_count = 0
        for fm_id, (fm, _handoff_path) in inp.frontmatters.items():
            tsf = inp.states.get(fm_id)
            if tsf is None or tsf.state not in ready_states:
                continue
            if any(d in inp.decisions_open for d in fm.decision_deps()):
                emit.note("guard-exclude", fm_id, "decision-held")
                continue  # decision-held -- not admissible ready work
            ready_count += 1

        has_nonterminal_task = any(
            tsf.state not in TERMINAL_TASK_STATES for tsf in inp.states.values()
        )
        milestone_admits_work = has_nonterminal_task or (
            inp.cfg.policy.carve_ahead_target > 0 and not inp.roadmap_exhausted_open
        )

        if (ready_count < inp.cfg.policy.carve_ahead_target
                and milestone_admits_work
                and ctx.budget_allows
                and ctx.frontier_route_available):
            emit.claim("carve-slot")
            emit(CarveDispatch(project=inp.cfg.project_id))
            emit.note("carve", None, "headroom")
