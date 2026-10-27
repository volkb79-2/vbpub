"""Shared fixtures for pwmcp's root-level tests. All dependency policy lives in _testdeps.py."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from _testdeps import REQUIRE_TESSERACT_VAR, optional_module, require_binary

DEJAVU_MONO = Path("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf")


@pytest.fixture
def require_pillow() -> None:
    optional_module("PIL", "Pillow decodes and builds the images the sidecar tests use")


@pytest.fixture
def require_node() -> str:
    return require_binary("node", "Node.js runs the gateway / ua-override code under test")


@pytest.fixture
def require_sh() -> str:
    return require_binary("sh", "sh runs the wrapper scripts under test")


@pytest.fixture
def real_tesseract() -> None:
    """The real-engine test: an explicit skip outside the sidecar image, a FAILURE when it is required.

    Unlike the other optional dependencies, the ``tesseract`` binary legitimately exists only in the OCR
    image, so by default its absence is a skip with the reason spelled out. ``PWMCP_REQUIRE_TESSERACT=1``
    turns that into a failure; set it only in a lane that runs inside the OCR image (none exists today:
    the registered gate runs in the tester image, which has no tesseract).
    """

    optional_module("PIL", "Pillow renders the console text for the real-engine test")
    problems = []
    if shutil.which("tesseract") is None:
        problems.append("the `tesseract` binary is not on PATH (it is installed only in the sidecar image)")
    if not DEJAVU_MONO.is_file():
        problems.append(f"DejaVu Sans Mono is missing at {DEJAVU_MONO}")
    if problems:
        reason = "real-Tesseract test: " + "; ".join(problems)
        if os.environ.get(REQUIRE_TESSERACT_VAR) == "1":
            pytest.fail(f"{REQUIRE_TESSERACT_VAR}=1 but {reason}", pytrace=False)
        pytest.skip(f"{reason} (set {REQUIRE_TESSERACT_VAR}=1 inside the OCR image to make this a failure)")
