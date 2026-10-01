"""B113/A-466: a stalled Go scanner cursor is refused, not spun (go.py, go_modfile.py).

Also kills its own target mutants textually under R2 (the anchor stops matching),
so those kills are not evidence that the guards work.
"""

import pytest
from scanner_progress_support import assert_case_is_refused, assert_cases_belong, assert_ordinary_input_terminates

from assay.adapters import go, go_modfile
from assay.errors import AssayError

CASES = [
    ("go-292", "adapters/go.py", "_scan_raw_string", "end == -1", "python:compare-swap", "Eq->NotEq", 0,
     lambda m: m._strip_comments_and_literals("package x\nvar s = `open")),
    ("go-321", "adapters/go.py", "_strip_comments_and_literals", 'two == "//"', "python:compare-swap", "Eq->NotEq", 0,
     lambda m: m._strip_comments_and_literals("package x\nfunc f() {}\n")),
    ("go-323", "adapters/go.py", "_strip_comments_and_literals", "end == -1", "python:compare-swap", "Eq->NotEq", 0,
     lambda m: m._strip_comments_and_literals("package x\n// c")),
    ("go-330", "adapters/go.py", "_strip_comments_and_literals", "close == -1", "python:compare-swap", "Eq->NotEq", 0,
     lambda m: m._strip_comments_and_literals("package x\n/* open")),
    ("gomod-393", "adapters/go_modfile.py", "_tokens", "newline == -1", "python:compare-swap", "Eq->NotEq", 0,
     lambda m: list(m._tokens("module x\n// c", source="go.mod"))),
]


@pytest.mark.parametrize("case", CASES, ids=[case[0] for case in CASES])
def test_a_stalled_scanner_mutant_is_refused_not_spun(case, monkeypatch):
    assert_case_is_refused(case, monkeypatch)


def test_go_cases_name_only_go_component_sources():
    assert_cases_belong(CASES, {"adapters/go.py", "adapters/go_modfile.py"})


def test_a_case_from_another_component_is_refused_by_the_component_check():
    misfiled = CASES + [("js-x", "adapters/javascript.py", "f", "a", "o", "d", 0, None)]
    with pytest.raises(AssertionError, match="outside this component"):
        assert_cases_belong(misfiled, {"adapters/go.py", "adapters/go_modfile.py"})


def test_every_guarded_function_still_terminates_on_ordinary_input():
    assert_ordinary_input_terminates(
        CASES,
        {"adapters/go.py": go, "adapters/go_modfile.py": go_modfile},
        (AssayError,),
    )


def test_the_block_comment_and_raw_string_guards_accept_ordinary_input():
    # Reaches go.py's closed `/* */` and closed raw-string guards with real code.
    assert go._strip_comments_and_literals("package x\n/* c */ var s = `r`\n") == "package x\n        var s =    \n"
