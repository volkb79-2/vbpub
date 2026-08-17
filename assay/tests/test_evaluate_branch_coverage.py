"""Wave-1 §4 (A-257/A-258/A-263) -- branch-arc arithmetic wired into
:func:`assay.evaluate.evaluate_coverage`, and :func:`assay.evaluate._tally_branches`
proven directly.

Every negative here is a flip: dropping the rule under test would change
``outcome``/``reason_code``/``pct`` from what is asserted, not merely leave an
assertion unexercised.

1. ``_tally_branches`` in isolation -- the shared per-line arithmetic both
   :func:`~assay.evaluate.evaluate_coverage` and
   :func:`~assay.evaluate.evaluate_targets` call.
2. wired into ``evaluate_coverage``: ``pct`` is the COMBINED line+branch
   percentage (A-263); a floor missed on branches alone (zero missing
   LINES, at least one uncovered arc) is ``UNCOVERED_BRANCHES``, never
   ``UNCOVERED_LINES`` -- and the reverse precedence when both are present.
3. rule 2 (module docstring): a line rule 3b resolves by SPAN ATTRIBUTION
   contributes nothing to the branch side, even when that line is itself a
   branch source in the artifact -- crediting arcs on the anchor line
   instead would count the same arcs twice under one attributed line and
   zero under another.
"""

from __future__ import annotations

from pathlib import Path
from types import MappingProxyType

from conftest import FakeAdapter

from assay.adapters.python import PythonAdapter
from assay.coverage_parsers.model import BranchCoverage, CoverageProfile, FileCoverage
from assay.diff import AddedLines
from assay.errors import Outcome, ReasonCode
from assay.evaluate import _tally_branches, evaluate_coverage

REPO_TOP = Path("/repo")
ADAPTER = FakeAdapter()


def _fail_on_read(path: str) -> str:
    raise AssertionError(f"read_source_text unexpectedly called for {path!r}")


def _added(path: str, lines: set[int]) -> AddedLines:
    return AddedLines(by_file=MappingProxyType({path: frozenset(lines)}))


def _profile(path: str, cov: FileCoverage) -> CoverageProfile:
    return CoverageProfile(files=MappingProxyType({path: cov}))


def _evaluate(added, profile, *, allow_excluded: bool = False, adapter=ADAPTER, read_source_text=_fail_on_read):
    return evaluate_coverage(
        added=added,
        profile=profile,
        adapter=adapter,
        repo_top=REPO_TOP,
        source_root_paths=(REPO_TOP / "pkg",),
        fail_under=100.0,
        allow_excluded=allow_excluded,
        read_source_text=read_source_text,
    )


# --- _tally_branches, in isolation --------------------------------------------


def test_tally_branches_returns_zeroes_when_the_file_has_no_branch_capability():
    file_cov = FileCoverage(executed=frozenset({2}), missing=frozenset(), excluded=frozenset())
    covered, total, missing = _tally_branches({2}, file_cov)
    assert (covered, total, missing) == (0, 0, frozenset())


def test_tally_branches_skips_a_line_absent_from_by_line():
    file_cov = FileCoverage(
        executed=frozenset({2, 3}), missing=frozenset(), excluded=frozenset(),
        branches=BranchCoverage(by_line={2: (2, 2)}),
    )
    # line 3 is executed but carries no branch at all -- absent from
    # by_line, so it contributes nothing to any of the three.
    covered, total, missing = _tally_branches({2, 3}, file_cov)
    assert (covered, total, missing) == (2, 2, frozenset())


def test_tally_branches_reports_a_fully_covered_line_as_not_missing():
    file_cov = FileCoverage(
        executed=frozenset({2}), missing=frozenset(), excluded=frozenset(),
        branches=BranchCoverage(by_line={2: (2, 2)}),
    )
    covered, total, missing = _tally_branches({2}, file_cov)
    assert (covered, total, missing) == (2, 2, frozenset())


def test_tally_branches_reports_a_partially_covered_line_as_missing():
    file_cov = FileCoverage(
        executed=frozenset({2}), missing=frozenset(), excluded=frozenset(),
        branches=BranchCoverage(by_line={2: (1, 2)}),
    )
    covered, total, missing = _tally_branches({2}, file_cov)
    assert (covered, total, missing) == (1, 2, frozenset({2}))


def test_tally_branches_sums_across_multiple_lines():
    file_cov = FileCoverage(
        executed=frozenset({2, 3}), missing=frozenset(), excluded=frozenset(),
        branches=BranchCoverage(by_line={2: (2, 2), 3: (1, 2)}),
    )
    covered, total, missing = _tally_branches({2, 3}, file_cov)
    assert (covered, total, missing) == (3, 4, frozenset({3}))


# --- wired into evaluate_coverage: outcome/reason-code precedence -------------


def test_full_line_and_branch_coverage_passes_at_the_combined_percentage():
    added = _added("pkg/mod.zzz", {2})
    profile = _profile(
        "pkg/mod.zzz",
        FileCoverage(
            executed=frozenset({2}), missing=frozenset(), excluded=frozenset(),
            branches=BranchCoverage(by_line={2: (2, 2)}),
        ),
    )
    result = _evaluate(added, profile)

    assert result.covered == 1
    assert result.executable == 1
    assert result.branches_covered == 2
    assert result.branches_total == 2
    # A-263: (covered + branches_covered) / (executable + branches_total)
    assert result.pct == 100.0
    assert result.outcome is Outcome.PASS
    assert result.reason_code is None
    assert result.missing_branch_lines == {}
    assert result.files_with_missing_branch_lines == ()


def test_a_branch_deficit_alone_fails_as_uncovered_branches_not_uncovered_lines():
    """Zero missing LINES, one uncovered arc: A-263's own outcome precedence."""
    added = _added("pkg/mod.zzz", {2})
    profile = _profile(
        "pkg/mod.zzz",
        FileCoverage(
            executed=frozenset({2}), missing=frozenset(), excluded=frozenset(),
            branches=BranchCoverage(by_line={2: (1, 2)}),
        ),
    )
    result = _evaluate(added, profile)

    assert result.covered == 1
    assert result.executable == 1
    assert result.branches_covered == 1
    assert result.branches_total == 2
    assert result.pct == 100.0 * 2 / 3
    assert result.missing_lines == {}
    assert result.outcome is Outcome.FAIL
    assert result.reason_code is ReasonCode.UNCOVERED_BRANCHES
    assert result.missing_branch_lines == {"pkg/mod.zzz": frozenset({2})}
    assert result.files_with_missing_branch_lines == ("pkg/mod.zzz",)


def test_a_missing_line_takes_precedence_over_a_branch_deficit():
    """Both a missing line AND an uncovered arc present: UNCOVERED_LINES
    wins -- "which mechanism refused" names the line failure first, per the
    module's own stated precedence.
    """
    added = _added("pkg/mod.zzz", {2, 3})
    profile = _profile(
        "pkg/mod.zzz",
        FileCoverage(
            executed=frozenset({2}), missing=frozenset({3}), excluded=frozenset(),
            branches=BranchCoverage(by_line={2: (1, 2)}),
        ),
    )
    result = _evaluate(added, profile)

    assert result.missing_lines == {"pkg/mod.zzz": frozenset({3})}
    assert result.outcome is Outcome.FAIL
    assert result.reason_code is ReasonCode.UNCOVERED_LINES


def test_branch_capability_unavailable_degenerates_to_the_line_only_value():
    """No `branches` on the FileCoverage at all -- the pre-wave-1 value,
    unaffected: `pct` degenerates with no special case."""
    added = _added("pkg/mod.zzz", {2, 3})
    profile = _profile(
        "pkg/mod.zzz",
        FileCoverage(executed=frozenset({2}), missing=frozenset({3}), excluded=frozenset()),
    )
    result = _evaluate(added, profile)

    assert result.branches_covered == 0
    assert result.branches_total == 0
    assert result.pct == 50.0
    assert result.branch_capability == "unavailable"


def test_an_excluded_branch_source_line_contributes_no_arcs():
    """Rule 3 (module docstring): a branch-source line that is excluded is
    unreachable via `changed_exec` by construction -- proven here by an
    excluded line that ALSO carries no branch entry (FileCoverage's own
    invariant forbids a branch line inside `excluded`), so only its
    disjointness from the branch tally is what this test can observe."""
    added = _added("pkg/mod.zzz", {2, 4})
    profile = _profile(
        "pkg/mod.zzz",
        FileCoverage(
            executed=frozenset({2}), missing=frozenset(), excluded=frozenset({4}),
            branches=BranchCoverage(by_line={2: (2, 2)}),
        ),
    )
    result = _evaluate(added, profile, allow_excluded=True)

    assert result.branches_covered == 2
    assert result.branches_total == 2
    assert 4 not in result.missing_branch_lines.get("pkg/mod.zzz", frozenset())


# --- rule 2: a span-attributed line contributes nothing to the branch side ----


def test_a_span_attributed_line_does_not_double_count_the_anchors_arcs():
    """`return {...}` spans lines 2-5; line 2 is the tracked anchor, itself
    a branch source with real arcs, but is NOT among the changed lines.
    Lines 3-4 are changed, unattributed by the coverage artifact itself,
    and resolve via rule 3b to line 2's EXECUTED status (PASS, correctly).
    The branch tally is drawn strictly from `changed_exec` -- the lines
    directly classified against the artifact -- never from whatever a line
    was attributed TO. If attribution fed its anchor's line number into the
    branch tally instead, this would report the anchor's real 2/2 arcs
    despite line 2 never having been a changed line at all -- inventing a
    branch measurement for a line the diff never touched.
    """
    source = (
        "def build_config():\n"      # 1
        "    return {\n"              # 2
        '        "a": 1,\n'          # 3
        '        "b": 2,\n'          # 4
        "    }\n"                    # 5
    )
    path = "pkg/mod.py"
    added = AddedLines(by_file=MappingProxyType({path: frozenset({3, 4})}))
    profile = CoverageProfile(
        files=MappingProxyType(
            {
                path: FileCoverage(
                    executed=frozenset({2}), missing=frozenset(), excluded=frozenset(),
                    branches=BranchCoverage(by_line={2: (2, 2)}),
                )
            }
        )
    )

    def read_source_text(p: str) -> str:
        assert p == path
        return source

    result = evaluate_coverage(
        added=added,
        profile=profile,
        adapter=PythonAdapter(),
        repo_top=REPO_TOP,
        source_root_paths=(REPO_TOP / "pkg",),
        fail_under=100.0,
        allow_excluded=False,
        read_source_text=read_source_text,
    )

    # Both changed lines attributed to line 2's executed status.
    assert result.covered == 2
    assert result.executable == 2
    assert result.unclassified_lines == {}
    # The branch tally is drawn from `changed_exec` (empty here -- lines 3-4
    # never intersect `file_cov.executed | file_cov.missing` directly) and
    # NEVER from attribution, so it stays exactly what line 2 alone would
    # have reported had it been the changed line -- which it was not, so it
    # is zero here.
    assert result.branches_covered == 0
    assert result.branches_total == 0
    assert result.outcome is Outcome.PASS
