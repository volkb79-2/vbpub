"""O4 — ``check_empty_coverage`` distinguishes a coverage artifact that
measured ZERO files (vacuous — ``NO_MEASUREMENT``/``EMPTY_COVERAGE``) from a
non-empty artifact whose files report empty executed sets (a legitimate,
if unusual, measurement that must reach evaluation normally).

A-093's interface requirement: this is a NAMED, independently callable guard
— ``check_empty_coverage(profile) -> None`` — reachable without going
through a full parse-and-evaluate call, the same shape as
:func:`assay.measurability.check_dirty_tree` /
:func:`assay.measurability.check_base_is_head`. P05's own O4 calls this
directly.

Negative: conflating "zero files" with "zero executed lines" either emits a
misleading coverage FAIL (treating a genuinely empty artifact as a 0%
measurement) or hides a legitimate 0% measurement (treating a real
zero-executed file as if nothing was measured at all).
"""

from __future__ import annotations

import inspect

import pytest

from assay.coverage import (
    CoverageProfile,
    FileCoverage,
    check_empty_coverage,
    load_coverage_profile,
)
from assay.errors import AssayError, Outcome, ReasonCode


def test_check_empty_coverage_is_independently_callable_with_the_documented_signature():
    # A-093's exact interface requirement: reachable without a full
    # parse-and-evaluate call, and named (not a lambda or a partial).
    signature = inspect.signature(check_empty_coverage)
    assert list(signature.parameters) == ["profile"]
    assert signature.return_annotation in (None, "None")


def test_zero_files_profile_raises_no_measurement_empty_coverage():
    profile = CoverageProfile(files={})

    with pytest.raises(AssayError) as excinfo:
        check_empty_coverage(profile)

    assert excinfo.value.outcome is Outcome.NO_MEASUREMENT
    assert excinfo.value.reason_code is ReasonCode.EMPTY_COVERAGE


def test_nonzero_files_with_empty_executed_sets_does_not_raise():
    # The exact conflation O4's negative warns against: a real file, measured,
    # simply never ran. That is a legitimate 0% -- not a vacuous artifact.
    profile = CoverageProfile(
        files={
            "a.py": FileCoverage(
                executed=frozenset(), missing=frozenset({1, 2, 3}), excluded=frozenset()
            )
        }
    )
    check_empty_coverage(profile)  # must not raise


def test_real_zero_files_artifact_parsed_end_to_end_raises():
    profile = load_coverage_profile('{"files": {}}', declared_format="coverage-py-json")
    with pytest.raises(AssayError) as excinfo:
        check_empty_coverage(profile)
    assert excinfo.value.reason_code is ReasonCode.EMPTY_COVERAGE


def test_real_nonzero_files_zero_executed_artifact_parsed_end_to_end_reaches_evaluation():
    profile = load_coverage_profile(
        '{"files": {"a.py": {"executed_lines": [], "missing_lines": [1, 2]}}}',
        declared_format="coverage-py-json",
    )
    check_empty_coverage(profile)  # must not raise -- reaches evaluation
    assert profile.files["a.py"].executed == frozenset()
