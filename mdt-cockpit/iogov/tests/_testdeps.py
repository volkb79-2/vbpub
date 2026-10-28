"""The ONE policy for optional test dependencies: a missing one FAILS, skipping is an opt-in (same rule as pwmcp/tests/_testdeps.py).

A test that needs a module or binary beyond pytest and the stdlib (e.g. `tomllib`, which needs python >= 3.11, or a tool such
as `fio` for an optional cross-check) must call `optional_module` / `require_binary` here. A missing one is a clear FAILURE; a
developer machine that really lacks it sets IOGOV_ALLOW_MISSING_TEST_DEPS=1 to get a skip instead. Bare `pytest.importorskip`
and `skipif(... is None)` are forbidden (see test_testdeps.py), because a skip can turn a gate green without testing anything.
"""

from __future__ import annotations

import importlib
import os
import shutil

import pytest

ALLOW_VAR = "IOGOV_ALLOW_MISSING_TEST_DEPS"


def allow_missing() -> bool:
    return os.environ.get(ALLOW_VAR) == "1"


def missing(what: str, reason: str) -> None:
    if allow_missing():
        pytest.skip(reason)
    pytest.fail(f"{what} is not available ({reason}). Missing test dependencies FAIL by default; "
                f"set {ALLOW_VAR}=1 on a developer machine to skip instead.", pytrace=False)


def optional_module(name: str, reason: str):
    try:
        return importlib.import_module(name)
    except ImportError:
        missing(name, reason)


def require_binary(name: str, reason: str) -> str:
    path = shutil.which(name)
    if path is None:
        missing(f"the `{name}` binary", reason)
    return path
