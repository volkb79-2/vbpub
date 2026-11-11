from __future__ import annotations

from pathlib import Path

import pytest

from assay.coverage import derive_branch_capability, load_coverage_profile
from assay.coverage_parsers.rust_lcov import normalize_stable_rust_branch_markers
from assay.errors import AssayError


def test_real_r0_cargo_llvm_cov_fixture_normalizes_to_branch_unavailable():
    root = Path(__file__).resolve().parents[3]
    fixture = root / "docs/rust-r0/evidence/s2-sampler-lcov.info"
    raw = fixture.read_text(encoding="utf-8")

    assert raw.count("SF:") == 13
    assert "BRDA:" not in raw
    assert raw.count("BRF:0") == 13
    assert raw.count("BRH:0") == 13

    normalized = normalize_stable_rust_branch_markers(raw)
    assert "BRF:" not in normalized
    assert "BRH:" not in normalized
    profile = load_coverage_profile(normalized, declared_format="lcov")
    assert len(profile.files) == 13
    assert derive_branch_capability(profile) == "unavailable"
    assert all(record.branches is None for record in profile.files.values())


def test_real_branch_records_are_preserved_byte_for_byte():
    raw = "SF:src/lib.rs\nDA:1,1\nBRDA:1,0,0,1\nBRF:1\nBRH:1\nend_of_record\n"

    assert normalize_stable_rust_branch_markers(raw) == raw
    profile = load_coverage_profile(raw, declared_format="lcov")
    assert derive_branch_capability(profile) == "reported"
    assert profile.files["src/lib.rs"].branches.by_line == {1: (1, 1)}


@pytest.mark.parametrize(
    "raw",
    [
        "SF:src/lib.rs\nDA:1,1\nBRF:1\nBRH:0\nend_of_record\n",
        "SF:src/lib.rs\nDA:1,1\nBRF:0\nend_of_record\n",
        "BRF:0\nBRH:0\n",
    ],
)
def test_nonzero_unpaired_or_out_of_record_totals_are_not_normalized(raw: str):
    assert normalize_stable_rust_branch_markers(raw) == raw


def test_nonzero_marker_without_branch_records_remains_a_parser_error():
    raw = "SF:src/lib.rs\nDA:1,1\nBRF:1\nBRH:0\nend_of_record\n"
    normalized = normalize_stable_rust_branch_markers(raw)

    with pytest.raises(AssayError, match="BRF:1 does not match the derived branch total"):
        load_coverage_profile(normalized, declared_format="lcov")
