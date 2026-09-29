"""O1/A-107 -- ``GoAdapter.inject_import_break``/``inject_uncovered_line``
(P09, A-010/A-105): structurally-valid, best-effort implementations
satisfying the frozen protocol's shape.

Go has no executable top-level statement (unlike Python's module body), so
BOTH mechanisms here are pure, trailing appends -- see ``adapters/go.py``'s
own module docstring for the full reasoning, and this package's LOG for the
empirical confirmation that import-break's own real-world consequence (a
package-init panic) prevents ``go test -coverprofile`` from ever writing a
coverprofile, which is why ``inject_import_break`` is never exercised by
this package's own R1-only Go canary (A-107) -- these tests instead prove
the MECHANISM directly, matching the Python adapter's own test module.
"""

from __future__ import annotations

from assay.adapters.go import GoAdapter

ADAPTER = GoAdapter()


# --- inject_import_break -------------------------------------------------


def test_import_break_appends_a_panicking_init_function():
    source = "package greet\n"

    new_text, description = ADAPTER.inject_import_break(source)

    assert new_text == (
        "package greet\n"
        "\n\nfunc init() {\n"
        '\tpanic("assay-canary-import-break")\n'
        "}\n"
    )
    assert "init()" in description
    assert "panic" in description


def test_import_break_guarantees_a_trailing_newline_before_appending():
    source = "package greet"  # deliberately no trailing newline

    new_text, _ = ADAPTER.inject_import_break(source)

    assert new_text.startswith("package greet\n\n\nfunc init()")


def test_import_break_is_returned_as_a_pair_and_never_touches_a_filesystem(tmp_path):
    ADAPTER.inject_import_break("package greet\n")

    assert list(tmp_path.iterdir()) == []


def test_import_break_appended_init_is_recognised_as_executable_code():
    """The mechanism's own claim, checked against the adapter's OWN,
    already-proven `has_executable_code`: the appended `init()` really is a
    top-level function declaration with a real body."""
    source = "package greet\n"
    new_text, _ = ADAPTER.inject_import_break(source)

    assert ADAPTER.has_executable_code("greet/greet.go", new_text) is True


# --- inject_uncovered_line -------------------------------------------------


def test_uncovered_line_appends_a_never_called_function():
    source = "package greet\n"

    new_text, description = ADAPTER.inject_uncovered_line(source)

    assert new_text == (
        "package greet\n"
        "\n\nfunc _assayCanaryUnreached(value int) int {\n"
        "\tdoubled := value * 2 // assay-canary: executed by no test\n"
        "\treturn doubled\n"
        "}\n"
    )
    assert "_assayCanaryUnreached" in description
    assert "2 uncovered lines" in description


def test_uncovered_line_guarantees_a_trailing_newline_before_appending():
    source = "package greet"

    new_text, _ = ADAPTER.inject_uncovered_line(source)

    assert new_text.startswith("package greet\n\n\nfunc _assayCanaryUnreached")


def test_uncovered_line_is_returned_as_a_pair_and_never_touches_a_filesystem(tmp_path):
    ADAPTER.inject_uncovered_line("package greet\n")

    assert list(tmp_path.iterdir()) == []


def test_uncovered_line_appended_function_is_recognised_as_executable_code():
    source = "package greet\n"
    new_text, _ = ADAPTER.inject_uncovered_line(source)

    assert ADAPTER.has_executable_code("greet/greet.go", new_text) is True


def test_uncovered_line_on_an_empty_file():
    """A genuinely empty file is a legal (if unusual) input -- pure and
    total, never raises."""
    new_text, description = ADAPTER.inject_uncovered_line("")

    assert new_text == (
        "\n\nfunc _assayCanaryUnreached(value int) int {\n"
        "\tdoubled := value * 2 // assay-canary: executed by no test\n"
        "\treturn doubled\n"
        "}\n"
    )
    assert description


def test_both_mechanisms_produce_distinct_appends():
    """Not a hollow tautology check: proves the two mechanisms are actually
    DIFFERENT transforms of the same input, matching their distinct
    real-world consequences (a panic vs. an uncovered function)."""
    source = "package greet\n"

    import_break_text, _ = ADAPTER.inject_import_break(source)
    uncovered_text, _ = ADAPTER.inject_uncovered_line(source)

    assert import_break_text != uncovered_text
    assert "panic" in import_break_text
    assert "panic" not in uncovered_text
