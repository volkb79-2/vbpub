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
    DEFAULT_PIPELINE, PRESETS, STAGE_REGISTRY, Stage, compose,
    effective_concurrency, validate_pipeline, validate_stage_overrides,
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


def test_compose_default_and_preset_and_list():
    assert compose(None) == list(DEFAULT_PIPELINE)
    assert compose("full") == list(PRESETS["full"])
    assert compose(["implement", "frontier_review", "auto_merge"]) == [
        "implement", "frontier_review", "auto_merge"]


def test_compose_rejects_unknown_preset():
    with pytest.raises(ValueError, match="unknown pipeline preset"):
        compose("turbo")


# --- validator rejects each distinct non-closure ---------------------------

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
    """triage routes an exhausted reject to READY_TO_CARVE; a pipeline with no
    carve stage to own that state is a dead-end and must be rejected (check 3).
    This is exactly why dropping `carve` is a B4 concern (B4 makes the exhausted
    target pipeline-aware)."""
    with pytest.raises(ValueError, match="dead-end"):
        validate_pipeline(["implement", "frontier_review", "triage", "auto_merge"])


def test_dead_end_check_passes_once_carve_is_added():
    """Control for the test above: add the owning stage and the same pipeline
    closes -- proving the rejection was about the missing owner, not noise."""
    validate_pipeline(
        ["carve", "implement", "frontier_review", "triage", "auto_merge"])


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
