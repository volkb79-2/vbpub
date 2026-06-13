"""
Sanity checks for the demo's committed global template and its core sections.

Only the *defaults* template (``ciu.global.defaults.toml.j2``) is committed; the
override ``ciu.global.toml.j2`` is gitignored and auto-created from the defaults
on first run (S3.1), so it is NOT asserted to pre-exist in a clean checkout.
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
