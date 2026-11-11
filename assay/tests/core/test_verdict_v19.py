"""Verdict schema v19 (B251): per-candidate outcome errors, plus the
profile/schedule summaries, worker counts and failing test id.

The golden verdicts are hand-written fixtures; ``jsonschema`` is the
independent oracle and ``assay verify`` the raw verifier, and every rejection
test asserts the INTENDED reason.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from conftest import R2_TELEMETRY, VERDICT_FIXTURE_DIR, why_invalid

from assay import verdict as verdict_module
from assay import verify as verify_module
from assay.verdict import JudgmentR2, VERDICT_SCHEMA_VERSION
from assay.verify import verify_document

MEASURED = "r2_pass.json"
PARTIAL = "r2_fail_mutants_survived.json"
UNMEASURED = "r2_budget_exceeded_lane_timeout.json"
PARALLEL_MIXED = "r2_pass_with_judgment.json"
GOLDEN = (MEASURED, PARTIAL, UNMEASURED, PARALLEL_MIXED)


def _load(name: str) -> dict:
    return json.loads((VERDICT_FIXTURE_DIR / name).read_text(encoding="utf-8"))


def _killed(document: dict) -> list[dict]:
    return [
        entry
        for claim in document["claims"]
        if claim.get("mutation")
        for entry in claim["mutation"]["killed"]
    ]


def test_the_schema_version_is_19():
    assert VERDICT_SCHEMA_VERSION == 19
    assert _load(MEASURED)["schema_version"] == 19


@pytest.mark.parametrize("name", GOLDEN)
def test_the_golden_verdicts_validate_against_the_schema_and_the_verifier(name, validator):
    document = _load(name)
    assert why_invalid(validator, document) == []
    assert verify_document(document) == []


def test_golden_verdicts_cover_a_measured_a_partial_and_an_unmeasured_campaign():
    statuses = {name: _load(name)["judgment"]["r2"]["profile"]["status_counts"] for name in GOLDEN}
    assert statuses[MEASURED] == {"MEASURED": 2, "PARTIAL": 0, "NO_MEASUREMENT": 0}
    assert statuses[PARTIAL] == {"MEASURED": 1, "PARTIAL": 1, "NO_MEASUREMENT": 0}
    assert statuses[UNMEASURED] == {"MEASURED": 0, "PARTIAL": 0, "NO_MEASUREMENT": 2}
    assert _load(UNMEASURED)["judgment"]["r2"]["profile"]["reason_counts"] == {
        "OBSERVER_UNAVAILABLE": 2
    }


def test_a_v18_verdict_is_refused_on_its_version_alone():
    document = _load(MEASURED)
    document["schema_version"] = 18
    failures = verify_document(document)
    assert len(failures) == 1
    assert "schema_version 18 is not this verifier's version 19" in failures[0]


def test_the_schema_refuses_v18(validator):
    document = _load(MEASURED)
    document["schema_version"] = 18
    assert any("19 was expected" in message for message in why_invalid(validator, document))


def test_candidate_outcome_error_is_allowed_only_on_native_crashed_candidates(validator):
    document = _load("r2_crashed_descendant_leak.json")
    mutation = document["claims"][1]["mutation"]
    crashed = mutation["crashed"][0]
    crashed["candidate_error"] = {
        "reason_code": "EXEC_FAILED",
        "message": "ValueError: forced candidate outcome validation failure",
    }
    crashed.pop("underlying_outcome")
    assert why_invalid(validator, document) == []
    assert verify_document(document) == []

    mutation["survived"].append(crashed)
    mutation["crashed"].clear()
    assert why_invalid(validator, document)
    assert verify_document(document)


def test_candidate_outcome_error_is_rejected_by_model_outside_crashed_bucket():
    document = _load("r2_crashed_descendant_leak.json")
    raw = document["claims"][1]["mutation"]["crashed"][0]
    raw["candidate_error"] = {
        "reason_code": "EXEC_FAILED",
        "message": "ValueError: forced candidate outcome validation failure",
    }
    raw.pop("underlying_outcome")
    candidate = verify_module._reconstruct_mutant_outcome(raw)

    with pytest.raises(
        ValueError,
        match="candidate_error is only legal on a native crashed candidate",
    ):
        verdict_module.Mutation(
            candidate_count=1,
            total=1,
            survived=(candidate,),
        )


# -- B203: worker counts ---------------------------------------------------------


def test_a_resumed_campaign_with_mixed_worker_counts_reports_the_maximum_and_each_count():
    r2 = _load(PARALLEL_MIXED)["judgment"]["r2"]
    assert r2["jobs"] == 3 and r2["attribution"] == "overlap-conservative"
    assert r2["worker_counts"] == {"1": 1, "3": 1}


def test_jobs_must_be_the_campaign_maximum():
    document = _load(PARALLEL_MIXED)
    document["judgment"]["r2"]["jobs"] = 2
    document["judgment"]["r2"]["schedule"]["jobs"] = 2
    failures = verify_document(document)
    assert any("must be the campaign maximum worker count 3" in f for f in failures), failures


@pytest.mark.parametrize("bad", [{"0": 1}, {"03": 1}, {"x": 1}, {"1": 0}, {"1": True}, {"1.5": 1}])
def test_worker_counts_refuse_malformed_keys_and_counts(bad, validator):
    document = _load(MEASURED)
    document["judgment"]["r2"]["worker_counts"] = bad
    assert why_invalid(validator, document)  # the schema refuses it ...
    assert verify_document(document)  # ... and so does the raw verifier


def test_worker_counts_are_native_only_and_required():
    document = _load(MEASURED)
    del document["judgment"]["r2"]["worker_counts"]
    assert verify_document(document)


# -- B206: the profile summary ---------------------------------------------------


def test_reason_counts_must_account_for_exactly_the_no_measurement_candidates():
    document = _load(UNMEASURED)
    document["judgment"]["r2"]["profile"]["reason_counts"] = {"OBSERVER_UNAVAILABLE": 1}
    failures = verify_document(document)
    assert any("reason_counts must account for exactly" in f for f in failures), failures


def test_the_profile_summary_refuses_an_unknown_reason_and_an_unknown_status(validator):
    document = _load(UNMEASURED)
    document["judgment"]["r2"]["profile"]["reason_counts"] = {"MADE_UP": 2}
    assert why_invalid(validator, document)
    assert verify_document(document)
    document = _load(MEASURED)
    document["judgment"]["r2"]["profile"]["status_counts"]["GUESSED"] = 1
    assert why_invalid(validator, document)
    assert verify_document(document)


def test_the_profile_summary_carries_no_per_candidate_number(validator):
    document = _load(MEASURED)
    document["judgment"]["r2"]["profile"]["peak_rss_bytes"] = 1
    assert why_invalid(validator, document)
    assert verify_document(document)
    document = _load(MEASURED)
    document["judgment"]["r2"]["peak_rss_bytes"] = 1
    assert why_invalid(validator, document)


def test_profile_and_schedule_are_required_for_native_and_forbidden_for_ingested(validator):
    document = _load(MEASURED)
    del document["judgment"]["r2"]["profile"]
    assert why_invalid(validator, document)
    document = _load(MEASURED)
    del document["judgment"]["r2"]["schedule"]
    assert why_invalid(validator, document)


# -- B207: the schedule summary --------------------------------------------------


def test_a_serial_schedule_is_canonical_with_null_thresholds_and_error():
    schedule = _load(MEASURED)["judgment"]["r2"]["schedule"]
    assert schedule["policy"] == "canonical"
    assert schedule["thresholds"] is None and schedule["error"] is None


def test_a_parallel_schedule_carries_thresholds_sources_and_the_error_summary():
    schedule = _load(PARALLEL_MIXED)["judgment"]["r2"]["schedule"]
    assert schedule["policy"] == "tem-lpt"
    assert set(schedule["thresholds"]) == {"high_bytes", "low_bytes", "high_factor", "low_factor"}
    assert schedule["memory_budget_bytes"] == 2_000_000_000
    assert schedule["error"]["admission_overshoots"] == 0


@pytest.mark.parametrize(
    "change",
    [
        lambda s: s.update(policy="round-robin"),
        lambda s: s.update(thresholds=None),  # tem-lpt needs thresholds
        lambda s: s["error"].pop("class_confusion"),
        lambda s: s["error"]["rss_ratio"].update(p99=1.0),
        lambda s: s["prediction_sources"].update(guess=1),
        lambda s: s.update(memory_budget_bytes=0),
    ],
)
def test_the_schedule_summary_refuses_malformed_shapes(change, validator):
    document = _load(PARALLEL_MIXED)
    change(document["judgment"]["r2"]["schedule"])
    assert verify_document(document)


def test_a_canonical_schedule_may_not_carry_an_error_summary():
    document = _load(MEASURED)
    document["judgment"]["r2"]["schedule"]["error"] = _load(PARALLEL_MIXED)["judgment"]["r2"][
        "schedule"
    ]["error"]
    assert any("error is null for the canonical policy" in f for f in verify_document(document))


# -- B205: the first failing test ------------------------------------------------


def test_a_killed_candidate_with_a_known_failing_test_carries_it_and_null_otherwise():
    assert [e["first_failing_test"] for e in _killed(_load(MEASURED))] == [
        "tests/test_checks.py::test_boundary",
        None,
    ]
    assert [e["first_failing_test"] for e in _killed(_load(PARTIAL))] == [None]


def test_a_native_killed_entry_without_the_key_is_refused(validator):
    document = _load(MEASURED)
    for entry in _killed(document):
        del entry["first_failing_test"]
    assert why_invalid(validator, document)
    failures = verify_document(document)
    assert any("first_failing_test" in f for f in failures), failures


def test_first_failing_test_is_forbidden_off_the_killed_bucket(validator):
    document = _load(PARTIAL)
    survivor = document["claims"][-1]["mutation"]["survived"][0]
    survivor["first_failing_test"] = None
    assert why_invalid(validator, document)
    failures = verify_document(document)
    assert failures, "a survivor named a failing test"


def test_first_failing_test_refuses_an_empty_string(validator):
    document = _load(MEASURED)
    _killed(document)[0]["first_failing_test"] = ""
    assert why_invalid(validator, document)
    assert verify_document(document)


# -- the model -------------------------------------------------------------------


def test_the_model_serialises_the_v18_fields_and_rejects_their_absence():
    kwargs = dict(
        jobs=1, attribution="serial", max_mutants=50, operators=("python:compare-swap",),
        kill_attribution="unattributed", cold_witness_kills=False,
    )
    r2 = JudgmentR2(**kwargs, **R2_TELEMETRY)
    wire = r2.to_dict()
    assert wire["worker_counts"] == {} and wire["profile"]["observer"]["name"] == "cgprofile"
    assert wire["schedule"]["policy"] == "canonical"
    for missing in ("worker_counts", "profile", "schedule"):
        partial = {k: v for k, v in R2_TELEMETRY.items() if k != missing}
        with pytest.raises(ValueError, match=missing):
            JudgmentR2(**kwargs, **partial)


def test_the_model_checks_jobs_against_worker_counts():
    kwargs = dict(
        attribution="overlap-conservative", max_mutants=50, operators=("python:compare-swap",),
        kill_attribution="unattributed", cold_witness_kills=False,
    )
    telemetry = copy.deepcopy(R2_TELEMETRY)
    telemetry["worker_counts"] = {"1": 2, "4": 1}
    with pytest.raises(ValueError, match="campaign maximum worker count 4"):
        JudgmentR2(jobs=2, **kwargs, **telemetry)
    assert JudgmentR2(jobs=4, **kwargs, **telemetry).to_dict()["worker_counts"] == {"1": 2, "4": 1}


def test_the_vocabularies_match_the_observer_client():
    from assay import observer_windows

    assert verdict_module.R2_PROFILE_REASONS == observer_windows.REASONS
    assert verdict_module.R2_PROFILE_STATUSES == observer_windows.STATUSES
    schema = json.loads(
        (Path(verdict_module.__file__).parent / "schemas" / "verdict.schema.json").read_text(
            encoding="utf-8"
        )
    )

    def find(node, key):
        if isinstance(node, dict):
            if key in node:
                yield node[key]
            for value in node.values():
                yield from find(value, key)
        elif isinstance(node, list):
            for value in node:
                yield from find(value, key)

    reason_counts = [
        item for item in find(schema, "reason_counts") if isinstance(item, dict) and "propertyNames" in item
    ]
    assert len(reason_counts) == 1
    assert tuple(reason_counts[0]["propertyNames"]["enum"]) == observer_windows.REASONS
