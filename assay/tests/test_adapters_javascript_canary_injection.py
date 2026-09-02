"""B036 — the two canary injection mechanisms (A-345). Both are PURE
``(text) -> (text, description)`` transforms that never touch a filesystem
(A-010), and both are plain trailing appends
(:mod:`assay.adapters.go`'s own shape) rather than Python's
insert-after-the-prologue shape.

Why appending is faithful here even though JS/TS *does* have an executable
module top level: an ES module's ``import`` declarations are HOISTED and its
imported modules evaluated first, so a ``throw`` at the very end of the file
still fires during module evaluation -- before any test can touch a single
export. That satisfies ``inject_import_break``'s own contract ("reliably
tripped by merely importing/loading the module") with none of Python's
docstring/``__future__`` insertion-point machinery.

Negative (both): a transform that reformats the file, or that is not valid in
one of the four extensions this adapter claims, breaks the "minimal, valid,
additive" contract the protocol states. These methods receive only *text* and
never a path, so a type annotation -- legal in ``.ts``, a syntax error in
``.js`` -- would be a real defect that only a ``.js`` consumer would ever
hit. Negative (uncovered-line): a canary function that is not exported is
what ``noUnusedLocals`` flags, breaking the "lint-clean" half of the
contract.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from assay.adapters.javascript import JavaScriptAdapter

ADAPTER = JavaScriptAdapter()

MODULE = """\
import { useState } from 'react'

export function counter(start: number): number {
  const [value] = useState(start)
  return value
}
"""


@pytest.mark.parametrize(
    "inject",
    [ADAPTER.inject_import_break, ADAPTER.inject_uncovered_line],
)
def test_the_original_text_survives_byte_for_byte_as_a_prefix(inject):
    """MINIMAL and ADDITIVE, proven as a byte fact rather than by eye: every
    byte of the original is still there, in order, at the front."""
    transformed, _description = inject(MODULE)

    assert transformed.startswith(MODULE)
    assert len(transformed) > len(MODULE)


@pytest.mark.parametrize(
    "inject",
    [ADAPTER.inject_import_break, ADAPTER.inject_uncovered_line],
)
def test_a_file_with_no_trailing_newline_gets_a_clean_append_boundary(inject):
    """The one shape where "append" could fuse the injected snippet onto the
    file's own last line and change it."""
    no_newline = "export const answer = 42"
    transformed, _description = inject(no_newline)

    assert transformed.startswith(no_newline + "\n")


@pytest.mark.parametrize(
    "inject",
    [ADAPTER.inject_import_break, ADAPTER.inject_uncovered_line],
)
def test_injection_is_pure_and_total_even_for_degenerate_input(inject):
    """Pure ``(text) -> (text, description)``: no filesystem, no raise, and a
    real answer for an empty file -- the same totality
    :meth:`assay.adapters.python.PythonAdapter.inject_import_break` promises
    for source that does not even parse."""
    transformed, description = inject("")

    assert isinstance(transformed, str) and transformed
    assert isinstance(description, str) and description


def test_import_break_appends_a_top_level_throw():
    transformed, description = ADAPTER.inject_import_break(MODULE)

    assert 'throw new Error("assay-canary-import-break")' in transformed
    assert "throw" in description
    # The throw sits at the module's own top level (column zero), not nested
    # inside the function above it -- a nested throw would only fire if that
    # function were called, which is a different claim entirely.
    added = transformed[len(MODULE) :]
    assert added.strip().startswith("throw ")
    assert "\n  throw" not in added


def test_uncovered_line_appends_a_never_called_exported_function():
    transformed, description = ADAPTER.inject_uncovered_line(MODULE)
    added = transformed[len(MODULE) :]

    assert "export function _assayCanaryUnreached(value = 0) {" in added
    assert "_assayCanaryUnreached" in description
    assert "2 uncovered lines" in description
    # Its own two BODY lines are what no test can execute; the declaration
    # line itself is reached merely by the module loading.
    assert "const doubled = value * 2" in added
    assert "return doubled" in added


def test_neither_snippet_uses_syntax_that_is_typescript_only():
    """These methods receive text, never a path, so the SAME snippet is
    appended to a ``.js`` file. A type annotation would be a syntax error
    there. The canary parameter therefore carries a DEFAULT (``value = 0``),
    which is valid JavaScript and lets TypeScript infer ``number`` so
    ``noImplicitAny`` has nothing to complain about either."""
    for inject in (ADAPTER.inject_import_break, ADAPTER.inject_uncovered_line):
        added = inject(MODULE)[0][len(MODULE) :]
        assert ": number" not in added
        assert ": string" not in added
        assert "value:" not in added


def test_the_canary_function_is_exported_so_no_unused_local_rule_fires():
    """The "lint-clean" half of ``inject_uncovered_line``'s contract:
    TypeScript's ``noUnusedLocals`` flags an unreferenced module-level
    declaration, which a canary is by construction. Exporting it removes the
    finding without making the body reachable."""
    added = ADAPTER.inject_uncovered_line(MODULE)[0][len(MODULE) :]

    assert added.lstrip().startswith("export function ")


def test_the_two_transforms_are_different_and_independent():
    """They isolate two different axes (R0-level rejection vs. an R1
    changed-line-coverage floor), so neither may be the other."""
    broken, _ = ADAPTER.inject_import_break(MODULE)
    uncovered, _ = ADAPTER.inject_uncovered_line(MODULE)

    assert broken != uncovered
    assert "throw" not in uncovered[len(MODULE) :]
    assert "_assayCanaryUnreached" not in broken


# --- the R1 half, proven by a REAL committed coverage artifact ---------------
#
# Round-1 review, Minor: A-345's verification was a report transcript, while
# the rest of this change committed real artifacts as evidence. It now
# commits one. `tests/fixtures/canary/javascript/` holds the exact text
# `inject_uncovered_line` produces for the probe project's own `roles.ts`,
# plus the `coverage-final.json` a real `vitest run --coverage` produced from
# that injected file, under BOTH providers.

CANARY_FIXTURES = (
    Path(__file__).resolve().parent / "fixtures" / "canary" / "javascript"
)
INJECTED = CANARY_FIXTURES / "roles.uncovered-line-injected.ts"
PROBE_SOURCE = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "coverage"
    / "probe-js"
    / "src"
    / "roles.ts"
)
CANARY_ARTIFACTS = (
    "coverage-istanbul-json.uncovered-line.vitest-istanbul.json",
    "coverage-istanbul-json.uncovered-line.vitest-v8.json",
)


def test_the_committed_injected_file_is_byte_for_byte_what_this_adapter_produces():
    """The join between the two halves of the evidence. Without this, the
    committed artifact would prove something about a file nobody could show
    was the adapter's own output; with it, a change to the snippet fails here
    FIRST and says the artifact needs regenerating, rather than letting the
    artifact quietly stop describing what ships."""
    produced, _description = ADAPTER.inject_uncovered_line(
        PROBE_SOURCE.read_text(encoding="utf-8")
    )

    assert produced == INJECTED.read_text(encoding="utf-8")


@pytest.mark.parametrize("artifact", CANARY_ARTIFACTS)
def test_a_real_coverage_run_reports_the_canary_body_as_uncovered(artifact: str):
    """A-345's R1 claim, as committed evidence instead of a transcript: the
    injected function's DECLARATION line is reached merely by the module
    loading, and its two BODY lines are executed by no test -- so a gate
    enforcing a changed-line-coverage floor rejects the transform while a
    tests-only gate sails past it. True under both providers (the defect
    A-346 rules on does not touch this shape: there is no conditional
    expression anywhere in the appended function)."""
    from assay.coverage import load_coverage_profile

    profile = load_coverage_profile(
        (CANARY_FIXTURES / artifact).read_text(encoding="utf-8"),
        declared_format="coverage-istanbul-json",
    )
    (record,) = [
        value for key, value in profile.files.items() if key.endswith("/roles.ts")
    ]
    injected_lines = INJECTED.read_text(encoding="utf-8").splitlines()

    # The two body lines of the appended function, located by content rather
    # than by a hardcoded number, so the assertion survives a snippet edit
    # that the byte-equality test above would have already caught.
    body = [
        number
        for number, text in enumerate(injected_lines, start=1)
        if text.strip() in ("const doubled = value * 2 // assay-canary: executed by no test",
                            "return doubled")
    ]

    assert len(body) == 2
    assert set(body) <= record.missing
    assert not (set(body) & record.executed)


def test_the_suite_that_produced_the_canary_artifacts_still_passed():
    """The other half of the R1 canary's contract, and the reason it isolates
    an AXIS rather than just breaking things: the injected function is valid,
    lint-clean and test-neutral, so the project's own tests still pass. Both
    committed artifacts exist at all only because the run they came from
    completed -- a suite that had failed would have produced no coverage
    document to commit, exactly as an import-break injection does (which is
    why THAT half has no artifact here and cannot have one)."""
    for artifact in CANARY_ARTIFACTS:
        assert (CANARY_FIXTURES / artifact).is_file()

