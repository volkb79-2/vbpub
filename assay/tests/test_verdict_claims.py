"""Computed rigor claims and externally sourced evidence stay separate.

The negatives this defends: *"R2 was declared but rendered no judgement" becomes
indistinguishable from "R2 was never declared" (A-024), or an external review
can be forced into an R3 slot and laundered as assay-verified (A-033).*

**The oracle is split, and deliberately so.** The rigor-coverage rule compares
two locations inside one instance (`declared_rigor` against `claims[].rigor`),
and JSON Schema draft 2020-12 has no way to express that — there is no `$data`
and no cross-instance reference. The handoff's `escalate_if` names exactly this
case, so it is reported rather than quietly weakened: the SCHEMA enforces the
envelope shape and the attested implication; the MODEL enforces the coverage
rule at construction. Both are asserted as rejections — `not is_valid` for the
schema, `pytest.raises` for the model. Neither is ever asserted as "the model
emits one claim per level", which is the acceptance test the escalate_if
forbids.
"""

from __future__ import annotations

import pytest
from conftest import verdict_fixture, why_invalid
from jsonschema import Draft202012Validator

from assay.errors import Outcome, ReasonCode
from assay.verdict import (
    CLAIM_SOURCES,
    EVIDENCE_SOURCES,
    ROLLUP_PRECEDENCE,
    Claim,
    Coverage,
    Evidence,
    EvidenceDeclaration,
    Judgment,
    JudgmentR1,
    JudgmentR2,
    Mutation,
    Verdict,
    rollup,
)

BASE = {
    "lane": "package",
    "commit": "a" * 40,
    "started": "2026-08-06T09:00:00+00:00",
    "ended": "2026-08-06T09:00:07+00:00",
    "assay_version": "0.1.0",
    "declared_evidence": (),
    "argv_declared": ("pytest", "-q"),
    "argv_appended": (),
    "argv_effective": ("pytest", "-q"),
    "env_declared": {},
    "env_effective": {},
    "scope": "S2",
    "enforcement": "gate",
}


#: The resolved R1 policy every verdict below that renders a PASSING R1
#: claim must carry (P16): a coverage payload without the policy that judged
#: it is refused, so the two travel together here exactly as they do in a
#: real artifact.
R1_JUDGMENT = Judgment(
    r1=JudgmentR1(
        language="python",
        source_roots=("src",),
        coverage_format="coverage-py-json",
        coverage_artifact="cov.json",
        fail_under=100.0,
        allow_excluded=False,
        base="b" * 40,
    )
)


def passing(rigor: str, source: str = "computed") -> Claim:
    """A PASSING claim for *rigor*, carrying the payload that level's own
    producer always attaches to a pass (P16 review): R1's ``Coverage``,
    R2's ``Mutation``. A judged status with no payload behind it is refused
    at construction, because it is precisely the artifact ``assay verify``
    cannot re-derive and would therefore have to take on trust.
    """
    payload: dict[str, object] = {}
    if rigor == "R1":
        payload["coverage"] = Coverage(
            covered=1,
            changed_executable=1,
            pct=100.0,
            considered=1,
            missing_lines={},
            files_missing_coverage=(),
        )
    elif rigor == "R2":
        payload["mutation"] = Mutation(total=1, killed=1)
    return Claim(
        rigor=rigor,
        source=source,
        status=Outcome.PASS,
        verified_by_assay=source == "computed",
        **payload,
    )


def claim_dict(**overrides) -> dict:
    claim = {
        "rigor": "R0",
        "source": "computed",
        "status": "PASS",
        "verified_by_assay": True,
    }
    claim.update(overrides)
    return claim


def document_with(claims: list[dict]) -> dict:
    document = verdict_fixture("PASS")
    document["claims"] = claims
    levels = sorted({claim["rigor"] for claim in claims if "rigor" in claim})
    document["declared_rigor"] = levels or ["R0"]
    return document


# --- provenance is not rigor -------------------------------------------------


def test_a_claim_is_computed_only(validator: Draft202012Validator):
    assert why_invalid(validator, document_with([claim_dict()])) == []
    for external in EVIDENCE_SOURCES:
        assert not validator.is_valid(
            document_with([claim_dict(source=external, verified_by_assay=False)])
        )


def test_the_three_tiers_are_exactly_the_declared_vocabulary():
    assert CLAIM_SOURCES == ("computed",)
    assert EVIDENCE_SOURCES == ("adjudicated", "attested")


@pytest.mark.parametrize(
    "field", ["rigor", "source", "status", "verified_by_assay"]
)
def test_a_claim_missing_an_envelope_field_is_rejected(
    field: str, validator: Draft202012Validator
):
    claim = claim_dict()
    assert why_invalid(validator, document_with([claim])) == []

    del claim[field]
    messages = why_invalid(validator, document_with([claim]))
    assert messages, f"a claim with no {field} was accepted"
    assert any(field in message for message in messages), messages


def test_an_unknown_claim_source_is_rejected(validator: Draft202012Validator):
    assert why_invalid(validator, document_with([claim_dict()])) == []

    assert not validator.is_valid(
        document_with([claim_dict(source="llm-reviewed")])
    )


def test_an_unknown_claim_key_is_rejected(validator: Draft202012Validator):
    """The additive-branch pattern: a payload is accepted only where a branch
    names it. Nobody has to write a rule against `mutants_killed` for it to be
    refused today — P10 adds the R2 branch that makes it legal."""
    assert why_invalid(validator, document_with([claim_dict()])) == []

    assert not validator.is_valid(
        document_with([claim_dict(mutants_killed=7)])
    )


def test_an_unknown_rigor_level_is_rejected(validator: Draft202012Validator):
    assert not validator.is_valid(document_with([claim_dict(rigor="R9")]))


@pytest.mark.parametrize("rigor", ["R0", "R2", "R3"])
def test_a_coverage_payload_outside_the_r1_branch_is_rejected(
    rigor: str, validator: Draft202012Validator
):
    """The pattern's teeth: `coverage` is legal in the R1 branch and nowhere
    else, because the claim is closed by `unevaluatedProperties`."""
    payload = {
        "covered": 1,
        "changed_executable": 1,
        "pct": 100.0,
        "considered": 1,
        "missing_lines": {},
        "files_missing_coverage": [],
        "unclassified_lines": {},
        "files_with_unclassified_lines": [],
        "excluded_lines": {},
        "files_with_excluded_lines": [],
    }
    assert why_invalid(
        validator, document_with([claim_dict(rigor="R1", coverage=payload)])
    ) == [], "the identical payload on an R1 claim must be accepted"

    assert not validator.is_valid(
        document_with([claim_dict(rigor=rigor, coverage=payload)])
    )


# --- A-033: assay never marks an attestation verified -------------------------


def test_an_attested_evidence_entry_marked_verified_is_rejected_by_the_schema(
    validator: Draft202012Validator,
):
    document = verdict_fixture("FAIL")
    assert why_invalid(validator, document) == []
    document["evidence"][0]["verified_by_assay"] = True
    messages = why_invalid(validator, document)
    assert messages, "attested evidence was allowed onto the record as verified"


def test_a_computed_claim_may_be_verified(validator: Draft202012Validator):
    """Proves the rule above is about `attested`, not about `verified_by_assay`
    being rejected everywhere — otherwise it would pass for the wrong reason."""
    assert (
        why_invalid(
            validator,
            document_with([claim_dict(source="computed", verified_by_assay=True)]),
        )
        == []
    )


def test_a_computed_claim_cannot_be_marked_unverified():
    with pytest.raises(ValueError, match="computed claim must carry"):
        Claim(
            rigor="R0",
            source="computed",
            status=Outcome.PASS,
            verified_by_assay=False,
        )


def test_attestation_reasons_cannot_be_put_on_a_computed_claim():
    with pytest.raises(ValueError, match="external attested evidence"):
        Claim(
            rigor="R0",
            source="computed",
            status=Outcome.NO_MEASUREMENT,
            verified_by_assay=True,
            reason_code=ReasonCode.MISSING_ATTESTATION,
        )


def test_the_model_refuses_attested_evidence_marked_verified():
    with pytest.raises(ValueError, match="never upgrades a review"):
        Evidence(
            source="attested",
            key="adversarial-review",
            status=Outcome.PASS,
            verified_by_assay=True,
            producer="reviewer@example",
            attested_commit="a" * 40,
            reviewed_paths=("src/assay/verdict.py",),
        )


def test_the_model_refuses_an_unknown_source():
    with pytest.raises(ValueError, match="source must be one of"):
        Claim(
            rigor="R0",
            source="reviewed-by-a-model",
            status=Outcome.PASS,
            verified_by_assay=False,
        )


def test_the_model_refuses_an_unknown_rigor_level():
    with pytest.raises(ValueError, match="rigor must be one of"):
        Claim(
            rigor="R4", source="computed", status=Outcome.PASS, verified_by_assay=True
        )


@pytest.mark.parametrize("bogus", ["PASS", 1, None])
def test_the_model_refuses_a_status_that_is_not_an_outcome(bogus):
    with pytest.raises(ValueError, match="status must be an Outcome"):
        Claim(
            rigor="R0", source="computed", status=bogus, verified_by_assay=True
        )


def test_the_model_refuses_a_non_boolean_verified_flag():
    with pytest.raises(ValueError, match="verified_by_assay must be a boolean"):
        Claim(
            rigor="R0", source="computed", status=Outcome.PASS, verified_by_assay="yes"
        )


# --- A-024: the claims cover the declared rigor, exactly ----------------------
#
# The schema cannot express this. The model can, and does, at construction.


def test_a_verdict_whose_claims_cover_the_declared_rigor_is_built():
    verdict = Verdict(
        **BASE,
        outcome=Outcome.PASS,
        declared_rigor=("R0", "R1", "R2"),
        claims=(passing("R0"), passing("R1"), passing("R2")),
        judgment=Judgment(
            r1=R1_JUDGMENT.r1, r2=JudgmentR2(jobs=1, operators=("compare-swap",))
        ),
    )
    assert [claim.rigor for claim in verdict.claims] == ["R0", "R1", "R2"]


@pytest.mark.parametrize("missing", ["R0", "R1", "R2"])
def test_the_model_refuses_a_verdict_that_renders_no_claim_for_a_declared_level(
    missing: str,
):
    """'R2 was declared but rendered no judgement' must not be able to look like
    'R2 was never declared'."""
    declared = ("R0", "R1", "R2")
    kept = tuple(passing(level) for level in declared if level != missing)
    # The R1 policy travels with the R1 coverage claim, so it is present
    # exactly when that claim is -- including in the case that drops it.
    judgment = None if missing == "R1" else R1_JUDGMENT

    with pytest.raises(ValueError, match=f"no claim for {missing}"):
        Verdict(
            **BASE,
            outcome=Outcome.PASS,
            declared_rigor=declared,
            claims=kept,
            judgment=judgment,
        )


def test_the_model_refuses_a_claim_for_a_level_the_lane_never_declared():
    with pytest.raises(ValueError, match="never declared"):
        Verdict(
            **BASE,
            outcome=Outcome.PASS,
            declared_rigor=("R0",),
            claims=(passing("R0"), passing("R2")),
        )


def test_the_model_refuses_two_claims_for_one_level():
    with pytest.raises(ValueError, match="more than one claim"):
        Verdict(
            **BASE,
            outcome=Outcome.PASS,
            declared_rigor=("R0",),
            claims=(passing("R0"), passing("R0")),
        )


def test_the_model_refuses_claims_when_no_lane_resolved():
    """A claim without a declared rigor level is a claim about nothing."""
    with pytest.raises(ValueError, match="no lane resolved"):
        Verdict(
            lane="package",
            commit="a" * 40,
            outcome=Outcome.PASS,
            started="2026-08-06T09:00:00+00:00",
            ended="2026-08-06T09:00:07+00:00",
            assay_version="0.1.0",
            claims=(passing("R0"),),
        )


def test_the_model_refuses_a_duplicated_declared_level():
    with pytest.raises(ValueError, match="duplicate level"):
        Verdict(
            **BASE,
            outcome=Outcome.PASS,
            declared_rigor=("R0", "R0"),
            claims=(passing("R0"),),
        )


def test_the_model_refuses_an_unknown_declared_level():
    with pytest.raises(ValueError, match="declared_rigor must contain only"):
        Verdict(
            **BASE,
            outcome=Outcome.PASS,
            declared_rigor=("R0", "R7"),
            claims=(passing("R0"),),
        )


def test_the_model_refuses_an_empty_declared_rigor():
    with pytest.raises(ValueError, match="at least one rigor level"):
        Verdict(**BASE, outcome=Outcome.PASS, declared_rigor=(), claims=())


# --- external declarations cover evidence identities exactly ----------------


def attestation(status: Outcome = Outcome.PASS, **overrides) -> Evidence:
    values = {
        "source": "attested",
        "key": "adversarial-review",
        "status": status,
        "verified_by_assay": False,
        "producer": "reviewer@example",
        "attested_commit": "a" * 40,
        "reviewed_paths": ("src/assay/verdict.py",),
    }
    values.update(overrides)
    return Evidence(**values)


def test_external_evidence_is_not_a_rigor_claim():
    verdict = Verdict(
        **{
            **BASE,
            "declared_evidence": (
                EvidenceDeclaration(source="attested", key="adversarial-review"),
            ),
        },
        outcome=Outcome.PASS,
        declared_rigor=("R0",),
        claims=(passing("R0"),),
        evidence=(attestation(),),
    )

    assert [claim.rigor for claim in verdict.claims] == ["R0"]
    assert verdict.evidence[0].identity == ("attested", "adversarial-review")


def test_external_identity_fields_and_types_are_closed():
    with pytest.raises(ValueError, match="declaration source"):
        EvidenceDeclaration(source="computed", key="review")
    with pytest.raises(ValueError, match="non-empty string"):
        EvidenceDeclaration(source="attested", key="")
    with pytest.raises(ValueError, match="evidence source"):
        Evidence(
            source="computed",
            key="review",
            status=Outcome.PASS,
            verified_by_assay=True,
        )
    with pytest.raises(ValueError, match="evidence key"):
        Evidence(
            source="adjudicated",
            key="",
            status=Outcome.PASS,
            verified_by_assay=True,
        )
    with pytest.raises(ValueError, match="status must be an Outcome"):
        Evidence(
            source="adjudicated",
            key="sast",
            status="PASS",
            verified_by_assay=True,
        )
    with pytest.raises(ValueError, match="must be a boolean"):
        Evidence(
            source="adjudicated",
            key="sast",
            status=Outcome.PASS,
            verified_by_assay="yes",
        )


def test_attestation_reason_requires_attested_source():
    with pytest.raises(ValueError, match="requires attested source"):
        Evidence(
            source="adjudicated",
            key="sast",
            status=Outcome.NO_MEASUREMENT,
            verified_by_assay=False,
            reason_code=ReasonCode.STALE_ATTESTATION,
        )


def test_declared_external_evidence_must_render_a_judgement():
    with pytest.raises(ValueError, match="rendered no judgement"):
        Verdict(
                **{
                    **BASE,
                    "declared_evidence": (
                        EvidenceDeclaration(
                            source="attested", key="adversarial-review"
                        ),
                    ),
                },
                outcome=Outcome.PASS,
                declared_rigor=("R0",),
            claims=(passing("R0"),),
        )


def test_undeclared_external_evidence_is_refused():
    with pytest.raises(ValueError, match="never declared"):
        Verdict(
                **{**BASE, "declared_evidence": ()},
                outcome=Outcome.PASS,
                declared_rigor=("R0",),
            claims=(passing("R0"),),
            evidence=(attestation(),),
        )


def test_duplicate_external_declarations_and_results_are_refused():
    declaration = EvidenceDeclaration(
        source="attested", key="adversarial-review"
    )
    with pytest.raises(ValueError, match="duplicate identities"):
        Verdict(
                **{**BASE, "declared_evidence": (declaration, declaration)},
                outcome=Outcome.PASS,
                declared_rigor=("R0",),
            claims=(passing("R0"),),
            evidence=(attestation(),),
        )
    with pytest.raises(ValueError, match="more than one evidence"):
        Verdict(
                **{**BASE, "declared_evidence": (declaration,)},
                outcome=Outcome.PASS,
                declared_rigor=("R0",),
            claims=(passing("R0"),),
            evidence=(attestation(), attestation()),
        )


def test_missing_attestation_is_an_explicit_no_measurement_entry():
    missing = Evidence(
        source="attested",
        key="adversarial-review",
        status=Outcome.NO_MEASUREMENT,
        verified_by_assay=False,
        reason_code=ReasonCode.MISSING_ATTESTATION,
    )

    assert missing.to_dict() == {
        "source": "attested",
        "key": "adversarial-review",
        "status": "NO_MEASUREMENT",
        "verified_by_assay": False,
        "reason_code": "MISSING_ATTESTATION",
    }


def test_stale_attestation_records_the_commit_and_reviewed_paths():
    stale = attestation(
        Outcome.NO_MEASUREMENT,
        reason_code=ReasonCode.STALE_ATTESTATION,
    )

    assert stale.to_dict()["attested_commit"] == "a" * 40
    assert stale.to_dict()["reviewed_paths"] == ["src/assay/verdict.py"]


def test_stale_attestation_without_obtained_payload_is_rejected():
    with pytest.raises(ValueError, match="requires producer"):
        Evidence(
            source="attested",
            key="adversarial-review",
            status=Outcome.NO_MEASUREMENT,
            verified_by_assay=False,
            reason_code=ReasonCode.STALE_ATTESTATION,
        )


def test_schema_rejects_stale_attestation_without_obtained_payload(
    validator: Draft202012Validator,
):
    document = verdict_fixture("FAIL")
    entry = document["evidence"][0]
    entry["status"] = "NO_MEASUREMENT"
    entry["reason_code"] = "STALE_ATTESTATION"
    for field in ("producer", "attested_commit", "reviewed_paths"):
        del entry[field]
    document["outcome"] = "NO_MEASUREMENT"
    document["reason_code"] = "STALE_ATTESTATION"
    document["exit_code"] = 3

    assert not validator.is_valid(document)


def test_present_attestation_requires_a_complete_nonempty_payload():
    with pytest.raises(ValueError, match="requires producer"):
        Evidence(
            source="attested",
            key="adversarial-review",
            status=Outcome.PASS,
            verified_by_assay=False,
        )
    with pytest.raises(ValueError, match="all present or all absent"):
        Evidence(
            source="attested",
            key="adversarial-review",
            status=Outcome.ERROR,
            verified_by_assay=False,
            reason_code=ReasonCode.UNREADABLE_ARTIFACT,
            producer="reviewer@example",
        )
    with pytest.raises(ValueError, match="must not be empty"):
        attestation(reviewed_paths=())
    with pytest.raises(ValueError, match="duplicate"):
        attestation(reviewed_paths=("src/a.py", "src/a.py"))


def test_missing_attestation_cannot_claim_a_payload_was_obtained():
    with pytest.raises(ValueError, match="missing attestation cannot carry"):
        attestation(
            Outcome.NO_MEASUREMENT,
            reason_code=ReasonCode.MISSING_ATTESTATION,
        )


def test_adjudicated_slot_is_reserved_without_attestation_fields():
    adjudicated = Evidence(
        source="adjudicated",
        key="sast",
        status=Outcome.PASS,
        verified_by_assay=True,
    )
    assert adjudicated.to_dict()["source"] == "adjudicated"

    with pytest.raises(ValueError, match="only to attested"):
        Evidence(
            source="adjudicated",
            key="sast",
            status=Outcome.PASS,
            verified_by_assay=True,
            producer="scanner",
        )


def test_external_evidence_participates_in_rollup():
    missing = Evidence(
        source="attested",
        key="adversarial-review",
        status=Outcome.NO_MEASUREMENT,
        verified_by_assay=False,
        reason_code=ReasonCode.MISSING_ATTESTATION,
    )
    verdict = Verdict(
        **{
            **BASE,
            "declared_evidence": (
                EvidenceDeclaration(source="attested", key="adversarial-review"),
            ),
        },
        outcome=Outcome.NO_MEASUREMENT,
        reason_code=ReasonCode.MISSING_ATTESTATION,
        declared_rigor=("R0",),
        claims=(passing("R0"),),
        evidence=(missing,),
    )
    assert verdict.outcome is Outcome.NO_MEASUREMENT


# --- A-023: rollup precedence -------------------------------------------------


def test_the_precedence_is_the_declared_one():
    assert [outcome.value for outcome in ROLLUP_PRECEDENCE] == [
        "ERROR",
        "NO_MEASUREMENT",
        "BUDGET_EXCEEDED",
        "FAIL",
        "INCONCLUSIVE",
    ]


def test_pass_only_when_every_claim_passed():
    assert rollup([Outcome.PASS, Outcome.PASS]) is Outcome.PASS
    for outcome in ROLLUP_PRECEDENCE:
        assert rollup([Outcome.PASS, outcome]) is outcome


@pytest.mark.parametrize(
    "statuses,expected",
    [
        ([Outcome.ERROR, Outcome.NO_MEASUREMENT], Outcome.ERROR),
        ([Outcome.NO_MEASUREMENT, Outcome.BUDGET_EXCEEDED], Outcome.NO_MEASUREMENT),
        ([Outcome.BUDGET_EXCEEDED, Outcome.FAIL], Outcome.BUDGET_EXCEEDED),
        ([Outcome.FAIL, Outcome.INCONCLUSIVE], Outcome.FAIL),
        ([Outcome.INCONCLUSIVE, Outcome.PASS], Outcome.INCONCLUSIVE),
        ([Outcome.ERROR, Outcome.FAIL, Outcome.PASS], Outcome.ERROR),
    ],
)
def test_every_adjacent_precedence_pair_resolves_the_declared_way(
    statuses, expected
):
    assert rollup(statuses) is expected
    assert rollup(list(reversed(statuses))) is expected, "order must not matter"


def test_a_rollup_over_no_claims_is_not_a_pass():
    """Returning PASS on an empty set would be a laundering default."""
    with pytest.raises(ValueError, match="not a PASS"):
        rollup([])


def test_the_model_refuses_an_outcome_that_disagrees_with_its_claims():
    claims = (
        passing("R0"),
        Claim(
            rigor="R1",
            source="computed",
            status=Outcome.FAIL,
            verified_by_assay=True,
            reason_code=ReasonCode.UNCOVERED_LINES,
            coverage=Coverage(
                covered=1,
                changed_executable=2,
                pct=50.0,
                considered=1,
                missing_lines={"src/assay/verdict.py": frozenset({7})},
                files_missing_coverage=(),
            ),
        ),
    )
    judgment = Judgment(
        r1=JudgmentR1(
            language="python",
            source_roots=("src",),
            coverage_format="coverage-py-json",
            coverage_artifact="cov.json",
            fail_under=100.0,
            allow_excluded=False,
            base="b" * 40,
        )
    )
    # The honest form builds.
    Verdict(
        **BASE,
        outcome=Outcome.FAIL,
        reason_code=ReasonCode.UNCOVERED_LINES,
        declared_rigor=("R0", "R1"),
        claims=claims,
        judgment=judgment,
    )

    with pytest.raises(ValueError, match="disagrees with the rollup"):
        Verdict(
            **BASE,
            outcome=Outcome.PASS,
            declared_rigor=("R0", "R1"),
            claims=claims,
            judgment=judgment,
        )
