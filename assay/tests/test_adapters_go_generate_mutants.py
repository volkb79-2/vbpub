"""O2 (Go half) — ``GoAdapter.generate_mutation_sites`` is unconditionally
``"UNSUPPORTED"`` (A-042/A-114, migrated to the bounded seam by P21/A-183):
no Go toolchain exists anywhere in this devcontainer to prove a generated
mutant would even be valid Go syntax, so this adapter never attempts a
text-guessed one.

P21 keeps the BEHAVIOUR and changes only the signature and the terminal the
marker renders as. That distinction is the point of A-183: the marker is now
payload-free ``INCONCLUSIVE/MUTATION_UNSUPPORTED`` rather than being folded
into ``NO_MUTANTS``, so "assay cannot analyse Go" stays distinguishable from
"assay analysed Go and found nothing mutable". P29 replaces this return with
real bounded sites.
"""

from __future__ import annotations

from conftest import PROJECT_ROOT

from assay.adapters.go import GoAdapter

FIXTURE_PATH = PROJECT_ROOT / "tests" / "fixtures" / "mutation" / "go" / "sample.go"
SAMPLE_TEXT = FIXTURE_PATH.read_text(encoding="utf-8")

ADAPTER = GoAdapter()
OPERATORS = ("compare-swap", "boolop-swap", "bool-const-flip", "falsy-swap")


def test_discovery_is_unsupported_for_real_go_source_with_real_targets():
    """The realistic case: a real, literal, committed Go fixture (parallel
    to Python's ``sample.py``) whose line 12 (``if a < b {``) LOOKS like an
    obvious compare-swap candidate to a human reader -- proving the
    adapter's own catalogue is never consulted for Go at all, not merely
    that this particular text happens to produce nothing."""
    result = ADAPTER.generate_mutation_sites(
        SAMPLE_TEXT, {12}, operators=OPERATORS, limit=10
    )
    assert result == "UNSUPPORTED"


def test_discovery_is_unsupported_regardless_of_lines_operators_or_limit():
    """No argument is consulted. A limit of 1 and a single selected operator
    would be the natural shape of a "just return the first site" shortcut;
    there is no engine for one to shortcut."""
    assert (
        ADAPTER.generate_mutation_sites(
            SAMPLE_TEXT, set(), operators=OPERATORS, limit=10
        )
        == "UNSUPPORTED"
    )
    assert (
        ADAPTER.generate_mutation_sites(
            SAMPLE_TEXT,
            set(range(1, len(SAMPLE_TEXT.splitlines()) + 1)),
            operators=("compare-swap",),
            limit=1,
        )
        == "UNSUPPORTED"
    )


def test_discovery_is_unsupported_regardless_of_text_content():
    """Never a text-guessed mutant (O2's own negative): garbage input,
    empty input, and even syntactically INVALID Go all produce the same
    unconditional marker -- this method never inspects *text* at all.

    Note the contrast with Python, which raises the typed discovery failure
    for unparseable source: Python HAS an engine, so its failures are
    failures. Go's marker is absence, not failure, and P21 gives the two
    genuinely different terminals."""
    for text in ("", "not go at all {{{", "package p\nfunc F() bool { return true }\n"):
        assert (
            ADAPTER.generate_mutation_sites(
                text, {1}, operators=OPERATORS, limit=10
            )
            == "UNSUPPORTED"
        )


def test_the_old_full_text_surface_is_gone():
    """A-180: `generate_mutants` returned a tuple of full mutated files, and
    keeping it as a compatibility shim would leave the unbounded seam
    reachable. Its absence is part of the contract, not an accident."""
    assert not hasattr(ADAPTER, "generate_mutants")
