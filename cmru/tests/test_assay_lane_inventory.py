"""Whole-target Assay lanes list exactly measurable CMRU source modules."""
from __future__ import annotations

import ast
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIRST_RELEASE_LANES = ("cmru-first-release", "cmru-first-release-r2")


def _has_executable_lines(path: Path) -> bool:
    body = ast.parse(path.read_text(encoding="utf-8")).body
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        body = body[1:]
    return bool(body)


def test_first_release_targets_are_exactly_the_executable_source_modules():
    assay = tomllib.loads((ROOT / "assay.toml").read_text(encoding="utf-8"))
    shipped = {
        path.relative_to(ROOT).as_posix()
        for path in (ROOT / "src" / "cmru").rglob("*.py")
        if path.is_file() and _has_executable_lines(path)
    }

    for lane_name in FIRST_RELEASE_LANES:
        targets = assay["lanes"][lane_name]["judge"]["targets"]
        assert len(targets) == len(set(targets)), f"duplicate targets in {lane_name}"
        assert set(targets) == shipped, {
            "lane": lane_name,
            "listed but unmeasurable or missing": sorted(set(targets) - shipped),
            "executable but not listed": sorted(shipped - set(targets)),
        }
        assert "src/cmru/__init__.py" not in targets
