"""B004/A-430 -- the adjudicator registry (A-078) and
:func:`assay.adjudication.load_adjudicated_evidence`: the caller-supplied
declared list, the closed source/key grammar repeated at this public
boundary (mirroring :mod:`tests.test_attestation_load_declared`'s coverage
for the Tier-3 sibling), and the descriptor-safe ``<adjudication_dir>/
<key>.json`` file convention.

The registry-vocabulary drift guard is the one test that is not a mirror of
anything in ``test_attestation_load_declared.py``, because attested evidence
has no registry to drift against: it is the same shape
``test_config_statement_attribution_format.py`` already uses for
`STATEMENT_ATTRIBUTABLE_FORMATS_BY_LANGUAGE` (DA-R1/A-406), applied here for
`assay.vocabulary.ADJUDICATED_EVIDENCE_KEYS`, which exists ONLY because
`assay.config` cannot import `assay.adjudication` directly (an import cycle
through `assay.verdict` -- see the constant's own docstring).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from assay import vocabulary
from assay.adjudication import ADJUDICATORS, load_adjudicated_evidence
from assay.errors import AssayError, Outcome, ReasonCode
from assay.verdict import EvidenceDeclaration


def _remaining() -> float:
    return 60.0


def _expired_remaining() -> float:
    raise AssayError(
        "the lane-wide deadline expired",
        outcome=Outcome.BUDGET_EXCEEDED,
        reason_code=ReasonCode.LANE_TIMEOUT,
    )


def _write_document(directory: Path, key: str, document: dict) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{key}.json").write_text(json.dumps(document), encoding="utf-8")


_GREEN = {
    "schema_version": 1,
    "instance": "x",
    "commit_under_test": "1b369e23",
    "tree_state": "clean",
    "containers": [],
    "overall": "verified-match",
}


# --- registry <-> vocabulary drift guard (DA-R1/A-406's shape, one field over) -


def test_the_registry_and_the_config_facing_vocabulary_name_exactly_the_same_keys():
    assert set(ADJUDICATORS) == vocabulary.ADJUDICATED_EVIDENCE_KEYS
    # A vacuity guard, exactly as `test_config_statement_attribution_format.
    # py` requires for its own derived set (A-406): if the registry were ever
    # emptied, this equality would still pass and the derivation would prove
    # nothing.
    assert vocabulary.ADJUDICATED_EVIDENCE_KEYS, "the derived vocabulary must not be empty"


# --- the closed source/key grammar, repeated at THIS public boundary (A-210) --


def test_a_declaration_whose_source_is_not_adjudicated_is_rejected_before_any_read(
    tmp_path: Path,
):
    declared = (EvidenceDeclaration(source="attested", key="review"),)

    with pytest.raises(AssayError) as excinfo:
        load_adjudicated_evidence(
            tmp_path,
            head="f" * 40,
            declared=declared,
            adjudication_dir="artifacts/adjudicated",
            remaining=_remaining,
        )
    assert excinfo.value.outcome is Outcome.ERROR
    assert excinfo.value.reason_code is ReasonCode.BAD_LANE_CONFIG
    assert not (tmp_path / "artifacts").exists(), "no read happened before the refusal"


def test_an_unregistered_adjudicator_key_is_rejected_before_any_read(tmp_path: Path):
    declared = (EvidenceDeclaration(source="adjudicated", key="no-such-adjudicator"),)

    with pytest.raises(AssayError) as excinfo:
        load_adjudicated_evidence(
            tmp_path,
            head="f" * 40,
            declared=declared,
            adjudication_dir="artifacts/adjudicated",
            remaining=_remaining,
        )
    assert excinfo.value.outcome is Outcome.ERROR
    assert excinfo.value.reason_code is ReasonCode.BAD_LANE_CONFIG
    assert "no-such-adjudicator" in str(excinfo.value)


# --- the descriptor-safe read boundary (row 4/row 5, carve §3.4) --------------


def test_an_absent_document_renders_provenance_unverified_not_an_error(tmp_path: Path):
    declared = (EvidenceDeclaration(source="adjudicated", key="image-provenance"),)

    result = load_adjudicated_evidence(
        tmp_path,
        head="f" * 40,
        declared=declared,
        adjudication_dir="artifacts/adjudicated",
        remaining=_remaining,
    )
    assert len(result) == 1
    assert result[0].status is Outcome.NO_MEASUREMENT
    assert result[0].reason_code is ReasonCode.PROVENANCE_UNVERIFIED
    assert result[0].verified_by_assay is False


def test_a_symlinked_document_is_unreadable_never_silently_followed(tmp_path: Path):
    real_target = tmp_path / "outside.json"
    real_target.write_text(json.dumps(_GREEN), encoding="utf-8")
    adjudication_dir = tmp_path / "artifacts" / "adjudicated"
    adjudication_dir.mkdir(parents=True)
    os.symlink(real_target, adjudication_dir / "image-provenance.json")
    declared = (EvidenceDeclaration(source="adjudicated", key="image-provenance"),)

    result = load_adjudicated_evidence(
        tmp_path,
        head="1b369e23" + "a" * 32,
        declared=declared,
        adjudication_dir="artifacts/adjudicated",
        remaining=_remaining,
    )
    assert result[0].status is Outcome.ERROR
    assert result[0].reason_code is ReasonCode.UNREADABLE_ARTIFACT


def test_a_document_that_fails_to_parse_renders_unreadable_artifact(tmp_path: Path):
    adjudication_dir = tmp_path / "artifacts" / "adjudicated"
    adjudication_dir.mkdir(parents=True)
    (adjudication_dir / "image-provenance.json").write_text("{not json", encoding="utf-8")
    declared = (EvidenceDeclaration(source="adjudicated", key="image-provenance"),)

    result = load_adjudicated_evidence(
        tmp_path,
        head="f" * 40,
        declared=declared,
        adjudication_dir="artifacts/adjudicated",
        remaining=_remaining,
    )
    assert result[0].status is Outcome.ERROR
    assert result[0].reason_code is ReasonCode.UNREADABLE_ARTIFACT


def test_a_well_formed_green_document_renders_pass_through_the_full_loader(tmp_path: Path):
    adjudication_dir = tmp_path / "artifacts" / "adjudicated"
    _write_document(adjudication_dir, "image-provenance", _GREEN)
    declared = (EvidenceDeclaration(source="adjudicated", key="image-provenance"),)

    result = load_adjudicated_evidence(
        tmp_path,
        head="1b369e23" + "a" * 32,
        declared=declared,
        adjudication_dir="artifacts/adjudicated",
        remaining=_remaining,
    )
    assert result[0].source == "adjudicated"
    assert result[0].key == "image-provenance"
    assert result[0].status is Outcome.PASS
    assert result[0].reason_code is None
    assert result[0].verified_by_assay is False


# --- A-212's one lane-wide deadline governs THIS loader too -------------------


def test_an_already_expired_deadline_is_observed_before_any_read(tmp_path: Path):
    adjudication_dir = tmp_path / "artifacts" / "adjudicated"
    _write_document(adjudication_dir, "image-provenance", _GREEN)
    declared = (EvidenceDeclaration(source="adjudicated", key="image-provenance"),)

    with pytest.raises(AssayError) as excinfo:
        load_adjudicated_evidence(
            tmp_path,
            head="1b369e23" + "a" * 32,
            declared=declared,
            adjudication_dir="artifacts/adjudicated",
            remaining=_expired_remaining,
        )
    assert excinfo.value.reason_code is ReasonCode.LANE_TIMEOUT


# --- the caller-supplied bound (this function does not assume every caller ---
# --- came through assay.config, exactly as A-210 states for attestation.py) --


def test_more_than_the_bound_is_rejected_even_from_a_hand_built_declaration(tmp_path: Path):
    from assay.adjudication import MAX_EVIDENCE_DECLARATIONS

    declared = tuple(
        EvidenceDeclaration(source="adjudicated", key="image-provenance")
        for _ in range(MAX_EVIDENCE_DECLARATIONS + 1)
    )
    # `EvidenceDeclaration` itself does not forbid a repeated identity (that
    # is `load_attested_evidence`'s own `_check_no_duplicate_declarations`,
    # a Tier-3-specific guard this Tier-2 loader has no equivalent of --
    # nothing here assembles a `declared_evidence[]` array from these, so
    # the bound is exercised directly).
    with pytest.raises(AssayError) as excinfo:
        load_adjudicated_evidence(
            tmp_path,
            head="f" * 40,
            declared=declared,
            adjudication_dir="artifacts/adjudicated",
            remaining=_remaining,
        )
    assert excinfo.value.reason_code is ReasonCode.BAD_LANE_CONFIG
