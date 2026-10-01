"""B129 / W10 step 1: characterization of the inline strict-int guards in the
istanbul and coverage.py JSON coverage parsers.

Pins accept and refuse, with the exact message and reason code. Run against
the UNCHANGED source; never edited to follow the refactor.
"""

from __future__ import annotations

import json

import pytest
from conftest import TESTS_ROOT

from assay.coverage_parsers import coverage_istanbul_json as istanbul
from assay.coverage_parsers import coverage_py_json as coverage_py
from assay.errors import AssayError, Outcome, ReasonCode

NOT_STRICT_INT = [True, False, 1.5, "1", None, b"1", [1]]
ISTANBUL = "istanbul coverage JSON: "
COVERAGE_PY = "coverage.py JSON: "


def refusal(call, message: str) -> None:
    with pytest.raises(AssayError) as caught:
        call()
    assert str(caught.value) == message
    assert caught.value.outcome is Outcome.ERROR
    assert caught.value.reason_code is ReasonCode.UNREADABLE_ARTIFACT


# ---- istanbul: plain strict int counts ------------------------------------------


@pytest.mark.parametrize("count", [0, 1, 5])
def test_istanbul_arm_and_statement_counts_accept_zero_and_up(count):
    assert istanbul._arm_count("p", "b", 0, count) == count
    assert istanbul._statement_count("p", "3", count) == count


def test_istanbul_arm_and_statement_counts_refuse_a_negative_int_with_their_own_message():
    refusal(
        lambda: istanbul._arm_count("p", "b", 0, -1),
        f"{ISTANBUL}record for 'p': b['b'][0] is -1, a negative execution count",
    )
    refusal(
        lambda: istanbul._statement_count("p", "3", -1),
        f"{ISTANBUL}record for 'p': s['3'] is -1, a negative execution count",
    )


@pytest.mark.parametrize("count", [True, False, 1.5, "1", None, b"1"])
def test_istanbul_arm_count_refuses_a_bool_or_non_int(count):
    refusal(
        lambda: istanbul._arm_count("p", "b", 2, count),
        f"{ISTANBUL}record for 'p': b['b'][2] is {type(count).__name__} "
        f"({count!r}), expected int",
    )


@pytest.mark.parametrize("count", [True, False, 1.5, "1", None, b"1"])
def test_istanbul_statement_count_refuses_a_bool_or_non_int(count):
    refusal(
        lambda: istanbul._statement_count("p", "3", count),
        f"{ISTANBUL}record for 'p': s['3'] is {type(count).__name__} "
        f"({count!r}), expected int",
    )


# ---- istanbul: position line (strict int, then positive) -----------------------


def _position(line):
    return {"start": {"line": line, "column": 0}}


def test_istanbul_position_line_accepts_line_one():
    assert istanbul._position_line("p", "statement '3'", _position(1), "start") == 1


@pytest.mark.parametrize("line", [True, False, 1.5, "1", None, b"1"])
def test_istanbul_position_line_refuses_a_bool_or_non_int(line):
    refusal(
        lambda: istanbul._position_line("p", "statement '3'", _position(line), "start"),
        f"{ISTANBUL}record for 'p': statement '3' has start.line = {line!r}, "
        f"expected an integer",
    )


@pytest.mark.parametrize("line", [0, -1])
def test_istanbul_position_line_refuses_a_non_positive_line(line):
    refusal(
        lambda: istanbul._position_line("p", "statement '3'", _position(line), "start"),
        f"{ISTANBUL}record for 'p': statement '3' has start.line = {line}, "
        f"which is not a positive line number",
    )


# ---- istanbul: start column (int at least 0) -----------------------------------


def _start(column):
    return {"start": {"line": 1, "column": column}}


@pytest.mark.parametrize("column", [0, 1, 72])
def test_istanbul_start_column_accepts_zero_and_up(column):
    assert istanbul._start_position("p", "node", _start(column)) == (1, column)


@pytest.mark.parametrize("column", NOT_STRICT_INT[:6] + [-1])
def test_istanbul_start_column_refuses_a_bool_non_int_or_negative(column):
    refusal(
        lambda: istanbul._start_position("p", "node", _start(column)),
        f"{ISTANBUL}record for 'p': node has start.column = {column!r}, "
        f"expected a nonnegative integer",
    )


# ---- istanbul: branch entry line (int at least 1) ------------------------------


def test_istanbul_entry_line_accepts_line_one_and_falls_back_to_loc_when_absent():
    assert istanbul._entry_line("p", "b", {"line": 1}) == 1
    assert istanbul._entry_line("p", "b", {"loc": _position(7)}) == 7


@pytest.mark.parametrize("line", NOT_STRICT_INT[:2] + [1.5, "1", b"1", 0, -1])
def test_istanbul_entry_line_refuses_a_bool_non_int_or_non_positive_line(line):
    refusal(
        lambda: istanbul._entry_line("p", "b", {"line": line}),
        f"{ISTANBUL}record for 'p': branch 'b' has line = {line!r}, "
        f"expected a positive integer",
    )


# ---- istanbul: default-arg function call count (int at least 0) ----------------

FIXTURE = TESTS_ROOT / "fixtures/coverage/coverage-istanbul-json.default-arg-signature.json"
KEY = "/fixture/src/ChartCard.tsx"


def _with_calls(calls):
    record = json.loads(FIXTURE.read_text(encoding="utf-8"))[KEY]
    record["f"]["0"] = calls
    return lambda: istanbul.parse(json.dumps({KEY: record}), producer="istanbul")


@pytest.mark.parametrize("calls", [0, 1, 9])
def test_istanbul_function_call_count_accepts_zero_and_up(calls):
    assert _with_calls(calls)().files[KEY].executed == (
        frozenset({34}) if calls else frozenset()
    )


@pytest.mark.parametrize("calls", [True, False, 1.5, "9", None, [], -1])
def test_istanbul_function_call_count_refuses_a_bool_non_int_or_negative(calls):
    refusal(
        _with_calls(calls),
        f"{ISTANBUL}record for {KEY!r}: f['0'] is {calls!r}, expected a "
        f"nonnegative integer function call count",
    )


# ---- coverage.py: branch arcs and int lists ------------------------------------


@pytest.mark.parametrize("arc", [[1, 2], [0, -1], [5, 5]])
def test_coverage_py_branch_pairs_accept_a_two_int_arc(arc):
    assert coverage_py._branch_pairs({"k": [arc]}, "p", "k") == [tuple(arc)]


def test_coverage_py_branch_pairs_accept_an_absent_key_as_empty():
    assert coverage_py._branch_pairs({}, "p", "k") == []


@pytest.mark.parametrize(
    "arc",
    [
        [1, True],
        [True, 1],
        [1, False],
        [False, 1],
        [1.5, 2],
        [1, 2.5],
        ["1", 2],
        [1, "2"],
        [1, None],
        [None, 1],
        [1],
        [1, 2, 3],
        [],
        "ab",
        None,
        5,
        (1, 2),
    ],
)
def test_coverage_py_branch_pairs_refuse_a_malformed_arc(arc):
    refusal(
        lambda: coverage_py._branch_pairs({"k": [arc]}, "p", "k"),
        f"{COVERAGE_PY}record for 'p': 'k' contains a malformed arc "
        f"{arc!r}, expected a 2-element [src, dst] list of integers",
    )


@pytest.mark.parametrize("value", [[], [0], [-1, 5, 7]])
def test_coverage_py_int_list_accepts_a_list_of_strict_ints(value):
    assert coverage_py._int_list({"k": value}, "p", "k") == value


@pytest.mark.parametrize("item", [True, False, 1.5, "1", None, b"1", [1]])
def test_coverage_py_int_list_refuses_a_bool_or_non_int_item(item):
    refusal(
        lambda: coverage_py._int_list({"k": [1, item]}, "p", "k"),
        f"{COVERAGE_PY}record for 'p': 'k' contains {type(item).__name__} "
        f"({item!r}), expected int",
    )
