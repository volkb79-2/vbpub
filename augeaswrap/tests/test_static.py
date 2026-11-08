"""Static guard: an undefined global name in the package fails R0/R1.

100% line and branch coverage cannot see the untaken arm of a conditional
expression, which is how ``apply()`` shipped a ``NameError``. The tester image has
neither ruff nor pyflakes, so this is a small stdlib check built on ``symtable``: every
name a scope resolves to a global must be defined at module level (assigned, imported,
a def/class, or assigned through ``global``) or be a builtin.
"""

from __future__ import annotations

import builtins
import symtable
from pathlib import Path

import pytest

PACKAGE = Path(__file__).resolve().parents[1] / "src" / "augeaswrap"
_MODULE_DUNDERS = {"__file__", "__name__", "__doc__", "__package__", "__spec__", "__loader__", "__path__", "__builtins__"}


def _tables(table: symtable.SymbolTable):
    yield table
    for child in table.get_children():
        yield from _tables(child)


def undefined_globals(source: str, filename: str = "<source>") -> list[str]:
    """Return ``name`` entries (sorted, unique) that resolve to no module-level definition."""

    top = symtable.symtable(source, filename, "exec")
    defined = set(_MODULE_DUNDERS) | set(dir(builtins))
    for table in _tables(top):
        for symbol in table.get_symbols():
            if table is top:
                if symbol.is_assigned() or symbol.is_imported() or symbol.is_namespace():
                    defined.add(symbol.get_name())
            elif symbol.is_declared_global() and symbol.is_assigned():
                defined.add(symbol.get_name())
    missing: set[str] = set()
    for table in _tables(top):
        for symbol in table.get_symbols():
            name = symbol.get_name()
            if name.startswith(".") or name.startswith("__") and name.endswith("__"):
                continue
            if symbol.is_referenced() and symbol.is_global() and name not in defined:
                missing.add(name)
    return sorted(missing)


def test_checker_reports_an_undefined_name_in_an_untaken_branch():
    source = (
        "from os import path\n"
        "def apply(planned):\n"
        "    return planned if isinstance(planned, int) else plan(planned)\n"
        "def other():\n"
        "    return [path for _ in range(2) if missing_name]\n"
    )
    assert undefined_globals(source) == ["missing_name", "plan"]


def test_checker_accepts_module_names_builtins_and_global_assignments():
    source = (
        "import os\n"
        "try:\n"
        "    import tomllib\n"
        "except ImportError:\n"
        "    tomllib = None\n"
        "def later(): return os, tomllib, len, __file__\n"
        "class Box:\n"
        "    attr = later\n"
        "    def method(self):\n"
        "        global cache\n"
        "        cache = later()\n"
        "        return cache, __class__\n"
        "def reader(): return cache\n"
        "from missing_module_ok import tail_name\n"
        "def tail(): return tail_name\n"
    )
    assert undefined_globals(source) == []


@pytest.mark.parametrize("path", sorted(PACKAGE.glob("*.py")), ids=lambda path: path.name)
def test_package_modules_reference_only_defined_global_names(path):
    assert undefined_globals(path.read_text(encoding="utf-8"), str(path)) == []
