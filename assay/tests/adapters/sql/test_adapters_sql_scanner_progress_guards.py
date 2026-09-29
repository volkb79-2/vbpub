"""B113/A-466: a stalled SQL lexer cursor is refused, not spun (sql_lex.py).

Also kills its own target mutants textually under R2 (the anchor stops matching),
so those kills are not evidence that the guards work.
"""

import pytest
from scanner_progress_support import assert_case_is_refused, assert_cases_belong, assert_ordinary_input_terminates

from assay.adapters import sql_lex
from assay.mutation import MutationDiscoveryError

CASES = [
    ("sql-193-eq", "adapters/sql_lex.py", "_lex_once", "b == _DASH and", "python:compare-swap", "Eq->NotEq", 0,
     lambda m: m._lex_once(b"SELECT 1;\n-- c\nSELECT 2;\n")),
    ("sql-193-or1", "adapters/sql_lex.py", "_lex_once", "b == _DASH and", "python:boolop-swap", "And->Or", 0,
     lambda m: m._lex_once(b"SELECT 1;\n-- c\nSELECT 2;\n")),
    ("sql-193-or2", "adapters/sql_lex.py", "_lex_once", "b == _DASH and", "python:boolop-swap", "And->Or", 1,
     lambda m: m._lex_once(b"SELECT 1;\n-- c\nSELECT 2;\n")),
    ("sql-195", "adapters/sql_lex.py", "_lex_once", "end == -1", "python:compare-swap", "Eq->NotEq", 0,
     lambda m: m._lex_once(b"SELECT 1; -- trailing, no newline")),
    ("sql-270", "adapters/sql_lex.py", "_lex_once", "tag_end is not None", "python:compare-swap", "IsNot->Is", 0,
     lambda m: m._lex_once(b"-- $1\nSELECT $1")),
    ("sql-273", "adapters/sql_lex.py", "_lex_once", "close == -1", "python:compare-swap", "Eq->NotEq", 0,
     lambda m: m._lex_once(b"SELECT 1; $$ open")),
]


@pytest.mark.parametrize("case", CASES, ids=[case[0] for case in CASES])
def test_a_stalled_scanner_mutant_is_refused_not_spun(case, monkeypatch):
    assert_case_is_refused(case, monkeypatch)


def test_sql_cases_name_only_the_sql_lexer_source():
    assert_cases_belong(CASES, {"adapters/sql_lex.py"})


def test_every_guarded_function_still_terminates_on_ordinary_input():
    assert_ordinary_input_terminates(CASES, {"adapters/sql_lex.py": sql_lex}, (MutationDiscoveryError,))
