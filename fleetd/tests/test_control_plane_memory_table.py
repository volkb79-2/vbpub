"""The per-service reserve table is one fact with two published copies, and a test.

fleetd's per-service fallback (FD-007c) needs a documented floor for every
control-plane service it lists. Those floors are owned by debian-install-v2:
the installer writes them as each unit's ``MemoryMin`` and documents them in
``docs/CONTROL-PLANE-MEMORY.md``. fleetd carries a copy so a host can be planned
without reading the installer, and this module makes drift *fail* instead of
being noticed later by a reviewer:

* the installer's table (parsed from its source, not imported) and fleetd's
  table must be equal, value for value and unit for unit;
* the installer's optional-unit set and fleetd's must be equal;
* the documented markdown table must agree with both, so a doc-only edit goes
  red too.

Parsing is used on purpose. Importing the installer would drag its whole
dependency surface into a planning test; a regex on the source only reads the
one constant this test is about.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from fleetd.contracts import (
    CONTROL_PLANE_SERVICE_MEMORY_MIN_DEFAULTS,
    CONTROL_PLANE_SERVICE_OPTIONAL_UNITS,
    CONTROL_PLANE_SERVICE_UNITS,
)

PROJECT = Path(__file__).resolve().parents[1]
REPO_ROOT = PROJECT.parent
INSTALLER = REPO_ROOT / "scripts" / "debian-install-v2"
INSTALLER_TEMPLATES = INSTALLER / "debian_install_v2" / "templates.py"
DOCUMENTED_TABLE = INSTALLER / "docs" / "CONTROL-PLANE-MEMORY.md"

pytestmark = pytest.mark.skipif(
    not INSTALLER_TEMPLATES.is_file(),
    reason="fleetd is checked out outside the vbpub monorepo, so the installer it must agree with is absent",
)


_BINARY = {
    ast.Add: lambda left, right: left + right,
    ast.Sub: lambda left, right: left - right,
    ast.Mult: lambda left, right: left * right,
}
_UNARY = {ast.USub: lambda value: -value, ast.UAdd: lambda value: value}


def _literal(node: ast.AST) -> object:
    """Evaluate a literal, including the integer arithmetic the table uses."""
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY:
        return _UNARY[type(node.op)](_literal(node.operand))
    if isinstance(node, ast.BinOp) and type(node.op) in _BINARY:
        return _BINARY[type(node.op)](_literal(node.left), _literal(node.right))
    if isinstance(node, (ast.Tuple, ast.List)):
        return tuple(_literal(item) for item in node.elts)
    if isinstance(node, ast.Dict):
        return {_literal(key): _literal(value) for key, value in zip(node.keys, node.values, strict=True)}
    if isinstance(node, ast.Set):
        return frozenset(_literal(item) for item in node.elts)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "frozenset":
        assert len(node.args) == 1 and not node.keywords, ast.dump(node)[:80]
        return frozenset(_literal(node.args[0]))
    raise ValueError(f"unsupported literal: {ast.dump(node)[:80]}")


    raise ValueError(f"unsupported literal: {ast.dump(node)[:80]}")


def _module_constants(path: Path) -> dict[str, object]:
    """Read module-level constants from a source file without importing it."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    values: dict[str, object] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            targets = [target.id for target in node.targets if isinstance(target, ast.Name)]
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            targets = [node.target.id]
        else:
            continue
        for target in targets:
            try:
                values[target] = _literal(node.value)
            except ValueError:
                continue
    return values


def _installer_table() -> dict[str, int]:
    constants = _module_constants(INSTALLER_TEMPLATES)
    table = constants.get("CONTROL_PLANE_MEMORY_MIN")
    assert isinstance(table, dict), "installer CONTROL_PLANE_MEMORY_MIN is not a literal dict"
    result = {}
    for unit, row in table.items():
        assert isinstance(row, tuple) and len(row) == 2, row
        token, expected_bytes = row
        assert isinstance(expected_bytes, int) and expected_bytes > 0, row
        assert re.fullmatch(r"[1-9][0-9]*M", token), row
        # The systemd token and the byte count state the same number (binary MiB).
        assert int(token[:-1]) * 1024**2 == expected_bytes, (unit, row)
        result[unit] = expected_bytes
    return result


def _installer_optional_units() -> frozenset[str]:
    constants = _module_constants(INSTALLER_TEMPLATES)
    optional = constants.get("CONTROL_PLANE_MEMORY_MIN_OPTIONAL_UNITS")
    assert isinstance(optional, frozenset | set), "installer optional-unit set is not a literal set"
    return frozenset(optional)


def _documented_table() -> dict[str, int]:
    text = DOCUMENTED_TABLE.read_text(encoding="utf-8")
    rows = re.findall(r"(?m)^\| `([^`]+)` \| ([0-9]+) MiB \|", text)
    assert rows, "the documented MemoryMin table has no rows"
    return {unit: int(mib) * 1024**2 for unit, mib in rows}


def test_fleetd_per_service_defaults_equal_the_installer_table():
    installer = _installer_table()
    assert set(CONTROL_PLANE_SERVICE_MEMORY_MIN_DEFAULTS) == set(installer)
    for unit, value in installer.items():
        assert CONTROL_PLANE_SERVICE_MEMORY_MIN_DEFAULTS[unit] == value, unit


def test_fleetd_optional_units_equal_the_installer_optional_set():
    assert CONTROL_PLANE_SERVICE_OPTIONAL_UNITS == _installer_optional_units()
    assert CONTROL_PLANE_SERVICE_OPTIONAL_UNITS <= set(CONTROL_PLANE_SERVICE_UNITS)


def test_the_documented_table_agrees_with_the_installer_and_with_fleetd():
    documented = _documented_table()
    assert documented == _installer_table()
    assert documented == CONTROL_PLANE_SERVICE_MEMORY_MIN_DEFAULTS


def test_fleetd_lists_exactly_the_installer_units():
    assert set(CONTROL_PLANE_SERVICE_UNITS) == set(_installer_table())
    assert len(CONTROL_PLANE_SERVICE_UNITS) == len(set(CONTROL_PLANE_SERVICE_UNITS))
