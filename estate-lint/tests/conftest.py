"""estate-lint's test services.

The estate checks load `cmru.orchestration.toml` through cmru's strict reader,
which DECLARES ``${CGROUP_PARENT_DEV_GATES}`` with no default: unset, the reader
exits 2. The lane environment forwards the host's *placement* slice, which must
not decide a test's outcome, so every test gets a constant fake value instead. Set inside the environment snapshot so a
test's own ``delenv`` is not re-introduced by fixture teardown.
"""
import contextlib
import os

import pytest


@contextlib.contextmanager
def preserved_environ():
    """Restore ``os.environ`` exactly as found on exit."""
    before = dict(os.environ)
    try:
        yield
    finally:
        if dict(os.environ) != before:
            os.environ.clear()
            os.environ.update(before)


@pytest.fixture(autouse=True)
def _estate_fakes_the_gates_cgroup_parent():
    with preserved_environ():
        os.environ["CGROUP_PARENT_DEV_GATES"] = "test-gates.slice"
        yield
