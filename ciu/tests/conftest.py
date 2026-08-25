"""Shared pytest fixtures for CIU's test suite.

Autouse env-scrubbing fixture (ciu-P13, CIU-57): this devcontainer's ambient
shell carries identity/policy environment variables left over from an
unrelated checkout (``REPO_ROOT``, ``PHYSICAL_REPO_ROOT``, ``REPO_NAME``,
``INSTANCE_ID``, ``DOCKER_NETWORK_INTERNAL``, ``PUBLIC_FQDN``) plus CIU's own
``CIU_EXIT_ON``/``CIU_KSM`` policy variables. Left ambiently set, these can
leak into any test that reads them (directly, or via e.g. ``workspace_env``
machine-identity resolution, ``warn_policy``'s env fallback, or
``governance.resolve_ksm_optin``'s ambient override), making a test's outcome
depend on the invoking shell OR on whichever earlier test in the same xdist
worker process last set the variable — rather than on that test's own
fixtures/monkeypatches.

``CIU_KSM`` (CIU-57) was missing from this list despite being a previously
hunted flake source (see CHANGES.md's own history of one-off ``CIU_KSM=off``
pins scattered across individual fixtures) — those were local patches on
individual flakes, not a fix of this fixture's actual coverage. Confirmed
live: ``test_absolute_governance_ksm_path_is_preserved_in_overlay``
(``test_ciu_composefile_branch109.py``) intermittently failed with
``KeyError: 'volumes'`` because ``resolve_ksm_optin`` reads ``CIU_KSM`` fresh
on every call and the test never pins it — a leaked ambient/leftover value
from another test in the same worker silently changed its outcome.

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
    "CIU_KSM",
)


@pytest.fixture(autouse=True)
def _scrub_ambient_identity_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Clear ambient identity/policy env vars before each test body runs."""
    for name in _AMBIENT_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
