"""Fixtures for the client tests: optional dependencies skip locally but FAIL in the gate."""

from __future__ import annotations

import importlib
import os

import pytest


@pytest.fixture
def optional_module():
    """Return ``import_it(name, reason)``: skip when missing, unless PWMCP_REQUIRE_TEST_DEPS=1 (the gate).

    Twin of tests/_testdeps.optional_module (the two test trees share no import path).
    """

    def import_it(name: str, reason: str):
        try:
            return importlib.import_module(name)
        except ImportError:
            if os.environ.get("PWMCP_REQUIRE_TEST_DEPS") == "1":
                pytest.fail(
                    f"PWMCP_REQUIRE_TEST_DEPS=1 but {name} is not importable ({reason}): the gate environment "
                    "must carry the pwmcp-client 'test' extra (pytest, pillow, websockets, jinja2, pyyaml)",
                    pytrace=False,
                )
            pytest.skip(reason)

    return import_it
