"""Direct controls for the canonical B106 candidate identity."""

from __future__ import annotations

import hashlib

import pytest

from assay.candidate_identity import candidate_id_from_fields


FIELDS = {
    "path": "src/checks.py",
    "source_sha256": "a" * 64,
    "start_byte": 2,
    "end_byte": 5,
    "mutated_file_sha256": "b" * 64,
    "operator": "python:compare-swap",
}


def test_candidate_identity_hashes_the_ordered_nul_delimited_identity():
    expected_input = b"\0".join(
        (
            b"src/checks.py",
            b"a" * 64,
            b"2",
            b"5",
            b"b" * 64,
            b"python:compare-swap",
        )
    )

    assert candidate_id_from_fields(**FIELDS) == hashlib.sha256(expected_input).hexdigest()


@pytest.mark.parametrize(
    "change",
    [
        {"path": None},
        {"path": ""},
        {"path": "src/checks.py\0suffix"},
        {"operator": 4},
        {"operator": ""},
        {"operator": "python:compare-swap\0suffix"},
        {"source_sha256": "A" * 64},
        {"source_sha256": "a" * 63},
        {"source_sha256": "z" * 64},
        {"mutated_file_sha256": "B" * 64},
        {"mutated_file_sha256": "b" * 63},
        {"mutated_file_sha256": "z" * 64},
        {"start_byte": True},
        {"end_byte": False},
        {"start_byte": -1},
        {"end_byte": 2},
        {"start_byte": "2"},
    ],
)
def test_candidate_identity_refuses_incomplete_or_malformed_inputs(change):
    with pytest.raises(ValueError):
        candidate_id_from_fields(**{**FIELDS, **change})
