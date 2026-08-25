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
from assay.verdict import CanaryResult, Claim, Coverage, MutantOutcome, Mutation

#: The payload each level's own producer attaches to a PASS (P16 review),
#: so a "the honest form builds" control is honest at every rigor level
#: rather than only at R0.
_PASSING_PAYLOAD = {
    "R0": {},
    "R2": {
        "mutation": Mutation(
            candidate_count=1,
            total=1,
            killed=(
                MutantOutcome(
                    path="pkg/mod.py",
                    lineno=3,
                    start_byte=40,
                    end_byte=41,
                    replacement_sha256=(
                        "b60080dc8b8982d2a2bff6f8f3715c1939614dc553cd223ef21832b88c815866"
                    ),
                    operator="python:compare-swap",
                    description="Lt->LtE",
                ),
            ),
        )
    },
    "R3": {
        "canary": CanaryResult(
            mechanism="uncovered-line",
            target="pkg/mod.py",
            description="appended a never-called function",
            control_outcome=Outcome.PASS,
            transformed_outcome=Outcome.FAIL,
            expected_reason_code=ReasonCode.UNCOVERED_LINES,
            observed_reason_code=ReasonCode.UNCOVERED_LINES,
        )
    },
}

#: A plausible, well-formed coverage payload. Its presence is the ONLY thing
#: wrong with the documents it is planted into below.
ZEROED_COVERAGE = {
    "covered": 0,
    "executable": 0,
    "pct": 100.0,
    "considered": 0,
    "exclusion_capability": "reported",
    "missing_lines": {},
    "files_missing_coverage": [],
    "unclassified_lines": {},
    "files_with_unclassified_lines": [],
    "excluded_lines": {},
    "files_with_excluded_lines": [],
    "branches_covered": 0,
    "branches_total": 0,
    "branch_capability": "unavailable",
    "missing_branch_lines": {},
    "files_with_missing_branch_lines": [],
}

NON_PASS = [outcome.value for outcome in Outcome if outcome is not Outcome.PASS]


# --- A-025: no percentage exists unless a measurement produced one ------------


def _verdict_document(name: str) -> dict:
    """A named fixture from `tests/fixtures/verdicts/`, freshly parsed.

    `verdict_fixture` only reaches the six outcome-named artifacts; A-251's
    branches need the per-rigor ones.
    """
    import json as _json
    from pathlib import Path as _Path

    path = _Path(__file__).resolve().parent / "fixtures" / "verdicts" / name
    return _json.loads(path.read_text(encoding="utf-8"))


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


@pytest.mark.parametrize("field", ["covered", "executable", "pct"])
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


#: (P33/A-228) The reason codes the shipped schema binds to an R2 claim AND
#: to a particular mutation-payload shape. A test that only wants "some code
#: valid for this outcome" must not pick one of these, or it silently starts
#: testing the rigor binding instead of the rule it names.
_R2_BOUND_CODES = frozenset(
    {
        ReasonCode.NO_MUTANTS,
        ReasonCode.MUTATION_UNSUPPORTED,
        ReasonCode.MUTATION_DISCOVERY_FAILED,
        ReasonCode.MUTANT_LIMIT_EXCEEDED,
        ReasonCode.ALL_MUTANTS_EQUIVALENT,
    }
)


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
    # (P33/A-228) Five codes are bound BY THE SCHEMA to an R2 claim carrying a
    # specific mutation payload, so none of them can ride on the plain R0
    # claim this test builds -- a rejection would then be about rigor, not
    # about the missing `reason_code` this test exists to check. Excluded by
    # derivation rather than by naming one substitute, so a sixth such code
    # cannot silently reintroduce the problem. `ALL_MUTANTS_EQUIVALENT` sorts
    # first among INCONCLUSIVE's members, which is how it surfaced.
    code = sorted(
        code for code in REASON_CODES[Outcome(outcome)] if code not in _R2_BOUND_CODES
    )[0].value
    document = verdict_fixture("PASS")
    document["declared_rigor"] = ["R0"]
    # wave-1 §6: the PASS fixture is R0,R1 and therefore carries
    # snapshot_policy; downgrading declared_rigor to R0-only without also
    # dropping it now trips the new schema-level conditional this test does
    # not exist to check.
    document.pop("snapshot_policy", None)
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
                covered=0,
                executable=0,
                pct=100.0,
                considered=0,
                exclusion_capability="reported",
                missing_lines={},
                files_missing_coverage=(),
            ),
        )


@pytest.mark.parametrize("rigor", ["R0", "R2", "R3"])
def test_the_model_refuses_a_coverage_payload_on_a_non_r1_claim(rigor: str):
    Claim(
        rigor=rigor, source="computed", status=Outcome.PASS,
        verified_by_assay=True, **_PASSING_PAYLOAD[rigor],
    )

    with pytest.raises(ValueError, match="belongs to the R1 claim"):
        Claim(
            rigor=rigor,
            source="computed",
            status=Outcome.PASS,
            verified_by_assay=True,
            coverage=Coverage(
                covered=1,
                executable=1,
                pct=100.0,
                considered=1,
                exclusion_capability="reported",
                missing_lines={},
                files_missing_coverage=(),
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
        ({"executable": -2}, "must not be negative"),
        ({"considered": -3}, "must not be negative"),
        ({"pct": 101.0}, "between 0 and 100"),
        ({"pct": -0.5}, "between 0 and 100"),
        ({"covered": 4}, "exceeds executable"),
        ({"covered": True}, "must be an integer"),
        ({"pct": "100"}, "must be a number"),
    ],
)
def test_the_coverage_payload_refuses_an_impossible_measurement(kwargs, match):
    base = {
        "covered": 3,
        "executable": 3,
        "pct": 100.0,
        "considered": 1,
        "exclusion_capability": "reported",
        "missing_lines": {},
        "files_missing_coverage": (),
    }
    assert Coverage(**base).pct == 100.0  # the untouched form builds

    with pytest.raises(ValueError, match=match):
        Coverage(**{**base, **kwargs})

# ---------------------------------------------------------------------------
# A-251 — a judged status carries the payload it was judged FROM
# ---------------------------------------------------------------------------
#
# The hollow-green lie: not a payload that contradicts its status (sol finding
# 2's three artifacts) but a DELETED payload. Every re-derivation then has
# nothing to judge and returns, the rollup still agrees, and a PASS backed by no
# evidence at all validated against the shipped schema. A-237/A-240 left this
# open on a doctrine ("the schema owns refusal of impossible payloads, not
# requiredness of evidence") that was already false of the shipped artifact --
# branches 7, 8 and 9 require a payload keyed on reason code. A-251 closes it and
# restores A-182's original one-sentence scope.
#
# Each test below deletes the payload from a REAL fixture and asserts the
# untouched document validates in the same body, so a schema that rejected
# everything would fail here too.

_HOLLOW_CASES = [
    ("r1_pass.json", "R1", "coverage"),
    ("r1_fail_uncovered_lines.json", "R1", "coverage"),
    ("r2_pass_with_judgment.json", "R2", "mutation"),
    ("r3_pass.json", "R3", "canary"),
    ("r3_fail_canary_survived_unexpected_pass.json", "R3", "canary"),
    ("r3_inconclusive_canary_inconclusive.json", "R3", "canary"),
]


@pytest.mark.parametrize("fixture, rigor, payload", _HOLLOW_CASES)
def test_a_judged_claim_whose_payload_was_deleted_is_rejected(
    validator: Draft202012Validator, fixture: str, rigor: str, payload: str,
):
    document = _verdict_document(fixture)
    assert why_invalid(validator, document) == [], "the canonical form must validate"

    claim = next(item for item in document["claims"] if item["rigor"] == rigor)
    assert payload in claim, f"{fixture} carries no {payload} to delete"
    del claim[payload]

    messages = why_invalid(validator, document)
    assert messages, (
        f"a {rigor} claim kept its judged status after its {payload} payload was "
        f"deleted -- the hollow-green lie"
    )


def test_mutants_survived_without_the_bucket_it_is_read_from_is_rejected(
    validator: Draft202012Validator,
):
    """The one member of the model's `_MUTATION_ONLY_REASON_CODES` the schema did
    not enforce. `NO_MUTANTS` and `ALL_MUTANTS_EQUIVALENT` were already required
    by their own branches; this was the third."""
    document = _verdict_document("r2_fail_mutants_survived.json")
    assert why_invalid(validator, document) == []

    claim = next(item for item in document["claims"] if item["rigor"] == "R2")
    assert claim["reason_code"] == "MUTANTS_SURVIVED"
    del claim["mutation"]

    assert why_invalid(validator, document), (
        "MUTANTS_SURVIVED validated with no survived bucket to have read it from"
    )


_STILL_LEGAL = [
    ("r2_error_exec_failed_baseline_crashed.json", "R2"),
    ("r2_inconclusive_mutation_unsupported.json", "R2"),
    ("r2_no_measurement_head_changed.json", "R2"),
    ("r2_error_mutation_discovery_failed.json", "R2"),
    ("r1_no_measurement_dirty_tree.json", "R1"),
    ("r1_error_git_failed.json", "R1"),
    ("r1_error_unreadable_artifact.json", "R1"),
]


@pytest.mark.parametrize("fixture, rigor", _STILL_LEGAL)
def test_a_payload_free_claim_a_producer_really_emits_stays_valid(
    validator: Draft202012Validator, fixture: str, rigor: str,
):
    """The scope guard, and the half the first attempt at this rule got wrong.

    A-116's own truthful propagation shape is a payload-free R2 claim reusing a
    failed baseline's `(outcome, reason_code)` verbatim, and the
    ERROR/NO_MEASUREMENT/BUDGET_EXCEEDED terminals describe machinery that never
    produced a result to judge. Every one of these is real producer output. If
    A-251's branches ever widen to cover them, `assay run` starts emitting
    artifacts its own schema rejects -- the exact defect A-240's overbroad
    attempt would have shipped.
    """
    document = _verdict_document(fixture)
    claim = next(item for item in document["claims"] if item["rigor"] == rigor)
    assert not any(
        key in claim for key in ("coverage", "mutation", "canary")
    ), f"{fixture} is not the payload-free shape this test exists to protect"
    assert why_invalid(validator, document) == []


def test_a_populated_shard_verdict_validates_against_the_shipped_schema(
    validator: Draft202012Validator,
):
    """(B012 remediation, D-10) `mutation.candidate_ids`/`progress_artifact`
    shipped on the wrong `$defs` entries (`coverage` and `claim`, which
    `additionalProperties: false` on `$defs/mutation` then rejected) --
    proven only by hand-rolling a schema fragment during remediation, never
    by an actual test against the real, shipped schema. This is that test:
    reverting the `$defs/mutation` move must turn it red.
    """
    document = _verdict_document("r2_fail_mutants_survived.json")
    assert why_invalid(validator, document) == [], "the canonical form must validate"

    claim = next(item for item in document["claims"] if item["rigor"] == "R2")
    claim["mutation"]["candidate_ids"] = [
        "a" * 64,
        "b" * 64,
    ]
    claim["mutation"]["progress_artifact"] = ".assay/package.progress.jsonl"

    assert why_invalid(validator, document) == [], (
        "a populated shard verdict (candidate_ids + progress_artifact) must "
        "validate against the shipped schema"
    )
