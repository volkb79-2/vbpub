"""W10 step 1: strict-int operands in verify.py's mutation checks (I2, verify side).

`_check_mutation_payload_shapes` reads `total`, `candidate_count` and the
policy's `max_mutants` as strict ints (a ``bool`` is not one). Each operand is
varied alone against a payload whose five buckets are empty, so the
"says N but lists 0" line is part of every expected list. Never edited after
the consolidation lands (W10 brief, step 1).
"""

from __future__ import annotations

import pytest

from assay import verify


def _shape_failures(total, candidate_count, max_mutants, reason=None) -> list[str]:
    mutation = {name: [] for name in verify.MUTATION_BUCKETS}
    mutation["total"] = total
    mutation["candidate_count"] = candidate_count
    claim = {"rigor": "R2", "mutation": mutation}
    if reason is not None:
        claim["reason_code"] = reason
    document = {"claims": [claim], "judgment": {"r2": {"max_mutants": max_mutants}}}
    failures: list[str] = []
    verify._check_mutation_payload_shapes(document, failures)
    return failures


def _says(total: int) -> str:
    return (
        f"the R2 mutation payload says {total} attempted mutant(s) but "
        f"lists 0 identity/identities across its five buckets"
    )


def _above_cap(total: int, cap: int) -> str:
    return (
        f"the R2 payload attempted {total} mutant(s) against a declared "
        f"ceiling of {cap}; a completed run above the cap means "
        f"the cap bounded nothing"
    )


def test_a_matching_zero_total_reports_nothing():
    assert _shape_failures(0, 0, 5) == []


def test_an_int_total_that_disagrees_with_the_bucket_sizes_is_named():
    assert _shape_failures(3, 3, 5) == [_says(3)]


@pytest.mark.parametrize("total", [True, 1.5, "1", None])
def test_a_total_that_is_not_a_strict_int_skips_the_bucket_sum_and_the_ceiling(total):
    assert _shape_failures(total, 3, 5) == []


def test_the_ceiling_is_reported_after_the_bucket_sum_when_total_exceeds_it():
    assert _shape_failures(6, 6, 5) == [_says(6), _above_cap(6, 5)]


def test_a_bool_total_never_reaches_the_ceiling_comparison():
    assert _shape_failures(True, 6, 5) == []


@pytest.mark.parametrize("candidate_count", [True, 6.0, "6", None])
def test_a_candidate_count_that_is_not_a_strict_int_ends_the_check_after_the_bucket_sum(
    candidate_count,
):
    assert _shape_failures(6, candidate_count, 5) == [_says(6)]


@pytest.mark.parametrize("max_mutants", [True, 5.0, "5", None])
def test_a_ceiling_that_is_not_a_strict_int_ends_the_check_after_the_bucket_sum(
    max_mutants,
):
    assert _shape_failures(6, 6, max_mutants) == [_says(6)]


def test_an_unattempted_remainder_is_named_when_the_counts_are_strict_ints():
    assert _shape_failures(3, 4, 5) == [
        _says(3),
        "the R2 payload observed 4 candidate(s) and attempted 3 under a "
        "declared ceiling of 5; a native run attempts every candidate it "
        "observes, and the only native shape with an unattempted remainder "
        "is the pre-submission refusal (zero attempted)",
    ]


def test_the_pre_submission_refusal_shape_needs_the_reason_and_the_exact_cap():
    assert _shape_failures(0, 6, 5, "MUTANT_LIMIT_EXCEEDED") == []
    assert _shape_failures(0, 6, 5) == [
        "the R2 payload has the pre-submission refusal shape but the claim "
        "reports None rather than MUTANT_LIMIT_EXCEEDED"
    ]
    assert _shape_failures(0, 5, 5, "MUTANT_LIMIT_EXCEEDED") == [
        "the R2 payload refuses before submission after seeing 5 candidate(s), "
        "which is not one more than the declared ceiling of 5; a refusal that "
        "does not sit exactly at the cap proves nothing about the cap"
    ]
