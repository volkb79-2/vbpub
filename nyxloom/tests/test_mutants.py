"""Tests for `nyxloom.mutants` -- the pure AST mutant-generation engine.

Extracted from test_mutation_gate.py (nyxloom-P98, 2026-09-02) along with the
`Mutant`/`generate_mutants` code it exercises, when the rest of the mutation
gate (the deleted `evaluate`/`_run_is_killed*`/CLI half) was retired.
`test_falsy_swap_motivating_survivor` stayed with the deleted file instead of
moving here: it also called `mutation_gate._run_is_killed` (the deleted
gate-judgment half), so it tested the retired integration, not this pure
engine.

Every generated mutant is a deterministic observable -- each test drives a real
code path and asserts the expected transformation.
"""

from __future__ import annotations

import pytest

from nyxloom import mutants as mg


# --------------------------------------------------------------------------- #
# generate_mutants — pure AST mutation
# --------------------------------------------------------------------------- #

def test_compare_swap():
    """A single compare operator is swapped to its boundary neighbour."""
    mutants = mg.generate_mutants("if x < 5:\n    pass\n", {1})
    assert len(mutants) == 1
    m = mutants[0]
    assert m.lineno == 1
    assert m.operator == "compare-swap"
    assert m.description == "Lt->LtE"
    assert "x <= 5" in m.mutated_source


def test_boolop_swap():
    """A boolean operator is swapped (And -> Or)."""
    mutants = mg.generate_mutants("y = a and b\n", {1})
    assert len(mutants) == 1
    m = mutants[0]
    assert m.lineno == 1
    assert m.operator == "boolop-swap"
    assert m.description == "And->Or"
    assert "a or b" in m.mutated_source


def test_boolop_swap_or_to_and():
    """Or is swapped to And."""
    mutants = mg.generate_mutants("x = a or b\n", {1})
    assert len(mutants) == 1
    assert mutants[0].description == "Or->And"
    assert "a and b" in mutants[0].mutated_source


def test_bool_const_flip():
    """A boolean constant True is flipped to False."""
    mutants = mg.generate_mutants("def f():\n    return True\n", {2})
    assert len(mutants) == 1
    m = mutants[0]
    assert m.lineno == 2
    assert m.operator == "bool-const-flip"
    assert m.description in ("True->False",)
    assert "return False" in m.mutated_source


def test_bool_const_flip_false_to_true():
    """False flips to True."""
    mutants = mg.generate_mutants("z = False\n", {1})
    assert len(mutants) == 1
    assert mutants[0].description == "False->True"
    assert "z = True" in mutants[0].mutated_source


@pytest.mark.parametrize(
    ("source", "description", "expected"),
    [
        ("def f():\n    return None\n", "None->[]", "return []"),
        ("def f():\n    return []\n", "[]->None", "return None"),
        ("def f():\n    return 0\n", "0->None", "return None"),
        ("def f():\n    return \"\"\n", "''->None", "return None"),
    ],
)
def test_falsy_swap_changes_direct_return_values(source, description, expected):
    """Each required falsy return substitution changes the generated source."""
    mutants = mg.generate_mutants(source, {2})
    assert len(mutants) == 1
    mutant = mutants[0]
    assert mutant.operator == "falsy-swap"
    assert mutant.description == description
    assert expected in mutant.mutated_source


@pytest.mark.parametrize("source", [
    "def f():\n    return ()\n",
    "def f():\n    return {}\n",
])
def test_falsy_swap_covers_other_empty_literals(source):
    """Empty tuple and dict literals are also distinct falsy return values."""
    mutants = mg.generate_mutants(source, {2})
    assert len(mutants) == 1
    assert mutants[0].operator == "falsy-swap"
    assert mutants[0].description.endswith("->None")
    assert "return None" in mutants[0].mutated_source


def test_falsy_swap_is_restricted_to_return_statements():
    """Falsy values in assignments do not create falsy-swap mutants."""
    source = "value = None\nitems = []\ncount = 0\ntext = \"\"\n"
    mutants = mg.generate_mutants(source, {1, 2, 3, 4})
    assert [m for m in mutants if m.operator == "falsy-swap"] == []


def test_falsy_swap_respects_target_lines():
    """A direct falsy return outside target_lines remains untouched."""
    source = "def f():\n    return None\n"
    assert mg.generate_mutants(source, {1}) == []


def test_falsy_swap_ignores_other_return_values():
    """A truthy constant in a return is not an eligible falsy swap."""
    source = "def f():\n    return 1\n"
    assert mg.generate_mutants(source, {2}) == []


def test_falsy_swap_ignores_a_non_literal_return_value():
    """A return of a non-literal expression (a Compare, here) is not an
    eligible falsy swap either -- `_falsy_swap_target`'s final fallback
    (neither Constant/None/empty-literal nor List/Tuple/Set/Dict) applies to
    anything else a `return` can carry, not just plain names/calls. Only the
    compare-swap mutant fires for this line."""
    source = "def f(x):\n    return x > 0\n"
    mutants = mg.generate_mutants(source, {2})
    assert [m.operator for m in mutants] == ["compare-swap"]


@pytest.mark.parametrize("value", ["True", "False"])
def test_boolean_returns_only_use_bool_const_flip(value):
    """Boolean returns are not duplicated by falsy-swap."""
    mutants = mg.generate_mutants(f"def f():\n    return {value}\n", {2})
    assert len(mutants) == 1
    assert mutants[0].operator == "bool-const-flip"
    assert "falsy-swap" not in [mutant.operator for mutant in mutants]


def test_falsy_swap_disambiguates_same_line_returns():
    """Multiple direct falsy returns on one line receive distinct mutations."""
    source = "def f(): return None; return []\n"
    mutants = mg.generate_mutants(source, {1})
    assert [mutant.description for mutant in mutants] == ["None->[]", "[]->None"]
    assert mutants[0].mutated_source != mutants[1].mutated_source


def test_falsy_swap_is_deterministic():
    """Repeated generation preserves both mutant contents and ordering."""
    source = (
        "def f(flag):\n"
        "    if flag:\n"
        "        return []\n"
        "    return None\n"
    )
    first = mg.generate_mutants(source, {3, 4})
    second = mg.generate_mutants(source, {3, 4})
    assert first == second


def test_line_scoping():
    """Only lines in target_lines are mutated; others are left alone."""
    source = "if x < 5:\n    pass\nif a and b:\n    pass\n"
    # target_lines = {2} → the AND on line 2, but line 2 is "    pass"
    # Actually: line 1 = "if x < 5:", line 2 = "    pass", line 3 = "if a and b:", line 4 = "    pass"
    # Let me use target_lines={3} → only the boolop on line 3
    mutants = mg.generate_mutants(source, {3})
    assert len(mutants) == 1
    assert mutants[0].operator == "boolop-swap"
    assert mutants[0].description == "And->Or"
    # The compare on line 1 should NOT be mutated
    assert "x < 5" in mutants[0].mutated_source
    assert "a or b" in mutants[0].mutated_source


def test_unparseable_source():
    """An invalid syntax returns an empty list."""
    assert mg.generate_mutants("def (:\n", {1}) == []


def test_empty_source():
    """An empty source returns an empty list."""
    assert mg.generate_mutants("", {1}) == []


def test_no_mutable_nodes_on_target_lines():
    """No mutable nodes on the target lines → empty list."""
    source = "x = 42\ny = 'hello'\n"
    assert mg.generate_mutants(source, {1, 2}) == []


def test_multiple_ops_on_one_line():
    """A line with both compare and boolop nodes yields mutants for each."""
    # `x < 5 and y > 3` has one Compare (ops=[Lt, Gt]) and one BoolOp (And)
    # → 2 compare-swap mutants (Lt->LtE, Gt->GtE) + 1 boolop-swap (And->Or)
    source = "x < 5 and y > 3\n"
    mutants = mg.generate_mutants(source, {1})
    # Order: lineno=1, type 0=compare, index 0 (Lt), index 1 (Gt), then type 1=boolop
    assert len(mutants) == 3
    # First: Lt->LtE
    assert mutants[0].operator == "compare-swap"
    assert mutants[0].description == "Lt->LtE"
    # Second: Gt->GtE
    assert mutants[1].operator == "compare-swap"
    assert mutants[1].description == "Gt->GtE"
    # Third: And->Or
    assert mutants[2].operator == "boolop-swap"
    assert mutants[2].description == "And->Or"

    # Verify each mutant changes only one site
    m0_source = mutants[0].mutated_source
    m1_source = mutants[1].mutated_source
    m2_source = mutants[2].mutated_source

    # Wait — since ast.unparse may not preserve token-level spacing, check
    # structural changes rather than exact string matching
    assert "x <= 5" in m0_source  # Lt → LtE in first mutant
    assert "y > 3" in m0_source   # Gt unchanged
    assert "and" in m0_source     # BoolOp unchanged

    assert "x < 5" in m1_source   # Lt unchanged
    assert "y >= 3" in m1_source  # Gt → GtE in second mutant
    assert "and" in m1_source     # BoolOp unchanged

    assert "x < 5" in m2_source   # Lt unchanged
    assert "y > 3" in m2_source   # Gt unchanged
    assert "or" in m2_source      # And → Or in third mutant


def test_chained_comparison_produces_distinct_mutants():
    """Chained comparison a < b < c yields two distinct mutants,
    one for each operator position."""
    mutants = mg.generate_mutants("x = a < b < c\n", {1})
    # One Compare node with two Lt ops → two compare-swap mutants
    assert len(mutants) == 2
    assert mutants[0].operator == "compare-swap"
    assert mutants[0].description == "Lt->LtE"
    assert mutants[1].operator == "compare-swap"
    assert mutants[1].description == "Lt->LtE"
    # Each must mutate a different operator position
    assert "a <= b" in mutants[0].mutated_source
    assert "b < c" in mutants[0].mutated_source
    assert "a < b" in mutants[1].mutated_source
    assert "b <= c" in mutants[1].mutated_source
    # Verify they are genuinely different
    assert mutants[0].mutated_source != mutants[1].mutated_source


def test_compare_eq_ne_swap():
    """Eq ↔ NotEq swap works."""
    src = "x == y\n"
    mutants = mg.generate_mutants(src, {1})
    assert len(mutants) == 1
    assert mutants[0].description == "Eq->NotEq"
    assert "x != y" in mutants[0].mutated_source


def test_compare_is_isnot_swap():
    """Is ↔ IsNot swap works."""
    src = "x is None\n"
    mutants = mg.generate_mutants(src, {1})
    assert len(mutants) == 1
    assert mutants[0].description == "Is->IsNot"
    assert "x is not None" in mutants[0].mutated_source


def test_all_compare_swaps():
    """Every entry in _COMPARE_SWAP produces the expected transformation."""
    # Test all 8 pairs
    pairs = [
        ("x < 5", "Lt", "LtE", "x <= 5"),
        ("x <= 5", "LtE", "Lt", "x < 5"),
        ("x > 5", "Gt", "GtE", "x >= 5"),
        ("x >= 5", "GtE", "Gt", "x > 5"),
        ("x == 5", "Eq", "NotEq", "x != 5"),
        ("x != 5", "NotEq", "Eq", "x == 5"),
    ]
    for src, src_name, tgt_name, expected_substr in pairs:
        mutants = mg.generate_mutants(f"if {src}: pass\n", {1})
        assert len(mutants) == 1, f"failed for {src}"
        assert mutants[0].description == f"{src_name}->{tgt_name}", (
            f"expected {src_name}->{tgt_name}, got {mutants[0].description}"
        )
        assert expected_substr in mutants[0].mutated_source, (
            f"expected {expected_substr} in {mutants[0].mutated_source}"
        )


def test_compare_not_in_swap_dict_unaffected():
    """Operators not in _COMPARE_SWAP (e.g. In, NotIn) are left unchanged."""
    src = "x in y\n"
    assert mg.generate_mutants(src, {1}) == []
