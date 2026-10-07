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
    # Hermeticity: the real estate config DECLARES ``${CGROUP_PARENT_DEV_GATES}`` with no
    # default. The assay lane runs pytest with ``env_passthrough = ["PATH"]``, so the host's
    # variable is absent there and every test that loads ``cmru.orchestration.toml`` would
    # exit 2. Every test gets a fake value (never the host's); the test proving the
    # declared-config refusal (``test_the_real_estate_config_refuses_without_the_gates_cgroup_parent``)
    # ``delenv``s it. It is set HERE, inside the snapshot, and not through a second
    # ``monkeypatch`` fixture: that would be torn down AFTER this restore and re-introduce
    # values a test's own ``monkeypatch.delenv`` recorded (a leak the zz probe catches).
    with preserved_environ():
        os.environ["CGROUP_PARENT_DEV_GATES"] = "test-gates.slice"
        yield
