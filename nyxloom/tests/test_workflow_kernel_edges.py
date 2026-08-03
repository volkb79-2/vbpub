"""The negative corpus for all 38 kernel edges.

The CR-07 inventory's obligation, per class, in one parameterized shape. Each
of the 38 edges is attacked THREE ways and asserted present ONCE:

  A. redirect / add  -- name the kernel target as an outcome value;
  B. remove          -- the reserved node key `suppress`;
  C. redefine        -- the reserved top-level key `predecessors`, which is the
                        inventory's own words for the K-escape and
                        K-merge-spine obligations;
  D. omit            -- not an error: the compiled node carries the edge
                        regardless, which is what "unrepresentable to remove"
                        MEANS.

Every refusal must name the edge's own inventory class, because a refusal that
does not say which safety property it protects teaches nothing and the next
author works around it.

All manifests come from `tests/workflow_corpus.py`; see that module for why.
"""

from __future__ import annotations

import pytest

from nyxloom.workflow_compile import (
    CompileError,
    DiagnosticCode,
    compile_workflow,
    expected_kernel_edges,
    kernel_edge_report,
)
from nyxloom.workflow_ir import (
    KERNEL_EDGES,
    EdgeOrigin,
    KernelEdgeClass,
)
from workflow_corpus import (
    kernel_edges,
    predecessor_source,
    redirect_source,
    suppress_source,
    valid_sources,
)

EDGES = kernel_edges()
IDS = [f"{a.value}->{b.value}" for a, b in EDGES]


def _refusal(source, name):
    with pytest.raises(CompileError) as excinfo:
        compile_workflow(source, origin=name)
    return excinfo.value


def _matching(error, code, from_state, to_state):
    return [d for d in error.diagnostics
            if d.code is code
            and from_state.value in d.message and to_state.value in d.message]


# --- A: redirect / add ----------------------------------------------------


@pytest.mark.parametrize("edge", EDGES, ids=IDS)
def test_a_manifest_cannot_declare_a_kernel_edge(edge):
    from_state, to_state = edge
    error = _refusal(redirect_source(from_state, to_state), "redirect")
    hits = _matching(error, DiagnosticCode.KERNEL_EDGE_NOT_DECLARABLE,
                     from_state, to_state)
    assert hits, [str(d) for d in error.diagnostics]
    assert hits[0].kernel_class is KERNEL_EDGES[edge]
    assert KERNEL_EDGES[edge].value in str(hits[0])


# --- B: remove ------------------------------------------------------------


@pytest.mark.parametrize("edge", EDGES, ids=IDS)
def test_a_manifest_cannot_suppress_a_kernel_edge(edge):
    from_state, to_state = edge
    error = _refusal(suppress_source(from_state, to_state), "suppress")
    hits = _matching(error, DiagnosticCode.KERNEL_NOT_MANIFEST_DATA,
                     from_state, to_state)
    assert hits, [str(d) for d in error.diagnostics]
    assert hits[0].kernel_class is KERNEL_EDGES[edge]


# --- C: redefine the predecessor set --------------------------------------


@pytest.mark.parametrize("edge", EDGES, ids=IDS)
def test_a_manifest_cannot_define_its_own_predecessor_set(edge):
    from_state, to_state = edge
    error = _refusal(predecessor_source(from_state, to_state), "predecessors")
    hits = _matching(error, DiagnosticCode.KERNEL_NOT_MANIFEST_DATA,
                     from_state, to_state)
    assert hits, [str(d) for d in error.diagnostics]
    assert hits[0].kernel_class is KERNEL_EDGES[edge]


# --- D: omission is not removal -------------------------------------------


@pytest.fixture(scope="module")
def compiled_full_vocabulary():
    return compile_workflow(valid_sources()["full-vocabulary"],
                            origin="full-vocabulary")


@pytest.mark.parametrize("edge", EDGES, ids=IDS)
def test_a_compiled_workflow_carries_every_kernel_edge_of_the_states_it_owns(
        edge, compiled_full_vocabulary):
    """The manifest mentions almost none of these. They are all there."""
    owned = frozenset(s for n in compiled_full_vocabulary.nodes for s in n.owns)
    assert edge[0] in owned, (
        "the full-vocabulary workflow no longer owns every non-terminal state, "
        "so this is no longer a proof about all 38 edges")
    present = {(e.from_state, e.to_state)
               for e in compiled_full_vocabulary.all_edges()
               if e.origin is EdgeOrigin.KERNEL}
    assert edge in present


def test_the_full_vocabulary_workflow_owns_every_non_terminal_state(
        compiled_full_vocabulary):
    """The population guard for the test above: if this workflow stopped
    covering a state, the per-edge presence test would silently narrow."""
    owned = frozenset(s for n in compiled_full_vocabulary.nodes for s in n.owns)
    assert expected_kernel_edges(owned) == frozenset(KERNEL_EDGES)


def test_kernel_edge_counts_per_class_match_the_inventory(
        compiled_full_vocabulary):
    report = kernel_edge_report(compiled_full_vocabulary)
    assert {k: len(v) for k, v in report.items()} == {
        KernelEdgeClass.ESCAPE: 22,
        KernelEdgeClass.MERGE_SPINE: 4,
        KernelEdgeClass.BLOCK: 5,
        KernelEdgeClass.DECISION: 6,
        KernelEdgeClass.OPERATOR_RETRY: 1,
    }


def test_merged_and_validating_get_no_escape_edges_from_the_compiler(
        compiled_full_vocabulary):
    """Structural fact 1, asserted on a COMPILED workflow rather than on the
    table: a node model with a uniform escape template would add four edges
    here and nowhere else."""
    for node in compiled_full_vocabulary.nodes:
        for edge in node.kernel_edges():
            if edge.kernel_class is KernelEdgeClass.ESCAPE:
                assert edge.from_state.value not in ("MERGED", "VALIDATING")


def test_merged_never_reaches_completed_directly(compiled_full_vocabulary):
    """Structural fact 2: every merged task transits VALIDATING, gate or no
    gate."""
    pairs = {(e.from_state.value, e.to_state.value)
             for e in compiled_full_vocabulary.all_edges()}
    assert ("MERGED", "COMPLETED") not in pairs
    assert ("MERGED", "VALIDATING") in pairs


def test_an_unlabelled_kernel_edge_is_still_an_edge(compiled_full_vocabulary):
    """K-operator-retry is bindable by no node -- only a wait node may sit on
    BLOCKED and its vocabulary is the two resume labels -- so the edge exists
    with no label. That is the correct shape for an OPERATOR authority, and it
    is why 'the manifest did not mention it' can never mean 'it is gone'."""
    retries = [e for e in compiled_full_vocabulary.all_edges()
               if e.kernel_class is KernelEdgeClass.OPERATOR_RETRY]
    assert len(retries) == 1
    assert retries[0].label is None
