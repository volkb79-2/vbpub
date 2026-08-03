"""The IR's own oracles: the partition, the kernel tables, and the digest.

The partition is checked against the CR-07 transition inventory DOCUMENT, not
against a second copy of itself. The inventory was derived mechanically from
the frozen graph by the controller; if `workflow_ir` and the document disagree,
one of them is wrong and this file does not get to decide which.
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

import pytest

from nyxloom import stages
from nyxloom.types import TASK_TRANSITIONS, TERMINAL_TASK_STATES, TaskState
from nyxloom.workflow_ir import (
    COMPILER_EDGES,
    COMPILER_TARGET_STATES,
    KERNEL_EDGES,
    KERNEL_EDGES_BY_STATE,
    KERNEL_OWNED_STATES,
    KERNEL_TARGET_STATES,
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
    canonical,
    canonical_json,
    classify_edge,
    digest,
    kernel_closure,
    verb_for,
)

INVENTORY = (Path(__file__).resolve().parents[1] / "nyxloom-trove" / "reports"
             / "CR-07-TRANSITION-INVENTORY-2026-08-03.md")


def _inventory_text() -> str:
    return INVENTORY.read_text(encoding="utf-8")


def test_partition_totals_match_the_inventory_document():
    """"The graph has 55 edges across 16 states: 38 kernel, 17 compiler.""" ""
    text = _inventory_text()
    match = re.search(r"(\d+) edges across (\d+) states: (\d+) kernel, "
                      r"(\d+) compiler", text)
    assert match, "the inventory no longer states its own totals"
    total, states, kernel, compiler = (int(g) for g in match.groups())
    assert len(KERNEL_EDGES) == kernel
    assert len(COMPILER_EDGES) == compiler
    assert len(KERNEL_EDGES) + len(COMPILER_EDGES) == total
    assert len(TASK_TRANSITIONS) == states


def test_kernel_class_counts_match_the_inventory_headings():
    counts = Counter(KERNEL_EDGES.values())
    declared = {name: int(n) for name, n in
                re.findall(r"### (K-[a-z-]+) \((\d+) edges?\)", _inventory_text())}
    assert declared, "the inventory no longer names its classes with counts"
    assert {k.value: v for k, v in counts.items()} == declared


def test_the_split_is_a_partition_of_targets_not_a_list():
    """The property that makes the two grammars unconfusable."""
    assert not (KERNEL_TARGET_STATES & COMPILER_TARGET_STATES)
    assert all(to in KERNEL_TARGET_STATES for _frm, to in KERNEL_EDGES)
    assert all(to in COMPILER_TARGET_STATES for _frm, to in COMPILER_EDGES)


@pytest.mark.parametrize("edge", sorted(KERNEL_EDGES,
                                        key=lambda e: (e[0].value, e[1].value)),
                         ids=lambda e: f"{e[0].value}->{e[1].value}")
def test_every_kernel_edge_has_exactly_one_verb(edge):
    frm, to = edge
    verb = verb_for(frm, to)
    assert verb is not None
    assert KERNEL_VERBS_BY_STATE[frm][verb] is to


def test_no_state_has_two_kernel_edges_sharing_a_verb():
    """The verb table is a FUNCTION -- which is what makes "the manifest names
    an authority, the kernel names the destination" a total rule."""
    for state, rows in KERNEL_EDGES_BY_STATE.items():
        verbs = [verb for _to, _cls, verb in rows]
        assert len(verbs) == len(set(verbs)), state.value


def test_compiler_edges_have_no_verb():
    assert all(verb_for(frm, to) is None for frm, to in COMPILER_EDGES)


def test_the_post_merge_spine_is_irreversible():
    """Structural fact 1 of the inventory: MERGED and VALIDATING are the ONLY
    non-terminal states with no escape edges.

    A node model granting every node a uniform escape set would silently add
    four edges to the frozen graph and make merged work cancellable. Deriving
    rather than templating is the only defence that works, so this asserts the
    derivation, not a comment.
    """
    escapeless = {s for s in TASK_TRANSITIONS
                  if s not in TERMINAL_TASK_STATES
                  and not any(cls is KernelEdgeClass.ESCAPE
                              for _to, cls, _verb in KERNEL_EDGES_BY_STATE[s])}
    assert escapeless == {TaskState.MERGED, TaskState.VALIDATING}
    for state in escapeless:
        for verb in (KernelVerb.SUPERSEDE, KernelVerb.CANCEL):
            assert verb not in KERNEL_VERBS_BY_STATE[state]


def test_merged_advances_only_to_validating_and_never_to_completed():
    """Structural fact 2: the boundary runs THROUGH VALIDATING, and
    MERGED -> COMPLETED must remain absent."""
    assert KERNEL_VERBS_BY_STATE[TaskState.MERGED] == {
        KernelVerb.ADVANCE: TaskState.VALIDATING}
    assert (TaskState.MERGED, TaskState.COMPLETED) not in KERNEL_EDGES
    assert TaskState.COMPLETED not in TASK_TRANSITIONS[TaskState.MERGED]


def test_the_merge_spine_is_a_strict_chain():
    """Each of MERGE_READY, MERGED and COMPLETED has exactly ONE non-escape
    entry -- the inventory's K-merge-spine claim, checked on the graph."""
    for target, expected in ((TaskState.MERGE_READY, TaskState.AWAITING_REVIEW),
                             (TaskState.MERGED, TaskState.MERGE_READY),
                             (TaskState.COMPLETED, TaskState.VALIDATING)):
        entries = {frm for frm, to in KERNEL_EDGES
                   if to is target and KERNEL_EDGES[(frm, to)]
                   is not KernelEdgeClass.ESCAPE}
        entries |= {frm for frm, to in COMPILER_EDGES if to is target}
        assert entries == {expected}, target.value


def test_operator_retry_is_recorded_and_not_deleted_as_dead():
    """The one edge no code path plans. The inventory records it precisely so
    CR-07 does not remove it as unreachable."""
    assert KERNEL_EDGES[(TaskState.BLOCKED, TaskState.VALIDATING)] is (
        KernelEdgeClass.OPERATOR_RETRY)
    assert verb_for(TaskState.BLOCKED, TaskState.VALIDATING) is (
        KernelVerb.OPERATOR_RETRY)


def test_classify_edge_is_total_over_hypothetical_edges():
    """A manifest that invents an edge the frozen graph does not have must
    still be refused with the class it was reaching for."""
    assert (TaskState.ACTIVE, TaskState.MERGE_READY) not in KERNEL_EDGES
    assert classify_edge(TaskState.ACTIVE, TaskState.MERGE_READY) is (
        KernelEdgeClass.MERGE_SPINE)
    assert classify_edge(TaskState.ACTIVE, TaskState.QUEUED) is None


def test_kernel_owned_states_track_the_stage_layer():
    """The IR's lifecycle region and `stages.LIFECYCLE_STATES` are the same
    claim; pinned rather than imported so the compiler does not depend on the
    layer it replaces."""
    assert KERNEL_OWNED_STATES == stages.LIFECYCLE_STATES


def test_wait_resume_regions_are_legal_frozen_edges():
    for state in KERNEL_OWNED_STATES:
        for label, region in WAIT_RESUME_REGIONS.items():
            for target in region & TASK_TRANSITIONS[state]:
                assert (state, target) in COMPILER_EDGES, (label, state, target)


def test_kernel_closure_completes_the_spine_for_a_gateless_pipeline():
    """A `lean` pipeline owns no post-merge state, and contract item 11 still
    auto-advances VALIDATING -> COMPLETED. Injecting only owned states would
    compile a shipped preset into a workflow that can never complete."""
    owned = frozenset({TaskState.MERGE_READY})
    closed = kernel_closure(owned)
    assert TaskState.MERGED in closed
    assert TaskState.VALIDATING in closed
    assert TaskState.COMPLETED in closed


def test_kernel_closure_stops_at_terminal_states():
    assert kernel_closure(frozenset({TaskState.COMPLETED})) == frozenset(
        {TaskState.COMPLETED})


# --- canonical form and digest --------------------------------------------


def _node(node_id: str, **kw) -> NodeIR:
    base = dict(kind=NodeKind.MECHANISM, entry_state=TaskState.MERGE_READY,
                exit_state=TaskState.MERGE_READY,
                owns=frozenset({TaskState.MERGE_READY}), handler="auto_merge")
    base.update(kw)
    return NodeIR(node_id=node_id, **base)


def _ir(nodes) -> WorkflowIR:
    return WorkflowIR(workflow_id="w", version=1, start="a", nodes=tuple(nodes))


def test_canonical_form_is_independent_of_node_declaration_order():
    a, b = _node("a"), _node("b")
    assert canonical(_ir([a, b])) == canonical(_ir([b, a]))
    assert digest(_ir([a, b])) == digest(_ir([b, a]))


def test_canonical_form_is_independent_of_edge_declaration_order():
    e1 = EdgeIR(TaskState.MERGE_READY, TaskState.MERGED, EdgeOrigin.KERNEL,
                "merged", KernelEdgeClass.MERGE_SPINE, KernelVerb.ADVANCE)
    e2 = EdgeIR(TaskState.MERGE_READY, TaskState.CANCELLED, EdgeOrigin.KERNEL,
                None, KernelEdgeClass.ESCAPE, KernelVerb.CANCEL)
    assert digest(_ir([_node("a", edges=(e1, e2))])) == digest(
        _ir([_node("a", edges=(e2, e1))]))


def test_digest_changes_when_the_workflow_changes():
    """The non-vacuity control: a stable digest is also what a broken digest
    function produces."""
    base = _ir([_node("a")])
    assert digest(base) != digest(_ir([_node("a", budget=2)]))
    assert digest(base) != digest(_ir([_node("a"), _node("b")]))
    assert digest(base) != digest(
        WorkflowIR(workflow_id="w", version=2, start="a", nodes=base.nodes))


def test_digest_is_prefixed_and_stable_across_processes():
    value = digest(_ir([_node("a")]))
    assert value.startswith("sha256:") and len(value) == len("sha256:") + 64
    assert value == digest(_ir([_node("a")]))


def test_canonical_json_is_compact_and_key_sorted():
    text = canonical_json(_ir([_node("a")]))
    assert ", " not in text and '": ' not in text
    assert text.index('"id"') < text.index('"nodes"') < text.index('"start"')


def test_optional_node_fields_are_absent_rather_than_null():
    """A null is a value a later reader can misinterpret; absence is not."""
    plain = canonical(_ir([_node("a")]))["nodes"][0]
    assert "role" not in plain and "prompt" not in plain and "join" not in plain
    rich = canonical(_ir([_node(
        "a", prompt="p/v1",
        join=JoinSpec("g", JoinMode.ALL, JoinCancel.CANCEL_SIBLINGS))]))
    assert rich["nodes"][0]["join"] == {
        "group": "g", "mode": "all", "on_cancel": "cancel_siblings"}


def test_workflow_ir_lookup_helpers():
    ir = _ir([_node("a"), _node("b", entry_state=TaskState.MERGED,
                                exit_state=TaskState.VALIDATING,
                                owns=frozenset({TaskState.MERGED}))])
    assert ir.node("b").node_id == "b"
    with pytest.raises(KeyError):
        ir.node("ghost")
    assert ir.state_owner(TaskState.MERGE_READY) == ("a",)
    assert ir.all_edges() == ()


def test_wait_resume_vocabulary_covers_every_lifecycle_compiler_edge():
    """Two labels, six edges: every compiler edge out of a kernel-owned state
    is expressible, and each label's meaning is checkable."""
    lifecycle = {(frm, to) for frm, to in COMPILER_EDGES
                 if frm in KERNEL_OWNED_STATES}
    covered = {(frm, to) for frm, to in lifecycle
               for label in WaitResume if to in WAIT_RESUME_REGIONS[label]}
    assert covered == lifecycle
