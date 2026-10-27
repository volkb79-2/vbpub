"""Fixtures for the client tests. The dependency policy is tests/_testdeps.py (one shared helper)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tests"))
import _testdeps  # noqa: E402


@pytest.fixture
def optional_module():
    """``optional_module(name, reason)``: import it, or fail (default) / skip (PWMCP_ALLOW_MISSING_TEST_DEPS=1)."""

    return _testdeps.optional_module
