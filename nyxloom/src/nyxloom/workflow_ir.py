"""The normalized workflow IR, and the kernel/compiler partition it rests on.

WHY THIS MODULE EXISTS (CR-07a)
-------------------------------
``nyxloom-trove/reports/CR-07-TRANSITION-INVENTORY-2026-08-03.md`` classifies
all 55 edges of the FROZEN ``types.TASK_TRANSITIONS`` graph as 38 *kernel* and
17 *compiler*, in five kernel classes. This module turns that inventory from a
document into a mechanism, and the central discovery is that the split is not a
list at all:

    **THE KERNEL/COMPILER SPLIT IS A PARTITION OF THE STATE SPACE BY EDGE
    TARGET.** Every edge into ``{SUPERSEDED, CANCELLED, BLOCKED,
    NEEDS_DECISION, MERGE_READY, MERGED, VALIDATING, COMPLETED}`` is kernel;
    every edge into ``{DRAFT-free: ACTIVE, QUEUED, CARVED, READY_TO_CARVE,
    SELF_REVIEWING, AWAITING_REVIEW, REVIEW_REJECTED}`` is compiler. The two
    target sets are DISJOINT, and the partition reproduces the inventory's
    counts exactly: 38/17, and 22/4/5/6/1 across the five classes.

That is worth stating loudly because it is what makes the safety property
*structural* rather than enforced by a checklist. A manifest never writes a
target STATE at all: a compiler outcome names a NODE, and a kernel outcome
names a VERB whose destination the kernel resolves from the frozen graph. There
is no syntax in which "``AWAITING_REVIEW`` goes to ``MERGE_READY``" is a thing
a workflow author can say, correctly or otherwise, so the merge authority chain
cannot be redirected by any manifest -- valid, hostile, or merely careless.

THE FIVE KERNEL CLASSES, AND HOW EACH BECOMES UNREPRESENTABLE
-------------------------------------------------------------
``K-escape`` (22), ``K-merge-spine`` (4), ``K-block`` (5), ``K-decision`` (6)
and ``K-operator-retry`` (1) are all handled by ONE mechanism rather than five:

* **cannot be removed** -- kernel edges are INJECTED by the compiler from
  :data:`KERNEL_EDGES`, over the transitive kernel closure of the states the
  workflow's nodes own (:func:`kernel_closure`). A manifest that mentions none
  of them still compiles to a workflow that has all of them.
* **cannot be redirected or added** -- the only thing a manifest may bind to a
  kernel edge is a LABEL, through a :class:`KernelVerb`. The verb's target is a
  lookup in :data:`KERNEL_VERBS_BY_STATE`, derived from the frozen graph.
* **cannot be argued about** -- the class of an edge is
  :func:`classify_edge`, a total function of the pair, so a *hypothetical*
  edge a manifest tries to invent (``ACTIVE -> MERGE_READY``, which is not even
  a frozen edge) still gets a class, and the refusal names it.

TWO STRUCTURAL FACTS THE INVENTORY ASKED FOR IN CODE
-----------------------------------------------------
1. **The post-merge spine is irreversible.** ``MERGED`` and ``VALIDATING`` are
   the only non-terminal states with no escape edges. Nothing here grants a
   node a uniform escape set: :data:`KERNEL_EDGES_BY_STATE` is read out of
   ``TASK_TRANSITIONS``, so ``MERGED`` gets exactly one kernel edge and it is
   ``-> VALIDATING``. The inventory warns that a node model with a uniform
   escape template silently adds four edges and makes merged work cancellable;
   the only defence that works is deriving rather than templating, and
   ``tests/test_workflow_ir.py`` pins the count at zero for both states.
2. **The boundary runs THROUGH ``VALIDATING``.** ``MERGED -> VALIDATING`` is
   kernel (``K-merge-spine``) while ``VALIDATING -> COMPLETED`` is
   pipeline-composed in today's engine -- but the pair is *both* kernel here,
   because what the pipeline composes is whether a gate RUNS at ``VALIDATING``,
   not where ``VALIDATING`` may lead. ``MERGED -> COMPLETED`` stays absent for
   free: :data:`KERNEL_VERBS_BY_STATE`\\ ``[MERGED][ADVANCE]`` is ``VALIDATING``
   and there is no other way to name a successor of ``MERGED``.

RELATION TO ``stages.py``
-------------------------
CR-07 generalizes a mechanism that already exists. ``stages.validate_pipeline``
applies exactly this reasoning to stage composition: exits must be real frozen
edges, and no stage may route into a state nothing owns. The generalization is
that a ``Stage``'s ``exit_map`` mixes kernel and compiler targets in one tuple
of ``(label, TaskState)`` pairs -- ``implement`` declares ``dead_end ->
BLOCKED`` (kernel) beside ``done -> AWAITING_REVIEW`` (compiler) -- so the
stage layer *can* express a kernel edge and is prevented from misexpressing one
only by the frozen-graph membership check. Here the two cannot be confused,
because they are different grammar.

This module is LOG-FREE and imports only ``types`` and ``workflow_guards``, for
``planning.py``'s reason: it sits inside the compile path that CR-07b will call
from config load and from the planner, and a DEBUG record on a pure function is
how ``stages.effective_concurrency`` made the planner's advertised purity false
transitively for two months.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import Enum

from .types import TASK_TRANSITIONS, TERMINAL_TASK_STATES, Role, TaskState
from .workflow_guards import GuardRef

#: The source schema identifier a manifest must declare.
SCHEMA_ID = "nyxloom.workflow/v1"


class KernelEdgeClass(Enum):
    """The five kernel classes of the CR-07 transition inventory.

    The values are the inventory's own names, so a compile diagnostic quotes
    the document a reviewer will go and read.
    """

    ESCAPE = "K-escape"
    MERGE_SPINE = "K-merge-spine"
    BLOCK = "K-block"
    DECISION = "K-decision"
    OPERATOR_RETRY = "K-operator-retry"


class KernelVerb(Enum):
    """The complete vocabulary a manifest may use to LABEL a kernel edge.

    A verb names an authority, never a destination. ``advance`` is the merge
    authority chain's single step forward from wherever the task is; the kernel
    decides what "forward" means and the manifest cannot.
    """

    SUPERSEDE = "supersede"
    CANCEL = "cancel"
    BLOCK = "block"
    DECIDE = "decide"
    ADVANCE = "advance"
    OPERATOR_RETRY = "operator_retry"


class NodeKind(Enum):
    """What drives a node.

    ``AGENT`` nodes dispatch an agent and are the only ones whose outcome comes
    from outside the control plane -- which is why §4.3 rejection condition 7
    is expressed as a restriction on this kind. ``MECHANISM`` nodes are driven
    by the daemon (merge, gate, triage). ``WAIT`` nodes sit on a kernel-owned
    state and declare only where an operator's or the kernel's resumption
    lands; they have no handler and no agent.
    """

    AGENT = "agent"
    MECHANISM = "mechanism"
    WAIT = "wait"


class EdgeOrigin(Enum):
    """Who owns an edge: the frozen kernel, or the compiled workflow."""

    KERNEL = "kernel"
    COMPILER = "compiler"


class JoinMode(Enum):
    """Typed join policy for parallel branches (§4.3 condition 8)."""

    ALL = "all"
    ANY = "any"


class JoinCancel(Enum):
    """What happens to the losing branches when a join resolves."""

    CANCEL_SIBLINGS = "cancel_siblings"
    LEAVE_SIBLINGS = "leave_siblings"


class WaitResume(Enum):
    """The closed outcome vocabulary of a :attr:`NodeKind.WAIT` node.

    Two members cover all six lifecycle edges the inventory classifies as
    compiler (``DRAFT/CARVED/NEEDS_DECISION/BLOCKED -> QUEUED |
    READY_TO_CARVE``), and each carries a checkable meaning: ``resume_implement``
    must land in the implementation region and ``resume_carve`` in the carve
    region. A label that means something is a label whose target can be
    verified; ``next`` would not have been.
    """

    RESUME_IMPLEMENT = "resume_implement"
    RESUME_CARVE = "resume_carve"


#: Where each :class:`WaitResume` label is permitted to land. Checked by the
#: compiler, which is what stops a wait node quietly re-routing the queue.
WAIT_RESUME_REGIONS: dict[WaitResume, frozenset] = {
    WaitResume.RESUME_IMPLEMENT: frozenset({TaskState.QUEUED, TaskState.ACTIVE}),
    WaitResume.RESUME_CARVE: frozenset({TaskState.READY_TO_CARVE, TaskState.CARVED}),
}

#: States whose onward movement is kernel or operator authority: intake,
#: escalation, queue admission and the human-decision hold. Mirrors
#: ``stages.LIFECYCLE_STATES`` exactly (pinned by
#: ``tests/test_workflow_ir.py``); a node on one of these may only be a
#: :attr:`NodeKind.WAIT` node, because there is nothing for an agent to do
#: while a human decides.
KERNEL_OWNED_STATES: frozenset = frozenset({
    TaskState.DRAFT, TaskState.NEEDS_DECISION, TaskState.CARVED, TaskState.BLOCKED,
})

#: Edge targets that belong to the kernel. This is the ONE declaration this
#: module makes about the partition; everything else is derived from it and
#: from ``TASK_TRANSITIONS``. ``tests/test_workflow_ir.py`` checks the derived
#: counts against the inventory DOCUMENT (38/17 and the five class counts), so
#: a wrong entry here fails against the artifact the controller produced rather
#: than against a copy of itself.
KERNEL_TARGET_STATES: frozenset = frozenset({
    TaskState.SUPERSEDED, TaskState.CANCELLED,
    TaskState.BLOCKED, TaskState.NEEDS_DECISION,
    TaskState.MERGE_READY, TaskState.MERGED,
    TaskState.VALIDATING, TaskState.COMPLETED,
})


def classify_edge(from_state: TaskState, to_state: TaskState) -> KernelEdgeClass | None:
    """The kernel class of ``from_state -> to_state``, or ``None`` if the edge
    belongs to the compiler.

    TOTAL over all state pairs, deliberately: a manifest that tries to invent
    an edge the frozen graph does not even have (``ACTIVE -> MERGE_READY``)
    must still be refused with the class it was reaching for, otherwise the
    "add" half of the negative-test obligation degrades into a generic
    illegal-transition message that names nothing.
    """
    if to_state in (TaskState.SUPERSEDED, TaskState.CANCELLED):
        return KernelEdgeClass.ESCAPE
    if to_state is TaskState.BLOCKED:
        return KernelEdgeClass.BLOCK
    if to_state is TaskState.NEEDS_DECISION:
        return KernelEdgeClass.DECISION
    if to_state is TaskState.VALIDATING and from_state is TaskState.BLOCKED:
        # The one edge no code path plans: an operator who fixed the underlying
        # cause by hand re-queues a blocked post-merge task to retry the gate.
        # Recorded by the inventory specifically so CR-07 does not delete it as
        # dead -- it is reachable only by human action, which is exactly why it
        # looks unreachable to a static reader.
        return KernelEdgeClass.OPERATOR_RETRY
    if to_state in KERNEL_TARGET_STATES:
        return KernelEdgeClass.MERGE_SPINE
    return None


def verb_for(from_state: TaskState, to_state: TaskState) -> KernelVerb | None:
    """The :class:`KernelVerb` that names ``from_state -> to_state``, or
    ``None`` for a compiler edge."""
    kind = classify_edge(from_state, to_state)
    if kind is None:
        return None
    if kind is KernelEdgeClass.ESCAPE:
        return (KernelVerb.SUPERSEDE if to_state is TaskState.SUPERSEDED
                else KernelVerb.CANCEL)
    if kind is KernelEdgeClass.BLOCK:
        return KernelVerb.BLOCK
    if kind is KernelEdgeClass.DECISION:
        return KernelVerb.DECIDE
    if kind is KernelEdgeClass.OPERATOR_RETRY:
        return KernelVerb.OPERATOR_RETRY
    return KernelVerb.ADVANCE


def _derive_edges() -> tuple[dict, frozenset]:
    kernel: dict[tuple[TaskState, TaskState], KernelEdgeClass] = {}
    compiler: set[tuple[TaskState, TaskState]] = set()
    for from_state, targets in TASK_TRANSITIONS.items():
        for to_state in targets:
            kind = classify_edge(from_state, to_state)
            if kind is None:
                compiler.add((from_state, to_state))
            else:
                kernel[(from_state, to_state)] = kind
    return kernel, frozenset(compiler)


#: The 38 kernel edges of the frozen graph, with their class.
KERNEL_EDGES: dict[tuple[TaskState, TaskState], KernelEdgeClass]
#: The 17 compiler edges -- "the entire set the language must express"
#: (inventory, §Stop-loss). If one of them needs an escape hatch into
#: imperative code, §5.4's trigger has fired.
COMPILER_EDGES: frozenset
KERNEL_EDGES, COMPILER_EDGES = _derive_edges()

#: Kernel edges grouped by source state, as ``(to_state, class, verb)``.
KERNEL_EDGES_BY_STATE: dict[TaskState, tuple] = {
    state: tuple(sorted(
        ((to, KERNEL_EDGES[(state, to)], verb_for(state, to))
         for (frm, to) in KERNEL_EDGES if frm is state),
        key=lambda row: row[0].value))
    for state in TASK_TRANSITIONS
}

#: ``state -> verb -> target``. A manifest binds a label to a verb; this table
#: is the only thing that decides where that label leads. It is a FUNCTION --
#: no state has two kernel edges sharing a verb -- and ``test_workflow_ir.py``
#: proves it by construction rather than by inspection.
KERNEL_VERBS_BY_STATE: dict[TaskState, dict[KernelVerb, TaskState]] = {
    state: {verb: to for (to, _cls, verb) in rows}
    for state, rows in KERNEL_EDGES_BY_STATE.items()
}

#: Compiler edges grouped by source state.
COMPILER_TARGETS_BY_STATE: dict[TaskState, frozenset] = {
    state: frozenset(to for (frm, to) in COMPILER_EDGES if frm is state)
    for state in TASK_TRANSITIONS
}

#: The compiler half of the target partition. Disjoint from
#: :data:`KERNEL_TARGET_STATES`, which is the whole reason the two grammars
#: cannot be confused.
COMPILER_TARGET_STATES: frozenset = frozenset(to for (_frm, to) in COMPILER_EDGES)


def kernel_closure(seed: frozenset) -> frozenset:
    """Every state the kernel can carry a task to, starting from ``seed``.

    The kernel edges of a compiled workflow are NOT limited to the states its
    nodes own, and that is load-bearing rather than generous. A ``lean``
    pipeline has no ``post_merge_gate`` stage, so nothing owns ``MERGED`` or
    ``VALIDATING`` -- yet contract item 11 auto-advances ``VALIDATING ->
    COMPLETED`` for exactly that pipeline. Injecting only owned states would
    compile ``lean`` into a workflow with no way to complete anything, which is
    a false rejection of a SHIPPED preset and therefore a false stop-loss
    signal. The closure is what makes the kernel spine complete regardless of
    what the manifest composes.
    """
    seen = set(seed)
    frontier = list(seed)
    while frontier:
        state = frontier.pop()
        if state in TERMINAL_TASK_STATES:
            continue
        for to, _cls, _verb in KERNEL_EDGES_BY_STATE[state]:
            if to not in seen:
                seen.add(to)
                frontier.append(to)
    return frozenset(seen)


@dataclass(frozen=True)
class JoinSpec:
    """A typed join policy (§4.3 condition 8). Parallel branches without one
    do not compile."""

    group: str
    mode: JoinMode
    on_cancel: JoinCancel

    def as_canonical(self) -> dict:
        return {"group": self.group, "mode": self.mode.value,
                "on_cancel": self.on_cancel.value}


@dataclass(frozen=True)
class EdgeIR:
    """One compiled outcome edge.

    ``label`` is ``None`` for a kernel edge no manifest outcome binds -- the
    edge is still THERE, and that is the whole point of K-escape/K-block/
    K-decision: a task can always be cancelled, blocked or escalated whether or
    not the workflow named a way to trigger it.
    """

    from_state: TaskState
    to_state: TaskState
    origin: EdgeOrigin
    label: str | None = None
    kernel_class: KernelEdgeClass | None = None
    verb: KernelVerb | None = None
    guard: GuardRef | None = None
    target_node: str | None = None

    def sort_key(self) -> tuple:
        return (self.from_state.value, self.to_state.value, self.origin.value,
                self.label or "", self.target_node or "")

    def as_canonical(self) -> dict:
        out: dict = {
            "from": self.from_state.value,
            "to": self.to_state.value,
            "origin": self.origin.value,
        }
        if self.label is not None:
            out["label"] = self.label
        if self.kernel_class is not None:
            out["kernel_class"] = self.kernel_class.value
        if self.verb is not None:
            out["verb"] = self.verb.value
        if self.target_node is not None:
            out["target_node"] = self.target_node
        if self.guard is not None:
            out["guard"] = self.guard.as_canonical()
        return out


@dataclass(frozen=True)
class NodeIR:
    """One compiled node.

    ``entry_state``/``exit_state``/``owns`` reproduce ``stages.Stage``'s shape
    on purpose: ``implement`` is entered at ``QUEUED`` and its outcome
    transitions FROM ``ACTIVE``, and a node model that collapsed the two would
    be unable to express the shipped ``implement`` stage at all.
    """

    node_id: str
    kind: NodeKind
    entry_state: TaskState
    exit_state: TaskState
    owns: frozenset
    handler: str | None = None
    role: Role | None = None
    prompt: str | None = None
    concurrency: object = "serial"
    context: frozenset = frozenset()
    budget: int | None = None
    join: JoinSpec | None = None
    edges: tuple = ()

    def compiler_edges(self) -> tuple:
        return tuple(e for e in self.edges if e.origin is EdgeOrigin.COMPILER)

    def kernel_edges(self) -> tuple:
        return tuple(e for e in self.edges if e.origin is EdgeOrigin.KERNEL)

    def as_canonical(self) -> dict:
        out: dict = {
            "id": self.node_id,
            "kind": self.kind.value,
            "entry_state": self.entry_state.value,
            "exit_state": self.exit_state.value,
            "owns": sorted(s.value for s in self.owns),
            "concurrency": self.concurrency,
            "context": sorted(self.context),
            "edges": [e.as_canonical() for e in sorted(self.edges, key=EdgeIR.sort_key)],
        }
        if self.handler is not None:
            out["handler"] = self.handler
        if self.role is not None:
            out["role"] = self.role.value
        if self.prompt is not None:
            # §4.3 condition 9: the prompt VERSION is part of the immutable
            # execution-plan digest, so a prompt bump is a workflow change a
            # reader can see rather than a silent substitution under a stable
            # digest.
            out["prompt"] = self.prompt
        if self.budget is not None:
            out["budget"] = self.budget
        if self.join is not None:
            out["join"] = self.join.as_canonical()
        return out


@dataclass(frozen=True)
class WorkflowIR:
    """A compiled workflow: the normalized, canonically serializable form.

    Runtime never interprets the source document again (parent §4.3). CR-07b
    binds ``TaskRuntime.workflow_digest`` to :func:`digest` of this record.
    """

    workflow_id: str
    version: int
    start: str
    nodes: tuple

    def node(self, node_id: str) -> NodeIR:
        for n in self.nodes:
            if n.node_id == node_id:
                return n
        raise KeyError(node_id)

    def all_edges(self) -> tuple:
        return tuple(e for n in self.nodes for e in n.edges)

    def state_owner(self, state: TaskState) -> tuple:
        return tuple(n.node_id for n in self.nodes if state in n.owns)


def canonical(ir: WorkflowIR) -> dict:
    """The canonical normalized form: sorted, total, and free of source shape.

    Node declaration order, key order, whitespace and comments in the source
    cannot reach this dict -- nodes are sorted by id and edges by
    :meth:`EdgeIR.sort_key`, and every value is a primitive. That is what makes
    :func:`digest` a function of the WORKFLOW rather than of its formatting.
    """
    return {
        "schema": SCHEMA_ID,
        "id": ir.workflow_id,
        "version": ir.version,
        "start": ir.start,
        "nodes": [n.as_canonical() for n in sorted(ir.nodes, key=lambda n: n.node_id)],
    }


def canonical_json(ir: WorkflowIR) -> str:
    """Deterministic JSON text for :func:`canonical`."""
    return json.dumps(canonical(ir), sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True)


def digest(ir: WorkflowIR) -> str:
    """``sha256:<hex>`` over :func:`canonical_json`."""
    return "sha256:" + hashlib.sha256(canonical_json(ir).encode("utf-8")).hexdigest()
