"""O4 -- four hand-written full verdict artifacts distinguish never declared,
declared-but-missing, current, and stale evidence, and each validates
independently against the shipped schema v2.

Built directly from ``EvidenceDeclaration``/``Evidence``/``Claim``/``Verdict``
here -- never through :mod:`assay.attestation`'s own orchestration (this
module is P09's ``test_verdict_canary_artifacts.py``'s own pattern, restated
for evidence): the model's own construction is the independent oracle the
schema is checked against, and the schema is the independent oracle the
model's serialisation is checked against. Neither is generated from the
other.

The negative this defends (O4, verbatim): *collapsing missing with undeclared
or stale with missing makes paired artifacts equal or producer output
differ.* The dedicated "no two of the four fixtures collapse" test below
makes that concrete.
"""

from __future__ import annotations

import json

from conftest import evidence_verdict_fixture, why_invalid
from jsonschema import Draft202012Validator

from assay.errors import Outcome, ReasonCode
from assay.verdict import Claim, Evidence, EvidenceDeclaration, Verdict

BASE = {
    "lane": "package",
    "assay_version": "0.1.0",
    "declared_rigor": ("R0",),
    "argv_declared": ("pytest", "tests", "-q"),
    "argv_appended": (),
    "argv_effective": ("pytest", "tests", "-q"),
    "env_declared": {},
    "env_effective": {},
    "scope": "S1",
    "enforcement": "gate",
}

R0_PASS = Claim(rigor="R0", source="computed", status=Outcome.PASS, verified_by_assay=True)


def _validate(document: dict, validator: Draft202012Validator) -> None:
    assert why_invalid(validator, document) == []


# --- the four terminal shapes, each matched against its own fixture ---------


def test_never_declared_matches_the_hand_written_fixture(validator: Draft202012Validator):
    verdict = Verdict(
        **BASE,
        commit="1" * 40,
        outcome=Outcome.PASS,
        started="2026-08-07T14:00:00+00:00",
        ended="2026-08-07T14:00:05+00:00",
        declared_evidence=(),
        claims=(R0_PASS,),
        evidence=(),
    )

    document = json.loads(verdict.to_json())
    assert document == evidence_verdict_fixture("evidence_never_declared")
    _validate(document, validator)


def test_declared_but_missing_matches_the_hand_written_fixture(
    validator: Draft202012Validator,
):
    evidence = Evidence(
        source="attested",
        key="adversarial-review",
        status=Outcome.NO_MEASUREMENT,
        verified_by_assay=False,
        reason_code=ReasonCode.MISSING_ATTESTATION,
    )
    verdict = Verdict(
        **BASE,
        commit="2" * 40,
        outcome=Outcome.NO_MEASUREMENT,
        reason_code=ReasonCode.MISSING_ATTESTATION,
        started="2026-08-07T14:05:00+00:00",
        ended="2026-08-07T14:05:05+00:00",
        declared_evidence=(EvidenceDeclaration(source="attested", key="adversarial-review"),),
        claims=(R0_PASS,),
        evidence=(evidence,),
    )

    document = json.loads(verdict.to_json())
    assert document == evidence_verdict_fixture("evidence_declared_missing")
    _validate(document, validator)


def test_current_matches_the_hand_written_fixture(validator: Draft202012Validator):
    evidence = Evidence(
        source="attested",
        key="adversarial-review",
        status=Outcome.PASS,
        verified_by_assay=False,
        producer="adversarial-review-bot-v3",
        attested_commit="4" * 40,
        reviewed_paths=("src/assay/example.py",),
    )
    verdict = Verdict(
        **BASE,
        commit="3" * 40,
        outcome=Outcome.PASS,
        started="2026-08-07T14:10:00+00:00",
        ended="2026-08-07T14:10:05+00:00",
        declared_evidence=(EvidenceDeclaration(source="attested", key="adversarial-review"),),
        claims=(R0_PASS,),
        evidence=(evidence,),
    )

    document = json.loads(verdict.to_json())
    assert document == evidence_verdict_fixture("evidence_current")
    _validate(document, validator)


def test_stale_matches_the_hand_written_fixture(validator: Draft202012Validator):
    evidence = Evidence(
        source="attested",
        key="adversarial-review",
        status=Outcome.NO_MEASUREMENT,
        verified_by_assay=False,
        reason_code=ReasonCode.STALE_ATTESTATION,
        producer="adversarial-review-bot-v3",
        attested_commit="6" * 40,
        reviewed_paths=("src/assay/example.py",),
    )
    verdict = Verdict(
        **BASE,
        commit="5" * 40,
        outcome=Outcome.NO_MEASUREMENT,
        reason_code=ReasonCode.STALE_ATTESTATION,
        started="2026-08-07T14:15:00+00:00",
        ended="2026-08-07T14:15:05+00:00",
        declared_evidence=(EvidenceDeclaration(source="attested", key="adversarial-review"),),
        claims=(R0_PASS,),
        evidence=(evidence,),
    )

    document = json.loads(verdict.to_json())
    assert document == evidence_verdict_fixture("evidence_stale")
    _validate(document, validator)


# --- O4's own negative: no two of the four fixtures may collapse ------------


def test_no_two_of_the_four_fixtures_are_equal():
    names = (
        "evidence_never_declared",
        "evidence_declared_missing",
        "evidence_current",
        "evidence_stale",
    )
    documents = {name: evidence_verdict_fixture(name) for name in names}
    for i, left in enumerate(names):
        for right in names[i + 1 :]:
            # commit/timestamps differ across all four by construction, so
            # compare the parts that matter: the evidence array itself and
            # the verdict-level outcome/reason_code -- what a naive collapse
            # (missing==undeclared, or stale==missing) would wrongly equate.
            assert documents[left]["evidence"] != documents[right]["evidence"], (
                f"{left} and {right} carry the same evidence array"
            )


def test_missing_and_stale_carry_different_reason_codes_despite_both_being_no_measurement():
    missing = evidence_verdict_fixture("evidence_declared_missing")
    stale = evidence_verdict_fixture("evidence_stale")

    assert missing["outcome"] == stale["outcome"] == "NO_MEASUREMENT"
    assert missing["evidence"][0]["reason_code"] == "MISSING_ATTESTATION"
    assert stale["evidence"][0]["reason_code"] == "STALE_ATTESTATION"
    # missing carries no attestation payload at all; stale carries the full
    # payload -- the exact distinction a "collapse missing with stale" bug
    # would erase.
    assert "producer" not in missing["evidence"][0]
    assert "producer" in stale["evidence"][0]


def test_never_declared_and_declared_missing_are_distinguishable():
    never = evidence_verdict_fixture("evidence_never_declared")
    missing = evidence_verdict_fixture("evidence_declared_missing")

    assert never["declared_evidence"] == []
    assert never["evidence"] == []
    assert missing["declared_evidence"] != []
    assert missing["evidence"] != []
    assert never["outcome"] != missing["outcome"]
