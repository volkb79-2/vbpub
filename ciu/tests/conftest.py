"""Shared pytest fixtures for CIU's test suite.

Autouse env-scrubbing fixture (ciu-P13): this devcontainer's ambient shell
carries identity/policy environment variables left over from an unrelated
checkout (``REPO_ROOT``, ``PHYSICAL_REPO_ROOT``, ``REPO_NAME``,
``INSTANCE_ID``, ``DOCKER_NETWORK_INTERNAL``, ``PUBLIC_FQDN``) plus CIU's own
``CIU_EXIT_ON`` policy variable. Left ambiently set, these can leak into any
test that reads them (directly, or via e.g. ``workspace_env`` machine-identity
resolution or ``warn_policy``'s env fallback), making a test's outcome depend
on the invoking shell rather than on that test's own fixtures/monkeypatches.

Scrub them before every test body runs, via ``monkeypatch`` rather than direct
``os.environ`` mutation: ``monkeypatch.delenv`` auto-restores the ambient value
after each test (including on failure), so nothing leaks across tests the way
a plain ``del os.environ[...]`` would. This fixture is function-scoped
(``monkeypatch`` itself is function-scoped) and autouse, so it runs before
every test body; a test's own ``monkeypatch.setenv``/``monkeypatch.delenv``
calls compose normally afterward, in fixture-then-test-body order.
"""
from __future__ import annotations

import pytest

_AMBIENT_ENV_VARS = (
    "REPO_ROOT",
    "PHYSICAL_REPO_ROOT",
    "REPO_NAME",
    "INSTANCE_ID",
    "DOCKER_NETWORK_INTERNAL",
    "PUBLIC_FQDN",
    "CIU_EXIT_ON",
)


@pytest.fixture(autouse=True)
def _scrub_ambient_identity_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Clear ambient identity/policy env vars before each test body runs."""
    for name in _AMBIENT_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
