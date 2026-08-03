"""The compiler's rejection conditions -- parent plan §4.3, one test each.

The parent's CR-07 acceptance is "each compile-time rejection condition in
section 4.3 has a negative test". The mapping from its nine numbered conditions
to this file's cases is :data:`CONDITION_COVERAGE`, and
:func:`test_every_numbered_rejection_condition_has_a_negative_test` refuses to
pass if any of the nine is unmapped or its case is missing from the corpus --
so the mapping cannot rot into a comment.

Manifests come from `tests/workflow_corpus.py`; see that module for why.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from nyxloom import stages, workflow_compile as wc
from nyxloom.types import Role, TaskState
from nyxloom.workflow_compile import (
    AGENT_OUTCOMES,
    HANDLER_REGISTRY,
    KNOWN_CONTEXT_FLAGS,
    CompileError,
    DiagnosticCode,
    compile_workflow,
)
from nyxloom.workflow_ir import EdgeOrigin, KernelEdgeClass, NodeKind, digest
from workflow_corpus import negative_sources, valid_sources

NEGATIVES = negative_sources()
VALID = valid_sources()

#: The parent's nine numbered rejection conditions, mapped to the corpus cases
#: that exercise them. A condition with no case fails the mapping test below.
CONDITION_COVERAGE: dict = {
    1: ["schema-unknown-field", "unknown-handler", "unknown-role",
        "guard-unknown", "unknown-context-flag", "unknown-state",
        "schema-wrong-const", "join-policy-missing"],
    2: ["undeclared-outcome", "duplicate-outcome", "unrouted-outcome"],
    3: ["absent-node", "outcome-names-compiler-state",
        "outcome-routes-to-kernel-node", "illegal-outcome-transition",
        "illegal-entry-exit-pair", "node-on-terminal-state",
        "agent-on-kernel-owned-state"],
    4: ["unreachable-node", "no-success-path", "unknown-start"],
    5: ["unbounded-cycle"],
    6: ["merge-without-review", "mechanism-approves"],
    7: ["agent-holds-merge", "merge-without-review"],
    8: ["join-policy-missing", "join-policy-inconsistent",
        "duplicate-state-owner"],
    9: ["agent-without-prompt", "agent-unversioned-prompt"],
}


def _refuse(name: str) -> CompileError:
    with pytest.raises(CompileError) as excinfo:
        compile_workflow(NEGATIVES[name], origin=name)
    return excinfo.value


def _codes(name: str) -> frozenset:
    return _refuse(name).codes()


# --- the mapping to the parent's numbered conditions ------------------------


def test_every_numbered_rejection_condition_has_a_negative_test():
    assert sorted(CONDITION_COVERAGE) == list(range(1, 10))
    missing = {n: [c for c in cases if c not in NEGATIVES]
               for n, cases in CONDITION_COVERAGE.items()}
    assert not any(missing.values()), f"cases absent from the corpus: {missing}"


@pytest.mark.parametrize("condition", sorted(CONDITION_COVERAGE))
def test_each_numbered_condition_actually_refuses(condition):
    for case in CONDITION_COVERAGE[condition]:
        _refuse(case)


# --- condition 1: unknown handler, role, prompt, guard, field --------------


def test_an_unknown_top_level_or_node_field_is_refused():
    assert DiagnosticCode.SCHEMA in _codes("schema-unknown-field")
    assert DiagnosticCode.SCHEMA in _codes("schema-wrong-const")


def test_an_unknown_state_handler_role_or_context_flag_is_refused():
    assert DiagnosticCode.UNKNOWN_STATE in _codes("unknown-state")
    assert DiagnosticCode.UNKNOWN_STATE in _codes("unknown-exit-state")
    assert DiagnosticCode.UNKNOWN_HANDLER in _codes("unknown-handler")
    assert DiagnosticCode.UNKNOWN_ROLE in _codes("unknown-role")
    assert DiagnosticCode.UNKNOWN_CONTEXT_FLAG in _codes("unknown-context-flag")


def test_role_declaration_is_bound_to_the_node_kind():
    assert DiagnosticCode.HANDLER_ROLE_MISMATCH in _codes("agent-without-role")
    assert DiagnosticCode.HANDLER_ROLE_MISMATCH in _codes("mechanism-with-role")


def test_the_amendments_own_smuggling_guard_is_refused_by_name():
    """`reviewer_said_ok` does not exist, and cannot be written -- see
    tests/test_workflow_guards.py for why the registry is not the only
    defence."""
    error = _refuse("guard-unknown")
    assert DiagnosticCode.UNKNOWN_GUARD in error.codes()
    assert "reviewer_said_ok" in str(error)


def test_guard_arguments_are_type_checked_at_compile_time():
    assert DiagnosticCode.GUARD_ARGUMENT in _codes("guard-missing-arg")
    assert DiagnosticCode.GUARD_ARGUMENT in _codes("guard-unwanted-arg")


def test_a_guard_on_an_unrouted_outcome_is_refused():
    assert DiagnosticCode.GUARD_ON_UNBOUND_OUTCOME in _codes(
        "guard-on-unbound-outcome")


# --- condition 2: outcomes the result type does not declare ---------------


def test_outcome_vocabulary_is_closed_and_exhaustively_routed():
    assert DiagnosticCode.UNDECLARED_OUTCOME in _codes("undeclared-outcome")
    assert DiagnosticCode.UNROUTED_OUTCOME in _codes("unrouted-outcome")
    assert DiagnosticCode.DUPLICATE_OUTCOME in _codes("duplicate-outcome")


def test_an_unknown_kernel_verb_is_refused_against_the_closed_vocabulary():
    error = _refuse("unknown-kernel-verb")
    assert DiagnosticCode.KERNEL_VERB_UNAVAILABLE in error.codes()
    assert "teleport" in str(error)


def test_a_kernel_verb_the_frozen_graph_does_not_offer_is_refused():
    """The post-merge spine is irreversible: `cancel` is not available at
    VALIDATING, and the refusal says which class it belongs to."""
    error = _refuse("kernel-verb-unavailable")
    assert DiagnosticCode.KERNEL_VERB_UNAVAILABLE in error.codes()
    assert KernelEdgeClass.ESCAPE in error.classes()


# --- condition 3: absent nodes and lifecycle mutation outside the kernel ---


def test_an_outcome_to_an_absent_node_is_refused():
    assert DiagnosticCode.ABSENT_NODE in _codes("absent-node")


def test_an_outcome_naming_a_state_is_a_lifecycle_mutation_and_is_refused():
    error = _refuse("outcome-names-compiler-state")
    assert DiagnosticCode.STATE_IS_NOT_A_NODE in error.codes()
    assert "lifecycle mutation outside the kernel" in str(error)


def test_a_compiler_outcome_into_the_merge_spine_is_refused():
    error = _refuse("outcome-routes-to-kernel-node")
    assert DiagnosticCode.KERNEL_EDGE_NOT_DECLARABLE in error.codes()
    assert KernelEdgeClass.MERGE_SPINE in error.classes()


def test_a_transition_the_frozen_graph_lacks_is_refused():
    assert DiagnosticCode.ILLEGAL_TRANSITION in _codes(
        "illegal-outcome-transition")
    assert DiagnosticCode.ILLEGAL_TRANSITION in _codes("illegal-entry-exit-pair")


def test_node_placement_respects_terminal_and_kernel_owned_states():
    assert DiagnosticCode.STATE_NOT_NODE_OWNABLE in _codes(
        "node-on-terminal-state")
    assert DiagnosticCode.STATE_NOT_NODE_OWNABLE in _codes(
        "agent-on-kernel-owned-state")
    assert DiagnosticCode.WAIT_NODE_PLACEMENT in _codes(
        "wait-node-off-kernel-state")


def test_a_wait_node_may_not_resume_into_the_wrong_region():
    error = _refuse("wait-resume-wrong-region")
    assert DiagnosticCode.WAIT_RESUME_REGION in error.codes()
    assert "resume_implement" in str(error)


# --- condition 4: reachability -------------------------------------------


def test_an_unknown_start_node_is_refused():
    assert DiagnosticCode.UNKNOWN_START in _codes("unknown-start")


def test_an_unreachable_node_is_refused():
    assert DiagnosticCode.UNREACHABLE_NODE in _codes("unreachable-node")


def test_a_workflow_that_can_never_complete_is_refused():
    """K-escape makes SUPERSEDED and CANCELLED reachable from every
    non-terminal node, so "a terminal state is reachable" is satisfied by the
    kernel and carries no information. The check that does is COMPLETED."""
    error = _refuse("no-success-path")
    assert DiagnosticCode.NO_SUCCESS_PATH in error.codes()
    assert "can never finish" in str(error)


# --- condition 5: unbounded loops -----------------------------------------


def test_a_cycle_with_no_bounded_counter_is_refused():
    error = _refuse("unbounded-cycle")
    assert DiagnosticCode.UNBOUNDED_CYCLE in error.codes()
    assert "implement" in str(error)


def test_the_cycle_check_ignores_kernel_edges():
    """A workflow whose only loop is through the kernel (block -> resume) needs
    no budget: the escalation hold is a HUMAN, and a human is not a retry
    counter the compiler can bound."""
    ir = compile_workflow(VALID["full-vocabulary"], origin="full-vocabulary")
    kernel_only = [n for n in ir.nodes if n.node_id == "escalation_hold"][0]
    assert kernel_only.budget is None


# --- conditions 6 and 7: merge and approval authority ---------------------


def test_a_workflow_that_reaches_merge_without_an_independent_reviewer_fails():
    error = _refuse("merge-without-review")
    assert DiagnosticCode.MERGE_WITHOUT_REVIEW in error.codes()
    assert KernelEdgeClass.MERGE_SPINE in error.classes()


def test_approval_for_self_is_refused():
    error = _refuse("merge-without-review")
    assert DiagnosticCode.AGENT_SELF_AUTHORITY in error.codes()
    assert "approval-for-self" in str(error)


def test_a_mechanism_may_not_stand_in_for_the_independent_reviewer():
    error = _refuse("mechanism-approves")
    assert DiagnosticCode.AGENT_SELF_AUTHORITY in error.codes()
    assert DiagnosticCode.MERGE_WITHOUT_REVIEW in error.codes()


def test_an_agent_may_not_hold_merge_authority():
    error = _refuse("agent-holds-merge")
    assert DiagnosticCode.AGENT_SELF_AUTHORITY in error.codes()
    assert "cannot emit a merge directly" in str(error)


def test_the_gate_half_of_condition_six_is_documented_as_out_of_scope():
    """Stated in the module, so the boundary is reviewable rather than
    silently narrowed: gates are project configuration, and what the compiler
    guarantees instead is that no agent can drive the merge node."""
    doc = inspect.getdoc(wc._authority_diagnostics)
    assert "pre-merge GATE half" in doc and "project configuration" in doc


# --- condition 8: typed joins ---------------------------------------------


def test_parallel_branches_require_a_typed_join_policy():
    assert DiagnosticCode.JOIN_POLICY_MISSING in _codes("join-policy-missing")
    assert DiagnosticCode.JOIN_POLICY_INCONSISTENT in _codes(
        "join-policy-inconsistent")
    assert DiagnosticCode.DUPLICATE_STATE_OWNER in _codes("duplicate-state-owner")


@pytest.mark.parametrize("case", ["join-all-cancel", "join-any-leave"])
def test_a_well_formed_join_group_compiles(case):
    ir = compile_workflow(VALID[case], origin=case)
    assert len(ir.state_owner(TaskState.AWAITING_REVIEW)) == 2


# --- condition 9: prompt versions in the digest ---------------------------


def test_an_agent_node_must_name_a_versioned_prompt():
    assert DiagnosticCode.PROMPT_VERSION_MISSING in _codes("agent-without-prompt")
    assert DiagnosticCode.PROMPT_VERSION_MISSING in _codes(
        "agent-unversioned-prompt")
    assert DiagnosticCode.PROMPT_VERSION_MISSING in _codes("mechanism-with-prompt")


def test_the_prompt_version_is_part_of_the_digest():
    base = VALID["full-vocabulary"]
    bumped = valid_sources()["full-vocabulary"]
    bumped["nodes"]["implement"]["prompt"] = "implement/v2"
    assert digest(compile_workflow(base)) != digest(compile_workflow(bumped))


# --- reserved keys ---------------------------------------------------------


@pytest.mark.parametrize("case,expected", [
    ("reserved-escapes-list", KernelEdgeClass.ESCAPE),
    ("reserved-escapes-str", KernelEdgeClass.ESCAPE),
    ("reserved-transitions", None),
    ("reserved-predecessors-bare", KernelEdgeClass.MERGE_SPINE),
    ("reserved-on-unparsable-node", KernelEdgeClass.BLOCK),
    ("reserved-node-body-not-object", KernelEdgeClass.MERGE_SPINE),
    ("reserved-nodes-not-object", KernelEdgeClass.ESCAPE),
])
def test_a_reserved_key_is_refused_with_the_class_it_reaches_for(case, expected):
    error = _refuse(case)
    assert DiagnosticCode.KERNEL_NOT_MANIFEST_DATA in error.codes()
    if expected is None:
        assert "all five kernel classes" in str(error)
    else:
        assert expected in error.classes()


def test_reserved_keys_are_checked_before_the_schema():
    """Otherwise `additionalProperties: false` answers first and the author
    learns nothing about which safety property they were reaching into."""
    error = _refuse("reserved-transitions")
    assert error.codes() == {DiagnosticCode.KERNEL_NOT_MANIFEST_DATA}


# --- positive properties ---------------------------------------------------


@pytest.mark.parametrize("case", sorted(VALID))
def test_every_valid_corpus_manifest_compiles(case):
    ir = compile_workflow(VALID[case], origin=case)
    assert ir.nodes and digest(ir).startswith("sha256:")


def test_compilation_is_deterministic_and_source_shape_free():
    """The digest is a function of the IR, not of the source text: reordering
    nodes, outcomes and context flags cannot move it."""
    plain = valid_sources()["full-vocabulary"]
    shuffled = valid_sources()["full-vocabulary"]
    shuffled["nodes"] = dict(reversed(list(shuffled["nodes"].items())))
    implement = shuffled["nodes"]["implement"]
    implement["outcomes"] = dict(reversed(list(implement["outcomes"].items())))
    shuffled["nodes"]["review"]["context"] = list(
        reversed(shuffled["nodes"]["review"]["context"]))
    assert digest(compile_workflow(plain)) == digest(compile_workflow(shuffled))


def test_the_compile_error_carries_its_origin_and_reads_as_a_report():
    error = _refuse("absent-node")
    assert error.origin == "absent-node"
    assert "workflow compilation failed (absent-node)" in str(error)
    assert all(d.location for d in error.diagnostics)


def test_a_compiled_node_records_its_kind_role_and_packet_context():
    ir = compile_workflow(VALID["full-vocabulary"], origin="fv")
    kinds = {n.node_id: n.kind for n in ir.nodes}
    assert kinds["implement"] is NodeKind.AGENT
    assert kinds["triage"] is NodeKind.MECHANISM
    assert kinds["escalation_hold"] is NodeKind.WAIT
    review = ir.node("review")
    assert review.role is Role.REVIEW_INDEPENDENT
    assert review.context == frozenset({"session-reuse", "spine-digest"})


def test_the_entry_edge_is_compiled_when_entry_and_exit_differ():
    ir = compile_workflow(VALID["full-vocabulary"], origin="fv")
    entry = [e for e in ir.node("implement").compiler_edges()
             if e.label == "(entry)"]
    assert len(entry) == 1
    assert (entry[0].from_state, entry[0].to_state) == (
        TaskState.QUEUED, TaskState.ACTIVE)
    # post_merge's MERGED -> VALIDATING entry is KERNEL, so it is injected
    # rather than declared -- the boundary running THROUGH a state.
    post = ir.node("post_merge")
    assert not [e for e in post.compiler_edges() if e.label == "(entry)"]
    assert any(e.from_state is TaskState.MERGED
               and e.to_state is TaskState.VALIDATING
               and e.origin is EdgeOrigin.KERNEL for e in post.kernel_edges())


def test_a_bound_guard_travels_onto_both_the_edge_and_the_digest():
    ir = compile_workflow(VALID["guard-showcase"], origin="guards")
    guarded = {e.label: e.guard for e in ir.node("implement").edges if e.guard}
    assert guarded["done"].name == "touches_tests"
    assert guarded["dead_end"].name == "touches_infra"
    assert digest(ir) != digest(compile_workflow(VALID["full-vocabulary"]))


# --- registry consistency --------------------------------------------------


def test_the_context_flag_menu_tracks_the_stage_layer():
    assert KNOWN_CONTEXT_FLAGS == stages.KNOWN_CONTEXT_FLAGS


def test_every_role_has_a_declared_outcome_vocabulary():
    """A role added to types.py with no outcomes would otherwise be a KeyError
    two phases after the manifest named it."""
    assert set(AGENT_OUTCOMES) == set(Role)


def test_required_outcomes_are_a_subset_of_declared_outcomes():
    for spec in HANDLER_REGISTRY.values():
        if spec.outcomes is not None:
            assert spec.required <= spec.outcomes
    for declared, required in AGENT_OUTCOMES.values():
        assert required <= declared


def test_the_required_outcomes_are_exactly_what_stages_declares_today():
    """The optional extras are the two compiler edges reconcile reaches by a
    context-sensitive upgrade rather than by declaration. Requiring them would
    force `carve` into every pipeline and break the carve-less presets --
    the exact trap stages.py's triage comment warns about."""
    for stage_name, handler in (("triage", "triage"), ("auto_merge", "auto_merge"),
                                ("post_merge_gate", "post_merge_gate")):
        declared = {label for label, _t in stages.STAGE_REGISTRY[stage_name].exit_map}
        assert HANDLER_REGISTRY[handler].required == declared
    for stage_name, role in (("carve", Role.CARVER), ("implement", Role.IMPLEMENTER),
                             ("self_review", Role.SELF_REVIEW),
                             ("review_independent", Role.REVIEW_INDEPENDENT)):
        declared = {label for label, _t in stages.STAGE_REGISTRY[stage_name].exit_map}
        assert AGENT_OUTCOMES[role][1] == declared


def test_the_compiler_never_logs():
    """`planning.py`'s reason: this sits inside the path CR-07b calls from
    config load and from the planner, and a DEBUG record on a pure function is
    how stages.effective_concurrency made the planner's purity false."""
    for module in ("workflow_compile.py", "workflow_ir.py", "workflow_guards.py"):
        src = Path(wc.__file__).with_name(module).read_text(encoding="utf-8")
        tree = ast.parse(src)
        imported = {n.module for n in ast.walk(tree)
                    if isinstance(n, ast.ImportFrom) and n.module}
        assert "log" not in imported, module


def test_jsonschema_is_imported_lazily():
    """The gate container's fake CLI runs without it -- CR-03's own gate found
    a module-scope import killing every dispatched leg."""
    tree = ast.parse(Path(wc.__file__).read_text(encoding="utf-8"))
    top_level = [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))]
    assert not any("jsonschema" in ast.dump(n) for n in top_level)
    inner = [n for n in ast.walk(tree) if isinstance(n, ast.Import)
             and any(a.name == "jsonschema" for a in n.names)]
    assert len(inner) == 1
