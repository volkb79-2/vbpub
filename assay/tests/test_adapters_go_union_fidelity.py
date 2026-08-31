"""O1 (end-to-end) -- the real ``GoAdapter`` plugged into
``evaluate_coverage`` (unmodified, P05's own function), proving the
adapter WIRES correctly into the real pipeline against COMMITTED Go
source plus a PRE-GENERATED coverprofile (DESIGN-GUIDE §10, A-042), not
just in isolated unit calls.

``tests/fixtures/go/hello/`` is a committed hello-world Go package:
``hello.go`` (two functions, one fully covered, one not -- the
pre-generated ``hello.out`` below models exactly that) and ``doc.go``
(comment-only, the literal shape of srdm's own historical incident that
``evaluate.go``'s own comment documents: "this gate's own first run
flagged 94 lines across four comment-only doc.go files as uncovered").
Regeneration instructions live in ``hello.go``'s own header comment. No Go
toolchain is invoked anywhere in this module.
"""

from __future__ import annotations

from pathlib import Path
from types import MappingProxyType

from conftest import PROJECT_ROOT, as_pre_oracle_attributed

from assay.adapters.go import GoAdapter
from assay.coverage import load_coverage_profile
from assay.diff import AddedLines
from assay.errors import Outcome, ReasonCode
from assay.evaluate import evaluate_coverage

#: ``tests/fixtures/go/`` -- REPO_TOP for this fixture pipeline. The real
#: files sit at ``REPO_TOP/hello/hello.go`` and ``REPO_TOP/hello/doc.go``,
#: exactly the spelling ``git diff`` would report for them if this were a
#: real repository rooted here.
REPO_TOP = PROJECT_ROOT / "tests" / "fixtures" / "go"
HELLO_DIR = REPO_TOP / "hello"
assert (HELLO_DIR / "hello.go").is_file(), f"missing fixture at {HELLO_DIR}"


def _read_source_text(path: str) -> str:
    return (REPO_TOP / path).read_text(encoding="utf-8")


def test_the_hello_world_fixture_produces_the_exact_expected_mapping():
    """``hello.go`` is 38 lines; the committed ``hello.out`` marks Greet's
    body (29-30) EXECUTED and Farewell's body (36-37) MISSING -- every
    other line (comments, ``package``/``import``, blank lines, braces) is
    untracked by any block and silently ignored. ``doc.go`` is 8 lines,
    entirely comment/package, absent from ``hello.out`` entirely, and
    ``has_executable_code`` correctly excludes it via the NoCode path
    (never ``files_missing_coverage``) -- srdm's own historical incident,
    proven not to recur here."""
    adapter = GoAdapter()
    profile_text = (HELLO_DIR / "hello.out").read_text(encoding="utf-8")
    profile = load_coverage_profile(profile_text, declared_format="go-cover")

    hello_go_lines = frozenset(range(1, 39))
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
        # (A-392/A-397) The union arithmetic under test is unchanged; what
        # changed is that GoAdapter now REFUSES an uncorrected block profile.
        # These line sets are the pre-oracle expansion A-234 records as stale
        # -- see the helper's docstring; F008-A4 replaces them.
        profile=as_pre_oracle_attributed(profile),
        adapter=adapter,
        repo_top=REPO_TOP,
        project_root=REPO_TOP,
        source_root_paths=(HELLO_DIR.resolve(),),
        fail_under=100.0,
        allow_excluded=False,
        read_source_text=_read_source_text,
    )

    assert result.considered == 2
    assert result.executable == 4  # {29, 30, 36, 37}
    assert result.covered == 2  # {29, 30}
    assert result.pct == 50.0
    assert result.missing_lines == {"hello/hello.go": frozenset({36, 37})}
    # doc.go is comment-only -- NoCode, never "missing coverage".
    assert result.files_missing_coverage == ()
    # Go's requires_span_attribution=False (A-102): never reaches rule 3b.
    assert result.unclassified_lines == {}
    assert result.files_with_unclassified_lines == ()
    assert result.outcome is Outcome.FAIL
    assert result.reason_code is ReasonCode.UNCOVERED_LINES


def test_the_hello_world_fixture_passes_once_farewell_is_also_exercised():
    """Same committed source, a DIFFERENT (still hand-written, still
    pre-generated-shaped) coverage profile marking Farewell's body
    executed too -- proving the FAIL above was really about the real gap
    in ``hello.out``, not an artefact of this fixture's shape."""
    adapter = GoAdapter()
    profile_text = (
        "mode: set\n"
        "hello/hello.go:29.34,30.42 1 1\n"
        "hello/hello.go:36.37,37.44 1 1\n"
    )
    profile = load_coverage_profile(profile_text, declared_format="go-cover")

    added = AddedLines(
        by_file=MappingProxyType({"hello/hello.go": frozenset(range(1, 39))})
    )

    result = evaluate_coverage(
        added=added,
        # (A-392/A-397) The union arithmetic under test is unchanged; what
        # changed is that GoAdapter now REFUSES an uncorrected block profile.
        # These line sets are the pre-oracle expansion A-234 records as stale
        # -- see the helper's docstring; F008-A4 replaces them.
        profile=as_pre_oracle_attributed(profile),
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
        # (A-392/A-397) The union arithmetic under test is unchanged; what
        # changed is that GoAdapter now REFUSES an uncorrected block profile.
        # These line sets are the pre-oracle expansion A-234 records as stale
        # -- see the helper's docstring; F008-A4 replaces them.
        profile=as_pre_oracle_attributed(profile),
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
        # (A-392/A-397) The union arithmetic under test is unchanged; what
        # changed is that GoAdapter now REFUSES an uncorrected block profile.
        # These line sets are the pre-oracle expansion A-234 records as stale
        # -- see the helper's docstring; F008-A4 replaces them.
        profile=as_pre_oracle_attributed(profile),
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
