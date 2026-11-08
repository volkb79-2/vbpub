"""Guard the package layout and Assay inventory against drift."""

from __future__ import annotations

import tomllib
from pathlib import Path

from cli_extended.review import check_cli_surface

from augeaswrap.cli import build_surface_cli

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = PROJECT_ROOT / "src" / "augeaswrap"


def test_assay_targets_cover_every_package_module():
    config = tomllib.loads((PROJECT_ROOT / "assay.toml").read_text(encoding="utf-8"))
    targets = config["lanes"]["augeaswrap"]["judge"]["targets"]
    modules = sorted(
        path.relative_to(PROJECT_ROOT).as_posix()
        for path in PACKAGE_ROOT.rglob("*.py")
    )
    assert targets == modules


def _surface_report(spec_path: Path):
    return check_cli_surface(
        build_surface_cli(),
        review_path=PROJECT_ROOT / "docs" / "cli-review.toml",
        manifest_path=PROJECT_ROOT / "docs" / "cli-surface.json",
        spec_path=spec_path,
    )


def _failure_message(report) -> str:
    # Findings are plain strings; format them as such so a real drift shows its text.
    return "; ".join(report.findings)


def test_cli_surface_files_match_the_registered_command():
    report = _surface_report(PROJECT_ROOT / "docs" / "CLI-SPEC.md")
    assert report.passed, _failure_message(report)


def test_cli_surface_drift_is_reported_with_its_message(tmp_path):
    spec = (PROJECT_ROOT / "docs" / "CLI-SPEC.md").read_text(encoding="utf-8")
    assert "cgprofiled install 3.0.0" in spec
    drifted = tmp_path / "CLI-SPEC.md"
    drifted.write_text(spec.replace("cgprofiled install 3.0.0", "cgpd install 2.3.0"), encoding="utf-8")

    report = _surface_report(drifted)

    assert not report.passed
    assert "generated CLI spec block is stale" in _failure_message(report)
