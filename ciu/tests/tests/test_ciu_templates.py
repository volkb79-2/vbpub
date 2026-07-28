"""Sanity checks for the demo's committed global template and core sections.

The full defaults template is committed.  A global override is optional and,
when a project authors one, must be a sparse committed template; CIU never
generates it from the defaults (S3.1/S3.1a).
"""
from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
TEST_REPO = REPO_ROOT / "test-repo"


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_ciu_global_defaults_template_exists() -> None:
    defaults_path = TEST_REPO / "ciu.global.defaults.toml.j2"
    assert defaults_path.exists(), "ciu.global.defaults.toml.j2 missing"


def test_demo_does_not_ship_a_duplicate_global_override() -> None:
    """S3.1a: defaults remain the single source until a sparse override is needed."""
    assert not (TEST_REPO / "ciu.global.toml.j2").exists()


def test_ciu_global_defaults_have_core_sections() -> None:
    content = _read_text(TEST_REPO / "ciu.global.defaults.toml.j2")
    for section in (
        "[ciu]",
        "[deploy]",
        "[deploy.env.defaults]",
        "[deploy.env.shared]",
        "[deploy.control]",      # S7.2 string control-flag demo
        "[vault.paths]",         # S4.16 KV path map
        "[deploy.phases.phase_1]",
        "[deploy.profiles.core_infra]",
    ):
        assert section in content, f"Missing section {section} in defaults template"
