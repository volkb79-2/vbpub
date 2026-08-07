"""O1 — one Verdict per outcome serialises and validates against the shipped FILE.

The negative this defends: *the schema is validated from an in-process object,
so A-029's claim that a consumer validates without importing assay is unproven
and the shipped file may not even parse.* So every validation below goes through
``conftest.SCHEMA_PATH`` — the file on disk — read by ``json.loads``, never a
dict built in Python.

That alone would be hollow, and the oracle's sibling O2 says why: six verdicts
validating against a schema that requires nothing is green and proves nothing.
Three things here stop that:

* the schema is asserted to be a *valid* draft 2020-12 schema, so "it accepts
  everything" cannot be hiding behind "it is not a schema at all";
* a vacuity block asserts that obviously-broken documents are REJECTED, so an
  emptied schema fails this module rather than passing it;
* and the round-trip is compared against **hand-written JSON artifacts** under
  ``tests/fixtures/verdicts/``, which nothing in ``assay`` generated. Comparing
  ``Verdict.to_dict()`` against ``Verdict`` would only prove the serialiser
  agrees with itself — A-067, and the same reason the config tests round-trip
  against ``tomllib``.
"""

from __future__ import annotations

import json

import pytest
from conftest import SCHEMA_PATH, VERDICT_FIXTURES, verdict_fixture, why_invalid
from jsonschema import Draft202012Validator

from assay.errors import Outcome, ReasonCode
from assay.verdict import (
    VERDICT_SCHEMA_VERSION,
    Claim,
    Coverage,
    Evidence,
    EvidenceDeclaration,
    Verdict,
    iso_utc,
    load_schema,
    schema_text,
)

VERSION = "0.1.0"


def build(outcome: str) -> Verdict:
    """The six verdicts, written out by hand as CONSTRUCTOR CALLS.

    Deliberately authored independently of the JSON fixtures they are compared
    against: two hand-written descriptions of the same artifact, in two
    languages. If the model renames a key, drops one, or invents one, the two
    stop agreeing.
    """
    if outcome == "PASS":
        return Verdict(
            lane="package",
            commit="1" * 40,
            outcome=Outcome.PASS,
            started="2026-08-06T09:00:00+00:00",
            ended="2026-08-06T09:00:07+00:00",
            assay_version=VERSION,
            declared_rigor=("R0", "R1"),
            declared_evidence=(),
            argv_declared=("pytest", "tests", "-q", "--cov-report=json:cov.json"),
            argv_appended=(),
            argv_effective=("pytest", "tests", "-q", "--cov-report=json:cov.json"),
            env_declared={"MOCK_MODE": "true"},
            env_effective={"MOCK_MODE": "true"},
            claims=(
                Claim(
                    rigor="R0",
                    source="computed",
                    status=Outcome.PASS,
                    verified_by_assay=True,
                ),
                Claim(
                    rigor="R1",
                    source="computed",
                    status=Outcome.PASS,
                    verified_by_assay=True,
                    coverage=Coverage(
                        covered=12,
                        changed_executable=12,
                        pct=100.0,
                        considered=4,
                        missing_lines={},
                        files_missing_coverage=(),
                    ),
                ),
            ),
        )
    if outcome == "FAIL":
        return Verdict(
            lane="package",
            commit="2" * 40,
            outcome=Outcome.FAIL,
            reason_code=ReasonCode.UNCOVERED_LINES,
            started="2026-08-06T09:10:00+00:00",
            ended="2026-08-06T09:10:31+00:00",
            assay_version=VERSION,
            declared_rigor=("R0", "R1"),
            declared_evidence=(
                EvidenceDeclaration(
                    source="attested", key="adversarial-review"
                ),
            ),
            argv_declared=("pytest", "tests", "-q"),
            argv_appended=("-k", "not slow"),
            argv_effective=("pytest", "tests", "-q", "-k", "not slow"),
            env_declared={"TZ": "UTC"},
            env_effective={"TZ": "UTC", "PATH": "/usr/bin"},
            claims=(
                Claim(
                    rigor="R0",
                    source="computed",
                    status=Outcome.PASS,
                    verified_by_assay=True,
                ),
                Claim(
                    rigor="R1",
                    source="computed",
                    status=Outcome.FAIL,
                    verified_by_assay=True,
                    reason_code=ReasonCode.UNCOVERED_LINES,
                    coverage=Coverage(
                        covered=2,
                        changed_executable=3,
                        pct=66.67,
                        considered=1,
                        missing_lines={"src/assay/verdict.py": frozenset({42})},
                        files_missing_coverage=(),
                    ),
                ),
            ),
            evidence=(
                Evidence(
                    source="attested",
                    key="adversarial-review",
                    status=Outcome.PASS,
                    verified_by_assay=False,
                    producer="reviewer@example",
                    attested_commit="2" * 40,
                    reviewed_paths=("src/assay/verdict.py",),
                ),
            ),
        )
    raise AssertionError(f"no constructor written for {outcome}")


# The two rich cases above are spelled out; the remaining four are short enough
# to build inline, and are kept separate so a reader can see each whole.
def build_error() -> Verdict:
    # A-027: emitted even when assay itself broke. No lane resolved, so the
    # lane-resolved group is ABSENT rather than empty — `argv_declared: []`
    # would assert that the lane declared no argv, which is false.
    return Verdict(
        lane="package",
        commit="3" * 40,
        outcome=Outcome.ERROR,
        reason_code=ReasonCode.BAD_LANE_CONFIG,
        started="2026-08-06T09:20:00+00:00",
        ended="2026-08-06T09:20:00+00:00",
        assay_version=VERSION,
    )


def build_no_measurement() -> Verdict:
    return Verdict(
        lane="package",
        commit="4" * 40,
        outcome=Outcome.NO_MEASUREMENT,
        reason_code=ReasonCode.DIRTY_TREE,
        started="2026-08-06T09:30:00+00:00",
        ended="2026-08-06T09:30:12+00:00",
        assay_version=VERSION,
        declared_rigor=("R0", "R1"),
        declared_evidence=(),
        argv_declared=("pytest", "tests", "-q"),
        argv_appended=(),
        argv_effective=("pytest", "tests", "-q"),
        env_declared={},
        env_effective={},
        claims=(
            Claim(
                rigor="R0",
                source="computed",
                status=Outcome.PASS,
                verified_by_assay=True,
            ),
            # A-025: no coverage payload at all. Not a zeroed one.
            Claim(
                rigor="R1",
                source="computed",
                status=Outcome.NO_MEASUREMENT,
                verified_by_assay=True,
                reason_code=ReasonCode.DIRTY_TREE,
            ),
        ),
    )


def build_budget_exceeded() -> Verdict:
    return Verdict(
        lane="integration",
        commit="5" * 40,
        outcome=Outcome.BUDGET_EXCEEDED,
        reason_code=ReasonCode.LANE_TIMEOUT,
        started="2026-08-06T09:40:00+00:00",
        ended="2026-08-06T09:45:00+00:00",
        assay_version=VERSION,
        declared_rigor=("R0",),
        declared_evidence=(),
        argv_declared=("pytest", "tests/integration", "-q"),
        argv_appended=(),
        argv_effective=("pytest", "tests/integration", "-q"),
        env_declared={"MOCK_MODE": "false"},
        env_effective={"MOCK_MODE": "false"},
        claims=(
            Claim(
                rigor="R0",
                source="computed",
                status=Outcome.BUDGET_EXCEEDED,
                verified_by_assay=True,
                reason_code=ReasonCode.LANE_TIMEOUT,
            ),
        ),
    )


def build_inconclusive() -> Verdict:
    return Verdict(
        lane="package",
        commit="6" * 40,
        outcome=Outcome.INCONCLUSIVE,
        reason_code=ReasonCode.NO_MUTANTS,
        started="2026-08-06T09:50:00+00:00",
        ended="2026-08-06T09:52:18+00:00",
        assay_version=VERSION,
        declared_rigor=("R0", "R2"),
        declared_evidence=(),
        argv_declared=("pytest", "tests", "-q"),
        argv_appended=(),
        argv_effective=("pytest", "tests", "-q"),
        env_declared={},
        env_effective={},
        claims=(
            Claim(
                rigor="R0",
                source="computed",
                status=Outcome.PASS,
                verified_by_assay=True,
            ),
            Claim(
                rigor="R2",
                source="computed",
                status=Outcome.INCONCLUSIVE,
                verified_by_assay=True,
                reason_code=ReasonCode.NO_MUTANTS,
            ),
        ),
    )


BUILDERS = {
    "PASS": lambda: build("PASS"),
    "FAIL": lambda: build("FAIL"),
    "ERROR": build_error,
    "NO_MEASUREMENT": build_no_measurement,
    "BUDGET_EXCEEDED": build_budget_exceeded,
    "INCONCLUSIVE": build_inconclusive,
}


# --- the fixtures really cover the vocabulary ---------------------------------


def test_there_is_one_verdict_per_outcome_and_no_outcome_is_unproven():
    """Guard the guard: a missing outcome must be a failure, not a silent gap."""
    assert set(BUILDERS) == {o.value for o in Outcome}
    assert set(VERDICT_FIXTURES) == {o.value for o in Outcome}
    assert len(Outcome) == 6


# --- the schema is a real schema, loaded from a real file ---------------------


def test_the_shipped_schema_is_a_valid_draft_2020_12_schema(schema: dict):
    Draft202012Validator.check_schema(schema)
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"


def test_the_schema_is_resolved_as_a_file_not_built_in_python():
    # A-029's whole point: a consumer validates against a FILE. If this ever
    # became a dict literal in verdict.py, the shipped artifact would be a
    # fiction and every test here would still pass.
    assert SCHEMA_PATH.is_file()
    assert SCHEMA_PATH.suffix == ".json"
    assert json.loads(SCHEMA_PATH.read_text(encoding="utf-8")) == load_schema()
    assert schema_text() == SCHEMA_PATH.read_text(encoding="utf-8")


# --- every outcome serialises and validates -----------------------------------


@pytest.mark.parametrize("outcome", sorted(BUILDERS))
def test_every_outcome_serialises_to_json_and_validates_against_the_file(
    outcome: str, validator: Draft202012Validator
):
    document = json.loads(BUILDERS[outcome]().to_json())

    assert why_invalid(validator, document) == []
    assert document["outcome"] == outcome
    assert document["schema_version"] == VERDICT_SCHEMA_VERSION
    assert isinstance(document["schema_version"], int)


@pytest.mark.parametrize("outcome", sorted(BUILDERS))
def test_serialisation_equals_the_hand_written_artifact(outcome: str):
    """The independent oracle (A-041/A-067).

    The right-hand side was written by hand as JSON text; nothing in assay
    produced it. A renamed key, a dropped field, or an invented one shows up
    here as an inequality rather than as something a reviewer has to notice.
    """
    assert json.loads(BUILDERS[outcome]().to_json()) == verdict_fixture(outcome)


@pytest.mark.parametrize("outcome", sorted(VERDICT_FIXTURES))
def test_the_hand_written_artifacts_themselves_validate(
    outcome: str, validator: Draft202012Validator
):
    assert why_invalid(validator, verdict_fixture(outcome)) == []


@pytest.mark.parametrize("outcome", sorted(BUILDERS))
def test_the_exit_code_is_the_verdict(outcome: str):
    verdict = BUILDERS[outcome]()
    assert verdict.exit_code == Outcome(outcome).exit_code
    assert json.loads(verdict.to_json())["exit_code"] == verdict.exit_code


def test_to_json_is_stable_and_newline_terminated():
    text = build("PASS").to_json()
    assert text.endswith("\n")
    assert text == build("PASS").to_json()


# --- vacuity guards: an emptied schema must FAIL this module ------------------


@pytest.mark.parametrize(
    "document,why",
    [
        ({}, "an empty object is not a verdict"),
        ({"outcome": "PASS"}, "a verdict is more than an outcome"),
        ("not an object", "a verdict is an object"),
        ([], "a verdict is not an array"),
    ],
)
def test_obviously_broken_documents_are_rejected(
    document, why: str, validator: Draft202012Validator
):
    assert not validator.is_valid(document), why


def test_an_unknown_outcome_is_rejected(validator: Draft202012Validator):
    document = verdict_fixture("PASS")
    assert why_invalid(validator, document) == []  # both directions, one body

    document["outcome"] = "PROBABLY_FINE"
    assert not validator.is_valid(document)


def test_an_unknown_top_level_key_is_rejected(validator: Draft202012Validator):
    document = verdict_fixture("PASS")
    assert why_invalid(validator, document) == []

    document["retry_policy"] = "always"
    messages = why_invalid(validator, document)
    assert messages, "an unknown key was silently accepted"
    assert any("retry_policy" in message for message in messages), messages


def test_a_string_schema_version_is_rejected(validator: Draft202012Validator):
    """O5's second clause: `schema_version` is an INTEGER on every verdict."""
    document = verdict_fixture("PASS")
    assert why_invalid(validator, document) == []

    document["schema_version"] = "1"
    assert not validator.is_valid(document)


def test_a_naive_timestamp_is_rejected(validator: Draft202012Validator):
    document = verdict_fixture("PASS")
    assert why_invalid(validator, document) == []

    document["started"] = "2026-08-06T09:00:00"
    assert not validator.is_valid(document)


def test_the_model_refuses_a_naive_timestamp_too():
    with pytest.raises(ValueError, match="explicit offset"):
        Verdict(
            lane="package",
            commit="7" * 40,
            outcome=Outcome.PASS,
            started="2026-08-06T09:00:00",
            ended="2026-08-06T09:00:01+00:00",
            assay_version=VERSION,
        )


@pytest.mark.parametrize("field", ["lane", "commit", "assay_version"])
def test_the_model_refuses_an_empty_identity_field(field: str):
    kwargs = {
        "lane": "package",
        "commit": "7" * 40,
        "outcome": Outcome.PASS,
        "started": "2026-08-06T09:00:00+00:00",
        "ended": "2026-08-06T09:00:01+00:00",
        "assay_version": VERSION,
    }
    assert Verdict(**kwargs).outcome is Outcome.PASS  # the untouched form loads

    with pytest.raises(ValueError, match=field):
        Verdict(**{**kwargs, field: ""})


def test_the_model_refuses_a_foreign_schema_version():
    with pytest.raises(ValueError, match="schema_version"):
        Verdict(
            lane="package",
            commit="7" * 40,
            outcome=Outcome.PASS,
            started="2026-08-06T09:00:00+00:00",
            ended="2026-08-06T09:00:01+00:00",
            assay_version=VERSION,
            schema_version=3,
        )


def test_the_model_refuses_something_that_is_not_an_outcome():
    with pytest.raises(ValueError, match="must be an Outcome"):
        Verdict(
            lane="package",
            commit="7" * 40,
            outcome="PASS",
            started="2026-08-06T09:00:00+00:00",
            ended="2026-08-06T09:00:01+00:00",
            assay_version=VERSION,
        )


# --- the timestamp helper -----------------------------------------------------


def test_iso_utc_renders_a_timestamp_the_schema_accepts(
    validator: Draft202012Validator,
):
    from datetime import datetime, timedelta, timezone

    document = verdict_fixture("PASS")
    for zone in (timezone.utc, timezone(timedelta(hours=2))):
        document["started"] = iso_utc(datetime(2026, 8, 6, 9, 0, 0, tzinfo=zone))
        assert why_invalid(validator, document) == [], document["started"]


def test_iso_utc_refuses_a_naive_datetime():
    from datetime import datetime

    with pytest.raises(ValueError, match="naive"):
        iso_utc(datetime(2026, 8, 6, 9, 0, 0))
