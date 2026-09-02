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

Docker-network side-effect containment (ciu-P48, CIU-87)
--------------------------------------------------------
Two mechanisms, both defined below:

1. ``CIU_TEST_SUITE=1`` — the product-side gate. Set at conftest IMPORT time
   (so collection-time code and any ``ciu`` subprocess a test spawns inherit
   it) and re-asserted per test through ``monkeypatch`` (so a test that
   mutates it is restored afterwards, failures included).
2. ``_track_real_docker_networks`` — the autouse teardown net. It records the
   networks a test's own ``_ensure_network_exists``/
   ``_connect_devcontainer_to_network`` calls really created/joined, and
   releases exactly those. It is deliberately NOT a
   ``docker network ls --filter name=...`` sweep: this devcontainer is a
   SHARED host and other sessions have legitimate, concurrent, not-yet-torn-
   down networks of their own on it. A test's teardown may only ever touch
   what that same test created.

A test that must exercise the real gated path asks for the
``real_network_side_effects`` fixture, which lifts mechanism 1 for that test
only and leaves mechanism 2 armed.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Callable, Optional

import pytest

# CIU-87. Captured at import time, before any test can `monkeypatch.setattr`
# `subprocess.run` (which several boundary tests do, on the shared module
# object). Teardown must reach the real daemon while a test's fake is
# installed, and must not append to a fake's recorded-argv list and break that
# test's own assertions.
_REAL_SUBPROCESS_RUN = subprocess.run

CIU_TEST_SUITE_ENV = "CIU_TEST_SUITE"

# Mechanism 1, at import time: collection-time module bodies and any `ciu`
# subprocess a test spawns both read os.environ, and neither sees a fixture.
os.environ[CIU_TEST_SUITE_ENV] = "1"

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


@pytest.fixture(autouse=True)
def _ciu_test_suite_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    """Re-assert the CIU-87 product-side gate for every test body.

    The import-time assignment above already covers collection and spawned
    subprocesses; this restores the value after any test that changes it —
    including ``real_network_side_effects``, which deletes it on purpose.
    """
    monkeypatch.setenv(CIU_TEST_SUITE_ENV, "1")


Runner = Callable[..., "subprocess.CompletedProcess[str]"]


def docker_cli_available() -> bool:
    """True when a ``docker`` client binary is on PATH (CIU-87).

    Teardown never *needs* Docker: a suite run on a host without the client
    cannot have created a network in the first place.
    """
    return shutil.which("docker") is not None


def run_docker(argv: list[str], *, runner: Optional[Runner] = None):
    """Run ``docker <argv>`` for teardown purposes, never raising (CIU-87).

    ``runner`` is the injection seam the unit tests drive; production teardown
    uses the import-time-captured real :func:`subprocess.run`.
    """
    real = runner or _REAL_SUBPROCESS_RUN
    try:
        return real(
            ["docker", *argv], capture_output=True, text=True, check=False
        )
    except OSError:  # docker vanished mid-run; nothing to clean up by hand
        return subprocess.CompletedProcess(["docker", *argv], 1, "", "")


def network_exists(name: str, *, runner: Optional[Runner] = None) -> bool:
    """Whether Docker currently knows a network called *name* (CIU-87)."""
    return run_docker(["network", "inspect", name], runner=runner).returncode == 0


def network_has_container(
    name: str, container: str, *, runner: Optional[Runner] = None
) -> bool:
    """Whether *container* is currently attached to network *name* (CIU-87).

    The membership half of the tracker's "did THIS call cause it?" rule. An
    unnamed cockpit, an absent network and an unreachable daemon all answer
    False — teardown may act only on a membership it positively observed
    appearing, never on one it merely failed to rule out.
    """
    if not container:
        return False
    probe = run_docker(
        [
            "network", "inspect", name,
            "--format", "{{range .Containers}}{{.Name}} {{end}}",
        ],
        runner=runner,
    )
    return probe.returncode == 0 and container in probe.stdout.split()


def release_test_network(
    name: str,
    container: str,
    *,
    remove: bool,
    runner: Optional[Runner] = None,
) -> list[list[str]]:
    """Undo one test's Docker-network side effects (CIU-87).

    Mirrors the order the product's own teardown uses (``deploy.py`` /
    ``worktree.py``): DISCONNECT the cockpit first, then remove the network —
    the daemon refuses to remove a network a container is still joined to,
    which is exactly why the leak was permanent. Both steps tolerate "already
    gone"; *remove* is False for a network the test merely joined and did not
    create, so a pre-existing network is never destroyed.

    Returns the argv actually issued (for the unit tests that pin the order).
    """
    issued: list[list[str]] = []
    if container:
        issued.append(["network", "disconnect", "-f", name, container])
        run_docker(issued[-1], runner=runner)
    if remove:
        issued.append(["network", "rm", name])
        run_docker(issued[-1], runner=runner)
    return issued


class NetworkSideEffectTracker:
    """Ledger of the Docker networks ONE test really created and joined.

    Kept as a plain object rather than fixture-local closures so its rules are
    directly assertable (``tests/tests/test_ciu87_network_side_effect_gate.py``
    drives it with fakes): "register a side effect only when THIS call is what
    caused it" is the whole difference between a surgical teardown and a
    blanket sweep that would eat a co-tenant's live network.

    Both ledgers obey that rule symmetrically, via a before/after observation
    of the daemon: ``created`` needs the network to have come into existence
    across the call, ``joined`` needs the cockpit's membership to have
    appeared across it. The membership half was NOT observation-gated in the
    first cut of this fixture (ciu-P48 review B1) — it recorded every name the
    product was ASKED to connect, so a boundary test that fully mocked
    ``subprocess.run`` and merely NAMED a network still had teardown issue a
    real ``docker network disconnect -f`` against the live daemon. That is the
    precise shared-host hazard this whole fixture exists to prevent, reached
    from the inside.
    """

    def __init__(
        self,
        *,
        exists: Callable[[str], bool],
        attached: Callable[[str], bool],
        suppressed: Callable[[], bool],
    ) -> None:
        self._exists = exists
        self._attached = attached
        self._suppressed = suppressed
        self.created: list[str] = []
        self.joined: list[str] = []

    def wrap_ensure(self, original: Callable[[str], None]) -> Callable[[str], None]:
        def _ensure(network_name: str) -> None:
            if self._suppressed():
                return original(network_name)
            existed = self._exists(network_name)
            try:
                return original(network_name)
            finally:
                # A network that was already there belongs to whoever made it —
                # possibly another agent's live work on this shared host.
                if not existed and self._exists(network_name):
                    self.created.append(network_name)

        return _ensure

    def wrap_connect(self, original: Callable[[str], None]) -> Callable[[str], None]:
        def _connect(network_name: str) -> None:
            if self._suppressed():
                return original(network_name)
            attached = self._attached(network_name)
            try:
                return original(network_name)
            finally:
                # Same discipline as `wrap_ensure`, for the same reason: a
                # membership that did not appear across this call is not this
                # test's to undo. A pre-existing attachment belongs to whoever
                # made it, and a mocked seam creates no attachment at all.
                if not attached and self._attached(network_name):
                    self.joined.append(network_name)

        return _connect

    def release(
        self,
        container: str,
        *,
        releaser: Callable[..., object] = release_test_network,
    ) -> list[str]:
        """Undo the ledger. Networks merely JOINED are only disconnected;
        only networks this test CREATED are also removed."""
        names = list(dict.fromkeys([*self.joined, *self.created]))
        for name in names:
            releaser(name, container, remove=name in self.created)
        return names


@pytest.fixture(autouse=True)
def _track_real_docker_networks(monkeypatch: pytest.MonkeyPatch):
    """Release exactly the networks THIS test really created/joined (CIU-87).

    A yield-fixture, not an end-of-test call: the teardown half runs even when
    the test body raises, which is the failure mode a plain cleanup call in the
    test body would skip.

    The wrappers are pass-throughs while mechanism 1's gate is active (or on a
    host with no docker client), since the product code cannot then reach the
    daemon at all — the tracking cost is paid only by a test that deliberately
    opted out.

    Yields the tracker, so a test can assert on the ledger the teardown is
    about to act on (that is how the B1 regression above is pinned).

    The cockpit name is resolved ONCE here, before the test body can rewrite
    ``DEVCONTAINER_NAME``/``HOSTNAME``. A boundary test that sets a fictional
    cockpit name must not make the membership probe ask about a container that
    does not exist, and teardown must disconnect the same container the probe
    watched.
    """
    from ciu import workspace_env

    cockpit = workspace_env.detect_devcontainer_name()
    tracker = NetworkSideEffectTracker(
        exists=network_exists,
        attached=lambda name: network_has_container(name, cockpit),
        suppressed=lambda: (
            workspace_env._test_suite_gate_active() or not docker_cli_available()
        ),
    )
    monkeypatch.setattr(
        workspace_env,
        "_ensure_network_exists",
        tracker.wrap_ensure(workspace_env._ensure_network_exists),
    )
    monkeypatch.setattr(
        workspace_env,
        "_connect_devcontainer_to_network",
        tracker.wrap_connect(workspace_env._connect_devcontainer_to_network),
    )

    yield tracker

    if tracker.created or tracker.joined:
        tracker.release(cockpit)


@pytest.fixture
def real_network_side_effects(_ciu_test_suite_gate, monkeypatch: pytest.MonkeyPatch) -> None:
    """Opt this test OUT of the CIU-87 gate (mechanism 1).

    For the boundary tests whose whole subject IS the gated code — they drive
    it through a controlled ``subprocess.run``/``_docker_available`` seam, so
    no daemon is reached, but the gate would otherwise return before the
    behavior under test ran. ``_track_real_docker_networks`` stays armed for
    every one of them, per CIU-87's requirement that an opted-out test carry
    the teardown net.

    Depends on ``_ciu_test_suite_gate`` explicitly so the ordering is a stated
    fact rather than a property of pytest's autouse-first heuristic.
    """
    monkeypatch.delenv(CIU_TEST_SUITE_ENV, raising=False)


@pytest.fixture
def write_instance_facts():
    """Write a checkout's ``[ciu.instance.generated]`` overlay facts (CIU-75).

    The post-cutover stand-in for the many fixtures that used to fabricate a
    ``ciu.env``: since ciu 7.7.0 CIU reads instance identity ONLY from that
    overlay table, so a test that wants a directory to look like a provisioned
    CIU instance writes it here instead. Goes through the shipped writer
    (``upsert_generated_facts``) rather than hand-rolled TOML, so a fixture can
    never encode a block shape the product would not itself produce.

    Unspecified facts default to ``""`` — the same shape ``ciu env generate``
    writes for a fact it could not derive (an FQDN-less workspace).
    """
    from ciu.workspace_env import GENERATED_FACTS_KEYS, upsert_generated_facts

    def _write(ciu_root: Path | str, **facts: str) -> Path:
        payload = {key: "" for key in GENERATED_FACTS_KEYS}
        payload.update(facts)
        return upsert_generated_facts(Path(ciu_root), payload)

    return _write
