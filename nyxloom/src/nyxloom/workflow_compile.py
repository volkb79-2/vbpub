"""The workflow compiler: source document -> validated, normalized IR.

WHY THIS MODULE EXISTS (CR-07a)
-------------------------------
The parent plan's §4.3 lists nine compile-time rejection conditions and says
"the compiler resolves and type-checks them". The CR-07 transition inventory
adds the harder obligation: the 38 KERNEL edges of the frozen graph must become
rejection conditions HERE, before any workflow is migrated onto the mechanism,
so that CR-07b's manifests are validated by machinery already proven to reject
the unsafe shapes rather than by machinery written alongside the thing it
validates.

THE GRAMMAR IS THE SAFETY PROPERTY
----------------------------------
There is no field in this vocabulary whose value is a transition target::

    outcomes:         label -> NODE ID          (compiler edges)
    kernel_outcomes:  label -> KERNEL VERB      (kernel edges, target chosen
                                                 by workflow_ir, not here)

So a manifest cannot say where a kernel edge goes, cannot say which
predecessors a kernel state has, and cannot say that a kernel edge is absent.
The 38 negative tests in ``tests/test_workflow_kernel_edges.py`` are therefore
not testing a list of rules -- they are testing that four distinct ATTEMPTS at
each of the 38 edges are refused with the edge's own inventory class:

* **redirect / add** -- name the target state as an outcome value
  (:attr:`DiagnosticCode.KERNEL_EDGE_NOT_DECLARABLE`);
* **remove** -- the reserved node key ``suppress``
  (:attr:`DiagnosticCode.KERNEL_NOT_MANIFEST_DATA`);
* **redefine the predecessor set** -- the reserved top-level key
  ``predecessors``, which is the inventory's own words for the K-escape and
  K-merge-spine obligations (same code);
* **omit it entirely** -- which is not an error at all: the compiled node
  carries the edge regardless, and the test asserts its presence.

THE RESERVED KEYS ARE DELIBERATE TARPITS
----------------------------------------
``suppress``, ``predecessors``, ``escapes`` and ``transitions`` are not part of
the schema and are checked BEFORE it, purely so that an author reaching for
them gets a diagnostic naming the kernel class they were reaching into rather
than a generic ``additionalProperties`` complaint. A refusal that does not say
which safety property it is protecting teaches nothing, and the next author
works around it.

WHAT ``VALIDATE_PIPELINE`` DID, AND WHAT IS DIFFERENT
-----------------------------------------------------
``stages.validate_pipeline`` already applies the adversarial reasoning this
module generalizes: exits must be real frozen edges (rule 2) and no stage may
route into a state nothing owns (rule 3). Two things are genuinely new:

1. **A ``Stage.exit_map`` mixes kernel and compiler targets in one tuple.**
   ``implement`` declares ``dead_end -> BLOCKED`` beside ``done ->
   AWAITING_REVIEW``; only frozen-graph membership stops a stage kind from
   declaring ``AWAITING_REVIEW -> MERGE_READY`` for itself. Here they are
   different grammar, so the confusion is not possible.
2. **Rule 3's exemptions become obligations.** ``LIFECYCLE_STATES`` lets a
   stage route into ``CARVED``/``NEEDS_DECISION``/``BLOCKED`` on the
   understanding that "the mechanism (or a human) carries it onward" -- without
   ever saying WHERE. A compiler outcome into a kernel-owned state must name a
   wait node here, so the shipped presets' implicit hand-off becomes a declared
   edge. That is what turned up the ``self_review`` unreachability recorded in
   ``workflow_shadow.py``.

LAZY ``jsonschema``
-------------------
The import sits inside :func:`_schema_diagnostics` for the reason CR-03's gate
found the hard way: a module-scope ``jsonschema`` import killed every
dispatched leg in the gate container, where the fake CLI runs without it.

LOG-FREE, like ``workflow_ir`` and ``planning``: compiling is a pure function
of the source document, and CR-07b calls it from config load and from paths the
planner's purity oracle walks.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from functools import lru_cache
from pathlib import Path

from .types import TASK_TRANSITIONS, TERMINAL_TASK_STATES, Role, TaskState
from .workflow_guards import GuardError, resolve_guard
from .workflow_ir import (
    COMPILER_EDGES,
    KERNEL_EDGES,
    KERNEL_EDGES_BY_STATE,
    KERNEL_OWNED_STATES,
    KERNEL_VERBS_BY_STATE,
    WAIT_RESUME_REGIONS,
    EdgeIR,
    EdgeOrigin,
    JoinCancel,
    JoinMode,
    JoinSpec,
    KernelEdgeClass,
    KernelVerb,
    NodeIR,
    NodeKind,
    WaitResume,
    WorkflowIR,
    classify_edge,
    kernel_closure,
)


class DiagnosticCode(Enum):
    """The closed vocabulary of compile refusals.

    Every member is exercised by a negative test; the vocabulary oracle in
    ``tests/test_workflow_vocabulary.py`` fails naming any member no test input
    reaches, which is CR-06's three-times-repeated defect answered in advance
    rather than after.
    """

    KERNEL_NOT_MANIFEST_DATA = "kernel-not-manifest-data"
    SCHEMA = "schema"
    UNKNOWN_STATE = "unknown-state"
    UNKNOWN_HANDLER = "unknown-handler"
    UNKNOWN_ROLE = "unknown-role"
    HANDLER_ROLE_MISMATCH = "handler-role-mismatch"
    UNKNOWN_GUARD = "unknown-guard"
    GUARD_ARGUMENT = "guard-argument"
    GUARD_ON_UNBOUND_OUTCOME = "guard-on-unbound-outcome"
    UNKNOWN_CONTEXT_FLAG = "unknown-context-flag"
    PROMPT_VERSION_MISSING = "prompt-version-missing"
    STATE_NOT_NODE_OWNABLE = "state-not-node-ownable"
    WAIT_NODE_PLACEMENT = "wait-node-placement"
    DUPLICATE_STATE_OWNER = "duplicate-state-owner"
    ILLEGAL_TRANSITION = "illegal-transition"
    KERNEL_EDGE_NOT_DECLARABLE = "kernel-edge-not-declarable"
    STATE_IS_NOT_A_NODE = "state-is-not-a-node"
    KERNEL_VERB_UNAVAILABLE = "kernel-verb-unavailable"
    UNDECLARED_OUTCOME = "undeclared-outcome"
    UNROUTED_OUTCOME = "unrouted-outcome"
    DUPLICATE_OUTCOME = "duplicate-outcome"
    ABSENT_NODE = "absent-node"
    UNKNOWN_START = "unknown-start"
    UNREACHABLE_NODE = "unreachable-node"
    NO_SUCCESS_PATH = "no-success-path"
    UNBOUNDED_CYCLE = "unbounded-cycle"
    MERGE_WITHOUT_REVIEW = "merge-without-review"
    AGENT_SELF_AUTHORITY = "agent-self-authority"
    JOIN_POLICY_MISSING = "join-policy-missing"
    JOIN_POLICY_INCONSISTENT = "join-policy-inconsistent"
    WAIT_RESUME_REGION = "wait-resume-region"


@dataclass(frozen=True)
class Diagnostic:
    """One refusal, with the source location that caused it.

    ``kernel_class`` is set whenever the refusal is protecting one of the
    inventory's five kernel classes, so a caller (and a test) can assert on the
    PROPERTY rather than on message text.
    """

    code: DiagnosticCode
    message: str
    location: str
    kernel_class: KernelEdgeClass | None = None

    def __str__(self) -> str:
        named = f" [{self.kernel_class.value}]" if self.kernel_class else ""
        return f"{self.location}: {self.code.value}{named}: {self.message}"


class CompileError(Exception):
    """Raised with every diagnostic the failing phase produced."""

    def __init__(self, diagnostics: tuple, origin: str) -> None:
        self.diagnostics = tuple(diagnostics)
        self.origin = origin
        super().__init__(f"workflow compilation failed ({origin}):\n" +
                         "\n".join(str(d) for d in self.diagnostics))

    def codes(self) -> frozenset:
        return frozenset(d.code for d in self.diagnostics)

    def classes(self) -> frozenset:
        return frozenset(d.kernel_class for d in self.diagnostics
                         if d.kernel_class is not None)


# --- registries -----------------------------------------------------------

#: The outcome vocabulary of an agent role. ``required`` is what a manifest
#: MUST route: it is exactly what ``stages.py``'s ``exit_map`` declares today,
#: so a shipped preset binds every required outcome and nothing more. The
#: optional extras (``triage.rescope``, ``auto_merge.rejected``) are the two
#: compiler edges the engine reaches by a context-sensitive upgrade in
#: ``reconcile`` rather than by declaration -- available to a manifest so the
#: language covers all 17 compiler edges, optional so a carve-less pipeline is
#: not forced to grow a carve node in order to compile.
AGENT_OUTCOMES: dict[Role, tuple] = {
    Role.CARVER: (frozenset({"done", "needs_decision", "rescope_superseded"}),
                  frozenset({"done", "needs_decision", "rescope_superseded"})),
    Role.IMPLEMENTER: (frozenset({"done", "incomplete", "dead_end"}),
                       frozenset({"done", "incomplete", "dead_end"})),
    Role.SELF_REVIEW: (frozenset({"approved", "rejected"}),
                       frozenset({"approved", "rejected"})),
    Role.REVIEW_INDEPENDENT: (frozenset({"approved", "rejected"}),
                              frozenset({"approved", "rejected"})),
}


@dataclass(frozen=True)
class HandlerSpec:
    """What one registered handler IS (parent §4.3 condition 1 and 2).

    ``outcomes``/``required`` are ``None`` for ``dispatch_agent``, whose
    outcome vocabulary is a property of the ROLE rather than of the dispatch
    mechanism -- an implementer reports ``done``/``incomplete``/``dead_end``
    and a reviewer reports ``approved``/``rejected`` through the same effector.
    """

    name: str
    kind: NodeKind
    outcomes: frozenset | None
    required: frozenset | None


HANDLER_REGISTRY: dict[str, HandlerSpec] = {
    "dispatch_agent": HandlerSpec("dispatch_agent", NodeKind.AGENT, None, None),
    "triage": HandlerSpec(
        "triage", NodeKind.MECHANISM,
        frozenset({"fixable", "exhausted", "rescope"}),
        frozenset({"fixable", "exhausted"})),
    "auto_merge": HandlerSpec(
        "auto_merge", NodeKind.MECHANISM,
        frozenset({"merged", "rejected"}), frozenset({"merged"})),
    "post_merge_gate": HandlerSpec(
        "post_merge_gate", NodeKind.MECHANISM,
        frozenset({"pass", "fail"}), frozenset({"pass", "fail"})),
}

#: Packet-assembly context flags a node may declare. Pinned equal to
#: ``stages.KNOWN_CONTEXT_FLAGS`` by ``tests/test_workflow_compile.py`` rather
#: than imported, so the compiler does not depend on the stage layer it is
#: meant to replace.
KNOWN_CONTEXT_FLAGS: frozenset = frozenset({"session-reuse", "spine-digest"})

#: Reserved keys that exist ONLY to be refused with a class-naming message.
#: See the module docstring.
RESERVED_KEYS: frozenset = frozenset({
    "suppress", "predecessors", "escapes", "transitions",
})

_STATES_BY_NAME: dict[str, TaskState] = {s.value: s for s in TaskState}
_ROLES_BY_VALUE: dict[str, Role] = {r.value: r for r in AGENT_OUTCOMES}
#: A source state for target-only classification. Anything but ``BLOCKED``,
#: whose ``-> VALIDATING`` edge is the one class that depends on the source.
_ANY_SOURCE = TaskState.MERGED

_VERB_CLASSES: dict[KernelVerb, KernelEdgeClass] = {
    KernelVerb.SUPERSEDE: KernelEdgeClass.ESCAPE,
    KernelVerb.CANCEL: KernelEdgeClass.ESCAPE,
    KernelVerb.BLOCK: KernelEdgeClass.BLOCK,
    KernelVerb.DECIDE: KernelEdgeClass.DECISION,
    KernelVerb.ADVANCE: KernelEdgeClass.MERGE_SPINE,
    KernelVerb.OPERATOR_RETRY: KernelEdgeClass.OPERATOR_RETRY,
}


@lru_cache(maxsize=1)
def _schema() -> dict:
    path = Path(__file__).parent / "schemas" / "workflow.schema.json"
    return json.loads(path.read_text(encoding="utf-8"))


# --- phase 0: the reserved-key tarpits -------------------------------------


def _reserved_diagnostics(source: dict) -> list:
    diags: list = []
    for key in sorted(RESERVED_KEYS):
        if key in source:
            diags.extend(_reserved_at(key, source[key], key, None))
    nodes = source.get("nodes")
    if isinstance(nodes, dict):
        for node_id in sorted(nodes):
            body = nodes[node_id]
            if not isinstance(body, dict):
                continue
            exit_state = _lenient_exit_state(body)
            for key in sorted(RESERVED_KEYS):
                if key in body:
                    diags.extend(_reserved_at(
                        key, body[key], f"nodes.{node_id}.{key}", exit_state))
    return diags


def _lenient_exit_state(body: dict) -> TaskState | None:
    """The node's exit state as far as phase 0 can tell.

    Phase 0 runs before the schema, so the value may be missing or nonsense;
    ``None`` means "classify by target alone", which still names a class.
    """
    raw = body.get("exit_state") or body.get("entry_state")
    return _STATES_BY_NAME.get(raw) if isinstance(raw, str) else None


def _named_states(value: object) -> list:
    """Every ``TaskState`` a reserved key's value mentions, in source order."""
    if isinstance(value, str):
        candidates = [value]
    elif isinstance(value, dict):
        candidates = [k for k in value if isinstance(k, str)]
    elif isinstance(value, (list, tuple)):
        candidates = [v for v in value if isinstance(v, str)]
    else:
        candidates = []
    return [_STATES_BY_NAME[c] for c in candidates if c in _STATES_BY_NAME]


def _reserved_pairs(key: str, value: object,
                    exit_state: TaskState | None) -> list:
    """The ``(source, target)`` edges a reserved key is reaching for.

    ``predecessors: {TARGET: [SOURCE, ...]}`` names BOTH ends, which is the
    only reason the refusal can name ``K-operator-retry`` rather than the class
    of ``VALIDATING``'s predecessor set as a whole -- the one target the frozen
    graph reaches from two different classes.
    """
    if key == "predecessors" and isinstance(value, dict):
        pairs: list = []
        for target in _named_states(value):
            sources = _named_states(value[target.value])
            pairs.extend((source, target) for source in sources)
            if not sources:
                pairs.append((_ANY_SOURCE, target))
        return pairs
    source_state = exit_state if (key == "suppress" and exit_state) else _ANY_SOURCE
    return [(source_state, target) for target in _named_states(value)]


def _reserved_at(key: str, value: object, location: str,
                 exit_state: TaskState | None) -> list:
    pairs = _reserved_pairs(key, value, exit_state)
    if not pairs:
        klass = KernelEdgeClass.ESCAPE if key == "escapes" else None
        named = (f"the {klass.value} class" if klass else
                 "all five kernel classes (K-escape, K-merge-spine, K-block, "
                 "K-decision, K-operator-retry)")
        return [Diagnostic(
            DiagnosticCode.KERNEL_NOT_MANIFEST_DATA,
            f"{key!r} is not workflow data: the frozen transition graph owns "
            f"{named}, and no manifest may add, remove or redirect them",
            location, klass)]
    diags = []
    for source_state, state in pairs:
        klass = classify_edge(source_state, state)
        diags.append(Diagnostic(
            DiagnosticCode.KERNEL_NOT_MANIFEST_DATA,
            f"{key!r} names {source_state.value} -> {state.value}, an edge of "
            f"the frozen transition graph"
            + (f" ({klass.value})" if klass else "")
            + "; a workflow may bind a LABEL to a kernel outcome, never its "
              "target, its predecessors, or its absence",
            location, klass))
    return diags


# --- phase 1: schema -------------------------------------------------------


def _schema_diagnostics(source: dict) -> list:
    import jsonschema  # LAZY -- see the module docstring.

    validator = jsonschema.Draft202012Validator(_schema())
    return [
        Diagnostic(DiagnosticCode.SCHEMA, err.message,
                   ".".join(str(p) for p in err.absolute_path) or "<document>")
        for err in sorted(validator.iter_errors(source),
                          key=lambda e: (list(str(p) for p in e.absolute_path), e.message))
    ]


# --- phase 2: nodes --------------------------------------------------------


@dataclass
class _Draft:
    """A node between phase 2 and phase 3: states and identity resolved,
    edges not yet."""

    node_id: str
    body: dict
    kind: NodeKind
    entry_state: TaskState
    exit_state: TaskState
    owns: frozenset
    handler: str | None
    role: Role | None
    prompt: str | None
    concurrency: object
    context: frozenset
    budget: int | None
    join: JoinSpec | None


def _node_diagnostics(source: dict) -> tuple:
    drafts: dict[str, _Draft] = {}
    diags: list = []
    for node_id in sorted(source["nodes"]):
        body = source["nodes"][node_id]
        loc = f"nodes.{node_id}"
        entry = _STATES_BY_NAME.get(body["entry_state"])
        exit_raw = body.get("exit_state", body["entry_state"])
        exit_state = _STATES_BY_NAME.get(exit_raw)
        if entry is None or exit_state is None:
            bad = body["entry_state"] if entry is None else exit_raw
            diags.append(Diagnostic(
                DiagnosticCode.UNKNOWN_STATE,
                f"{bad!r} is not a TaskState; known states: "
                f"{sorted(_STATES_BY_NAME)}", loc))
            continue

        handler = body.get("handler")
        if handler is None:
            kind = NodeKind.WAIT
            spec = None
        else:
            spec = HANDLER_REGISTRY.get(handler)
            if spec is None:
                diags.append(Diagnostic(
                    DiagnosticCode.UNKNOWN_HANDLER,
                    f"unknown handler {handler!r}; registered handlers: "
                    f"{sorted(HANDLER_REGISTRY)}", f"{loc}.handler"))
                continue
            kind = spec.kind

        role, role_diags = _resolve_role(body, kind, loc)
        diags.extend(role_diags)
        diags.extend(_prompt_diagnostics(body, kind, loc))
        diags.extend(_placement_diagnostics(entry, exit_state, kind, loc))

        context = frozenset(body.get("context", ()))
        unknown_flags = sorted(context - KNOWN_CONTEXT_FLAGS)
        if unknown_flags:
            diags.append(Diagnostic(
                DiagnosticCode.UNKNOWN_CONTEXT_FLAG,
                f"unknown packet context flag(s) {unknown_flags}; menu: "
                f"{sorted(KNOWN_CONTEXT_FLAGS)}", f"{loc}.context"))

        join = None
        if "join" in body:
            raw = body["join"]
            join = JoinSpec(raw["group"], JoinMode(raw["mode"]),
                            JoinCancel(raw["on_cancel"]))

        drafts[node_id] = _Draft(
            node_id=node_id, body=body, kind=kind, entry_state=entry,
            exit_state=exit_state, owns=frozenset({entry, exit_state}),
            handler=handler, role=role, prompt=body.get("prompt"),
            concurrency=body.get("concurrency", "serial"), context=context,
            budget=body.get("budget"), join=join)
    return drafts, diags


def _resolve_role(body: dict, kind: NodeKind, loc: str) -> tuple:
    raw = body.get("role")
    if kind is not NodeKind.AGENT:
        if raw is not None:
            return None, [Diagnostic(
                DiagnosticCode.HANDLER_ROLE_MISMATCH,
                f"only an agent node declares a role; this node is a "
                f"{kind.value} node", f"{loc}.role")]
        return None, []
    if raw is None:
        return None, [Diagnostic(
            DiagnosticCode.HANDLER_ROLE_MISMATCH,
            "an agent node must declare the role it dispatches", f"{loc}.role")]
    # Resolved through AGENT_OUTCOMES rather than through `Role(raw)`, so a
    # role added to types.py without an outcome vocabulary is an UNKNOWN_ROLE
    # refusal rather than a KeyError two phases later.
    role = _ROLES_BY_VALUE.get(raw)
    if role is None:
        return None, [Diagnostic(
            DiagnosticCode.UNKNOWN_ROLE,
            f"unknown role {raw!r}; roles with a declared outcome vocabulary: "
            f"{sorted(_ROLES_BY_VALUE)}", f"{loc}.role")]
    return role, []


def _prompt_diagnostics(body: dict, kind: NodeKind, loc: str) -> list:
    prompt = body.get("prompt")
    if kind is not NodeKind.AGENT:
        if prompt is not None:
            return [Diagnostic(
                DiagnosticCode.PROMPT_VERSION_MISSING,
                f"only an agent node carries a prompt; this node is a "
                f"{kind.value} node", f"{loc}.prompt")]
        return []
    if prompt is None or not _is_versioned_prompt(prompt):
        return [Diagnostic(
            DiagnosticCode.PROMPT_VERSION_MISSING,
            f"an agent node must name a VERSIONED prompt (`name/vN`), got "
            f"{prompt!r} -- the version is part of the execution-plan digest",
            f"{loc}.prompt")]
    return []


def _is_versioned_prompt(prompt: str) -> bool:
    head, sep, tail = prompt.rpartition("/v")
    return bool(head) and bool(sep) and tail.isdigit()


def _placement_diagnostics(entry: TaskState, exit_state: TaskState,
                           kind: NodeKind, loc: str) -> list:
    diags: list = []
    owned = {entry, exit_state}
    terminal = sorted(s.value for s in owned & TERMINAL_TASK_STATES)
    if terminal:
        diags.append(Diagnostic(
            DiagnosticCode.STATE_NOT_NODE_OWNABLE,
            f"no node may sit on the terminal state(s) {terminal}: a terminal "
            f"task has left the workflow", loc))
    kernel_owned = sorted(s.value for s in owned & KERNEL_OWNED_STATES)
    if kernel_owned and kind is not NodeKind.WAIT:
        diags.append(Diagnostic(
            DiagnosticCode.STATE_NOT_NODE_OWNABLE,
            f"{kernel_owned} are kernel-owned: intake, queue admission, the "
            f"human-decision hold and escalation move under kernel or operator "
            f"authority, so only a wait node (no handler) may sit there", loc))
    if kind is NodeKind.WAIT and not kernel_owned:
        diags.append(Diagnostic(
            DiagnosticCode.WAIT_NODE_PLACEMENT,
            f"a wait node declares no handler, so it must sit on a "
            f"kernel-owned state {sorted(s.value for s in KERNEL_OWNED_STATES)}; "
            f"this one sits on {entry.value}", loc))
    if entry is not exit_state and exit_state not in TASK_TRANSITIONS[entry]:
        diags.append(Diagnostic(
            DiagnosticCode.ILLEGAL_TRANSITION,
            f"{entry.value} -> {exit_state.value} is not a transition of the "
            f"frozen graph, so no node can be entered at {entry.value} and "
            f"exit from {exit_state.value}", loc))
    return diags


# --- phase 3: outcomes and edges -------------------------------------------


def _declared_outcomes(draft: _Draft) -> tuple:
    if draft.kind is NodeKind.WAIT:
        vocab = frozenset(w.value for w in WaitResume)
        return vocab, frozenset()
    spec = HANDLER_REGISTRY[draft.handler]
    if spec.outcomes is not None:
        return spec.outcomes, spec.required
    return AGENT_OUTCOMES[draft.role]


def _edge_diagnostics(drafts: dict) -> tuple:
    nodes: list = []
    diags: list = []
    for node_id in sorted(drafts):
        draft = drafts[node_id]
        loc = f"nodes.{node_id}"
        body = draft.body
        vocab, required = _declared_outcomes(draft)
        raw_outcomes = body.get("outcomes", {})
        raw_kernel = body.get("kernel_outcomes", {})
        bound = set(raw_outcomes) | set(raw_kernel)
        # Undeclared labels are reported, then DROPPED: resolving them would
        # ask the wait-node vocabulary to interpret a label it has no member
        # for, and a compiler that crashes on bad input is not a compiler.
        outcomes = {k: v for k, v in raw_outcomes.items() if k in vocab}
        kernel_outcomes = {k: v for k, v in raw_kernel.items() if k in vocab}

        for label in sorted(set(raw_outcomes) & set(raw_kernel)):
            diags.append(Diagnostic(
                DiagnosticCode.DUPLICATE_OUTCOME,
                f"outcome {label!r} is bound both to a node and to a kernel "
                f"verb; an outcome has exactly one destination", f"{loc}.outcomes"))
        for label in sorted(bound - vocab):
            diags.append(Diagnostic(
                DiagnosticCode.UNDECLARED_OUTCOME,
                f"outcome {label!r} is not declared by this node's result type; "
                f"declared: {sorted(vocab)}", f"{loc}.outcomes"))
        for label in sorted(required - bound):
            diags.append(Diagnostic(
                DiagnosticCode.UNROUTED_OUTCOME,
                f"required outcome {label!r} is not routed; an unrouted outcome "
                f"strands the task with no declared next step", loc))

        guards, guard_diags = _guard_refs(body.get("guards", {}), bound, loc)
        diags.extend(guard_diags)

        edges, edge_diags = _resolve_outcomes(draft, drafts, outcomes,
                                              kernel_outcomes, guards, loc)
        diags.extend(edge_diags)
        edges.extend(_inject_kernel_edges(draft, kernel_outcomes, guards))
        nodes.append(NodeIR(
            node_id=draft.node_id, kind=draft.kind, entry_state=draft.entry_state,
            exit_state=draft.exit_state, owns=draft.owns, handler=draft.handler,
            role=draft.role, prompt=draft.prompt, concurrency=draft.concurrency,
            context=draft.context, budget=draft.budget, join=draft.join,
            edges=tuple(edges)))
    return tuple(nodes), diags


def _guard_refs(raw: dict, bound: set, loc: str) -> tuple:
    guards: dict = {}
    diags: list = []
    for label in sorted(raw):
        spec = raw[label]
        if label not in bound:
            diags.append(Diagnostic(
                DiagnosticCode.GUARD_ON_UNBOUND_OUTCOME,
                f"guard declared for outcome {label!r}, which this node does "
                f"not route", f"{loc}.guards.{label}"))
            continue
        try:
            guards[label] = resolve_guard(spec["name"], spec.get("arg"))
        except GuardError as exc:
            code = (DiagnosticCode.UNKNOWN_GUARD if "unknown guard" in str(exc)
                    else DiagnosticCode.GUARD_ARGUMENT)
            diags.append(Diagnostic(code, str(exc), f"{loc}.guards.{label}"))
    return guards, diags


def _resolve_outcomes(draft: _Draft, drafts: dict, outcomes: dict,
                      kernel_outcomes: dict, guards: dict, loc: str) -> tuple:
    edges: list = []
    diags: list = []
    if draft.entry_state is not draft.exit_state and classify_edge(
            draft.entry_state, draft.exit_state) is None:
        # The node's own entry edge: `implement` is entered at QUEUED and its
        # outcome transitions from ACTIVE. A kernel entry edge (post_merge_gate's
        # MERGED -> VALIDATING) needs no declaration -- it is injected below.
        edges.append(EdgeIR(
            from_state=draft.entry_state, to_state=draft.exit_state,
            origin=EdgeOrigin.COMPILER, label="(entry)", target_node=draft.node_id))

    for label in sorted(outcomes):
        target = outcomes[label]
        where = f"{loc}.outcomes.{label}"
        if target in _STATES_BY_NAME:
            state = _STATES_BY_NAME[target]
            klass = classify_edge(draft.exit_state, state)
            if klass is not None:
                diags.append(Diagnostic(
                    DiagnosticCode.KERNEL_EDGE_NOT_DECLARABLE,
                    f"{draft.exit_state.value} -> {state.value} is a kernel edge "
                    f"({klass.value}); a workflow may bind a label to it through "
                    f"`kernel_outcomes`, but may never declare, redirect or add "
                    f"it -- the frozen graph owns where it goes",
                    where, klass))
            else:
                diags.append(Diagnostic(
                    DiagnosticCode.STATE_IS_NOT_A_NODE,
                    f"an outcome names a NODE, not the state {state.value}: a "
                    f"lifecycle mutation outside the kernel is not expressible",
                    where))
            continue
        if target not in drafts:
            diags.append(Diagnostic(
                DiagnosticCode.ABSENT_NODE,
                f"outcome {label!r} routes to absent node {target!r}; known "
                f"nodes: {sorted(drafts)}", where))
            continue
        to_state = drafts[target].entry_state
        klass = classify_edge(draft.exit_state, to_state)
        if klass is not None:
            diags.append(Diagnostic(
                DiagnosticCode.KERNEL_EDGE_NOT_DECLARABLE,
                f"outcome {label!r} would take {draft.exit_state.value} -> "
                f"{to_state.value}, a kernel edge ({klass.value}); the "
                f"predecessor set of {to_state.value} is not workflow data",
                where, klass))
            continue
        if (draft.exit_state, to_state) not in COMPILER_EDGES:
            diags.append(Diagnostic(
                DiagnosticCode.ILLEGAL_TRANSITION,
                f"outcome {label!r} would take {draft.exit_state.value} -> "
                f"{to_state.value}, which is not a transition of the frozen "
                f"graph", where))
            continue
        if draft.kind is NodeKind.WAIT and to_state not in WAIT_RESUME_REGIONS[
                WaitResume(label)]:
            diags.append(Diagnostic(
                DiagnosticCode.WAIT_RESUME_REGION,
                f"{label!r} must resume into "
                f"{sorted(s.value for s in WAIT_RESUME_REGIONS[WaitResume(label)])}, "
                f"not {to_state.value}", where))
            continue
        edges.append(EdgeIR(
            from_state=draft.exit_state, to_state=to_state,
            origin=EdgeOrigin.COMPILER, label=label, target_node=target,
            guard=guards.get(label)))

    for label in sorted(kernel_outcomes):
        where = f"{loc}.kernel_outcomes.{label}"
        raw = kernel_outcomes[label]
        try:
            verb = KernelVerb(raw)
        except ValueError:
            diags.append(Diagnostic(
                DiagnosticCode.KERNEL_VERB_UNAVAILABLE,
                f"unknown kernel verb {raw!r}; the closed vocabulary is "
                f"{sorted(v.value for v in KernelVerb)}", where))
            continue
        if verb not in KERNEL_VERBS_BY_STATE[draft.exit_state]:
            klass = _VERB_CLASSES[verb]
            diags.append(Diagnostic(
                DiagnosticCode.KERNEL_VERB_UNAVAILABLE,
                f"the frozen graph gives {draft.exit_state.value} no "
                f"{verb.value!r} edge ({klass.value}); available here: "
                f"{sorted(v.value for v in KERNEL_VERBS_BY_STATE[draft.exit_state])}",
                where, klass))
    return edges, diags


def _inject_kernel_edges(draft: _Draft, kernel_outcomes: dict, guards: dict) -> list:
    """Attach every kernel edge of every state this node owns.

    This is the whole of "unrepresentable to remove": the manifest is not
    consulted about WHETHER these exist, only about what to call them. Labels
    bind on the node's exit state, because that is where an outcome is reported
    from.
    """
    labels: dict = {}
    for label, raw in kernel_outcomes.items():
        try:
            verb = KernelVerb(raw)
        except ValueError:  # already reported in _resolve_outcomes
            continue
        if verb in KERNEL_VERBS_BY_STATE[draft.exit_state]:
            labels[verb] = label
    edges: list = []
    for state in sorted(draft.owns, key=lambda s: s.value):
        for to_state, klass, verb in KERNEL_EDGES_BY_STATE[state]:
            label = labels.get(verb) if state is draft.exit_state else None
            edges.append(EdgeIR(
                from_state=state, to_state=to_state, origin=EdgeOrigin.KERNEL,
                label=label, kernel_class=klass, verb=verb,
                guard=guards.get(label) if label else None))
    return edges


# --- phase 4: whole-graph properties ---------------------------------------


def _graph_diagnostics(nodes: tuple, start: str) -> list:
    diags: list = []
    by_id = {n.node_id: n for n in nodes}
    if start not in by_id:
        return [Diagnostic(
            DiagnosticCode.UNKNOWN_START,
            f"start names absent node {start!r}; known nodes: {sorted(by_id)}",
            "start")]

    diags.extend(_ownership_diagnostics(nodes))
    diags.extend(_reachability_diagnostics(nodes, by_id, start))
    diags.extend(_cycle_diagnostics(nodes, by_id))
    diags.extend(_authority_diagnostics(nodes))
    return diags


def _ownership_diagnostics(nodes: tuple) -> list:
    diags: list = []
    owners: dict = {}
    for node in nodes:
        for state in node.owns:
            owners.setdefault(state, []).append(node)
    for state in sorted(owners, key=lambda s: s.value):
        group = owners[state]
        if len(group) == 1:
            continue
        # Parallel branches on one position are legal only as a typed join
        # group (§4.3 condition 8) -- otherwise two nodes own one state and
        # nothing says which result wins, or what happens to the loser.
        names = sorted(n.node_id for n in group)
        ungrouped = sorted(n.node_id for n in group if n.join is None)
        if ungrouped:
            diags.append(Diagnostic(
                DiagnosticCode.JOIN_POLICY_MISSING,
                f"{names} are parallel branches on {state.value} but "
                f"{ungrouped} declare no join policy",
                f"nodes.{ungrouped[0]}.join"))
        elif len({(n.join.group, n.join.mode, n.join.on_cancel)
                  for n in group}) > 1:
            diags.append(Diagnostic(
                DiagnosticCode.JOIN_POLICY_INCONSISTENT,
                f"parallel branches on {state.value} disagree about their join "
                f"policy: {names}", f"nodes.{names[0]}.join"))
        elif len({(n.handler, n.role) for n in group}) == 1:
            diags.append(Diagnostic(
                DiagnosticCode.DUPLICATE_STATE_OWNER,
                f"{names} are indistinguishable branches on {state.value}: same "
                f"handler and role, so a join cannot tell their results apart",
                f"nodes.{names[0]}"))
    return diags


def _reachability_diagnostics(nodes: tuple, by_id: dict, start: str) -> list:
    diags: list = []
    entries: dict = {}
    for node in nodes:
        entries.setdefault(node.entry_state, []).append(node.node_id)
    reached = {start}
    frontier = [start]
    while frontier:
        node = by_id[frontier.pop()]
        for edge in node.edges:
            for nxt in entries.get(edge.to_state, ()):
                if nxt not in reached:
                    reached.add(nxt)
                    frontier.append(nxt)
    for node in nodes:
        if node.node_id not in reached:
            diags.append(Diagnostic(
                DiagnosticCode.UNREACHABLE_NODE,
                f"no edge from {start!r} reaches this node; an unreachable node "
                f"is either dead workflow or a routing mistake",
                f"nodes.{node.node_id}"))

    # The success path. K-escape makes SUPERSEDED and CANCELLED reachable from
    # every non-terminal node, so "a terminal state is reachable" is satisfied
    # by the kernel and says nothing about the workflow. The property that
    # carries information is that a task can COMPLETE: follow the declared
    # compiler edges and the kernel `advance` chain only.
    owned = frozenset(s for n in nodes for s in n.owns)
    closure = kernel_closure(owned)
    success: dict = {}
    for node in nodes:
        for edge in node.edges:
            if edge.origin is EdgeOrigin.COMPILER or edge.verb is KernelVerb.ADVANCE:
                success.setdefault(edge.from_state, set()).add(edge.to_state)
    for state in closure:
        for to_state, _klass, verb in KERNEL_EDGES_BY_STATE[state]:
            if verb is KernelVerb.ADVANCE:
                success.setdefault(state, set()).add(to_state)
    seen = {by_id[start].entry_state}
    walk = [by_id[start].entry_state]
    while walk:
        state = walk.pop()
        for to_state in success.get(state, ()):
            if to_state not in seen:
                seen.add(to_state)
                walk.append(to_state)
    if TaskState.COMPLETED not in seen:
        diags.append(Diagnostic(
            DiagnosticCode.NO_SUCCESS_PATH,
            f"no path of declared outcomes and kernel `advance` edges reaches "
            f"{TaskState.COMPLETED.value} from {start!r}; the workflow can "
            f"start work it can never finish", "start"))
    return diags


def _cycle_diagnostics(nodes: tuple, by_id: dict) -> list:
    """§4.3 condition 5: a cycle with no statically bounded counter.

    Tarjan over the COMPILER edges only. Kernel edges cannot form a workflow
    loop -- every kernel target is either terminal, a wait, or the one-way
    merge spine -- so including them would report a cycle the workflow does not
    control and cannot bound.
    """
    graph: dict = {n.node_id: set() for n in nodes}
    for node in nodes:
        for edge in node.compiler_edges():
            if edge.target_node in graph:
                graph[node.node_id].add(edge.target_node)
    diags: list = []
    for component in _strong_components(graph):
        self_loop = len(component) == 1 and next(iter(component)) in graph[
            next(iter(component))]
        if len(component) == 1 and not self_loop:
            continue
        if any(by_id[n].budget is not None for n in component):
            continue
        diags.append(Diagnostic(
            DiagnosticCode.UNBOUNDED_CYCLE,
            f"the cycle {sorted(component)} has no statically bounded "
            f"retry/escalation counter; declare `budget` on one of its nodes",
            f"nodes.{sorted(component)[0]}"))
    return diags


def _strong_components(graph: dict) -> list:
    """Tarjan's SCC, iterative (a workflow is small, but recursion depth is not
    a property a compiler should depend on)."""
    index: dict = {}
    low: dict = {}
    stack: list = []
    on_stack: set = set()
    result: list = []
    counter = 0
    for root in sorted(graph):
        if root in index:
            continue
        work = [(root, iter(sorted(graph[root])))]
        index[root] = low[root] = counter
        counter += 1
        stack.append(root)
        on_stack.add(root)
        while work:
            node, children = work[-1]
            advanced = False
            for child in children:
                if child not in index:
                    index[child] = low[child] = counter
                    counter += 1
                    stack.append(child)
                    on_stack.add(child)
                    work.append((child, iter(sorted(graph[child]))))
                    advanced = True
                    break
                if child in on_stack:
                    low[node] = min(low[node], index[child])
            if advanced:
                continue
            work.pop()
            if work:
                low[work[-1][0]] = min(low[work[-1][0]], low[node])
            if low[node] == index[node]:
                component = set()
                while True:
                    popped = stack.pop()
                    on_stack.discard(popped)
                    component.add(popped)
                    if popped == node:
                        break
                result.append(component)
    return result


def _authority_diagnostics(nodes: tuple) -> list:
    """§4.3 conditions 6 and 7, as structure.

    Condition 6's approval half: if the compiled workflow can reach
    ``MERGE_READY`` at all, some node must be the declared producer of the
    independent approval, and it must be an independent reviewer. Condition 7:
    no agent-driven node may hold merge or post-merge authority, and no agent
    may approve its own work.

    The pre-merge GATE half of condition 6 is deliberately not checked here:
    gates are project configuration (``[gates.*]``), not workflow structure,
    and the merge effector runs them on the merged tree before publication.
    What this module can guarantee -- and does -- is that the merge node is a
    MECHANISM node no agent can drive, so no agent can reach past the gate.
    """
    diags: list = []
    owned = frozenset(s for n in nodes for s in n.owns)
    approver = None
    for node in nodes:
        merge_authority = node.owns & {
            TaskState.MERGE_READY, TaskState.MERGED, TaskState.VALIDATING}
        if node.kind is NodeKind.AGENT and merge_authority:
            diags.append(Diagnostic(
                DiagnosticCode.AGENT_SELF_AUTHORITY,
                f"an agent node may not own "
                f"{sorted(s.value for s in merge_authority)}: merge and "
                f"post-merge validation are mechanism authority, so an agent "
                f"cannot emit a merge directly", f"nodes.{node.node_id}",
                KernelEdgeClass.MERGE_SPINE))
        advances = [e for e in node.kernel_edges() if e.verb is KernelVerb.ADVANCE
                    and e.label is not None and e.from_state is node.exit_state]
        if not advances:
            continue
        if node.exit_state is TaskState.AWAITING_REVIEW:
            if node.kind is NodeKind.AGENT and node.role is Role.REVIEW_INDEPENDENT:
                approver = node
            else:
                diags.append(Diagnostic(
                    DiagnosticCode.AGENT_SELF_AUTHORITY,
                    f"only an independent reviewer may produce the approval "
                    f"that advances AWAITING_REVIEW; this node is "
                    f"{node.kind.value}"
                    + (f"/{node.role.value}" if node.role else "")
                    + " (approval-for-self)", f"nodes.{node.node_id}",
                    KernelEdgeClass.MERGE_SPINE))
        elif node.kind is NodeKind.AGENT:
            diags.append(Diagnostic(
                DiagnosticCode.AGENT_SELF_AUTHORITY,
                f"an agent node may not bind the merge-spine `advance` verb at "
                f"{node.exit_state.value}", f"nodes.{node.node_id}",
                KernelEdgeClass.MERGE_SPINE))
    if TaskState.MERGE_READY in kernel_closure(owned) and approver is None:
        diags.append(Diagnostic(
            DiagnosticCode.MERGE_WITHOUT_REVIEW,
            "this workflow can reach MERGE_READY but declares no independent "
            "reviewer that produces the approval; removing the review "
            "prerequisite from a merge path is not compilable",
            "nodes", KernelEdgeClass.MERGE_SPINE))
    return diags


# --- the entry point -------------------------------------------------------


def compile_workflow(source: dict, *, origin: str = "<memory>") -> WorkflowIR:
    """Compile a workflow source document into a validated :class:`WorkflowIR`.

    Raises :class:`CompileError` carrying every diagnostic the failing phase
    produced. Phases run in order and stop at the first that refuses, because a
    later phase's checks are only meaningful over data an earlier one accepted
    -- reporting "unreachable node" for a document whose states did not parse
    is noise, not diagnosis.
    """
    _raise_if(_reserved_diagnostics(source), origin)
    _raise_if(_schema_diagnostics(source), origin)
    drafts, node_diags = _node_diagnostics(source)
    _raise_if(node_diags, origin)
    nodes, edge_diags = _edge_diagnostics(drafts)
    _raise_if(edge_diags, origin)
    _raise_if(_graph_diagnostics(nodes, source["start"]), origin)
    return WorkflowIR(workflow_id=source["id"], version=source["version"],
                      start=source["start"], nodes=nodes)


def _raise_if(diags: list, origin: str) -> None:
    if diags:
        raise CompileError(tuple(diags), origin)


def kernel_edge_report(ir: WorkflowIR) -> dict:
    """Every kernel edge the compiled workflow carries, by class.

    The observable the negative corpus asserts against: "this class of edge is
    present whatever the manifest said". Kept in production rather than in a
    test helper so an operator can ask the same question of a live workflow.
    """
    report: dict = {}
    for node in ir.nodes:
        for edge in node.kernel_edges():
            report.setdefault(edge.kernel_class, set()).add(
                (edge.from_state, edge.to_state))
    return {k: frozenset(v) for k, v in report.items()}


def expected_kernel_edges(states: frozenset) -> frozenset:
    """The complete set of kernel edges a workflow owning ``states`` must
    carry -- the oracle side of "unrepresentable to remove"."""
    return frozenset((frm, to) for (frm, to) in KERNEL_EDGES if frm in states)
