"""Transparency of what actually ran (DESIGN-GUIDE §6, A-036) — and the rule
that decides when a field is omitted rather than emptied.

No oracle names this; it is Work item 1's `argv_declared` / `argv_appended` /
`argv_effective` / `env_declared` / `env_effective`, and the principle that
governs them:

    **Absent means unknowable. Empty means known-and-empty.**

`coverage` is omitted under NO_MEASUREMENT because a number that is not a
measurement gets read as one (A-025). `argv_declared = []` on a verdict where no
lane ever loaded asserts *"the lane declared no argv"*, which is false — same
failure, same remedy. So the lane-resolved group is all-present or all-absent,
and a verdict for a lane that never loaded carries none of it.

The negative, if this were absent: an appended flag is invisible in the verdict,
so a gate PASS can describe a run that skipped most of the suite.
"""

from __future__ import annotations

import json

import pytest
from conftest import verdict_fixture, why_invalid
from jsonschema import Draft202012Validator

from assay.errors import Outcome, ReasonCode
from assay.verdict import LANE_RESOLVED_FIELDS, Verdict

RESOLVED = {
    "lane": "package",
    "commit": "a" * 40,
    "started": "2026-08-06T09:00:00+00:00",
    "ended": "2026-08-06T09:00:07+00:00",
    "assay_version": "0.1.0",
    "outcome": Outcome.PASS,
    "declared_rigor": ("R0",),
    "declared_evidence": (),
    "argv_declared": ("pytest", "-q"),
    "argv_appended": (),
    "argv_effective": ("pytest", "-q"),
    "env_declared": {"TZ": "UTC"},
    "env_effective": {"TZ": "UTC"},
    "scope": "S2",
    "enforcement": "gate",
}


def a_claim() -> tuple:
    from assay.verdict import Claim

    return (
        Claim(
            rigor="R0", source="computed", status=Outcome.PASS, verified_by_assay=True
        ),
    )


# --- recorded on every outcome where a lane resolved --------------------------


@pytest.mark.parametrize(
    "outcome", ["PASS", "FAIL", "NO_MEASUREMENT", "BUDGET_EXCEEDED", "INCONCLUSIVE"]
)
def test_what_ran_is_recorded_on_every_outcome_where_a_lane_resolved(outcome: str):
    document = verdict_fixture(outcome)
    for field in LANE_RESOLVED_FIELDS:
        assert field in document, f"{outcome} does not record {field}"


def test_a_verdict_for_a_lane_that_never_loaded_omits_them_rather_than_emptying():
    """A-027 emits the artifact even when assay broke; ruling: it omits what it
    cannot know rather than asserting an empty truth."""
    document = verdict_fixture("ERROR")
    assert document["outcome"] == "ERROR"
    assert document["reason_code"] == "BAD_LANE_CONFIG"

    for field in LANE_RESOLVED_FIELDS:
        assert field not in document, (
            f"{field} was emitted for a lane that never loaded; an empty value "
            f"asserts a fact, and this one would be false"
        )
    assert document["claims"] == []


def test_the_model_omits_the_group_when_it_was_never_given_one():
    verdict = Verdict(
        lane="package",
        commit="a" * 40,
        outcome=Outcome.ERROR,
        reason_code=ReasonCode.BAD_LANE_CONFIG,
        started="2026-08-06T09:00:00+00:00",
        ended="2026-08-06T09:00:00+00:00",
        assay_version="0.1.0",
    )
    document = json.loads(verdict.to_json())

    assert not (set(LANE_RESOLVED_FIELDS) & set(document))
    assert verdict.argv_modified is None


# --- all present or all absent, never a mixture -------------------------------


@pytest.mark.parametrize(
    "dropped",
    [f for f in LANE_RESOLVED_FIELDS if f != "argv_modified"],
)
def test_the_model_refuses_a_half_populated_group(dropped: str):
    assert Verdict(**RESOLVED, claims=a_claim()).lane == "package"  # untouched form

    with pytest.raises(ValueError, match="all present or all absent"):
        Verdict(**{**RESOLVED, dropped: None}, claims=a_claim())


@pytest.mark.parametrize(
    "dropped",
    [f for f in LANE_RESOLVED_FIELDS],
)
def test_the_schema_refuses_a_half_populated_group(
    dropped: str, validator: Draft202012Validator
):
    document = verdict_fixture("PASS")
    assert why_invalid(validator, document) == []

    del document[dropped]
    messages = why_invalid(validator, document)
    assert messages, f"a verdict missing only {dropped} was accepted"


# --- argv_effective is really declared + appended -----------------------------


def test_an_appended_flag_is_recorded_in_all_three_places():
    verdict = Verdict(
        **{
            **RESOLVED,
            "argv_appended": ("-k", "not slow"),
            "argv_effective": ("pytest", "-q", "-k", "not slow"),
        },
        claims=a_claim(),
    )
    document = json.loads(verdict.to_json())

    assert document["argv_declared"] == ["pytest", "-q"]
    assert document["argv_appended"] == ["-k", "not slow"]
    assert document["argv_effective"] == ["pytest", "-q", "-k", "not slow"]
    assert document["argv_modified"] is True


def test_argv_modified_is_false_when_nothing_was_appended():
    document = json.loads(Verdict(**RESOLVED, claims=a_claim()).to_json())
    assert document["argv_appended"] == []
    assert document["argv_modified"] is False


def test_the_model_refuses_an_effective_argv_that_is_not_declared_plus_appended():
    """A PASS must never be able to describe a run that skipped most of the
    suite."""
    with pytest.raises(ValueError, match="is not argv_declared"):
        Verdict(
            **{
                **RESOLVED,
                "argv_appended": ("-k", "not slow"),
                "argv_effective": ("pytest", "-q"),
            },
            claims=a_claim(),
        )


def test_the_model_refuses_an_empty_declared_argv():
    with pytest.raises(ValueError, match="argv_declared is empty"):
        Verdict(
            **{**RESOLVED, "argv_declared": (), "argv_effective": ()},
            claims=a_claim(),
        )


@pytest.mark.parametrize("modified", [True, False])
def test_the_schema_refuses_an_argv_modified_that_disagrees(
    modified: bool, validator: Draft202012Validator
):
    document = verdict_fixture("FAIL" if modified else "PASS")
    assert why_invalid(validator, document) == []
    assert document["argv_modified"] is modified

    document["argv_modified"] = not modified
    messages = why_invalid(validator, document)
    assert messages, (
        "argv_modified was allowed to disagree with argv_appended, which is how "
        "an appended flag becomes invisible in the verdict"
    )


def test_the_declared_environment_is_carried_verbatim():
    """A-019: `env` is declared-only, and `env_effective` is what the process
    actually saw. Both are recorded, so the two can be compared by a reader."""
    document = verdict_fixture("FAIL")
    assert document["env_declared"] == {"TZ": "UTC"}
    assert document["env_effective"] == {"TZ": "UTC", "PATH": "/usr/bin"}


def test_a_non_string_environment_value_is_rejected(validator: Draft202012Validator):
    document = verdict_fixture("PASS")
    assert why_invalid(validator, document) == []

    document["env_declared"] = {"MOCK_MODE": True}
    assert not validator.is_valid(document)


def test_an_empty_argv_declared_is_rejected_by_the_schema(
    validator: Draft202012Validator,
):
    document = verdict_fixture("PASS")
    assert why_invalid(validator, document) == []

    document["argv_declared"] = []
    assert not validator.is_valid(document)
