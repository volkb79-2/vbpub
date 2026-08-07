"""``assay.mutation`` itself: the closed catalogue, :class:`Mutant`'s
construction-time discipline and its derived :attr:`~Mutant.identity`
(A-092/A-114), and the byte<->line arithmetic every splice depends on.

Never touches an adapter or ``ast`` — these are the language-free pieces
P11 owns, tested in isolation the same way ``test_verdict_claims.py`` tests
``Coverage``/``Claim`` construction independently of any producer.
"""

from __future__ import annotations

import dataclasses

import pytest

from assay.mutation import MUTATION_OPERATORS, Mutant, byte_offset, line_for_offset


def test_mutation_operators_is_the_closed_four_value_catalogue():
    """A-112/A-114's own catalogue, verbatim from DESIGN-GUIDE §11's TOML
    example -- never fewer, never an invented fifth."""
    assert MUTATION_OPERATORS == {
        "compare-swap",
        "boolop-swap",
        "bool-const-flip",
        "falsy-swap",
    }


def _mutant(**overrides) -> Mutant:
    fields = dict(
        lineno=3,
        operator="compare-swap",
        description="Lt->LtE",
        mutated_text="x = 1\nif a <= b:\n    pass\n",
    )
    fields.update(overrides)
    return Mutant(**fields)


def test_a_mutant_constructs_with_valid_fields():
    mutant = _mutant()
    assert mutant.lineno == 3
    assert mutant.operator == "compare-swap"
    assert mutant.description == "Lt->LtE"
    assert mutant.mutated_text == "x = 1\nif a <= b:\n    pass\n"


def test_a_mutant_is_frozen():
    mutant = _mutant()
    with pytest.raises(dataclasses.FrozenInstanceError):
        mutant.lineno = 99  # type: ignore[misc]


@pytest.mark.parametrize("bad_lineno", [0, -1, 1.5, "3", True, False])
def test_mutant_rejects_a_non_positive_or_non_integer_lineno(bad_lineno):
    with pytest.raises(ValueError, match="lineno"):
        _mutant(lineno=bad_lineno)


def test_mutant_rejects_an_operator_outside_the_closed_catalogue():
    with pytest.raises(ValueError, match="operator"):
        _mutant(operator="arithmetic-swap")


@pytest.mark.parametrize("bad_description", ["", None, 42])
def test_mutant_rejects_an_empty_or_non_string_description(bad_description):
    with pytest.raises(ValueError, match="description"):
        _mutant(description=bad_description)


@pytest.mark.parametrize("bad_text", ["", None, 42])
def test_mutant_rejects_an_empty_or_non_string_mutated_text(bad_text):
    with pytest.raises(ValueError, match="mutated_text"):
        _mutant(mutated_text=bad_text)


# --- identity: derived, stable, and disambiguates same-triple sites ---------


def test_identity_is_stable_across_repeated_access():
    mutant = _mutant()
    assert mutant.identity == mutant.identity


def test_identity_is_equal_for_two_mutants_built_from_identical_fields():
    """Value equality, not object identity -- two independently constructed
    Mutants describing the SAME site agree."""
    a = _mutant()
    b = _mutant()
    assert a is not b
    assert a.identity == b.identity


def test_identity_disambiguates_two_sites_sharing_lineno_operator_and_description():
    """A-115's own trap: a 3+-operand boolean chain produces more than one
    site with an IDENTICAL ``(lineno, operator, description)`` triple (e.g.
    two ``"And->Or"`` sites on the same line). ``mutated_text`` differs
    because the splice landed at a different byte offset, so folding it
    into ``identity`` still gives each site its own stable identity without
    a fifth stored field."""
    first = _mutant(
        operator="boolop-swap",
        description="And->Or",
        mutated_text="if a or b and c:\n    pass\n",
    )
    second = _mutant(
        operator="boolop-swap",
        description="And->Or",
        mutated_text="if a and b or c:\n    pass\n",
    )
    assert first.lineno == second.lineno
    assert first.operator == second.operator
    assert first.description == second.description
    assert first.identity != second.identity


# --- byte_offset / line_for_offset ------------------------------------------


def test_byte_offset_of_line_one_column_zero_is_zero():
    assert byte_offset(b"abc\ndef\n", lineno=1, col_offset=0) == 0


def test_byte_offset_advances_past_each_newline():
    text_bytes = b"aa\nbbb\ncc\n"
    # line 2 ("bbb") starts right after the first "\n" at index 2.
    assert byte_offset(text_bytes, lineno=2, col_offset=0) == 3
    # line 3 ("cc") starts right after the second "\n" at index 6.
    assert byte_offset(text_bytes, lineno=3, col_offset=0) == 7


def test_byte_offset_adds_the_column_within_its_own_line():
    text_bytes = b"aa\nbbbb\n"
    assert byte_offset(text_bytes, lineno=2, col_offset=2) == 3 + 2


def test_byte_offset_accounts_for_multibyte_utf8_characters_before_it():
    """`ast.col_offset` is a UTF-8 BYTE offset, not a character offset --
    verified empirically while authoring this package: a multi-byte
    character shifts every later `col_offset` on its own line by its own
    BYTE width, not by 1. `é` is 2 bytes in UTF-8, so the token starting
    right after it is 2 bytes further along than its character count would
    suggest."""
    line = "x = 'é' + 1\n"  # 'é' is 2 UTF-8 bytes
    text_bytes = line.encode("utf-8")
    # `ast` itself reports col_offset=9 for the "+' in this exact line
    # (character index would be 8) -- confirmed directly against `ast.parse`
    # while authoring this package.
    import ast

    tree = ast.parse(line)
    plus_col = tree.body[0].value.col_offset  # the BinOp's own col_offset
    # the BinOp starts at the left operand, i.e. the opening quote
    assert plus_col == 4
    right_operand = tree.body[0].value.right
    assert byte_offset(text_bytes, lineno=1, col_offset=right_operand.col_offset) == (
        line.encode("utf-8").index(b"1")
    )


def test_line_for_offset_is_the_inverse_of_byte_offset():
    text_bytes = b"aa\nbbb\ncccc\n"
    for lineno in (1, 2, 3):
        offset = byte_offset(text_bytes, lineno=lineno, col_offset=0)
        assert line_for_offset(text_bytes, offset) == lineno


def test_line_for_offset_at_offset_zero_is_line_one():
    assert line_for_offset(b"anything\n", 0) == 1
