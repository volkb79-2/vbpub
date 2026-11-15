"""The Rust adapter normalizes the measured stable LCOV artifact at R1 input."""

from __future__ import annotations

import pytest
from conftest import PROJECT_ROOT

from assay.adapters.rust import RustAdapter
from assay.coverage import check_empty_coverage, derive_branch_capability, parse_coverage_artifact
from assay.errors import AssayError, ReasonCode


def test_real_r0_artifact_parses_through_the_rust_adapter_as_branch_unavailable():
    artifact = PROJECT_ROOT / "docs/rust-r0/evidence/s2-sampler-lcov.info"
    profile = parse_coverage_artifact(
        artifact.read_bytes(),
        declared_format="lcov",
        normalizer=RustAdapter().normalize_coverage_text,
    )

    check_empty_coverage(profile)
    assert len(profile.files) == 13
    assert derive_branch_capability(profile) == "unavailable"
    assert all(file_coverage.branches is None for file_coverage in profile.files.values())


def test_empty_lcov_is_a_format_refusal_not_an_empty_pass():
    with pytest.raises(AssayError) as excinfo:
        parse_coverage_artifact(
            b"",
            declared_format="lcov",
            normalizer=RustAdapter().normalize_coverage_text,
        )

    assert excinfo.value.reason_code is ReasonCode.FORMAT_MISMATCH
