"""O2 (P15) — ``FileCoverage`` enforces the common coverage model's own
invariants at construction: every line number is positive, and ``executed``,
``missing``, and (when known) ``excluded`` are pairwise disjoint.

Direct unit tests of the shared model, independent of any one format parser
(:mod:`assay.coverage_parsers.coverage_py_json` has its own end-to-end
tests proving a REAL malformed JSON artifact trips this) — this is where
every branch of :meth:`~assay.coverage_parsers.model.FileCoverage.__post_init__`
itself is exercised.

Negative: removing either check would let sol's reproduction through —  a
line simultaneously ``executed`` and ``missing`` reports a false ``PASS``
while still claiming the same line missing.
"""

from __future__ import annotations

import pytest

from assay.coverage_parsers.model import BranchCoverage, FileCoverage


def test_disjoint_executed_and_missing_with_no_excluded_data_is_accepted():
    coverage = FileCoverage(
        executed=frozenset({1, 2}), missing=frozenset({3}), excluded=None
    )
    assert coverage.executed == frozenset({1, 2})
    assert coverage.missing == frozenset({3})
    assert coverage.excluded is None


def test_disjoint_three_way_split_with_known_excluded_data_is_accepted():
    coverage = FileCoverage(
        executed=frozenset({1}), missing=frozenset({2}), excluded=frozenset({3})
    )
    assert coverage.excluded == frozenset({3})


def test_empty_excluded_frozenset_is_accepted_and_stays_distinct_from_none():
    coverage = FileCoverage(executed=frozenset(), missing=frozenset(), excluded=frozenset())
    assert coverage.excluded == frozenset()
    assert coverage.excluded is not None


@pytest.mark.parametrize("field", ["executed", "missing", "excluded"])
def test_a_zero_line_number_is_rejected_in_every_field(field: str):
    values = {"executed": frozenset(), "missing": frozenset(), "excluded": frozenset()}
    values[field] = frozenset({0})
    with pytest.raises(ValueError, match="non-positive"):
        FileCoverage(**values)


@pytest.mark.parametrize("field", ["executed", "missing", "excluded"])
def test_a_negative_line_number_is_rejected_in_every_field(field: str):
    values = {"executed": frozenset(), "missing": frozenset(), "excluded": frozenset()}
    values[field] = frozenset({-3})
    with pytest.raises(ValueError, match="non-positive"):
        FileCoverage(**values)


def test_a_line_both_executed_and_missing_is_rejected():
    # sol's exact reproduction: a line simultaneously claimed executed AND
    # missing would otherwise compute a false 100% while still reporting it
    # missing.
    with pytest.raises(ValueError, match="executed and .missing are not disjoint"):
        FileCoverage(executed=frozenset({1}), missing=frozenset({1}), excluded=None)


def test_a_line_both_executed_and_excluded_is_rejected():
    with pytest.raises(ValueError, match="executed and .excluded are not disjoint"):
        FileCoverage(executed=frozenset({1}), missing=frozenset(), excluded=frozenset({1}))


def test_a_line_both_missing_and_excluded_is_rejected():
    with pytest.raises(ValueError, match="missing and .excluded are not disjoint"):
        FileCoverage(executed=frozenset(), missing=frozenset({1}), excluded=frozenset({1}))


def test_excluded_none_skips_every_excluded_related_check():
    # A format that cannot express exclusions at all (lcov, cobertura,
    # go-cover) must never be penalised for having nothing to say about a
    # line executed/missing also "excludes" -- there is no third bucket to
    # collide with.
    FileCoverage(executed=frozenset({1}), missing=frozenset(), excluded=None)


# --- BranchCoverage: wave-1 §3.1 invariants 1-2 (own the pure by_line shape) --


def test_branch_coverage_accepts_a_well_formed_by_line_mapping():
    branches = BranchCoverage(by_line={1: (1, 2), 5: (0, 1)})
    assert branches.by_line == {1: (1, 2), 5: (0, 1)}


def test_branch_coverage_accepts_an_empty_by_line_mapping():
    # The branch-free-file-in-a-branch-tracking-artifact shape (§3.2):
    # capable format, zero branches on THIS file, not "cannot express".
    assert BranchCoverage(by_line={}).by_line == {}


@pytest.mark.parametrize("bad_line", [0, -1, -100])
def test_branch_coverage_rejects_a_non_positive_line(bad_line: int):
    with pytest.raises(ValueError, match="non-positive"):
        BranchCoverage(by_line={bad_line: (0, 1)})


def test_branch_coverage_rejects_a_zero_total():
    # Invariant 2: a recorded line with zero total arcs is malformed, not
    # "no branches" -- that case is simply absent from by_line instead.
    with pytest.raises(ValueError, match="total=0"):
        BranchCoverage(by_line={1: (0, 0)})


def test_branch_coverage_rejects_a_negative_total():
    with pytest.raises(ValueError, match="total=-1"):
        BranchCoverage(by_line={1: (0, -1)})


def test_branch_coverage_rejects_covered_greater_than_total():
    with pytest.raises(ValueError, match="covered=3"):
        BranchCoverage(by_line={1: (3, 2)})


def test_branch_coverage_rejects_negative_covered():
    with pytest.raises(ValueError, match="covered=-1"):
        BranchCoverage(by_line={1: (-1, 2)})


def test_branch_coverage_accepts_covered_equal_to_total():
    # 0 <= covered <= total is inclusive at both ends.
    assert BranchCoverage(by_line={1: (2, 2)}).by_line == {1: (2, 2)}


def test_branch_coverage_accepts_zero_covered():
    assert BranchCoverage(by_line={1: (0, 2)}).by_line == {1: (0, 2)}


# --- FileCoverage.branches: wave-1 §3.1 invariants 3-5 (cross-bucket rules) --


def test_file_coverage_defaults_branches_to_none():
    # Every caller written before this field existed is unchanged.
    fc = FileCoverage(executed=frozenset({1}), missing=frozenset(), excluded=None)
    assert fc.branches is None


def test_file_coverage_accepts_a_branch_line_that_is_executed():
    fc = FileCoverage(
        executed=frozenset({1}),
        missing=frozenset(),
        excluded=None,
        branches=BranchCoverage(by_line={1: (1, 2)}),
    )
    assert fc.branches.by_line == {1: (1, 2)}


def test_file_coverage_accepts_a_branch_line_that_is_missing_with_zero_covered():
    fc = FileCoverage(
        executed=frozenset(),
        missing=frozenset({1}),
        excluded=None,
        branches=BranchCoverage(by_line={1: (0, 2)}),
    )
    assert fc.branches.by_line == {1: (0, 2)}


def test_file_coverage_rejects_a_branch_line_outside_executed_and_missing():
    # Invariant 3: an arc from a line the format does not consider code at
    # all is a self-inconsistent artifact.
    with pytest.raises(ValueError, match="neither .executed nor .missing"):
        FileCoverage(
            executed=frozenset({1}),
            missing=frozenset(),
            excluded=None,
            branches=BranchCoverage(by_line={99: (1, 1)}),
        )


def test_file_coverage_rejects_a_branch_line_that_is_also_excluded():
    # Invariant 4: no branch line may be in `excluded`. `excluded` is already
    # disjoint from executed/missing (an existing, earlier-checked
    # invariant), so the line reported here is confined to `excluded` alone
    # -- the minimal input that reaches invariant 4 as its OWN distinct
    # failure rather than tripping invariant 3 (or the pre-existing
    # executed/excluded disjointness check) first.
    with pytest.raises(ValueError, match="also in .excluded"):
        FileCoverage(
            executed=frozenset(),
            missing=frozenset(),
            excluded=frozenset({1}),
            branches=BranchCoverage(by_line={1: (1, 1)}),
        )


def test_file_coverage_excluded_none_skips_the_branch_excluded_check():
    # A format that cannot express exclusions (branches=None case aside)
    # must not be penalised for having nothing to cross-check against.
    fc = FileCoverage(
        executed=frozenset({1}),
        missing=frozenset(),
        excluded=None,
        branches=BranchCoverage(by_line={1: (1, 1)}),
    )
    assert fc.branches.by_line == {1: (1, 1)}


def test_file_coverage_rejects_a_missing_branch_line_with_a_nonzero_covered_count():
    # Invariant 5, the strongest anti-tamper invariant: a line that never ran
    # cannot have taken an arc.
    with pytest.raises(ValueError, match="never ran cannot have taken an arc"):
        FileCoverage(
            executed=frozenset(),
            missing=frozenset({1}),
            excluded=None,
            branches=BranchCoverage(by_line={1: (1, 2)}),
        )


def test_two_fixtures_with_identical_lines_but_different_branches_are_not_equal():
    # branches participates in dataclass equality like every other field.
    no_branches = FileCoverage(executed=frozenset({1}), missing=frozenset(), excluded=None)
    with_branches = FileCoverage(
        executed=frozenset({1}),
        missing=frozenset(),
        excluded=None,
        branches=BranchCoverage(by_line={}),
    )
    assert no_branches != with_branches
