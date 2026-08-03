"""Shadow compilation: the shipped stage presets, expressed as workflow source.

WHY THIS MODULE EXISTS (CR-07a)
-------------------------------
"The compiler works" is not a claim a test can fail. "The workflow the product
ships today compiles, and its compiled form says exactly what ``stages.py``
says" is. This module PROJECTS a stage pipeline into a workflow source document
so ``tests/test_workflow_shadow.py`` can compare the compiled IR against the
stage registry edge by edge -- the §5.4 differential for a package that adds no
behaviour, applied to the only population that matters: what is running.

It is also the stop-loss instrument. The inventory makes the trigger countable:
the 17 compiler edges are the entire set the language must express, and if any
one of them needs a per-node escape hatch into imperative code, CR-07 stops.
:func:`full_vocabulary_sources` declares all 17 as data, so the trigger is
answered by a compile rather than by an opinion.

Two of the 17 -- ``ACTIVE -> AWAITING_REVIEW`` and ``ACTIVE -> SELF_REVIEWING``
-- are the SAME outcome (``implement.done``) with different destinations, so no
single document can carry both; they are alternatives in the product too. The
stop-loss asks whether the LANGUAGE can express each edge, so the instrument is
two documents whose compiler-edge sets UNION to all 17. Claiming one document
would have meant either inventing a second implementer node (two nodes owning
``ACTIVE``) or an ``if``, and the second is the trigger itself.

WHAT THE PROJECTION HAD TO ADD, AND WHY IT IS NOT AN ESCAPE HATCH
-----------------------------------------------------------------
Two additions, both DATA, both forced by the IR being stricter than
``validate_pipeline`` -- which is the point of the exercise rather than a
concession:

1. **Wait nodes for the lifecycle hand-off.** ``stages.LIFECYCLE_STATES``
   exempts ``CARVED``/``NEEDS_DECISION``/``BLOCKED``/``DRAFT`` from the
   dead-end rule on the grounds that "a stage may route a task INTO one of
   these; the mechanism (or a human) carries it onward". It never says WHERE
   onward is. A compiler outcome into a kernel-owned state must name a wait
   node here, so ``carve``'s ``done -> CARVED`` acquires the declared
   continuation ``CARVED -> QUEUED`` that queue admission has always performed
   in ``reconcile``. The exemption becomes an obligation; nothing became code.

2. **The self-review routing.** ``stages.py`` declares ``implement``'s ``done``
   exit as ``-> AWAITING_REVIEW`` unconditionally, but the engine routes
   ``done -> SELF_REVIEWING`` whenever ``self_review`` is composed
   (``effects_exit.py``: ``if attempt.role == Role.IMPLEMENTER and
   "self_review" in cfg.pipeline``). The declared record and the behaviour
   disagree, and ``validate_pipeline`` cannot see it: rule 3 treats a stage's
   ``entry_state`` as "handled", so ``self_review`` counts as handled by virtue
   of EXISTING, and nothing checks that anything routes into it. Under this
   compiler the declared form of the same pipeline fails
   ``UNREACHABLE_NODE`` -- ``tests/test_workflow_shadow.py`` pins that as a real
   defect found rather than as a projection convenience.

   The projection therefore routes ``done`` to the node that actually receives
   it. That is the conditional DISAPPEARING into data, which is the opposite of
   an escape hatch: the ``if`` in ``effects_exit.py`` exists because a stage
   record cannot say "in this pipeline, done goes here", and a manifest can.
   :data:`DECLARED_DIVERGENCES` names it, singular and exact, and the shadow of
   :data:`LEGACY_PIPELINE` (the same flow without ``self_review``) is equality
   with nothing subtracted -- which is what proves the divergence is the
   self-review adjacency and nothing else.

This module is the only one in the package that imports ``stages``. The
compiler must not know what a stage is; the thing that compares them must.
"""

from __future__ import annotations

from .stages import PRESETS, STAGE_REGISTRY, Stage
from .types import Role, TaskState
from .workflow_ir import (
    COMPILER_EDGES,
    KERNEL_OWNED_STATES,
    SCHEMA_ID,
    WaitResume,
    classify_edge,
    verb_for,
)

#: The pipeline that is byte-identical to the pre-B5 engine: every preset minus
#: ``self_review``. Shadowing it is how :data:`DECLARED_DIVERGENCES` is proved
#: to be exactly one thing rather than a general licence.
LEGACY_PIPELINE: tuple = (
    "carve", "implement", "review_independent", "triage", "auto_merge",
    "post_merge_gate",
)

#: The single point at which a compiled preset differs from what ``stages.py``
#: DECLARES, with the production site that decides it. Read by the shadow test,
#: which subtracts exactly this and demands equality on everything else.
DECLARED_DIVERGENCES: dict = {
    "implement.done": (
        "stages.py declares implement's `done` exit as -> AWAITING_REVIEW "
        "unconditionally; effects_exit.py routes it to SELF_REVIEWING whenever "
        "the self_review stage is composed. The projection follows the engine, "
        "not the declaration."),
}

#: Prompt names the projection assigns per role. CR-07a has no prompt registry
#: -- prompts are wrapper/adapter assets -- so these exist to satisfy §4.3
#: condition 9 (a prompt VERSION is part of the execution-plan digest) and are
#: replaced by real names when CR-07b lands the prompt catalogue.
SHADOW_PROMPTS: dict = {
    Role.CARVER: "carve/v1",
    Role.IMPLEMENTER: "implement/v1",
    Role.SELF_REVIEW: "self_review/v1",
    Role.REVIEW_INDEPENDENT: "review_independent/v1",
}

#: The wait node id for each kernel-owned state a projection may need.
WAIT_NODE_IDS: dict = {
    TaskState.DRAFT: "intake",
    TaskState.CARVED: "queue_admission",
    TaskState.NEEDS_DECISION: "decision_hold",
    TaskState.BLOCKED: "escalation_hold",
}

#: Which stage owns the region each :class:`WaitResume` label resumes into.
_RESUME_STAGES: dict = {
    WaitResume.RESUME_IMPLEMENT: "implement",
    WaitResume.RESUME_CARVE: "carve",
}

#: The attempt budget the projection declares on ``implement``. §4.3 condition
#: 5 requires the implement/review/triage cycle -- the product's one real loop
#: -- to carry a statically bounded counter, and the attempt budget is what
#: bounds it in the engine today.
SHADOW_ATTEMPT_BUDGET = 3


class UnownedTarget(KeyError):
    """A projected pipeline routes into a state no stage in it owns.

    ``validate_pipeline`` rule 3 refuses the same shape; raising rather than
    emitting ``None`` keeps the projection honest instead of handing the
    compiler a document with a hole in it.
    """


def _concurrency(stage: Stage) -> object:
    return "inherit" if stage.concurrency is None else stage.concurrency


def _successor_state(stage: Stage, label: str, declared: TaskState,
                     pipeline: tuple) -> TaskState:
    """The state a stage exit ACTUALLY lands in for this pipeline.

    One rule, and it is the one ``effects_exit.py`` implements: an
    implementer's ``done`` goes to the self-review leg when the pipeline has
    one. See :data:`DECLARED_DIVERGENCES`.
    """
    if stage.name == "implement" and label == "done" and "self_review" in pipeline:
        return TaskState.SELF_REVIEWING
    return declared


def _owner_of(state: TaskState, pipeline: tuple) -> str:
    for name in pipeline:
        stage = STAGE_REGISTRY[name]
        if state in stage.owns or stage.entry_state is state:
            return name
    raise UnownedTarget(
        f"no stage in {list(pipeline)} owns or enters at {state.value}")


def preset_source(pipeline: tuple, *, workflow_id: str = "shadow",
                  version: int = 1) -> dict:
    """Project a stage pipeline into a workflow source document.

    Pure and total over the shipped presets. A pipeline naming an unknown stage
    raises ``KeyError`` from ``STAGE_REGISTRY`` and one routing into an unowned
    state raises :class:`UnownedTarget` -- both refusals ``validate_pipeline``
    also makes, kept here so a projection failure is never mistaken for a
    compiler failure.
    """
    pipeline = tuple(pipeline)
    nodes: dict = {}
    wait_states: set = set()
    for name in pipeline:
        stage = STAGE_REGISTRY[name]
        node: dict = {"entry_state": stage.entry_state.value,
                      "exit_state": stage.exit_from.value}
        if stage.role is not None:
            node["handler"] = "dispatch_agent"
            node["role"] = stage.role.value
            node["prompt"] = SHADOW_PROMPTS[stage.role]
        else:
            node["handler"] = name
        node["concurrency"] = _concurrency(stage)
        if stage.context:
            node["context"] = sorted(stage.context)
        outcomes: dict = {}
        kernel_outcomes: dict = {}
        for label, declared in stage.exit_map:
            target = _successor_state(stage, label, declared, pipeline)
            if classify_edge(stage.exit_from, target) is not None:
                kernel_outcomes[label] = verb_for(stage.exit_from, target).value
            elif target in KERNEL_OWNED_STATES:
                wait_states.add(target)
                outcomes[label] = WAIT_NODE_IDS[target]
            else:
                outcomes[label] = _owner_of(target, pipeline)
        if outcomes:
            node["outcomes"] = outcomes
        if kernel_outcomes:
            node["kernel_outcomes"] = kernel_outcomes
        if name == "implement":
            node["budget"] = SHADOW_ATTEMPT_BUDGET
        nodes[name] = node

    for state in sorted(wait_states, key=lambda s: s.value):
        nodes[WAIT_NODE_IDS[state]] = _wait_node(state, pipeline)

    return {
        "schema": SCHEMA_ID,
        "id": workflow_id,
        "version": version,
        "start": pipeline[0],
        "nodes": nodes,
    }


def _wait_node(state: TaskState, pipeline: tuple) -> dict:
    outcomes: dict = {}
    for label, stage_name in _RESUME_STAGES.items():
        if stage_name not in pipeline:
            continue
        target = STAGE_REGISTRY[stage_name].entry_state
        if (state, target) in COMPILER_EDGES:
            outcomes[label.value] = stage_name
    return {"entry_state": state.value, "outcomes": outcomes}


def declared_source(pipeline: tuple, **kwargs) -> dict:
    """The projection of what ``stages.py`` DECLARES, divergences included.

    Identical to :func:`preset_source` except that ``implement.done`` follows
    the stage record rather than the engine. It exists to be COMPILED AND
    REFUSED: for any preset containing ``self_review`` it produces an
    unreachable node, which is the defect described in this module's docstring
    made executable.
    """
    source = preset_source(pipeline, **kwargs)
    if "implement" in source["nodes"] and "self_review" in pipeline:
        source["nodes"]["implement"]["outcomes"]["done"] = "review_independent"
    return source


def _full_vocabulary_base() -> dict:
    return {
        "schema": SCHEMA_ID,
        "id": "full-vocabulary",
        "version": 1,
        "start": "intake",
        "nodes": {
            "intake": {
                "entry_state": "DRAFT",
                "outcomes": {"resume_carve": "carve"},
            },
            "carve": {
                "handler": "dispatch_agent", "role": "carver",
                "prompt": "carve/v1",
                "entry_state": "READY_TO_CARVE", "exit_state": "READY_TO_CARVE",
                "context": ["spine-digest"],
                "outcomes": {"done": "queue_admission"},
                "kernel_outcomes": {"needs_decision": "decide",
                                    "rescope_superseded": "supersede"},
            },
            "queue_admission": {
                "entry_state": "CARVED",
                "outcomes": {"resume_implement": "implement"},
            },
            "decision_hold": {
                "entry_state": "NEEDS_DECISION",
                "outcomes": {"resume_implement": "implement",
                             "resume_carve": "carve"},
            },
            "escalation_hold": {
                "entry_state": "BLOCKED",
                "outcomes": {"resume_implement": "implement",
                             "resume_carve": "carve"},
            },
            "implement": {
                "handler": "dispatch_agent", "role": "implementer",
                "prompt": "implement/v1",
                "entry_state": "QUEUED", "exit_state": "ACTIVE",
                "concurrency": "inherit", "budget": SHADOW_ATTEMPT_BUDGET,
                "outcomes": {"done": "self_review", "incomplete": "implement"},
                "kernel_outcomes": {"dead_end": "block"},
                "guards": {"done": {"name": "touches_tests"},
                           "incomplete": {"name": "attempts_remaining"}},
            },
            "self_review": {
                "handler": "dispatch_agent", "role": "self-review",
                "prompt": "self_review/v1",
                "entry_state": "SELF_REVIEWING", "exit_state": "SELF_REVIEWING",
                "context": ["session-reuse"],
                "outcomes": {"approved": "review", "rejected": "implement"},
            },
            "review": {
                "handler": "dispatch_agent", "role": "review-independent",
                "prompt": "review_independent/v1",
                "entry_state": "AWAITING_REVIEW", "exit_state": "AWAITING_REVIEW",
                "context": ["session-reuse", "spine-digest"],
                "outcomes": {"rejected": "triage"},
                "kernel_outcomes": {"approved": "advance"},
            },
            "triage": {
                "handler": "triage",
                "entry_state": "REVIEW_REJECTED", "exit_state": "REVIEW_REJECTED",
                "outcomes": {"fixable": "implement", "rescope": "carve"},
                "kernel_outcomes": {"exhausted": "decide"},
            },
            "merge": {
                "handler": "auto_merge",
                "entry_state": "MERGE_READY", "exit_state": "MERGE_READY",
                "outcomes": {"rejected": "triage"},
                "kernel_outcomes": {"merged": "advance"},
            },
            "post_merge": {
                "handler": "post_merge_gate",
                "entry_state": "MERGED", "exit_state": "VALIDATING",
                "kernel_outcomes": {"pass": "advance", "fail": "block"},
            },
        },
    }


def full_vocabulary_sources() -> tuple:
    """Two workflows whose compiler edges UNION to all 17 of the inventory's.

    The second is the first without ``self_review``, which is the only way to
    express ``ACTIVE -> AWAITING_REVIEW`` -- see the module docstring.
    """
    with_self_review = _full_vocabulary_base()
    without = _full_vocabulary_base()
    without["id"] = "full-vocabulary-legacy"
    del without["nodes"]["self_review"]
    without["nodes"]["implement"]["outcomes"]["done"] = "review"
    return (with_self_review, without)


def preset_names() -> tuple:
    """The shipped preset names, in a stable order."""
    return tuple(sorted(PRESETS))
