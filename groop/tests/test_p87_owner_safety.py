"""P87 - Docker action owner / protected-ID safety.

Acceptance oracles (all binding; numbered per the handoff):

1. The same protected container is refused when targeted by name, short id and
   full 64-hex id; removing the canonicalization turns the full-id test red.
2. Compose-, CIU- and Wings-labelled fixtures refuse every raw mutation before
   the runner is called, with one pre/post audit pair and no secret label
   values in the message.
3. A standalone fixture still executes each existing verb through the unchanged
   gate chain; existing P46/P72 tests pass unmodified (verified by the full
   suite, which does not touch those files).
4. Conflicting/partial owner labels fail closed with ``owner-ambiguous``.
5. ``docker inspect`` failure or an unresolvable identity is a typed refusal,
   not a name-only fallback.

All action tests use injected runner/clock/identity/inspect fixtures; there is
zero real Docker mutation and zero real ``docker inspect``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from groop.actions.execute import ExecuteResult, execute_kill, execute_plan, execute_update

# A canonical, valid 64-hex container id and its derived short id / name.
FULL_ID = "abcdef0123456789" * 4
SHORT_ID = FULL_ID[:12]
NAME = "wings-db"

# A secret that lives in an unrelated label and must never reach a message.
SECRET = "s3cr3t-token-hunter2"

COMPOSE_LABELS = {"com.docker.compose.project": "myproj", "com.example.token": SECRET}
CIU_LABELS = {"ciu.managed": "true", "ciu.stack": "infra/redis-core", "x.secret": SECRET}
WINGS_LABELS = {"Service": "Pterodactyl", "ContainerType": "server_process", "env.pw": SECRET}


def _inspect(full_id: str = FULL_ID, name: str = NAME, labels: dict | None = None):
    """Build an injectable single-object ``docker inspect`` seam."""
    payload = [{"Id": full_id, "Name": "/" + name, "Config": {"Labels": dict(labels or {})}}]

    def inspect(ref: str):
        return payload

    return inspect


def _counting_inspect(full_id: str = FULL_ID, name: str = NAME, labels: dict | None = None):
    """An inspect seam that records every call, to prove a single inspect."""
    calls: list[str] = []
    payload = [{"Id": full_id, "Name": "/" + name, "Config": {"Labels": dict(labels or {})}}]

    def inspect(ref: str):
        calls.append(ref)
        return payload

    return inspect, calls


def _runner_spy():
    calls: list[tuple[str, ...]] = []

    def runner(argv, *, timeout=30.0):
        calls.append(argv)
        return ExecuteResult("", "", argv, 0, "", "", "success", 0.0)

    return runner, calls


def _run_start(target, audit, *, inspect, protected=(), runner=None):
    return execute_plan(
        "docker-start", target,
        admin=True, confirm="EXECUTE",
        audit_path=audit, root_check=lambda: True, runner=runner,
        owner_inspect=inspect, owner_protected_services=protected,
    )


def _run_kill(target, audit, *, inspect, protected=(), runner=None):
    return execute_kill(
        "docker-kill", target,
        signal="TERM", admin=True, confirm="KILL",
        audit_path=audit, root_check=lambda: True, runner=runner,
        owner_inspect=inspect, owner_protected_services=protected,
    )


def _run_update(target, audit, *, inspect, protected=(), runner=None,
                memory="512M", cpus=None, mem_reader=lambda t: 1024):
    return execute_update(
        target,
        memory=memory, cpus=cpus,
        admin=True, confirm="UPDATE",
        audit_path=audit, root_check=lambda: True, runner=runner,
        current_memory_reader=mem_reader,
        owner_inspect=inspect, owner_protected_services=protected,
    )


# ---------------------------------------------------------------------------
# Owner detection unit tests (provenance only; contract 2 and 6)
# ---------------------------------------------------------------------------


class TestDetectOwner:
    def test_compose(self) -> None:
        from groop.actions.owner_safety import detect_owner
        d = detect_owner({"com.docker.compose.project": "myproj"})
        assert d.owner == "compose" and not d.ambiguous and d.detail == "myproj"

    def test_ciu_label(self) -> None:
        from groop.actions.owner_safety import detect_owner
        d = detect_owner({"ciu.managed": "true", "ciu.stack": "infra/redis-core"})
        assert d.owner == "ciu" and not d.ambiguous and d.detail == "infra/redis-core"

    def test_ciu_over_compose_is_a_coherent_chain(self) -> None:
        """CIU sits above Compose; both present is CIU, not ambiguous."""
        from groop.actions.owner_safety import detect_owner
        d = detect_owner({"ciu.managed": "true", "com.docker.compose.project": "p"})
        assert d.owner == "ciu" and not d.ambiguous

    def test_wings(self) -> None:
        from groop.actions.owner_safety import detect_owner
        d = detect_owner({"Service": "Pterodactyl", "ContainerType": "server_process"})
        assert d.owner == "wings" and not d.ambiguous

    def test_unknown_labels_are_not_ownership(self) -> None:
        """Contract 2: unknown metadata is not permission and not a refusal."""
        from groop.actions.owner_safety import detect_owner
        d = detect_owner({"maintainer": "me", "org.opencontainers.image.title": "x"})
        assert d.owner is None and not d.ambiguous

    def test_empty(self) -> None:
        from groop.actions.owner_safety import detect_owner
        d = detect_owner({})
        assert d.owner is None and not d.ambiguous

    def test_wings_and_compose_conflict_is_ambiguous(self) -> None:
        from groop.actions.owner_safety import detect_owner
        d = detect_owner({"Service": "Pterodactyl", "com.docker.compose.project": "p"})
        assert d.owner is None and d.ambiguous

    def test_wings_and_ciu_conflict_is_ambiguous(self) -> None:
        from groop.actions.owner_safety import detect_owner
        d = detect_owner({"ContainerType": "server_process", "ciu.managed": "true"})
        assert d.owner is None and d.ambiguous

    def test_partial_ciu_managed_value_is_ambiguous(self) -> None:
        from groop.actions.owner_safety import detect_owner
        assert detect_owner({"ciu.managed": "maybe"}).ambiguous
        assert detect_owner({"ciu.managed": ""}).ambiguous

    def test_ciu_managed_false_is_not_managed(self) -> None:
        from groop.actions.owner_safety import detect_owner
        d = detect_owner({"ciu.managed": "false"})
        assert d.owner is None and not d.ambiguous


class TestEvaluateTypedReasons:
    """evaluate() returns typed refusal reasons (contract 4: refusals are typed)."""

    def _eval(self, kind, target, *, labels=None, protected=(), inspect=None):
        from groop.actions.owner_safety import evaluate
        if inspect is None:
            inspect = _inspect(labels=labels or {})
        return evaluate(kind, target, inspect=inspect, protected_services=protected)

    def test_owner_managed_reason(self) -> None:
        r = self._eval("docker-start", NAME, labels=COMPOSE_LABELS)
        assert r is not None and r.reason == "owner-managed"

    def test_owner_ambiguous_reason(self) -> None:
        r = self._eval("docker-kill", NAME, labels={"ciu.managed": "maybe"})
        assert r is not None and r.reason == "owner-ambiguous"

    def test_protected_reason(self) -> None:
        r = self._eval("docker-update", FULL_ID, labels={}, protected=(NAME,))
        assert r is not None and r.reason == "protected"

    def test_inspect_failed_reason(self) -> None:
        r = self._eval("docker-start", NAME, inspect=lambda ref: None)
        assert r is not None and r.reason == "inspect-failed"

    def test_standalone_allows(self) -> None:
        assert self._eval("docker-restart", NAME, labels={}) is None

    def test_non_guarded_kind_is_noop(self) -> None:
        assert self._eval("systemd-restart", "nginx.service", labels=COMPOSE_LABELS) is None

    def test_no_inspect_seam_is_noop(self) -> None:
        from groop.actions.owner_safety import evaluate
        assert evaluate("docker-start", NAME, inspect=None) is None


class TestResolveIdentity:
    def test_valid(self) -> None:
        from groop.actions.owner_safety import resolve_identity
        r = resolve_identity([{"Id": FULL_ID, "Name": "/c1", "Config": {"Labels": {}}}])
        assert r is not None
        assert r.full_id == FULL_ID and r.short_id == SHORT_ID and r.name == "c1"

    def test_none_payload(self) -> None:
        from groop.actions.owner_safety import resolve_identity
        assert resolve_identity(None) is None

    def test_empty_list(self) -> None:
        from groop.actions.owner_safety import resolve_identity
        assert resolve_identity([]) is None

    def test_malformed_id(self) -> None:
        from groop.actions.owner_safety import resolve_identity
        assert resolve_identity([{"Id": "not-hex", "Name": "/c"}]) is None
        assert resolve_identity([{"Name": "/c"}]) is None


# ---------------------------------------------------------------------------
# Oracle 1 - protected container refused by name AND short id AND full id
# ---------------------------------------------------------------------------


class TestOracle1ProtectedCanonicalId:
    @pytest.mark.parametrize("address", [NAME, SHORT_ID, FULL_ID])
    def test_protected_by_name_refused_for_every_address_form(
        self, tmp_path: Path, address: str
    ) -> None:
        runner, calls = _runner_spy()
        result = _run_kill(
            address, tmp_path / "a.jsonl",
            inspect=_inspect(labels={}), protected=(NAME,), runner=runner,
        )
        assert result.outcome == "refusal"
        assert "protected" in result.stderr
        assert calls == []

    def test_full_id_case_is_the_canonicalization_mutation_test(
        self, tmp_path: Path
    ) -> None:
        """Oracle 1 mutation: protected lists the NAME, target is the 64-hex id.

        A raw ``target in protected_services`` check (canonicalization removed)
        would let this through, so this assertion goes red under that mutation.
        """
        runner, calls = _runner_spy()
        result = _run_kill(
            FULL_ID, tmp_path / "a.jsonl",
            inspect=_inspect(name=NAME, labels={}), protected=(NAME,), runner=runner,
        )
        assert result.outcome == "refusal"
        assert calls == []

    def test_protected_by_full_id_addressed_by_name_also_needs_canonicalization(
        self, tmp_path: Path
    ) -> None:
        runner, calls = _runner_spy()
        result = _run_kill(
            NAME, tmp_path / "a.jsonl",
            inspect=_inspect(name=NAME, labels={}), protected=(FULL_ID,), runner=runner,
        )
        assert result.outcome == "refusal"
        assert calls == []

    def test_protected_refusal_is_audited_pre_post(self, tmp_path: Path) -> None:
        audit = tmp_path / "a.jsonl"
        runner, calls = _runner_spy()
        result = _run_kill(
            FULL_ID, audit, inspect=_inspect(labels={}), protected=(NAME,), runner=runner
        )
        assert result.outcome == "refusal"
        lines = audit.read_text().strip().splitlines()
        assert len(lines) == 2
        assert json.loads(lines[0])["stage"] == "pre"
        assert json.loads(lines[1])["stage"] == "post"
        assert calls == []


# ---------------------------------------------------------------------------
# Oracle 2 - owner-managed fixtures refuse every raw mutation, audited, no
# secret label values in the message
# ---------------------------------------------------------------------------

_OWNER_CASES = [
    ("compose", COMPOSE_LABELS, "Docker Compose", "myproj"),
    ("ciu", CIU_LABELS, "CIU", "infra/redis-core"),
    ("wings", WINGS_LABELS, "Pterodactyl", None),
]


class TestOracle2OwnerManagedRefusals:
    @pytest.mark.parametrize("owner,labels,marker,safe_ident", _OWNER_CASES)
    @pytest.mark.parametrize("verb", ["start", "stop", "restart"])
    def test_start_stop_restart_refused(
        self, tmp_path: Path, owner, labels, marker, safe_ident, verb
    ) -> None:
        runner, calls = _runner_spy()
        audit = tmp_path / "a.jsonl"
        result = execute_plan(
            f"docker-{verb}", NAME,
            admin=True, confirm="EXECUTE",
            audit_path=audit, root_check=lambda: True, runner=runner,
            owner_inspect=_inspect(labels=labels), owner_protected_services=(),
        )
        assert result.outcome == "refusal"
        assert marker in result.stderr
        assert SECRET not in result.stderr
        if safe_ident is not None:
            assert safe_ident in result.stderr
        assert calls == []
        lines = audit.read_text().strip().splitlines()
        assert len(lines) == 2  # exactly one pre/post pair

    @pytest.mark.parametrize("owner,labels,marker,safe_ident", _OWNER_CASES)
    def test_kill_refused(self, tmp_path: Path, owner, labels, marker, safe_ident) -> None:
        runner, calls = _runner_spy()
        result = _run_kill(NAME, tmp_path / "a.jsonl", inspect=_inspect(labels=labels), runner=runner)
        assert result.outcome == "refusal"
        assert marker in result.stderr
        assert SECRET not in result.stderr
        assert calls == []

    @pytest.mark.parametrize("owner,labels,marker,safe_ident", _OWNER_CASES)
    def test_durable_update_refused(
        self, tmp_path: Path, owner, labels, marker, safe_ident
    ) -> None:
        """Contract 2/3: a durable --memory update on an owner-managed container.

        The current-usage guard passes (usage 1 KiB < 512 MiB) so the owner gate
        is what refuses.
        """
        runner, calls = _runner_spy()
        audit = tmp_path / "a.jsonl"
        result = _run_update(
            NAME, audit, inspect=_inspect(labels=labels), runner=runner, memory="512M"
        )
        assert result.outcome == "refusal"
        assert marker in result.stderr
        assert SECRET not in result.stderr
        assert calls == []
        lines = audit.read_text().strip().splitlines()
        assert len(lines) == 2

    @pytest.mark.parametrize("owner,labels,marker,safe_ident", _OWNER_CASES)
    def test_cpus_only_update_refused(
        self, tmp_path: Path, owner, labels, marker, safe_ident
    ) -> None:
        runner, calls = _runner_spy()
        result = _run_update(
            NAME, tmp_path / "a.jsonl", inspect=_inspect(labels=labels),
            runner=runner, memory=None, cpus="1.5",
        )
        assert result.outcome == "refusal"
        assert marker in result.stderr
        assert calls == []


# ---------------------------------------------------------------------------
# Oracle 3 - standalone fixture executes every verb
# ---------------------------------------------------------------------------


class TestOracle3StandaloneExecutes:
    @pytest.mark.parametrize("verb", ["start", "stop", "restart"])
    def test_standalone_start_stop_restart_succeeds(self, tmp_path: Path, verb) -> None:
        runner, calls = _runner_spy()
        audit = tmp_path / "a.jsonl"
        result = execute_plan(
            f"docker-{verb}", NAME,
            admin=True, confirm="EXECUTE",
            audit_path=audit, root_check=lambda: True, runner=runner,
            owner_inspect=_inspect(labels={}), owner_protected_services=(),
        )
        assert result.outcome == "success"
        assert len(calls) == 1
        assert len(audit.read_text().strip().splitlines()) == 2

    def test_standalone_kill_succeeds(self, tmp_path: Path) -> None:
        runner, calls = _runner_spy()
        result = _run_kill(NAME, tmp_path / "a.jsonl", inspect=_inspect(labels={}), runner=runner)
        assert result.outcome == "success"
        assert len(calls) == 1

    def test_standalone_update_succeeds(self, tmp_path: Path) -> None:
        runner, calls = _runner_spy()
        result = _run_update(NAME, tmp_path / "a.jsonl", inspect=_inspect(labels={}), runner=runner)
        assert result.outcome == "success"
        assert len(calls) == 1

    def test_unknown_labels_do_not_refuse(self, tmp_path: Path) -> None:
        """Contract 2: unknown metadata is not permission -> still executes."""
        runner, calls = _runner_spy()
        result = _run_kill(
            NAME, tmp_path / "a.jsonl",
            inspect=_inspect(labels={"maintainer": "me", "random": "x"}), runner=runner,
        )
        assert result.outcome == "success"
        assert len(calls) == 1

    def test_legacy_path_without_inspect_is_a_noop(self, tmp_path: Path) -> None:
        """No owner_inspect seam -> the gate is a no-op (legacy P46/P72 path)."""
        runner, calls = _runner_spy()
        result = execute_plan(
            "docker-start", NAME,
            admin=True, confirm="EXECUTE",
            audit_path=tmp_path / "a.jsonl", root_check=lambda: True, runner=runner,
        )
        assert result.outcome == "success"
        assert len(calls) == 1


# ---------------------------------------------------------------------------
# Oracle 4 - owner-ambiguous fails closed
# ---------------------------------------------------------------------------


class TestOracle4OwnerAmbiguous:
    @pytest.mark.parametrize(
        "labels",
        [
            {"Service": "Pterodactyl", "com.docker.compose.project": "p"},
            {"ContainerType": "server_process", "ciu.managed": "true"},
            {"ciu.managed": "maybe"},
        ],
    )
    def test_ambiguous_refused_not_docker_fallback(self, tmp_path: Path, labels) -> None:
        runner, calls = _runner_spy()
        result = _run_kill(NAME, tmp_path / "a.jsonl", inspect=_inspect(labels=labels), runner=runner)
        assert result.outcome == "refusal"
        assert "owner-ambiguous" in result.stderr
        assert calls == []

    def test_ambiguous_is_audited_pre_post(self, tmp_path: Path) -> None:
        audit = tmp_path / "a.jsonl"
        runner, calls = _runner_spy()
        _run_kill(
            NAME, audit,
            inspect=_inspect(labels={"Service": "Pterodactyl", "ciu.managed": "true"}),
            runner=runner,
        )
        assert len(audit.read_text().strip().splitlines()) == 2
        assert calls == []


# ---------------------------------------------------------------------------
# Oracle 5 - inspect failure / unresolvable identity -> typed refusal
# ---------------------------------------------------------------------------


class TestOracle5InspectFailure:
    def test_inspect_returns_none_refused(self, tmp_path: Path) -> None:
        runner, calls = _runner_spy()
        result = _run_kill(NAME, tmp_path / "a.jsonl", inspect=lambda ref: None, runner=runner)
        assert result.outcome == "refusal"
        assert "inspect" in result.stderr
        assert calls == []

    def test_inspect_raises_refused(self, tmp_path: Path) -> None:
        runner, calls = _runner_spy()

        def boom(ref):
            raise OSError("docker daemon unreachable")

        result = _run_kill(NAME, tmp_path / "a.jsonl", inspect=boom, runner=runner)
        assert result.outcome == "refusal"
        assert "docker daemon unreachable" not in result.stderr
        assert calls == []

    def test_unresolvable_identity_refused(self, tmp_path: Path) -> None:
        """A malformed id (identity cannot be established) refuses."""
        runner, calls = _runner_spy()
        bad = lambda ref: [{"Id": "not-a-real-id", "Name": "/c", "Config": {"Labels": {}}}]
        result = _run_kill(NAME, tmp_path / "a.jsonl", inspect=bad, runner=runner)
        assert result.outcome == "refusal"
        assert calls == []

    def test_empty_inspect_refused(self, tmp_path: Path) -> None:
        runner, calls = _runner_spy()
        result = _run_kill(NAME, tmp_path / "a.jsonl", inspect=lambda ref: [], runner=runner)
        assert result.outcome == "refusal"
        assert calls == []

    def test_no_name_only_fallback_when_unprotected_and_unlabelled(
        self, tmp_path: Path
    ) -> None:
        """Contract 7: an inspect failure refuses even for a would-be-safe target.

        Removing the inspect-failure refusal would let this reach the runner.
        """
        runner, calls = _runner_spy()
        result = _run_kill(
            NAME, tmp_path / "a.jsonl", inspect=lambda ref: None, protected=(), runner=runner
        )
        assert result.outcome == "refusal"
        assert calls == []


# ---------------------------------------------------------------------------
# Contract 1 - single inspect / no TOCTOU; systemd unaffected
# ---------------------------------------------------------------------------


class TestContract1SingleInspect:
    def test_success_path_inspects_exactly_once(self, tmp_path: Path) -> None:
        inspect, calls = _counting_inspect(labels={})
        runner, run_calls = _runner_spy()
        result = _run_start(NAME, tmp_path / "a.jsonl", inspect=inspect, runner=runner)
        assert result.outcome == "success"
        assert len(calls) == 1  # exactly one inspect for authorize->execute
        assert len(run_calls) == 1

    def test_refusal_path_inspects_exactly_once(self, tmp_path: Path) -> None:
        inspect, calls = _counting_inspect(labels=COMPOSE_LABELS)
        runner, run_calls = _runner_spy()
        result = _run_start(NAME, tmp_path / "a.jsonl", inspect=inspect, runner=runner)
        assert result.outcome == "refusal"
        assert len(calls) == 1
        assert run_calls == []


class TestSystemdKindsUnaffected:
    def test_systemd_kill_does_not_inspect(self, tmp_path: Path) -> None:
        inspect, calls = _counting_inspect(labels=COMPOSE_LABELS)
        runner, run_calls = _runner_spy()
        result = execute_kill(
            "systemd-kill", "nginx.service",
            signal="TERM", admin=True, confirm="KILL",
            audit_path=tmp_path / "a.jsonl", root_check=lambda: True, runner=runner,
            owner_inspect=inspect,
        )
        assert result.outcome == "success"
        assert calls == []  # non-Docker kind: gate is a no-op
        assert len(run_calls) == 1

    def test_systemd_restart_does_not_inspect(self, tmp_path: Path) -> None:
        inspect, calls = _counting_inspect(labels=COMPOSE_LABELS)
        runner, run_calls = _runner_spy()
        result = execute_plan(
            "systemd-restart", "demo.service",
            admin=True, confirm="EXECUTE",
            audit_path=tmp_path / "a.jsonl", root_check=lambda: True, runner=runner,
            owner_inspect=inspect,
        )
        assert result.outcome == "success"
        assert calls == []
        assert len(run_calls) == 1


class TestCliProductionWiring:
    """Review-fix: pin the production engagement of the gate.

    The owner gate is opt-in at the executor API (``owner_inspect=None`` is
    the legacy no-op path, required so P46/P72 tests pass unmodified), which
    makes ``cli.py``'s three ``owner_inspect=owner_safety.default_owner_inspect``
    keyword arguments the ONLY thing standing between production and a silently
    inert safety package. If a refactor drops one of them, every other test in
    this file stays green. These three go red.
    """

    @staticmethod
    def _capture(monkeypatch, name: str):
        from groop.actions import execute as execute_module

        captured: dict[str, object] = {}

        def fake(*args, **kwargs):
            captured.update(kwargs)
            captured["__args__"] = args
            return ExecuteResult(
                kind="docker-stop", target="c1", argv=("docker", "stop", "c1"),
                returncode=0, stdout="", stderr="", outcome="success", duration_s=0.0,
            )

        monkeypatch.setattr(execute_module, name, fake)
        return captured

    def test_cli_execute_plan_engages_the_owner_gate(self, monkeypatch) -> None:
        from groop.actions import owner_safety
        from groop.cli import _main_action

        captured = self._capture(monkeypatch, "execute_plan")
        code = _main_action(
            ["execute", "--kind", "docker-stop", "--target", "c1",
             "--admin", "--confirm", "EXECUTE", "--json"]
        )
        assert code == 0
        assert captured["owner_inspect"] is owner_safety.default_owner_inspect

    def test_cli_execute_kill_engages_the_owner_gate(self, monkeypatch) -> None:
        from groop.actions import owner_safety
        from groop.cli import _main_action

        captured = self._capture(monkeypatch, "execute_kill")
        code = _main_action(
            ["execute", "--kind", "docker-kill", "--target", "c1",
             "--signal", "TERM", "--admin", "--confirm", "KILL", "--json"]
        )
        assert code == 0
        assert captured["owner_inspect"] is owner_safety.default_owner_inspect

    def test_cli_execute_update_engages_the_owner_gate(self, monkeypatch) -> None:
        from groop.actions import owner_safety
        from groop.cli import _main_action

        captured = self._capture(monkeypatch, "execute_update")
        code = _main_action(
            ["execute", "--kind", "docker-update", "--target", "c1",
             "--memory", "512M", "--admin", "--confirm", "UPDATE", "--json"]
        )
        assert code == 0
        assert captured["owner_inspect"] is owner_safety.default_owner_inspect
