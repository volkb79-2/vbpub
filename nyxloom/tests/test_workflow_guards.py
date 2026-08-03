"""The guard boundary: three independent structural layers, each with a
control that fails when the layer is removed.

The amendment's wording is the specification: "a guard named `reviewer_said_ok`
reading a report body would satisfy every listed condition and defeat the
boundary". So the tests here are not "guards are pure" -- they are "there is
nothing a guard could read that could hold text", asserted three ways.
"""

from __future__ import annotations

import ast
import inspect
from dataclasses import fields

import pytest

from nyxloom import workflow_guards as wg
from nyxloom.workflow_guards import (
    GUARD_FACT_FIELDS,
    GUARD_REGISTRY,
    GuardArgumentError,
    GuardFacts,
    GuardRef,
    UnknownGuard,
    UntypedGuardFact,
    evaluate,
    resolve_guard,
)
from workflow_corpus import guard_evaluations

#: The AST node types a guard body may contain. Anything else -- a call, an
#: import, a subscript, a comprehension, an f-string -- is a way out of the
#: boundary, so the allow-list is the check.
_ALLOWED_NODES = (
    ast.Return, ast.Attribute, ast.Name, ast.Load, ast.Compare, ast.BoolOp,
    ast.UnaryOp, ast.And, ast.Or, ast.Not, ast.Lt, ast.LtE, ast.Gt, ast.GtE,
    ast.Eq, ast.NotEq, ast.Constant, ast.Expr,
)


def _guard_body(spec) -> list:
    """Every AST node in the guard's BODY.

    Signature annotations are excluded deliberately: `-> bool` is a Name and
    `int | None` is a BinOp, and admitting either to the allow-list would open
    it to the expressions this oracle exists to refuse.
    """
    fn = ast.parse(inspect.getsource(spec.predicate).lstrip()).body[0]
    return [node for stmt in fn.body for node in ast.walk(stmt)]


# --- layer 1: the type ----------------------------------------------------


def test_every_guard_fact_is_a_bool_or_an_int():
    """Widening this record to `str` is a review-visible edit to the module AND
    to this oracle, never a quiet field addition."""
    offending = {f.name: f.type for f in fields(GuardFacts)
                 if f.type not in ("bool", "int")}
    assert not offending, (
        f"GuardFacts fields that could carry agent-authored text: {offending}")


def test_the_declared_fact_field_set_is_derived_from_the_record():
    assert GUARD_FACT_FIELDS == {f.name for f in fields(GuardFacts)}


# --- layer 2: the value ---------------------------------------------------


@pytest.mark.parametrize("smuggled", [
    "the reviewer said it looks good",
    b"bytes are text with a hat on",
    ("a", "tuple", "of", "text"),
    ["a list"],
    {"a": "dict"},
    3.5,
    None,
])
def test_a_fact_that_is_not_a_bool_or_an_int_is_refused(smuggled):
    with pytest.raises(UntypedGuardFact) as excinfo:
        GuardFacts(touches_tests=smuggled)
    assert "agent could have authored" in str(excinfo.value)


def test_the_value_layer_covers_fields_the_type_layer_would_have_allowed():
    """Layer 2 is not a duplicate of layer 1: it fires on an int-annotated
    field handed a string, which is the shape a careless derivation in CR-07b
    would produce."""
    with pytest.raises(UntypedGuardFact):
        GuardFacts(gate_rigor="high")


def test_a_well_typed_fact_record_constructs():
    facts = GuardFacts(touches_tests=True, gate_rigor=2, effective_band=4)
    assert facts.touches_tests is True and facts.gate_rigor == 2


# --- layer 3: the body ----------------------------------------------------


@pytest.mark.parametrize("name", sorted(GUARD_REGISTRY))
def test_a_guard_body_can_reach_nothing_but_its_own_arguments(name):
    spec = GUARD_REGISTRY[name]
    for node in _guard_body(spec):
        assert isinstance(node, _ALLOWED_NODES), (
            f"guard {name!r} contains {type(node).__name__}, which is a way out "
            f"of the typed-fact boundary")
        if isinstance(node, ast.Attribute):
            assert isinstance(node.value, ast.Name) and node.value.id == "facts"
            assert node.attr in GUARD_FACT_FIELDS, (
                f"guard {name!r} reads facts.{node.attr}, which is not a "
                f"declared fact")
        if isinstance(node, ast.Name):
            assert node.id in ("facts", "arg"), (
                f"guard {name!r} reaches the module-level name {node.id!r}")
        if isinstance(node, ast.Constant):
            # The allow-list's own description says INTEGER constants, and
            # `ast.Constant` alone admitted every literal type including `str`
            # (CR-07a review). A string literal in a guard body cannot carry
            # agent text -- it is reviewed source like any other line -- but a
            # rule that says one thing and checks another is the defect class
            # this file exists to refuse, one level up from the guards.
            assert type(node.value) in (int, bool), (
                f"guard {name!r} contains a {type(node.value).__name__} "
                f"literal; the boundary admits integer constants only")


@pytest.mark.parametrize("name", sorted(GUARD_REGISTRY))
def test_declared_reads_are_derived_and_compared(name):
    """`reads` is DERIVED from the body and compared -- over-broad fails
    exactly like missing. A declaration that is not verified is decoration
    (planning.py), and this program has paid for that three times."""
    spec = GUARD_REGISTRY[name]
    derived = frozenset(node.attr for node in _guard_body(spec)
                        if isinstance(node, ast.Attribute))
    assert derived == spec.reads


def test_the_body_oracle_rejects_a_guard_that_reads_agent_text():
    """The control. Without it the layer-3 assertions could be vacuous.

    `reviewer_said_ok` is the amendment's own example, written out and shown to
    be caught -- by the SHAPE of what it does, not by its name.
    """
    source = ("def reviewer_said_ok(facts, arg):\n"
              "    return 'looks good' in facts.report_body\n")
    tree = ast.parse(source)
    reads = [n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)]
    assert reads == ["report_body"]
    assert "report_body" not in GUARD_FACT_FIELDS
    assert any(not isinstance(n, _ALLOWED_NODES) for n in ast.walk(tree)), (
        "the allow-list would have admitted a membership test over text")


def test_the_registry_name_matches_the_spec_name():
    for name, spec in GUARD_REGISTRY.items():
        assert spec.name == name


# --- resolution and evaluation --------------------------------------------


def test_resolve_rejects_an_unregistered_guard():
    with pytest.raises(UnknownGuard):
        resolve_guard("reviewer_said_ok")


def test_resolve_rejects_a_missing_or_mistyped_argument():
    with pytest.raises(GuardArgumentError):
        resolve_guard("gate_rigor_below")
    with pytest.raises(GuardArgumentError):
        resolve_guard("gate_rigor_below", "three")


def test_resolve_rejects_an_argument_the_guard_does_not_take():
    with pytest.raises(GuardArgumentError):
        resolve_guard("touches_tests", 1)


def test_resolve_returns_an_ordered_reference():
    assert resolve_guard("touches_tests") == GuardRef("touches_tests", None)
    assert resolve_guard("gate_rigor_below", 3) == GuardRef("gate_rigor_below", 3)
    assert sorted([GuardRef("b"), GuardRef("a")]) == [GuardRef("a"), GuardRef("b")]


def test_guard_ref_canonical_omits_an_absent_argument():
    assert GuardRef("touches_tests").as_canonical() == {"name": "touches_tests"}
    assert GuardRef("gate_rigor_below", 3).as_canonical() == {
        "name": "gate_rigor_below", "arg": 3}


@pytest.mark.parametrize("name,arg,facts,expected", guard_evaluations(),
                         ids=lambda v: str(v))
def test_every_registered_guard_evaluates_both_ways(name, arg, facts, expected):
    assert evaluate(resolve_guard(name, arg), GuardFacts(**facts)) is expected


def test_the_truth_table_covers_every_registered_guard_in_both_directions():
    """The population check on the table above: a guard present in the registry
    but absent from the table would be evaluated by nothing."""
    outcomes: dict = {}
    for name, _arg, _facts, expected in guard_evaluations():
        outcomes.setdefault(name, set()).add(expected)
    assert set(outcomes) == set(GUARD_REGISTRY)
    assert all(v == {True, False} for v in outcomes.values())


def test_the_module_imports_nothing_from_the_package():
    """A leaf: a guard can be exercised without a snapshot, a config or a
    store, which is what stops the facts record growing a back door."""
    tree = ast.parse(inspect.getsource(wg))
    relative = [n for n in ast.walk(tree)
                if isinstance(n, ast.ImportFrom) and n.level > 0]
    assert not relative
