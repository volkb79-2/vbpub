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

from .log import get_logger
from .types import TaskState, Role, TASK_TRANSITIONS, TERMINAL_TASK_STATES

log = get_logger("stages")

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
    # B3/P71 per-stage scheduling: an int N, "serial" (== 1), or None on the
    # `implement` stage to INHERIT policy.max_active_tasks (parity -- the old
    # single global knob). Resolved to an int by effective_concurrency(); a
    # per-project [stage.<name>] concurrency override wins over this default.
    concurrency: int | str | None = "serial"
    # B6/P74 packet-assembly policy (docs/spec-flow-stages.md §"Stage schema").
    # A frozenset of CONTEXT FLAGS naming how this stage's dispatch packet is
    # built beyond the base diff-only form. Only two are wired today (the frozen
    # menu is KNOWN_CONTEXT_FLAGS): "session-reuse" (the reviewer resumes its warm
    # session across a wave/cycle for prompt-cache hits, via adapters.build_resume
    # -- reconcile plans it, the daemon executes it, and the A7 verdict-attempt
    # binding is preserved by stamping the NEW attempt id even on a resumed
    # session) and "spine-digest" (the packet references the carver-maintained
    # SPINE-DIGEST.md by POINTER, never slurping its body). Default empty == the
    # pre-B6 cold, diff-only packet. Declared per stage KIND here; a per-project
    # override surface can be added later exactly like `concurrency`.
    context: frozenset = frozenset()


# The frozen menu of stage KINDS. entry_state / exit_from / exit_map / owns are
# grounded in types.TASK_TRANSITIONS AND the current reconcile.py behaviour, so
# the declarative record matches what the engine actually plans. A genuinely new
# kind is a code change here carrying the full validate_pipeline obligation.
STAGE_REGISTRY: dict[str, Stage] = {
    # carve exits: `done` (new packages carved -> CARVED), `needs_decision` (the
    # carver escalates a product question -> NEEDS_DECISION), and
    # `rescope_superseded` (B7/P75): when a READY_TO_CARVE entry is a RE-SCOPE of a
    # rejected task (triage routed it here as architectural/stale/exhausted), the
    # daemon supersedes that ORIGINAL task once the re-scope carve launches
    # (daemon._execute_carve_dispatch, RESCOPED outcome). B7 makes this declared
    # edge real; READY_TO_CARVE -> SUPERSEDED is already a legal frozen-graph edge.
    "carve": Stage(
        name="carve", role=Role.CARVER,
        entry_state=TaskState.READY_TO_CARVE, exit_from=TaskState.READY_TO_CARVE,
        exit_map=(("done", TaskState.CARVED),
                  ("needs_decision", TaskState.NEEDS_DECISION),
                  ("rescope_superseded", TaskState.SUPERSEDED)),
        owns=frozenset({TaskState.READY_TO_CARVE}),
        # B6/P74: the carve packet references SPINE-DIGEST.md by pointer AND the
        # carver is the component that MAINTAINS it (carve-6-style reflections
        # become standing instructions there rather than one-off prose).
        context=frozenset({"spine-digest"})),
    "implement": Stage(
        name="implement", role=Role.IMPLEMENTER,
        entry_state=TaskState.QUEUED, exit_from=TaskState.ACTIVE,
        exit_map=(("done", TaskState.AWAITING_REVIEW),
                  ("incomplete", TaskState.QUEUED),
                  ("dead_end", TaskState.BLOCKED)),
        owns=frozenset({TaskState.QUEUED, TaskState.ACTIVE}),
        concurrency=None),   # inherit policy.max_active_tasks unless overridden
    "self_review": Stage(
        name="self_review", role=Role.SELF_REVIEW,
        entry_state=TaskState.SELF_REVIEWING, exit_from=TaskState.SELF_REVIEWING,
        # B5 (2026-07-20): the implementer's WARM session (context=session-reuse)
        # reviews its own diff before the expensive frontier reviewer sees it.
        # approved -> AWAITING_REVIEW (hand to the frontier reviewer); rejected ->
        # QUEUED (a fresh, budget-bounded fix attempt -- deliberately NOT ACTIVE,
        # which would re-expose the ACTIVE-scoped stale-receipt re-consumption the
        # frontier reject loop avoids; the warm in-session fix loop is deferred,
        # see D-063). Ships in every preset but NOT in DEFAULT_PIPELINE (parity:
        # a no-pipeline project keeps today's exact behaviour). Must sit
        # immediately after `implement` (validate_pipeline rule 5).
        exit_map=(("approved", TaskState.AWAITING_REVIEW),
                  ("rejected", TaskState.QUEUED)),
        owns=frozenset({TaskState.SELF_REVIEWING})),
    "frontier_review": Stage(
        name="frontier_review", role=Role.FRONTIER_REVIEW,
        entry_state=TaskState.AWAITING_REVIEW, exit_from=TaskState.AWAITING_REVIEW,
        exit_map=(("approved", TaskState.MERGE_READY),
                  ("rejected", TaskState.REVIEW_REJECTED)),
        owns=frozenset({TaskState.AWAITING_REVIEW}),
        # B6/P74: the reviewer resumes its warm session across a wave/cycle for
        # prompt-cache hits (D-R10, safe now that A7 verdict-attempt binding is
        # enforced on resumed sessions too), and its packet references the spine
        # digest by pointer.
        context=frozenset({"session-reuse", "spine-digest"})),
    "triage": Stage(
        name="triage", role=None,
        entry_state=TaskState.REVIEW_REJECTED, exit_from=TaskState.REVIEW_REJECTED,
        # The DECLARED floor (pipeline-independent, always safe): attempts
        # remaining -> QUEUED; exhausted -> NEEDS_DECISION (a human decides). This
        # is what lets a carve-less pipeline (`gated`/`lean`) validate. B4a: when
        # the pipeline DOES include a carve stage, reconcile UPGRADES the
        # exhausted case to READY_TO_CARVE (carve owns that state, so it is never
        # a dead-end and needs no separate declaration here). B4b (DONE 2026-07-20)
        # adds the rest of the {infra, stale-premise, fixable, architectural,
        # product} matrix as further CONTEXT-SENSITIVE upgrades in reconcile, not
        # new declared edges: stale-premise (input_revision drift, critique I4) and
        # architectural -> READY_TO_CARVE when a carve stage is present (else the
        # NEEDS_DECISION floor); product -> NEEDS_DECISION (already the declared
        # floor's target). Keeping the tuple as the minimal always-safe floor is
        # deliberate -- declaring READY_TO_CARVE here would force `carve` into
        # every pipeline and break the carve-less presets' closure check.
        exit_map=(("fixable", TaskState.QUEUED),
                  ("exhausted", TaskState.NEEDS_DECISION)),
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

# The default pipeline == the PROVEN STANDARD flow, self_review included. This is
# a greenfield engine with no external byte-compat contract to preserve, so the
# compiled default is the best recommended pipeline -- NOT a legacy subset. A
# project with no `pipeline` key gets self-review (warm, near-free, run before the
# expensive frontier reviewer) by default; `full` is an alias for it. A project
# that deliberately wants the pre-B5 flow composes an explicit legacy list
# WITHOUT self_review (proven byte-identical by test_*_legacy_pipeline_*). Opting
# OUT is the documented exception; the good default is the rule.
DEFAULT_PIPELINE: tuple = (
    "carve", "implement", "self_review", "frontier_review", "triage", "auto_merge", "post_merge_gate",
)

# Ergonomic presets (docs/spec-flow-stages.md). B4a makes the carve-less presets
# real (triage escalates exhausted rejects to NEEDS_DECISION when no carve stage
# is present, so they close). B5: self_review sits IMMEDIATELY after implement in
# EVERY preset (the warm, near-free self-check before the expensive frontier
# reviewer). `full` == DEFAULT_PIPELINE (the proven standard); `gated` drops carve
# (externally-fed handoffs + a real gate, e.g. dstdns); `lean` also drops the gate
# (low-ceremony projects).
PRESETS: dict[str, tuple] = {
    "full": DEFAULT_PIPELINE,
    "gated": ("implement", "self_review", "frontier_review", "triage", "auto_merge", "post_merge_gate"),
    "lean": ("implement", "self_review", "frontier_review", "triage", "auto_merge"),
}


def compose(spec: object) -> list[str]:
    """Resolve a `pipeline` config value to an ordered list of stage names.

    `spec` is None (-> DEFAULT_PIPELINE), a preset name (str), or an explicit
    list of stage names. Does NOT validate closure -- call validate_pipeline.
    """
    if spec is None:
        result = list(DEFAULT_PIPELINE)
        log.debug("pipeline composed", source="default", count=len(result))
        return result
    if isinstance(spec, str):
        if spec not in PRESETS:
            log.warning("pipeline compose rejected", reason="unknown-preset", preset=spec)
            raise ValueError(
                f"unknown pipeline preset {spec!r}; known presets: {sorted(PRESETS)}")
        result = list(PRESETS[spec])
        log.debug("pipeline composed", source="preset", preset=spec, count=len(result))
        return result
    if isinstance(spec, (list, tuple)):
        result = [str(n) for n in spec]
        log.debug("pipeline composed", source="explicit", count=len(result))
        return result
    log.warning("pipeline compose rejected", reason="invalid-spec-type", spec_type=type(spec).__name__)
    raise ValueError(f"pipeline must be a preset name or a list of stage names, got {type(spec).__name__}")


def validate_pipeline(names: list[str]) -> None:
    """Raise ValueError unless the composed pipeline closes against the frozen
    graph. Five checks (docs/spec-flow-stages.md, load-time validation):

      1. every stage name is a known kind, and no state is owned by two stages;
      2. every exit_map target is a real TASK_TRANSITIONS edge from exit_from;
      3. every non-terminal exit target is HANDLED -- owned by a present stage,
         the entry of a present stage, a lifecycle state, or the post-merge
         region -- so no stage routes a task into a dead-end;
      4. the pipeline can reach a terminal state;
      5. self_review, if present, sits immediately after implement (it resumes
         that stage's warm session, so it is meaningless anywhere else).
    """
    if not names:
        log.warning("pipeline validation failed", reason="empty")
        raise ValueError("pipeline is empty")
    unknown = [n for n in names if n not in STAGE_REGISTRY]
    if unknown:
        log.warning("pipeline validation failed", reason="unknown-stage-kind", unknown=unknown)
        raise ValueError(
            f"unknown stage kind(s): {unknown}; menu: {sorted(STAGE_REGISTRY)}")
    stages = [STAGE_REGISTRY[n] for n in names]

    # 5 (B5): self_review adjacency -- checked EARLY (before the generic
    # ownership/dead-end scans) so a misplaced or implement-less self_review
    # gets a precise message instead of a downstream QUEUED/AWAITING_REVIEW
    # dead-end complaint. self_review resumes the implementer's warm session
    # (context=session-reuse) to review the diff that session just produced, so
    # it is meaningless -- and has no session to borrow -- anywhere but the slot
    # immediately after implement.
    if "self_review" in names:
        if "implement" not in names:
            log.warning("pipeline validation failed", reason="self-review-requires-implement")
            raise ValueError(
                "self_review requires the implement stage -- it resumes the "
                "implementer's warm session")
        if names.index("self_review") != names.index("implement") + 1:
            log.warning("pipeline validation failed", reason="self-review-not-adjacent")
            raise ValueError(
                "self_review must immediately follow implement (it resumes that "
                "stage's warm session); found it at a non-adjacent position")

    # 1: single ownership
    owner: dict[TaskState, str] = {}
    for st in stages:
        for s in st.owns:
            if s in owner:
                log.warning("pipeline validation failed", reason="duplicate-ownership",
                            state=s.value, first=owner[s], second=st.name)
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
                log.warning("pipeline validation failed", reason="illegal-exit-edge",
                            stage=st.name, label=label, target=to.value)
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
                log.warning("pipeline validation failed", reason="dead-end",
                            stage=st.name, label=label, target=to.value)
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
        log.warning("pipeline validation failed", reason="no-terminal-path")
        raise ValueError("pipeline has no path to a terminal state")

    log.debug("pipeline validated", count=len(names))


# B6/P74: the frozen menu of packet-assembly context flags (see Stage.context).
# A stage kind may only declare flags from this set; the registry-consistency
# test (test_stages.py) pins it so a typo like "session_reuse" fails loudly
# rather than silently disabling the reviewer's cache reuse.
KNOWN_CONTEXT_FLAGS: frozenset = frozenset({"session-reuse", "spine-digest"})


def stage_context(stage_name: str) -> frozenset:
    """The declared packet-assembly context flags for a stage KIND (B6/P74).

    Pure registry read -- the flags are a property of the stage kind (like
    exit_map), not per-project config yet, so callers (reconcile's reviewer
    session-reuse gate, the daemon's packet builders) consult this single source
    rather than hardcoding a stage-name check. Unknown stage names raise KeyError
    (a programming error), matching STAGE_REGISTRY's other accessors."""
    return STAGE_REGISTRY[stage_name].context


def effective_concurrency(stage_name: str, overrides: dict, max_active_tasks: int) -> int:
    """Resolve a stage's effective integer concurrency (B3/P71).

    Precedence: a per-project `[stage.<name>] concurrency` override, else the
    Stage default. `None` (the `implement` default) inherits max_active_tasks --
    exact parity with the old single global knob. `"serial"` resolves to 1.
    Values are validated at config load (validate_stage_overrides), so this is a
    pure resolver.
    """
    raw = overrides.get(stage_name, {}).get("concurrency")
    if raw is None:
        raw = STAGE_REGISTRY[stage_name].concurrency
    if raw is None:                      # implement's inherit-the-policy default
        result = max_active_tasks
    elif raw == "serial":
        result = 1
    else:
        result = int(raw)
    log.debug("stage concurrency resolved", stage=stage_name, concurrency=result)
    return result


def validate_stage_overrides(overrides: dict) -> None:
    """Raise ValueError unless every `[stage.<name>]` override names a known
    stage kind and carries a legal `concurrency` (a positive int or "serial").
    Called at config load so a bad knob fails loudly, never at plan time."""
    for name, tbl in overrides.items():
        if name not in STAGE_REGISTRY:
            log.warning("stage override rejected", reason="unknown-stage", stage=name)
            raise ValueError(
                f"[stage.{name}] overrides an unknown stage kind; "
                f"menu: {sorted(STAGE_REGISTRY)}")
        if "concurrency" in tbl:
            c = tbl["concurrency"]
            if c == "serial":
                continue
            if isinstance(c, bool) or not isinstance(c, int) or c < 1:
                log.warning("stage override rejected", reason="invalid-concurrency",
                            stage=name, value=repr(c))
                raise ValueError(
                    f'[stage.{name}] concurrency must be a positive int or '
                    f'"serial", got {c!r}')
    log.debug("stage overrides validated", count=len(overrides))
