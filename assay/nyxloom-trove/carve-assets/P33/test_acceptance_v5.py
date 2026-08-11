"""P33's locked v5 acceptance suite. Carver-owned; implementers and reviewers
may not edit it (A-176/A-206/A-214 precedent).

This suite replaces `carve-assets/P26/test_acceptance.py` in the registered gate
(A-224). It runs POST-implementation, against the installed wheel, and every
expectation here is derived from the locked v5 schema and the shipped forbidden
modules rather than from whatever the implementer happens to write — so it
cannot be back-fitted.

**What moved out of P26's suite, and where it landed (A-222's second clause,
executed).** P26's suite validated four attestation-state artifacts
(`current`, `stale-directory`, `independent-errors`, `attestation-timeout`)
with `verify_document(expected) == []` plus exact equality. v5 changes nothing
about those shapes except `schema_version`, so `test_p26_attestation_shapes_
survive_v5` below reads P26's own locked v4 templates, bumps only
`schema_version` in memory, and requires all four to validate under v5. P26's
templates are never edited and its suite is never run again. That is the whole
of the carry-forward; nothing else in P26's suite asserted artifact shape.

Run: PYTHONPATH=src python3 -m pytest nyxloom-trove/carve-assets/P33/test_acceptance_v5.py -q -p no:randomly
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
P26_EXPECTED = ROOT / "nyxloom-trove" / "carve-assets" / "P26" / "expected"

SUBS = {
    "@ASSAY_VERSION@": "1.3.0",
    "@HEAD_OID@": "c23bbafbc3d52bd1a5d8ab58a23ca8ae61a70d9e",
    "@BASE_OID@": "93c31eebd233b2aa9eb95f5533695a29e7c11516",
    "@ATTESTED_OID@": "93c31eebd233b2aa9eb95f5533695a29e7c11516",
    "@STARTED@": "2026-08-11T00:00:00+00:00",
    "@ENDED@": "2026-08-11T00:00:01+00:00",
    "@SQL_HELPER_IDENTITY@": "assay-sql-sites 0.1.0 (libpg_query 17-6.1.0)",
    "@KILLED_REPLACEMENT_SHA@": "a" * 64,
    "@SURVIVED_REPLACEMENT_SHA@": "b" * 64,
    "@EQUIVALENT_REPLACEMENT_SHA@": "c" * 64,
    "@EQ1_SHA@": "1" * 64,
    "@EQ2_SHA@": "2" * 64,
    "@EQ3_SHA@": "3" * 64,
}

V5_TEMPLATES = [
    "missing-tool-v5-template.json",
    "sql-r2-v5-template.json",
    "ca1-r3-no-base-v5-template.json",
    "ca4-all-equivalent-v5-template.json",
]


def load(path: Path) -> dict:
    text = path.read_text()
    for key, value in SUBS.items():
        text = text.replace(key, value)
    return json.loads(text)


def refuses_only_the_defect(verify_document, clean: dict, broken: dict, why: str):
    """Every negative in this suite is DIFFERENTIAL, deliberately.

    A bare `assert verify_document(broken)` passes on a pre-implementation tree
    for the wrong reason -- the v4 verifier refuses any v5 document on its
    version alone, which is exactly the short-circuit that let three blocking
    defects survive P33's first carve. Requiring the unmodified control to
    verify CLEAN in the same breath makes that impossible: the pair can only
    both hold once the v5 verifier exists and the specific rule is implemented.
    """
    assert verify_document(clean) == [], (
        f"{why}: the control document must verify clean, so the negative below "
        f"cannot pass on a version mismatch"
    )
    assert verify_document(broken), why


@pytest.fixture(scope="module")
def verify_document():
    from assay.verify import verify_document as fn

    return fn


# --- identity -----------------------------------------------------------------

def test_schema_identity_is_internally_consistent():
    """B8: the v5 contract must not self-identify as v4.

    One assertion tying all three spellings together, so a future bump cannot
    move one and leave the others -- which is exactly what happened to v5's
    `$id` when the transform's rewrite silently matched nothing.
    """
    from assay import verdict as V

    schema = json.loads(
        (ROOT / "src" / "assay" / "schemas" / "verdict.schema.json").read_text()
    )
    assert schema["$id"] == "urn:assay:schema:verdict:5"
    assert schema["properties"]["schema_version"]["const"] == 5
    assert V.VERDICT_SCHEMA_VERSION == 5


def test_shipped_schema_is_byte_identical_to_the_locked_asset():
    shipped = (ROOT / "src" / "assay" / "schemas" / "verdict.schema.json").read_bytes()
    locked = (HERE / "verdict.schema.v5.json").read_bytes()
    assert shipped == locked, "the implementer altered the locked v5 schema"


def test_migration_transform_still_verifies_after_the_work():
    """B1: `--check` reads a committed v4 snapshot, so it survives the migration
    it verifies. Before the repair this exited 2 post-implementation and the
    requirement was unsatisfiable."""
    proc = subprocess.run(
        [sys.executable, str(HERE / "migrate_v4_to_v5.py"), "--check"],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


# --- the three negatives no migrated fixture can satisfy ----------------------

def test_a_v4_artifact_is_refused_for_its_version(verify_document):
    doc = load(P26_EXPECTED / "current-v4-template.json")
    assert doc["schema_version"] == 4
    failures = verify_document(doc)
    assert failures, "a v4 artifact must not validate against the v5 verifier"


def test_a_v5_artifact_missing_judgment_resolved_is_refused(verify_document):
    clean = load(HERE / "expected" / "sql-r2-v5-template.json")
    broken = load(HERE / "expected" / "sql-r2-v5-template.json")
    del broken["judgment"]["resolved"]
    refuses_only_the_defect(verify_document, clean, broken,
                            "judgment without resolved must be refused")


def test_a_cross_language_operator_is_refused(verify_document):
    """O2's artifact half: the prefix must equal judgment.resolved.language, not
    merely be one of the known prefixes."""
    clean = load(HERE / "expected" / "sql-r2-v5-template.json")
    broken = load(HERE / "expected" / "sql-r2-v5-template.json")
    broken["judgment"]["resolved"]["language"] = "python"
    refuses_only_the_defect(verify_document, clean, broken,
                            "a python lane carrying sql: operators must be refused")


# --- the locked templates -----------------------------------------------------

@pytest.mark.parametrize("name", V5_TEMPLATES)
def test_locked_v5_template_is_accepted(verify_document, name):
    """B2: this is the check that could not pass at carve time -- the SQL
    template contradicted A-117's precedence with a non-empty budget_exceeded
    bucket. Witnessed red before the repair, required green now."""
    assert verify_document(load(HERE / "expected" / name)) == []


def test_p26_attestation_shapes_survive_v5(verify_document):
    """A-222's carry-forward, executed. P26's four locked v4 templates are read,
    never edited; only `schema_version` is bumped in memory."""
    names = [
        "current-v4-template.json",
        "stale-directory-v4-template.json",
        "independent-errors-v4-template.json",
        "attestation-timeout-v4-template.json",
    ]
    for name in names:
        doc = load(P26_EXPECTED / name)
        doc["schema_version"] = 5
        assert verify_document(doc) == [], f"{name} shape did not survive v5"


# --- combined-axis fixtures ---------------------------------------------------

def test_ca1_r0r3_lane_needs_no_base(verify_document):
    """A-223(a). JUDGE_FIELDS_BY_RIGOR carries `base` for R1/R2 and not R3, so an
    R0,R3 lane has no comparison commit and must not be made to invent one."""
    doc = load(HERE / "expected" / "ca1-r3-no-base-v5-template.json")
    assert "base" not in doc["judgment"]["resolved"]
    assert verify_document(doc) == []


def test_ca1_an_r2_lane_without_base_is_refused(verify_document):
    """The other half of the conditional: absent for R3, required for R2."""
    clean = load(HERE / "expected" / "ca4-all-equivalent-v5-template.json")
    broken = load(HERE / "expected" / "ca4-all-equivalent-v5-template.json")
    del broken["judgment"]["resolved"]["base"]
    refuses_only_the_defect(verify_document, clean, broken,
                            "an R2 lane without resolved.base must be refused")


def test_ca3_two_independent_violations_produce_two_failures(verify_document):
    """Per-clause breaks, made observable (work item 3 / P26's one-hop note).
    One document violating O4's forbid clause AND O3's pairing clause must
    report both, not collapse into one diagnostic."""
    doc = load(HERE / "expected" / "ca4-all-equivalent-v5-template.json")
    doc["judgment"]["r2"]["kill_attribution"] = "unattributed"
    del doc["judgment"]["r2"]["equivalence_artifact"]      # pairing violation
    doc["claims"][1]["mutation"]["killed"] = [
        dict(doc["claims"][1]["mutation"]["equivalent"][0], kill_signal="23514")
    ]
    failures = verify_document(doc)
    assert len(failures) >= 2, f"expected two distinct failures, got {failures}"


def test_ca4_all_equivalent_is_inconclusive_not_pass(verify_document):
    """A-223(d), and the reason the exclusion clause is falsifiable at all.

    killed 0, survived 0, equivalent 3 proves nothing about the tests. Under the
    pre-P33 judge_mutation this walked to PASS -- A-026/A-035's 0/0-is-100% bug
    one layer down. An implementation that simply ignores `equivalent` renders
    PASS here and fails this test.
    """
    doc = load(HERE / "expected" / "ca4-all-equivalent-v5-template.json")
    assert doc["claims"][1]["status"] == "INCONCLUSIVE"
    assert doc["claims"][1]["reason_code"] == "ALL_MUTANTS_EQUIVALENT"
    assert doc["outcome"] == "INCONCLUSIVE" and doc["exit_code"] == 5
    assert verify_document(doc) == []


def test_ca4_equivalent_mutants_do_not_count_as_survived(verify_document):
    """The exclusion, from the other side: relabel the three equivalent entries
    as survived and the verdict must become FAIL/MUTANTS_SURVIVED, not stay
    INCONCLUSIVE. If an implementation treats the buckets alike, one of these two
    tests fails."""
    clean = load(HERE / "expected" / "ca4-all-equivalent-v5-template.json")
    broken = load(HERE / "expected" / "ca4-all-equivalent-v5-template.json")
    m = broken["claims"][1]["mutation"]
    m["survived"], m["equivalent"] = m["equivalent"], []
    refuses_only_the_defect(verify_document, clean, broken,
        "the same mutants as survived must not still verify as ALL_MUTANTS_EQUIVALENT")


def test_ca6_source_roots_are_the_declared_spelling(verify_document):
    """A-223(f) / A-049 / A-149. `resolved.source_roots` records the declared
    project-relative spelling, never the relocated absolute scratch paths. Two
    prior packages got a copy-based orchestration wrong about paths in a way no
    fixture could see; v5's single shared `resolved` is a fresh opportunity."""
    for name in V5_TEMPLATES:
        doc = load(HERE / "expected" / name)
        resolved = doc.get("judgment", {}).get("resolved")
        if not resolved:
            continue
        for root in resolved["source_roots"]:
            assert not root.startswith("/"), (
                f"{name}: source_roots must be project-relative, got {root!r}"
            )
            assert "/tmp/" not in root and "scratch" not in root


def test_kill_signal_is_rejected_outside_the_killed_bucket(verify_document):
    """A-223(e) / B10: a kill signal on a mutant nothing killed is a
    contradiction. Checked on every non-killed bucket, not just one."""
    clean = load(HERE / "expected" / "sql-r2-v5-template.json")
    for bucket in ("survived", "equivalent"):
        broken = load(HERE / "expected" / "sql-r2-v5-template.json")
        entries = broken["claims"][1]["mutation"][bucket]
        assert entries, f"the template must exercise the {bucket} bucket"
        entries[0]["kill_signal"] = "23514 check_violation"
        refuses_only_the_defect(verify_document, clean, broken,
                                f"kill_signal on a {bucket} entry must be refused")


def test_helpers_entry_requires_a_correspondingly_judged_claim(verify_document):
    """A-223(c): only the OBSERVABLE direction. A `mutation-sites` helper entry
    requires an R2 claim carrying a mutation payload. The converse -- a claim
    requiring a helpers entry -- is P34's, because nothing in the bytes says a
    claim used a helper."""
    clean = load(HERE / "expected" / "ca1-r3-no-base-v5-template.json")
    broken = load(HERE / "expected" / "ca1-r3-no-base-v5-template.json")
    broken["helpers"] = [
        {
            "role": "mutation-sites",
            "tool": "assay-sql-sites",
            "resolved_path": "/opt/assay-helpers/bin/assay-sql-sites",
            "identity": "assay-sql-sites 0.1.0",
        }
    ]
    refuses_only_the_defect(verify_document, clean, broken,
        "a mutation-sites helper on a lane with no R2 mutation claim must be refused")
