"""Tests for the stages-as-data layer (D-060; PACKAGE B2/P70).

The validator is the P43 closure invariant promoted from declaration to
composition. These tests assert it ACCEPTS every shipped composition and
REJECTS each distinct way a pipeline can fail to close against the frozen graph
-- each rejection test is written so that neutering the corresponding check
makes it pass (a real discriminator, not a hollow assert).
"""

from __future__ import annotations

import pytest

from nyxloom import stages
from nyxloom.stages import (
    DEFAULT_PIPELINE, KNOWN_CONTEXT_FLAGS, PRESETS, STAGE_REGISTRY, Stage, compose,
    effective_concurrency, stage_context, validate_pipeline, validate_stage_overrides,
)
from nyxloom.types import TaskState, TASK_TRANSITIONS, TERMINAL_TASK_STATES


# --- registry is internally consistent -------------------------------------

def test_default_pipeline_validates():
    """The default pipeline == current behaviour must close (parity baseline)."""
    validate_pipeline(list(DEFAULT_PIPELINE))


def test_every_preset_validates():
    """Every shipped preset closes against the frozen graph (the composition
    port of test_invariants' closure check)."""
    for name, pipeline in PRESETS.items():
        validate_pipeline(list(pipeline)), name


def test_registry_exit_edges_are_all_legal():
    """Every stage's declared exit edges are real TASK_TRANSITIONS edges --
    the declarative record cannot silently drift from the frozen graph."""
    for st in STAGE_REGISTRY.values():
        legal = TASK_TRANSITIONS[st.exit_from]
        for label, to in st.exit_map:
            assert to in legal, f"{st.name}:{label} -> {to} absent from graph"


def test_carve_stage_declares_rescope_superseded_edge():
    """B7 2026-07-20 (P75): the carve stage declares the `rescope_superseded`
    exit READY_TO_CARVE -> SUPERSEDED, which daemon._execute_carve_dispatch makes
    real (the RESCOPED atomic supersede of a re-scoped origin task). Pin it here so
    a future edit that drops the edge fails loudly rather than silently unwiring
    the declared model from the runtime behaviour. SUPERSEDED is terminal, so the
    dead-end scan skips it; this asserts the edge exists AND is graph-legal."""
    carve = STAGE_REGISTRY["carve"]
    labels = {label: to for label, to in carve.exit_map}
    assert labels["rescope_superseded"] is TaskState.SUPERSEDED
    assert carve.exit_from is TaskState.READY_TO_CARVE
    assert TaskState.SUPERSEDED in TASK_TRANSITIONS[TaskState.READY_TO_CARVE]


# --- B6/P74 packet-assembly context policy --------------------------------

def test_context_flags_declared_are_known_and_frontier_reviewer_reuses():
    """B6 2026-07-20 (P74): the packet-assembly context policy is stages-as-data.
    Pin the two declarations B6 relies on AND the registry-consistency invariant
    (no stage may declare a flag outside the frozen KNOWN_CONTEXT_FLAGS menu -- a
    typo like "session_reuse" would silently disable reviewer cache reuse, so it
    must fail loudly here). The `implement` stage carries NO context flag -- the
    discriminating negative proving `context` is a real per-kind property, not a
    blanket default; if every stage got the flags, reviewer-only reuse would be
    indistinguishable from a global."""
    assert KNOWN_CONTEXT_FLAGS == frozenset({"session-reuse", "spine-digest"})
    # every declared flag is in the frozen menu (registry cannot drift)
    for name, st in STAGE_REGISTRY.items():
        assert st.context <= KNOWN_CONTEXT_FLAGS, f"{name} declares unknown flag(s): {st.context}"
    # the two load-bearing declarations
    assert stage_context("frontier_review") == frozenset({"session-reuse", "spine-digest"})
    assert stage_context("carve") == frozenset({"spine-digest"})
    # the discriminating negatives: reuse is reviewer-specific, not global
    assert "session-reuse" not in stage_context("implement")
    assert stage_context("implement") == frozenset()
    assert "session-reuse" not in stage_context("auto_merge")


def test_context_does_not_break_pipeline_closure():
    """Adding the `context` field must not disturb the closure invariant -- every
    shipped composition still validates (context is packet policy, orthogonal to
    the frozen-graph closure the validator checks)."""
    validate_pipeline(list(DEFAULT_PIPELINE))
    for pipeline in PRESETS.values():
        validate_pipeline(list(pipeline))


def test_compose_default_and_preset_and_list():
    assert compose(None) == list(DEFAULT_PIPELINE)
    assert compose("full") == list(PRESETS["full"])
    assert compose(["implement", "frontier_review", "auto_merge"]) == [
        "implement", "frontier_review", "auto_merge"]


def test_compose_rejects_unknown_preset():
    with pytest.raises(ValueError, match="unknown pipeline preset"):
        compose("turbo")


def test_compose_rejects_invalid_spec_type():
    """logging-P05b: closes the one previously-uncovered compose() branch --
    a spec that is neither None, a preset name, nor a list/tuple (e.g. an
    int) is rejected with a precise message, not a silent misbehavior."""
    with pytest.raises(ValueError, match="must be a preset name or a list"):
        compose(42)


# --- validator rejects each distinct non-closure ---------------------------

def test_rejects_empty_pipeline():
    """logging-P05b: closes the one previously-uncovered validate_pipeline
    branch -- an empty pipeline list is rejected outright."""
    with pytest.raises(ValueError, match="pipeline is empty"):
        validate_pipeline([])


def test_rejects_unknown_stage_kind():
    with pytest.raises(ValueError, match="unknown stage kind"):
        validate_pipeline(["implement", "frobnicate", "auto_merge"])


def test_rejects_duplicate_ownership():
    """Two stages owning the same state is a composition error (check 1)."""
    with pytest.raises(ValueError, match="owned by both"):
        validate_pipeline(["implement", "implement", "frontier_review", "auto_merge"])


def test_rejects_illegal_exit_edge(monkeypatch):
    """A stage whose exit_map names a transition absent from the frozen graph is
    rejected (check 2). QUEUED -> COMPLETED is not a legal edge."""
    bad = Stage(
        name="bad", role=None,
        entry_state=TaskState.QUEUED, exit_from=TaskState.QUEUED,
        exit_map=(("teleport", TaskState.COMPLETED),),
        owns=frozenset({TaskState.QUEUED}),
    )
    assert TaskState.COMPLETED not in TASK_TRANSITIONS[TaskState.QUEUED]
    monkeypatch.setitem(STAGE_REGISTRY, "bad", bad)
    with pytest.raises(ValueError, match="not a legal transition"):
        validate_pipeline(["bad"])


def test_rejects_dead_end_routing():
    """frontier_review routes a rejection to REVIEW_REJECTED; a pipeline with no
    triage stage to own that state is a dead-end and must be rejected (check 3).
    (B4a made the carve-less-but-triaged case VALID -- triage now escalates an
    exhausted reject to NEEDS_DECISION -- so the genuine remaining dead-end is a
    rejection with nothing owning REVIEW_REJECTED.)"""
    with pytest.raises(ValueError, match="dead-end"):
        validate_pipeline(["implement", "frontier_review", "auto_merge"])


def test_dead_end_check_passes_once_triage_is_added():
    """Control for the test above: add the owning stage (triage) and the same
    pipeline closes -- proving the rejection was about the missing owner of
    REVIEW_REJECTED, not noise. This is the carve-less `lean` shape."""
    validate_pipeline(["implement", "frontier_review", "triage", "auto_merge"])


def test_rejects_no_terminal_path():
    """A pipeline that can never reach a terminal state is rejected (check 4).
    Build a self-contained closed loop with no terminal exit and no auto_merge."""
    loop = Stage(
        name="loop", role=None,
        entry_state=TaskState.QUEUED, exit_from=TaskState.ACTIVE,
        exit_map=(("again", TaskState.QUEUED),),
        owns=frozenset({TaskState.QUEUED, TaskState.ACTIVE}),
    )
    # ACTIVE -> QUEUED is a legal edge, QUEUED is owned -> passes checks 1-3;
    # no terminal exit and no auto_merge -> check 4 fires.
    import nyxloom.stages as _s
    _s.STAGE_REGISTRY["loop"] = loop
    try:
        with pytest.raises(ValueError, match="no path to a terminal"):
            validate_pipeline(["loop"])
    finally:
        del _s.STAGE_REGISTRY["loop"]


def test_lean_preset_drops_the_gate_but_still_closes():
    """The `lean` preset omits post_merge_gate; MERGED/VALIDATING are then
    handled by the mechanism (auto-advance), so the pipeline still closes and
    reaches a terminal via auto_merge."""
    assert "post_merge_gate" not in PRESETS["lean"]
    validate_pipeline(list(PRESETS["lean"]))


def test_gated_and_lean_presets_are_carveless_and_close():
    """B4a: `gated` and `lean` drop the carve stage entirely and still close --
    triage escalates an exhausted reject to NEEDS_DECISION (a lifecycle state)
    rather than to READY_TO_CARVE, so there is no unowned dead-end. This is the
    operator's per-project divergence made real."""
    for name in ("gated", "lean"):
        assert "carve" not in PRESETS[name], name
        assert "triage" in PRESETS[name], name
        validate_pipeline(list(PRESETS[name]))
    assert "post_merge_gate" in PRESETS["gated"]
    assert "post_merge_gate" not in PRESETS["lean"]


# --- B3/P71 per-stage concurrency ------------------------------------------

def test_implement_concurrency_defaults_to_max_active_tasks():
    """Parity: implement's concurrency is None in the registry, so with no
    override it inherits policy.max_active_tasks -- the old single global knob."""
    assert STAGE_REGISTRY["implement"].concurrency is None
    assert effective_concurrency("implement", {}, 4) == 4
    assert effective_concurrency("implement", {}, 1) == 1


def test_stage_override_wins_over_policy():
    """A [stage.implement] concurrency override takes precedence over
    max_active_tasks -- the knob that makes implement parallelism per-stage."""
    assert effective_concurrency("implement", {"implement": {"concurrency": 2}}, 9) == 2


def test_serial_resolves_to_one():
    """"serial" (the default for review/carve/etc.) resolves to 1."""
    assert STAGE_REGISTRY["frontier_review"].concurrency == "serial"
    assert effective_concurrency("frontier_review", {}, 9) == 1
    assert effective_concurrency("implement", {"implement": {"concurrency": "serial"}}, 9) == 1


def test_validate_stage_overrides_accepts_legal():
    validate_stage_overrides({})
    validate_stage_overrides({"implement": {"concurrency": 4}})
    validate_stage_overrides({"frontier_review": {"concurrency": "serial"}})


def test_validate_stage_overrides_rejects_unknown_stage():
    with pytest.raises(ValueError, match="unknown stage kind"):
        validate_stage_overrides({"frobnicate": {"concurrency": 2}})


@pytest.mark.parametrize("bad", [0, -1, True, 1.5, "parallel"])
def test_validate_stage_overrides_rejects_bad_concurrency(bad):
    with pytest.raises(ValueError, match="positive int"):
        validate_stage_overrides({"implement": {"concurrency": bad}})


# --- B5/P73 self_review stage ----------------------------------------------

def test_self_review_stage_owns_self_reviewing_and_routes_approved_rejected():
    """The registered stage kind: owns SELF_REVIEWING, approved->AWAITING_REVIEW
    (hand to the frontier reviewer), rejected->QUEUED (a fresh, budget-bounded
    fix attempt; NOT ACTIVE -- see D-063)."""
    st = STAGE_REGISTRY["self_review"]
    assert st.entry_state == TaskState.SELF_REVIEWING
    assert st.owns == frozenset({TaskState.SELF_REVIEWING})
    assert dict(st.exit_map) == {
        "approved": TaskState.AWAITING_REVIEW, "rejected": TaskState.QUEUED}


def test_self_review_is_the_default_and_in_every_preset_after_implement():
    """B5: self_review is the proven-standard default -- present in
    DEFAULT_PIPELINE and every preset, always in the slot IMMEDIATELY after
    implement (it resumes that stage's warm session). full == the compiled
    default (greenfield: the default IS the recommended flow, not a subset)."""
    for pl in (DEFAULT_PIPELINE, *PRESETS.values()):
        assert "self_review" in pl
        assert pl[pl.index("implement") + 1] == "self_review"
    assert PRESETS["full"] == DEFAULT_PIPELINE


def test_rejects_self_review_not_immediately_after_implement():
    """Rule 5: self_review anywhere but the slot right after implement is
    rejected. Uses a triage-bearing pipeline so it is otherwise closed, so ONLY
    rule 5 can be the cause (the paired control below confirms it)."""
    with pytest.raises(ValueError, match="immediately follow"):
        validate_pipeline(["implement", "frontier_review", "triage", "self_review", "auto_merge"])


def test_rejects_self_review_before_implement():
    with pytest.raises(ValueError, match="immediately follow"):
        validate_pipeline(["self_review", "implement", "frontier_review", "triage", "auto_merge"])


def test_rejects_self_review_without_implement():
    """Rule 5 (checked EARLY, before the generic dead-end scan): self_review has
    no session to borrow without an implement stage -- a precise message, not a
    downstream QUEUED-dead-end complaint."""
    with pytest.raises(ValueError, match="requires the implement"):
        validate_pipeline(["self_review", "frontier_review", "triage", "auto_merge"])


def test_adjacency_rejection_is_placement_specific_not_stage_set():
    """Non-hollow control for the rejections above: the SAME stage set, only
    reordered so self_review sits right after implement, validates cleanly. So
    the rejection is caused by rule 5 (placement), not by the stages present --
    neutering rule 5 would let the misplaced form pass."""
    misplaced = ["implement", "frontier_review", "triage", "self_review", "auto_merge"]
    with pytest.raises(ValueError, match="immediately follow"):
        validate_pipeline(misplaced)
    adjacent = ["implement", "self_review", "frontier_review", "triage", "auto_merge"]
    validate_pipeline(adjacent)  # closes cleanly -- only placement differed


def test_legacy_pipeline_without_self_review_still_validates():
    """The opt-out path: a project can compose the pre-B5 flow (no self_review)
    and it still closes -- proving self_review is a composable stage, not a
    hardcoded requirement. The composition port of B5's parity claim (a
    no-self_review pipeline plans implement-done exactly as pre-B5)."""
    validate_pipeline(["carve", "implement", "frontier_review", "triage",
                       "auto_merge", "post_merge_gate"])


def test_compose_full_preset_includes_self_review():
    assert "self_review" in compose("full")
    assert "self_review" in compose(None)  # None -> DEFAULT_PIPELINE, which now has it
