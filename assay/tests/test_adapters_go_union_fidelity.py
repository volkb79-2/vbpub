"""O1 (end-to-end) -- the real ``GoAdapter`` plugged into
``evaluate_coverage`` (unmodified, P05's own function), proving the
adapter WIRES correctly into the real pipeline against COMMITTED Go
source plus a PRE-GENERATED coverprofile (DESIGN-GUIDE §10, A-042), not
just in isolated unit calls.

``tests/fixtures/go/hello/`` is a committed hello-world Go package:
``hello.go`` (two functions, one fully covered, one not -- ``hello.out``
models exactly that) and ``doc.go`` (comment-only, the literal shape of
srdm's own historical incident that ``evaluate.go``'s own comment
documents: "this gate's own first run flagged 94 lines across four
comment-only doc.go files as uncovered").

**Both halves of this fixture are now REAL toolchain output (F008-A4).**
``hello.out`` is what ``go test -coverprofile`` emitted for these exact
source bytes, and the statement positions it does NOT carry come from
``carve-assets/P27-recarve/fixture-oracle.json``, the real output of
``assay/helpers/go/stmtpos/`` over the same bytes. Both were produced by
one run of ``carve-assets/P27-recarve/regenerate-fixtures.sh`` inside
``tester-unified-go:local``; the provenance, the raw run output and the
per-fixture derivation table are in that directory's ``PROVENANCE.md``.

That pairing is the point, not an implementation detail. A-234 recorded
these fixtures as hand-authored and wrong in both coordinates, and its own
warning was that regenerating the BYTES alone would replace a wrong
profile with a real profile still read as statement truth. So this module
joins the two with the real
:func:`~assay.statement_attribution.attribute_statements` -- which refuses
outright if the profile's extents and the oracle's ever disagree, making
"do not edit the fixture without re-running the script" an enforced
precondition rather than a comment.

No Go toolchain is invoked anywhere in this module, and none is available
(A-042/A-087): the toolchain ran once, in the image, and its output is
committed.
"""

from __future__ import annotations

from pathlib import Path
from types import MappingProxyType

from conftest import (
    PROJECT_ROOT,
    as_statement_attributed,
    load_go_statement_oracle,
)

from assay.adapters.go import GoAdapter
from assay.coverage import load_coverage_profile
from assay.coverage_parsers.model import CoverageProfile
from assay.diff import AddedLines
from assay.errors import Outcome, ReasonCode
from assay.evaluate import evaluate_coverage
from assay.statement_attribution import attribute_statements

#: ``tests/fixtures/go/`` -- REPO_TOP for this fixture pipeline. The real
#: files sit at ``REPO_TOP/hello/hello.go`` and ``REPO_TOP/hello/doc.go``,
#: exactly the spelling ``git diff`` would report for them if this were a
#: real repository rooted here.
REPO_TOP = PROJECT_ROOT / "tests" / "fixtures" / "go"
HELLO_DIR = REPO_TOP / "hello"
assert (HELLO_DIR / "hello.go").is_file(), f"missing fixture at {HELLO_DIR}"

#: The real oracle's statement positions for the fixture sources, keyed by
#: basename. `hello.out` spells the file `hello/hello.go`; the oracle ran over
#: a flat staging directory and reports `hello.go`.
ORACLE = load_go_statement_oracle(
    PROJECT_ROOT
    / "nyxloom-trove"
    / "carve-assets"
    / "P27-recarve"
    / "fixture-oracle.json"
)

#: `hello.go` is 40 lines. Asserted rather than written as a literal range so
#: an edit to the fixture cannot leave this module silently judging a window
#: that no longer covers the file.
HELLO_LINE_COUNT = len(
    (HELLO_DIR / "hello.go").read_text(encoding="utf-8").splitlines()
)
assert HELLO_LINE_COUNT == 40, HELLO_LINE_COUNT


def _read_source_text(path: str) -> str:
    return (REPO_TOP / path).read_text(encoding="utf-8")


def _attributed(profile: CoverageProfile) -> CoverageProfile:
    """*profile* corrected by the REAL oracle document -- the same call the
    runner makes on a live Go lane (``runner._attribute_statements_for_lane``),
    with the oracle's subprocess replaced by its committed output and nothing
    else."""
    return attribute_statements(
        profile, {"hello/hello.go": ORACLE["hello.go"]}
    )


def test_the_hello_world_fixture_produces_the_exact_expected_mapping():
    """``hello.go`` is 40 lines. The real ``hello.out`` carries two blocks --
    ``32.32,34.2`` count 1 and ``38.35,40.2`` count 0 -- and the real oracle
    says each contains exactly ONE statement, on line 33 and line 39. So the
    whole diff is 2 executable lines, not 6: the two ``func`` signatures and
    the two closing braces sit inside those extents and are not statements.
    Every other line (comments, ``package``/``import``, blanks) is in no block
    at all. ``doc.go`` is 8 lines, entirely comment/package, absent from
    ``hello.out`` entirely, and ``has_executable_code`` correctly excludes it
    via the NoCode path (never ``files_missing_coverage``) -- srdm's own
    historical incident, proven not to recur here."""
    adapter = GoAdapter()
    profile_text = (HELLO_DIR / "hello.out").read_text(encoding="utf-8")
    profile = load_coverage_profile(profile_text, declared_format="go-cover")

    hello_go_lines = frozenset(range(1, HELLO_LINE_COUNT + 1))
    doc_go_lines = frozenset(range(1, 9))
    added = AddedLines(
        by_file=MappingProxyType(
            {
                "hello/hello.go": hello_go_lines,
                "hello/doc.go": doc_go_lines,
            }
        )
    )

    result = evaluate_coverage(
        added=added,
        profile=_attributed(profile),
        adapter=adapter,
        repo_top=REPO_TOP,
        project_root=REPO_TOP,
        source_root_paths=(HELLO_DIR.resolve(),),
        fail_under=100.0,
        allow_excluded=False,
        read_source_text=_read_source_text,
    )

    assert result.considered == 2
    assert result.executable == 2  # {33, 39} -- NOT {32,33,34,38,39,40}
    assert result.covered == 1  # {33}
    assert result.pct == 50.0
    assert result.missing_lines == {"hello/hello.go": frozenset({39})}
    # doc.go is comment-only -- NoCode, never "missing coverage".
    assert result.files_missing_coverage == ()
    # Go's requires_span_attribution=False (A-102): never reaches rule 3b.
    assert result.unclassified_lines == {}
    assert result.files_with_unclassified_lines == ()
    assert result.outcome is Outcome.FAIL
    assert result.reason_code is ReasonCode.UNCOVERED_LINES


def test_the_naive_block_expansion_of_this_very_fixture_reports_three_times_the_lines():
    """The control for the test above, and the reason F008-A4 could not be
    "regenerate the bytes". Parsing the SAME real profile without the oracle
    yields ``{32,33,34}`` executed and ``{38,39,40}`` missing -- six lines
    where Go has two statements, with a function signature and a closing brace
    reported as uncovered code in the missing half.

    The old hand-authored bytes hid this: their blocks ended at the return
    statement's own last column, so the naive expansion happened to be right
    by accident. Real bytes make the over-approximation visible, which is why
    swapping them in without re-deriving the expectations would have made this
    fixture worse, not better (A-234's own warning)."""
    profile = load_coverage_profile(
        (HELLO_DIR / "hello.out").read_text(encoding="utf-8"),
        declared_format="go-cover",
    )
    naive = profile.files["hello/hello.go"]
    assert naive.executed == frozenset({32, 33, 34})
    assert naive.missing == frozenset({38, 39, 40})

    corrected = _attributed(profile).files["hello/hello.go"]
    assert corrected.executed == frozenset({33})
    assert corrected.missing == frozenset({39})


def test_the_committed_profile_and_the_committed_oracle_describe_the_same_bytes():
    """The guard that makes ``hello.go``'s own "editing this file invalidates
    hello.out" header enforceable. ``attribute_statements`` compares the two
    documents' extent SETS and refuses ``UNREADABLE_ARTIFACT`` on any
    disagreement, so an edit that moves a function body without a re-run turns
    this suite red instead of silently attributing lines from a stale pair."""
    profile = load_coverage_profile(
        (HELLO_DIR / "hello.out").read_text(encoding="utf-8"),
        declared_format="go-cover",
    )
    blocks = profile.files["hello/hello.go"].blocks
    assert blocks is not None
    assert {block.extent for block in blocks} == {
        block.extent for block in ORACLE["hello.go"]
    }
    # doc.go is comment-only, and the oracle says so independently of its
    # absence from the profile: zero blocks, derived from the source itself.
    assert ORACLE["doc.go"] == ()


def test_the_hello_world_fixture_passes_once_farewell_is_also_exercised():
    """Same committed source and the same two REAL block extents, with
    Farewell's count flipped from 0 to 1 -- proving the FAIL above was really
    about the gap ``hello.out`` records, not an artefact of this fixture's
    shape. Only the count is hand-changed: the extents stay byte-identical to
    the toolchain's own, so the same oracle document still joins and the same
    two statement lines come out."""
    adapter = GoAdapter()
    profile_text = (
        "mode: set\n"
        "hello/hello.go:32.32,34.2 1 1\n"
        "hello/hello.go:38.35,40.2 1 1\n"
    )
    profile = load_coverage_profile(profile_text, declared_format="go-cover")

    added = AddedLines(
        by_file=MappingProxyType(
            {"hello/hello.go": frozenset(range(1, HELLO_LINE_COUNT + 1))}
        )
    )

    result = evaluate_coverage(
        added=added,
        profile=_attributed(profile),
        adapter=adapter,
        repo_top=REPO_TOP,
        project_root=REPO_TOP,
        source_root_paths=(HELLO_DIR.resolve(),),
        fail_under=100.0,
        allow_excluded=False,
        read_source_text=_read_source_text,
    )

    assert result.outcome is Outcome.PASS
    assert result.pct == 100.0
    assert result.missing_lines == {}


# --- is_test_path end-to-end: a changed Go test file is skipped entirely ---


def test_a_go_test_file_is_skipped_entirely_through_the_real_pipeline():
    adapter = GoAdapter()
    added = AddedLines(
        by_file=MappingProxyType(
            {
                "pkg/widget_test.go": frozenset({1, 2, 3}),
                "pkg/widget.go": frozenset({1}),
            }
        )
    )
    profile = load_coverage_profile(
        "mode: set\npkg/widget.go:1.1,1.20 1 1\n", declared_format="go-cover"
    )

    def read_source_text(path: str) -> str:
        raise AssertionError(f"unexpected read for {path!r}")

    result = evaluate_coverage(
        added=added,
        # A synthetic one-line block: its expansion IS its statement set, and
        # `as_statement_attributed` checks that rather than assuming it. The
        # subject here is `is_test_path`, not statement granularity.
        profile=as_statement_attributed(profile),
        adapter=adapter,
        repo_top=Path("/repo"),
        project_root=Path("/repo"),
        source_root_paths=(Path("/repo"),),
        fail_under=100.0,
        allow_excluded=False,
        read_source_text=read_source_text,
    )

    assert result.considered == 1  # only widget.go -- the test file never counts
    assert result.outcome is Outcome.PASS


# --- normalize_coverage_key end-to-end: a genuinely differing key spelling ---


def test_normalize_coverage_key_reconciles_the_real_pipeline_end_to_end():
    """The coverage artifact's own key carries the module's full import
    path (``"srdm/internal/x.go"``, matching ``go test``'s own profile
    spelling), while the diff's spelling has already dropped it
    (``"internal/x.go"``) -- only the adapter's own module-path strip lets
    them match, mirroring
    ``test_adapters_python_union_fidelity.py``'s own O2 end-to-end proof."""
    adapter = GoAdapter(module_path="srdm")
    added = AddedLines(
        by_file=MappingProxyType({"internal/x.go": frozenset({2, 3})})
    )
    profile = load_coverage_profile(
        "mode: set\nsrdm/internal/x.go:2.1,2.10 1 1\nsrdm/internal/x.go:3.1,3.10 1 0\n",
        declared_format="go-cover",
    )

    def read_source_text(path: str) -> str:
        raise AssertionError("this file has a coverage entry")

    result = evaluate_coverage(
        added=added,
        # Two synthetic one-line blocks; the subject is the module-path strip,
        # not statement granularity. See the helper's own invariant check.
        profile=as_statement_attributed(profile),
        adapter=adapter,
        repo_top=Path("/repo"),
        project_root=Path("/repo"),
        source_root_paths=(Path("/repo"),),
        fail_under=100.0,
        allow_excluded=False,
        read_source_text=read_source_text,
    )

    assert result.covered == 1
    assert result.executable == 2
    assert result.missing_lines == {"internal/x.go": frozenset({3})}
