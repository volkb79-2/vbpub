"""Format loading: ``parse_attestation``/``load_attestation_file`` — pure,
no git involved. Every malformed shape here is a STATIC, committed fixture
(``tests/fixtures/attestations/*.json``) precisely because its defect is
independent of any git commit's resolvability: a JSON object missing
``producer`` is broken before assay ever asks git anything.

Negative (this module's own slice of O3): a malformed attestation FILE must
raise ``ERROR``/``UNREADABLE_ARTIFACT`` here rather than being silently
accepted with missing/wrong-typed fields that would only surface as a
confusing failure two layers deeper (in :func:`assay.attestation.
evaluate_attestation` or in :class:`assay.verdict.Evidence`'s own
construction-time checks).

P26: :func:`load_attestation_file` now reads exactly
``<attestation_dir>/<key>.json`` through the descriptor-safe seam
(:func:`assay.safeio.read_bounded_input`) rather than an arbitrary caller-
composed path -- every fixture here is addressed by
``(project_root, attestation_dir, key)`` instead of a bare :class:`Path`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from assay.attestation import AttestationRecord, load_attestation_file, parse_attestation
from assay.errors import AssayError, Outcome, ReasonCode

FIXTURE_ROOT = Path(__file__).resolve().parent / "fixtures"
ATTESTATION_DIR = "attestations"


def test_a_well_formed_file_parses_into_an_attestation_record():
    record = load_attestation_file(
        FIXTURE_ROOT, attestation_dir=ATTESTATION_DIR, key="well_formed_example"
    )

    assert record == AttestationRecord(
        producer="adversarial-review-bot-v3",
        attested_commit="0123456789abcdef0123456789abcdef01234567",
        reviewed_paths=("src/assay/example.py", "src/assay/other.py"),
    )


@pytest.mark.parametrize(
    "key",
    [
        "missing_producer",
        "reviewed_paths_not_array",
        "reviewed_paths_not_all_strings",
        "empty_reviewed_paths",
        "not_valid_json",
        "top_level_not_an_object",
    ],
)
def test_a_malformed_file_raises_unreadable_artifact(key: str):
    with pytest.raises(AssayError) as excinfo:
        load_attestation_file(FIXTURE_ROOT, attestation_dir=ATTESTATION_DIR, key=key)

    assert excinfo.value.outcome is Outcome.ERROR
    assert excinfo.value.reason_code is ReasonCode.UNREADABLE_ARTIFACT
    assert key in str(excinfo.value)


def test_a_missing_file_returns_none_not_a_raised_error(tmp_path: Path):
    # P26/A-210: absence is now the loader's own None -- the declared
    # producer supplied no record. Present-but-unreadable is
    # load_attestation_file's OTHER concern, proven above.
    record = load_attestation_file(tmp_path, attestation_dir="attestations", key="does-not-exist")

    assert record is None


# --- parse_attestation is the shared, filesystem-free core -------------------


def test_parse_attestation_accepts_the_identical_text_load_attestation_file_reads():
    text = (FIXTURE_ROOT / ATTESTATION_DIR / "well_formed_example.json").read_text(
        encoding="utf-8"
    )

    record = parse_attestation(text, source_name="<inline>")

    assert record.producer == "adversarial-review-bot-v3"
    assert record.reviewed_paths == ("src/assay/example.py", "src/assay/other.py")


def test_parse_attestation_names_its_source_in_the_error_message():
    with pytest.raises(AssayError, match="my-inline-source"):
        parse_attestation("{}", source_name="my-inline-source")


# --- AttestationRecord's own construction-time discipline (A-092) -----------


def test_attestation_record_rejects_an_empty_producer():
    with pytest.raises(ValueError, match="producer"):
        AttestationRecord(
            producer="", attested_commit="a" * 40, reviewed_paths=("x.py",)
        )


def test_attestation_record_rejects_an_empty_attested_commit():
    with pytest.raises(ValueError, match="attested_commit"):
        AttestationRecord(producer="p", attested_commit="", reviewed_paths=("x.py",))


def test_attestation_record_rejects_empty_reviewed_paths():
    with pytest.raises(ValueError, match="reviewed_paths"):
        AttestationRecord(producer="p", attested_commit="a" * 40, reviewed_paths=())


def test_attestation_record_rejects_a_blank_reviewed_path():
    with pytest.raises(ValueError, match="reviewed path"):
        AttestationRecord(
            producer="p", attested_commit="a" * 40, reviewed_paths=("x.py", "")
        )


def test_attestation_record_rejects_a_duplicate_reviewed_path():
    with pytest.raises(ValueError, match="duplicate"):
        AttestationRecord(
            producer="p", attested_commit="a" * 40, reviewed_paths=("x.py", "x.py")
        )
