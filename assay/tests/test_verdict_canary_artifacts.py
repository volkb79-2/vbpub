"""O2/A-108/A-109 -- the canary result attaches as ``Claim.canary``, gated to
``rigor == "R3"`` (the exact pattern already enforced for
``Claim.coverage``/``rigor == "R1"``), and records control outcome,
transformed outcome, transform identity, and expected versus observed
reason in an INDEPENDENTLY WRITTEN, schema-valid artifact.

Four hand-written fixtures (``tests/fixtures/verdicts/r3_*.json``), one per
A-109 terminal case, built directly from ``Claim``/``CanaryResult``/
``Verdict`` here -- never through :mod:`assay.canary`'s own orchestration
(this module is P07's ``test_verdict_span_attribution_artifacts.py``'s own
pattern, restated for R3): the model's own construction is the independent
oracle the schema is checked against, and the schema is the independent
oracle the model's serialisation is checked against. Neither is generated
from the other.

The negative this defends (O2, verbatim): *recording only 'rejected=true'
lets the wrong failure cause pass and differs from the expected artifact.*
Every fixture below carries the FULL ``canary`` block, and every reject test
proves a narrower record is caught, either by the model refusing to
construct it or by the schema refusing to validate it.
"""

from __future__ import annotations

import json

import pytest
from conftest import canary_verdict_fixture, why_invalid
from jsonschema import Draft202012Validator

from assay.errors import Outcome, ReasonCode
from assay.verdict import CanaryResult, Claim, Judgment, JudgmentR3, Verdict

BASE = {
    "lane": "package",
    "assay_version": "0.1.0",
    "declared_rigor": ("R0", "R3"),
    "declared_evidence": (),
    "argv_declared": ("pytest", "tests", "-q", "--cov=pkg", "--cov-report=json:cov.json"),
    "argv_appended": (),
    "argv_effective": ("pytest", "tests", "-q", "--cov=pkg", "--cov-report=json:cov.json"),
    "env_declared": {},
    "env_effective": {},
    "scope": "S1",
    "enforcement": "gate",
}

R0_PASS = Claim(rigor="R0", source="computed", status=Outcome.PASS, verified_by_assay=True)


def _validate(document: dict, validator: Draft202012Validator) -> None:
    assert why_invalid(validator, document) == []


# --- the four A-109 terminal shapes, each matched against its own fixture -----


def test_pass_matches_the_hand_written_fixture(validator: Draft202012Validator):
    canary_result = CanaryResult(
        mechanism="uncovered-line",
        description=(
            "appended never-called `def _assay_canary_unreached` "
            "(2 uncovered lines) at end of file"
        ),
        control_outcome=Outcome.PASS,
        transformed_outcome=Outcome.FAIL,
        expected_reason_code=ReasonCode.UNCOVERED_LINES,
        observed_reason_code=ReasonCode.UNCOVERED_LINES,
    )
    r3_claim = Claim(
        rigor="R3", source="computed", status=Outcome.PASS,
        verified_by_assay=True, canary=canary_result,
    )
    verdict = Verdict(
        **BASE,
        commit="c" * 40,
        outcome=Outcome.PASS,
        started="2026-08-07T13:00:00+00:00",
        ended="2026-08-07T13:00:05+00:00",
        judgment=Judgment(r3=JudgmentR3(mechanism="uncovered-line", target="pkg/greet.py")),
        claims=(R0_PASS, r3_claim),
    )

    document = json.loads(verdict.to_json())
    assert document == canary_verdict_fixture("r3_pass")
    _validate(document, validator)


def test_canary_survived_via_unexpected_pass_matches_the_hand_written_fixture(
    validator: Draft202012Validator,
):
    canary_result = CanaryResult(
        mechanism="uncovered-line",
        description=(
            "appended never-called `def _assay_canary_unreached` "
            "(2 uncovered lines) at end of file"
        ),
        control_outcome=Outcome.PASS,
        transformed_outcome=Outcome.PASS,  # the bad case unexpectedly passed
        expected_reason_code=ReasonCode.UNCOVERED_LINES,
        observed_reason_code=None,
    )
    r3_claim = Claim(
        rigor="R3", source="computed", status=Outcome.FAIL,
        verified_by_assay=True, reason_code=ReasonCode.CANARY_SURVIVED,
        canary=canary_result,
    )
    verdict = Verdict(
        **BASE,
        commit="d" * 40,
        outcome=Outcome.FAIL,
        reason_code=ReasonCode.CANARY_SURVIVED,
        started="2026-08-07T13:05:00+00:00",
        ended="2026-08-07T13:05:05+00:00",
        judgment=Judgment(r3=JudgmentR3(mechanism="uncovered-line", target="pkg/greet.py")),
        claims=(R0_PASS, r3_claim),
    )

    document = json.loads(verdict.to_json())
    assert document == canary_verdict_fixture("r3_fail_canary_survived_unexpected_pass")
    _validate(document, validator)


def test_canary_survived_via_wrong_reason_matches_the_hand_written_fixture(
    validator: Draft202012Validator,
):
    canary_result = CanaryResult(
        mechanism="import-break",
        description=(
            "mislabeled: performed the uncovered-line transform under the "
            "import-break mechanism name"
        ),
        control_outcome=Outcome.PASS,
        transformed_outcome=Outcome.FAIL,
        expected_reason_code=ReasonCode.COMMAND_FAILED,
        observed_reason_code=ReasonCode.UNCOVERED_LINES,  # wrong cause
    )
    r3_claim = Claim(
        rigor="R3", source="computed", status=Outcome.FAIL,
        verified_by_assay=True, reason_code=ReasonCode.CANARY_SURVIVED,
        canary=canary_result,
    )
    verdict = Verdict(
        **BASE,
        commit="e" * 40,
        outcome=Outcome.FAIL,
        reason_code=ReasonCode.CANARY_SURVIVED,
        started="2026-08-07T13:10:00+00:00",
        ended="2026-08-07T13:10:05+00:00",
        judgment=Judgment(r3=JudgmentR3(mechanism="import-break", target="pkg/greet.py")),
        claims=(R0_PASS, r3_claim),
    )

    document = json.loads(verdict.to_json())
    assert document == canary_verdict_fixture("r3_fail_canary_survived_wrong_reason")
    _validate(document, validator)


def test_canary_inconclusive_matches_the_hand_written_fixture(
    validator: Draft202012Validator,
):
    canary_result = CanaryResult(
        mechanism="not-a-real-mechanism",
        description=(
            "'not-a-real-mechanism' is not a known canary mechanism -- "
            "expected one of ['import-break', 'uncovered-line']; nothing "
            "to judge"
        ),
        control_outcome=Outcome.PASS,
    )
    r3_claim = Claim(
        rigor="R3", source="computed", status=Outcome.INCONCLUSIVE,
        verified_by_assay=True, reason_code=ReasonCode.CANARY_INCONCLUSIVE,
        canary=canary_result,
    )
    verdict = Verdict(
        **BASE,
        commit="f" * 40,
        outcome=Outcome.INCONCLUSIVE,
        reason_code=ReasonCode.CANARY_INCONCLUSIVE,
        started="2026-08-07T13:15:00+00:00",
        ended="2026-08-07T13:15:05+00:00",
        judgment=Judgment(
            r3=JudgmentR3(mechanism="not-a-real-mechanism", target="pkg/greet.py")
        ),
        claims=(R0_PASS, r3_claim),
    )

    document = json.loads(verdict.to_json())
    assert document == canary_verdict_fixture("r3_inconclusive_canary_inconclusive")
    _validate(document, validator)


# --- the vacuity guard: recording only 'rejected=true' would differ -----------


def test_recording_only_rejected_true_would_differ_from_the_expected_artifact(
    validator: Draft202012Validator,
):
    """O2's own negative, made concrete at the SCHEMA level: a record of a
    non-PASS ``transformed_outcome`` that omits WHICH reason produced it is
    refused -- a record that cannot say why it failed cannot prove it failed
    for the intended cause, so 'rejected=true' alone is not an accepted
    shape."""
    document = canary_verdict_fixture("r3_pass")
    assert why_invalid(validator, document) == []

    del document["claims"][1]["canary"]["observed_reason_code"]
    messages = why_invalid(validator, document)
    assert messages, (
        "a non-PASS transformed_outcome without observed_reason_code was accepted"
    )


# --- A-108: the exact R1/coverage template, restated for R3 -------------------


def test_a_canary_payload_outside_the_r3_branch_is_rejected(
    validator: Draft202012Validator,
):
    """The pattern's teeth (mirrors ``test_verdict_claims.py``'s identical
    coverage/R1 test): ``canary`` is legal in the R3 branch and nowhere
    else, because the claim is closed by ``additionalProperties: false``."""
    document = canary_verdict_fixture("r3_pass")
    original_canary_block = document["claims"][1]["canary"]
    assert why_invalid(validator, document) == []

    for rigor in ("R0", "R1", "R2"):
        document["claims"][1]["rigor"] = rigor
        if rigor == "R1":
            document["declared_rigor"] = ["R0", "R1"]
        messages = why_invalid(validator, document)
        assert messages, f"a canary payload on an {rigor} claim was accepted"

    # sanity: the identical payload IS accepted back on R3.
    document["claims"][1]["rigor"] = "R3"
    document["declared_rigor"] = ["R0", "R3"]
    assert why_invalid(validator, document) == []
    assert document["claims"][1]["canary"] == original_canary_block


def test_the_model_refuses_a_canary_payload_outside_r3():
    canary_result = CanaryResult(
        mechanism="uncovered-line", description="x", control_outcome=Outcome.PASS,
    )
    with pytest.raises(ValueError, match="a canary payload belongs to the R3 claim"):
        Claim(
            rigor="R1", source="computed", status=Outcome.PASS,
            verified_by_assay=True, canary=canary_result,
        )


def test_the_model_refuses_a_canary_payload_on_a_no_measurement_claim():
    canary_result = CanaryResult(
        mechanism="uncovered-line", description="x", control_outcome=Outcome.PASS,
    )
    with pytest.raises(ValueError, match="NO_MEASUREMENT carries no canary payload"):
        Claim(
            rigor="R3", source="computed", status=Outcome.NO_MEASUREMENT,
            verified_by_assay=True, reason_code=ReasonCode.EMPTY_COVERAGE,
            canary=canary_result,
        )


def test_a_no_measurement_verdict_carrying_a_canary_block_is_rejected_by_the_schema(
    validator: Draft202012Validator,
):
    document = canary_verdict_fixture("r3_pass")
    document["claims"][1]["rigor"] = "R3"
    document["claims"][1]["status"] = "NO_MEASUREMENT"
    document["claims"][1]["reason_code"] = "EMPTY_COVERAGE"

    messages = why_invalid(validator, document)
    assert messages, "a NO_MEASUREMENT claim was allowed to carry a canary block"


def test_an_unknown_canary_key_is_rejected(validator: Draft202012Validator):
    document = canary_verdict_fixture("r3_pass")
    assert why_invalid(validator, document) == []

    document["claims"][1]["canary"]["mutants_killed"] = 7
    assert not validator.is_valid(document)
