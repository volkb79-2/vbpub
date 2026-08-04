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
the 16 compiler edges (17 before CR-07d removed ``DRAFT -> READY_TO_CARVE``
along with the state itself) are the entire set the language must express, and
if any one of them needs a per-node escape hatch into imperative code, CR-07
stops. :func:`full_vocabulary_sources` declares all 16 as data, so the trigger
is answered by a compile rather than by an opinion.

Two of the 16 -- ``ACTIVE -> AWAITING_REVIEW`` and ``ACTIVE -> SELF_REVIEWING``
-- are the SAME outcome (``implement.done``) with different destinations, so no
single document can carry both; they are alternatives in the product too. The
stop-loss asks whether the LANGUAGE can express each edge, so the instrument is
two documents whose compiler-edge sets UNION to all 16. Claiming one document
would have meant either inventing a second implementer node (two nodes owning
``ACTIVE``) or an ``if``, and the second is the trigger itself.

WHAT THE PROJECTION HAD TO ADD, AND WHY IT IS NOT AN ESCAPE HATCH
-----------------------------------------------------------------
One addition, DATA, forced by the IR being stricter than ``validate_pipeline``
-- which is the point of the exercise rather than a concession:

**Wait nodes for the lifecycle hand-off.** ``stages.LIFECYCLE_STATES`` exempts
``CARVED``/``NEEDS_DECISION``/``BLOCKED`` from the dead-end rule on
the grounds that "a stage may route a task INTO one of these; the mechanism
(or a human) carries it onward". It never says WHERE onward is. A compiler
outcome into a kernel-owned state must name a wait node here, so ``carve``'s
``done -> CARVED`` acquires the declared continuation ``CARVED -> QUEUED``
that queue admission has always performed in ``reconcile``. The exemption
becomes an obligation; nothing became code.

THE SELF-REVIEW ROUTING (CR-07a found it, CR-07c closed it)
-------------------------------------------------------------
CR-07a found that ``stages.py`` declared ``implement``'s ``done`` exit as
``-> AWAITING_REVIEW`` unconditionally while the engine
(``effects_exit.py``) routed ``done -> SELF_REVIEWING`` whenever
``self_review`` was composed -- a real, live defect (every shipped preset
composes ``self_review``, so the declared record was wrong since B5,
2026-07-20) that ``validate_pipeline`` could not see: rule 3 treats a stage's
``entry_state`` as "handled" by virtue of existing, so nothing checked that
anything routed INTO it. CR-07a pinned the disagreement (compiling the
declared form of every self-reviewing preset raised ``UNREACHABLE_NODE``)
rather than fixing it, on the grounds that repairing the layer being replaced
belongs to the package that replaces it.

CR-07c is that repair: ``stages.effective_exit_map`` is now the SINGLE
function ``validate_pipeline``, ``effects_exit.py``'s dispatch and this
module's :func:`preset_source` all read to resolve ``implement.done``'s
target for a given pipeline, so the declared and the actual form cannot
diverge again the way they did before -- there is only one place left that
knows the rule. This module no longer needs a second, projection-local copy
of it (the deleted ``_successor_state``), nor a document that is deliberately
compiled and refused to prove the two disagreed (the deleted
``declared_source`` / ``DECLARED_DIVERGENCES``): ``preset_source`` IS the
declared form now, because the declaration itself is composition-aware.

This module is the only one in the package that imports ``stages``. The
compiler must not know what a stage is; the thing that compares them must.
"""

from __future__ import annotations

from .stages import PRESETS, STAGE_REGISTRY, Stage, effective_exit_map
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
#: ``self_review``. Used by the full-vocabulary shadow (the second document in
#: :func:`full_vocabulary_sources`) and by the corpus's "shadow-legacy" case.
LEGACY_PIPELINE: tuple = (
    "carve", "implement", "review_independent", "triage", "auto_merge",
    "post_merge_gate",
)

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
        for label, target in effective_exit_map(stage, pipeline):
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


def _full_vocabulary_base() -> dict:
    return {
        "schema": SCHEMA_ID,
        "id": "full-vocabulary",
        "version": 1,
        # CR-07d: DRAFT (the "intake" wait node) is gone -- daemon.py's
        # CreateTask hardcodes CARVED, so queue_admission is the document's
        # real entry point, not a projection convenience.
        "start": "queue_admission",
        "nodes": {
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
    """Two workflows whose compiler edges UNION to all 16 of the inventory's.

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
