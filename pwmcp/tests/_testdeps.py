"""The ONE policy for optional test dependencies: a missing one FAILS, skipping is an opt-in.

Operator decision (2026-10-27): a test that needs Pillow, websockets, jinja2, PyYAML, node, sh (or any
other optional module or binary) must not silently skip, because a skip can turn a gate green without
testing anything. So the default is a clear test FAILURE naming the missing thing and the tester image's
``pwmcp-client`` ``test`` extra. A developer machine that really lacks the dependency opts out with
``PWMCP_ALLOW_MISSING_TEST_DEPS=1``, which restores the clean skip.

Every site uses this module (tests/ imports it; client/tests/conftest.py puts tests/ on sys.path and
re-exports it as a fixture), and a test forbids bare ``pytest.importorskip`` / ``skipif(... is None)``.
The one deliberate exception is the real-Tesseract test (see ``tests/conftest.py::real_tesseract``): the
``tesseract`` binary legitimately exists only in the sidecar image, so it skips with an explicit reason
unless ``PWMCP_REQUIRE_TESSERACT=1`` (set it only where the OCR image's tesseract is present).
"""

from __future__ import annotations

import importlib
import os
import shutil

import pytest

ALLOW_VAR = "PWMCP_ALLOW_MISSING_TEST_DEPS"
REQUIRE_TESSERACT_VAR = "PWMCP_REQUIRE_TESSERACT"


def allow_missing() -> bool:
    """True when the developer opted in to skipping (read at call time, so tests can toggle it)."""

    return os.environ.get(ALLOW_VAR) == "1"


def missing_message(what: str, reason: str) -> str:
    return (
        f"{what} is not available ({reason}). Missing test dependencies FAIL by default: the tester image "
        "must carry the pwmcp-client 'test' extra (pytest, pillow, websockets, jinja2, pyyaml) plus node and sh. "
        f"On a developer machine without it set {ALLOW_VAR}=1 to skip these tests instead."
    )


def missing(what: str, reason: str, *, module_level: bool = False) -> None:
    """Fail (default) or skip (opt-in) because ``what`` is missing; never returns."""

    if allow_missing():
        pytest.skip(reason, allow_module_level=module_level)
    pytest.fail(missing_message(what, reason), pytrace=False)


def optional_module(name: str, reason: str, *, module_level: bool = False):
    """Import ``name`` or fail/skip per the policy above (use ``module_level=True`` at import time)."""

    try:
        return importlib.import_module(name)
    except ImportError:
        missing(name, reason, module_level=module_level)


def require_binary(name: str, reason: str) -> str:
    """The path of ``name`` on PATH, or fail/skip per the policy above."""

    path = shutil.which(name)
    if path is None:
        missing(f"the `{name}` binary", reason)
    return path
