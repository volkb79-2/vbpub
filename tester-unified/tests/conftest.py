"""Services the tester-unified image suite needs, and nothing else.

`cmru/` and `libraries/worktree/` come from the lane's `PYTHONPATH` (the lane
judges the worktree's cmru, not the copy baked into the image); this file only
supplies the two pieces of process hygiene the suite relied on cmru's conftest
for while it lived under `cmru/tests/`:

* Git children must not start detached automatic maintenance (the suite runs
  `git ls-files`, `git ls-tree` and a throw-away `git init`/`commit`), and
* the real estate config DECLARES ``${CGROUP_PARENT_DEV_GATES}`` with no
  default, so a test that loads ``cmru.orchestration.toml`` through the strict
  reader exits 2 unless the variable is present. The lane environment forwards
  the host's *placement* slice, which must not decide a test's outcome, so every
  test gets a constant fake value instead.

The test-policy plugin and the descendant reaper are deliberately absent: this
suite declares no ``cli_case`` or ``estate_allow`` markers and defines no
fixtures of its own.
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


@pytest.fixture(scope="session", autouse=True)
def _suite_disables_detached_git_maintenance():
    """Keep every test Git child inside its subprocess lifetime.

    Run-Gate may disable system config for its container copy. Append explicit
    settings to that environment rather than relying on tester-unified's
    system config to keep automatic maintenance foreground-only.
    """
    raw_count = os.environ.get("GIT_CONFIG_COUNT", "0")
    if not raw_count.isdecimal():
        raise RuntimeError("GIT_CONFIG_COUNT is malformed; refusing test Git children")
    count = int(raw_count)
    additions = {
        "GIT_CONFIG_COUNT": str(count + 4),
        f"GIT_CONFIG_KEY_{count}": "maintenance.auto",
        f"GIT_CONFIG_VALUE_{count}": "false",
        f"GIT_CONFIG_KEY_{count + 1}": "maintenance.autoDetach",
        f"GIT_CONFIG_VALUE_{count + 1}": "false",
        f"GIT_CONFIG_KEY_{count + 2}": "gc.auto",
        f"GIT_CONFIG_VALUE_{count + 2}": "0",
        f"GIT_CONFIG_KEY_{count + 3}": "gc.autoDetach",
        f"GIT_CONFIG_VALUE_{count + 3}": "false",
    }
    with preserved_environ():
        os.environ.update(additions)
        yield


@pytest.fixture(autouse=True)
def _suite_fakes_the_gates_cgroup_parent():
    """Hermeticity: the declared ``${CGROUP_PARENT_DEV_GATES}`` gets a fake value.

    Set inside the environment snapshot (not through a per-test ``monkeypatch``)
    so a test's own ``delenv`` is not re-introduced by fixture teardown.
    """
    with preserved_environ():
        os.environ["CGROUP_PARENT_DEV_GATES"] = "test-gates.slice"
        yield
