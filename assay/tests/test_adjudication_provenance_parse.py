"""B004/A-430 -- :func:`assay.adjudication.evaluate_provenance`, the ONE
registered ``image-provenance`` adjudicator: the closed-vocabulary tolerance
rules of the carve's §3.3 (assay validates exactly what it consumes and
NOTHING else about ``containers``/``status``/``labelled_revision``/``image``/
``tree_state``), the DA-R12 ``schema_version`` parser (the closed integer set
``{1, 2}``), and the O1-O9 acceptance oracles from
``nyxloom-trove/W2-CARVE-B004-provenance-verified.md``.

Every "assay does NOT refuse a real shape" test below uses the ACTUAL value
measured in the carve's §9/§3.3 (a real ciu document was read to find it),
never an invented one -- A-334/A-272's rule applied at the unit level, one
layer beneath the frozen-asset pipeline tests in
``test_adjudication_pipeline_integration.py``.
"""

from __future__ import annotations

import json

from assay.adjudication import evaluate_provenance
from assay.errors import Outcome, ReasonCode

_HEAD = "1b369e23" + "a" * 32


def _bytes(document: dict) -> bytes:
    return json.dumps(document).encode("utf-8")


def _green(**overrides) -> dict:
    document = {
        "schema_version": 1,
        "instance": "dstdns-dev",
        "commit_under_test": "1b369e23",
        "tree_state": "clean",
        "containers": [],
        "overall": "verified-match",
    }
    document.update(overrides)
    return document


# --- the green path, and the ONE fact assay itself checks on it (the commit) --


def test_a_verified_match_document_binding_head_is_pass_with_no_reason_code():
    outcome, reason_code = evaluate_provenance(_bytes(_green()), _HEAD)
    assert (outcome, reason_code) == (Outcome.PASS, None)


def test_carve_o4_a_verified_match_document_for_a_different_commit_is_unverified():
    outcome, reason_code = evaluate_provenance(_bytes(_green()), "deadbeef" + "a" * 32)
    assert (outcome, reason_code) == (Outcome.NO_MEASUREMENT, ReasonCode.PROVENANCE_UNVERIFIED)


def test_a_head_that_is_only_a_prefix_of_the_documents_longer_commit_does_not_bind():
    # `commit_under_test` is ciu's claim about ITS commit; the binding
    # direction is `head.startswith(commit_under_test)`, never the reverse.
    document = _green(commit_under_test="1b369e23aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")
    outcome, reason_code = evaluate_provenance(_bytes(document), "1b369e23")
    assert (outcome, reason_code) == (Outcome.NO_MEASUREMENT, ReasonCode.PROVENANCE_UNVERIFIED)


# --- DA-R12: the schema_version parser, the closed integer set {1, 2} --------


def test_schema_version_1_is_accepted():
    outcome, _ = evaluate_provenance(_bytes(_green(schema_version=1)), _HEAD)
    assert outcome is Outcome.PASS


def test_schema_version_2_is_accepted_the_same_way():
    outcome, _ = evaluate_provenance(_bytes(_green(schema_version=2)), _HEAD)
    assert outcome is Outcome.PASS


def test_schema_version_3_is_refused_as_an_unaccepted_future_shape():
    outcome, reason_code = evaluate_provenance(_bytes(_green(schema_version=3)), _HEAD)
    assert (outcome, reason_code) == (Outcome.ERROR, ReasonCode.FORMAT_MISMATCH)


def test_schema_version_as_the_string_2_is_refused_a_type_change_is_a_shape_change():
    outcome, reason_code = evaluate_provenance(_bytes(_green(schema_version="2")), _HEAD)
    assert (outcome, reason_code) == (Outcome.ERROR, ReasonCode.FORMAT_MISMATCH)


def test_schema_version_absent_is_refused():
    document = _green()
    del document["schema_version"]
    outcome, reason_code = evaluate_provenance(_bytes(document), _HEAD)
    assert (outcome, reason_code) == (Outcome.ERROR, ReasonCode.FORMAT_MISMATCH)


def test_schema_version_as_json_true_is_refused_bool_is_not_a_schema_version():
    # `True == 1` in Python; this document must not pass BY COINCIDENCE.
    outcome, reason_code = evaluate_provenance(_bytes(_green(schema_version=True)), _HEAD)
    assert (outcome, reason_code) == (Outcome.ERROR, ReasonCode.FORMAT_MISMATCH)


# --- carve O5: an unrecognised `overall` is refused, not guessed -------------


def test_an_unrecognised_overall_is_format_mismatch_not_a_guessed_no_measurement():
    outcome, reason_code = evaluate_provenance(_bytes(_green(overall="probably-fine")), _HEAD)
    assert (outcome, reason_code) == (Outcome.ERROR, ReasonCode.FORMAT_MISMATCH)


# --- carve row 8: all five non-green states collapse to ONE terminal --------


def test_every_non_green_overall_value_renders_the_same_terminal():
    for overall in (
        "mismatch",
        "not-verified-dirty",
        "not-verified-unknown",
        "not-verified-no-evidence",
        "refused-no-identity",
    ):
        outcome, reason_code = evaluate_provenance(_bytes(_green(overall=overall)), _HEAD)
        assert (outcome, reason_code) == (
            Outcome.NO_MEASUREMENT,
            ReasonCode.PROVENANCE_UNVERIFIED,
        ), overall


def test_carve_f7d_a_null_commit_under_test_on_a_non_green_document_is_not_refused():
    # Measured (carve F7(d)): ciu's own `refused-no-identity` fixture carries
    # `"commit_under_test": null`. The green-path-only grammar must not
    # inspect it here.
    document = _green(overall="refused-no-identity", commit_under_test=None)
    outcome, reason_code = evaluate_provenance(_bytes(document), _HEAD)
    assert (outcome, reason_code) == (Outcome.NO_MEASUREMENT, ReasonCode.PROVENANCE_UNVERIFIED)


# --- carve §3.3: assay asserts NOTHING beyond schema_version/overall/commit --
# --- every value below is a REAL measured shape, never invented (A-334) -----


def test_labelled_revision_a_branch_ref_not_a_sha_is_not_refused():
    # Measured: `postgres.labelled_revision == "refs/heads/master"` (carve §9
    # M2). The field is not even read by the adjudicator, but a document
    # carrying it must still parse.
    document = _green(
        containers=[
            {
                "name": "dstdns-98535c-postgres",
                "image": "postgres:16",
                "labelled_revision": "refs/heads/master",
                "status": "mismatch",
            }
        ],
    )
    outcome, _ = evaluate_provenance(_bytes(document), _HEAD)
    assert outcome is Outcome.PASS


def test_a_bare_image_id_not_name_colon_tag_is_not_refused():
    # Measured: `consul.image == "6cf88efc53e8"` (carve §9 M2).
    document = _green(
        containers=[
            {
                "name": "dstdns-98535c-consul",
                "image": "6cf88efc53e8",
                "labelled_revision": None,
                "status": "unlabelled",
            }
        ],
    )
    outcome, _ = evaluate_provenance(_bytes(document), _HEAD)
    assert outcome is Outcome.PASS


def test_containers_null_rather_than_an_empty_list_is_not_refused():
    # Measured (carve §9 M1/M8): `containers` is JSON `null`, not `[]`,
    # whenever ciu's own enumeration could not run (e.g. `not-a-checkout`,
    # a dirty tree).
    document = _green(overall="not-verified-dirty", containers=None)
    outcome, reason_code = evaluate_provenance(_bytes(document), _HEAD)
    assert (outcome, reason_code) == (Outcome.NO_MEASUREMENT, ReasonCode.PROVENANCE_UNVERIFIED)


def test_an_unlabelled_status_the_16_of_20_real_case_is_not_refused():
    document = _green(
        containers=[
            {
                "name": "dstdns-98535c-test-runner",
                "image": "dstdns/test-runner:latest",
                "labelled_revision": None,
                "status": "unlabelled",
            }
        ],
    )
    outcome, _ = evaluate_provenance(_bytes(document), _HEAD)
    assert outcome is Outcome.PASS


# --- row 5 vs row 6: unreadable (present-but-untrustworthy) vs malformed ------
# --- shape (decodable, wrong contents) -- N1's recorded, deliberate asymmetry -


def test_invalid_utf8_bytes_render_unreadable_artifact():
    outcome, reason_code = evaluate_provenance(b"\xff\xfe not utf-8", _HEAD)
    assert (outcome, reason_code) == (Outcome.ERROR, ReasonCode.UNREADABLE_ARTIFACT)


def test_invalid_json_renders_unreadable_artifact():
    outcome, reason_code = evaluate_provenance(b"{not json", _HEAD)
    assert (outcome, reason_code) == (Outcome.ERROR, ReasonCode.UNREADABLE_ARTIFACT)


def test_legible_json_that_is_not_an_object_renders_format_mismatch():
    # N1: decoded CLEANLY -- "unreadable" would be a false diagnosis of a
    # document assay read perfectly well and judged to be the wrong shape.
    outcome, reason_code = evaluate_provenance(json.dumps([1, 2, 3]).encode(), _HEAD)
    assert (outcome, reason_code) == (Outcome.ERROR, ReasonCode.FORMAT_MISMATCH)


def test_a_verified_match_document_with_an_illegible_commit_is_format_mismatch():
    document = _green(commit_under_test="not-hex-at-all")
    outcome, reason_code = evaluate_provenance(_bytes(document), _HEAD)
    assert (outcome, reason_code) == (Outcome.ERROR, ReasonCode.FORMAT_MISMATCH)


def test_a_verified_match_document_with_a_too_short_commit_is_format_mismatch():
    # ciu's own grammar is `--short=8` MINIMUM (carve §9 M10); fewer than 8
    # hex characters is not a legal `commit_under_test` on the green path.
    document = _green(commit_under_test="1234567")
    outcome, reason_code = evaluate_provenance(_bytes(document), _HEAD)
    assert (outcome, reason_code) == (Outcome.ERROR, ReasonCode.FORMAT_MISMATCH)
