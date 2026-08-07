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

from assay.coverage_parsers.model import FileCoverage


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
    FileCoverage(executed=frozenset({1}), missing=frozenset({2}), excluded=None)
