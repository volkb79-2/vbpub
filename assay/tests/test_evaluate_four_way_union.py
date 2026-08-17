"""O1 — the four-way changed-line union, proven with a fake adapter.

The negative this defends (verbatim): *dropping any union member makes its
literal set fixture change from FAIL to PASS or vice versa.* Each test below
isolates ONE of the four rules with a fixture engineered so that removing
just that rule's effect (simulated by hand in the assertions' own reasoning,
and for real in the LOG's mutation self-review) flips PASS/FAIL:

1. a changed EXECUTABLE line requires execution (``missing`` fails it);
2. a changed EXCLUDED line fails unless ``allow_excluded``, even at 100%;
3. a changed line outside executed/missing/excluded (non-executable) is
   silently ignored — it can never turn a pass into a fail;
4. an uncovered executable line the diff never touched cannot affect the
   result — only ``added.by_file`` is ever consulted.

No filesystem, no git, no real language: :class:`~conftest.FakeAdapter`'s
source globs are ``*.zzz`` and its markers are literal strings, so nothing
here could pass by accident if ``evaluate_coverage`` secretly special-cased
Python.
"""

from __future__ import annotations

from pathlib import Path
from types import MappingProxyType

import pytest
from conftest import FakeAdapter

from assay.coverage_parsers.model import CoverageProfile, FileCoverage
from assay.diff import AddedLines
from assay.errors import Outcome, ReasonCode
from assay.evaluate import evaluate_coverage

REPO_TOP = Path("/repo")
ADAPTER = FakeAdapter()


def _fail_on_read(path: str) -> str:
    """Every scenario below has a coverage entry for every considered file,
    so ``has_executable_code`` must never be consulted and this must never
    be called. A stray call is a real bug, not a fixture gap — surfacing it
    as a hard failure (rather than returning empty text) keeps the test
    honest about which code path it exercises."""
    raise AssertionError(f"read_source_text unexpectedly called for {path!r}")


def _added(path: str, lines: set[int]) -> AddedLines:
    return AddedLines(by_file=MappingProxyType({path: frozenset(lines)}))


def _profile(path: str, cov: FileCoverage) -> CoverageProfile:
    return CoverageProfile(files=MappingProxyType({path: cov}))


def _evaluate(added: AddedLines, profile: CoverageProfile, *, allow_excluded: bool = False):
    return evaluate_coverage(
        added=added,
        profile=profile,
        adapter=ADAPTER,
        repo_top=REPO_TOP,
        source_root_paths=(REPO_TOP / "pkg",),
        fail_under=100.0,
        allow_excluded=allow_excluded,
        read_source_text=_fail_on_read,
    )


# --- member 1: a changed executable line requires execution -------------------


def test_a_changed_missing_line_fails_the_union():
    added = _added("pkg/mod.zzz", {2, 3})
    profile = _profile(
        "pkg/mod.zzz",
        FileCoverage(executed=frozenset({2}), missing=frozenset({3}), excluded=frozenset()),
    )
    result = _evaluate(added, profile)

    assert result.covered == 1
    assert result.executable == 2
    assert result.pct == 50.0
    assert result.considered == 1
    assert result.missing_lines == {"pkg/mod.zzz": frozenset({3})}
    assert result.files_missing_coverage == ()
    assert result.outcome is Outcome.FAIL
    assert result.reason_code is ReasonCode.UNCOVERED_LINES
    # Dropping this rule (treating `missing` as if it were `executed`, or
    # never counting it toward the denominator at all) would make this
    # PASS at 100% instead — the flip the negative names.


def test_every_changed_line_executed_passes():
    added = _added("pkg/mod.zzz", {2, 3})
    profile = _profile(
        "pkg/mod.zzz",
        FileCoverage(executed=frozenset({2, 3}), missing=frozenset(), excluded=frozenset()),
    )
    result = _evaluate(added, profile)

    assert result.covered == 2
    assert result.executable == 2
    assert result.pct == 100.0
    assert result.missing_lines == {}
    assert result.outcome is Outcome.PASS
    assert result.reason_code is None


# --- member 2: a changed excluded line fails unless allow_excluded ------------


def test_a_changed_excluded_line_fails_even_at_100_percent():
    added = _added("pkg/mod.zzz", {2, 3, 4})
    profile = _profile(
        "pkg/mod.zzz",
        FileCoverage(
            executed=frozenset({2, 3}), missing=frozenset(), excluded=frozenset({4})
        ),
    )
    result = _evaluate(added, profile, allow_excluded=False)

    # The totals alone say 100% -- proving the fail comes from the excluded
    # rule, not from the percentage.
    assert result.covered == 2
    assert result.executable == 2
    assert result.pct == 100.0
    assert result.missing_lines == {}
    assert result.outcome is Outcome.FAIL
    assert result.reason_code is ReasonCode.EXCLUDED_LINES
    # Dropping this rule (never checking `excluded` at all) would make this
    # PASS -- the flip the negative names.


def test_allow_excluded_opts_a_lane_back_into_passing():
    added = _added("pkg/mod.zzz", {2, 3, 4})
    profile = _profile(
        "pkg/mod.zzz",
        FileCoverage(
            executed=frozenset({2, 3}), missing=frozenset(), excluded=frozenset({4})
        ),
    )
    result = _evaluate(added, profile, allow_excluded=True)

    assert result.outcome is Outcome.PASS
    assert result.reason_code is None


# --- member 3: a changed non-executable line is silently ignored --------------


def test_a_changed_line_outside_executable_and_excluded_never_fails_it():
    # Line 5 is changed but appears in NEITHER executed, missing, NOR
    # excluded -- a comment or blank line the format never tracked.
    added = _added("pkg/mod.zzz", {2, 3, 5})
    profile = _profile(
        "pkg/mod.zzz",
        FileCoverage(executed=frozenset({2, 3}), missing=frozenset(), excluded=frozenset()),
    )
    result = _evaluate(added, profile)

    assert result.covered == 2
    assert result.executable == 2  # line 5 contributes to NEITHER
    assert result.pct == 100.0
    assert result.missing_lines == {}
    assert result.outcome is Outcome.PASS
    # Dropping this rule (counting line 5 toward executable without
    # a matching executed/covered contribution) would make this FAIL at
    # 66.67% -- the flip the negative names.


# --- member 4: an uncovered line the diff never touched does not matter -------


def test_a_pre_existing_uncovered_line_outside_the_diff_is_invisible():
    # The file's OWN coverage record has more missing lines than the diff
    # touched -- line 9 is pre-existing and was never part of `added`.
    added = _added("pkg/mod.zzz", {2, 3})
    profile = _profile(
        "pkg/mod.zzz",
        FileCoverage(
            executed=frozenset({2, 3}), missing=frozenset({9}), excluded=frozenset()
        ),
    )
    result = _evaluate(added, profile)

    assert result.covered == 2
    assert result.executable == 2  # line 9 never enters the denominator
    assert result.pct == 100.0
    assert result.missing_lines == {}  # line 9 is not "missing" from THIS claim
    assert result.outcome is Outcome.PASS
    # Dropping this rule (using the file's WHOLE `missing` set instead of
    # intersecting with `lines`) would make this FAIL at 66.67% -- the flip
    # the negative names.


# --- the combined fixture: every member exercised in one evaluation -----------


def test_all_four_members_combine_in_one_evaluation():
    added = _added("pkg/mod.zzz", {2, 3, 4, 5, 6})
    profile = _profile(
        "pkg/mod.zzz",
        FileCoverage(
            executed=frozenset({2}),  # member 1: covered
            missing=frozenset({3, 9}),  # member 1 (3 in diff) + member 4 (9 not)
            excluded=frozenset({4}),  # member 2: excluded, changed
            # line 5 is in none of the three sets -- member 3
            # line 6 also in none -- member 3, second instance
        ),
    )
    result = _evaluate(added, profile, allow_excluded=False)

    assert result.covered == 1
    assert result.executable == 2  # {2, 3}; 4 excluded, 5/6 non-code
    assert result.pct == 50.0
    assert result.considered == 1
    assert result.missing_lines == {"pkg/mod.zzz": frozenset({3})}
    assert result.files_missing_coverage == ()
    # Excluded-line failure takes precedence over the percentage failure --
    # both are real here, and the claim carries exactly one reason_code.
    assert result.outcome is Outcome.FAIL
    assert result.reason_code is ReasonCode.EXCLUDED_LINES
