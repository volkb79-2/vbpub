"""O2 -- the SAME evaluator (``evaluate_coverage``, unmodified by this
package) run once with :class:`~assay.adapters.python.PythonAdapter` and
once with :class:`~assay.adapters.go.GoAdapter`, on genuinely equivalent
constructs (a two-branch function, one path taken and covered, one path
never taken), returns equivalent numeric results.

The negative this defends (verbatim, O2): *a Python special case in core
makes the paired result differ; declaring requires_span_attribution=True
without a genuine, non-synthetic ambiguity source to prove it is a scope
violation.* Go's own ``requires_span_attribution=False`` (A-102) is
asserted directly (also covered by
``test_adapters_go_registration.py``); what THIS module adds is proof, via
the real four-way union, that Go's own untracked lines (comment/blank/
bare-brace lines a Go coverprofile simply never mentions) fall straight
through to rule 4 exactly the way Python's untracked lines do (rule 4 is
already P05's proven, language-free behaviour -- this is not a re-test of
rule 4 itself, only proof Go reaches it via the SAME path Python does, and
never rule 3b, which Go structurally cannot reach at all).
"""

from __future__ import annotations

from pathlib import Path
from types import MappingProxyType

from assay.adapters.go import GoAdapter
from assay.adapters.python import PythonAdapter
from assay.coverage import load_coverage_profile
from assay.coverage_parsers.model import CoverageProfile, FileCoverage
from assay.diff import AddedLines
from assay.errors import Outcome, ReasonCode
from assay.evaluate import evaluate_coverage

REPO_TOP = Path("/repo")

# --- the genuinely equivalent construct: a two-branch function where the
# taken branch is covered and the untaken branch is not ---
#
# Python (coverage.py: the `def` line and the `if` header both execute at
# call time regardless of which branch is taken; only the UNTAKEN branch's
# own return line is missing):
#
#   1  def classify(x):
#   2      if x > 0:
#   3          return "positive"
#   4      return "other"
#
# executed={1,2,3}, missing={4} -- independently reasoned, not derived from
# calling PythonAdapter.

_PY_SOURCE = 'def classify(x):\n    if x > 0:\n        return "positive"\n    return "other"\n'
assert _PY_SOURCE.count("\n") == 4

# Go (go test -coverprofile: the entry block covers the signature line
# through the taken branch's own return; the untaken branch's return is its
# own separate, missing block; neither closing brace is tracked by any
# block at all -- untracked, not missing):
#
#   1  func Classify(x int) string {
#   2      if x > 0 {
#   3          return "positive"
#   4      }
#   5      return "other"
#   6  }
#
# executed={1,2,3}, missing={5} -- lines 4 and 6 (bare closing braces) are
# in NO block at all.

_GO_SOURCE = (
    "func Classify(x int) string {\n"
    "\tif x > 0 {\n"
    '\t\treturn "positive"\n'
    "\t}\n"
    '\treturn "other"\n'
    "}\n"
)
assert _GO_SOURCE.count("\n") == 6


def _no_read(path: str) -> str:
    raise AssertionError(f"unexpected read_source_text call for {path!r}")


def test_python_and_go_return_equivalent_results_for_a_genuinely_equivalent_construct():
    python_result = evaluate_coverage(
        added=AddedLines(
            by_file=MappingProxyType({"pkg/classify.py": frozenset({1, 2, 3, 4})})
        ),
        profile=CoverageProfile(
            files=MappingProxyType(
                {
                    "pkg/classify.py": FileCoverage(
                        executed=frozenset({1, 2, 3}),
                        missing=frozenset({4}),
                        excluded=frozenset(),
                    )
                }
            )
        ),
        adapter=PythonAdapter(),
        repo_top=REPO_TOP,
        project_root=REPO_TOP,
        source_root_paths=(REPO_TOP,),
        fail_under=100.0,
        allow_excluded=False,
        read_source_text=_no_read,
    )

    go_result = evaluate_coverage(
        added=AddedLines(
            by_file=MappingProxyType(
                {"pkg/classify.go": frozenset({1, 2, 3, 4, 5, 6})}
            )
        ),
        profile=CoverageProfile(
            files=MappingProxyType(
                {
                    "pkg/classify.go": FileCoverage(
                        executed=frozenset({1, 2, 3}),
                        missing=frozenset({5}),
                        excluded=None,
                    )
                }
            )
        ),
        adapter=GoAdapter(),
        repo_top=REPO_TOP,
        project_root=REPO_TOP,
        source_root_paths=(REPO_TOP,),
        fail_under=100.0,
        allow_excluded=False,
        read_source_text=_no_read,
    )

    # Equivalent numeric results for genuinely equivalent constructs (O2):
    for result, label in ((python_result, "python"), (go_result, "go")):
        assert result.executable == 4, label
        assert result.covered == 3, label
        assert result.pct == 75.0, label
        assert result.outcome is Outcome.FAIL, label
        assert result.reason_code is ReasonCode.UNCOVERED_LINES, label

    # Go's own untracked lines (the two bare closing braces) fell straight
    # through rule 4, exactly as Python's own untracked lines would --
    # never rule 3b, which requires_span_attribution=False rules out
    # structurally.
    assert go_result.unclassified_lines == {}
    assert go_result.files_with_unclassified_lines == ()
    assert go_result.missing_lines == {"pkg/classify.go": frozenset({5})}


def test_go_declares_no_span_attribution_the_python_adapter_genuinely_needs():
    assert GoAdapter().requires_span_attribution is False
    assert PythonAdapter().requires_span_attribution is True


# --- "an unknown Go region instead renders FAIL/UNCOVERED_LINES via O4's
# fail-closed has_executable_code" (O2's own text) ---


def test_an_unknown_go_region_absent_from_coverage_renders_uncovered_lines():
    """A changed Go file with no coverage-artifact entry at all, whose text
    is lexically malformed (an unterminated block comment -- this module's
    own O4 fail-closed case), is treated as HAVING code (never silently
    excused) and therefore counts fully toward the denominator as missing
    -- FAIL/UNCOVERED_LINES, never FAIL/UNCLASSIFIED_LINES (which Go's
    ``requires_span_attribution=False`` makes structurally unreachable)."""
    malformed_text = "package x\n\n/* never closed\nfunc F() {\n\treturn\n}\n"
    added = AddedLines(
        by_file=MappingProxyType({"pkg/unknown.go": frozenset({1, 2, 3})})
    )
    profile = load_coverage_profile("mode: set\n", declared_format="go-cover")

    def read_source_text(path: str) -> str:
        assert path == "pkg/unknown.go"
        return malformed_text

    result = evaluate_coverage(
        added=added,
        profile=profile,
        adapter=GoAdapter(),
        repo_top=REPO_TOP,
        project_root=REPO_TOP,
        source_root_paths=(REPO_TOP,),
        fail_under=100.0,
        allow_excluded=False,
        read_source_text=read_source_text,
    )

    assert result.outcome is Outcome.FAIL
    assert result.reason_code is ReasonCode.UNCOVERED_LINES
    assert result.files_missing_coverage == ("pkg/unknown.go",)
    assert result.missing_lines == {"pkg/unknown.go": frozenset({1, 2, 3})}
    assert result.unclassified_lines == {}
