"""Make ``cmru`` importable from the source tree without installing the package.

Also enables the cli-extended pytest plugin (W2-PKG5, CLI-T3): at collection it loads
``docs/cli-review.toml`` (found through ``[tool.cli-extended]`` in ``pyproject.toml``) and fails
the session unless every active reviewed case links a collected test through
``@pytest.mark.cli_case``. A focused run of a few test files must pass
``--cli-case-partial`` (the full gate and the full suite need no flag).
"""
import contextlib
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

pytest_plugins = ["cli_extended.pytest_plugin"]

#: The process environment when the session started (before any test ran).
SESSION_START_ENVIRON = dict(os.environ)


@contextlib.contextmanager
def preserved_environ():
    """Restore ``os.environ`` exactly as found on exit (W3-PREP).

    The code under test writes ``CMRU_INTERNAL_*`` and ``PYTHONUNBUFFERED`` straight
    into the process environment (``cli.main`` / the runner), which no per-test
    monkeypatch can anticipate; without this, those values leaked into every later
    test in the session.
    """
    before = dict(os.environ)
    try:
        yield
    finally:
        if dict(os.environ) != before:
            os.environ.clear()
            os.environ.update(before)


@pytest.fixture(autouse=True)
def _restore_process_environment():
    with preserved_environ():
        yield
