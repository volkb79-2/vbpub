"""The installed launcher and source-tree shape required by D3/D27."""

from __future__ import annotations

import os
import stat
import unittest

import _support


class PayloadTest(unittest.TestCase):
    def test_d27_payload_launcher_and_runtime_tree(self) -> None:
        launcher = (
            _support._PROJECT
            / "files"
            / "usr"
            / "local"
            / "sbin"
            / "dr-state"
        )
        text = launcher.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("#!/usr/bin/python3 -I\n"))
        self.assertIn('parent.parent / "lib" / "dr-state"', text)
        self.assertIn("from dr_state.cli import main", text)
        self.assertEqual(stat.S_IMODE(os.stat(launcher).st_mode), 0o755)
        package = (
            _support._PROJECT
            / "files"
            / "usr"
            / "local"
            / "lib"
            / "dr-state"
            / "dr_state"
        )
        for module in (
            "__init__",
            "paths",
            "errors",
            "lock",
            "ids",
            "metadata",
            "apt",
            "retention",
            "cli",
        ):
            with self.subTest(module=module):
                self.assertTrue((package / f"{module}.py").is_file())


if __name__ == "__main__":
    unittest.main()
