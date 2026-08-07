"""O1/O2 — the independent expected-artifact matrix, audited against a
hand-written vocabulary manifest; and ``assay verify`` (:mod:`assay.verify`)
exercised as a SECONDARY consumer of that same fixture set, never its own
sole oracle (DESIGN-GUIDE §9).

**O1.** ``VOCABULARY`` below is transcribed BY HAND from DESIGN-GUIDE §6's
own outcome/``reason_code`` table — deliberately never
``assay.errors.REASON_CODES`` (``tests/test_errors.py`` already proves THAT
table agrees with the design guide; auditing the fixtures against the same
enum they exist to independently check would only prove the fixtures agree
with the code, never that the code agrees with the design). A-128 narrows
the 19-pair closed vocabulary to the 17 pairs a complete ``Verdict``
artifact can actually represent — ``ERROR``/``GIT_FAILED`` and claim-level
``ERROR``/``FORMAT_MISMATCH`` are structurally unreachable by pre-existing
design (confirmed by reading ``runner.py``/``cli.py`` directly: a genuinely
broken coverage artifact or git failure propagates UNCAUGHT before any
``Verdict`` is ever built) and are excluded here, not fixtured. The one
genuinely missing, genuinely closeable gap — EVIDENCE-level
``ERROR``/``UNREADABLE_ARTIFACT`` (P10's attested-evidence path, A-110) — is
closed by ``tests/fixtures/verdicts/evidence_unreadable_artifact.json``,
this package's one new fixture.

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

#: A-128: structurally unreachable as a COMPLETE Verdict artifact, by
#: pre-existing design — `evaluate_r1`'s own docstring states a genuinely
#: broken coverage artifact or a git failure "propagates uncaught," and
#: `cli.py main()`'s only exception handler exits WITHOUT ever calling
#: `assemble_verdict`/`write_verdict`. No complete artifact can exist to
#: fixture for these two; do not try, and do not "fix" `evaluate_r1` to make
#: them reachable (`runner.py` is forbidden, A-132).
EXCLUDED_ENTIRELY: frozenset[tuple[str, str]] = frozenset(
    {
        ("ERROR", "GIT_FAILED"),
        ("ERROR", "FORMAT_MISMATCH"),
    }
)

#: ERROR/UNREADABLE_ARTIFACT is deliberately NOT in EXCLUDED_ENTIRELY: the
#: pair is excluded only at CLAIM level (same unreachability as the two
#: above); it remains reachable — and required — at EVIDENCE level.
REQUIRED_PAIRS: frozenset[tuple[str, str]] = ALL_PAIRS - EXCLUDED_ENTIRELY


def _pairs_in(document: dict) -> set[tuple[str, str]]:
    """Every (status, reason_code) pair *document* names, at verdict,
    claim, or evidence level."""
    pairs: set[tuple[str, str]] = set()
    if document.get("reason_code") is not None:
        pairs.add((document["outcome"], document["reason_code"]))
    for claim in document.get("claims", []):
        if claim.get("reason_code") is not None:
            pairs.add((claim["status"], claim["reason_code"]))
    for item in document.get("evidence", []):
        if item.get("reason_code") is not None:
            pairs.add((item["status"], item["reason_code"]))
    return pairs


def test_the_transcribed_manifest_matches_the_closed_vocabularys_known_size():
    # Pins the audit's OWN transcription against the count `tests/test_errors.py`
    # independently proves `assay.errors.REASON_CODES` also has — two
    # independent transcriptions of the same design-guide table, expected to
    # agree in SIZE without either importing the other's source of truth.
    assert len(ALL_PAIRS) == 19
    assert len(EXCLUDED_ENTIRELY) == 2
    assert len(REQUIRED_PAIRS) == 17


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


def test_the_audits_own_negative_deleting_a_fixture_leaves_a_pair_uncovered():
    # O1's own negative, demonstrated in-suite: covered-WITHOUT this
    # package's one new fixture must be missing EXACTLY the one pair it
    # exists to close, never more, never a different one.
    covered: dict[tuple[str, str], list[str]] = {}
    for path in FIXTURE_PATHS:
        if path.name == "evidence_unreadable_artifact.json":
            continue
        for pair in _pairs_in(_load_path(path)):
            covered.setdefault(pair, []).append(path.name)
    missing = sorted(REQUIRED_PAIRS - covered.keys())
    assert missing == [("ERROR", "UNREADABLE_ARTIFACT")]


def test_error_unreadable_artifact_is_reachable_only_via_evidence_never_a_claim():
    # A-128: the identical ReasonCode is excluded at CLAIM level (structurally
    # unreachable, same reasoning as GIT_FAILED/FORMAT_MISMATCH) but
    # required — and present — at EVIDENCE level (P10's attested path).
    claim_hits = []
    evidence_hits = []
    for path in FIXTURE_PATHS:
        document = _load_path(path)
        for claim in document.get("claims", []):
            if (claim.get("status"), claim.get("reason_code")) == (
                "ERROR",
                "UNREADABLE_ARTIFACT",
            ):
                claim_hits.append(path.name)
        for item in document.get("evidence", []):
            if (item.get("status"), item.get("reason_code")) == (
                "ERROR",
                "UNREADABLE_ARTIFACT",
            ):
                evidence_hits.append(path.name)
    assert claim_hits == [], (
        f"ERROR/UNREADABLE_ARTIFACT must never appear on a claim (A-128); "
        f"found in {claim_hits}"
    )
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
