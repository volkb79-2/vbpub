"""B113/A-466: a stalled JavaScript scanner cursor is refused, not spun (javascript.py).

Also kills its own target mutants textually under R2 (the anchor stops matching),
so those kills are not evidence that the guards work.
"""

import pytest
from scanner_progress_support import assert_case_is_refused, assert_cases_belong, assert_ordinary_input_terminates

from assay.adapters import javascript
from assay.errors import AssayError

CASES = [
    ("js-243", "adapters/javascript.py", "_strip_comments", 'two == "//"', "python:compare-swap", "Eq->NotEq", 0,
     lambda m: m._strip_comments("a\nb\n")),
    ("js-245", "adapters/javascript.py", "_strip_comments", "end == -1", "python:compare-swap", "Eq->NotEq", 0,
     lambda m: m._strip_comments("a // c")),
    ("js-249", "adapters/javascript.py", "_strip_comments", "close == -1", "python:compare-swap", "Eq->NotEq", 0,
     lambda m: m._strip_comments("ab /* open")),
]


@pytest.mark.parametrize("case", CASES, ids=[case[0] for case in CASES])
def test_a_stalled_scanner_mutant_is_refused_not_spun(case, monkeypatch):
    assert_case_is_refused(case, monkeypatch)


def test_javascript_cases_name_only_the_javascript_source():
    assert_cases_belong(CASES, {"adapters/javascript.py"})


def test_every_guarded_function_still_terminates_on_ordinary_input():
    assert_ordinary_input_terminates(CASES, {"adapters/javascript.py": javascript}, (AssayError,))
