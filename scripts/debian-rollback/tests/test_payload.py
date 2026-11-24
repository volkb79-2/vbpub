"""The installed launcher and source-tree shape required by D3/D27."""

from __future__ import annotations

import contextlib
import io
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import _support

_FILES = _support._PROJECT / "files" / "usr" / "local"
LAUNCHER = _FILES / "sbin" / "dr-state"


class _OldPython(tuple):
    """A ``sys.version_info`` stand-in older than the supported minimum."""

    major = property(lambda self: self[0])
    minor = property(lambda self: self[1])


class PayloadTest(unittest.TestCase):
    def test_d27_payload_launcher_and_runtime_tree(self) -> None:
        self.assertEqual(
            LAUNCHER.read_text(encoding="utf-8").splitlines()[0],
            "#!/usr/bin/python3 -I",
        )
        self.assertEqual(stat.S_IMODE(os.stat(LAUNCHER).st_mode), 0o755)
        package = _FILES / "lib" / "dr-state" / "dr_state"
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
            "core",
            "storage/__init__",
            "storage/btrfs",
            "boot/__init__",
            "boot/grub",
        ):
            with self.subTest(module=module):
                self.assertTrue((package / f"{module}.py").is_file())

    def test_launcher_runs_isolated_and_reaches_the_error_translation(self) -> None:
        # With the real dispatcher (P-20b) `list` on an empty sysroot is refused
        # by storage or boot detection, which depends on the host filesystem
        # (ext4, overlay, ...). What is host-independent is the launcher
        # contract: it runs under -I, reaches the implementation and turns the
        # refusal into one `dr-state:` line and a non-zero exit, no traceback.
        with tempfile.TemporaryDirectory() as sysroot:
            result = subprocess.run(
                [sys.executable, "-I", "-B", str(LAUNCHER), "--sysroot", sysroot, "list"],
                capture_output=True,
                text=True,
                check=False,
                timeout=60,
            )
        self.assertIn(result.returncode, (1, 3, 4), result.stderr)
        self.assertEqual(result.stdout, "")
        lines = result.stderr.splitlines()
        self.assertEqual(len(lines), 1, result.stderr)
        self.assertTrue(lines[0].startswith("dr-state: "), result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_launcher_refuses_a_python_older_than_the_minimum(self) -> None:
        code = compile(LAUNCHER.read_text(encoding="utf-8"), str(LAUNCHER), "exec")
        stderr = io.StringIO()
        with (
            mock.patch.object(sys, "version_info", _OldPython((3, 12, 9, "final", 0))),
            contextlib.redirect_stderr(stderr),
            self.assertRaises(SystemExit) as raised,
        ):
            exec(code, {"__file__": str(LAUNCHER), "__name__": "__main__"})
        self.assertEqual(raised.exception.code, 70)
        self.assertIn("requires Python 3.13 or newer (found 3.12)", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
