"""The new-vocabulary coverage oracle, and the anti-curation guard under it.

WHY THIS FILE EXISTS
--------------------
CR-06 finished with the same defect three times, and its report names the shape
exactly: *the oracle was sound and the population it ran over was not.* CR-06a's
permutation test passed over a corpus with zero ``SELF_REVIEWING`` tasks;
CR-06b's anti-absorption control was bound to a two-action case (deleting a
guard left it green, proved by ablation); CR-06c's reservation branch was
unreachable from all 15,966 differential passes. **Coverage cannot see this
class** -- every one of those lines was covered.

CR-07a introduces a whole new vocabulary, so it is precisely the package that
would repeat it. This oracle enumerates every member of that vocabulary and
fails NAMING any member no test input reaches:

* every member of every enum the IR and compiler define;
* every registered handler and every registered guard;
* every role with a declared outcome vocabulary;
* every FIELD of every IR dataclass, counted as reached only when some input
  gives it a value other than its default -- because a field nothing ever sets
  is exactly the CR-06c shape (a branch reachable from no input, at 100%
  coverage, because the default is what every test saw).

The population it runs over is ``workflow_corpus.all_sources()``, which is the
same population every workflow test compiles.
:func:`test_no_workflow_test_module_invents_its_own_manifest` is what keeps
those two facts the same fact rather than a coincidence someone maintains.
"""

from __future__ import annotations

import ast
from dataclasses import MISSING, fields
from pathlib import Path

import pytest

from nyxloom.types import Role
from nyxloom.workflow_compile import (
    AGENT_OUTCOMES,
    HANDLER_REGISTRY,
    CompileError,
    DiagnosticCode,
    compile_workflow,
)
from nyxloom.workflow_guards import GUARD_REGISTRY, GuardFacts, GuardRef
from nyxloom.workflow_ir import (
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
)
from workflow_corpus import all_sources, guard_evaluations

TEST_DIR = Path(__file__).resolve().parent
_IR_TYPES = (WorkflowIR, NodeIR, EdgeIR, JoinSpec, GuardRef, GuardFacts)


class _Observed:
    """What the corpus actually constructs."""

    def __init__(self) -> None:
        self.enums: set = set()
        self.handlers: set = set()
        self.guards: set = set()
        self.roles: set = set()
        self.codes: set = set()
        self.fields: set = set()

    def see_value(self, owner, name, value) -> None:
        if isinstance(value, (KernelEdgeClass, KernelVerb, NodeKind, EdgeOrigin,
                              JoinMode, JoinCancel, WaitResume)):
            self.enums.add(value)
        if isinstance(value, Role):
            self.roles.add(value)
        self.fields.add(f"{owner}.{name}")

    def see_record(self, record) -> None:
        owner = type(record).__name__
        for spec in fields(record):
            value = getattr(record, spec.name)
            default = (spec.default if spec.default is not MISSING
                       else spec.default_factory() if spec.default_factory
                       is not MISSING else _SENTINEL)
            if value != default:
                self.see_value(owner, spec.name, value)
            if isinstance(value, _IR_TYPES):
                self.see_record(value)
            if isinstance(value, tuple):
                for item in value:
                    if isinstance(item, _IR_TYPES):
                        self.see_record(item)


_SENTINEL = object()


def _observe() -> _Observed:
    seen = _Observed()
    for name, source in sorted(all_sources().items()):
        try:
            ir = compile_workflow(source, origin=name)
        except CompileError as error:
            seen.codes.update(error.codes())
            seen.enums.update(error.classes())
            continue
        seen.see_record(ir)
        for node in ir.nodes:
            if node.handler is not None:
                seen.handlers.add(node.handler)
            for edge in node.edges:
                if edge.label in {w.value for w in WaitResume}:
                    seen.enums.add(WaitResume(edge.label))
                if edge.guard is not None:
                    seen.guards.add(edge.guard.name)
    # The guard-facts half of the population. Compiling a manifest RESOLVES a
    # guard but never evaluates one, so a fact field would otherwise be
    # reported unreachable while `tests/test_workflow_guards.py` exercises it
    # -- the oracle must run over both halves or it is measuring the wrong set.
    for _name, _arg, facts, _expected in guard_evaluations():
        seen.see_record(GuardFacts(**facts))
    return seen


@pytest.fixture(scope="module")
def observed() -> _Observed:
    return _observe()


def _universe() -> set:
    members: set = set()
    for enum_type in (KernelEdgeClass, KernelVerb, NodeKind, EdgeOrigin,
                      JoinMode, JoinCancel, WaitResume):
        members.update(enum_type)
    return members


def test_every_member_of_every_new_enum_is_constructed_by_some_input(observed):
    unreached = sorted(f"{type(m).__name__}.{m.name}"
                       for m in _universe() - observed.enums)
    assert not unreached, (
        "vocabulary members no corpus input reaches -- coverage cannot see "
        f"this: {unreached}")


def test_every_diagnostic_code_is_produced_by_some_input(observed):
    unreached = sorted(c.value for c in set(DiagnosticCode) - observed.codes)
    assert not unreached, f"refusals no negative input triggers: {unreached}"


def test_every_registered_handler_and_guard_is_used_by_some_input(observed):
    assert set(HANDLER_REGISTRY) - observed.handlers == set()
    assert set(GUARD_REGISTRY) - observed.guards == set()


def test_every_role_with_an_outcome_vocabulary_is_dispatched_by_some_input(
        observed):
    assert set(AGENT_OUTCOMES) - observed.roles == set()
    assert set(Role) - observed.roles == set()


def test_every_ir_field_is_given_a_non_default_value_by_some_input(observed):
    """The CR-06c shape, refused structurally: a field nothing ever sets is a
    branch no input reaches, sitting at 100% coverage because every test saw
    the default."""
    universe = {f"{cls.__name__}.{spec.name}"
                for cls in _IR_TYPES for spec in fields(cls)}
    unreached = sorted(universe - observed.fields)
    assert not unreached, (
        f"IR fields no corpus input ever sets to a non-default value: {unreached}")


def test_the_oracle_is_not_vacuous():
    """The control. If `_observe` silently returned nothing, every assertion
    above would still pass by set arithmetic on an empty universe."""
    empty = _Observed()
    assert _universe() - empty.enums == _universe()
    seen = _observe()
    assert len(seen.enums) >= len(_universe())
    assert seen.codes and seen.handlers and seen.guards and seen.roles


# --- the anti-curation guard ----------------------------------------------


def _workflow_test_modules() -> list:
    return sorted(p for p in TEST_DIR.glob("test_workflow_*.py"))


def test_every_workflow_test_module_draws_from_the_shared_corpus():
    for path in _workflow_test_modules():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
                 and isinstance(n.func, ast.Name)
                 and n.func.id == "compile_workflow"]
        if not calls:
            continue
        imports = {n.module for n in ast.walk(tree)
                   if isinstance(n, ast.ImportFrom)}
        assert "workflow_corpus" in imports, path.name


def test_no_workflow_test_module_invents_its_own_manifest():
    """A manifest written inside a test module would be a population the
    oracle above never runs over -- which is CR-06's defect with a new
    vocabulary. Manifests live in `workflow_corpus.py`; mutations of them do
    too.
    """
    markers = {"schema", "entry_state", "nodes", "kernel_outcomes"}
    offenders: list = []
    for path in _workflow_test_modules():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Dict):
                continue
            keys = {k.value for k in node.keys
                    if isinstance(k, ast.Constant) and isinstance(k.value, str)}
            if keys & markers:
                offenders.append(f"{path.name}:{node.lineno}")
    assert not offenders, (
        "manifest literals outside tests/workflow_corpus.py -- the vocabulary "
        f"oracle cannot see them: {offenders}")


def test_no_workflow_test_module_passes_a_literal_to_the_compiler():
    for path in _workflow_test_modules():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == "compile_workflow" and node.args):
                assert not isinstance(node.args[0], ast.Dict), (
                    f"{path.name}:{node.lineno}")


def test_the_guard_covers_the_modules_it_claims_to():
    """Its own population check: a workflow test module that stopped matching
    the glob would leave the guard passing over nothing."""
    names = {p.name for p in _workflow_test_modules()}
    assert names >= {"test_workflow_compile.py", "test_workflow_ir.py",
                     "test_workflow_kernel_edges.py", "test_workflow_shadow.py",
                     "test_workflow_guards.py", "test_workflow_vocabulary.py"}
