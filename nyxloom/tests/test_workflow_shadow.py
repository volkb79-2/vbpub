"""Shadow compilation: the shipped presets, and the §5.4 stop-loss measurement.

This is the package's differential, and it is the honest one -- the population
is what the product ships (`stages.PRESETS`), not a corpus written to be
compiled. Equivalence is PROVEN rather than asserted: the compiled IR's
``(from_state, label, to_state)`` triples are compared for exact set equality
against the stage registry's ``exit_map`` triples, plus per-node equality of
entry/exit/ownership/role/concurrency/packet-context. Anything a stage record
says that the compiled node does not, or the reverse, fails.

There is exactly ONE declared divergence, it is named in
``workflow_shadow.DECLARED_DIVERGENCES``, and the legacy pipeline -- the same
flow without ``self_review`` -- is equality with NOTHING subtracted, which is
what proves the divergence is the self-review adjacency and nothing else.
"""

from __future__ import annotations

import pytest

from nyxloom import workflow_shadow as ws
from nyxloom.stages import PRESETS, STAGE_REGISTRY, validate_pipeline
from nyxloom.types import TaskState
from nyxloom.workflow_compile import CompileError, DiagnosticCode, compile_workflow
from nyxloom.workflow_ir import (
    COMPILER_EDGES,
    EdgeOrigin,
    KernelEdgeClass,
    NodeKind,
    classify_edge,
    digest,
)
from workflow_corpus import valid_sources

VALID = valid_sources()
PRESET_CASES = sorted(PRESETS)


def _stage_triples(pipeline: tuple) -> set:
    """What `stages.py` DECLARES, as (from_state, label, to_state) triples,
    plus the implicit entry edge of every stage whose entry and exit differ.

    A stage whose entry edge is a KERNEL edge contributes no labelled triple:
    ``post_merge_gate`` is entered at ``MERGED`` and exits from ``VALIDATING``,
    and ``MERGED -> VALIDATING`` is K-merge-spine, so the compiled node carries
    it as an unlabelled kernel edge rather than as a declared one. That is the
    inventory's second structural fact -- the boundary runs THROUGH the state
    -- and :func:`test_the_kernel_entry_edge_is_carried_not_dropped` proves
    the edge is present rather than lost by this exclusion.
    """
    triples = set()
    for name in pipeline:
        stage = STAGE_REGISTRY[name]
        for label, target in stage.exit_map:
            triples.add((stage.exit_from, label, target))
        if (stage.entry_state is not stage.exit_from
                and classify_edge(stage.entry_state, stage.exit_from) is None):
            triples.add((stage.entry_state, "(entry)", stage.exit_from))
    return triples


def _compiled_triples(ir, stage_names: set) -> set:
    """What the compiled workflow says, restricted to the STAGE-BACKED nodes.

    The wait nodes are excluded because they are what the projection ADDS --
    ``stages.LIFECYCLE_STATES``'s "the mechanism carries it onward" made
    explicit -- and comparing them against a stage record that does not exist
    would be comparing the projection to itself.
    """
    triples = set()
    for node in ir.nodes:
        if node.node_id not in stage_names:
            continue
        for edge in node.edges:
            if edge.label is None:
                continue
            triples.add((edge.from_state, edge.label, edge.to_state))
    return triples


def _expected(pipeline: tuple) -> set:
    """The stage triples with the single declared divergence applied."""
    triples = _stage_triples(pipeline)
    if "implement" in pipeline and "self_review" in pipeline:
        triples.discard((TaskState.ACTIVE, "done", TaskState.AWAITING_REVIEW))
        triples.add((TaskState.ACTIVE, "done", TaskState.SELF_REVIEWING))
    return triples


# --- the differential ------------------------------------------------------


@pytest.mark.parametrize("preset", PRESET_CASES)
def test_a_shipped_preset_compiles(preset):
    validate_pipeline(list(PRESETS[preset]))          # it is a legal pipeline
    ir = compile_workflow(VALID[f"shadow-{preset}"], origin=preset)
    assert digest(ir).startswith("sha256:")


@pytest.mark.parametrize("preset", PRESET_CASES)
def test_a_compiled_preset_says_exactly_what_the_stage_registry_says(preset):
    pipeline = PRESETS[preset]
    ir = compile_workflow(VALID[f"shadow-{preset}"], origin=preset)
    assert _compiled_triples(ir, set(pipeline)) == _expected(pipeline)


def test_the_legacy_pipeline_is_equality_with_nothing_subtracted():
    """The control that makes the divergence exactly one thing. If the
    projection were quietly rewriting anything else, this is where it shows:
    no ``discard``, no substitution, plain set equality."""
    ir = compile_workflow(VALID["shadow-legacy"], origin="legacy")
    assert _compiled_triples(ir, set(ws.LEGACY_PIPELINE)) == _stage_triples(
        ws.LEGACY_PIPELINE)
    assert "self_review" not in ws.LEGACY_PIPELINE


@pytest.mark.parametrize("preset", PRESET_CASES)
def test_every_stage_becomes_a_node_with_the_same_shape(preset):
    pipeline = PRESETS[preset]
    ir = compile_workflow(VALID[f"shadow-{preset}"], origin=preset)
    for name in pipeline:
        stage = STAGE_REGISTRY[name]
        node = ir.node(name)
        assert node.entry_state is stage.entry_state
        assert node.exit_state is stage.exit_from
        assert node.owns == stage.owns
        assert node.role is stage.role
        assert node.context == stage.context
        assert node.concurrency == (
            "inherit" if stage.concurrency is None else stage.concurrency)
        assert node.kind is (NodeKind.AGENT if stage.role else NodeKind.MECHANISM)


@pytest.mark.parametrize("preset", PRESET_CASES)
def test_the_projection_adds_only_wait_nodes(preset):
    ir = compile_workflow(VALID[f"shadow-{preset}"], origin=preset)
    added = {n.node_id for n in ir.nodes} - set(PRESETS[preset])
    assert all(ir.node(n).kind is NodeKind.WAIT for n in added)
    assert added <= set(ws.WAIT_NODE_IDS.values())


def test_only_the_carve_bearing_preset_needs_a_wait_node():
    """`stages.LIFECYCLE_STATES` exempts four states from the dead-end rule; in
    practice the shipped presets route into exactly one of them by a COMPILER
    edge, so the obligation the IR adds is one node, not four."""
    added = {}
    for preset in PRESET_CASES:
        ir = compile_workflow(VALID[f"shadow-{preset}"], origin=preset)
        added[preset] = {n.node_id for n in ir.nodes} - set(PRESETS[preset])
    assert added == {"full": {"queue_admission"}, "gated": set(), "lean": set()}


def test_a_gateless_preset_still_completes_through_the_kernel():
    """`lean` owns no post-merge state, and contract item 11 auto-advances
    VALIDATING -> COMPLETED for exactly that pipeline. If the compiler injected
    kernel edges only for OWNED states, this shipped preset would be refused --
    a false stop-loss signal."""
    ir = compile_workflow(VALID["shadow-lean"], origin="lean")
    assert not ir.state_owner(TaskState.VALIDATING)
    assert digest(ir).startswith("sha256:")


# --- the defect the differential found -------------------------------------


@pytest.mark.parametrize("preset", PRESET_CASES)
def test_the_declared_form_of_every_self_reviewing_preset_is_unreachable(preset):
    """`stages.py` declares implement's `done` exit as -> AWAITING_REVIEW
    unconditionally, and every shipped preset composes `self_review`. Nothing
    routes INTO SELF_REVIEWING in the declared record, and
    ``validate_pipeline`` cannot see it: rule 3 counts a stage's entry_state as
    "handled" merely because the stage is present.

    This is a real defect in the existing declaration, found by the new
    machinery rather than asserted by it -- and it is why the projection
    follows ``effects_exit.py`` instead of the stage record.
    """
    assert "self_review" in PRESETS[preset]
    validate_pipeline(list(PRESETS[preset]))          # the stage layer accepts it
    with pytest.raises(CompileError) as excinfo:
        compile_workflow(ws.declared_source(PRESETS[preset]), origin=preset)
    assert DiagnosticCode.UNREACHABLE_NODE in excinfo.value.codes()
    assert "self_review" in str(excinfo.value)


def test_the_kernel_entry_edge_is_carried_not_dropped():
    """The one stage entry edge `_stage_triples` excludes is present in the
    compiled workflow as a kernel edge -- excluded from the LABEL comparison,
    never from the graph."""
    excluded = {(s.entry_state, s.exit_from) for s in
                (STAGE_REGISTRY[n] for n in PRESETS["full"])
                if s.entry_state is not s.exit_from
                and classify_edge(s.entry_state, s.exit_from) is not None}
    assert excluded == {(TaskState.MERGED, TaskState.VALIDATING)}
    ir = compile_workflow(VALID["shadow-full"], origin="full")
    carried = [e for e in ir.node("post_merge_gate").kernel_edges()
               if (e.from_state, e.to_state) in excluded]
    assert len(carried) == 1
    assert carried[0].kernel_class is KernelEdgeClass.MERGE_SPINE
    assert carried[0].label is None


def test_the_divergence_is_declared_and_singular():
    assert list(ws.DECLARED_DIVERGENCES) == ["implement.done"]
    assert "effects_exit.py" in ws.DECLARED_DIVERGENCES["implement.done"]


# --- the stop-loss measurement ---------------------------------------------


def test_the_language_expresses_all_seventeen_compiler_edges():
    """The §5.4 trigger, made countable by the inventory: "the 17 compiler
    edges are the entire set the language must express... if any one of them
    needs an escape hatch, the trigger has fired".

    Two documents, because ``ACTIVE -> AWAITING_REVIEW`` and
    ``ACTIVE -> SELF_REVIEWING`` are the same outcome with different
    destinations and are alternatives in the product too. Everything is data;
    there is no hook, no callback, and no `if`.
    """
    expressed = set()
    for name in ("full-vocabulary", "full-vocabulary-legacy"):
        ir = compile_workflow(VALID[name], origin=name)
        expressed |= {(e.from_state, e.to_state) for e in ir.all_edges()
                      if e.origin is EdgeOrigin.COMPILER}
    assert expressed == COMPILER_EDGES


def test_each_full_vocabulary_document_is_a_legal_workflow_on_its_own():
    """Union coverage would be worthless if either half only compiled because
    the other existed."""
    for name in ("full-vocabulary", "full-vocabulary-legacy"):
        assert compile_workflow(VALID[name], origin=name).nodes


# --- the projection's own refusals -----------------------------------------


def test_the_projection_refuses_a_pipeline_that_routes_into_an_unowned_state():
    """`validate_pipeline` rule 3 refuses the same shape. Raising here keeps a
    projection failure from being read as a compiler failure."""
    with pytest.raises(ws.UnownedTarget):
        ws.preset_source(("implement",))
    with pytest.raises(ValueError):
        validate_pipeline(["implement"])


def test_a_wait_node_only_resumes_into_stages_the_pipeline_actually_has():
    """A carve-only pipeline routes into CARVED and has nowhere to resume to.
    The projection produces the hold with NO resume outcome rather than
    inventing a target -- which is then refused downstream as a workflow that
    can never complete, exactly as it should be."""
    node = ws.preset_source(("carve",))["nodes"][
        ws.WAIT_NODE_IDS[TaskState.CARVED]]
    assert node["outcomes"] == {}


def test_the_projection_refuses_an_unknown_stage_kind():
    with pytest.raises(KeyError):
        ws.preset_source(("teleport",))


def test_preset_names_are_the_shipped_presets():
    assert ws.preset_names() == tuple(PRESET_CASES)


def test_declared_source_is_a_no_op_without_self_review():
    """Its only edit is the divergence, so on the legacy pipeline it must
    return the projection unchanged -- otherwise the control above is testing a
    different document from the one it claims."""
    assert ws.declared_source(ws.LEGACY_PIPELINE) == ws.preset_source(
        ws.LEGACY_PIPELINE)
