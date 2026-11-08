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


def test_cli_surface_files_match_the_registered_command():
    report = check_cli_surface(
        build_surface_cli(),
        review_path=PROJECT_ROOT / "docs" / "cli-review.toml",
        manifest_path=PROJECT_ROOT / "docs" / "cli-surface.json",
        spec_path=PROJECT_ROOT / "docs" / "CLI-SPEC.md",
    )
    assert report.passed, [finding.message for finding in report.findings]
