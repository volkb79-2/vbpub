"""Shared import path and temporary-root helpers for the unit tests."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

_PROJECT = Path(__file__).resolve().parents[1]
_LIBRARY = _PROJECT / "files" / "usr" / "local" / "lib" / "dr-state"
if str(_LIBRARY) not in sys.path:
    sys.path.insert(0, str(_LIBRARY))

from dr_state.paths import Paths


class TemporaryRootTest(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary.name)
        self.paths = Paths(self.root)
    def tearDown(self) -> None:
        self._temporary.cleanup()
    def make_control_dir(self) -> None:
        self.paths.metadata_dir.mkdir(parents=True, exist_ok=True)
        self.paths.control_dir.chmod(0o700)
        self.paths.metadata_dir.chmod(0o700)
    def isolate_lock_path(self) -> None:
        self.paths.lock_file.parent.mkdir(parents=True, exist_ok=True)
