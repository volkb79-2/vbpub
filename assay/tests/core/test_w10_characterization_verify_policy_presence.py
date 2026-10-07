"""W10 step 1: characterization of `verify._check_judgment_matches_claims` (I3, verify side).

The raw-document twin of the producer rule pinned in
``test_w10_characterization_verdict_policy_presence.py``. Its wording is its
OWN, never the model's (see the function's docstring), so these pins assert
each failure string byte for byte, and the full `failures` list, for R1, R2 and
R3, orphan and missing, on hand-built raw documents where one operand decides
each case. They also pin the raw operands the future `_raw_claim` and
`_raw_policy_iff_attempted` helpers must keep: a claim is found by
``isinstance(item, dict) and item.get("rigor") == rigor`` (first match wins),
"judged" is KEY presence (a ``None`` payload still counts), and a non-list
``claims`` short-circuits with no failure. Never edited after the I3 helpers
land (W10 brief, step 1).
"""

from __future__ import annotations

import pytest

from assay import verify

R1_ORPHAN = (
    "judgment.r1 is declared without a corresponding R1 coverage "
    "claim or BRANCH_UNAVAILABLE/TARGET_NOT_MEASURED terminal"
)
R1_MISSING = (
    "an R1 coverage claim or BRANCH_UNAVAILABLE/TARGET_NOT_MEASURED "
    "terminal is declared without a corresponding judgment.r1"
)
R2_ORPHAN = (
    "judgment.r2 is declared without a corresponding R2 mutation claim "
    "or unsupported-capability terminal"
)
R2_MISSING = (
    "an R2 mutation claim or unsupported-capability terminal is "
    "declared without a corresponding judgment.r2"
)
R3_ORPHAN = "judgment.r3 is declared without a corresponding R3 canary claim"
R3_MISSING = "an R3 canary claim is declared without a corresponding judgment.r3"


def _failures(document: dict) -> list[str]:
    failures: list[str] = []
    verify._check_judgment_matches_claims(document, failures)
    return failures


def _claim(rigor: str, **fields) -> dict:
    return {"rigor": rigor, **fields}


# --- R1 ---------------------------------------------------------------------


def test_r1_policy_and_coverage_claim_together_add_nothing():
    document = {"claims": [_claim("R1", coverage={})], "judgment": {"r1": {}}}
    assert _failures(document) == []


def test_r1_orphan_when_no_r1_claim_exists():
    document = {"claims": [_claim("R0")], "judgment": {"r1": {}}}
    assert _failures(document) == [R1_ORPHAN]


def test_r1_orphan_when_the_claim_has_neither_coverage_nor_a_terminal():
    document = {"claims": [_claim("R1")], "judgment": {"r1": {}}}
    assert _failures(document) == [R1_ORPHAN]


def test_r1_missing_when_the_judgment_has_no_r1_key():
    document = {"claims": [_claim("R1", coverage={})], "judgment": {}}
    assert _failures(document) == [R1_MISSING]


def test_r1_missing_when_the_judgment_is_none():
    document = {"claims": [_claim("R1", coverage={})], "judgment": None}
    assert _failures(document) == [R1_MISSING]


def test_r1_judged_is_key_presence_so_a_none_payload_still_counts():
    document = {"claims": [_claim("R1", coverage=None)], "judgment": None}
    assert _failures(document) == [R1_MISSING]


@pytest.mark.parametrize("reason", ["BRANCH_UNAVAILABLE", "TARGET_NOT_MEASURED"])
def test_r1_payload_free_terminals_are_attempted(reason):
    claims = [_claim("R1", reason_code=reason)]
    assert _failures({"claims": claims, "judgment": {}}) == [R1_MISSING]
    assert _failures({"claims": claims, "judgment": {"r1": {}}}) == []


def test_r1_another_reason_code_is_not_attempted():
    claims = [_claim("R1", reason_code="EMPTY_COVERAGE")]
    assert _failures({"claims": claims, "judgment": {}}) == []
    assert _failures({"claims": claims, "judgment": {"r1": {}}}) == [R1_ORPHAN]


# --- R2 ---------------------------------------------------------------------


def test_r2_policy_and_mutation_claim_together_add_nothing():
    document = {"claims": [_claim("R2", mutation=None)], "judgment": {"r2": None}}
    assert _failures(document) == []


def test_r2_orphan_when_no_r2_claim_exists():
    document = {"claims": [_claim("R0")], "judgment": {"r2": {}}}
    assert _failures(document) == [R2_ORPHAN]


def test_r2_missing_when_the_judgment_has_no_r2_key():
    document = {"claims": [_claim("R2", mutation={})], "judgment": {}}
    assert _failures(document) == [R2_MISSING]


def test_r2_missing_when_the_judgment_is_not_a_dict():
    document = {"claims": [_claim("R2", mutation={})], "judgment": "x"}
    assert _failures(document) == [R2_MISSING]


def test_r2_unsupported_terminal_is_attempted():
    claims = [_claim("R2", reason_code="MUTATION_UNSUPPORTED")]
    assert _failures({"claims": claims, "judgment": {}}) == [R2_MISSING]
    assert _failures({"claims": claims, "judgment": {"r2": {}}}) == []


def test_r2_another_reason_code_is_not_attempted():
    claims = [_claim("R2", reason_code="EMPTY_COVERAGE")]
    assert _failures({"claims": claims, "judgment": {}}) == []
    assert _failures({"claims": claims, "judgment": {"r2": {}}}) == [R2_ORPHAN]


# --- R3 ---------------------------------------------------------------------


def test_r3_policy_and_canary_claim_together_add_nothing():
    document = {"claims": [_claim("R3", canary=None)], "judgment": {"r3": None}}
    assert _failures(document) == []


def test_r3_orphan_when_the_claim_has_no_canary_key():
    document = {"claims": [_claim("R3")], "judgment": {"r3": {}}}
    assert _failures(document) == [R3_ORPHAN]


def test_r3_missing_when_the_judgment_has_no_r3_key():
    document = {"claims": [_claim("R3", canary={})], "judgment": {"r1": {}}}
    assert _failures(document) == [R1_ORPHAN, R3_MISSING]


# --- shared operands of the raw claim lookup -------------------------------


def test_the_orphan_is_reported_before_the_missing_in_one_document():
    document = {"claims": [_claim("R3", canary={})], "judgment": {"r1": {}}}
    assert _failures(document) == [R1_ORPHAN, R3_MISSING]


def test_non_dict_claim_entries_are_skipped_when_finding_a_claim():
    document = {"claims": ["x", 1, None, _claim("R3", canary={})], "judgment": {}}
    assert _failures(document) == [R3_MISSING]


def test_the_first_claim_of_a_rigor_wins():
    first_bare = {
        "claims": [_claim("R3"), _claim("R3", canary={})],
        "judgment": {"r3": {}},
    }
    assert _failures(first_bare) == [R3_ORPHAN]
    first_judged = {
        "claims": [_claim("R3", canary={}), _claim("R3")],
        "judgment": {},
    }
    assert _failures(first_judged) == [R3_MISSING]


@pytest.mark.parametrize("claims", ["x", None, {"rigor": "R1"}, 1])
def test_a_non_list_claims_value_short_circuits_with_no_failure(claims):
    assert _failures({"claims": claims, "judgment": {"r1": {}, "r3": {}}}) == []


def test_a_document_without_claims_short_circuits():
    assert _failures({"judgment": {"r1": {}}}) == []


# --- the model-side rederivation lookups (the future `_claim_of` sites) -----
#
# Each `_check_rN_rederivation` finds ITS claim with `next(..., None)` and
# returns silently when there is none. The claim-present behaviour of every
# one of them is pinned by the verify conformance suites (for example
# `test_verdict_conformance.py` and `test_verify_raw_b105.py`); here only the
# lookup's own miss is pinned, one rigor at a time.


def _r0_only_verdict():
    from assay.errors import Outcome
    from assay.verdict import Claim, Verdict

    return Verdict(
        lane="package",
        commit="b" * 40,
        started="2026-08-07T09:00:00+00:00",
        ended="2026-08-07T09:00:01+00:00",
        assay_version="0.1.0",
        declared_evidence=(),
        argv_declared=("pytest", "-q"),
        argv_appended=(),
        argv_effective=("pytest", "-q"),
        env_declared={},
        env_effective={},
        env_passthrough=(),
        scope="S1",
        enforcement="gate",
        outcome=Outcome.PASS,
        declared_rigor=("R0",),
        claims=(
            Claim(rigor="R0", source="computed", status=Outcome.PASS, verified_by_assay=True),
        ),
    )


@pytest.mark.parametrize(
    "check",
    [
        verify._check_r1_rederivation,
        verify._check_r2_rederivation,
        verify._check_r3_rederivation,
        verify._check_r4_rederivation,
    ],
)
def test_a_rederivation_with_no_claim_of_its_rigor_reports_nothing(check):
    failures: list[str] = []
    check(_r0_only_verdict(), failures)
    assert failures == []
