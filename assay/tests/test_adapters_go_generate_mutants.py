"""O2 (Go half) — ``GoAdapter.generate_mutants`` is unconditionally
``"UNSUPPORTED"`` (A-042/A-114): no Go toolchain exists anywhere in this
devcontainer to prove a generated mutant would even be valid Go syntax, so
this adapter never attempts a text-guessed one.
"""

from __future__ import annotations

from conftest import PROJECT_ROOT

from assay.adapters.go import GoAdapter

FIXTURE_PATH = PROJECT_ROOT / "tests" / "fixtures" / "mutation" / "go" / "sample.go"
SAMPLE_TEXT = FIXTURE_PATH.read_text(encoding="utf-8")

ADAPTER = GoAdapter()


def test_generate_mutants_is_unsupported_for_real_go_source_with_real_targets():
    """The realistic case: a real, literal, committed Go fixture (parallel
    to Python's ``sample.py``) whose line 12 (``if a < b {``) LOOKS like an
    obvious compare-swap candidate to a human reader -- proving the
    adapter's own catalogue is never consulted for Go at all, not merely
    that this particular text happens to produce nothing."""
    result = ADAPTER.generate_mutants(SAMPLE_TEXT, {12})
    assert result == "UNSUPPORTED"


def test_generate_mutants_is_unsupported_regardless_of_the_lines_argument():
    assert ADAPTER.generate_mutants(SAMPLE_TEXT, set()) == "UNSUPPORTED"
    assert ADAPTER.generate_mutants(
        SAMPLE_TEXT, set(range(1, len(SAMPLE_TEXT.splitlines()) + 1))
    ) == "UNSUPPORTED"


def test_generate_mutants_is_unsupported_regardless_of_text_content():
    """Never a text-guessed mutant (O2's own negative): garbage input,
    empty input, and even syntactically INVALID Go all produce the same
    unconditional sentinel -- this method never inspects *text* at all."""
    assert ADAPTER.generate_mutants("", {1}) == "UNSUPPORTED"
    assert ADAPTER.generate_mutants("not even go source {{{", {1}) == "UNSUPPORTED"
    assert ADAPTER.generate_mutants("package main\n", {1, 2, 3}) == "UNSUPPORTED"
