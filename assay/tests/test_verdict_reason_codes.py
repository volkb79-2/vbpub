"""O3 — `reason_code` is drawn from the CLOSED enumeration (A-050, A-051).

The negative this defends: *reason_code is a free string, so a typo'd or
invented code passes validation and a consumer switching on it silently falls
through.*

**Why this module is not self-agreement.** The schema is a hand-written file; it
does not import `errors.py` and nothing generates it from `errors.py`. So
asserting that the file's enumerations EQUAL `errors.REASON_CODES` is a real
drift guard between two independently maintained artifacts — the same
relationship the config suite has with `tomllib`. If the schema were ever
generated from Python, every test below would collapse into proving that assay
agrees with itself, and this module would be worthless. It is the one thing a
successor must not "simplify".
"""

from __future__ import annotations

import json

import pytest
from conftest import SCHEMA_PATH, verdict_fixture, why_invalid
from jsonschema import Draft202012Validator

from assay.errors import EXIT_CODES, REASON_CODES, Outcome, ReasonCode
from assay.verdict import Verdict

NON_PASS = [outcome for outcome in Outcome if outcome is not Outcome.PASS]

#: Every (outcome, code) pair the design guide declares legal.
VALID_PAIRS = [
    (outcome.value, code.value)
    for outcome in NON_PASS
    for code in sorted(REASON_CODES[outcome])
]

#: Every (outcome, code) pair it does NOT — a code that is real, but belongs to
#: a different outcome. This is the shape a consumer's switch falls through on.
CROSS_PAIRS = [
    (outcome.value, code.value)
    for outcome in NON_PASS
    for code in sorted(ReasonCode)
    if code not in REASON_CODES[outcome]
]

ATTESTATION_ONLY = {
    ReasonCode.MISSING_ATTESTATION,
    ReasonCode.STALE_ATTESTATION,
}
CLAIM_VALID_PAIRS = [
    (outcome, code)
    for outcome, code in VALID_PAIRS
    if ReasonCode(code) not in ATTESTATION_ONLY
]
CLAIM_INVALID_PAIRS = [
    (outcome.value, code.value)
    for outcome in NON_PASS
    for code in ReasonCode
    if (outcome.value, code.value) not in CLAIM_VALID_PAIRS
]


LANE_RESOLVED_KEYS = (
    "declared_rigor",
    "declared_evidence",
    "argv_declared",
    "argv_appended",
    "argv_effective",
    "argv_modified",
    "env_declared",
    "env_effective",
    "scope",
    "enforcement",
)


def a_verdict_with(outcome: str, code: str | None) -> dict:
    """The smallest valid artifact for *outcome*, carrying exactly *code*.

    Built from the ERROR fixture — the one shape with no claims and no
    lane-resolved group — so that `reason_code` is the ONLY thing this module
    varies. A rejection here cannot be caused by anything else in the document.
    """
    document = verdict_fixture("ERROR")
    document["outcome"] = outcome
    document["exit_code"] = EXIT_CODES[Outcome(outcome)]
    if code is None:
        document.pop("reason_code", None)
    else:
        document["reason_code"] = code
    return document


#: (P21) reason codes the schema binds to the R2 claim specifically.
_R2_ONLY_CODES = frozenset(
    {
        "NO_MUTANTS",
        "MUTATION_UNSUPPORTED",
        "MUTATION_DISCOVERY_FAILED",
        "MUTANT_LIMIT_EXCEEDED",
        # (P33/A-228) the new terminal inherits every constraint its three
        # siblings carry, in every layer that owns one. It was added to the
        # schema's own claim branches for exactly this reason: a payload-free,
        # non-R2 ALL_MUTANTS_EQUIVALENT would otherwise validate, which is
        # verbatim the forgery A-136 closed for NO_MUTANTS, one bucket over.
        "ALL_MUTANTS_EQUIVALENT",
        # (A-251) the third member of the model's own
        # `_MUTATION_ONLY_REASON_CODES`, and the one the schema had never
        # pinned. It is read off the `survived` bucket and nowhere else, so it
        # belongs here for exactly the reason its two siblings do -- this
        # helper's docstring already stated the rule; only this row was missing.
        "MUTANTS_SURVIVED",
    }
)

#: (wave-1 §4/§5, A-259/A-260) reason codes the schema binds to the R1 claim
#: specifically -- read off R1's own guard sequence and nowhere else, the
#: identical shape `_R2_ONLY_CODES` already gives one tier over.
_R1_ONLY_CODES = frozenset({"BRANCH_UNAVAILABLE", "TARGET_NOT_MEASURED"})

#: (P21) the payload each of those codes requires, or must NOT carry.
#: `NO_MUTANTS` is the SUPPORTED empty analysis, so it records its
#: zero/zero result; `MUTANT_LIMIT_EXCEEDED` records the pre-submission
#: sentinel; the two capability/boundary terminals carry nothing at all.
_EMPTY_BUCKETS = {
    "killed": [],
    "survived": [],
    "crashed": [],
    "budget_exceeded": [],
    # (P33/V5-3) five buckets since v5, and the limit sentinel's own branch in
    # the schema pins this one empty too (A-228).
    "equivalent": [],
}
#: (P33/V5-3) one provably-inert mutant, for the terminal that exists to
#: report exactly that. `ALL_MUTANTS_EQUIVALENT` is the SUPPORTED all-inert
#: result, so unlike the two capability terminals it carries its payload.
_ONE_EQUIVALENT_MUTANT = {
    "path": "schema/001.sql",
    "lineno": 3,
    "start_byte": 10,
    "end_byte": 20,
    "replacement_sha256": "c" * 64,
    "operator": "sql:drop-check",
    "description": "drop the CHECK constraint",
}
#: (A-251) one mutant that survived, for the terminal that reports exactly
#: that. Deliberately a DIFFERENT bucket from `_ONE_EQUIVALENT_MUTANT`'s: a
#: survivor changed behaviour and was not caught, an equivalent one changed
#: nothing, and a payload that conflated them would let this row pass while the
#: rule it checks was wrong.
_ONE_SURVIVING_MUTANT = {
    "path": "pkg/mod.py",
    "lineno": 3,
    "start_byte": 10,
    "end_byte": 20,
    "replacement_sha256": "d" * 64,
    "operator": "python:compare-swap",
    "description": "Lt->LtE",
}
_MUTATION_PAYLOAD_FOR: dict[str | None, dict | None] = {
    "NO_MUTANTS": {"candidate_count": 0, "total": 0, **_EMPTY_BUCKETS},
    "MUTANTS_SURVIVED": {
        "candidate_count": 1,
        "total": 1,
        **{**_EMPTY_BUCKETS, "survived": [_ONE_SURVIVING_MUTANT]},
    },
    "MUTANT_LIMIT_EXCEEDED": {"candidate_count": 3, "total": 0, **_EMPTY_BUCKETS},
    "ALL_MUTANTS_EQUIVALENT": {
        "candidate_count": 1,
        "total": 1,
        **{**_EMPTY_BUCKETS, "equivalent": [_ONE_EQUIVALENT_MUTANT]},
    },
}


def a_claim_with(status: str, code: str | None) -> dict:
    """The same, one level down: one R0 claim carrying exactly *code*.

    The verdict-level outcome is set to a shape the schema accepts and is held
    constant, so a rejection is unambiguously the CLAIM's.
    """
    document = verdict_fixture("PASS")
    for key in LANE_RESOLVED_KEYS:
        document.pop(key, None)
    # wave-1 §6: snapshot_policy is its OWN conditional (required iff
    # declared_rigor contains R1/R2/R3), not part of LANE_RESOLVED_KEYS --
    # stripping the lane-resolved group without also stripping this leaves a
    # document that carries a policy for a lane that (as far as this document
    # says) never resolved at all.
    document.pop("snapshot_policy", None)
    document["outcome"] = "PASS"
    document["exit_code"] = 0
    document.pop("reason_code", None)

    # P21: four reason codes are now bound BY THE SCHEMA to the R2 claim and
    # to the presence/shape of a mutation payload (A-163/A-183), because
    # "which rigor level can even produce this cause" is a local, expressible
    # fact the schema was previously silent about. The claim this helper
    # builds therefore has to be honest about rigor and payload -- otherwise
    # every row below would be asserting that the schema accepts a claim no
    # producer can emit. wave-1 §4/§5 (A-259/A-260) adds the identical
    # binding for R1's own two new payload-free terminals.
    if code in _R2_ONLY_CODES:
        rigor = "R2"
    elif code in _R1_ONLY_CODES:
        rigor = "R1"
    else:
        rigor = "R0"
    claim = {
        "rigor": rigor,
        "source": "computed",
        "status": status,
        "verified_by_assay": True,
    }
    if code is not None:
        claim["reason_code"] = code
    payload = _MUTATION_PAYLOAD_FOR.get(code)
    if payload is not None:
        claim["mutation"] = payload
    document["claims"] = [claim]
    return document


# --- the enumeration is really closed, and really the declared one -----------


def test_the_shipped_schema_enumerates_exactly_the_codes_errors_py_declares():
    """The drift guard between two independently written artifacts."""
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    per_outcome = schema["$defs"]["reason_codes"]

    declared = {
        outcome.value: {code.value for code in codes}
        for outcome, codes in REASON_CODES.items()
        if codes
    }
    shipped = {
        name: set(entry["enum"])
        for name, entry in per_outcome.items()
        if isinstance(entry, dict) and "enum" in entry
    }
    assert shipped == declared

    assert set(schema["$defs"]["reason_code"]["enum"]) == {
        code.value for code in ReasonCode
    }
    assert "PASS" not in per_outcome, "a pass has no cause to name"


def test_the_shipped_schema_enumerates_exactly_the_six_outcomes():
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    assert set(schema["$defs"]["outcome"]["enum"]) == {o.value for o in Outcome}


# --- each declared pair validates ---------------------------------------------


@pytest.mark.parametrize("outcome,code", VALID_PAIRS)
def test_each_declared_pair_validates(
    outcome: str, code: str, validator: Draft202012Validator
):
    assert why_invalid(validator, a_verdict_with(outcome, code)) == []


def test_every_declared_code_is_exercised_by_the_accept_half():
    """Guard the guard: an unexercised code proves nothing about itself."""
    assert {code for _, code in VALID_PAIRS} == {code.value for code in ReasonCode}
    # 19 in v3; P21 adds seven named, reachable-or-reserved terminals
    # (OUTPUT_WRITE_FAILED, MUTATION_DISCOVERY_FAILED, HEAD_CHANGED,
    # MISSING_EXTERNAL_TOOL, MUTANT_LIMIT_EXCEEDED, SNAPSHOT_LIMIT_EXCEEDED,
    # MUTATION_UNSUPPORTED). The literal is kept rather than derived: it is
    # the thing that makes an accidental vocabulary addition a red test.
    # P33/A-223d adds the 27th: ALL_MUTANTS_EQUIVALENT. wave-1 §4/§5/§6
    # (A-258/A-259/A-260) adds three more: UNCOVERED_BRANCHES,
    # BRANCH_UNAVAILABLE, TARGET_NOT_MEASURED -- 30. Wave D's v10 cut adds
    # the last two: PROVENANCE_UNVERIFIED (B004/A-430, NO_MEASUREMENT) and
    # RED_FIRST_UNPROVEN (F015/A-433 as amended by A-434, FAIL) -- 32.
    assert len(VALID_PAIRS) == len(ReasonCode) == 32


# --- a code valid for a DIFFERENT outcome is rejected -------------------------


@pytest.mark.parametrize("outcome,code", CROSS_PAIRS)
def test_a_code_belonging_to_another_outcome_is_rejected(
    outcome: str, code: str, validator: Draft202012Validator
):
    valid = sorted(REASON_CODES[Outcome(outcome)])[0].value
    assert why_invalid(validator, a_verdict_with(outcome, valid)) == [], (
        "the same document with a CORRECT code must validate, or this rejection "
        "is about something else"
    )

    assert not validator.is_valid(a_verdict_with(outcome, code))


def test_the_cross_matrix_is_not_empty():
    assert len(CROSS_PAIRS) == len(NON_PASS) * len(ReasonCode) - len(VALID_PAIRS)
    assert len(CROSS_PAIRS) == 5 * 32 - 32


@pytest.mark.parametrize("outcome", [o.value for o in NON_PASS])
def test_an_invented_code_is_rejected(outcome: str, validator: Draft202012Validator):
    """A-050: the enumeration is closed. An implementer who needs a code that is
    not listed stops and asks; they do not add one."""
    assert not validator.is_valid(a_verdict_with(outcome, "SEEMS_FINE"))
    assert not validator.is_valid(a_verdict_with(outcome, "uncovered_lines"))
    assert not validator.is_valid(a_verdict_with(outcome, ""))


# --- PASS carries none at all -------------------------------------------------


@pytest.mark.parametrize("code", [code.value for code in ReasonCode])
def test_pass_carrying_any_reason_code_at_all_is_rejected(
    code: str, validator: Draft202012Validator
):
    assert why_invalid(validator, a_verdict_with("PASS", None)) == []

    assert not validator.is_valid(a_verdict_with("PASS", code))


# --- the same rule, one level down, on a claim --------------------------------


@pytest.mark.parametrize("status,code", CLAIM_VALID_PAIRS)
def test_each_declared_pair_validates_on_a_claim(
    status: str, code: str, validator: Draft202012Validator
):
    assert why_invalid(validator, a_claim_with(status, code)) == []


@pytest.mark.parametrize("status,code", CLAIM_INVALID_PAIRS)
def test_a_claim_code_belonging_to_another_status_is_rejected(
    status: str, code: str, validator: Draft202012Validator
):
    valid = next(
        code_value
        for outcome_value, code_value in CLAIM_VALID_PAIRS
        if outcome_value == status
    )
    assert why_invalid(validator, a_claim_with(status, valid)) == []

    assert not validator.is_valid(a_claim_with(status, code))


@pytest.mark.parametrize("code", [code.value for code in ReasonCode])
def test_a_pass_claim_carrying_any_reason_code_is_rejected(
    code: str, validator: Draft202012Validator
):
    assert why_invalid(validator, a_claim_with("PASS", None)) == []

    assert not validator.is_valid(a_claim_with("PASS", code))


# --- the exit code is the verdict ---------------------------------------------


@pytest.mark.parametrize("outcome", [o.value for o in Outcome])
def test_an_exit_code_that_disagrees_with_the_outcome_is_rejected(
    outcome: str, validator: Draft202012Validator
):
    """The exit code IS the verdict: a consumer checking only it must never be
    wrong, so the artifact is never allowed to say something else."""
    document = verdict_fixture(outcome)
    assert why_invalid(validator, document) == []
    assert document["exit_code"] == EXIT_CODES[Outcome(outcome)]

    for wrong in range(6):
        if wrong == EXIT_CODES[Outcome(outcome)]:
            continue
        document["exit_code"] = wrong
        assert not validator.is_valid(document), (
            f"{outcome} was allowed to report exit {wrong}"
        )


# --- and the model refuses the same pairings ----------------------------------


@pytest.mark.parametrize("outcome,code", CROSS_PAIRS)
def test_the_model_refuses_a_code_from_another_outcome(outcome: str, code: str):
    kwargs = {
        "lane": "package",
        "commit": "a" * 40,
        "outcome": Outcome(outcome),
        "started": "2026-08-06T09:00:00+00:00",
        "ended": "2026-08-06T09:00:01+00:00",
        "assay_version": "0.1.0",
    }
    valid = sorted(REASON_CODES[Outcome(outcome)])[0]
    assert Verdict(**kwargs, reason_code=valid).reason_code is valid

    with pytest.raises(ValueError, match="is not valid for"):
        Verdict(**kwargs, reason_code=ReasonCode(code))
