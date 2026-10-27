"""Optional test dependencies: skip on a developer machine, FAIL in the registered gate.

The gate lanes (assay.toml) set ``PWMCP_REQUIRE_TEST_DEPS=1``. With it, a missing optional
dependency (Pillow, websockets, jinja2, PyYAML: the pwmcp-client ``test`` extra, which the tester
image must carry) is a test FAILURE; without it the test skips with an explicit reason, so a
developer machine lacking them still runs the rest. Use ``optional_module`` instead of
``pytest.importorskip`` (a test below enforces that), so no skip can silently turn the gate green.
A twin of this helper lives in client/tests/conftest.py (the two trees do not share an import path).
"""

from __future__ import annotations

import importlib
import os

import pytest

REQUIRE_VAR = "PWMCP_REQUIRE_TEST_DEPS"
REQUIRED = os.environ.get(REQUIRE_VAR) == "1"


def missing_message(name: str, reason: str) -> str:
    return (
        f"{REQUIRE_VAR}=1 but {name} is not importable ({reason}): the gate environment must carry the "
        "pwmcp-client 'test' extra (pytest, pillow, websockets, jinja2, pyyaml)"
    )


def optional_module(name: str, reason: str):
    try:
        return importlib.import_module(name)
    except ImportError:
        if os.environ.get(REQUIRE_VAR) == "1":
            pytest.fail(missing_message(name, reason), pytrace=False)
        pytest.skip(reason, allow_module_level=True)
