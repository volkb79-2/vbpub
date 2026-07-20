"""Flow stages — the stages-as-data layer (D-060; docs/spec-flow-stages.md).

A Stage is a registered, code-backed record composing the FROZEN TaskState
graph (types.TASK_TRANSITIONS) into a per-project pipeline. This module is the
mechanism/policy seam: the stage KINDS and their state-region ownership are
mechanism (frozen, invariant-tested here); the per-project `pipeline` list that
chooses and orders them is policy (parsed in config.py, honoured in reconcile).

`validate_pipeline` is the P43 closure invariant promoted from *declaration* to
*composition*: every stage's exit edges must be real TASK_TRANSITIONS edges, and
no stage may route a task into a state that nothing in the pipeline owns or
handles (a dead-end). A project literally cannot express a merge-without-review
or a state no stage owns, even by fat-fingering the config.

B2 (P70) introduces this layer and honours exactly ONE composition axis in the
engine: `post_merge_gate` presence (reconcile.py item 11). The declarative
exit_maps are grounded in the CURRENT reconcile.py behaviour so the default
pipeline plans byte-identically (parity). B3–B7 deepen the engine (per-stage
concurrency, triage routing, the SELF_REVIEWING state, session-reuse, re-scope).
"""

from __future__ import annotations

from dataclasses import dataclass

from .types import TaskState, Role, TASK_TRANSITIONS, TERMINAL_TASK_STATES

# States handled by the frozen mechanism or by manual operator action, never
# owned by a composable stage: intake (DRAFT, NEEDS_DECISION), queue admission
# (CARVED -> QUEUED), and escalation (BLOCKED). A stage may route a task INTO
# one of these; the mechanism (or a human) carries it onward, so such an exit is
# never a dead-end.
LIFECYCLE_STATES: frozenset[TaskState] = frozenset({
    TaskState.DRAFT, TaskState.NEEDS_DECISION, TaskState.CARVED, TaskState.BLOCKED,
})
# The post-merge region is owned by post_merge_gate when that stage is present,
# and auto-advanced by the mechanism (VALIDATING -> COMPLETED) when it is not --
# either way an exit into it is not a dead-end.
_POST_MERGE_STATES: frozenset[TaskState] = frozenset({
    TaskState.MERGED, TaskState.VALIDATING,
})


@dataclass(frozen=True)
class Stage:
    name: str
    role: Role | None                 # prompt + packet builder come from the role
    entry_state: TaskState            # the state a task is in when this stage runs
    exit_from: TaskState              # the state its outcome transitions FROM
    exit_map: tuple                   # ((label, TaskState), ...) -- outcome -> target
    owns: frozenset                   # the non-terminal states this stage owns


# The frozen menu of stage KINDS. entry_state / exit_from / exit_map / owns are
# grounded in types.TASK_TRANSITIONS AND the current reconcile.py behaviour, so
# the declarative record matches what the engine actually plans. A genuinely new
# kind is a code change here carrying the full validate_pipeline obligation.
STAGE_REGISTRY: dict[str, Stage] = {
    "carve": Stage(
        name="carve", role=Role.CARVER,
        entry_state=TaskState.READY_TO_CARVE, exit_from=TaskState.READY_TO_CARVE,
        exit_map=(("done", TaskState.CARVED),
                  ("needs_decision", TaskState.NEEDS_DECISION),
                  ("rescope_superseded", TaskState.SUPERSEDED)),
        owns=frozenset({TaskState.READY_TO_CARVE})),
    "implement": Stage(
        name="implement", role=Role.IMPLEMENTER,
        entry_state=TaskState.QUEUED, exit_from=TaskState.ACTIVE,
        exit_map=(("done", TaskState.AWAITING_REVIEW),
                  ("incomplete", TaskState.QUEUED),
                  ("dead_end", TaskState.BLOCKED)),
        owns=frozenset({TaskState.QUEUED, TaskState.ACTIVE})),
    "frontier_review": Stage(
        name="frontier_review", role=Role.FRONTIER_REVIEW,
        entry_state=TaskState.AWAITING_REVIEW, exit_from=TaskState.AWAITING_REVIEW,
        exit_map=(("approved", TaskState.MERGE_READY),
                  ("rejected", TaskState.REVIEW_REJECTED)),
        owns=frozenset({TaskState.AWAITING_REVIEW})),
    "triage": Stage(
        name="triage", role=None,
        entry_state=TaskState.REVIEW_REJECTED, exit_from=TaskState.REVIEW_REJECTED,
        # B2: matches the current reject-loop (item 10) -- attempts-remaining ->
        # QUEUED, exhausted -> READY_TO_CARVE. B4 makes the exhausted target
        # pipeline-aware (NEEDS_DECISION when no carve stage) + adds LLM tiers.
        exit_map=(("fixable", TaskState.QUEUED),
                  ("exhausted", TaskState.READY_TO_CARVE)),
        owns=frozenset({TaskState.REVIEW_REJECTED})),
    "auto_merge": Stage(
        name="auto_merge", role=None,
        entry_state=TaskState.MERGE_READY, exit_from=TaskState.MERGE_READY,
        exit_map=(("merged", TaskState.MERGED),),
        owns=frozenset({TaskState.MERGE_READY})),
    "post_merge_gate": Stage(
        name="post_merge_gate", role=None,
        entry_state=TaskState.MERGED, exit_from=TaskState.VALIDATING,
        exit_map=(("pass", TaskState.COMPLETED),
                  ("fail", TaskState.BLOCKED)),
        owns=frozenset({TaskState.MERGED, TaskState.VALIDATING})),
}

# The default pipeline == the current hardcoded behaviour (the parity baseline).
# A project with no `pipeline` key gets exactly this, so existing projects are
# byte-identical after B2.
DEFAULT_PIPELINE: tuple = (
    "carve", "implement", "frontier_review", "triage", "auto_merge", "post_merge_gate",
)

# Ergonomic presets (docs/spec-flow-stages.md). B2 ships `full` (== default) and
# `lean` (drops post_merge_gate -> VALIDATING advances straight to COMPLETED).
# `gated` (drops carve) and self_review-in-every-preset land in B4/B5, which own
# reject-routing and the SELF_REVIEWING state respectively -- until then, a
# carve-less pipeline that still routes rejects to READY_TO_CARVE is (correctly)
# rejected by validate_pipeline's dead-end check.
PRESETS: dict[str, tuple] = {
    "full": DEFAULT_PIPELINE,
    "lean": ("carve", "implement", "frontier_review", "triage", "auto_merge"),
}


def compose(spec: object) -> list[str]:
    """Resolve a `pipeline` config value to an ordered list of stage names.

    `spec` is None (-> DEFAULT_PIPELINE), a preset name (str), or an explicit
    list of stage names. Does NOT validate closure -- call validate_pipeline.
    """
    if spec is None:
        return list(DEFAULT_PIPELINE)
    if isinstance(spec, str):
        if spec not in PRESETS:
            raise ValueError(
                f"unknown pipeline preset {spec!r}; known presets: {sorted(PRESETS)}")
        return list(PRESETS[spec])
    if isinstance(spec, (list, tuple)):
        return [str(n) for n in spec]
    raise ValueError(f"pipeline must be a preset name or a list of stage names, got {type(spec).__name__}")


def validate_pipeline(names: list[str]) -> None:
    """Raise ValueError unless the composed pipeline closes against the frozen
    graph. Four checks (docs/spec-flow-stages.md, load-time validation):

      1. every stage name is a known kind, and no state is owned by two stages;
      2. every exit_map target is a real TASK_TRANSITIONS edge from exit_from;
      3. every non-terminal exit target is HANDLED -- owned by a present stage,
         the entry of a present stage, a lifecycle state, or the post-merge
         region -- so no stage routes a task into a dead-end;
      4. the pipeline can reach a terminal state.
    """
    if not names:
        raise ValueError("pipeline is empty")
    unknown = [n for n in names if n not in STAGE_REGISTRY]
    if unknown:
        raise ValueError(
            f"unknown stage kind(s): {unknown}; menu: {sorted(STAGE_REGISTRY)}")
    stages = [STAGE_REGISTRY[n] for n in names]

    # 1: single ownership
    owner: dict[TaskState, str] = {}
    for st in stages:
        for s in st.owns:
            if s in owner:
                raise ValueError(
                    f"state {s.value} is owned by both {owner[s]} and {st.name}")
            owner[s] = st.name

    # 2: edge legality against the frozen graph. TASK_TRANSITIONS already lists
    # every legal target from a state, terminal targets included, so membership
    # is the whole check.
    for st in stages:
        legal = TASK_TRANSITIONS[st.exit_from]
        for label, to in st.exit_map:
            if to not in legal:
                raise ValueError(
                    f"stage {st.name}: exit {label!r} -> {to.value} is not a legal "
                    f"transition from {st.exit_from.value} (TASK_TRANSITIONS)")

    # 3: no dead-end routing
    owned = set(owner)
    entries = {st.entry_state for st in stages}
    handled = owned | entries | set(LIFECYCLE_STATES) | set(_POST_MERGE_STATES)
    for st in stages:
        for label, to in st.exit_map:
            if to in TERMINAL_TASK_STATES:
                continue
            if to not in handled:
                raise ValueError(
                    f"stage {st.name}: exit {label!r} -> {to.value} lands in a state "
                    f"no stage in this pipeline owns or handles (dead-end); add a "
                    f"stage that owns {to.value} or remove the routing")

    # 4: terminal reachable (a terminal exit, or auto_merge -> MERGED -> COMPLETED
    # via the gate/auto-advance mechanism)
    reaches_terminal = any(
        to in TERMINAL_TASK_STATES for st in stages for _label, to in st.exit_map
    ) or any(st.name == "auto_merge" for st in stages)
    if not reaches_terminal:
        raise ValueError("pipeline has no path to a terminal state")
