#!/usr/bin/env python3
"""`hostobs_client` imports nothing outside the standard library and never reads `__file__`.

    python3 -I -m unittest discover -s python/tests -p 'test_client_stdlib_only.py' -v
"""

from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1] / "hostobs_client"
ALLOWED = set(sys.stdlib_module_names) | {"hostobs_client"}


class StdlibOnly(unittest.TestCase):
    def test_every_import_is_standard_library_or_the_package_itself(self):
        files = sorted(PACKAGE.glob("*.py"))
        names = {path.stem for path in files}
        self.assertTrue({"conn", "envelope", "profiling", "mark", "wire", "stream", "rates"} <= names)
        for path in files:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    roots = [alias.name.split(".")[0] for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    if node.level:
                        continue  # a relative import stays inside the package
                    roots = [(node.module or "").split(".")[0]]
                else:
                    continue
                for root in roots:
                    self.assertIn(root, ALLOWED, f"{path.name}: import of {root}")

    def test_no_module_reads_file_or_uses_the_filesystem_path_of_itself(self):
        for path in sorted(PACKAGE.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Name) and node.id == "__file__":
                    self.fail(f"{path.name} reads __file__ (zipimport-unsafe)")


    def test_no_threads_and_no_clock_outside_a_default_argument(self) -> None:
        """Every time source is the injected clock; ``time.monotonic_ns`` appears only as a default."""
        forbidden_modules = {"threading", "_thread", "asyncio", "select", "selectors", "signal"}
        allowed_time = {"monotonic_ns"}
        for path in sorted(PACKAGE.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            defaults = set()
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    for default in node.args.defaults + [d for d in node.args.kw_defaults if d is not None]:
                        defaults.update(id(inner) for inner in ast.walk(default))
            for node in ast.walk(tree):
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    modules = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""]
                    for module in modules:
                        self.assertNotIn(module.split(".")[0], forbidden_modules, f"{path.name} imports {module}")
                    if isinstance(node, ast.ImportFrom) and node.module == "time":
                        self.fail(f"{path.name}: import names from time (use the injected clock)")
                if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "time":
                    self.assertIn(node.attr, allowed_time, f"{path.name}: time.{node.attr}")
                    self.assertIn(id(node), defaults, f"{path.name}: time.{node.attr} outside a default argument")


if __name__ == "__main__":
    unittest.main()
