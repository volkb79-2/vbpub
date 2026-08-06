"""O2 — the schema REJECTS a malformed verdict, as a validation FAILURE.

The negative this defends: *the schema accepts everything, so "six verdicts
validate" is green against a schema that requires nothing — and a
NO_MEASUREMENT artifact carrying `pct: 100.0` rebuilds the exact ambiguity this
project exists to remove, one layer up (A-025).*

Two disciplines make these rejections real rather than decorative:

* **Every reject test asserts the untouched document VALIDATES, in the same
  body.** A schema that rejects everything therefore fails here too, which is
  the same defence the config suite uses.
* **The rejection instances are raw dicts, never built through `Verdict`.** The
  model refuses to construct most of them, so routing them through it would mean
  the validator was never reached and the schema was never tested. The model's
  own refusals are asserted separately, at the bottom.
"""

from __future__ import annotations

import pytest
from conftest import verdict_fixture, why_invalid
from jsonschema import Draft202012Validator

from assay.errors import REASON_CODES, Outcome, ReasonCode
from assay.verdict import Claim, Coverage

#: A plausible, well-formed coverage payload. Its presence is the ONLY thing
#: wrong with the documents it is planted into below.
ZEROED_COVERAGE = {
    "covered": 0,
    "changed_executable": 0,
    "pct": 100.0,
    "considered": 0,
}

NON_PASS = [outcome.value for outcome in Outcome if outcome is not Outcome.PASS]


# --- A-025: no percentage exists unless a measurement produced one ------------


def test_a_no_measurement_verdict_carrying_a_coverage_block_is_rejected(
    validator: Draft202012Validator,
):
    """The bug this whole project exists to remove, one layer up.

    `{"covered": 0, "changed_executable": 0, "pct": 100.0}` beside
    `outcome: NO_MEASUREMENT` reads as 100% to any consumer that reads `pct` and
    ignores `outcome`. The guard is what is ABSENT.
    """
    document = verdict_fixture("NO_MEASUREMENT")
    assert why_invalid(validator, document) == [], "the canonical form must validate"

    no_measurement_claim = next(
        claim for claim in document["claims"] if claim["status"] == "NO_MEASUREMENT"
    )
    no_measurement_claim["coverage"] = ZEROED_COVERAGE

    messages = why_invalid(validator, document)
    assert messages, "a NO_MEASUREMENT claim was allowed to carry pct: 100.0"


def test_the_same_coverage_block_is_fine_on_a_measured_claim(
    validator: Draft202012Validator,
):
    """Proves the rejection above is about NO_MEASUREMENT, not about the payload.

    Without this, `ZEROED_COVERAGE` could be malformed and the test above would
    pass for the wrong reason — the schema would still be accepting the real
    defect.
    """
    document = verdict_fixture("PASS")
    r1 = next(claim for claim in document["claims"] if claim["rigor"] == "R1")
    r1["coverage"] = ZEROED_COVERAGE

    assert why_invalid(validator, document) == []


def test_a_legitimate_zero_over_zero_pass_still_emits_its_numbers(
    validator: Draft202012Validator,
):
    """A-026's mirror case: the legitimate 0/0 DOES carry the block."""
    document = verdict_fixture("PASS")
    r1 = next(claim for claim in document["claims"] if claim["rigor"] == "R1")
    r1["coverage"] = dict(ZEROED_COVERAGE, considered=3)

    assert why_invalid(validator, document) == []
    assert r1["coverage"]["considered"] == 3, (
        "an empty denominator must explain itself rather than being textually "
        "identical to a broken measurement"
    )


def test_a_coverage_block_missing_considered_is_rejected(
    validator: Draft202012Validator,
):
    document = verdict_fixture("PASS")
    r1 = next(claim for claim in document["claims"] if claim["rigor"] == "R1")
    assert why_invalid(validator, document) == []

    del r1["coverage"]["considered"]
    assert not validator.is_valid(document)


@pytest.mark.parametrize("field", ["covered", "changed_executable", "pct"])
def test_a_coverage_block_missing_a_number_is_rejected(
    field: str, validator: Draft202012Validator
):
    document = verdict_fixture("PASS")
    r1 = next(claim for claim in document["claims"] if claim["rigor"] == "R1")
    assert why_invalid(validator, document) == []

    del r1["coverage"][field]
    assert not validator.is_valid(document)


def test_an_unknown_coverage_key_is_rejected(validator: Draft202012Validator):
    document = verdict_fixture("PASS")
    r1 = next(claim for claim in document["claims"] if claim["rigor"] == "R1")
    assert why_invalid(validator, document) == []

    r1["coverage"]["percent"] = 100.0
    assert not validator.is_valid(document)


# --- A-022: reason_code is required on every non-PASS outcome ------------------


@pytest.mark.parametrize("outcome", NON_PASS)
def test_a_non_pass_verdict_missing_reason_code_is_rejected(
    outcome: str, validator: Draft202012Validator
):
    document = verdict_fixture(outcome)
    assert why_invalid(validator, document) == [], "the canonical form must validate"
    assert document["reason_code"], "this fixture must carry one to begin with"

    del document["reason_code"]

    messages = why_invalid(validator, document)
    assert messages, f"{outcome} was allowed to name no cause"
    assert any("reason_code" in message for message in messages), messages


@pytest.mark.parametrize("outcome", NON_PASS)
def test_a_non_pass_claim_missing_reason_code_is_rejected(
    outcome: str, validator: Draft202012Validator
):
    """The same rule one level down — a FAIL claim must name its cause too."""
    code = sorted(REASON_CODES[Outcome(outcome)])[0].value
    document = verdict_fixture("PASS")
    document["declared_rigor"] = ["R0"]
    claim = {
        "rigor": "R0",
        "source": "computed",
        "status": outcome,
        "verified_by_assay": True,
        "reason_code": code,
    }
    document["claims"] = [claim]
    assert why_invalid(validator, document) == [], "the coded form must validate"

    del claim["reason_code"]

    messages = why_invalid(validator, document)
    assert messages, f"a {outcome} claim was allowed to name no cause"
    assert any("reason_code" in message for message in messages), messages


def test_a_null_reason_code_is_rejected_not_treated_as_absent(
    validator: Draft202012Validator,
):
    """A-051: omitted, never null.

    `null` invites a consumer to treat absence and emptiness as different
    states, which is the ambiguity the omission exists to remove.
    """
    document = verdict_fixture("PASS")
    assert why_invalid(validator, document) == []

    document["reason_code"] = None
    assert not validator.is_valid(document)


def test_a_pass_verdict_carrying_a_reason_code_is_rejected(
    validator: Draft202012Validator,
):
    document = verdict_fixture("PASS")
    assert why_invalid(validator, document) == []

    document["reason_code"] = "UNCOVERED_LINES"
    assert not validator.is_valid(document)


# --- the model refuses to build what the schema refuses to accept -------------


def test_the_model_refuses_a_no_measurement_claim_with_coverage():
    # ACCEPT half, same body: the identical claim WITHOUT the payload is fine.
    Claim(
        rigor="R1",
        source="computed",
        status=Outcome.NO_MEASUREMENT,
        verified_by_assay=True,
        reason_code=ReasonCode.DIRTY_TREE,
    )

    with pytest.raises(ValueError, match="omitted, not zeroed"):
        Claim(
            rigor="R1",
            source="computed",
            status=Outcome.NO_MEASUREMENT,
            verified_by_assay=True,
            reason_code=ReasonCode.DIRTY_TREE,
            coverage=Coverage(
                covered=0, changed_executable=0, pct=100.0, considered=0
            ),
        )


@pytest.mark.parametrize("rigor", ["R0", "R2", "R3"])
def test_the_model_refuses_a_coverage_payload_on_a_non_r1_claim(rigor: str):
    Claim(rigor=rigor, source="computed", status=Outcome.PASS, verified_by_assay=True)

    with pytest.raises(ValueError, match="belongs to the R1 claim"):
        Claim(
            rigor=rigor,
            source="computed",
            status=Outcome.PASS,
            verified_by_assay=True,
            coverage=Coverage(
                covered=1, changed_executable=1, pct=100.0, considered=1
            ),
        )


@pytest.mark.parametrize("outcome", [o for o in Outcome if o is not Outcome.PASS])
def test_the_model_refuses_a_non_pass_claim_with_no_reason_code(outcome: Outcome):
    code = sorted(REASON_CODES[outcome])[0]
    Claim(rigor="R0", source="computed", status=outcome, verified_by_assay=True,
          reason_code=code)

    with pytest.raises(ValueError, match="requires a reason_code"):
        Claim(rigor="R0", source="computed", status=outcome, verified_by_assay=True)


def test_the_model_refuses_a_pass_claim_carrying_a_reason_code():
    with pytest.raises(ValueError, match="PASS carries no reason_code"):
        Claim(
            rigor="R0",
            source="computed",
            status=Outcome.PASS,
            verified_by_assay=True,
            reason_code=ReasonCode.UNCOVERED_LINES,
        )


@pytest.mark.parametrize(
    "kwargs,match",
    [
        ({"covered": -1}, "must not be negative"),
        ({"changed_executable": -2}, "must not be negative"),
        ({"considered": -3}, "must not be negative"),
        ({"pct": 101.0}, "between 0 and 100"),
        ({"pct": -0.5}, "between 0 and 100"),
        ({"covered": 4}, "exceeds changed_executable"),
        ({"covered": True}, "must be an integer"),
        ({"pct": "100"}, "must be a number"),
    ],
)
def test_the_coverage_payload_refuses_an_impossible_measurement(kwargs, match):
    base = {"covered": 3, "changed_executable": 3, "pct": 100.0, "considered": 1}
    assert Coverage(**base).pct == 100.0  # the untouched form builds

    with pytest.raises(ValueError, match=match):
        Coverage(**{**base, **kwargs})
