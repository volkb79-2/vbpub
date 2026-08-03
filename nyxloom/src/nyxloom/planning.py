"""The planning kernel: the pass context, the rule vocabulary, and the arbiter.

WHY THIS MODULE EXISTS (CR-06a, DR-06)
--------------------------------------
``reconcile.plan_project`` was one 1,160-line function. Everything about it
that matters -- which decision outranks which, which facts are shared, which
decisions contend for the same scarce resource -- was expressed as STATEMENT
ORDER and hoisted local variables inside that function. Three consequences the
redesign is here to remove:

* a shared fact is a local, so a new branch re-derives it. The module contract
  has to SAY "computed ONCE and shared -- not a second, independently-drifting
  version" (items 9/12/14) because nothing structural says it;
* priority is line number. The contract's items 12/15/17/9 carry a paragraph
  each explaining that their relative order is a real design call -- a rare
  trigger placed after item 9's near-always-true condition would starve -- and
  the only way to review that claim was to read 700 lines of function body;
* the single-carve-authority invariant was maintained by EIGHT hand-written
  copies of ``and not carve_dispatch_planned``. A ninth producer that forgot
  the check would silently double-dispatch the strategic carver, and no test
  could have been written that would fail for a rule that does not exist yet.

THE FOUR PIECES
---------------
:class:`PlanContext`
    The facts a pass derives from its ``ReconcileInput``, derived ONCE, before
    any rule runs. Frozen, so a rule cannot smuggle state to a later rule
    through it. Every fact that two rules share lives here exactly once, which
    is what makes "the exact same predicate item 9 uses" a structural fact
    rather than a comment asking the next author to be careful.

:class:`RuleSpec`
    What one rule IS: the numbered contract items it implements, its concern,
    where its actions land in the plan, the action types it may emit, and the
    exclusive resources it may claim. ``emits`` is checked STRUCTURALLY by
    ``tests/test_planning.py``, which walks the rule's own call graph with
    ``ast`` and compares the action classes it can construct against what it
    declared -- over-broad fails exactly like missing. A declaration that is
    not verified is decoration.

:func:`rule_table`
    The ordered rule table, as DATA. Each entry carries the rationale for its
    own position, so the ordering argument the contract makes in prose is
    reviewable next to the ordering itself. Reordering the tuple is the whole
    edit; there is no function body to re-read.

:class:`PlanArbiter`
    Exclusive resources are GRANTED, not remembered. A rule declares it needs
    the ``carve-slot``; the arbiter grants it at most once per pass; and an
    action whose type is resource-bound (:data:`EXCLUSIVE_ACTIONS`) cannot be
    emitted by a rule that does not hold the grant. The parent's acceptance --
    "two rules cannot authorize the same exclusive resource in one pass" --
    holds because a second grant is UNREPRESENTABLE, not because every author
    remembered the flag. The arbiter also owns the whole-plan consistency step
    (:meth:`PlanArbiter.deconflict`): a plan must never both dead-end a task
    and launch an agent for it, and that is a property of the PLAN, so no
    single rule can own it.

THE LEGACY RATCHET
------------------
CR-06a moved the kernel and the concerns that claim no arbitrated resource:
lifecycle routing, review waves, and the attention/cadence probes. CR-06b moved
dispatch and the attempt ladder. Carve authority is CR-06c's. Until it moves,
those rules are registered with :attr:`RuleSpec.legacy_owner` and their bodies
stay in ``reconcile.py`` -- and ``tests/test_planning.py`` holds the count to
:data:`LEGACY_RULE_BUDGET` in BOTH directions. Over budget means new debt;
under budget means debt repaid without recording it. Same ratchet shape as
``effects.LEGACY_HANDLER_BUDGET`` and ``exception_census.LEGACY_BUDGET``, for
the same reason: a number that only goes down survives a package boundary
where a hand-maintained list does not.

The legacy carve rules claim the ``carve-slot`` THROUGH the arbiter in THIS
package, before their internals move. An arbiter with no claimant would be
decoration, and the invariant it exists to hold is exactly the one the legacy
rules were the eight copies of.

PURITY
------
Rules perform no I/O, no clock read, no logging, no environment read and no
global mutation, and that is a STRUCTURAL test rather than a claim: the oracle
in ``tests/test_planning.py`` walks each rule's call graph across package
modules and fails on any reachable logging call, clock read, environment read,
file or subprocess operation, or store append. :meth:`PlanContext.derive` is a
root of that walk too -- an impure context derivation would make the pass
impure whatever the rules do.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from functools import lru_cache
from typing import Any, Callable

from .stages import effective_concurrency, stage_context
from .types import Role, TaskState, TERMINAL_TASK_STATES

#: Rules whose bodies still live in ``reconcile.py`` because the concern they
#: implement belongs to a later package. CR-06c owns what is left: carve
#: authority (items 9, 12, 14, 15, 17 and the carver-session ladder).
#:
#: This number may only go DOWN, and it must go down in the same commit that
#: moves the rule. See the module docstring. 8 -> 6 in CR-06b, which moved
#: implementer dispatch and the attempt ladder (contract items 3 and 4).
LEGACY_RULE_BUDGET = 6

#: The exclusive resources a rule may contend for. A resource is a thing at
#: most one rule may be granted per pass; naming it here is what lets the
#: arbiter refuse an undeclared claim rather than inventing a resource on
#: first use.
#:
#: ``carve-slot`` is the single strategic carver's authority to be asked for
#: work this pass. The operator was explicit that it is the SOLE carve
#: authority, so every producer of a carve-family action contends for this one
#: grant -- including the "a turn is already effectively in flight" reading in
#: the carver-session ladder, which consumes the slot without emitting an
#: action at all.
KNOWN_RESOURCES: frozenset[str] = frozenset({"carve-slot"})

#: Action class name -> the exclusive resource its emission requires. This is
#: what makes the single-carve-authority invariant structural: a rule that
#: emits one of these without holding the grant raises
#: :class:`UnclaimedResource`, so a NEW producer that forgets the check fails
#: loudly on its first pass instead of quietly launching a second carver.
#:
#: Keyed by class NAME rather than by class so this module never imports
#: ``reconcile`` -- the kernel must not depend on the vocabulary its rules
#: happen to emit, and the ``emits`` oracle derives names from source anyway.
EXCLUSIVE_ACTIONS: dict[str, str] = {
    "CarveDispatch": "carve-slot",
    "AdmitCarveProposal": "carve-slot",
    "StartCarverSession": "carve-slot",
    "ResumeCarverSession": "carve-slot",
    "CompactCarverSession": "carve-slot",
}


class Channel(Enum):
    """Where a rule's actions land in the returned plan.

    The plan's output order is part of its contract (``plan_project``'s own
    docstring: lifecycle first, sorted by task id, then attempts, then waves,
    then attention, then carve). Making the channel a per-RULE declaration
    rather than a per-ACTION-TYPE one is deliberate: the same
    ``DispatchImplementer`` class is a lifecycle action when the dispatch rule
    plans it and an attempt action when the fresh-start path re-cuts a
    poisoned attempt, and that difference is a property of the decision, not
    of the action.
    """

    LIFECYCLE = "lifecycle"
    ATTEMPT = "attempt"
    WAVE = "wave"
    SELF_REVIEW = "self-review"
    SPEC = "spec"
    CARVE = "carve"
    CARVER = "carver"
    GATE_VERIFY = "gate-verify"


#: Assembly order of the channels. The LIFECYCLE channel is additionally
#: grouped by task id and sorted, which is why it is keyed separately below.
CHANNEL_ORDER: tuple[Channel, ...] = (
    Channel.LIFECYCLE, Channel.ATTEMPT, Channel.WAVE, Channel.SELF_REVIEW,
    Channel.SPEC, Channel.CARVE, Channel.CARVER, Channel.GATE_VERIFY,
)


class RuleScope(Enum):
    """How often a rule is invoked in a pass.

    ``TASK`` rules are invoked once per handoff, by the KERNEL's own loop --
    never by their own. That is what lets several lifecycle concerns be
    separate rules without changing the order their actions and breadcrumbs
    appear in: a run of consecutive TASK rules shares ONE pass over the
    handoffs, so the per-task interleaving the monolith produced is preserved
    exactly.
    """

    TASK = "task"
    PLAN = "plan"


class UnknownResource(Exception):
    """A rule named a resource the kernel does not know about."""


class UndeclaredClaim(Exception):
    """A rule claimed a resource its spec does not declare.

    Distinct from losing a contested claim: the rule may be perfectly entitled
    to the resource, but its DECLARATION is what a reviewer reads to know
    which rules contend, so an undeclared claim makes the declaration a lie.
    """


class UndeclaredEmission(Exception):
    """A rule emitted an action type its spec does not declare."""


class UnclaimedResource(Exception):
    """A rule emitted a resource-bound action without holding the grant.

    This is the failure a ninth carve producer gets instead of a silent second
    dispatch of the single strategic carver.
    """


class DuplicateRule(Exception):
    """Two rules registered under one name."""


class TaskLessLifecycleAction(Exception):
    """A LIFECYCLE-channel action named no task, so it has no plan position.

    See :meth:`PlanChannels.add`. Named rather than left as the ``TypeError``
    that ``sorted()`` would eventually raise, because the two say completely
    different things to the author who hits it.
    """


# ---------------------------------------------------------------------------
# the pass context


@dataclass(frozen=True)
class PlanTask:
    """One handoff, as a TASK-scoped rule sees it.

    ``state`` is None for a handoff with no statefile yet -- the new-handoff
    rule's whole condition, and the reason every other task rule begins by
    refusing that case.
    """

    task_id: str
    fm: Any
    handoff_path: str
    state: Any | None


@dataclass(frozen=True)
class PlanContext:
    """The facts of one pass, derived ONCE from the input snapshot.

    Frozen for two reasons. It stops a rule handing state to a later rule
    through the context (the arbiter is the only sanctioned channel for
    cross-rule coupling, and it records who holds what). And it makes the
    "computed once and shared" discipline the module contract asks for in
    prose into something a reader can verify by looking at one dataclass:
    if a fact is not here, no two rules can be sharing it.

    ``inp`` rides along because rules genuinely need the snapshot -- the
    per-task maps, the policy, the routes. What may NOT happen is a rule
    re-deriving one of the fields below from it.
    """

    #: The snapshot this pass plans against. Never mutated by planning.
    inp: Any

    # -- carve authority (contract items 9, 12, 14, 15, 17) -----------------
    #: A carver attempt is already in flight: any non-terminal task carrying
    #: an Attempt with role CARVER. The carve slot, observed rather than
    #: granted -- a pass cannot take back the authority a previous pass spent.
    carve_in_flight: bool
    #: A healthy route exists for the carver's own tier (the frontier-review
    #: role). Item 3's no-healthy-route rule, applied to the carver: a project
    #: with no review/carve infrastructure configured must never spuriously
    #: dispatch one.
    frontier_route_available: bool
    #: ``budget_remaining`` is unset or positive. The same stop every dispatch
    #: path in the planner honours: no budget left, no new agent process.
    budget_allows: bool
    #: The composed pipeline includes a carve stage. ONE field for what the
    #: monolith held as two locals -- ``carver_pipeline_gate`` for the carver
    #: ladder and ``has_carve`` for the reject-triage routing -- which the
    #: contract already required to be "the EXACT SAME predicate ... never a
    #: second, independently-drifting copy".
    carve_stage_present: bool

    # -- pause modes (contract item 5's P15 note, item 14) ------------------
    #: ``pause_mode == "drain-agents"``: no NEW agent process of any kind may
    #: start. Distinct from ``project_paused`` (true for BOTH drain modes),
    #: which gates dispatch, carve and merge instead.
    drain_agents: bool

    # -- dispatch capacity (contract item 3) --------------------------------
    #: Tasks occupying an implement slot right now (ACTIVE or AWAITING_REVIEW).
    active_count: int
    #: The implement stage's resolved concurrency: a ``[stage.implement]``
    #: override, else ``policy.max_active_tasks``. Resolved HERE, once, because
    #: the resolver is the one shared helper the planner reaches that is not
    #: itself part of the planner.
    implement_concurrency: int
    #: How many more implementer dispatches this pass may plan.
    dispatch_capacity: int

    # -- review stage context (contract item 5) -----------------------------
    #: The ``review_independent`` stage's declared packet-assembly context
    #: flags, read once from the stage registry.
    review_stage_context: frozenset
    #: Whether that stage declares ``session-reuse`` -- the reviewer resumes
    #: its warmest prior review session for prompt-cache hits.
    review_session_reuse: bool

    # -- ordering (determinism) ---------------------------------------------
    #: Every task id with a statefile, sorted. The planner's determinism rule
    #: is "sorted task-id order" in four separate places; this is that order,
    #: derived once.
    sorted_task_ids: tuple[str, ...]

    @classmethod
    def derive(cls, inp: Any) -> "PlanContext":
        """Derive the pass's shared facts from its snapshot.

        Pure, and held to that structurally: this method is a root of the
        purity oracle's call-graph walk, so an impure derivation fails the
        same test an impure rule does. It was NOT pure before CR-06a -- the
        concurrency resolver it calls logged on every invocation, twice per
        pass plus once per queued task, which made the planner's advertised
        "no logger reachable from this module" false in a way nobody noticed
        for four packages.
        """
        carve_in_flight = any(
            tsf.state not in TERMINAL_TASK_STATES
            and any(a.role is Role.CARVER for a in tsf.attempts)
            for tsf in inp.states.values()
        )
        frontier_routes = inp.routes.for_role(Role.REVIEW_INDEPENDENT.value)
        frontier_route_available = any(
            inp.provider_ok.get(r.route_id, False) for r in frontier_routes
        )
        active_count = sum(
            1 for tsf in inp.states.values()
            if tsf.state in (TaskState.ACTIVE, TaskState.AWAITING_REVIEW)
        )
        implement_concurrency = effective_concurrency(
            "implement", inp.cfg.stage_overrides, inp.cfg.policy.max_active_tasks)
        review_context = frozenset(stage_context("review_independent"))
        return cls(
            inp=inp,
            carve_in_flight=carve_in_flight,
            frontier_route_available=frontier_route_available,
            budget_allows=inp.budget_remaining is None or inp.budget_remaining > 0,
            carve_stage_present="carve" in inp.cfg.pipeline,
            drain_agents=inp.pause_mode == "drain-agents",
            active_count=active_count,
            implement_concurrency=implement_concurrency,
            dispatch_capacity=implement_concurrency - active_count,
            review_stage_context=review_context,
            review_session_reuse="session-reuse" in review_context,
            sorted_task_ids=tuple(sorted(inp.states)),
        )

    def tasks(self) -> list[PlanTask]:
        """Every handoff, in snapshot order, as TASK rules see it.

        Snapshot order, NOT sorted order: the lifecycle channel is grouped by
        task id and sorted at assembly, so iteration order cannot affect the
        plan -- and the permutation test in ``tests/test_planning.py`` is what
        proves that rather than asserting it.
        """
        return [
            PlanTask(task_id=fm_id, fm=fm, handoff_path=path,
                     state=self.inp.states.get(fm_id))
            for fm_id, (fm, path) in self.inp.frontmatters.items()
        ]


# ---------------------------------------------------------------------------
# the rule vocabulary


@dataclass(frozen=True)
class RuleSpec:
    """What one planning rule is, and what it is allowed to do."""

    #: Stable name for explanations and for the arbiter's grant ledger.
    #: Independent of the function name so renaming a function does not
    #: silently rewrite recorded history.
    name: str
    #: The numbered ``reconcile`` module-contract item(s) this rule
    #: implements. The contract is the frozen interface; a rule that
    #: implements none of it has no business in the pass.
    contract_items: tuple[int, ...]
    #: The lifecycle concern this rule belongs to -- the grouping the parent
    #: plan's work item 1 asks for.
    concern: str
    scope: RuleScope
    channel: Channel
    #: ``(ctx, emit)`` for a PLAN rule, ``(ctx, emit, task)`` for a TASK rule.
    rule: Callable[..., None]
    #: Action class NAMES this rule may emit. Verified structurally against
    #: the rule's own call graph -- see the module docstring. Names rather
    #: than classes so the kernel never imports the action vocabulary.
    emits: frozenset[str]
    #: Exclusive resources this rule may claim from the arbiter.
    claims: frozenset[str] = frozenset()
    #: Why this rule sits where it does in the table. Load-bearing for the
    #: entries whose ORDER is a real design call rather than an accident.
    rationale: str = ""
    #: Set to the owning package while the rule's body still lives in
    #: ``reconcile.py``. Empty means it lives in a rule module.
    #: See :data:`LEGACY_RULE_BUDGET`.
    legacy_owner: str = ""

    def __post_init__(self) -> None:
        if not self.contract_items:
            raise ValueError(f"{self.name}: a rule must name the contract item(s) it implements")
        if not self.rationale:
            raise ValueError(f"{self.name}: a rule must state why it sits where it does")
        unknown = self.claims - KNOWN_RESOURCES
        if unknown:
            raise UnknownResource(f"{self.name}: unknown resource(s) {sorted(unknown)}")
        # A rule that can emit a resource-bound action MUST declare the claim.
        # Without this the declaration would be optional exactly where it
        # matters: the emit-time check below would still refuse the action,
        # but only on the pass that first reaches that branch.
        for action_name in sorted(self.emits):
            needed = EXCLUSIVE_ACTIONS.get(action_name)
            if needed is not None and needed not in self.claims:
                raise UndeclaredClaim(
                    f"{self.name}: emits {action_name}, which requires the "
                    f"{needed!r} resource, but does not claim it")


class PlanArbiter:
    """Grants exclusive resources, and deconflicts the finished plan.

    ONE grant per resource per pass, held in a dict keyed by resource. That
    dict is the whole enforcement: a second grant is not refused by a check
    someone remembered to write, it is unrepresentable. What the monolith did
    instead -- a ``carve_dispatch_planned`` boolean consulted by eight
    hand-written copies of the same ``and not`` -- is exactly the shape that
    cannot survive a ninth producer.
    """

    def __init__(self, specs: tuple[RuleSpec, ...] = ()) -> None:
        self._granted: dict[str, str] = {}
        seen: set[str] = set()
        for spec in specs:
            if spec.name in seen:
                raise DuplicateRule(f"two rules named {spec.name!r}")
            seen.add(spec.name)

    def available(self, resource: str) -> bool:
        """Whether ``resource`` is still ungranted this pass.

        A read, not a claim. Rules that must decide something BEFORE they know
        whether they will fire (the headroom trigger computes an admissible
        ready-count, and records why it excluded each task, before it knows it
        wants the slot) ask this; taking the grant early and not using it
        would starve every later claimant.
        """
        self._require_known(resource)
        return resource not in self._granted

    def grant(self, spec: RuleSpec, resource: str) -> bool:
        """Grant ``resource`` to ``spec``, or refuse because it is taken."""
        self._require_known(resource)
        if resource not in spec.claims:
            raise UndeclaredClaim(
                f"{spec.name}: claimed {resource!r}, which its spec does not declare")
        if resource in self._granted:
            return False
        self._granted[resource] = spec.name
        return True

    def holder(self, resource: str) -> str | None:
        """The rule holding ``resource``, or None."""
        self._require_known(resource)
        return self._granted.get(resource)

    def grants(self) -> dict[str, str]:
        """The pass's grant ledger -- resource -> the rule that holds it."""
        return dict(self._granted)

    @staticmethod
    def _require_known(resource: str) -> None:
        if resource not in KNOWN_RESOURCES:
            raise UnknownResource(f"unknown exclusive resource {resource!r}")

    # -- cross-rule consistency ---------------------------------------------

    #: Actions that launch an agent process for the task they name.
    LAUNCHES_FOR_TASK: frozenset[str] = frozenset({
        "DispatchImplementer", "ResumeAttempt", "LaunchSelfReview",
    })
    #: Actions that launch one agent process for a SET of tasks.
    LAUNCHES_FOR_TASKS: frozenset[str] = frozenset({"LaunchReview"})

    def deconflict(self, actions: list) -> list:
        """Drop launches for tasks this same plan dead-ends.

        A property of the PLAN, not of any rule, which is why it lives with
        the arbiter rather than in whichever rule happened to notice: a single
        pass must NEVER both dead-end a task (a Transition to BLOCKED) and
        launch an agent for it. Executing both blocks the task and then wastes
        an agent session on the now-BLOCKED task whose exit receipt is never
        consumed -- an orphan attempt (P62 2026-07-20, A10/M10).

        The dead-end is KEPT: it is the inspectable half. A wave launch left
        with no members is dropped entirely.
        """
        blocked_here = {
            a.task_id for a in actions
            if type(a).__name__ == "Transition" and a.to == TaskState.BLOCKED
        }
        if not blocked_here:
            return actions
        deconflicted: list = []
        for a in actions:
            kind = type(a).__name__
            if kind in self.LAUNCHES_FOR_TASK and a.task_id in blocked_here:
                continue  # drop: this task is being dead-ended this pass
            if kind in self.LAUNCHES_FOR_TASKS:
                keep = [t for t in a.task_ids if t not in blocked_here]
                if not keep:
                    continue  # every member was being blocked -> drop the launch
                if len(keep) != len(a.task_ids):
                    # Rebuilt with wave_id and members ONLY, which drops
                    # `resume_session` -- so a trimmed wave cold-launches
                    # where the untrimmed one would have resumed the warm
                    # reviewer session. Preserved verbatim from P62: it is a
                    # prompt-cache miss, not a correctness change, and CR-06a
                    # changes no planning policy. Named here rather than
                    # silently fixed, because "the deconfliction step also
                    # quietly changes how a launch is dispatched" is exactly
                    # the kind of thing a reader should not have to infer
                    # from a constructor call.
                    a = type(a)(wave_id=a.wave_id, task_ids=keep)
            deconflicted.append(a)
        return deconflicted


@dataclass(frozen=True)
class RuleMatch:
    """One action, attributed to the rule that decided it.

    The parent plan's work item 3 -- "actions plus structured rule-match
    explanations". A breadcrumb says WHY in a fixed vocabulary a reader has to
    already know; this says WHICH RULE and which numbered contract item
    produced a given action, which is the question an operator staring at an
    unexpected plan actually has.
    """

    rule: str
    concern: str
    contract_items: tuple[int, ...]
    action_kind: str
    task_id: str | None


class PlanChannels:
    """The actions a pass has planned, by channel.

    The LIFECYCLE channel is grouped by task id because the plan's contract is
    "task lifecycle actions, sorted by task id" -- grouping here (rather than
    sorting a flat list at the end) is what keeps a task's actions adjacent.
    """

    def __init__(self) -> None:
        self.lifecycle: dict[str, list] = {}
        self.other: dict[Channel, list] = {
            ch: [] for ch in CHANNEL_ORDER if ch is not Channel.LIFECYCLE
        }

    def add(self, channel: Channel, action: Any) -> None:
        if channel is Channel.LIFECYCLE:
            task_id = getattr(action, "task_id", None)
            if task_id is None:
                # CR-06b (CR-06a review). The LIFECYCLE channel is GROUPED BY
                # TASK ID and sorted, so a lifecycle action that names no task
                # has no defined position in the plan. The monolith could not
                # produce one -- it keyed on the frontmatter id it was
                # processing -- and no rule can today. Refused HERE, by name,
                # rather than left to die inside `sorted(self.lifecycle)` with
                # a TypeError about NoneType and str: the fix for this is a
                # design decision (does a task-less lifecycle action sort
                # first, last, or belong in another channel?), and a rule
                # author deserves to be told that rather than to debug a sort.
                raise TaskLessLifecycleAction(
                    f"{type(action).__name__} was planned into the LIFECYCLE "
                    f"channel with no task_id; that channel is grouped and "
                    f"sorted by task id, so the action has no position")
            self.lifecycle.setdefault(task_id, []).append(action)
        else:
            self.other[channel].append(action)

    def assemble(self) -> list:
        actions: list = []
        for task_id in sorted(self.lifecycle):
            actions.extend(self.lifecycle[task_id])
        for channel in CHANNEL_ORDER:
            if channel is not Channel.LIFECYCLE:
                actions.extend(self.other[channel])
        return actions


class RuleEmitter:
    """The only way a rule reaches the pass it is part of.

    A rule gets one of these, bound to its own spec. Everything it can do --
    plan an action, record a breadcrumb, contend for a resource -- goes
    through here, which is what lets every emission be checked against the
    declaration and attributed to its rule without the rule cooperating.
    """

    def __init__(self, spec: RuleSpec, arbiter: PlanArbiter,
                 channels: PlanChannels, trace: Any) -> None:
        self._spec = spec
        self._arbiter = arbiter
        self._channels = channels
        self._trace = trace

    def __call__(self, action: Any) -> None:
        """Plan ``action``.

        Refuses an action type the spec does not declare, and refuses a
        resource-bound action from a rule that does not hold the grant. The
        second refusal is the structural half of the single-carve-authority
        invariant: a boolean flag can only catch a producer that CONSULTS it.
        """
        kind = type(action).__name__
        if kind not in self._spec.emits:
            raise UndeclaredEmission(
                f"{self._spec.name}: emitted {kind}, which its spec does not declare")
        resource = EXCLUSIVE_ACTIONS.get(kind)
        if resource is not None and self._arbiter.holder(resource) != self._spec.name:
            raise UnclaimedResource(
                f"{self._spec.name}: emitted {kind}, which requires the "
                f"{resource!r} resource, without holding the grant "
                f"(held by {self._arbiter.holder(resource)!r})")
        self._channels.add(self._spec.channel, action)
        self._trace.match(RuleMatch(
            rule=self._spec.name, concern=self._spec.concern,
            contract_items=self._spec.contract_items, action_kind=kind,
            task_id=getattr(action, "task_id", None)))

    def note(self, kind: str, task_id: str | None, detail: str) -> None:
        """Record a breadcrumb, attributed to this rule."""
        self._trace.note(kind, task_id, detail, rule=self._spec.name)

    def claim(self, resource: str) -> bool:
        """Take the pass's grant for ``resource``, or lose it to an earlier rule."""
        return self._arbiter.grant(self._spec, resource)

    def available(self, resource: str) -> bool:
        """Whether ``resource`` is still free. See :meth:`PlanArbiter.available`."""
        return self._arbiter.available(resource)


def run_rules(ctx: PlanContext, table: tuple[RuleSpec, ...], trace: Any,
              arbiter: PlanArbiter) -> PlanChannels:
    """Run every rule in table order and collect what they plan.

    Consecutive TASK-scoped rules share ONE pass over the handoffs. That is
    not an optimisation: the monolith evaluated its lifecycle concerns
    per-task inside a single loop, so a task's actions and its breadcrumbs
    interleaved by TASK first and by concern second. Running each rule to
    completion over all tasks instead would reorder the breadcrumb stream, and
    the differential's subsequence check would (correctly) call that a lost
    trace.

    A rule NAME is an identity here, not a label, so uniqueness is enforced
    where names become identities rather than only in
    :meth:`PlanArbiter.__init__`. The arbiter's grant ledger is keyed by name
    and :meth:`RuleEmitter.__call__` authorises an exclusive emission by
    comparing the holder's name to the emitting spec's -- so two specs sharing
    a name are ONE identity to the arbiter, and the second would emit under
    the first's grant. That is a second carve authority in a single pass: the
    exact invariant this kernel exists to make unrepresentable, reachable by
    copy-pasting a table entry and forgetting to change ``name=``. Checking it
    only in the arbiter's constructor made the invariant depend on the CALLER
    passing the table there, which ``run_rules``'s own signature does not
    require.
    """
    channels = PlanChannels()
    emitters: dict[str, RuleEmitter] = {}
    for spec in table:
        if spec.name in emitters:
            raise DuplicateRule(
                f"two rules named {spec.name!r} -- a name is the arbiter's "
                f"identity for a rule, so the second would emit under the "
                f"first's grant")
        emitters[spec.name] = RuleEmitter(spec, arbiter, channels, trace)
    index = 0
    while index < len(table):
        spec = table[index]
        if spec.scope is RuleScope.PLAN:
            spec.rule(ctx, emitters[spec.name])
            index += 1
            continue
        batch = []
        while index < len(table) and table[index].scope is RuleScope.TASK:
            batch.append(table[index])
            index += 1
        for task in ctx.tasks():
            for task_spec in batch:
                task_spec.rule(ctx, emitters[task_spec.name], task)
    return channels


# ---------------------------------------------------------------------------
# the ordered rule table


@lru_cache(maxsize=1)
def rule_table() -> tuple[RuleSpec, ...]:
    """The pass's rules, in evaluation order, with the rationale for that order.

    THE ORDER IS THE DESIGN. The module contract argues this at length for the
    carve family (items 12/15/17/9) and the argument is real: item 9's
    condition -- the ready queue is below its target -- holds on essentially
    every pass of an actively-draining project, so a seldom-run trigger placed
    after it would lose the single carve slot indefinitely and its cadence
    would silently never fire. Encoding that as a tuple with a rationale per
    entry is the whole point of this function: reordering it is a reviewable
    one-line diff, where reordering 700 lines of function body was not.

    The rule MODULES are imported here rather than at module scope, and this
    is the one place in the kernel that names them. The kernel must not depend
    on its rules -- a rule module imports ``planning`` for the vocabulary and
    ``reconcile`` for the action classes, so a module-level import here would
    make the package's import graph cyclic and its load order significant.
    Deferring it to first use keeps the graph a DAG in every direction.
    """
    from . import (
        reconcile, rules_attempts, rules_attention, rules_dispatch,
        rules_lifecycle, rules_review,
    )

    return (
        # === lifecycle: one shared pass over the handoffs ===================
        RuleSpec(
            name="new-handoffs", contract_items=(1,), concern="lifecycle",
            scope=RuleScope.TASK, channel=Channel.LIFECYCLE,
            rule=rules_lifecycle.new_handoffs,
            emits=frozenset({"CreateTask"}),
            rationale=(
                "First: a handoff with no statefile has no state for any other "
                "rule to read, so every rule below begins by refusing it."),
        ),
        RuleSpec(
            name="queue-admission", contract_items=(1,), concern="lifecycle",
            scope=RuleScope.TASK, channel=Channel.LIFECYCLE,
            rule=rules_lifecycle.queue_admission,
            emits=frozenset({"Transition"}),
            rationale=(
                "CARVED -> QUEUED. Position among the lifecycle rules is free: "
                "they key on mutually exclusive task states, so at most one "
                "fires per task per pass."),
        ),
        RuleSpec(
            name="decision-hold", contract_items=(2,), concern="lifecycle",
            scope=RuleScope.TASK, channel=Channel.LIFECYCLE,
            rule=rules_lifecycle.decision_hold,
            emits=frozenset({"Transition"}),
            rationale=(
                "QUEUED <-> NEEDS_DECISION. Runs BEFORE dispatch so a task "
                "moved to NEEDS_DECISION this pass is never also dispatched; "
                "dispatch_eligible's own decision-hold check makes that "
                "belt-and-braces rather than load-bearing."),
        ),
        RuleSpec(
            name="reject-triage", contract_items=(10,), concern="lifecycle",
            scope=RuleScope.TASK, channel=Channel.LIFECYCLE,
            rule=rules_lifecycle.reject_triage,
            emits=frozenset({"LaunchGateDiagnosis", "Transition"}),
            rationale=(
                "REVIEW_REJECTED routing. The four-class precedence INSIDE the "
                "rule is the ordering that matters here (most-terminal first); "
                "its position among the lifecycle rules is free for the same "
                "disjoint-state reason as the others."),
        ),
        RuleSpec(
            name="post-merge-validation", contract_items=(11,), concern="lifecycle",
            scope=RuleScope.TASK, channel=Channel.LIFECYCLE,
            rule=rules_lifecycle.post_merge_validation,
            emits=frozenset({"RunPostMergeGate", "Transition"}),
            rationale="MERGED -> VALIDATING -> gate. Disjoint states; position free.",
        ),
        RuleSpec(
            name="guarded-merge", contract_items=(13,), concern="lifecycle",
            scope=RuleScope.TASK, channel=Channel.LIFECYCLE,
            rule=rules_lifecycle.guarded_merge,
            emits=frozenset({"AutoMergeTask"}),
            rationale=(
                "MERGE_READY -> AutoMergeTask. Last of the lifecycle rules "
                "only because it was last in the monolith's if/elif chain; the "
                "states are disjoint, so nothing depends on it."),
        ),

        # === carve authority: every claimant of the single carve slot =======
        RuleSpec(
            name="carver-session-ladder", contract_items=(9, 12, 14),
            concern="carve-authority", scope=RuleScope.PLAN, channel=Channel.CARVER,
            rule=reconcile.carver_session_ladder,
            emits=frozenset({"AdmitCarveProposal", "CompactCarverSession",
                             "ResumeCarverSession", "StartCarverSession"}),
            claims=frozenset({"carve-slot"}),
            legacy_owner="CR-06c",
            rationale=(
                "FIRST claimant of the carve slot (plan-long-running-carver "
                "§3.3 priority 1-4): admitting an already-produced proposal, "
                "or bootstrapping/recovering/feeding the persistent session, "
                "outranks starting any new carve -- and must run before item "
                "12's re-scope, which a pending merge feed may invalidate."),
        ),
        RuleSpec(
            name="ready-to-carve", contract_items=(12, 14),
            concern="carve-authority", scope=RuleScope.PLAN, channel=Channel.LIFECYCLE,
            rule=reconcile.ready_to_carve,
            emits=frozenset({"CarveDispatch"}),
            claims=frozenset({"carve-slot"}),
            legacy_owner="CR-06c",
            rationale=(
                "Before BOTH cadence triggers and the headroom refill: "
                "finishing already-started work (a rejected task re-scoped by "
                "the carver) outranks starting new work of either kind. Emits "
                "into the LIFECYCLE channel because the dispatch is attributed "
                "to the origin task whose context seeds the re-scope packet."),
        ),
        RuleSpec(
            name="carver-human-intake", contract_items=(9, 12, 14),
            concern="carve-authority", scope=RuleScope.PLAN, channel=Channel.CARVER,
            rule=reconcile.carver_human_intake,
            emits=frozenset({"ResumeCarverSession"}),
            claims=frozenset({"carve-slot"}),
            legacy_owner="CR-06c",
            rationale=(
                "Plan §3.3 priority 6: queued human intake, evaluated AFTER "
                "item 12's re-scope but BEFORE the cadence triggers and the "
                "headroom refill."),
        ),

        # === dispatch and the attempt ladder ================================
        RuleSpec(
            name="implementer-dispatch", contract_items=(3,), concern="dispatch",
            scope=RuleScope.PLAN, channel=Channel.LIFECYCLE,
            rule=rules_dispatch.implementer_dispatch,
            emits=frozenset({"DispatchImplementer"}),
            rationale=(
                "After the lifecycle rules, so a task transitioned this pass "
                "is planned from the state the transition leaves it in. PLAN "
                "scope rather than TASK: the capacity budget is shared across "
                "tasks and spent in sorted task-id order."),
        ),
        RuleSpec(
            name="attempt-ladder", contract_items=(4,), concern="attempts",
            scope=RuleScope.PLAN, channel=Channel.ATTEMPT,
            rule=rules_attempts.attempt_ladder,
            emits=frozenset({"DispatchImplementer", "EmitAttemptExit",
                             "InterruptAttempt", "MarkInterrupted", "MarkStalled",
                             "ResumeAttempt", "StallCheck", "Transition"}),
            rationale=(
                "After dispatch, so a fresh-start re-cut of a poisoned attempt "
                "sees the same capacity picture the dispatch rule planned "
                "against. Its BLOCKED transitions are what the arbiter's "
                "deconfliction step later reconciles against every launch."),
        ),

        # === review ==========================================================
        RuleSpec(
            name="review-waves", contract_items=(5,), concern="review",
            scope=RuleScope.PLAN, channel=Channel.WAVE,
            rule=rules_review.review_waves,
            emits=frozenset({"LaunchReview", "OpenWave"}),
            rationale=(
                "After the attempt ladder: a review attempt the ladder just "
                "interrupted or exited must be visible to the in-flight guard, "
                "or the wave relaunches over a leg that is still live."),
        ),
        RuleSpec(
            name="self-review-dispatch", contract_items=(5,), concern="review",
            scope=RuleScope.PLAN, channel=Channel.SELF_REVIEW,
            rule=rules_review.self_review_dispatch,
            emits=frozenset({"LaunchSelfReview"}),
            rationale=(
                "With the wave review, and after it, because both read the "
                "same latest-attempt recency guard and the self-review leg is "
                "the cheaper of the two."),
        ),

        # === attention =======================================================
        RuleSpec(
            name="progress-ratchet", contract_items=(6,), concern="attention",
            scope=RuleScope.PLAN, channel=Channel.SPEC,
            rule=rules_attention.progress_ratchet,
            emits=frozenset({"SpecAttention"}),
            rationale=(
                "Attention is a report on the pass, so it runs after the rules "
                "that do the work. The ratchet is first among the attention "
                "rules because its SpecAttention is first in the plan."),
        ),
        RuleSpec(
            name="spec-health", contract_items=(7,), concern="attention",
            scope=RuleScope.PLAN, channel=Channel.SPEC,
            rule=rules_attention.spec_health,
            emits=frozenset({"SpecAttention"}),
            rationale=(
                "Immediately after the ratchet: its three branches emit in a "
                "fixed order (carve-outcome, rejections, blocked-"
                "underspecified) that the plan's action order preserves."),
        ),

        # === carve cadences and the headroom refill (CR-06c) =================
        RuleSpec(
            name="test-health-carve", contract_items=(15,), concern="carve-authority",
            scope=RuleScope.PLAN, channel=Channel.CARVE,
            rule=reconcile.test_health_carve,
            emits=frozenset({"CarveDispatch"}),
            claims=frozenset({"carve-slot"}),
            legacy_owner="CR-06c",
            rationale=(
                "BEFORE the headroom refill, DELIBERATELY (contract item 15's "
                "own ORDERING note): item 9's condition holds on essentially "
                "every pass of an actively-draining project, so a seldom-run "
                "trigger placed after it would lose the pass's single carve "
                "slot forever and its cadence would silently never fire."),
        ),
        RuleSpec(
            name="gap-audit-carve", contract_items=(17,), concern="carve-authority",
            scope=RuleScope.PLAN, channel=Channel.CARVE,
            rule=reconcile.gap_audit_carve,
            emits=frozenset({"CarveDispatch"}),
            claims=frozenset({"carve-slot"}),
            legacy_owner="CR-06c",
            rationale=(
                "After test-health and before the headroom refill (contract "
                "item 17's own ORDERING note) -- the same starvation argument, "
                "and the two cadences are ranked against each other only by "
                "which was written first."),
        ),
        RuleSpec(
            name="headroom-carve", contract_items=(9, 14), concern="carve-authority",
            scope=RuleScope.PLAN, channel=Channel.CARVE,
            rule=reconcile.headroom_carve,
            emits=frozenset({"CarveDispatch"}),
            claims=frozenset({"carve-slot"}),
            legacy_owner="CR-06c",
            rationale=(
                "LAST claimant of the carve slot. Its condition -- the ready "
                "queue is below target -- is nearly always true on a draining "
                "project, so anything placed after it would starve; being last "
                "is what makes every rarer trigger above it reachable."),
        ),

        # === cadence outside the mutex ======================================
        RuleSpec(
            name="gate-verify", contract_items=(16,), concern="attention",
            scope=RuleScope.PLAN, channel=Channel.GATE_VERIFY,
            rule=rules_attention.gate_verify,
            emits=frozenset({"VerifyGate"}),
            claims=frozenset(),
            rationale=(
                "Claims NOTHING, and that is the design (contract item 16): a "
                "gate verify runs a subprocess against disposable canary "
                "commits -- no LLM frontier route, no carve budget, no agent "
                "turn -- so it is not a carve sibling and must never contend "
                "for the carve slot, or a busy carve queue would silently "
                "suppress the probe that notices a gate has stopped "
                "discriminating. Last only because its action is last in the "
                "plan; nothing above it can affect it."),
        ),
    )
