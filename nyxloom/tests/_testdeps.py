"""Explicit policy for optional dependencies and the external usage browser probe.

Missing optional dependencies fail by default. Developer machines may opt in to
skips with ``NYXLOOM_ALLOW_MISSING_TEST_DEPS=1``. The managed PWMCP service is
outside the tester-unified lane, so its acceptance test has a separate named
opt-in: ``NYXLOOM_RUN_BROWSER_ACCEPTANCE=1``.
"""

from __future__ import annotations

import importlib
import os
import shutil

import pytest


ALLOW_MISSING_VAR = "NYXLOOM_ALLOW_MISSING_TEST_DEPS"
RUN_BROWSER_VAR = "NYXLOOM_RUN_BROWSER_ACCEPTANCE"
BROWSER_ACCEPTANCE_SKIP_REASON = (
    "managed PWMCP browser acceptance runs outside tester-unified; set "
    f"{RUN_BROWSER_VAR}=1 where the managed browser service is reachable"
)


def allow_missing() -> bool:
    """Return whether the developer explicitly opted into missing-dependency skips."""
    return os.environ.get(ALLOW_MISSING_VAR) == "1"


def missing(what: str, reason: str) -> None:
    """Fail for a missing optional dependency unless the developer opted out."""
    if allow_missing():
        pytest.skip(reason)
    pytest.fail(
        f"{what} is unavailable ({reason}); set {ALLOW_MISSING_VAR}=1 to skip locally",
        pytrace=False,
    )


def optional_module(name: str, reason: str):
    """Import an optional test module under the fail-by-default policy."""
    try:
        return importlib.import_module(name)
    except ImportError:
        missing(name, reason)


def require_binary(name: str, reason: str) -> str:
    """Resolve an optional test binary under the fail-by-default policy."""
    path = shutil.which(name)
    if path is None:
        missing(f"the `{name}` binary", reason)
    return path


def require_browser_acceptance() -> None:
    """Skip only when the separate managed-browser acceptance was not requested."""
    if os.environ.get(RUN_BROWSER_VAR) != "1":
        pytest.skip(BROWSER_ACCEPTANCE_SKIP_REASON)
