"""Bounded coverage for public hook-module validation."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu.hooks_runner import load_hook  # noqa: E402


def test_load_hook_rejects_unloadable_or_unsupported_modules(
    tmp_path: Path,
) -> None:
    """Unsupported hook modules produce the documented typed errors."""
    unsupported_suffix = tmp_path / "hook.txt"
    unsupported_suffix.write_text("run = lambda config, ctx: {}\n")
    with pytest.raises(ImportError, match="Cannot create module spec"):
        load_hook(unsupported_suffix)

    unsupported_module = tmp_path / "hook.py"
    unsupported_module.write_text("VALUE = 1\n")
    with pytest.raises(AttributeError, match="does not define a 'run'"):
        load_hook(unsupported_module)
