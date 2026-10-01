"""B129 / W10 step 1: characterization of the inline strict-int guards in
``adapters.go_stmtpos`` and ``adapters.base.StatementSpan``.

Pins accept and refuse, with the exact message. Run against the UNCHANGED
source; never edited to follow the refactor.
"""

from __future__ import annotations

import pytest

from assay.adapters import go_stmtpos
from assay.adapters.base import StatementSpan
from assay.errors import AssayError, Outcome, ReasonCode

NOT_STRICT_INT = [True, False, 1.5, "1", None, b"1", [1]]


def refusal(call, message: str, exc=AssayError) -> None:
    with pytest.raises(exc) as caught:
        call()
    assert str(caught.value) == message
    if exc is AssayError:
        assert caught.value.outcome is Outcome.ERROR
        assert caught.value.reason_code is ReasonCode.UNREADABLE_ARTIFACT


# ---- go_stmtpos._stmt_line and _int -------------------------------------------


@pytest.mark.parametrize("value", [0, 1, 7, -1])
def test_stmt_line_accepts_any_strict_int(value):
    assert go_stmtpos._stmt_line(value, "a.go") == value


@pytest.mark.parametrize("value", NOT_STRICT_INT)
def test_stmt_line_refuses_a_bool_or_non_int(value):
    refusal(
        lambda: go_stmtpos._stmt_line(value, "a.go"),
        f"'a.go': a `stmt_lines` entry is {value!r}, not an integer",
    )


@pytest.mark.parametrize("value", [0, 1, 7, -1])
def test_block_field_accepts_any_strict_int(value):
    assert go_stmtpos._int({"start_line": value}, "start_line", "a.go") == value


@pytest.mark.parametrize("value", NOT_STRICT_INT)
def test_block_field_refuses_a_bool_or_non_int(value):
    refusal(
        lambda: go_stmtpos._int({"start_line": value}, "start_line", "a.go"),
        f"'a.go': block field 'start_line' is {value!r}, not an integer",
    )


def test_block_field_refuses_an_absent_field_as_none():
    refusal(
        lambda: go_stmtpos._int({}, "start_line", "a.go"),
        "'a.go': block field 'start_line' is None, not an integer",
    )


# ---- StatementSpan: strict int, then at least 1 --------------------------------


def test_statement_span_accepts_line_one():
    span = StatementSpan(start_line=1, end_line=1)
    assert (span.start_line, span.end_line) == (1, 1)


@pytest.mark.parametrize("name", ["start_line", "end_line"])
@pytest.mark.parametrize("value", NOT_STRICT_INT)
def test_statement_span_refuses_a_bool_or_non_int(name, value):
    values = {"start_line": 1, "end_line": 2}
    values[name] = value
    refusal(
        lambda: StatementSpan(**values),
        f"StatementSpan.{name} must be an integer, got {value!r}",
        exc=ValueError,
    )


@pytest.mark.parametrize("name", ["start_line", "end_line"])
@pytest.mark.parametrize("value", [0, -1])
def test_statement_span_refuses_a_line_below_one(name, value):
    values = {"start_line": 1, "end_line": 2}
    values[name] = value
    refusal(
        lambda: StatementSpan(**values),
        f"StatementSpan.{name} must be >= 1, got {value}",
        exc=ValueError,
    )
