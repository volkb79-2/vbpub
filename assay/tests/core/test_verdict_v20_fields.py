"""B245 / verdict schema v20 additions, at the SCHEMA and MODEL layers: the
ingested `cargo-mutants` operator namespace, `judgment.r2.report_absent` and
`judgment.r2.changed_diff`. The raw verifier's own statement of the same rules
is in `test_verify_ingested_r2.py`.

Every document here is a mutated real run over the committed StrykerJS
artifact (the `ingested_document` fixture), rewritten into the two cargo-mutants
shapes by the same helpers the raw-layer tests use. `jsonschema` is the
independent oracle for the schema layer; the model layer is
`verify._reconstruct_verdict`, the exact construction the producer path goes
through. Each refusal asserts the INTENDED wording, never just "invalid".
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from conftest import VERDICT_FIXTURE_DIR, why_invalid

from assay import verify as raw_verify
from assay.errors import Outcome, ReasonCode
from assay.verdict import (
    ChangedDiff,
    claim_for,
    JudgmentR2,
    PRODUCER_VERSION_UNOBSERVED,
    VERDICT_SCHEMA_VERSION,
)
from assay.vocabulary import (
    ARGV_PLACEHOLDER_CHANGED_DIFF,
    COMMIT_OID_RE,
    INGESTED_OPERATOR_NAMESPACES,
    INGESTED_OPERATOR_RE,
    is_ingested_operator,
)

from conftest import GitRepo
from test_verify_ingested_r2 import (
    _r2_claim,
    as_cargo_mutants,
    as_report_absent,
    as_whole_target,
    build_ingested_document,
)

OID = "a" * 40


@pytest.fixture
def ingested_document(git_repo: GitRepo, tmp_path: Path) -> dict:
    return build_ingested_document(git_repo, tmp_path)


def _model(document: dict):
    """The model construction; raises ValueError on a refused document."""
    return raw_verify._reconstruct_verdict(document)


def _model_refuses(document: dict, fragment: str) -> None:
    with pytest.raises(ValueError) as caught:
        _model(document)
    assert fragment in str(caught.value), str(caught.value)


def _schema_refuses(validator, document: dict, fragment: str) -> None:
    messages = why_invalid(validator, document)
    assert any(fragment in message for message in messages), messages


# --------------------------------------------------------------------------
# Vocabulary
# --------------------------------------------------------------------------


def test_the_vocabulary_constants_are_what_the_schema_and_producers_use():
    assert VERDICT_SCHEMA_VERSION == 20
    assert INGESTED_OPERATOR_NAMESPACES == ("stryker", "cargo-mutants")
    assert ARGV_PLACEHOLDER_CHANGED_DIFF == "{assay.changed_diff}"
    assert PRODUCER_VERSION_UNOBSERVED == "unobserved"
    assert COMMIT_OID_RE.fullmatch("a" * 40)
    assert COMMIT_OID_RE.fullmatch("0" * 64)
    for bad in ("a" * 39, "a" * 41, "A" * 40, "g" * 40, "", "a" * 40 + "\n"):
        assert not COMMIT_OID_RE.fullmatch(bad), bad


@pytest.mark.parametrize(
    ("operator", "accepted"),
    [
        ("stryker:BlockStatement", True),
        ("cargo-mutants:FnValue", True),
        ("cargo-mutants:MatchArmGuard", True),
        ("other-tool:FnValue", False),
        ("cargo-mutants:", False),
        ("cargo-mutants", False),
        ("cargo_mutants:FnValue", False),
        ("python:compare-swap", False),
    ],
)
def test_the_ingested_operator_predicate_and_namespaces(operator: str, accepted: bool):
    assert is_ingested_operator(operator) is accepted
    assert bool(INGESTED_OPERATOR_RE.fullmatch(operator)) is accepted


# --------------------------------------------------------------------------
# The documents themselves: accepted at every layer
# --------------------------------------------------------------------------


def test_the_two_new_ingested_shapes_are_accepted_by_schema_and_model(
    ingested_document: dict, validator
):
    for document in (
        as_report_absent(ingested_document),
        as_cargo_mutants(ingested_document),
    ):
        assert why_invalid(validator, document) == []
        _model(document)


def test_the_model_serialises_report_absent_true_and_omits_the_default(
    ingested_document: dict,
):
    absent = _model(as_report_absent(ingested_document)).to_dict()
    assert absent["judgment"]["r2"]["report_absent"] is True
    assert "changed_diff" not in absent["judgment"]["r2"]
    plain = _model(ingested_document).to_dict()
    assert "report_absent" not in plain["judgment"]["r2"]
    assert "changed_diff" not in plain["judgment"]["r2"]


def test_the_model_round_trips_the_changed_diff(ingested_document: dict):
    document = as_cargo_mutants(ingested_document)
    assert _model(document).to_dict()["judgment"]["r2"]["changed_diff"] == (
        document["judgment"]["r2"]["changed_diff"]
    )


def test_a_cargo_mutants_operator_namespace_is_accepted_by_schema_and_model(
    ingested_document: dict, validator
):
    document = as_cargo_mutants(ingested_document)
    for bucket in ("killed", "survived"):
        for entry in _r2_claim(document)["mutation"][bucket]:
            entry["operator"] = "cargo-mutants:FnValue"
    assert why_invalid(validator, document) == []
    _model(document)


@pytest.mark.parametrize("operator", ["other-tool:FnValue", "cargo-mutants:"])
def test_a_foreign_or_empty_operator_is_refused_by_schema_and_model(
    ingested_document: dict, validator, operator: str
):
    document = as_cargo_mutants(ingested_document)
    _r2_claim(document)["mutation"]["survived"][0]["operator"] = operator
    assert why_invalid(validator, document), "the schema accepted the operator"
    with pytest.raises(ValueError):
        _model(document)


def test_a_v19_document_is_refused_by_schema_and_verifier(
    ingested_document: dict, validator
):
    document = copy.deepcopy(ingested_document)
    document["schema_version"] = 19
    assert why_invalid(validator, document)
    assert raw_verify.verify_document(document)[0].startswith(
        "schema_version 19 is not this verifier's version 20"
    )


# --------------------------------------------------------------------------
# report_absent, both directions, schema + model
# --------------------------------------------------------------------------


def _flag_only(document: dict) -> dict:
    """The report_absent flag and the unobserved version on an otherwise real
    measured payload: every OTHER model rule is satisfied, so the refusal below
    can only be `report_absent`'s own."""
    document = copy.deepcopy(document)
    r2 = document["judgment"]["r2"]
    r2["producer_tool"] = {
        "name": "cargo-mutants",
        "version": "unobserved",
        "report_schema_version": "outcomes.json",
    }
    r2["report_absent"] = True
    return document


def test_report_absent_on_a_measured_payload_is_refused_by_the_model(
    ingested_document: dict,
):
    """The claim is FAIL/MUTANTS_SURVIVED: a tool that wrote no report ran zero
    mutants, so it cannot have a verdict on 109 of them."""
    document = _flag_only(ingested_document)
    _model_refuses(document, "the R2 claim is not INCONCLUSIVE/NO_MUTANTS")


def test_report_absent_needs_each_clause_of_its_claim_guard_in_the_model(
    ingested_document: dict,
):
    """Every clause of the report_absent guard is independently needed: a zero
    payload with a wrong status, a wrong reason, or a non-zero candidate count
    is each refused with the guard's OWN wording."""
    guard = "the R2 claim is not INCONCLUSIVE/NO_MUTANTS"
    verdict = _model(as_report_absent(ingested_document))
    claim = claim_for(verdict.claims, "R2")
    policy = verdict.judgment.r2
    verdict._check_v20_ingested_r2(claim, policy)  # the legal shape passes
    # The claim record's own status/reason/payload pairing refuses most wrong
    # shapes before this guard is reached, so each clause is exercised on the
    # guard directly, flipping exactly ONE field of an otherwise legal claim.
    for field, wrong in (
        ("reason_code", ReasonCode.MUTATION_UNSUPPORTED),
        ("status", Outcome.FAIL),
    ):
        original = getattr(claim, field)
        object.__setattr__(claim, field, wrong)
        try:
            with pytest.raises(ValueError) as caught:
                verdict._check_v20_ingested_r2(claim, policy)
            assert guard in str(caught.value), (field, str(caught.value))
        finally:
            object.__setattr__(claim, field, original)
    object.__setattr__(claim, "mutation", None)
    with pytest.raises(ValueError) as caught:
        verdict._check_v20_ingested_r2(claim, policy)
    assert guard in str(caught.value)
    with pytest.raises(ValueError) as caught:
        verdict._check_v20_ingested_r2(None, policy)
    assert guard in str(caught.value)

    candidates_only = as_report_absent(ingested_document)
    _r2_claim(candidates_only)["mutation"]["candidate_count"] = 3
    _model_refuses(candidates_only, "records total 0 and candidate_count 3")

    # `total` alone: the payload's own total-equals-identities rule refuses a
    # non-zero total on a whole document, so the guard's total clause is hit
    # directly on an otherwise legal payload.
    legal = _model(as_report_absent(ingested_document))
    legal_claim = claim_for(legal.claims, "R2")
    object.__setattr__(legal_claim.mutation, "total", 2)
    with pytest.raises(ValueError) as caught:
        legal._check_v20_ingested_r2(legal_claim, legal.judgment.r2)
    assert "records total 2 and candidate_count 0" in str(caught.value)


def test_report_absent_needs_the_cargo_mutants_producer_in_the_model(
    ingested_document: dict,
):
    wrong_name = as_report_absent(ingested_document)
    wrong_name["judgment"]["r2"]["producer_tool"]["name"] = "StrykerJS"
    _model_refuses(wrong_name, "is true but producer_tool is 'StrykerJS'")
    wrong_schema = as_report_absent(ingested_document)
    wrong_schema["judgment"]["r2"]["producer_tool"]["report_schema_version"] = "1.0"
    _model_refuses(wrong_schema, "is true but producer_tool is 'cargo-mutants' / '1.0'")


def test_report_absent_with_a_nonzero_total_is_refused_by_the_model(
    ingested_document: dict,
):
    """NO_MUTANTS on the label but 109 attempted mutants in the payload."""
    document = _flag_only(ingested_document)
    claim = _r2_claim(document)
    claim["status"] = "INCONCLUSIVE"
    claim["reason_code"] = "NO_MUTANTS"
    document["outcome"] = "INCONCLUSIVE"
    document["reason_code"] = "NO_MUTANTS"
    document["exit_code"] = 5
    _model_refuses(document, "records total 109 and candidate_count 109")


def test_report_absent_with_no_r2_claim_payload_is_refused_by_the_model(
    ingested_document: dict,
):
    """`claim is None` and `mutation is None` are separate arms of the guard;
    a document missing the claim entirely is refused earlier by the model's
    own claim cover, so the payload-free arm is the reachable one here."""
    document = as_report_absent(ingested_document)
    claim = _r2_claim(document)
    claim["status"] = "ERROR"
    claim["reason_code"] = "MUTATION_DISCOVERY_FAILED"
    del claim["mutation"]
    with pytest.raises(ValueError):
        _model(document)


def test_the_unobserved_version_without_report_absent_is_refused_by_the_model(
    ingested_document: dict,
):
    document = as_report_absent(ingested_document)
    del document["judgment"]["r2"]["report_absent"]
    _model_refuses(document, "is legal only with report_absent true")


def test_report_absent_with_a_real_version_is_refused_by_the_model(
    ingested_document: dict,
):
    document = as_report_absent(ingested_document)
    document["judgment"]["r2"]["producer_tool"]["version"] = "27.1.0"
    _model_refuses(document, "assay read no report, so it has no version to copy")


def test_the_schema_pairs_report_absent_and_unobserved_in_both_directions(
    ingested_document: dict, validator
):
    absent = as_report_absent(ingested_document)
    assert why_invalid(validator, absent) == []

    only_version = copy.deepcopy(absent)
    del only_version["judgment"]["r2"]["report_absent"]
    assert why_invalid(validator, only_version), "unobserved without report_absent"

    only_flag = copy.deepcopy(absent)
    only_flag["judgment"]["r2"]["producer_tool"]["version"] = "27.1.0"
    assert why_invalid(validator, only_flag), "report_absent with a real version"

    explicit_false = copy.deepcopy(ingested_document)
    explicit_false["judgment"]["r2"]["report_absent"] = False
    assert why_invalid(validator, explicit_false), "report_absent is const true"


def test_a_native_judgment_without_the_v20_keys_is_unaffected():
    """The v20 rules are ingested-only: a real native verdict constructs, and
    serialises without either key."""
    native = json.loads(
        (VERDICT_FIXTURE_DIR / "r2_pass_with_judgment.json").read_text(encoding="utf-8")
    )
    r2 = _model(native).to_dict()["judgment"]["r2"]
    assert r2["producer"] == "native"
    assert "report_absent" not in r2
    assert "changed_diff" not in r2


def test_the_v20_keys_are_refused_under_native_by_schema_and_model(validator):
    native = json.loads(
        (VERDICT_FIXTURE_DIR / "r2_pass_with_judgment.json").read_text(encoding="utf-8")
    )
    assert native["judgment"]["r2"]["producer"] == "native"
    assert why_invalid(validator, native) == []
    for key, value in (
        ("report_absent", True),
        (
            "changed_diff",
            {
                "base_rev": OID,
                "head_rev": OID,
                "relative_to": ".",
                "sha256": "0" * 64,
                "bytes": 0,
            },
        ),
    ):
        document = copy.deepcopy(native)
        document["judgment"]["r2"][key] = value
        assert why_invalid(validator, document), f"{key} accepted under native"
        _model_refuses(document, f"judgment.r2.{key} is legal only under producer")


# --------------------------------------------------------------------------
# changed_diff, both directions, schema + model
# --------------------------------------------------------------------------


def test_changed_diff_is_required_when_the_placeholder_is_present_in_the_model(
    ingested_document: dict,
):
    document = as_cargo_mutants(ingested_document, changed_diff=False)
    _model_refuses(document, "judgment.r2.changed_diff is required")


def test_changed_diff_is_forbidden_without_the_placeholder_in_the_model(
    ingested_document: dict,
):
    document = as_cargo_mutants(ingested_document)
    document["argv_declared"] = document["argv_declared"][:-1]
    document["argv_effective"] = document["argv_effective"][:-1]
    _model_refuses(document, "judgment.r2.changed_diff is forbidden unless")


def test_changed_diff_is_forbidden_for_a_non_cargo_mutants_producer_in_the_model(
    ingested_document: dict,
):
    document = as_cargo_mutants(ingested_document)
    document["judgment"]["r2"]["producer_tool"]["name"] = "StrykerJS"
    _model_refuses(document, "judgment.r2.changed_diff is forbidden unless")


def test_a_placeholder_alone_is_legal_for_another_producer_in_the_model(
    ingested_document: dict,
):
    document = as_cargo_mutants(ingested_document, changed_diff=False)
    document["judgment"]["r2"]["producer_tool"]["name"] = "StrykerJS"
    _model(document)


def test_base_rev_must_be_the_resolved_base_in_the_model(ingested_document: dict):
    document = as_cargo_mutants(ingested_document)
    document["judgment"]["r2"]["changed_diff"]["base_rev"] = "c" * 40
    _model_refuses(document, "differs from judgment.resolved.base")


def test_head_rev_must_be_the_verdict_commit_in_the_model(ingested_document: dict):
    document = as_cargo_mutants(ingested_document)
    document["judgment"]["r2"]["changed_diff"]["head_rev"] = "d" * 40
    _model_refuses(document, "differs from the verdict commit")


def test_with_no_resolved_base_only_head_rev_is_checked_by_the_model(
    ingested_document: dict,
):
    """`resolved.base is None`: the base_rev comparison is skipped (there is
    nothing to compare it with) while head_rev is still held to the commit."""
    document = as_cargo_mutants(ingested_document)
    resolved = document["judgment"]["resolved"]
    del resolved["base"]
    del resolved["base_resolution"]
    document["judgment"]["r2"]["changed_diff"]["base_rev"] = "c" * 40
    # The whole-model construction refuses a base-less changed-lines judgment
    # for its OWN reason (a changed-line judgment is scoped to a resolved
    # base), so the base_rev comparison is exercised on the check directly.
    _model_refuses(document, "records no base")
    full = as_cargo_mutants(ingested_document)
    verdict = _model(full)
    object.__setattr__(verdict.judgment.resolved, "base", None)
    claim = claim_for(verdict.claims, "R2")
    policy = verdict.judgment.r2
    object.__setattr__(policy.changed_diff, "base_rev", "c" * 40)
    verdict._check_v20_ingested_r2(claim, policy)  # nothing to compare base_rev with
    object.__setattr__(policy.changed_diff, "head_rev", "d" * 40)
    with pytest.raises(ValueError) as caught:
        verdict._check_v20_ingested_r2(claim, policy)
    assert str(caught.value) == (
        f"judgment.r2.changed_diff.head_rev {'d' * 40!r} differs from the verdict "
        f"commit {full['commit']!r}"
    )


def test_changed_diff_is_a_changed_lines_field_in_the_model(
    ingested_document: dict,
):
    """Under `whole_target` assay hands the tool no diff: the placeholder alone
    is legal, and a recorded `changed_diff` is refused."""
    document = as_whole_target(ingested_document)
    _model(document)
    document["judgment"]["r2"]["changed_diff"] = as_cargo_mutants(ingested_document)[
        "judgment"
    ]["r2"]["changed_diff"]
    _model_refuses(
        document, "judgment.r2.changed_diff is forbidden unless"
    )


def test_the_schema_forbids_changed_diff_under_whole_target(
    ingested_document: dict, validator
):
    document = as_whole_target(ingested_document)
    assert why_invalid(validator, document) == []
    document["judgment"]["r2"]["changed_diff"] = as_cargo_mutants(ingested_document)[
        "judgment"
    ]["r2"]["changed_diff"]
    assert why_invalid(validator, document), "changed_diff under whole_target"


def test_the_schema_pins_the_report_absent_producer(
    ingested_document: dict, validator
):
    absent = as_report_absent(ingested_document)
    assert why_invalid(validator, absent) == []
    wrong_name = copy.deepcopy(absent)
    wrong_name["judgment"]["r2"]["producer_tool"]["name"] = "StrykerJS"
    assert why_invalid(validator, wrong_name)
    wrong_schema = copy.deepcopy(absent)
    wrong_schema["judgment"]["r2"]["producer_tool"]["report_schema_version"] = "1.0"
    assert why_invalid(validator, wrong_schema)


def test_the_new_schema_patterns_refuse_a_trailing_newline(
    ingested_document: dict, validator, schema: dict
):
    """The new patterns end in `(?![\\s\\S])`, so Python's `re.search` (what
    `jsonschema` uses) does not let `$` match before a trailing newline, and
    the schema agrees with the model and the raw verifier. The patterns are the
    SAME source strings the vocabulary module compiles."""
    changed = schema["$defs"]["changed_diff"]["properties"]
    assert changed["base_rev"]["pattern"] == COMMIT_OID_RE.pattern
    assert changed["head_rev"]["pattern"] == COMMIT_OID_RE.pattern
    good = as_cargo_mutants(ingested_document)
    assert why_invalid(validator, good) == []
    for field, value in (
        ("base_rev", good["judgment"]["r2"]["changed_diff"]["base_rev"] + "\n"),
        ("head_rev", good["judgment"]["r2"]["changed_diff"]["head_rev"] + "\n"),
        ("sha256", "ab" * 32 + "\n"),
    ):
        document = copy.deepcopy(good)
        document["judgment"]["r2"]["changed_diff"][field] = value
        assert why_invalid(validator, document), f"{field} with a trailing newline"
    operator = copy.deepcopy(good)
    mutant = _r2_claim(operator)["mutation"]["survived"][0]
    mutant["operator"] = "cargo-mutants:FnValue\n"
    assert why_invalid(validator, operator), "operator with a trailing newline"


def test_the_schema_checks_the_changed_diff_shape(ingested_document: dict, validator):
    good = as_cargo_mutants(ingested_document)
    assert why_invalid(validator, good) == []
    mutations = {
        "extra key": lambda diff: diff.update(note="x"),
        "missing key": lambda diff: diff.pop("bytes"),
        "uppercase oid": lambda diff: diff.update(base_rev="A" * 40),
        "short oid": lambda diff: diff.update(head_rev="abc"),
        "bad digest": lambda diff: diff.update(sha256="xyz"),
        "negative bytes": lambda diff: diff.update(bytes=-1),
        "boolean bytes": lambda diff: diff.update(bytes=True),
    }
    for label, mutate in mutations.items():
        document = copy.deepcopy(good)
        mutate(document["judgment"]["r2"]["changed_diff"])
        assert why_invalid(validator, document), f"schema accepted: {label}"


# --------------------------------------------------------------------------
# The ChangedDiff record itself
# --------------------------------------------------------------------------


def _diff(**overrides) -> dict:
    value = {
        "base_rev": OID,
        "head_rev": "b" * 64,
        "relative_to": "crates/core",
        "sha256": "e" * 64,
        "bytes": 0,
    }
    value.update(overrides)
    return value


def test_a_changed_diff_round_trips_and_accepts_a_dot_and_both_oid_widths():
    for relative in (".", "crates/core", "a"):
        diff = ChangedDiff.from_dict(_diff(relative_to=relative))
        assert diff.to_dict() == _diff(relative_to=relative)


@pytest.mark.parametrize(
    ("overrides", "fragment"),
    [
        ({"base_rev": "A" * 40}, "changed_diff.base_rev must be a full lowercase"),
        ({"head_rev": 12}, "changed_diff.head_rev must be a full lowercase"),
        ({"relative_to": "/abs"}, "changed_diff.relative_to"),
        ({"relative_to": "a/../b"}, "changed_diff.relative_to"),
        ({"relative_to": ""}, "changed_diff.relative_to"),
        ({"sha256": "nope"}, "changed_diff.sha256 must be 64 lowercase hex"),
        ({"bytes": -1}, "changed_diff.bytes must be a non-negative integer"),
        ({"bytes": True}, "changed_diff.bytes must be a non-negative integer"),
        ({"bytes": 1.5}, "changed_diff.bytes must be a non-negative integer"),
    ],
)
def test_a_malformed_changed_diff_record_is_refused(overrides: dict, fragment: str):
    with pytest.raises(ValueError) as caught:
        ChangedDiff.from_dict(_diff(**overrides))
    assert fragment in str(caught.value)


@pytest.mark.parametrize("drop", ["base_rev", "bytes"])
def test_a_changed_diff_with_missing_or_extra_fields_is_refused(drop: str):
    missing = _diff()
    del missing[drop]
    with pytest.raises(ValueError, match="unknown or missing fields"):
        ChangedDiff.from_dict(missing)
    with pytest.raises(ValueError, match="unknown or missing fields"):
        ChangedDiff.from_dict({**_diff(), "extra": 1})


def test_judgment_r2_rejects_a_non_bool_report_absent_and_a_wrong_typed_diff(
    ingested_document: dict,
):
    r2 = _model(ingested_document).judgment.r2
    assert isinstance(r2, JudgmentR2)
    import dataclasses

    with pytest.raises(ValueError, match="report_absent must be a bool"):
        dataclasses.replace(r2, report_absent=1)
    with pytest.raises(ValueError, match="changed_diff has the wrong type"):
        dataclasses.replace(r2, changed_diff={"base_rev": OID})


# --------------------------------------------------------------------------
# Design risk R8: an ingested document may carry a crashed mutant
# --------------------------------------------------------------------------


def _crash_one_survivor(document: dict) -> dict:
    """Move a survivor that is not an uncovered position into `crashed`, and
    state the honest terminal: R2 and the rollup ERROR/EXEC_FAILED."""
    document = copy.deepcopy(document)
    claim = _r2_claim(document)
    uncovered = {
        (entry["path"], entry["lineno"])
        for entry in document["judgment"]["r2"]["survived_uncovered"]
    }
    survived = claim["mutation"]["survived"]
    index = next(
        position
        for position, entry in enumerate(survived)
        if (entry["path"], entry["lineno"]) not in uncovered
    )
    claim["mutation"]["crashed"].append(survived.pop(index))
    claim["status"] = "ERROR"
    claim["reason_code"] = "EXEC_FAILED"
    document["outcome"] = "ERROR"
    document["reason_code"] = "EXEC_FAILED"
    document["exit_code"] = 2
    return document


def test_R8_a_valid_ingested_document_with_a_crashed_mutant_verifies(
    ingested_document: dict, validator
):
    document = _crash_one_survivor(ingested_document)
    assert _r2_claim(document)["mutation"]["crashed"]
    assert why_invalid(validator, document) == []
    _model(document)
    assert raw_verify.verify_document(document) == []


def test_R8_a_crashed_mutant_under_a_FAIL_terminal_is_refused(
    ingested_document: dict,
):
    document = _crash_one_survivor(ingested_document)
    _r2_claim(document)["status"] = "FAIL"
    _r2_claim(document)["reason_code"] = "MUTANTS_SURVIVED"
    document["outcome"] = "FAIL"
    document["reason_code"] = "MUTANTS_SURVIVED"
    document["exit_code"] = 1
    # The terminal re-derivation is the raw verifier's job (the model checks
    # structure, not the judgment arithmetic); it re-judges the ingested
    # payload and finds the crashed mutant makes the claim ERROR/EXEC_FAILED.
    failures = raw_verify.verify_document(document)
    assert any(
        "disagrees with the re-derived judgment from mutation buckets "
        "(ERROR, EXEC_FAILED)" in failure
        for failure in failures
    ), failures
