"""assay.toml's whole-target inventory must match the shipped modules.

A whole-target floor refuses a target with no executable lines (TARGET_NOT_MEASURED), so a
docstring-only package ``__init__.py`` must NOT be listed, and every module that does have
executable lines must be (otherwise the mutation lane silently skips it).
"""
import ast
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = "src/tnt/__main__.py"


def _has_executable_lines(path: Path) -> bool:
    body = ast.parse(path.read_text(encoding="utf-8")).body
    statements = [
        node for node in body
        if not (isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str))
    ]
    return bool(statements)


def test_assay_targets_are_exactly_the_modules_with_executable_lines():
    config = tomllib.loads((ROOT / "assay.toml").read_text(encoding="utf-8"))
    targets = set(config["lanes"]["tnt"]["judge"]["targets"])
    shipped = {path.relative_to(ROOT).as_posix() for path in (ROOT / "src" / "tnt").rglob("*.py")}
    expected = {rel for rel in shipped if rel != LAUNCHER and _has_executable_lines(ROOT / rel)}

    assert targets == expected, {
        "listed but unmeasurable or missing": sorted(targets - expected),
        "executable but not listed": sorted(expected - targets),
    }
    assert "src/tnt/__init__.py" not in targets and "src/tnt/tui/__init__.py" not in targets
