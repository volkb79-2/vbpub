"""B105 edge coverage for the bounded attestation reader and batch planner."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from assay import attestation
from assay.errors import AssayError, Outcome, ReasonCode
from assay.verdict import EvidenceDeclaration


def _document(**changes) -> str:
    payload = {
        "producer": "reviewer-v1",
        "attested_commit": "a" * 40,
        "reviewed_paths": ["src/module.py"],
    }
    payload.update(changes)
    return json.dumps(payload, ensure_ascii=True)


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"producer": 7}, "producer.*must be a string"),
        ({"producer": ""}, "producer.*must be 1"),
        ({"producer": "x" * (attestation.MAX_PRODUCER_BYTES + 1)}, "producer.*must be 1"),
        ({"producer": "\ud800"}, "producer.*cannot be encoded as UTF-8"),
        ({"attested_commit": 3}, "attested_commit.*40 lowercase hex"),
        ({"attested_commit": "A" * 40}, "attested_commit.*40 lowercase hex"),
        ({"reviewed_paths": None}, "reviewed_paths.*array"),
        ({"reviewed_paths": []}, "reviewed_paths.*array"),
        ({"reviewed_paths": [3]}, "reviewed path must be a string"),
        ({"reviewed_paths": ["a" * (attestation.MAX_REVIEWED_PATH_BYTES + 1)]}, "reviewed path must be 1"),
        ({"reviewed_paths": ["/absolute.py"]}, "not project-relative"),
        ({"reviewed_paths": ["src//module.py"]}, "not canonical"),
        ({"reviewed_paths": ["src/./module.py"]}, "not canonical"),
        ({"reviewed_paths": ["src/../module.py"]}, "not canonical"),
        ({"reviewed_paths": ["src/\ud800.py"]}, "cannot be encoded as UTF-8"),
        ({"reviewed_paths": ["same.py", "same.py"]}, "contains a duplicate"),
    ],
)
def test_attestation_parser_maps_each_field_boundary_to_unreadable(
    changes: dict, message: str
):
    with pytest.raises(AssayError, match=message) as caught:
        attestation.parse_attestation(_document(**changes), source_name="review.json")
    assert caught.value.outcome is Outcome.ERROR
    assert caught.value.reason_code is ReasonCode.UNREADABLE_ARTIFACT


def test_attestation_rejects_duplicate_json_member_names():
    text = (
        '{"producer":"one","producer":"two",'
        '"attested_commit":"' + "a" * 40 + '","reviewed_paths":["x.py"]}'
    )
    with pytest.raises(AssayError, match="duplicate JSON member 'producer'"):
        attestation.parse_attestation(text, source_name="review.json")


def test_attestation_record_constructor_error_is_wrapped_at_the_parser_boundary(
    monkeypatch: pytest.MonkeyPatch,
):
    def reject(**_kwargs):
        raise ValueError("record invariant changed")

    monkeypatch.setattr(attestation, "AttestationRecord", reject)
    with pytest.raises(AssayError, match="record invariant changed") as caught:
        attestation.parse_attestation(_document(), source_name="review.json")
    assert caught.value.reason_code is ReasonCode.UNREADABLE_ARTIFACT


def test_attestation_file_with_non_utf8_bytes_is_typed_unreadable(tmp_path: Path):
    directory = tmp_path / "attestations"
    directory.mkdir()
    (directory / "review.json").write_bytes(b"\xff\xfe")

    with pytest.raises(AssayError, match="not valid UTF-8") as caught:
        attestation.load_attestation_file(
            tmp_path, attestation_dir="attestations", key="review"
        )

    assert caught.value.reason_code is ReasonCode.UNREADABLE_ARTIFACT


@pytest.mark.parametrize(
    "value",
    [
        None,
        "",
        "\ud800",
        "x" * (attestation.MAX_ATTESTATION_DIR_BYTES + 1),
        "/absolute",
        "has\ncontrol",
        "nested//dir",
        "./nested",
        "nested/.",
        ".",
        "..",
        "/".join(["x"] * (attestation.MAX_ATTESTATION_DIR_COMPONENTS + 1)),
    ],
)
def test_attestation_directory_spelling_refuses_each_invalid_shape(value: object):
    with pytest.raises(AssayError) as caught:
        attestation._validate_attestation_dir(value)
    assert caught.value.reason_code is ReasonCode.BAD_LANE_CONFIG


@pytest.mark.parametrize("key", [None, "", "../review", "bad/key", "x" * 65])
def test_attestation_key_uses_the_closed_single_component_grammar(key: object):
    with pytest.raises(AssayError) as caught:
        attestation._validate_key(key)
    assert caught.value.reason_code is ReasonCode.BAD_LANE_CONFIG


def test_declaration_count_bound_is_checked_before_any_attestation_read(tmp_path: Path):
    declarations = tuple(
        EvidenceDeclaration(source="attested", key=f"review-{index}")
        for index in range(attestation.MAX_EVIDENCE_DECLARATIONS + 1)
    )
    with pytest.raises(AssayError, match="evidence declarations exceeds") as caught:
        attestation.load_attested_evidence(
            tmp_path,
            head="a" * 40,
            declared=declarations,
            project_root=tmp_path,
            attestation_dir="attestations",
            remaining=lambda: 10.0,
        )
    assert caught.value.reason_code is ReasonCode.BAD_LANE_CONFIG


def test_aggregate_git_query_limit_refuses_valid_records_but_preserves_absence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    valid_dir = tmp_path / "attestations"
    valid_dir.mkdir()
    paths = [f"src/file-{index}.py" for index in range(700)]
    record = {
        "producer": "reviewer-v1",
        "attested_commit": "a" * 40,
        "reviewed_paths": paths,
    }
    for key in ("first", "second", "third"):
        (valid_dir / f"{key}.json").write_text(json.dumps(record), encoding="utf-8")

    def no_git(*_args, **_kwargs):
        raise AssertionError("the aggregate limit must refuse before any Git query")

    monkeypatch.setattr(attestation.git, "verify_exact_commit", no_git)
    results = attestation.load_attested_evidence(
        tmp_path,
        head="a" * 40,
        declared=tuple(
            EvidenceDeclaration(source="attested", key=key)
            for key in ("first", "missing", "second", "third")
        ),
        project_root=tmp_path,
        attestation_dir="attestations",
        remaining=lambda: 10.0,
    )

    assert [item.status for item in results] == [
        Outcome.ERROR,
        Outcome.NO_MEASUREMENT,
        Outcome.ERROR,
        Outcome.ERROR,
    ]
    assert results[1].reason_code is ReasonCode.MISSING_ATTESTATION
    assert all(
        item.reason_code is ReasonCode.UNREADABLE_ARTIFACT
        for item in (results[0], results[2], results[3])
    )
