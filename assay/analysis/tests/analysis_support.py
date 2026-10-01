"""Paths the analysis tests share; derived from this file's own location."""

from __future__ import annotations

from pathlib import Path

#: The `assay/` project directory (`analysis/tests/<this file>`).
PROJECT_ROOT = Path(__file__).resolve().parents[2]
assert (PROJECT_ROOT / "pyproject.toml").is_file(), f"no pyproject.toml at {PROJECT_ROOT}"

#: The judge's verdict fixtures (data: `r0_pass.json` and its siblings).
JUDGE_VERDICT_FIXTURES = PROJECT_ROOT / "tests" / "fixtures" / "verdicts"
assert (JUDGE_VERDICT_FIXTURES / "r0_pass.json").is_file()
