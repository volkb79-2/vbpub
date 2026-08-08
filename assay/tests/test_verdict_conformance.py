"""O1/O2 — the independent expected-artifact matrix, audited against a
hand-written vocabulary manifest; and ``assay verify`` (:mod:`assay.verify`)
exercised as a SECONDARY consumer of that same fixture set, never its own
sole oracle (DESIGN-GUIDE §9).

**O1.** ``VOCABULARY`` below is transcribed BY HAND from DESIGN-GUIDE §6's
own outcome/``reason_code`` table — deliberately never
``assay.errors.REASON_CODES`` (``tests/test_errors.py`` already proves THAT
table agrees with the design guide; auditing the fixtures against the same
enum they exist to independently check would only prove the fixtures agree
with the code, never that the code agrees with the design).

**A-128 is CLOSED (P17/A-141): all 19 pairs are now fixtured.** A-128 used
to narrow the closed vocabulary to 17, on the reading that
``ERROR``/``GIT_FAILED`` and claim-level ``ERROR``/``FORMAT_MISMATCH`` and
``ERROR``/``UNREADABLE_ARTIFACT`` were structurally unreachable — a
genuinely broken coverage artifact or git failure propagated UNCAUGHT out
of ``evaluate_r1`` before any ``Verdict`` was built. That was a true
description of the code, never of the design: P17's work item 6 widened
``evaluate_r1`` to render every ``AssayError`` its own guard sequence can
raise as a complete R1 claim, so all three are now ordinary producer
terminals — reproduced against the installed console script, not inferred.
``EXCLUDED_ENTIRELY`` is therefore empty, and it stays a named constant
rather than being deleted so that re-narrowing the audit is a visible,
argued edit rather than a silent one.

The audit is LEVEL-AWARE, because "this pair appears somewhere" is a weaker
claim than either package actually made: ``ERROR``/``UNREADABLE_ARTIFACT``
is reachable at claim level (P17, a coverage artifact the lane's command
never wrote) AND at evidence level (P10's attested-evidence path, A-110),
and each has its own fixture and its own negative below. Auditing only the
flattened pair set would let either fixture silently cover for the other's
deletion.

**O2.** ``assay verify``'s own contract (A-129): parse JSON, reconstruct the
same :mod:`assay.verdict` dataclass graph the packaged schema is maintained
equivalent to (never a hand-rolled JSON-Schema evaluator — ``jsonschema`` is
a *test*-only dependency, A-005), then explicitly re-check the four things
JSON Schema 2020-12 cannot express by comparing two locations in the same
instance. Every ACCEPT and every REJECT below is cross-checked against the
INDEPENDENT ``jsonschema`` validator too (``conftest.validator``/
``why_invalid``) or against a fact computed by hand outside
``assay.verify`` — Test constraint C: ``assay verify`` is never the sole
oracle for its own claim.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
from conftest import PROJECT_ROOT, why_invalid
from jsonschema import Draft202012Validator

from assay.cli import main
from assay.verdict import Outcome, rollup
from assay.verify import cmd_verify, verify_document, verify_text

FIXTURES_DIR = PROJECT_ROOT / "tests" / "fixtures" / "verdicts"
FIXTURE_PATHS = sorted(FIXTURES_DIR.glob("*.json"))
assert FIXTURE_PATHS, f"expected hand-written fixtures under {FIXTURES_DIR}"


def _load(name: str) -> dict:
    return json.loads((FIXTURES_DIR / name).read_text(encoding="utf-8"))


def _load_path(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


# ============================================================================
# O1 — the vocabulary audit (A-128)
# ============================================================================

#: DESIGN-GUIDE §6's reason_code table, transcribed by hand — see the module
#: docstring for why this is never `assay.errors.REASON_CODES`.
VOCABULARY: dict[str, tuple[str, ...]] = {
    "FAIL": (
        "UNCOVERED_LINES",
        "EXCLUDED_LINES",
        "UNCLASSIFIED_LINES",
        "MUTANTS_SURVIVED",
        "CANARY_SURVIVED",
        "COMMAND_FAILED",
    ),
    "ERROR": (
        "GIT_FAILED",
        "UNREADABLE_ARTIFACT",
        "FORMAT_MISMATCH",
        "BAD_LANE_CONFIG",
        "EXEC_FAILED",
    ),
    "NO_MEASUREMENT": (
        "DIRTY_TREE",
        "BASE_IS_HEAD",
        "EMPTY_COVERAGE",
        "MISSING_ATTESTATION",
        "STALE_ATTESTATION",
    ),
    "BUDGET_EXCEEDED": ("LANE_TIMEOUT",),
    "INCONCLUSIVE": ("NO_MUTANTS", "CANARY_INCONCLUSIVE"),
}

ALL_PAIRS: frozenset[tuple[str, str]] = frozenset(
    (outcome, code) for outcome, codes in VOCABULARY.items() for code in codes
)

#: EMPTY since P17 (A-141). Every pair in the closed vocabulary is now
#: reachable as a COMPLETE Verdict artifact and is fixtured below. Kept as
#: a named constant, rather than deleted, so that any future re-narrowing
#: of this audit has to be written down here and argued — a shrinking
#: REQUIRED set is precisely the change that would quietly weaken the whole
#: conformance claim.
EXCLUDED_ENTIRELY: frozenset[tuple[str, str]] = frozenset()

REQUIRED_PAIRS: frozenset[tuple[str, str]] = ALL_PAIRS - EXCLUDED_ENTIRELY

#: The three pairs P17's work item 6 turned from "structurally unreachable"
#: (A-128) into real R1 producer terminals, and the fixture each one is
#: closed by. Asserted BY LEVEL below: a pair being present *somewhere* is
#: not the claim P17 made.
P17_CLAIM_LEVEL_FIXTURES: dict[tuple[str, str], str] = {
    ("ERROR", "GIT_FAILED"): "r1_error_git_failed.json",
    ("ERROR", "FORMAT_MISMATCH"): "r1_error_format_mismatch.json",
    ("ERROR", "UNREADABLE_ARTIFACT"): "r1_error_unreadable_artifact.json",
}


def _claim_pairs_in(document: dict) -> set[tuple[str, str]]:
    """Every (status, reason_code) pair *document* names on a CLAIM."""
    return {
        (claim["status"], claim["reason_code"])
        for claim in document.get("claims", [])
        if claim.get("reason_code") is not None
    }


def _evidence_pairs_in(document: dict) -> set[tuple[str, str]]:
    """Every (status, reason_code) pair *document* names on an EVIDENCE
    entry."""
    return {
        (item["status"], item["reason_code"])
        for item in document.get("evidence", [])
        if item.get("reason_code") is not None
    }


def _pairs_in(document: dict) -> set[tuple[str, str]]:
    """Every (status, reason_code) pair *document* names, at verdict,
    claim, or evidence level."""
    pairs: set[tuple[str, str]] = set()
    if document.get("reason_code") is not None:
        pairs.add((document["outcome"], document["reason_code"]))
    return pairs | _claim_pairs_in(document) | _evidence_pairs_in(document)


def test_the_transcribed_manifest_matches_the_closed_vocabularys_known_size():
    # Pins the audit's OWN transcription against the count `tests/test_errors.py`
    # independently proves `assay.errors.REASON_CODES` also has — two
    # independent transcriptions of the same design-guide table, expected to
    # agree in SIZE without either importing the other's source of truth.
    assert len(ALL_PAIRS) == 19
    assert len(EXCLUDED_ENTIRELY) == 0, "A-141: nothing is unreachable any more"
    assert len(REQUIRED_PAIRS) == 19


def test_every_required_vocabulary_pair_has_a_covering_fixture():
    covered: dict[tuple[str, str], list[str]] = {}
    for path in FIXTURE_PATHS:
        for pair in _pairs_in(_load_path(path)):
            covered.setdefault(pair, []).append(path.name)

    missing = sorted(REQUIRED_PAIRS - covered.keys())
    assert missing == [], f"vocabulary pairs with no covering fixture: {missing}"

    # The inverse direction matters too: a fixture using a pair OUTSIDE the
    # closed 19-pair vocabulary would prove nothing about a real vocabulary.
    unexpected = sorted(set(covered.keys()) - ALL_PAIRS)
    assert unexpected == [], f"fixture(s) use a pair outside the closed vocabulary: {unexpected}"


def test_the_evidence_audits_own_negative_p10s_fixture_is_the_only_cover():
    # P10's own negative, kept level-aware (A-141): at EVIDENCE level,
    # dropping `evidence_unreadable_artifact.json` must leave exactly the
    # one pair it exists to close uncovered. Flattening levels would let
    # P17's new CLAIM-level fixture silently cover for its deletion —
    # which is exactly the kind of accidental cross-cover this audit
    # exists to prevent.
    covered: set[tuple[str, str]] = set()
    for path in FIXTURE_PATHS:
        if path.name == "evidence_unreadable_artifact.json":
            continue
        covered |= _evidence_pairs_in(_load_path(path))
    assert ("ERROR", "UNREADABLE_ARTIFACT") not in covered


def test_the_claim_audits_own_negative_p17s_three_fixtures_are_the_only_cover():
    # P17's own negative (A-141), the mirror of P10's above: dropping the
    # three fixtures work item 6 made reachable must leave EXACTLY those
    # three pairs uncovered at CLAIM level, never more and never a
    # different one. Before P17 this test could not have been written at
    # all — the three pairs had no complete artifact to fixture.
    covered: set[tuple[str, str]] = set()
    for path in FIXTURE_PATHS:
        if path.name in P17_CLAIM_LEVEL_FIXTURES.values():
            continue
        covered |= _claim_pairs_in(_load_path(path))
    still_missing = sorted(set(P17_CLAIM_LEVEL_FIXTURES) - covered)
    assert still_missing == sorted(P17_CLAIM_LEVEL_FIXTURES)


@pytest.mark.parametrize(
    ("pair", "fixture_name"),
    sorted(P17_CLAIM_LEVEL_FIXTURES.items()),
    ids=lambda value: value if isinstance(value, str) else "/".join(value),
)
def test_each_p17_pair_is_covered_by_its_own_named_claim_fixture(
    pair: tuple[str, str], fixture_name: str
):
    # A-141 replaces A-128's "must never appear on a claim" assertion,
    # which had become an active LIE: it forbade the very fixture the
    # shipped product now genuinely emits, so a correct fixture would have
    # FAILED the suite. Each pair is pinned to its own file, so deleting
    # one fixture cannot be masked by another.
    document = _load(fixture_name)
    assert pair in _claim_pairs_in(document)


def test_error_unreadable_artifact_is_now_reachable_at_both_levels():
    # The one ReasonCode with two genuinely different producers: a coverage
    # artifact the lane's own command never wrote (claim level, P17) and a
    # broken attestation file (evidence level, P10, A-110). Both are real;
    # neither substitutes for the other.
    claim_hits = sorted(
        path.name
        for path in FIXTURE_PATHS
        if ("ERROR", "UNREADABLE_ARTIFACT") in _claim_pairs_in(_load_path(path))
    )
    evidence_hits = sorted(
        path.name
        for path in FIXTURE_PATHS
        if ("ERROR", "UNREADABLE_ARTIFACT") in _evidence_pairs_in(_load_path(path))
    )
    assert claim_hits == ["r1_error_unreadable_artifact.json"]
    assert evidence_hits == ["evidence_unreadable_artifact.json"]


@pytest.mark.parametrize("path", FIXTURE_PATHS, ids=lambda p: p.name)
def test_every_fixture_independently_validates_against_the_packaged_schema(
    path: Path, validator: Draft202012Validator
):
    document = _load_path(path)
    assert why_invalid(validator, document) == [], f"{path.name} is not schema-valid"


# ============================================================================
# O2 — `assay verify` accepts every valid independent artifact
# ============================================================================


@pytest.mark.parametrize("path", FIXTURE_PATHS, ids=lambda p: p.name)
def test_verify_accepts_every_independently_schema_valid_fixture(
    path: Path, validator: Draft202012Validator
):
    document = _load_path(path)
    # Test constraint C: cross-checked against the INDEPENDENT jsonschema
    # validator on every single fixture, not only a hand-picked few — an
    # ACCEPT here is never `assay verify`'s word alone.
    assert why_invalid(validator, document) == [], f"{path.name} is not schema-valid"
    failures = verify_document(document)
    assert failures == [], f"{path.name}: {failures}"


def test_verify_text_accepts_the_same_document_as_json_text():
    text = (FIXTURES_DIR / "pass.json").read_text(encoding="utf-8")
    assert verify_text(text) == []


# ============================================================================
# O2 — `assay verify` rejects malformed / unknown-key / wrong-rollup artifacts
# ============================================================================
#
# Every REJECT case below mutates a real, independently schema-valid fixture
# by exactly ONE change, and is checked one of two ways (test constraint C):
# a genuinely SCHEMA-shaped break is cross-verified against the independent
# `jsonschema` validator too (both must reject it); a break in one of the
# four cross-field properties JSON Schema 2020-12 cannot express is
# cross-verified against a fact computed BY HAND in the test itself (never
# routed back through `assay.verify`'s own logic a second time).


def test_verify_rejects_text_that_is_not_json():
    failures = verify_text("{not json")
    assert failures != []
    assert any("JSON" in message for message in failures)


def test_verify_rejects_a_document_that_is_not_a_json_object():
    failures = verify_document([1, 2, 3])
    assert failures == ["artifact must be a JSON object, got list"]


def test_verify_rejects_a_document_missing_a_required_field(
    validator: Draft202012Validator,
):
    document = _load("pass.json")
    del document["lane"]
    assert why_invalid(validator, document) != [], "the mutation must actually break schema validity"
    failures = verify_document(document)
    assert any("missing required field(s)" in message and "lane" in message for message in failures)


def test_verify_accepts_the_unmutated_document_missing_field_case():
    # The paired ACCEPT half of the mutation above: the ORIGINAL, unmutated
    # document must not trip the same failure.
    document = _load("pass.json")
    assert verify_document(document) == []


def test_verify_rejects_an_outcome_outside_the_closed_vocabulary(
    validator: Draft202012Validator,
):
    document = _load("pass.json")
    document["outcome"] = "BOGUS"
    assert why_invalid(validator, document) != []
    failures = verify_document(document)
    assert any("outcome" in message and "BOGUS" in message for message in failures)


def test_verify_rejects_a_reason_code_outside_the_closed_vocabulary(
    validator: Draft202012Validator,
):
    document = _load("r0_fail_command_failed.json")
    document["reason_code"] = "BOGUS_CODE"
    document["claims"][0]["reason_code"] = "BOGUS_CODE"
    assert why_invalid(validator, document) != []
    failures = verify_document(document)
    assert any("reason_code" in message and "BOGUS_CODE" in message for message in failures)


def test_verify_reports_an_outcome_missing_but_a_reason_code_present_and_valid():
    # outcome unresolved but reason_code itself is a perfectly legal code —
    # exercises the branch where `outcome is not None` is False, so the
    # reason_code-valid-for-outcome cross-check is skipped (nothing to check
    # it against), while the reason_code is still independently well-formed.
    document = _load("pass.json")
    del document["outcome"]
    document["reason_code"] = "DIRTY_TREE"
    failures = verify_document(document)
    assert any("missing required field(s)" in message and "outcome" in message for message in failures)
    # No "not a recognised reason code" complaint: DIRTY_TREE IS recognised.
    assert not any("not a recognised reason code" in message for message in failures)


def test_verify_rejects_a_reason_code_that_does_not_belong_to_its_outcome(
    validator: Draft202012Validator,
):
    document = _load("r0_fail_command_failed.json")
    document["reason_code"] = "DIRTY_TREE"  # a real code, but NO_MEASUREMENT's, not FAIL's
    document["claims"][0]["reason_code"] = "DIRTY_TREE"
    assert why_invalid(validator, document) != []
    failures = verify_document(document)
    assert any(
        "reason_code DIRTY_TREE is not valid for outcome FAIL" in message
        for message in failures
    )


def test_verify_rejects_a_mismatched_exit_code(validator: Draft202012Validator):
    document = _load("pass.json")
    document["exit_code"] = 1
    assert why_invalid(validator, document) != []
    failures = verify_document(document)
    assert any("exit_code 1 does not match outcome PASS" in message for message in failures)


def test_verify_rejects_a_partially_present_lane_resolved_group(
    validator: Draft202012Validator,
):
    document = _load("r0_pass.json")
    del document["env_effective"]
    assert why_invalid(validator, document) != [], "dependentRequired must reject this too"
    failures = verify_document(document)
    assert any(
        "all present or all absent" in message and "env_effective" in message
        for message in failures
    )


def test_verify_rejects_argv_effective_that_does_not_equal_declared_plus_appended(
    validator: Draft202012Validator,
):
    document = _load("r0_pass.json")
    document["argv_effective"] = ["totally", "different"]
    assert why_invalid(validator, document) == [], (
        "argv_effective's own cross-field rule is NOT expressible in JSON "
        "Schema 2020-12 -- this mutation must stay schema-VALID so the test "
        "actually exercises O2's own non-schema check, not a redundant one"
    )
    # Hand-computed independent fact: argv_declared + argv_appended, worked
    # out here rather than by calling assay.verify a second time.
    expected = document["argv_declared"] + document["argv_appended"]
    assert expected != document["argv_effective"]
    failures = verify_document(document)
    assert any("argv_effective" in message and "is not argv_declared" in message for message in failures)


def test_verify_rejects_argv_modified_missing_once_a_lane_has_resolved(
    validator: Draft202012Validator,
):
    document = _load("r0_pass.json")
    del document["argv_modified"]
    assert why_invalid(validator, document) != [], "dependentRequired names argv_modified too"
    failures = verify_document(document)
    assert any("argv_modified is required" in message for message in failures)


def test_verify_rejects_argv_modified_that_disagrees_with_argv_appended(
    validator: Draft202012Validator,
):
    document = _load("r0_error_argv_append_rejected.json")
    document["argv_modified"] = False
    assert why_invalid(validator, document) != [], (
        "the schema's own allOf rule ties argv_modified to argv_appended's "
        "emptiness"
    )
    assert bool(document["argv_appended"]) != document["argv_modified"]
    failures = verify_document(document)
    assert any("argv_modified False does not match" in message for message in failures)


def test_verify_rejects_argv_modified_present_without_a_resolved_lane():
    document = _load("error.json")
    document["argv_modified"] = False
    failures = verify_document(document)
    assert any("argv_modified present without a resolved lane" in message for message in failures)


def test_verify_rejects_claims_missing_a_declared_rigor_level(
    validator: Draft202012Validator,
):
    document = _load("pass.json")
    document["claims"] = [document["claims"][0]]  # drop the R1 claim; still declared
    assert why_invalid(validator, document) == [], (
        "claims-cover-declared-rigor is NOT expressible in JSON Schema "
        "2020-12 either -- this stays schema-valid on purpose"
    )
    failures = verify_document(document)
    assert any("has no claim for" in message and "R1" in message for message in failures)


def test_verify_rejects_claims_for_an_undeclared_rigor_level(
    validator: Draft202012Validator,
):
    document = _load("r0_pass.json")
    extra_claim = {
        "rigor": "R1",
        "source": "computed",
        "status": "PASS",
        "verified_by_assay": True,
    }
    document["claims"] = [*document["claims"], extra_claim]
    assert why_invalid(validator, document) == [], (
        "claims-cover-declared-rigor is NOT expressible in JSON Schema "
        "2020-12 either -- each claim is independently schema-valid, so "
        "this stays schema-valid on purpose"
    )
    failures = verify_document(document)
    assert any("undeclared rigor level" in message and "R1" in message for message in failures)


def test_verify_rejects_evidence_missing_a_declared_identity(
    validator: Draft202012Validator,
):
    document = _load("evidence_current.json")
    document["evidence"] = []
    assert why_invalid(validator, document) == [], (
        "evidence-covers-declared-evidence is NOT expressible in JSON "
        "Schema 2020-12 either"
    )
    failures = verify_document(document)
    assert any("rendered no judgement" in message for message in failures)


def test_verify_rejects_evidence_for_an_undeclared_identity():
    document = _load("r0_pass.json")
    document["declared_evidence"] = []
    document["evidence"] = [
        {
            "source": "attested",
            "key": "surplus",
            "status": "PASS",
            "verified_by_assay": False,
            "producer": "bot",
            "attested_commit": "9" * 40,
            "reviewed_paths": ["x.py"],
        }
    ]
    failures = verify_document(document)
    assert any("was never declared" in message for message in failures)


def test_verify_rejects_an_outcome_that_disagrees_with_the_rollup_of_its_claims():
    document = _load("pass.json")
    document["outcome"] = "FAIL"
    document["reason_code"] = "COMMAND_FAILED"
    document["exit_code"] = 1
    # Hand-computed independent fact: rollup() re-derived directly from the
    # claim statuses, exactly as A-129 names ("import and call
    # verdict.rollup()") -- computed HERE, not by calling verify_document
    # and trusting its own internal arithmetic.
    statuses = [Outcome(claim["status"]) for claim in document["claims"]]
    implied = rollup(statuses)
    assert implied is Outcome.PASS
    assert implied.value != document["outcome"]

    failures = verify_document(document)
    assert any(
        "outcome FAIL disagrees with the rollup" in message and "PASS" in message
        for message in failures
    )


def test_verify_rejects_an_unknown_top_level_field(validator: Draft202012Validator):
    document = _load("r0_pass.json")
    document["bogus_field"] = "unexpected"
    assert why_invalid(validator, document) != [], "unevaluatedProperties: false must reject this"
    failures = verify_document(document)
    assert any("unknown top-level field(s)" in message and "bogus_field" in message for message in failures)


def test_verify_rejects_an_unknown_claim_level_field(validator: Draft202012Validator):
    document = _load("r0_pass.json")
    document["claims"][0]["bogus"] = 1
    assert why_invalid(validator, document) != []
    failures = verify_document(document)
    assert any("unknown claim field(s)" in message and "bogus" in message for message in failures)


def test_verify_rejects_an_unknown_evidence_level_field(validator: Draft202012Validator):
    document = _load("evidence_current.json")
    document["evidence"][0]["bogus"] = 1
    assert why_invalid(validator, document) != []
    failures = verify_document(document)
    assert any("unknown evidence field(s)" in message and "bogus" in message for message in failures)


def test_verify_rejects_an_unknown_coverage_level_field(validator: Draft202012Validator):
    document = _load("pass.json")
    document["claims"][1]["coverage"]["bogus"] = 1
    assert why_invalid(validator, document) != []
    failures = verify_document(document)
    assert any("unknown coverage field(s)" in message and "bogus" in message for message in failures)


def test_verify_rejects_an_unknown_canary_level_field(validator: Draft202012Validator):
    document = _load("r3_pass.json")
    document["claims"][1]["canary"]["bogus"] = 1
    assert why_invalid(validator, document) != []
    failures = verify_document(document)
    assert any("unknown canary field(s)" in message and "bogus" in message for message in failures)


def test_verify_rejects_an_unknown_mutation_level_field(validator: Draft202012Validator):
    document = _load("r2_fail_mutants_survived.json")
    document["claims"][1]["mutation"]["bogus"] = 1
    assert why_invalid(validator, document) != []
    failures = verify_document(document)
    assert any("unknown mutation field(s)" in message and "bogus" in message for message in failures)


def test_verify_rejects_an_unknown_mutant_outcome_level_field(validator: Draft202012Validator):
    document = _load("r2_fail_mutants_survived.json")
    document["claims"][1]["mutation"]["survived"][0]["bogus"] = 1
    assert why_invalid(validator, document) != []
    failures = verify_document(document)
    assert any("unknown mutant outcome field(s)" in message and "bogus" in message for message in failures)


def test_verify_skips_the_rollup_check_when_a_claim_entry_is_not_an_object():
    # `_check_outcome_agrees_with_rollup` bails out rather than crashing when
    # a `claims[]` entry is not itself an object -- reconstruction (a
    # DIFFERENT stage) is what reports this shape, not the rollup check.
    document = _load("r0_pass.json")
    document["claims"] = [42]
    failures = verify_document(document)
    assert not any("disagrees with the rollup" in message for message in failures)
    assert any(failure.startswith("schema:") for failure in failures)


def test_verify_skips_the_rollup_check_when_a_status_is_not_a_recognised_outcome():
    document = _load("r0_pass.json")
    document["claims"][0]["status"] = "BOGUS"
    failures = verify_document(document)
    assert not any("disagrees with the rollup" in message for message in failures)
    assert any(failure.startswith("schema:") for failure in failures)


def test_verify_reports_a_structural_type_error_it_does_not_specifically_name():
    # Reconstruction is the catch-all: a shape none of the explicit checks
    # above specifically name (claims not even a list) still produces a
    # reported failure rather than an uncaught exception escaping verify_document.
    document = _load("r0_pass.json")
    document["claims"] = "not-a-list"
    failures = verify_document(document)
    assert any(failure.startswith("schema:") for failure in failures)


# ============================================================================
# O2 — the vacuity guard: a verifier that always accepts proves nothing
# ============================================================================


def test_a_verifier_that_always_returns_success_would_wrongly_accept_a_broken_artifact():
    # Documents, in-suite, exactly the failure O2's negative names: this is
    # what an always-accepting `verify_document` would do wrong, contrasted
    # with the real one immediately after.
    document = _load("pass.json")
    document["outcome"] = "FAIL"  # now internally inconsistent

    def always_accepts(_doc: dict) -> list[str]:
        return []

    assert always_accepts(document) == [], "the strawman accepts everything, by construction"
    assert verify_document(document) != [], "the real verifier must not"


# ============================================================================
# O2 — `assay verify` wired into `assay.cli.main`
# ============================================================================


def _run(argv: list[str], stdin_text: str = "") -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    inp = io.StringIO(stdin_text)
    code = main(argv, stdin=inp, stdout=out, stderr=err)
    return code, out.getvalue(), err.getvalue()


def test_cli_verify_accepts_a_valid_file_and_exits_zero(tmp_path: Path):
    path = tmp_path / "verdict.json"
    path.write_text((FIXTURES_DIR / "pass.json").read_text(encoding="utf-8"), encoding="utf-8")

    code, out, err = _run(["verify", str(path)])

    assert code == 0
    assert err == ""


def test_cli_verify_rejects_an_invalid_file_and_exits_nonzero(tmp_path: Path):
    document = _load("pass.json")
    document["outcome"] = "BOGUS"
    path = tmp_path / "verdict.json"
    path.write_text(json.dumps(document), encoding="utf-8")

    code, out, err = _run(["verify", str(path)])

    assert code == 1
    assert "assay verify:" in err
    assert "BOGUS" in err


def test_cli_verify_reads_from_stdin_when_path_is_a_dash():
    text = (FIXTURES_DIR / "pass.json").read_text(encoding="utf-8")

    code, out, err = _run(["verify", "-"], stdin_text=text)

    assert code == 0
    assert err == ""


def test_cli_verify_reports_a_missing_file(tmp_path: Path):
    missing = tmp_path / "does-not-exist.json"

    code, out, err = _run(["verify", str(missing)])

    assert code == 1
    assert "cannot read" in err


def test_cmd_verify_matches_the_cli_wiring_directly(tmp_path: Path):
    # Exercises assay.verify.cmd_verify directly (not only through cli.main),
    # matching the same "prove the unit, then prove the wiring" split the
    # rest of this project already uses for run/lanes.
    path = tmp_path / "verdict.json"
    path.write_text((FIXTURES_DIR / "pass.json").read_text(encoding="utf-8"), encoding="utf-8")
    err = io.StringIO()

    code = cmd_verify(str(path), stdin=io.StringIO(""), stderr=err)

    assert code == 0
    assert err.getvalue() == ""


# ============================================================================
# O2 (P16, stage 3) -- independent re-derivation of R1/R2/R3 status from
# payload plus recorded policy, and the judgment.r1 <-> R1-coverage raw
# cross-check. Every REJECT below is sol finding 2's own reproduction,
# reproduced A-135's binding way: a VALID Coverage/Mutation/CanaryResult
# payload (nothing internally inconsistent) carrying a WRONG claim status
# -- never a self-contradictory payload, which P15's own construction-time
# invariants make impossible to build in the first place.
# ============================================================================


def test_verify_rejects_judgment_r1_present_without_an_r1_coverage_claim():
    document = _load("r0_pass.json")
    document["judgment"] = {
        "r1": {
            "language": "python",
            "source_roots": ["src"],
            "coverage_format": "coverage-py-json",
            "coverage_artifact": "cov.json",
            "fail_under": 100.0,
            "allow_excluded": False,
            "base": "a" * 40,
        }
    }
    failures = verify_document(document)
    assert any(
        "judgment.r1 is declared without a corresponding R1 coverage claim" in f
        for f in failures
    )


def test_verify_rejects_an_r1_coverage_claim_without_judgment():
    document = _load("r1_fail_uncovered_lines.json")
    del document["judgment"]

    failures = verify_document(document)
    assert any(
        "an R1 coverage claim is declared without a corresponding judgment.r1" in f
        for f in failures
    )


def test_verify_accepts_reconstructed_judgment_r2_and_r3():
    # `judgment.r3` is still RESERVED -- no producer populates it until P19
    # -- but reconstruction must accept it when present, since that package
    # populates it additively without another schema bump. `judgment.r2` is
    # NO LONGER reserved (P18 populates it on every R2 run that rendered a
    # mutation payload); it keeps its synthesised half here anyway, because
    # a reconstruction that only ever saw the ONE shape assay's own producer
    # emits would not be an independent check of the closed shape.
    document = _load("r2_pass.json")
    document["judgment"] = {"r2": {"jobs": 4, "operators": ["compare-swap", "boolop-swap"]}}
    assert verify_document(document) == []

    document2 = _load("r3_pass.json")
    document2["judgment"] = {"r3": {"mechanism": "uncovered-line", "target": "pkg/mod.py"}}
    assert verify_document(document2) == []


#: A-141, applied to what P18 made real: `judgment.r2` acquired a genuine
#: producer, so the hand-written matrix has to carry the shape that producer
#: actually emits -- not merely a synthesised one injected into an R1-era
#: fixture. `r2_pass_with_judgment.json` is that shape, transcribed by hand
#: from what a real `assay run` of an R0+R2 lane emits (see
#: `test_standalone.py`'s own installed-wheel R2 comparisons), never
#: generated from it.
JUDGMENT_R2_FIXTURE = "r2_pass_with_judgment.json"


def test_the_matrix_carries_the_r2_judgment_shape_its_producer_now_emits():
    document = _load(JUDGMENT_R2_FIXTURE)
    r2_claim = next(claim for claim in document["claims"] if claim["rigor"] == "R2")

    assert document["judgment"] == {
        "r2": {"jobs": 2, "operators": ["compare-swap", "bool-const-flip"]}
    }
    assert r2_claim["mutation"]["total"] == 2

    # The one correspondence a consumer can check TODAY, stated here as the
    # audit's own claim rather than as a rule `assay verify` enforces (it
    # does not yet -- A-148): every operator naming a recorded mutant is one
    # the declared policy actually selected. A payload naming an operator
    # `judgment.r2.operators` never declared is a contradiction no schema
    # can see.
    recorded = {
        entry["operator"]
        for bucket in ("survived", "crashed", "budget_exceeded")
        for entry in r2_claim["mutation"][bucket]
    }
    assert recorded <= set(document["judgment"]["r2"]["operators"])


def test_the_r2_judgment_fixture_is_the_only_one_carrying_it():
    # The level-aware negative, the same shape P10's and P17's already have:
    # dropping this one file must leave `judgment.r2` uncovered ENTIRELY, so
    # a later deletion cannot be masked by some other fixture that happens
    # to grow the field.
    covered = [
        path.name
        for path in FIXTURE_PATHS
        if "r2" in (_load_path(path).get("judgment") or {})
    ]
    assert covered == [JUDGMENT_R2_FIXTURE]


def test_verify_rejects_an_r1_pass_reporting_zero_percent_coverage():
    """Sol finding 2, first reproduction, verbatim: 'An R1 claim's
    coverage.pct set to 0.0 while status stayed PASS -- accepted.'"""
    document = _load("r1_fail_uncovered_lines_span_attributed.json")
    assert document["claims"][1]["coverage"]["pct"] == 0.0  # the fixture really is 0%
    document["claims"][1]["status"] = "PASS"
    del document["claims"][1]["reason_code"]
    document["outcome"] = "PASS"
    del document["reason_code"]
    document["exit_code"] = 0

    failures = verify_document(document)
    assert any(
        "disagrees with the re-derived judgment from coverage plus policy" in f
        for f in failures
    )


def test_verify_rejects_an_r1_pass_hiding_disallowed_excluded_lines():
    """The 'hidden excluded lines' negative work item 6 names, verbatim."""
    document = _load("r1_fail_excluded_lines.json")
    assert document["claims"][1]["coverage"]["excluded_lines"]  # really non-empty
    document["claims"][1]["status"] = "PASS"
    del document["claims"][1]["reason_code"]
    document["outcome"] = "PASS"
    del document["reason_code"]
    document["exit_code"] = 0

    failures = verify_document(document)
    assert any(
        "disagrees with the re-derived judgment from coverage plus policy" in f
        for f in failures
    )


def test_verify_rejects_an_r2_pass_with_a_genuine_surviving_mutant():
    """Sol finding 2, second reproduction, verbatim: 'An R2 claim with a
    genuine surviving mutant added while status stayed PASS -- accepted.'"""
    document = _load("r2_fail_mutants_survived.json")
    assert document["claims"][1]["mutation"]["survived"]  # really non-empty
    document["claims"][1]["status"] = "PASS"
    del document["claims"][1]["reason_code"]
    document["outcome"] = "PASS"
    del document["reason_code"]
    document["exit_code"] = 0

    failures = verify_document(document)
    assert any(
        "disagrees with the re-derived judgment from mutation buckets" in f
        for f in failures
    )


def test_verify_rejects_an_r3_pass_whose_transform_never_actually_failed():
    """Sol finding 2, third reproduction, verbatim: 'An R3 claim with the
    transformed canary outcome set to PASS (i.e. the canary never actually
    failed) while status stayed PASS -- accepted.'"""
    document = _load("r3_fail_canary_survived_unexpected_pass.json")
    assert document["claims"][1]["canary"]["transformed_outcome"] == "PASS"
    document["claims"][1]["status"] = "PASS"
    del document["claims"][1]["reason_code"]
    document["outcome"] = "PASS"
    del document["reason_code"]
    document["exit_code"] = 0

    failures = verify_document(document)
    assert any(
        "disagrees with the re-derived judgment from the canary result" in f
        for f in failures
    )


def test_verify_skips_r2_rederivation_when_a_payload_less_claim_has_no_r0_sibling():
    """An R2-only lane (no R0 declared) still validates: a claim carrying NO
    mutation payload reused a baseline the artifact does not record, so
    there is no honest comparison to make. This is the ONLY case that
    skips — a claim carrying a real payload is re-judged with or without an
    R0 sibling (see the test below), and the one status that would be a
    contradiction regardless is unconstructible
    (``Claim._check_a_judged_status_carries_its_own_payload``)."""
    document = {
        "schema_version": 3,
        "assay_version": "0.1.0",
        "lane": "package",
        "commit": "a" * 40,
        "outcome": "ERROR",
        "reason_code": "EXEC_FAILED",
        "exit_code": 2,
        "started": "2026-08-07T09:00:00+00:00",
        "ended": "2026-08-07T09:00:01+00:00",
        "declared_rigor": ["R2"],
        "declared_evidence": [],
        "argv_declared": ["pytest", "-q"],
        "argv_appended": [],
        "argv_effective": ["pytest", "-q"],
        "argv_modified": False,
        "env_declared": {},
        "env_effective": {},
        "scope": "S1",
        "enforcement": "gate",
        "claims": [
            {
                "rigor": "R2",
                "source": "computed",
                "status": "ERROR",
                "verified_by_assay": True,
                "reason_code": "EXEC_FAILED",
            }
        ],
        "evidence": [],
    }
    assert verify_document(document) == []


def test_verify_rejects_an_r2_pass_with_a_survivor_even_with_no_r0_claim(
    validator: Draft202012Validator,
):
    """The same forgery as
    ``test_verify_rejects_an_r2_pass_with_a_genuine_surviving_mutant``,
    evading re-derivation by not declaring R0 — ``rigor = ["R2"]`` is a
    legal lane declaration (``assay.config`` requires only a non-empty
    subset of R0-R3), and ``judge_mutation`` never reads its ``baseline``
    argument once a real mutation payload is present, so there is nothing
    an absent R0 claim could excuse skipping."""
    document = _load("r2_fail_mutants_survived.json")
    assert document["claims"][1]["mutation"]["survived"]  # really non-empty
    document["claims"] = [c for c in document["claims"] if c["rigor"] != "R0"]
    document["declared_rigor"] = ["R2"]
    document["claims"][0]["status"] = "PASS"
    del document["claims"][0]["reason_code"]
    document["outcome"] = "PASS"
    del document["reason_code"]
    document["exit_code"] = 0

    # Independently schema-valid: nothing but the re-derivation can see it.
    assert why_invalid(validator, document) == []

    failures = verify_document(document)
    assert any(
        "disagrees with the re-derived judgment from mutation buckets" in f
        for f in failures
    )


# ============================================================================
# O2 (P16 review) -- a status with NO payload behind it, and a foreign
# schema version. Both are evasions by OMISSION rather than contradiction:
# the first leaves the re-derivation nothing to judge, the second hands the
# verifier a shape it was never written against.
# ============================================================================


@pytest.mark.parametrize(
    "fixture,payload,match",
    [
        ("r1_pass.json", "coverage", "without a coverage payload"),
        ("r2_pass.json", "mutation", "PASS without a mutation payload"),
        ("r3_pass.json", "canary", "without a canary payload"),
    ],
)
def test_verify_rejects_a_pass_claim_whose_payload_was_simply_deleted(
    fixture: str, payload: str, match: str
):
    """Deleting the evidence is cheaper than contradicting it, and leaves
    the top-level rollup in perfect agreement. Every one of these was
    ACCEPTED before this rule existed."""
    document = _load(fixture)
    claim = next(c for c in document["claims"] if payload in c)
    del claim[payload]
    if payload == "coverage":
        del document["judgment"]

    failures = verify_document(document)
    assert any(match in f for f in failures), failures


def test_verify_rejects_a_foreign_schema_version_as_a_version_problem():
    """P16 work item 7. A REAL v2 artifact — this repository's own r1_pass
    fixture as it stood at the P15 merge, transcribed by hand rather than
    down-converted by any code in this package — is rejected with ONE
    diagnostic naming the version, not with a pile of KeyErrors on fields
    its producer had never heard of."""
    document = _load("r1_pass.json")
    document["schema_version"] = 2
    del document["judgment"]
    del document["scope"]
    del document["enforcement"]
    for claim in document["claims"]:
        claim.pop("coverage", {}).pop("excluded_lines", None)

    failures = verify_document(document)
    assert failures == [
        "schema_version 2 is not this verifier's version 3: a verdict "
        "artifact is rejected, never upgraded in place -- re-produce it "
        "with an assay whose VERDICT_SCHEMA_VERSION is 3"
    ]


# ============================================================================
# O2 (P16 review) -- the rest of work item 6's named contradictory negatives:
# survivor/crash/budget PRECEDENCE (not merely the survivor mapping), broken
# mutation prerequisite propagation, and the two canary contradictions
# besides survival. Every one is a VALID payload carrying a WRONG status
# (A-135), and every one is independently schema-valid -- only the stage-3
# re-derivation can see it.
# ============================================================================


def _a_survivor() -> dict:
    return {
        "path": "pkg/a.py",
        "lineno": 3,
        "operator": "compare-swap",
        "description": "a < b -> a >= b",
    }


@pytest.mark.parametrize(
    "fixture,bucket,status,reason_code,exit_code,expected",
    [
        # crashed outranks survived: a crash means the mutant never got a
        # verdict at all, which is not the same news as "it lived".
        (
            "r2_error_exec_failed_mutant_crashed.json", "survived",
            "FAIL", "MUTANTS_SURVIVED", 1, "(ERROR, EXEC_FAILED)",
        ),
        # budget_exceeded outranks survived, for the same reason.
        (
            "r2_budget_exceeded_lane_timeout.json", "survived",
            "FAIL", "MUTANTS_SURVIVED", 1, "(BUDGET_EXCEEDED, LANE_TIMEOUT)",
        ),
        # and crashed outranks budget_exceeded -- ROLLUP_PRECEDENCE applied
        # one level down (A-117).
        (
            "r2_error_exec_failed_mutant_crashed.json", "budget_exceeded",
            "BUDGET_EXCEEDED", "LANE_TIMEOUT", 4, "(ERROR, EXEC_FAILED)",
        ),
    ],
)
def test_verify_rejects_an_r2_status_that_ignores_bucket_precedence(
    validator: Draft202012Validator,
    fixture: str, bucket: str, status: str, reason_code: str,
    exit_code: int, expected: str,
):
    document = _load(fixture)
    claim = next(c for c in document["claims"] if c["rigor"] == "R2")
    claim["mutation"][bucket] = [_a_survivor()]
    claim["mutation"]["total"] += 1
    claim["status"] = status
    claim["reason_code"] = reason_code
    # The R0 sibling passes in both fixtures, so the rollup is the R2 status
    # itself -- computed here by hand rather than read off the artifact.
    assert [c["status"] for c in document["claims"] if c["rigor"] == "R0"] == ["PASS"]
    document["outcome"] = status
    document["reason_code"] = reason_code
    document["exit_code"] = exit_code

    assert why_invalid(validator, document) == []
    failures = verify_document(document)
    assert any(
        f"disagrees with the re-derived judgment from mutation buckets {expected}" in f
        for f in failures
    ), failures


def test_verify_rejects_an_r2_claim_that_misreports_its_own_failed_prerequisite(
    validator: Draft202012Validator,
):
    """Work item 6's "broken mutation prerequisite propagation": with no
    mutation payload the R2 claim must reuse the baseline's own
    ``(outcome, reason_code)`` VERBATIM (A-116), not merely land on some
    other adverse status."""
    document = _load("r2_error_exec_failed_baseline_crashed.json")
    claim = next(c for c in document["claims"] if c["rigor"] == "R2")
    assert "mutation" not in claim  # the prerequisite never passed
    claim["status"] = "FAIL"
    claim["reason_code"] = "COMMAND_FAILED"

    assert why_invalid(validator, document) == []
    failures = verify_document(document)
    assert any(
        "disagrees with the re-derived judgment from mutation buckets "
        "(ERROR, EXEC_FAILED)" in f
        for f in failures
    ), failures


def test_verify_rejects_an_r3_pass_whose_canary_failed_for_the_wrong_cause(
    validator: Draft202012Validator,
):
    """A canary that fails for some OTHER reason proves nothing about the
    defect it was built to catch -- FAIL/CANARY_SURVIVED, never PASS."""
    document = _load("r3_fail_canary_survived_wrong_reason.json")
    canary = document["claims"][1]["canary"]
    assert canary["observed_reason_code"] != canary["expected_reason_code"]
    document["claims"][1]["status"] = "PASS"
    del document["claims"][1]["reason_code"]
    document["outcome"] = "PASS"
    del document["reason_code"]
    document["exit_code"] = 0

    assert why_invalid(validator, document) == []
    failures = verify_document(document)
    assert any(
        "disagrees with the re-derived judgment from the canary result "
        "(FAIL, CANARY_SURVIVED)" in f
        for f in failures
    ), failures


def test_verify_rejects_an_r3_pass_built_on_a_broken_control(
    validator: Draft202012Validator,
):
    """A control that does not itself pass makes the transformed run
    meaningless -- INCONCLUSIVE/CANARY_INCONCLUSIVE, never PASS. Nothing
    else in the artifact changes: the control outcome is the only edit."""
    document = _load("r3_pass.json")
    document["claims"][1]["canary"]["control_outcome"] = "FAIL"

    assert why_invalid(validator, document) == []
    failures = verify_document(document)
    assert any(
        "disagrees with the re-derived judgment from the canary result "
        "(INCONCLUSIVE, CANARY_INCONCLUSIVE)" in f
        for f in failures
    ), failures


def test_verify_still_reports_a_missing_schema_version_as_a_missing_field():
    """The version diagnostic answers "which version is this"; it must not
    swallow the different question "is the field there at all"."""
    document = _load("r0_pass.json")
    del document["schema_version"]

    failures = verify_document(document)
    assert any("missing required field(s)" in f for f in failures)
    assert not any("is not this verifier's version" in f for f in failures)
