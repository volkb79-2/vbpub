"""The ONE policy for optional test dependencies: a missing one FAILS, skipping is an opt-in.

Operator rule (2026-10-27, estate-wide; the convention is ``pwmcp/tests/_testdeps.py``): a test
that needs an optional module (PyYAML, the wheel toolchain), a sibling project of the monorepo
(cmru, the estate troves, ``run-gate.root.toml``) or a binary must not silently skip, because a
skip can turn a gate green without testing anything — the 24.0.2 candidate gate reported
``2 skipped`` and nobody could say which two. So the default is a clear test FAILURE naming the
missing thing. A checkout that really lacks it (a wheel consumer running the suite outside the
monorepo, a developer venv without PyYAML) opts out with ``RUN_GATE_ALLOW_MISSING_TEST_DEPS=1``,
which restores the clean skip with the same reason.

Two deliberate, documented exceptions remain plain skips, each with an explicit reason, because
the condition is structural rather than a missing dependency:

* ``TestRgNarrowRealContainer`` needs a Docker socket, the ``tester-unified:local`` image and
  ``$CGROUP_PARENT_DEV_GATES``; inside the selftest lane's own container the socket is absent BY
  DESIGN (RG-NARROW: a lane container sees only its worktree). It skips unless
  ``RUN_GATE_REQUIRE_CONTAINER_SMOKE=1`` (set it where Docker is reachable: a developer host).
* ``test_an_unreadable_record_is_reported`` cannot run as root (root reads a 0o000 file).

``tests/test_testdeps_policy.py`` forbids any other bare ``pytest.importorskip`` /
``skipif(... is None)`` / dependency ``pytest.skip`` in this suite, and the selftest lane runs
pytest with ``-rs`` so every remaining skip is named in the gate log.
"""

from __future__ import annotations

import importlib
import os
import shutil

import pytest

ALLOW_VAR = "RUN_GATE_ALLOW_MISSING_TEST_DEPS"
REQUIRE_CONTAINER_SMOKE_VAR = "RUN_GATE_REQUIRE_CONTAINER_SMOKE"


def allow_missing() -> bool:
    """True when the developer opted in to skipping (read at call time, so tests can toggle it)."""

    return os.environ.get(ALLOW_VAR) == "1"


def missing_message(what: str, reason: str) -> str:
    return (
        f"{what} is not available ({reason}). Missing test dependencies FAIL by default: the "
        "tester-unified image carries PyYAML and the pinned wheel toolchain, and the release gate "
        "runs from the full monorepo checkout. On a checkout without it set "
        f"{ALLOW_VAR}=1 to skip these tests instead."
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


def require_container_smoke() -> bool:
    """True when the real-container smoke test must run rather than skip."""

    return os.environ.get(REQUIRE_CONTAINER_SMOKE_VAR) == "1"
