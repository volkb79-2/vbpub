"""Wave-1's one-for-one v6 successors for the 26 locked P33 nodes that
`schema_version` 5 -> 6 (A-261/A-262/A-264, amended for `snapshot_policy` by
A-269) legitimately reddens.

**How the deselection list was derived.** Per the carve's own instruction --
"derive the deselection list by MEASUREMENT, not by reading" -- v6 was
implemented first, then `nyxloom-trove/carve-assets/P33/test_acceptance_v5.py`
was run UNMODIFIED (that module is carver-owned, locked, and forbidden to
edit -- A-176/A-206/A-214/A-222) and every red was inspected individually with
`--tb=short`. All 26 failed for exactly ONE underlying cause: `assay.verify`'s
schema-version guard is a short-circuit -- it returns `["schema_version 5 is
not this verifier's version 6: ..."]` and never reaches any downstream
per-field check, so `verify_document(clean) == []` (the differential control
every negative in that suite requires) fails before the negative under test
ever runs. Two of the 26 (`test_schema_identity_is_internally_consistent`,
`test_shipped_schema_is_byte_identical_to_the_locked_asset`) fail even more
directly: they assert the literal string `"urn:assay:schema:verdict:5"` and
byte-identity with the frozen `verdict.schema.v5.json` respectively. None of
the 26 is a regression; every one is the hard cut behaving exactly as
specified. (The other 5 nodes the gate script already deselects --
`test_config_fixture_itself_loads_today` and its four siblings -- are
B006a/WI-1's LANE_SCHEMA_VERSION work, unrelated to this schema-version cut,
and already have their own v2 successors in
`tests/test_lane_schema_v2_locked_successors.py`; they are not repeated here.)

**Why a v6 successor and not an edit.** `carve-assets/P33/**` is locked
carver-owned evidence (A-222); this wave amends it forward by *addition*, per
the exact precedent P33 itself set for P26's four v4-shape deselections. This
module gets its OWN `expected/` directory -- the six v5 templates under
`carve-assets/P33/expected/` stay frozen, byte-untouched, and are never
rewritten into v6. The six v6 siblings here were produced by handing each v5
template (loaded as raw JSON, before placeholder substitution -- the three
structural edits below never touch a `@PLACEHOLDER@` token, so substitution
still happens at load time exactly as it does in the locked suite) to
`migrate_v5_to_v6.py`'s own `transform_document`, then verifying each result
schema-valid and `assay.verify`-clean before writing it out. That is the same
function the real migration runs, so a v6 template here and a migrated
production fixture are produced by identical code, not two hand-maintained
copies that could quietly drift apart.

Also new here: `verdict.schema.v6.json`, a locked byte snapshot of the schema
this commit ships -- the same role `carve-assets/P33/verdict.schema.v5.json`
played for P33's own byte-identity test, one wave later.

============================================================================  ==============================================================================
Locked node (rootdir-relative, deselected in tester-unified-gate.sh)          Successor in this module
============================================================================  ==============================================================================
test_schema_identity_is_internally_consistent                                 test_schema_identity_is_internally_consistent_under_v6
test_shipped_schema_is_byte_identical_to_the_locked_asset                     test_shipped_schema_is_byte_identical_to_the_locked_v6_asset
test_a_v5_artifact_missing_judgment_resolved_is_refused                       test_a_v6_artifact_missing_judgment_resolved_is_refused
test_a_cross_language_operator_is_refused                                     test_a_cross_language_operator_is_refused_under_v6
test_locked_v5_template_is_accepted[missing-tool-v5-template.json]            test_locked_v6_template_is_accepted[missing-tool-v6-template.json]
test_locked_v5_template_is_accepted[sql-r2-v5-template.json]                  test_locked_v6_template_is_accepted[sql-r2-v6-template.json]
test_locked_v5_template_is_accepted[ca1-r3-no-base-v5-template.json]          test_locked_v6_template_is_accepted[ca1-r3-no-base-v6-template.json]
test_locked_v5_template_is_accepted[ca4-all-equivalent-v5-template.json]      test_locked_v6_template_is_accepted[ca4-all-equivalent-v6-template.json]
test_p26_attestation_shapes_survive_v5                                        test_p26_attestation_shapes_survive_v6
test_ca1_r0r3_lane_needs_no_base                                              test_ca1_r0r3_lane_needs_no_base_under_v6
test_ca1_an_r2_lane_without_base_is_refused                                   test_ca1_an_r2_lane_without_base_is_refused_under_v6
test_ca3_two_independent_violations_produce_two_failures                      test_ca3_two_independent_violations_produce_two_failures_under_v6
test_ca4_all_equivalent_is_inconclusive_not_pass                              test_ca4_all_equivalent_is_inconclusive_not_pass_under_v6
test_ca4_equivalent_mutants_do_not_count_as_survived                          test_ca4_equivalent_mutants_do_not_count_as_survived_under_v6
test_kill_signal_is_rejected_outside_the_killed_bucket                        test_kill_signal_is_rejected_outside_the_killed_bucket_under_v6
test_helpers_entry_requires_a_correspondingly_judged_claim                    test_helpers_entry_requires_a_correspondingly_judged_claim_under_v6
test_ca9_payload_free_all_mutants_equivalent_is_refused                       test_ca9_payload_free_all_mutants_equivalent_is_refused_under_v6
test_ca9_all_mutants_equivalent_is_bound_to_r2                                test_ca9_all_mutants_equivalent_is_bound_to_r2_under_v6
test_base_is_forbidden_unless_r1_or_r2                                        test_base_is_forbidden_unless_r1_or_r2_under_v6
test_ca10_declared_attribution_requires_a_kill_signal_artifact                test_ca10_declared_attribution_requires_a_kill_signal_artifact_under_v6
test_ca10_declared_requires_a_kill_signal_on_every_killed_entry               test_ca10_declared_requires_a_kill_signal_on_every_killed_entry_under_v6
test_ca10_unattributed_forbids_a_kill_signal_on_a_killed_entry                test_ca10_unattributed_forbids_a_kill_signal_on_a_killed_entry_under_v6
test_p25_v5_siblings_validate[pass-v4-template.json-p25-pass-v5-template.json]         test_p25_v6_siblings_validate[p25-pass-v6-template.json]
test_p25_v5_siblings_validate[missing-v4-template.json-p25-missing-v5-template.json]   test_p25_v6_siblings_validate[p25-missing-v6-template.json]
test_helpers_executable_code_requires_a_payload_bearing_claim                 test_helpers_executable_code_requires_a_payload_bearing_claim_under_v6
test_helpers_is_omitted_when_no_helper_ran                                    test_helpers_is_omitted_when_no_helper_ran_under_v6
============================================================================  ==============================================================================

Run: PYTHONPATH=src python3 -m pytest nyxloom-trove/carve-assets/W1/test_acceptance_v6.py -q -p no:randomly
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
P26_EXPECTED = ROOT / "nyxloom-trove" / "carve-assets" / "P26" / "expected"

# Identical to the locked suite's own SUBS -- these six templates were
# produced by transforming the locked suite's own v5 templates, which
# carry these same placeholder tokens, and the runtime substitution for a
# template's ANCESTRY is a fact about the template's shape, not about
# which schema version happens to be reading it.
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

V6_TEMPLATES = [
    "missing-tool-v6-template.json",
    "sql-r2-v6-template.json",
    "ca1-r3-no-base-v6-template.json",
    "ca4-all-equivalent-v6-template.json",
]

P25_V6_TEMPLATES = ["p25-pass-v6-template.json", "p25-missing-v6-template.json"]


def load(path: Path) -> dict:
    text = path.read_text()
    for key, value in SUBS.items():
        text = text.replace(key, value)
    return json.loads(text)


def refuses_only_the_defect(verify_document, clean: dict, broken: dict, why: str):
    """Same differential discipline as the locked suite's own helper
    (`test_acceptance_v5.py:72`): the unmodified control must verify clean
    in the same breath the defect is asserted to fail, so the negative
    cannot pass merely because SOME rule elsewhere in the document is
    violated -- least of all a schema-version mismatch.
    """
    assert verify_document(clean) == [], (
        f"{why}: the control document must verify clean under v6, so the "
        f"negative below cannot pass for an unrelated reason"
    )
    assert verify_document(broken), why


@pytest.fixture(scope="module")
def verify_document():
    from assay.verify import verify_document as fn

    return fn


# --- identity -----------------------------------------------------------------


def test_schema_identity_is_internally_consistent_under_v6():
    """Successor to `test_schema_identity_is_internally_consistent`."""
    from assay import verdict as V

    schema = json.loads(
        (ROOT / "src" / "assay" / "schemas" / "verdict.schema.json").read_text()
    )
    assert schema["$id"] == "urn:assay:schema:verdict:6"
    assert schema["properties"]["schema_version"]["const"] == 6
    assert V.VERDICT_SCHEMA_VERSION == 6


def test_shipped_schema_is_byte_identical_to_the_locked_v6_asset():
    """Successor to `test_shipped_schema_is_byte_identical_to_the_locked_asset`.

    `verdict.schema.v6.json` is a byte snapshot of the schema THIS commit
    ships, taken in the same commit -- the v6 analogue of the role
    `carve-assets/P33/verdict.schema.v5.json` played for P33's own version.
    """
    shipped = (ROOT / "src" / "assay" / "schemas" / "verdict.schema.json").read_bytes()
    locked = (HERE / "verdict.schema.v6.json").read_bytes()
    assert shipped == locked, "the shipped schema drifted from the v6 snapshot landed with it"


# --- the two negatives the schema-version short-circuit was masking -----------


def test_a_v6_artifact_missing_judgment_resolved_is_refused(verify_document):
    """Successor to `test_a_v5_artifact_missing_judgment_resolved_is_refused`."""
    clean = load(HERE / "expected" / "sql-r2-v6-template.json")
    broken = load(HERE / "expected" / "sql-r2-v6-template.json")
    del broken["judgment"]["resolved"]
    refuses_only_the_defect(verify_document, clean, broken,
                            "judgment without resolved must be refused (v6)")


def test_a_cross_language_operator_is_refused_under_v6(verify_document):
    """Successor to `test_a_cross_language_operator_is_refused`."""
    clean = load(HERE / "expected" / "sql-r2-v6-template.json")
    broken = load(HERE / "expected" / "sql-r2-v6-template.json")
    broken["judgment"]["resolved"]["language"] = "python"
    refuses_only_the_defect(verify_document, clean, broken,
                            "a python lane carrying sql: operators must be refused (v6)")


# --- the locked v6 templates ---------------------------------------------------


@pytest.mark.parametrize("name", V6_TEMPLATES)
def test_locked_v6_template_is_accepted(verify_document, name):
    """Successor to `test_locked_v5_template_is_accepted`."""
    assert verify_document(load(HERE / "expected" / name)) == []


def test_p26_attestation_shapes_survive_v6(verify_document):
    """Successor to `test_p26_attestation_shapes_survive_v5`. P26's four
    locked v4 templates are R0-only, so bumping only `schema_version` to 6
    in memory is sufficient -- no `snapshot_policy` object belongs on an
    R0-only document (§6 Migration bucket 1's rule, amended by A-269)."""
    names = [
        "current-v4-template.json",
        "stale-directory-v4-template.json",
        "independent-errors-v4-template.json",
        "attestation-timeout-v4-template.json",
    ]
    for name in names:
        doc = load(P26_EXPECTED / name)
        doc["schema_version"] = 6
        assert verify_document(doc) == [], f"{name} shape did not survive v6"


# --- combined-axis fixtures ---------------------------------------------------


def test_ca1_r0r3_lane_needs_no_base_under_v6(verify_document):
    """Successor to `test_ca1_r0r3_lane_needs_no_base`."""
    doc = load(HERE / "expected" / "ca1-r3-no-base-v6-template.json")
    assert "base" not in doc["judgment"]["resolved"]
    assert verify_document(doc) == []


def test_ca1_an_r2_lane_without_base_is_refused_under_v6(verify_document):
    """Successor to `test_ca1_an_r2_lane_without_base_is_refused`."""
    clean = load(HERE / "expected" / "ca4-all-equivalent-v6-template.json")
    broken = load(HERE / "expected" / "ca4-all-equivalent-v6-template.json")
    del broken["judgment"]["resolved"]["base"]
    refuses_only_the_defect(verify_document, clean, broken,
                            "an R2 lane without resolved.base must be refused (v6)")


def test_ca3_two_independent_violations_produce_two_failures_under_v6(verify_document):
    """Successor to `test_ca3_two_independent_violations_produce_two_failures`."""
    doc = load(HERE / "expected" / "ca4-all-equivalent-v6-template.json")
    doc["judgment"]["r2"]["kill_attribution"] = "unattributed"
    del doc["judgment"]["r2"]["equivalence_artifact"]      # pairing violation
    doc["claims"][1]["mutation"]["killed"] = [
        dict(doc["claims"][1]["mutation"]["equivalent"][0], kill_signal="23514")
    ]
    failures = verify_document(doc)
    assert len(failures) >= 2, f"expected two distinct failures, got {failures}"


def test_ca4_all_equivalent_is_inconclusive_not_pass_under_v6(verify_document):
    """Successor to `test_ca4_all_equivalent_is_inconclusive_not_pass`."""
    doc = load(HERE / "expected" / "ca4-all-equivalent-v6-template.json")
    assert doc["claims"][1]["status"] == "INCONCLUSIVE"
    assert doc["claims"][1]["reason_code"] == "ALL_MUTANTS_EQUIVALENT"
    assert doc["outcome"] == "INCONCLUSIVE" and doc["exit_code"] == 5
    assert verify_document(doc) == []


def test_ca4_equivalent_mutants_do_not_count_as_survived_under_v6(verify_document):
    """Successor to `test_ca4_equivalent_mutants_do_not_count_as_survived`."""
    clean = load(HERE / "expected" / "ca4-all-equivalent-v6-template.json")
    broken = load(HERE / "expected" / "ca4-all-equivalent-v6-template.json")
    m = broken["claims"][1]["mutation"]
    m["survived"], m["equivalent"] = m["equivalent"], []
    refuses_only_the_defect(verify_document, clean, broken,
        "the same mutants as survived must not still verify as ALL_MUTANTS_EQUIVALENT (v6)")


def test_kill_signal_is_rejected_outside_the_killed_bucket_under_v6(verify_document):
    """Successor to `test_kill_signal_is_rejected_outside_the_killed_bucket`."""
    clean = load(HERE / "expected" / "sql-r2-v6-template.json")
    for bucket in ("survived", "equivalent"):
        broken = load(HERE / "expected" / "sql-r2-v6-template.json")
        entries = broken["claims"][1]["mutation"][bucket]
        assert entries, f"the template must exercise the {bucket} bucket"
        entries[0]["kill_signal"] = "23514 check_violation"
        refuses_only_the_defect(verify_document, clean, broken,
                                f"kill_signal on a {bucket} entry must be refused (v6)")


def test_helpers_entry_requires_a_correspondingly_judged_claim_under_v6(verify_document):
    """Successor to `test_helpers_entry_requires_a_correspondingly_judged_claim`."""
    clean = load(HERE / "expected" / "ca1-r3-no-base-v6-template.json")
    broken = load(HERE / "expected" / "ca1-r3-no-base-v6-template.json")
    broken["helpers"] = [
        {
            "role": "mutation-sites",
            "tool": "assay-sql-sites",
            "resolved_path": "/opt/assay-helpers/bin/assay-sql-sites",
            "identity": "assay-sql-sites 0.1.0",
        }
    ]
    refuses_only_the_defect(verify_document, clean, broken,
        "a mutation-sites helper on a lane with no R2 mutation claim must be refused (v6)")


def test_ca9_payload_free_all_mutants_equivalent_is_refused_under_v6(verify_document):
    """Successor to `test_ca9_payload_free_all_mutants_equivalent_is_refused`."""
    clean = load(HERE / "expected" / "ca4-all-equivalent-v6-template.json")
    broken = load(HERE / "expected" / "ca4-all-equivalent-v6-template.json")
    del broken["claims"][1]["mutation"]
    refuses_only_the_defect(verify_document, clean, broken,
        "a payload-free ALL_MUTANTS_EQUIVALENT must be refused (v6)")


def test_ca9_all_mutants_equivalent_is_bound_to_r2_under_v6(verify_document):
    """Successor to `test_ca9_all_mutants_equivalent_is_bound_to_r2`."""
    clean = load(HERE / "expected" / "ca1-r3-no-base-v6-template.json")
    broken = load(HERE / "expected" / "ca1-r3-no-base-v6-template.json")
    broken["claims"][1] = {
        "rigor": "R3", "source": "computed", "status": "INCONCLUSIVE",
        "verified_by_assay": True, "reason_code": "ALL_MUTANTS_EQUIVALENT",
    }
    refuses_only_the_defect(verify_document, clean, broken,
        "ALL_MUTANTS_EQUIVALENT on a non-R2 claim must be refused (v6)")


def test_base_is_forbidden_unless_r1_or_r2_under_v6(verify_document):
    """Successor to `test_base_is_forbidden_unless_r1_or_r2`."""
    clean = load(HERE / "expected" / "ca1-r3-no-base-v6-template.json")
    broken = load(HERE / "expected" / "ca1-r3-no-base-v6-template.json")
    broken["judgment"]["resolved"]["base"] = "9" * 40
    refuses_only_the_defect(verify_document, clean, broken,
        "an r3-only judgment must not record a comparison base (v6)")


def test_ca10_declared_attribution_requires_a_kill_signal_artifact_under_v6(verify_document):
    """Successor to `test_ca10_declared_attribution_requires_a_kill_signal_artifact`."""
    clean = load(HERE / "expected" / "sql-r2-v6-template.json")
    broken = load(HERE / "expected" / "sql-r2-v6-template.json")
    del broken["judgment"]["r2"]["kill_signal_artifact"]
    refuses_only_the_defect(verify_document, clean, broken,
        "kill_attribution=declared without kill_signal_artifact must be refused (v6)")


def test_ca10_declared_requires_a_kill_signal_on_every_killed_entry_under_v6(verify_document):
    """Successor to `test_ca10_declared_requires_a_kill_signal_on_every_killed_entry`."""
    clean = load(HERE / "expected" / "sql-r2-v6-template.json")
    broken = load(HERE / "expected" / "sql-r2-v6-template.json")
    del broken["claims"][1]["mutation"]["killed"][0]["kill_signal"]
    refuses_only_the_defect(verify_document, clean, broken,
        "a killed entry without kill_signal under declared must be refused (v6)")


def test_ca10_unattributed_forbids_a_kill_signal_on_a_killed_entry_under_v6(verify_document):
    """Successor to `test_ca10_unattributed_forbids_a_kill_signal_on_a_killed_entry`."""
    clean = load(HERE / "expected" / "ca4-all-equivalent-v6-template.json")
    broken = load(HERE / "expected" / "sql-r2-v6-template.json")
    broken["judgment"]["r2"]["kill_attribution"] = "unattributed"
    del broken["judgment"]["r2"]["kill_signal_artifact"]
    refuses_only_the_defect(verify_document, clean, broken,
        "unattributed with a kill_signal on a killed entry must be refused (v6)")


@pytest.mark.parametrize("v6_name", P25_V6_TEMPLATES)
def test_p25_v6_siblings_validate(verify_document, v6_name):
    """Successor to `test_p25_v5_siblings_validate`."""
    assert verify_document(load(HERE / "expected" / v6_name)) == []


def test_helpers_executable_code_requires_a_payload_bearing_claim_under_v6(verify_document):
    """Successor to `test_helpers_executable_code_requires_a_payload_bearing_claim`."""
    entry = {
        "role": "executable-code", "tool": "assay-go-exec",
        "resolved_path": "/opt/assay-helpers/bin/assay-go-exec",
        "identity": "assay-go-exec 0.1.0",
    }
    # R2 branch: accepted alongside a mutation-bearing claim.
    with_r2 = load(HERE / "expected" / "sql-r2-v6-template.json")
    with_r2["helpers"] = [entry]
    assert verify_document(with_r2) == [], (
        "executable-code alongside an R2 mutation claim must be accepted (v6)"
    )
    # R1 branch: accepted alongside a coverage-bearing claim.
    r1_doc = load(HERE / "expected" / "p25-pass-v6-template.json")
    r1_clean = json.loads(json.dumps(r1_doc))
    r1_doc["helpers"] = [entry]
    assert verify_document(r1_doc) == [], (
        "executable-code alongside an R1 coverage claim must be accepted (v6)"
    )
    # Neither branch: same document, one defect.
    clean = load(HERE / "expected" / "ca1-r3-no-base-v6-template.json")
    broken = load(HERE / "expected" / "ca1-r3-no-base-v6-template.json")
    broken["helpers"] = [entry]
    refuses_only_the_defect(verify_document, clean, broken,
        "executable-code on an R3-only lane carries no payload-bearing claim (v6)")
    assert "helpers" not in r1_clean


def test_helpers_is_omitted_when_no_helper_ran_under_v6(verify_document):
    """Successor to `test_helpers_is_omitted_when_no_helper_ran`."""
    for name in V6_TEMPLATES:
        doc = load(HERE / "expected" / name)
        if name == "sql-r2-v6-template.json":
            continue  # the one template that legitimately carries helpers
        assert "helpers" not in doc, (
            f"{name} carries an empty/absent-helper field; the emission default "
            f"is omission"
        )
        assert verify_document(doc) == []
