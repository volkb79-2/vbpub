"""W4's v8 acceptance suite: B035's hard cut, plus B018's judge provenance.

The six templates are the frozen W2 v7 documents migrated by bumping
`schema_version` to 8 and adding `judgment.r2.mode` to the two that carry an
r2 -- both `changed_lines`, which is what those documents always were (each
records a `judgment.resolved.base`, and a whole-target document cannot). They
are committed, not transformed at runtime, so this suite tests real bytes.

It carries forward W1's and W2's differential discipline unchanged: every
negative has an unmodified clean control that must verify clean in the SAME
test, so no negative here can pass merely because the whole document became
foreign.

What is NEW at v8, and what this suite exists to pin:

* the `judgment.resolved.base` rule is enforceable for an `R0,R2` lane -- the
  shape A-325 had to exempt entirely, and the shape every SQL lane has;
* `judgment.r2.targets` makes a declared whole-target file that produced zero
  mutation sites distinguishable from one never considered;
* two tiers of one judgment cannot record two different lane scopes;
* an optional top-level `judge_provenance` names the build that produced the
  document, and is refused rather than truncated when malformed.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
W1_EXPECTED = ROOT / "nyxloom-trove" / "carve-assets" / "W1" / "expected"
W2_EXPECTED = ROOT / "nyxloom-trove" / "carve-assets" / "W2" / "expected"
P26_EXPECTED = ROOT / "nyxloom-trove" / "carve-assets" / "P26" / "expected"

V8_TEMPLATES = [
    "missing-tool-v8-template.json",
    "sql-r2-v8-template.json",
    "ca1-r3-no-base-v8-template.json",
    "ca4-all-equivalent-v8-template.json",
]

P25_V8_TEMPLATES = ["p25-pass-v8-template.json", "p25-missing-v8-template.json"]

SUBS = {
    "@STARTED@": "2026-08-11T00:00:00+00:00",
    "@ENDED@": "2026-08-11T00:00:01+00:00",
}

#: A real digest shape, not a token: `judge_provenance.digest` is pattern-bound
#: to 64 lowercase hex, so a placeholder cannot stand in for one here.
A_REAL_DIGEST = "9f" * 32


def load(path: Path) -> dict:
    text = path.read_text()
    for key, value in SUBS.items():
        text = text.replace(key, value)
    return json.loads(text)


def refuses_only_the_defect(verify_document, clean: dict, broken: dict, why: str):
    assert verify_document(clean) == [], (
        f"{why}: control document must verify clean under v8"
    )
    assert verify_document(broken), why


@pytest.fixture(scope="module")
def verify_document():
    from assay.verify import verify_document as fn

    return fn


# --- identity and hard-cut guards -------------------------------------------


def test_schema_identity_is_internally_consistent_under_v8():
    from assay import verdict as V

    schema = load(ROOT / "src" / "assay" / "schemas" / "verdict.schema.json")
    assert schema["$id"] == "urn:assay:schema:verdict:8"
    assert schema["properties"]["schema_version"]["const"] == 8
    assert V.VERDICT_SCHEMA_VERSION == 8


def test_shipped_schema_is_byte_identical_to_the_locked_v8_asset():
    """The guard this project has been bitten by TWICE. It is carried forward
    into every generation deliberately: whatever moves in the shipped schema
    must move in the frozen copy in the same commit, or this fails."""
    shipped = (ROOT / "src" / "assay" / "schemas" / "verdict.schema.json").read_bytes()
    locked = (HERE / "verdict.schema.v8.json").read_bytes()
    assert shipped == locked


@pytest.mark.parametrize(
    "frozen",
    [
        pytest.param(path, id=f"{path.parent.parent.name}/{path.name}")
        for path in sorted(W1_EXPECTED.glob("*.json")) + sorted(W2_EXPECTED.glob("*.json"))
    ],
)
def test_every_earlier_frozen_template_is_rejected_under_v8(verify_document, frozen):
    """A-170's hard cut, over BOTH earlier generations at once: v8 rejects v6
    and v7 alike, with exactly one diagnostic and no downstream noise."""
    failures = verify_document(load(frozen))
    assert len(failures) == 1
    assert "is not this verifier's version 8" in failures[0], failures


# --- migrated v7 controls ----------------------------------------------------


@pytest.mark.parametrize("name", V8_TEMPLATES)
def test_locked_v8_template_is_accepted(verify_document, name):
    assert verify_document(load(HERE / "expected" / name)) == []


@pytest.mark.parametrize("name", P25_V8_TEMPLATES)
def test_p25_v8_siblings_validate(verify_document, name):
    assert verify_document(load(HERE / "expected" / name)) == []


@pytest.mark.parametrize(
    "name",
    [
        "current-v4-template.json",
        "stale-directory-v4-template.json",
        "independent-errors-v4-template.json",
        "attestation-timeout-v4-template.json",
    ],
)
def test_p26_attestation_shapes_survive_v8(verify_document, name):
    doc = load(P26_EXPECTED / name)
    doc["schema_version"] = 8
    assert verify_document(doc) == []


# --- B035: judgment.r2 can now witness its own judging scope ----------------


def test_an_r0_r2_document_must_declare_the_scope_it_judged_under(verify_document):
    """The whole of B035 in one assertion: `mode` is REQUIRED on
    `judgment.r2`, so no v8 document can be silent about the thing its base
    rule turns on."""
    clean = load(HERE / "expected" / "sql-r2-v8-template.json")
    broken = copy.deepcopy(clean)
    del broken["judgment"]["r2"]["mode"]
    refuses_only_the_defect(
        verify_document, clean, broken, "judgment.r2 without mode must be refused"
    )


def test_a_diff_based_r0_r2_document_that_omits_its_base_is_refused(verify_document):
    """**The regression B035 was filed for.** 2.4.1 refused this document;
    2.4.2 accepted it, because A-325 had to stop enforcing the rule for a
    shape the artifact could not witness. v8 refuses it again, and this time
    on evidence the document itself carries."""
    clean = load(HERE / "expected" / "sql-r2-v8-template.json")
    assert clean["judgment"]["r2"]["mode"] == "changed_lines"
    broken = copy.deepcopy(clean)
    del broken["judgment"]["resolved"]["base"]
    broken["judgment"]["resolved"].pop("base_resolution", None)
    refuses_only_the_defect(
        verify_document,
        clean,
        broken,
        "a changed-line R0,R2 document without a base must be refused",
    )


def test_a_whole_target_r0_r2_document_that_records_a_base_is_refused(verify_document):
    """The only-if half, and the half A-325 could not assert either. The
    honest whole-target document -- no base at all -- stays accepted, which
    is the property A-325 bought and this must not spend."""
    honest = load(HERE / "expected" / "sql-r2-v8-template.json")
    honest["judgment"]["r2"]["mode"] = "whole_target"
    honest["judgment"]["r2"]["targets"] = ["infra/db-init/init-scripts/01-schema.sql"]
    broken = copy.deepcopy(honest)
    del honest["judgment"]["resolved"]["base"]
    honest["judgment"]["resolved"].pop("base_resolution", None)
    refuses_only_the_defect(
        verify_document,
        honest,
        broken,
        "a whole-target R0,R2 document recording a base must be refused",
    )


def test_whole_target_r2_targets_obey_r1s_own_pairing_rules(verify_document):
    """`targets` is required under whole-target scope and forbidden outside
    it -- `judgment.r1`'s contract, word for word, on the tier that had
    none."""
    clean = load(HERE / "expected" / "sql-r2-v8-template.json")

    missing_targets = copy.deepcopy(clean)
    missing_targets["judgment"]["r2"]["mode"] = "whole_target"
    del missing_targets["judgment"]["resolved"]["base"]
    missing_targets["judgment"]["resolved"].pop("base_resolution", None)
    refuses_only_the_defect(
        verify_document,
        clean,
        missing_targets,
        "whole-target r2 without targets must be refused",
    )

    inert_targets = copy.deepcopy(clean)
    inert_targets["judgment"]["r2"]["targets"] = ["infra/db-init/init-scripts/01.sql"]
    refuses_only_the_defect(
        verify_document,
        clean,
        inert_targets,
        "changed-line r2 carrying targets must be refused",
    )


def test_two_tiers_cannot_record_two_different_lane_scopes(verify_document):
    """`mode` is a LANE-level scope. A document holding two of them makes
    "the lane's mode" ambiguous, and the base rule above cannot rest on an
    ambiguous premise."""
    clean = load(HERE / "expected" / "p25-pass-v8-template.json")
    assert clean["judgment"]["r1"]["mode"] == "changed_lines"
    broken = copy.deepcopy(clean)
    broken["judgment"]["r2"] = {
        "jobs": 1,
        "max_mutants": 50,
        "operators": ["python:compare-swap"],
        "kill_attribution": "unattributed",
        "mode": "whole_target",
        "targets": ["pkg/mod.py"],
    }
    assert verify_document(clean) == []
    assert verify_document(broken), "two disagreeing tier modes must be refused"


# --- B018: the producing judge's own identity --------------------------------


def test_a_verdict_may_name_the_build_that_produced_it(verify_document):
    """The additive half of this cut. A complete identity is accepted; the
    field's ABSENCE stays accepted too, because a source-tree invocation has
    no build artifact and must not invent one."""
    clean = load(HERE / "expected" / "sql-r2-v8-template.json")
    assert "judge_provenance" not in clean
    assert verify_document(clean) == []

    identified = copy.deepcopy(clean)
    identified["judge_provenance"] = {
        "name": "assay",
        "version": "2.4.3",
        "artifact": "wheel",
        "digest_algorithm": "sha256",
        "digest": A_REAL_DIGEST,
    }
    assert verify_document(identified) == []


@pytest.mark.parametrize("field", ["name", "version", "artifact", "digest_algorithm", "digest"])
def test_a_partial_judge_identity_is_refused_field_by_field(verify_document, field):
    """"Absent rather than partial" made mechanical: every one of the five
    fields is required, so no document can carry four of them and read as an
    identity."""
    clean = load(HERE / "expected" / "sql-r2-v8-template.json")
    clean["judge_provenance"] = {
        "name": "assay",
        "version": "2.4.3",
        "artifact": "zipapp",
        "digest_algorithm": "sha256",
        "digest": A_REAL_DIGEST,
    }
    broken = copy.deepcopy(clean)
    del broken["judge_provenance"][field]
    refuses_only_the_defect(
        verify_document, clean, broken, f"judge_provenance without {field} must be refused"
    )


@pytest.mark.parametrize(
    "digest",
    [A_REAL_DIGEST.upper(), A_REAL_DIGEST[:63], A_REAL_DIGEST + "a", "not-a-digest"],
)
def test_a_digest_that_is_not_lowercase_sha256_is_refused(verify_document, digest):
    """Lowercase and length are contract, not formatting: a consumer compares
    this against a digest it resolved itself, and two spellings of one digest
    are not equal."""
    clean = load(HERE / "expected" / "sql-r2-v8-template.json")
    clean["judge_provenance"] = {
        "name": "assay",
        "version": "2.4.3",
        "artifact": "wheel",
        "digest_algorithm": "sha256",
        "digest": A_REAL_DIGEST,
    }
    broken = copy.deepcopy(clean)
    broken["judge_provenance"]["digest"] = digest
    refuses_only_the_defect(
        verify_document, clean, broken, f"digest {digest!r} must be refused"
    )


def test_an_unknown_artifact_kind_or_algorithm_is_refused(verify_document):
    """Both vocabularies are CLOSED. `assay` ships exactly two artifacts and
    hashes with exactly one algorithm; a document naming a third of either
    describes something this contract cannot mean."""
    clean = load(HERE / "expected" / "sql-r2-v8-template.json")
    clean["judge_provenance"] = {
        "name": "assay",
        "version": "2.4.3",
        "artifact": "wheel",
        "digest_algorithm": "sha256",
        "digest": A_REAL_DIGEST,
    }
    for field, value in (("artifact", "sdist"), ("digest_algorithm", "sha512")):
        broken = copy.deepcopy(clean)
        broken["judge_provenance"][field] = value
        refuses_only_the_defect(
            verify_document, clean, broken, f"judge_provenance.{field} = {value!r}"
        )
