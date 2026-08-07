"""O2 — the registry itself: exactly four formats, an unrecognised declared
format is refused with the SAME error class :mod:`assay.config` would raise
(defence in depth for a caller that skips config loading), and
:func:`assay.coverage.read_coverage_artifact` is the thin file-I/O boundary
around the pure text parser.

Negative: a format missing from :data:`assay.coverage.FORMAT_REGISTRY`, or a
registry that falls back to guessing a format from content instead of
refusing an unknown declared key, lets a bad declaration through.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from assay.coverage import FORMAT_REGISTRY, load_coverage_profile, read_coverage_artifact
from assay.errors import AssayError, LaneConfigError, Outcome, ReasonCode


def test_registry_has_exactly_the_four_formats_this_package_ships():
    # A literal set, not `>= 4` — this is what "removing a registered
    # format" (O1's negative) would actually change.
    assert set(FORMAT_REGISTRY) == {
        "coverage-py-json",
        "lcov",
        "cobertura",
        "go-cover",
    }


def test_unrecognised_declared_format_raises_lane_config_error():
    with pytest.raises(LaneConfigError) as excinfo:
        load_coverage_profile("mode: set\n", declared_format="not-a-real-format")
    assert excinfo.value.outcome is Outcome.ERROR
    assert excinfo.value.reason_code is ReasonCode.BAD_LANE_CONFIG
    assert "not-a-real-format" in str(excinfo.value)


def test_read_coverage_artifact_parses_a_real_file(tmp_path: Path):
    artifact = tmp_path / "cov.json"
    artifact.write_text(
        '{"files": {"a.py": {"executed_lines": [1], "missing_lines": []}}}',
        encoding="utf-8",
    )
    profile = read_coverage_artifact(artifact, declared_format="coverage-py-json")
    assert set(profile.files) == {"a.py"}


def test_read_coverage_artifact_raises_unreadable_artifact_for_a_missing_file(
    tmp_path: Path,
):
    missing = tmp_path / "does-not-exist.json"
    with pytest.raises(AssayError) as excinfo:
        read_coverage_artifact(missing, declared_format="coverage-py-json")
    assert excinfo.value.outcome is Outcome.ERROR
    assert excinfo.value.reason_code is ReasonCode.UNREADABLE_ARTIFACT


def test_read_coverage_artifact_raises_unreadable_artifact_for_undecodable_bytes(
    tmp_path: Path,
):
    artifact = tmp_path / "cov.json"
    artifact.write_bytes(b"\xff\xfe\x00not utf-8 at all")
    with pytest.raises(AssayError) as excinfo:
        read_coverage_artifact(artifact, declared_format="coverage-py-json")
    assert excinfo.value.reason_code is ReasonCode.UNREADABLE_ARTIFACT


def test_read_coverage_artifact_still_cross_checks_the_signature(tmp_path: Path):
    # The file-I/O boundary must not skip the sniff cross-check just because
    # it added an extra step — a file's CONTENT, not its own path/extension,
    # is what the artifact is read from disk to inspect.
    artifact = tmp_path / "cov.json"  # a .json name, but lcov content inside
    artifact.write_text("SF:a.c\nDA:1,1\nend_of_record\n", encoding="utf-8")
    with pytest.raises(AssayError) as excinfo:
        read_coverage_artifact(artifact, declared_format="coverage-py-json")
    assert excinfo.value.reason_code is ReasonCode.FORMAT_MISMATCH
