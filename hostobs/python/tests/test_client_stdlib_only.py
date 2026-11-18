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
        self.assertGreaterEqual(len(files), 4)
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


if __name__ == "__main__":
    unittest.main()
