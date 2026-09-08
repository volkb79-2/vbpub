"""P93 - Lifecycle owner-chain protocol and action migration.

Acceptance oracles (all binding; numbered per the handoff):

O1  A standalone Docker container resolves to itself as the sole
    authoritative owner.
O2  A systemd-owned service resolves systemd as the authoritative owner
    ahead of the raw container.
O3  A Compose-owned container resolves Compose as the authoritative owner.
O4  CIU/Wings ownership chains resolve through the fixture adapters with
    correct precedence.
O5  Conflicting ownership labels surface as a typed conflict rather than an
    automatic pick.
O6  A disappeared owner causes typed refusal instead of execution through a
    lower link in the chain.
O7  A stale incarnation is revalidated before execution and refused if it
    changed.
O8  An unsupported action for the resolved owner is refused with a typed
    outcome.
O9  A verification failure after execution is reported as a typed partial
    outcome with a durable audit record.
O10 Existing Docker/systemd action tests pass unchanged through the migrated
    protocol.
O11 A fake adapter's discovery and plan production are proven side-effect-
    free with no invocation of any external CLI.

All tests use injected inspect/runner/clock/identity fixtures; there is zero
real Docker/systemd mutation and zero real subprocess invocation.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from topos.actions.execute import ExecuteResult
from topos.actions.lifecycle import (
    Capability,
    ChainConflict,
    ChainRefusal,
    Confidence,
    DiscoveryResult,
    OwnerFamily,
    OwnerLink,
    Provenance,
    check_capability,
    resolve_authoritative_owner,
    revalidate,
    verify,
)
from topos.actions.lifecycle_adapters import (
    DOCKER_STANDALONE_CAPABILITIES,
    FIXTURE_OWNER_CAPABILITIES,
    SYSTEMD_STANDALONE_CAPABILITIES,
    CiuAdapter,
    ComposeAdapter,
    DockerAdapter,
    FakeAdapter,
    SystemdAdapter,
    WingsAdapter,
    evaluate_docker_owner_chain,
)
from topos.actions.lifecycle_execute import execute_via_owner_chain

FULL_ID = "abcdef0123456789" * 4
FULL_ID_2 = "1234567890abcdef" * 4
NAME = "wings-db"


def _inspect(full_id: str = FULL_ID, name: str = NAME, labels: dict | None = None):
    payload = [{
        "Id": full_id,
        "Name": "/" + name,
        "Config": {"Labels": dict(labels or {})},
        "State": {"Status": "running"},
    }]

    def inspect(ref: str):
        return payload

    return inspect


def _runner_spy(*, outcome: str = "success", returncode: int = 0):
    calls: list[tuple[str, ...]] = []

    def runner(argv, *, timeout=30.0):
        calls.append(argv)
        return ExecuteResult("", "", argv, returncode, "", "", outcome, 0.0)

    return runner, calls


# ---------------------------------------------------------------------------
# O1 - standalone Docker container is its own sole authoritative owner
# ---------------------------------------------------------------------------


class TestOracle1Standalone:
    def test_standalone_resolves_to_itself(self) -> None:
        adapter = DockerAdapter(inspect=_inspect(labels={}))
        discovery = adapter.discover(NAME)
        assert discovery.conflict is None
        assert len(discovery.chain) == 1
        owner = resolve_authoritative_owner(discovery)
        assert isinstance(owner, OwnerLink)
        assert owner.family is OwnerFamily.DOCKER
        assert owner.incarnation == FULL_ID

    def test_standalone_never_misattributed_to_a_higher_owner(self) -> None:
        """Negative: unknown/unrelated labels must not fabricate an owner link."""
        adapter = DockerAdapter(inspect=_inspect(labels={"maintainer": "me"}))
        discovery = adapter.discover(NAME)
        assert discovery.conflict is None
        assert [link.family for link in discovery.chain] == [OwnerFamily.DOCKER]


# ---------------------------------------------------------------------------
# O2 - systemd owns the container ahead of the raw Docker object
# ---------------------------------------------------------------------------


class TestOracle2SystemdOwnsContainer:
    @staticmethod
    def _systemd_owner_lookup(target: str) -> OwnerLink:
        return OwnerLink(
            family=OwnerFamily.SYSTEMD,
            identity="wings-db.service",
            incarnation="wings-db.service#1",
            provenance=Provenance.FIXTURE,
            confidence=Confidence.HIGH,
            capabilities=SYSTEMD_STANDALONE_CAPABILITIES,
        )

    def test_systemd_is_authoritative_ahead_of_the_container(self) -> None:
        adapter = DockerAdapter(
            inspect=_inspect(labels={}), systemd_owner_lookup=self._systemd_owner_lookup
        )
        discovery = adapter.discover(NAME)
        assert [link.family for link in discovery.chain] == [
            OwnerFamily.SYSTEMD,
            OwnerFamily.DOCKER,
        ]
        owner = resolve_authoritative_owner(discovery)
        assert owner.family is OwnerFamily.SYSTEMD

    def test_raw_container_not_treated_as_authoritative_when_systemd_owns_it(self) -> None:
        """Negative: the raw container link must never be index 0 here."""
        adapter = DockerAdapter(
            inspect=_inspect(labels={}), systemd_owner_lookup=self._systemd_owner_lookup
        )
        discovery = adapter.discover(NAME)
        owner = resolve_authoritative_owner(discovery)
        assert owner.family is not OwnerFamily.DOCKER


# ---------------------------------------------------------------------------
# O3 - Compose-owned container resolves Compose as authoritative
# ---------------------------------------------------------------------------


class TestOracle3Compose:
    def test_compose_is_authoritative(self) -> None:
        adapter = DockerAdapter(
            inspect=_inspect(labels={"com.docker.compose.project": "myproj"})
        )
        discovery = adapter.discover(NAME)
        owner = resolve_authoritative_owner(discovery)
        assert owner.family is OwnerFamily.COMPOSE
        assert owner.detail == "myproj"
        # capability contract 6: no mutate capability is ever advertised.
        assert owner.capabilities == FIXTURE_OWNER_CAPABILITIES

    def test_compose_ownership_not_detected_means_standalone(self) -> None:
        """Negative-adjacent: no compose label at all -> standalone, not Compose."""
        adapter = DockerAdapter(inspect=_inspect(labels={}))
        owner = resolve_authoritative_owner(adapter.discover(NAME))
        assert owner.family is OwnerFamily.DOCKER


# ---------------------------------------------------------------------------
# O4 - CIU/Wings chains resolve through the fixture adapters, correct
# precedence (CIU sits above its own Compose signal; Wings is independent)
# ---------------------------------------------------------------------------


class TestOracle4CiuWings:
    def test_ciu_is_authoritative(self) -> None:
        adapter = DockerAdapter(
            inspect=_inspect(labels={"ciu.managed": "true", "ciu.stack": "infra/redis-core"})
        )
        owner = resolve_authoritative_owner(adapter.discover(NAME))
        assert owner.family is OwnerFamily.CIU
        assert owner.detail == "infra/redis-core"

    def test_ciu_supersedes_compose_signal(self) -> None:
        adapter = DockerAdapter(
            inspect=_inspect(
                labels={
                    "ciu.managed": "true",
                    "ciu.stack": "infra/redis-core",
                    "com.docker.compose.project": "infra-redis-core",
                }
            )
        )
        discovery = adapter.discover(NAME)
        assert discovery.conflict is None
        owner = resolve_authoritative_owner(discovery)
        assert owner.family is OwnerFamily.CIU

    def test_wings_is_authoritative(self) -> None:
        adapter = DockerAdapter(
            inspect=_inspect(labels={"Service": "Pterodactyl", "ContainerType": "server_process"})
        )
        owner = resolve_authoritative_owner(adapter.discover(NAME))
        assert owner.family is OwnerFamily.WINGS

    def test_fixture_adapters_never_invoke_a_cli(self) -> None:
        """Compose/CIU/Wings discover() is fixture-only; direct use must refuse."""
        for cls in (ComposeAdapter, CiuAdapter, WingsAdapter):
            with pytest.raises(NotImplementedError):
                cls().discover("target")
            link = (
                cls().describe_link("proj")
                if cls is not WingsAdapter
                else cls().describe_link()
            )
            with pytest.raises(NotImplementedError):
                cls().plan(DiscoveryResult(chain=(link,)), Capability.RESTART, "target")


# ---------------------------------------------------------------------------
# O5 - conflicting ownership surfaces a typed conflict, never an automatic
# pick
# ---------------------------------------------------------------------------


class TestOracle5Conflict:
    def test_wings_and_compose_together_is_a_typed_conflict(self) -> None:
        adapter = DockerAdapter(
            inspect=_inspect(
                labels={
                    "Service": "Pterodactyl",
                    "ContainerType": "server_process",
                    "com.docker.compose.project": "myproj",
                }
            )
        )
        discovery = adapter.discover(NAME)
        assert discovery.conflict is not None
        assert discovery.conflict.reason == "owner-ambiguous"
        resolution = resolve_authoritative_owner(discovery)
        assert isinstance(resolution, ChainRefusal)
        assert resolution.reason == "owner-ambiguous"

    def test_conflict_is_never_silently_resolved_to_one_owner(self) -> None:
        """Negative: resolution must not return a bare OwnerLink for a conflict."""
        conflicting = DiscoveryResult(
            chain=(),
            conflict=ChainConflict("owner-ambiguous", "conflicting owner labels"),
        )
        resolution = resolve_authoritative_owner(conflicting)
        assert isinstance(resolution, ChainRefusal)

    def test_evaluate_docker_owner_chain_preserves_inspect_failed_reason(self) -> None:
        """A malformed inspect payload is inspect-failed, not owner-ambiguous."""
        refusal = evaluate_docker_owner_chain(
            "docker-start", NAME, inspect=lambda t: [{"Id": "not-64-hex"}]
        )
        assert refusal is not None
        assert refusal.reason == "inspect-failed"


# ---------------------------------------------------------------------------
# O6 - disappeared owner refuses rather than falling back down the chain
# ---------------------------------------------------------------------------


class TestOracle6Disappeared:
    def test_disappeared_owner_is_a_typed_refusal(self) -> None:
        first = OwnerLink(
            family=OwnerFamily.CIU, identity="stack", incarnation="stack#1",
            provenance=Provenance.FIXTURE, confidence=Confidence.HIGH,
            capabilities=FIXTURE_OWNER_CAPABILITIES,
        )

        class _FlakyAdapter:
            """Standalone (no CIU signal) on the revalidation call -- the CIU
            owner previously selected is gone."""

            def discover(self, target: str) -> DiscoveryResult:
                docker_link = OwnerLink(
                    family=OwnerFamily.DOCKER, identity=NAME, incarnation=FULL_ID,
                    provenance=Provenance.LABEL, confidence=Confidence.HIGH,
                    capabilities=DOCKER_STANDALONE_CAPABILITIES,
                )
                return DiscoveryResult(chain=(docker_link,))

        refusal = revalidate(_FlakyAdapter(), NAME, first)
        assert refusal is not None
        assert refusal.reason == "disappeared"

    def test_execution_refuses_rather_than_falling_back_to_a_lower_link(
        self, tmp_path: Path
    ) -> None:
        """Negative: execute_via_owner_chain must not run against the fallen-back link."""

        class _DisappearingAdapter(FakeAdapter):
            def __init__(self) -> None:
                super().__init__()
                self._calls = 0

            def discover(self, target: str) -> DiscoveryResult:
                self._calls += 1
                if self._calls == 1:
                    return super().discover(target)
                # On revalidation, a different (lower-precedence) owner is
                # "discovered" -- this must never be silently accepted.
                lower = OwnerLink(
                    family=OwnerFamily.FAKE, identity=f"{target}-lower", incarnation="lower-1",
                    provenance=Provenance.FIXTURE, confidence=Confidence.LOW,
                    capabilities=frozenset({Capability.INSPECT, Capability.RESTART}),
                )
                return DiscoveryResult(chain=(lower,))

        runner, calls = _runner_spy()
        result = execute_via_owner_chain(
            _DisappearingAdapter(), "t1", Capability.RESTART,
            admin=True, confirm="EXECUTE", audit_path=tmp_path / "a.jsonl",
            root_check=lambda: True, runner=runner,
        )
        assert result.outcome == "disappeared"
        assert calls == []  # the runner was never invoked


# ---------------------------------------------------------------------------
# O7 - stale incarnation is revalidated and refused if it changed
# ---------------------------------------------------------------------------


class TestOracle7Stale:
    def test_changed_incarnation_is_refused(self) -> None:
        class _RecreatedAdapter(FakeAdapter):
            def __init__(self) -> None:
                super().__init__(incarnation="incarnation-1")
                self._calls = 0

            def discover(self, target: str) -> DiscoveryResult:
                self._calls += 1
                incarnation = "incarnation-1" if self._calls == 1 else "incarnation-2"
                link = OwnerLink(
                    family=OwnerFamily.FAKE, identity=target, incarnation=incarnation,
                    provenance=Provenance.FIXTURE, confidence=Confidence.HIGH,
                    capabilities=frozenset({Capability.INSPECT, Capability.RESTART}),
                )
                return DiscoveryResult(chain=(link,))

        adapter = _RecreatedAdapter()
        first = adapter.discover("t1").chain[0]
        refusal = revalidate(adapter, "t1", first)
        assert refusal is not None
        assert refusal.reason == "stale"

    def test_execution_refuses_a_stale_plan_without_running(self, tmp_path: Path) -> None:
        class _RecreatedAdapter(FakeAdapter):
            def __init__(self) -> None:
                super().__init__(incarnation="incarnation-1")
                self._calls = 0

            def discover(self, target: str) -> DiscoveryResult:
                self._calls += 1
                incarnation = "incarnation-1" if self._calls == 1 else "incarnation-2"
                link = OwnerLink(
                    family=OwnerFamily.FAKE, identity=target, incarnation=incarnation,
                    provenance=Provenance.FIXTURE, confidence=Confidence.HIGH,
                    capabilities=frozenset({Capability.INSPECT, Capability.RESTART}),
                )
                return DiscoveryResult(chain=(link,))

        runner, calls = _runner_spy()
        result = execute_via_owner_chain(
            _RecreatedAdapter(), "t1", Capability.RESTART,
            admin=True, confirm="EXECUTE", audit_path=tmp_path / "a.jsonl",
            root_check=lambda: True, runner=runner,
        )
        assert result.outcome == "stale"
        assert calls == []

    def test_unchanged_incarnation_proceeds(self) -> None:
        adapter = FakeAdapter(incarnation="stable-1")
        first = adapter.discover("t1").chain[0]
        assert revalidate(adapter, "t1", first) is None


# ---------------------------------------------------------------------------
# O8 - unsupported action for the resolved owner is a typed refusal
# ---------------------------------------------------------------------------


class TestOracle8Unsupported:
    def test_compose_owner_refuses_restart(self) -> None:
        link = ComposeAdapter().describe_link("myproj")
        refusal = check_capability(link, Capability.RESTART)
        assert refusal is not None
        assert refusal.reason == "unsupported"

    def test_supported_action_is_not_refused(self) -> None:
        link = ComposeAdapter().describe_link("myproj")
        assert check_capability(link, Capability.INSPECT) is None

    def test_unsupported_action_is_not_silently_attempted(self, tmp_path: Path) -> None:
        runner, calls = _runner_spy()
        adapter = DockerAdapter(
            inspect=_inspect(labels={"Service": "Pterodactyl", "ContainerType": "server_process"})
        )
        result = execute_via_owner_chain(
            adapter, NAME, Capability.RESTART,
            admin=True, confirm="EXECUTE", audit_path=tmp_path / "a.jsonl",
            root_check=lambda: True, runner=runner,
        )
        assert result.outcome == "unsupported"
        assert calls == []


# ---------------------------------------------------------------------------
# O9 - verification failure is a typed partial outcome with a durable audit
# record, never swallowed
# ---------------------------------------------------------------------------


class TestOracle9PartialVerification:
    def test_failed_verification_is_a_typed_partial_outcome(self, tmp_path: Path) -> None:
        runner, calls = _runner_spy()
        audit = tmp_path / "a.jsonl"
        result = execute_via_owner_chain(
            FakeAdapter(), "t1", Capability.RESTART,
            admin=True, confirm="EXECUTE", audit_path=audit,
            root_check=lambda: True, runner=runner,
            owner_check=lambda: True, runtime_check=lambda: False,
        )
        assert result.outcome == "partial"
        assert len(calls) == 1  # the action did run

    def test_partial_outcome_leaves_a_durable_audit_record(self, tmp_path: Path) -> None:
        import json

        runner, calls = _runner_spy()
        audit = tmp_path / "a.jsonl"
        result = execute_via_owner_chain(
            FakeAdapter(), "t1", Capability.RESTART,
            admin=True, confirm="EXECUTE", audit_path=audit,
            root_check=lambda: True, runner=runner,
            owner_check=lambda: False, runtime_check=lambda: True,
        )
        assert result.outcome == "partial"
        lines = audit.read_text().strip().splitlines()
        assert len(lines) == 2
        post = json.loads(lines[1])
        assert post["outcome"] == "partial"

    def test_confirmed_verification_stays_success(self, tmp_path: Path) -> None:
        runner, calls = _runner_spy()
        result = execute_via_owner_chain(
            FakeAdapter(), "t1", Capability.RESTART,
            admin=True, confirm="EXECUTE", audit_path=tmp_path / "a.jsonl",
            root_check=lambda: True, runner=runner,
            owner_check=lambda: True, runtime_check=lambda: True,
        )
        assert result.outcome == "success"

    def test_no_verification_configured_stays_success(self, tmp_path: Path) -> None:
        """Without owner_check/runtime_check, verification is not run at all."""
        runner, calls = _runner_spy()
        result = execute_via_owner_chain(
            FakeAdapter(), "t1", Capability.RESTART,
            admin=True, confirm="EXECUTE", audit_path=tmp_path / "a.jsonl",
            root_check=lambda: True, runner=runner,
        )
        assert result.outcome == "success"

    def test_verify_kernel_reports_which_side_failed(self) -> None:
        outcome = verify(owner_check=lambda: True, runtime_check=lambda: False)
        assert outcome.owner_verified is True
        assert outcome.runtime_verified is False
        assert outcome.ok is False
        assert "runtime" in outcome.detail

    def test_verify_kernel_never_swallows_a_raising_check(self) -> None:
        def boom():
            raise RuntimeError("boom")

        outcome = verify(owner_check=boom, runtime_check=lambda: True)
        assert outcome.owner_verified is False
        assert outcome.ok is False


# ---------------------------------------------------------------------------
# O10 - existing Docker/systemd action tests pass unchanged (this is
# verified by running the full suite; this class pins the specific migration
# seam so a regression here fails locally too)
# ---------------------------------------------------------------------------


class TestOracle10MigrationParity:
    """evaluate_docker_owner_chain must match owner_safety.evaluate exactly."""

    @pytest.mark.parametrize(
        "labels",
        [
            {},
            {"maintainer": "me"},
            {"com.docker.compose.project": "myproj"},
            {"ciu.managed": "true", "ciu.stack": "infra/redis-core"},
            {"Service": "Pterodactyl", "ContainerType": "server_process"},
            {"ciu.managed": "maybe"},
            {"Service": "Pterodactyl", "com.docker.compose.project": "p"},
        ],
    )
    def test_parity_with_legacy_owner_safety_evaluate(self, labels: dict) -> None:
        from topos.actions import owner_safety

        legacy = owner_safety.evaluate(
            "docker-start", NAME, inspect=_inspect(labels=labels), protected_services=()
        )
        migrated = evaluate_docker_owner_chain(
            "docker-start", NAME, inspect=_inspect(labels=labels), protected_services=()
        )
        if legacy is None:
            assert migrated is None
        else:
            assert migrated is not None
            assert migrated.reason == legacy.reason
            assert migrated.message == legacy.message

    def test_non_guarded_kind_is_a_noop(self) -> None:
        assert (
            evaluate_docker_owner_chain(
                "systemd-start", "unit.service", inspect=_inspect(labels={})
            )
            is None
        )

    def test_no_inspect_seam_is_a_noop(self) -> None:
        assert evaluate_docker_owner_chain("docker-start", NAME, inspect=None) is None

    def test_systemd_owner_gate_is_a_noop_by_default(self, tmp_path: Path) -> None:
        """Production systemd wiring passes no owner_lookup -- always allow."""
        from topos.actions.execute import execute_plan

        runner, calls = _runner_spy()
        result = execute_plan(
            "systemd-start", "my.service",
            admin=True, confirm="EXECUTE", audit_path=tmp_path / "a.jsonl",
            root_check=lambda: True, runner=runner,
        )
        assert result.outcome == "success"
        assert len(calls) == 1


# ---------------------------------------------------------------------------
# O11 - a fake adapter's discovery/plan is side-effect-free: no external CLI
# invocation at all
# ---------------------------------------------------------------------------


class TestOracle11SideEffectFree:
    def test_discovery_and_plan_never_invoke_a_subprocess(self, monkeypatch) -> None:
        def _forbidden(*args, **kwargs):
            raise AssertionError("discovery/plan must never invoke a subprocess")

        monkeypatch.setattr(subprocess, "run", _forbidden)
        monkeypatch.setattr(subprocess, "Popen", _forbidden)
        monkeypatch.setattr(subprocess, "check_output", _forbidden)
        monkeypatch.setattr(subprocess, "check_call", _forbidden)

        adapter = FakeAdapter()
        discovery = adapter.discover("t1")
        plan = adapter.plan(discovery, Capability.RESTART, "t1")

        assert plan.argv is None
        assert plan.api_intent == "fake:restart:t1"
        assert discovery.chain[0].family is OwnerFamily.FAKE

    def test_discovery_and_plan_produce_no_mutation_side_effects(self) -> None:
        """Calling discover()/plan() repeatedly must be idempotent (no state mutation)."""
        adapter = FakeAdapter()
        first = adapter.discover("t1")
        second = adapter.discover("t1")
        assert first == second
        plan1 = adapter.plan(first, Capability.RESTART, "t1")
        plan2 = adapter.plan(second, Capability.RESTART, "t1")
        assert plan1 == plan2


# ---------------------------------------------------------------------------
# Protocol-level unit tests (types and kernel behavior not tied to one
# oracle above)
# ---------------------------------------------------------------------------


class TestLifecyclePlanInvariant:
    def test_plan_requires_exactly_one_of_argv_or_api_intent(self) -> None:
        from topos.actions.lifecycle import LifecyclePlan

        owner = OwnerLink(
            family=OwnerFamily.FAKE, identity="t1", incarnation="1",
            provenance=Provenance.FIXTURE, confidence=Confidence.HIGH,
            capabilities=frozenset({Capability.INSPECT}),
        )
        with pytest.raises(ValueError):
            LifecyclePlan(
                owner=owner, action=Capability.INSPECT, observed_incarnation="1",
                observed_state="", argv=None, api_intent=None, reversible=True,
                persistence="runtime",
            )
        with pytest.raises(ValueError):
            LifecyclePlan(
                owner=owner, action=Capability.INSPECT, observed_incarnation="1",
                observed_state="", argv=("a",), api_intent="b", reversible=True,
                persistence="runtime",
            )


class TestSystemdAdapterSelfOwning:
    def test_bare_unit_is_self_owning(self) -> None:
        adapter = SystemdAdapter()
        discovery = adapter.discover("my.service")
        assert len(discovery.chain) == 1
        assert discovery.chain[0].family is OwnerFamily.SYSTEMD
        assert discovery.chain[0].capabilities == SYSTEMD_STANDALONE_CAPABILITIES

    def test_plan_builds_systemctl_argv(self) -> None:
        adapter = SystemdAdapter()
        discovery = adapter.discover("my.service")
        plan = adapter.plan(discovery, Capability.RESTART, "my.service")
        assert plan.argv is not None
        assert plan.argv[-2:] == ("restart", "my.service")
