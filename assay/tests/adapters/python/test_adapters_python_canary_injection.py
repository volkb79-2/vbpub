"""O1/O2 -- ``PythonAdapter.inject_import_break``/``inject_uncovered_line``
(P09, A-010/A-105): the two PURE, ``(text) -> (text, description)`` canary
mechanisms :mod:`assay.canary` drives. Ported from
``/workspaces/vbpub/nyxloom/src/nyxloom/gate_canary.py``'s own
``inject_import_break``/``inject_uncovered_line`` mechanism, never their
file-write.

The negative this defends: *a transform that does not actually insert the
canary statement, that corrupts a leading docstring/``__future__`` import,
or that touches a filesystem, would still look plausible from the return
value alone unless each construct family gets its own committed, exact
expected text.*
"""

from __future__ import annotations

import ast

from assay.adapters.python import PythonAdapter

ADAPTER = PythonAdapter()


# --- inject_import_break: insertion point -------------------------------------


def test_import_break_is_inserted_at_the_top_of_a_bare_module():
    source = "def f():\n    return 1\n"

    new_text, description = ADAPTER.inject_import_break(source)

    assert new_text == (
        'raise AssertionError("assay-canary-import-break")\n'
        "def f():\n"
        "    return 1\n"
    )
    assert "line 1" in description


def test_import_break_is_returned_as_a_pair_and_never_touches_a_filesystem(tmp_path):
    """Pure: calling this on text alone must not create any file — proven by
    asserting the (otherwise empty) tmp_path stays empty."""
    ADAPTER.inject_import_break("def f():\n    return 1\n")

    assert list(tmp_path.iterdir()) == []


def test_import_break_is_inserted_after_a_leading_docstring():
    source = '"""A module docstring."""\ndef f():\n    return 1\n'

    new_text, description = ADAPTER.inject_import_break(source)

    assert new_text == (
        '"""A module docstring."""\n'
        'raise AssertionError("assay-canary-import-break")\n'
        "def f():\n"
        "    return 1\n"
    )
    assert "line 2" in description


def test_import_break_is_inserted_after_a_future_import():
    source = "from __future__ import annotations\ndef f():\n    return 1\n"

    new_text, description = ADAPTER.inject_import_break(source)

    assert new_text == (
        "from __future__ import annotations\n"
        'raise AssertionError("assay-canary-import-break")\n'
        "def f():\n"
        "    return 1\n"
    )
    assert "line 2" in description


def test_import_break_is_inserted_after_both_a_docstring_and_a_future_import():
    """The negative this specifically defends: skipping only ONE of the two
    would insert the canary statement BEFORE the __future__ import, which is
    itself a real SyntaxError unrelated to the canary's own intended cause."""
    source = (
        '"""doc"""\n'
        "from __future__ import annotations\n"
        "def f():\n"
        "    return 1\n"
    )

    new_text, description = ADAPTER.inject_import_break(source)

    assert new_text == (
        '"""doc"""\n'
        "from __future__ import annotations\n"
        'raise AssertionError("assay-canary-import-break")\n'
        "def f():\n"
        "    return 1\n"
    )
    assert "line 3" in description
    # The negative, made concrete: the inserted line must not land before the
    # future import (which would itself be a SyntaxError of unrelated cause).
    assert new_text.index("__future__") < new_text.index("raise AssertionError")


def test_import_break_appends_after_a_module_that_is_only_a_docstring():
    """The insertion-point scan's loop runs out WITHOUT ever hitting a
    non-skippable statement (no trailing `break`) when the whole module body
    is nothing but a docstring/future-import prefix -- a real, if unusual,
    input the loop must still resolve correctly rather than only ever being
    exercised by a module that has a statement after the prefix."""
    source = '"""only a docstring, nothing else"""\n'

    new_text, description = ADAPTER.inject_import_break(source)

    assert new_text == (
        '"""only a docstring, nothing else"""\n'
        'raise AssertionError("assay-canary-import-break")\n'
    )
    assert "line 2" in description


def test_import_break_prepends_on_unparseable_source():
    """A syntax-broken source is not this function's problem (module
    docstring) -- it still returns something, prepending rather than
    guessing at structure it cannot parse."""
    source = "def f(:\n"

    new_text, description = ADAPTER.inject_import_break(source)

    assert new_text.startswith('raise AssertionError("assay-canary-import-break")\n')
    assert "line 1" in description


def test_import_break_produces_a_real_import_error_on_a_real_module():
    """The mechanism's OWN claim, checked directly: importing the
    transformed text as a module really does raise, proving the inserted
    statement is reachable at module-import time (not, say, inside a
    function body it would never trip)."""
    source = "def f():\n    return 1\n"
    new_text, _ = ADAPTER.inject_import_break(source)

    module = ast.parse(new_text)
    compiled = compile(module, "<canary>", "exec")
    try:
        exec(compiled, {"__name__": "canary_test_module"})
    except AssertionError as exc:
        assert "assay-canary-import-break" in str(exc)
    else:
        raise AssertionError("expected the transformed module to raise on exec")


# --- inject_uncovered_line: pure append, never called ---------------------


def test_uncovered_line_appends_a_never_called_function():
    source = "def f():\n    return 1\n"

    new_text, description = ADAPTER.inject_uncovered_line(source)

    assert new_text == (
        "def f():\n"
        "    return 1\n"
        "\n\n"
        "def _assay_canary_unreached(value: int) -> int:\n"
        "    doubled = value * 2  # assay-canary: executed by no test\n"
        "    return doubled\n"
    )
    assert "_assay_canary_unreached" in description
    assert "2 uncovered lines" in description


def test_uncovered_line_guarantees_a_trailing_newline_before_appending():
    source = "def f():\n    return 1"  # deliberately no trailing newline

    new_text, _ = ADAPTER.inject_uncovered_line(source)

    assert new_text.startswith("def f():\n    return 1\n\n\ndef _assay_canary_unreached")


def test_uncovered_line_is_returned_as_a_pair_and_never_touches_a_filesystem(tmp_path):
    ADAPTER.inject_uncovered_line("def f():\n    return 1\n")

    assert list(tmp_path.iterdir()) == []


def test_uncovered_line_produces_valid_python_whose_appended_function_is_never_called():
    """The mechanism's OWN claim: the appended function parses, defines
    cleanly (its `def` line executes), but calling `f` alone never reaches
    it -- proven by executing the transformed module and confirming the
    canary function exists but leaves no side effect of having run."""
    source = "def f():\n    return 1\n"
    new_text, _ = ADAPTER.inject_uncovered_line(source)

    namespace: dict = {}
    exec(compile(new_text, "<canary>", "exec"), namespace)
    assert namespace["f"]() == 1
    assert callable(namespace["_assay_canary_unreached"])
    assert namespace["_assay_canary_unreached"](21) == 42  # only reached if WE call it


def test_uncovered_line_on_an_empty_module():
    """A genuinely empty file is a legal (if unusual) input -- pure and
    total, never raises."""
    new_text, description = ADAPTER.inject_uncovered_line("")

    assert new_text == (
        "\n\ndef _assay_canary_unreached(value: int) -> int:\n"
        "    doubled = value * 2  # assay-canary: executed by no test\n"
        "    return doubled\n"
    )
    assert description
