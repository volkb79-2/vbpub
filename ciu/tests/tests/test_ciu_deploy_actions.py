"""CIU v2 ciu-deploy orchestrator tests (P10).

Covers the v2 API of src/ciu/deploy.py:
  - selection/ordering (S7.1 numeric: phase_2 before phase_10);
  - deploy failure-stops-phase semantics (S7.3) with a stubbed
    engine.main_execution: later phases skipped + exit 1;
  - --ignore-errors continues but exit is still 1 (S7.3);
  - profile env_overrides reach the stack (S7.4);
  - vault preflight (S7.6): aborts when *_VAULT specs exist and no token
    resolves; passes when the vault stack precedes in the selection.

These tests drive deploy functions directly with synthetic configs and a
monkeypatched engine — no docker, no real rendering of the test-repo.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import deploy  # noqa: E402
from ciu.deploy_pkg.profiles import Profile  # noqa: E402


# ---------------------------------------------------------------------------
# Selection / ordering (S7.1) — phase_2 before phase_10
# ---------------------------------------------------------------------------


def _config_with_phases(phases: dict) -> dict:
    return {
        "deploy": {
            "project_name": "p",
            "environment_tag": "t",
            "phases": phases,
        }
    }


def test_build_selection_numeric_order_phase_2_before_phase_10():
    config = _config_with_phases(
        {
            "phase_10": {"services": [{"path": "applications/late", "name": "late", "enabled": True}]},
            "phase_2": {"services": [{"path": "applications/early", "name": "early", "enabled": True}]},
        }
    )
    profile = Profile(name=None, phase_keys=None, config=config)

    selection = deploy.build_selection(profile)
    order = [e["phase_key"] for e in selection]

    # Numeric order, not lexicographic (S7.1): phase_2 strictly before phase_10.
    assert order == ["phase_2", "phase_10"]
    assert [e["name"] for e in selection] == ["early", "late"]


def test_build_selection_intersects_cli_phase_filter():
    config = _config_with_phases(
        {
            "phase_1": {"services": [{"path": "infra/a", "name": "a", "enabled": True}]},
            "phase_2": {"services": [{"path": "applications/b", "name": "b", "enabled": True}]},
        }
    )
    profile = Profile(name=None, phase_keys=None, config=config)

    selection = deploy.build_selection(profile, cli_phases={"phase_2"})
    assert [e["path"] for e in selection] == ["applications/b"]


def test_build_selection_appends_profile_extra_stacks_last():
    config = _config_with_phases(
        {"phase_1": {"services": [{"path": "infra/a", "name": "a", "enabled": True}]}}
    )
    profile = Profile(name="p", phase_keys={"phase_1"}, extra_stacks=["tools/x"], config=config)

    selection = deploy.build_selection(profile)
    # Numbered phase first, then the profile's extra stack (documented ordering).
    assert [e["path"] for e in selection] == ["infra/a", "tools/x"]
    assert selection[-1]["phase_key"].startswith(deploy.EXTRA_STACKS_KEY)


def test_extra_stacks_each_form_own_pseudo_phase():
    """S7.4: each profile stack is its OWN pseudo-phase so the deploy loop's
    per-phase provisioning probe runs just-in-time per stack (a shared
    pseudo-phase probed every extra stack's requires before any deployed —
    greenfield cross-stack requirements could never pass)."""
    profile = Profile(
        name="p", phase_keys=set(),
        extra_stacks=["infra/vault", "infra/db-core", "infra/db-init"],
        config={"deploy": {"phases": {}, "control": {}}},
    )
    selection = deploy.build_selection(profile)
    keys = [e["phase_key"] for e in selection]
    assert len(set(keys)) == 3  # all distinct
    groups = deploy.group_by_phase(selection)
    assert [(k, [e["path"] for e in es]) for k, es in groups] == [
        (keys[0], ["infra/vault"]),
        (keys[1], ["infra/db-core"]),
        (keys[2], ["infra/db-init"]),
    ]


def test_group_by_phase_groups_consecutive_entries():
    config = _config_with_phases(
        {
            "phase_1": {
                "services": [
                    {"path": "infra/a", "name": "a", "enabled": True},
                    {"path": "infra/b", "name": "b", "enabled": True},
                ]
            },
            "phase_2": {"services": [{"path": "applications/c", "name": "c", "enabled": True}]},
        }
    )
    profile = Profile(name=None, phase_keys=None, config=config)
    grouped = deploy.group_by_phase(deploy.build_selection(profile))

    assert [key for key, _ in grouped] == ["phase_1", "phase_2"]
    assert [len(entries) for _, entries in grouped] == [2, 1]


# ---------------------------------------------------------------------------
# Health target resolution (S7.7) — Compose identities, never display labels
# ---------------------------------------------------------------------------


def _write_compose(stack_dir: Path, text: str) -> None:
    stack_dir.mkdir(parents=True)
    (stack_dir / "ciu.compose.yml").write_text(text, encoding="utf-8")


def test_health_targets_come_from_all_compose_services_not_phase_display_name(tmp_path):
    _write_compose(
        tmp_path / "infra/db-core",
        """\
services:
  postgres:
    container_name: p-t-postgres
  minio:
    container_name: p-t-minio
""",
    )
    config = _config_with_phases(
        {
            "phase_2": {
                "services": [
                    {
                        "path": "infra/db-core",
                        "name": "Database Core (Postgres and MinIO)",
                        "enabled": True,
                    }
                ]
            }
        }
    )
    profile = Profile(name=None, phase_keys=None, config=config)

    targets = deploy.resolve_selection_health_containers(
        tmp_path, profile, deploy.build_selection(profile), default_timeout_s=30.0,
    )

    assert targets == {"p-t-postgres": 30.0, "p-t-minio": 30.0}
    assert all("Database Core" not in target for target in targets)


def test_health_targets_honor_entry_and_host_compose_profiles(tmp_path):
    _write_compose(
        tmp_path / "tools/admin",
        """\
services:
  always:
    container_name: p-t-always
  debug:
    container_name: p-t-debug
    profiles: [debug]
  metrics:
    container_name: p-t-metrics
    profiles: [metrics]
  dormant:
    container_name: p-t-dormant
    profiles: [not-active]
""",
    )
    config = _config_with_phases(
        {
            "phase_1": {
                "services": [
                    {
                        "path": "tools/admin",
                        "name": "Administration tools",
                        "enabled": True,
                        "profiles": ["debug"],
                    }
                ]
            }
        }
    )
    profile = Profile(
        name="ops",
        phase_keys=None,
        compose_profiles=["metrics"],
        config=config,
    )

    targets = deploy.resolve_selection_health_containers(
        tmp_path, profile, deploy.build_selection(profile), default_timeout_s=30.0,
    )

    assert targets == {"p-t-always": 30.0, "p-t-debug": 30.0, "p-t-metrics": 30.0}


def test_health_target_resolution_fails_for_ambiguous_compose_identity(tmp_path):
    _write_compose(
        tmp_path / "infra/cache",
        """\
services:
  redis:
    image: redis:latest
""",
    )
    config = _config_with_phases(
        {
            "phase_1": {
                "services": [
                    {"path": "infra/cache", "name": "Redis cache", "enabled": True}
                ]
            }
        }
    )
    profile = Profile(name=None, phase_keys=None, config=config)

    with pytest.raises(ValueError, match="set a concrete container_name") as exc:
        deploy.resolve_selection_health_containers(
            tmp_path, profile, deploy.build_selection(profile), default_timeout_s=30.0,
        )

    assert "infra/cache" in str(exc.value)
    assert "redis" in str(exc.value)


def test_bare_health_action_gates_compose_target_not_display_name(monkeypatch, tmp_path):
    _write_compose(
        tmp_path / "infra/cache",
        "services:\n  redis:\n    container_name: p-t-redis\n",
    )
    config = _config_with_phases(
        {
            "phase_1": {
                "services": [
                    {"path": "infra/cache", "name": "Friendly Redis", "enabled": True}
                ]
            }
        }
    )
    profile = Profile(name=None, phase_keys=None, config=config)
    checked: list[str] = []

    def fake_gate(container_names, **kwargs):
        checked.extend(container_names)
        return True, {
            "healthy": list(container_names),
            "pending": [],
            "unhealthy": [],
            "no_healthcheck": [],
            "not_found": [],
        }

    monkeypatch.setattr(deploy, "run_container_health_gate", fake_gate)

    rc = deploy.action_healthcheck(
        tmp_path, profile, deploy.build_selection(profile)
    )

    assert rc == 0
    assert checked == ["p-t-redis"]


def test_bare_health_passes_without_calling_gate_when_all_entries_excluded(
    monkeypatch, tmp_path, capsys
):
    config = _config_with_phases(
        {
            "phase_1": {
                "services": [
                    {
                        "path": "jobs/schema-init",
                        "name": "Schema initialization",
                        "enabled": True,
                        "health": False,
                    }
                ]
            }
        }
    )
    profile = Profile(name=None, phase_keys=None, config=config)
    monkeypatch.setattr(
        deploy,
        "run_container_health_gate",
        lambda *args, **kwargs: pytest.fail("empty health gate must not be called"),
    )

    rc = deploy.action_healthcheck(
        tmp_path, profile, deploy.build_selection(profile)
    )

    assert rc == 0
    assert "No health-enabled containers selected; health gate passes" in capsys.readouterr().out


def test_post_deploy_health_passes_without_gate_for_excluded_one_shot(
    monkeypatch, tmp_path
):
    config = _config_with_phases(
        {
            "phase_1": {
                "services": [
                    {
                        "path": "jobs/schema-init",
                        "name": "Schema initialization",
                        "enabled": True,
                        "health": False,
                    }
                ]
            }
        }
    )
    profile = Profile(name=None, phase_keys=None, config=config)
    stub = _StubEngine(fail_for=set())
    _patch_engine(monkeypatch, stub)
    monkeypatch.setattr(
        deploy,
        "run_container_health_gate",
        lambda *args, **kwargs: pytest.fail("empty health gate must not be called"),
    )

    rc = deploy.action_deploy(
        tmp_path,
        profile,
        deploy.build_selection(profile),
        dry_run=False,
        ignore_errors=False,
        health_after_phase=True,
        update_cert_permission=False,
    )

    assert rc == 0
    assert [call["name"] for call in stub.calls] == ["schema-init"]


# ---------------------------------------------------------------------------
# Deploy: failure-stops-phase (S7.3) with stubbed engine.main_execution
# ---------------------------------------------------------------------------


def _two_phase_profile() -> Profile:
    config = _config_with_phases(
        {
            "phase_1": {
                "services": [
                    {"path": "infra/a", "name": "a", "enabled": True},
                    {"path": "infra/b", "name": "b", "enabled": True},
                ]
            },
            "phase_2": {"services": [{"path": "applications/c", "name": "c", "enabled": True}]},
        }
    )
    return Profile(name=None, phase_keys=None, config=config)


class _StubEngine:
    """Records main_execution calls and returns success/failure per stack name."""

    def __init__(self, fail_for: set[str]):
        self.fail_for = fail_for
        self.calls: list[dict] = []

    def main_execution(self, *, working_dir, dry_run, yes, update_cert_permission, compose_profiles, **kw):
        name = Path(working_dir).name
        self.calls.append(
            {
                "name": name,
                "dry_run": dry_run,
                "yes": yes,
                "compose_profiles": compose_profiles,
                "env_USES": __import__("os").environ.get("PROFILE_PROBE"),
            }
        )
        status = "error" if name in self.fail_for else "success"
        return {"status": status}


def _patch_engine(monkeypatch, stub: _StubEngine):
    monkeypatch.setattr(deploy.engine, "main_execution", stub.main_execution)
    # Make the stack-dir existence check pass without touching the filesystem.
    monkeypatch.setattr(deploy.Path, "is_dir", lambda self: True)


def test_deploy_failure_stops_phase_and_later_phases(monkeypatch, tmp_path):
    profile = _two_phase_profile()
    # 'a' is the FIRST service of phase_1 and fails → 'b' skipped, phase_2 skipped.
    stub = _StubEngine(fail_for={"a"})
    _patch_engine(monkeypatch, stub)

    rc = deploy.action_deploy(
        tmp_path,
        profile,
        deploy.build_selection(profile),
        dry_run=False,
        ignore_errors=False,
        health_after_phase=False,
        update_cert_permission=False,
    )

    assert rc == 1
    # Only 'a' ran; 'b' (same phase, after the failure) and 'c' (later phase)
    # were skipped (S7.3).
    assert [c["name"] for c in stub.calls] == ["a"]


def test_deploy_ignore_errors_continues_but_exits_1(monkeypatch, tmp_path):
    profile = _two_phase_profile()
    stub = _StubEngine(fail_for={"a"})
    _patch_engine(monkeypatch, stub)

    rc = deploy.action_deploy(
        tmp_path,
        profile,
        deploy.build_selection(profile),
        dry_run=False,
        ignore_errors=True,
        health_after_phase=False,
        update_cert_permission=False,
    )

    # --ignore-errors: every service still ran (a fails, b and c run), but the
    # final exit code is still 1 (S7.3).
    assert rc == 1
    assert [c["name"] for c in stub.calls] == ["a", "b", "c"]


def test_deploy_all_success_returns_0(monkeypatch, tmp_path):
    profile = _two_phase_profile()
    stub = _StubEngine(fail_for=set())
    _patch_engine(monkeypatch, stub)

    rc = deploy.action_deploy(
        tmp_path,
        profile,
        deploy.build_selection(profile),
        dry_run=False,
        ignore_errors=False,
        health_after_phase=False,
        update_cert_permission=False,
    )
    assert rc == 0
    assert [c["name"] for c in stub.calls] == ["a", "b", "c"]


# ---------------------------------------------------------------------------
# Deploy: post-phase health failures (S7.3 / S7.7)
# ---------------------------------------------------------------------------


def _health_two_phase_profile() -> Profile:
    """A deliberately small phase graph for deploy/health ordering tests."""
    return Profile(
        name=None,
        phase_keys=None,
        config=_config_with_phases(
            {
                "phase_1": {
                    "services": [
                        {"path": "infra/cache", "name": "cache", "enabled": True}
                    ]
                },
                "phase_2": {
                    "services": [
                        {"path": "applications/api", "name": "api", "enabled": True}
                    ]
                },
            }
        ),
    )


def _unhealthy_summary(name: str) -> dict[str, list[str]]:
    return {
        "healthy": [],
        "pending": [],
        "unhealthy": [name],
        "no_healthcheck": [],
        "not_found": [],
    }


def test_deploy_health_failure_stops_later_phase_after_reporting_summary(monkeypatch, tmp_path, capsys):
    """S7.7 failure is a phase failure: gate it after phase 1, never start phase 2."""
    profile = _health_two_phase_profile()
    events: list[tuple[str, tuple[str, ...]]] = []

    def fake_run(stack_dir, **_kwargs):
        events.append(("deploy", (stack_dir.name,)))
        return True

    def fake_targets(_root, _profile, entries, *, default_timeout_s):
        names = tuple(f"project-prod-{entry['name']}" for entry in entries)
        events.append(("targets", names))
        return {name: default_timeout_s for name in names}

    def fake_gate(container_timeouts, **_kwargs):
        names = tuple(container_timeouts)
        events.append(("health", names))
        return False, _unhealthy_summary(names[0])

    monkeypatch.setattr(deploy, "_run_stack", fake_run)
    monkeypatch.setattr(deploy, "resolve_selection_health_containers", fake_targets)
    monkeypatch.setattr(deploy, "run_container_health_gate", fake_gate)

    rc = deploy.action_deploy(
        tmp_path, profile, deploy.build_selection(profile),
        dry_run=False, ignore_errors=False, health_after_phase=True,
        update_cert_permission=False,
    )

    assert rc == 1
    # The externally meaningful sequence is start -> resolve identities ->
    # inspect health.  API is never started after cache's phase fails.
    assert events == [
        ("deploy", ("cache",)),
        ("targets", ("project-prod-cache",)),
        ("health", ("project-prod-cache",)),
    ]
    output = capsys.readouterr().out
    assert "unhealthy: project-prod-cache" in output
    assert "health gate FAILED for phase phase_1" in output
    assert "SKIP phase phase_2" in output


def test_deploy_ignore_errors_continues_after_health_failure_but_returns_1(monkeypatch, tmp_path):
    """`--ignore-errors` continues at the next phase, without laundering failure."""
    profile = _health_two_phase_profile()
    events: list[tuple[str, tuple[str, ...]]] = []

    def fake_run(stack_dir, **_kwargs):
        events.append(("deploy", (stack_dir.name,)))
        return True

    def fake_targets(_root, _profile, entries, *, default_timeout_s):
        names = tuple(f"project-prod-{entry['name']}" for entry in entries)
        events.append(("targets", names))
        return {name: default_timeout_s for name in names}

    gate_results = iter([False, True])

    def fake_gate(container_timeouts, **_kwargs):
        names = tuple(container_timeouts)
        events.append(("health", names))
        passed = next(gate_results)
        return passed, (
            {"healthy": list(names), "pending": [], "unhealthy": [], "no_healthcheck": [], "not_found": []}
            if passed else _unhealthy_summary(names[0])
        )

    monkeypatch.setattr(deploy, "_run_stack", fake_run)
    monkeypatch.setattr(deploy, "resolve_selection_health_containers", fake_targets)
    monkeypatch.setattr(deploy, "run_container_health_gate", fake_gate)

    rc = deploy.action_deploy(
        tmp_path, profile, deploy.build_selection(profile),
        dry_run=False, ignore_errors=True, health_after_phase=True,
        update_cert_permission=False,
    )

    assert rc == 1
    assert events == [
        ("deploy", ("cache",)),
        ("targets", ("project-prod-cache",)),
        ("health", ("project-prod-cache",)),
        ("deploy", ("api",)),
        ("targets", ("project-prod-api",)),
        ("health", ("project-prod-api",)),
    ]


# ---------------------------------------------------------------------------
# Deploy: profile env_overrides + compose_profiles reach the engine (S7.4)
# ---------------------------------------------------------------------------


def test_profile_env_overrides_and_compose_profiles_reach_engine(monkeypatch, tmp_path):
    config = _config_with_phases(
        {
            "phase_1": {
                "services": [
                    {
                        "path": "infra/a",
                        "name": "a",
                        "enabled": True,
                        "profiles": ["svc_profile"],
                    }
                ]
            }
        }
    )
    profile = Profile(
        name="p",
        phase_keys={"phase_1"},
        compose_profiles=["host_profile"],
        env_overrides={"PROFILE_PROBE": "from_profile"},
        config=config,
    )
    stub = _StubEngine(fail_for=set())
    _patch_engine(monkeypatch, stub)

    rc = deploy.action_deploy(
        tmp_path,
        profile,
        deploy.build_selection(profile),
        dry_run=False,
        ignore_errors=False,
        health_after_phase=False,
        update_cert_permission=False,
    )

    assert rc == 0
    call = stub.calls[0]
    # env_overrides were visible in os.environ during the in-process call (S7.4).
    assert call["env_USES"] == "from_profile"
    # service.profiles + profile.compose_profiles both reach the engine (S7.4).
    assert call["compose_profiles"] == ["svc_profile", "host_profile"]
    # And os.environ was restored afterwards (no permanent mutation).
    import os

    assert os.environ.get("PROFILE_PROBE") is None


# ---------------------------------------------------------------------------
# Vault preflight (S7.6)
# ---------------------------------------------------------------------------


def _vault_topology() -> dict:
    return {"topology": {"services": {"vault": {"internal_host": "vault", "internal_port": 8200}}}}


def test_main_runs_vault_preflight_before_any_deploy_action(monkeypatch, tmp_path):
    """A missing Vault credential is a pre-action configuration failure (S7.6).

    This drives the public ``main`` boundary rather than calling
    :func:`vault_preflight` alone.  It proves a failed preflight cannot be
    accidentally moved behind an engine/deploy action in a future refactor.
    """
    profile = Profile(
        name=None,
        phase_keys=None,
        config={"deploy": {"project_name": "p", "environment_tag": "t", "phases": {}}},
    )
    selection = [{
        "phase_num": 1,
        "phase_key": "phase_1",
        "path": "applications/app",
        "name": "app",
        "service": {"path": "applications/app", "name": "app", "enabled": True},
    }]
    events: list[str] = []

    monkeypatch.setattr(deploy, "bootstrap_workspace_env", lambda **_kw: None)
    monkeypatch.setattr(deploy, "enforce_standalone_root", lambda _cwd: None)
    monkeypatch.setattr(deploy, "resolve_repo_root", lambda _root: tmp_path)
    monkeypatch.setattr(deploy, "load_global_config", lambda _root: profile.config)
    monkeypatch.setattr(deploy, "resolve_profiles", lambda _config, _names: profile)
    monkeypatch.setattr(deploy, "build_selection", lambda _profile, _phases: selection)
    monkeypatch.setattr(deploy, "render_selected_stacks", lambda *_args, **_kw: {})

    def fail_vault(*_args):
        events.append("vault")
        raise ValueError("[S7.6] no Vault token")

    monkeypatch.setattr(deploy, "vault_preflight", fail_vault)
    monkeypatch.setattr(
        deploy,
        "action_deploy",
        lambda *_args, **_kwargs: pytest.fail("deploy must not run after failed Vault preflight"),
    )

    assert deploy.main(["--deploy"]) == 2
    assert events == ["vault"]


def test_vault_preflight_aborts_without_token(monkeypatch, tmp_path):
    # A single app stack that consumes a *_VAULT secret, no vault stack in the
    # selection, and no token resolves → S7.6 ValueError (exit 2).
    import pytest

    config = {
        **_vault_topology(),
        "deploy": {"project_name": "p", "environment_tag": "t", "phases": {}},
        "vault": {"stack_path": "infra/vault"},
    }
    profile = Profile(name=None, phase_keys=None, config=config)
    selection = [
        {
            "phase_num": 2,
            "phase_key": "phase_2",
            "path": "applications/app",
            "name": "app",
            "service": {"path": "applications/app", "name": "app", "enabled": True},
        }
    ]
    rendered = {
        "applications/app": {
            "app": {"secrets": {"db_password": "ASK_VAULT:secret/db"}}
        }
    }

    monkeypatch.setattr(deploy, "resolve_vault_token", lambda cfg, root: None)
    with pytest.raises(ValueError) as exc_info:
        deploy.vault_preflight(tmp_path, profile, selection, rendered)
    assert "[S7.6]" in str(exc_info.value)
    # S7.6 no-token is a config error → exit 2 (pinned).
    from ciu import engine as _engine
    assert _engine._exit_code_for(exc_info.value) == 2


def test_vault_preflight_passes_when_vault_stack_precedes(monkeypatch, tmp_path):
    # Vault stack in phase_1, vault-consuming app in phase_2 → ordering satisfied
    # even with NO token (S7.6) — no exception raised.
    config = {
        **_vault_topology(),
        "deploy": {"project_name": "p", "environment_tag": "t", "phases": {}},
        "vault": {"stack_path": "infra/vault"},
    }
    profile = Profile(name=None, phase_keys=None, config=config)
    selection = [
        {
            "phase_num": 1,
            "phase_key": "phase_1",
            "path": "infra/vault",
            "name": "vault",
            "service": {"path": "infra/vault", "name": "vault", "enabled": True},
        },
        {
            "phase_num": 2,
            "phase_key": "phase_2",
            "path": "applications/app",
            "name": "app",
            "service": {"path": "applications/app", "name": "app", "enabled": True},
        },
    ]
    rendered = {
        # vault stack declares no *_VAULT directives (S7.6 bootstrap rule).
        "infra/vault": {"vault_core": {"name": "vault"}},
        "applications/app": {"app": {"secrets": {"db_password": "ASK_VAULT:secret/db"}}},
    }

    # No token available, but ordering alone must satisfy the gate (no raise).
    monkeypatch.setattr(deploy, "resolve_vault_token", lambda cfg, root: None)
    deploy.vault_preflight(tmp_path, profile, selection, rendered)  # must not raise


def test_vault_preflight_passes_with_token(monkeypatch, tmp_path):
    # No vault stack in the selection, but a token resolves → gate passes (S7.6/S4.16).
    config = {
        **_vault_topology(),
        "deploy": {"project_name": "p", "environment_tag": "t", "phases": {}},
        "vault": {"stack_path": "infra/vault"},
    }
    profile = Profile(name=None, phase_keys=None, config=config)
    selection = [
        {
            "phase_num": 2,
            "phase_key": "phase_2",
            "path": "applications/app",
            "name": "app",
            "service": {"path": "applications/app", "name": "app", "enabled": True},
        }
    ]
    rendered = {"applications/app": {"app": {"secrets": {"db_password": "ASK_VAULT:secret/db"}}}}

    monkeypatch.setattr(deploy, "resolve_vault_token", lambda cfg, root: "s.token")
    deploy.vault_preflight(tmp_path, profile, selection, rendered)  # must not raise


def test_vault_preflight_noop_without_vault_directives(tmp_path):
    # No *_VAULT directives anywhere → gate is a no-op regardless of token.
    config = {
        "deploy": {"project_name": "p", "environment_tag": "t", "phases": {}},
    }
    profile = Profile(name=None, phase_keys=None, config=config)
    selection = [
        {
            "phase_num": 1,
            "phase_key": "phase_1",
            "path": "applications/app",
            "name": "app",
            "service": {"path": "applications/app", "name": "app", "enabled": True},
        }
    ]
    rendered = {"applications/app": {"app": {"env": {"FOO": "bar"}}}}
    deploy.vault_preflight(tmp_path, profile, selection, rendered)  # must not raise


def test_vault_preflight_flags_misplaced_directive(tmp_path):
    # A directive string OUTSIDE a secrets table is a violation (S4.5) → ValueError.
    import pytest

    config = {"deploy": {"project_name": "p", "environment_tag": "t", "phases": {}}}
    profile = Profile(name=None, phase_keys=None, config=config)
    selection = [
        {
            "phase_num": 1,
            "phase_key": "phase_1",
            "path": "applications/app",
            "name": "app",
            "service": {"path": "applications/app", "name": "app", "enabled": True},
        }
    ]
    rendered = {"applications/app": {"app": {"token": "ASK_VAULT:secret/oops"}}}
    with pytest.raises(ValueError) as exc_info:
        deploy.vault_preflight(tmp_path, profile, selection, rendered)
    assert "S4.5" in str(exc_info.value) or "S4.1" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Governance slice preflight (D-G9 check 1, S15.8) — fail CLOSED
# ---------------------------------------------------------------------------


def _governance_selection_rendered(cgroup_parent: str, *, enabled: bool = True):
    selection = [
        {
            "phase_num": 1,
            "phase_key": "phase_1",
            "path": "applications/app",
            "name": "app",
            "service": {"path": "applications/app", "name": "app", "enabled": True},
        }
    ]
    rendered = {
        "applications/app": {
            "app": {"governance": {"enabled": enabled, "cgroup_parent": cgroup_parent}}
        }
    }
    return selection, rendered


def _plain_config() -> dict:
    return {"deploy": {"project_name": "p", "environment_tag": "t", "phases": {}}}


def test_governance_slice_preflight_raises_when_slice_missing(monkeypatch, tmp_path):
    import pytest

    profile = Profile(name=None, phase_keys=None, config=_plain_config())
    selection, rendered = _governance_selection_rendered("nyxloom-daemon.slice")

    monkeypatch.setattr(
        deploy.governance_mod,
        "check_slice_unit",
        lambda name: (False, f"{name}: LoadState=not-found"),
    )
    with pytest.raises(ValueError) as exc_info:
        deploy.governance_slice_preflight(tmp_path, profile, selection, rendered)
    assert "[S15.G9-1]" in str(exc_info.value)
    assert "nyxloom-daemon.slice" in str(exc_info.value)
    # A missing slice is a configuration/setup failure → exit 2 (S10.3), like
    # registry_preflight's missing-`docker login` case.
    from ciu import engine as _engine

    assert _engine._exit_code_for(exc_info.value) == 2


def test_governance_slice_preflight_passes_when_slice_exists(monkeypatch, tmp_path):
    profile = Profile(name=None, phase_keys=None, config=_plain_config())
    selection, rendered = _governance_selection_rendered("nyxloom-daemon.slice")

    monkeypatch.setattr(
        deploy.governance_mod,
        "check_slice_unit",
        lambda name: (True, f"{name}: LoadState=loaded"),
    )
    deploy.governance_slice_preflight(tmp_path, profile, selection, rendered)  # must not raise


def test_governance_slice_preflight_skips_on_non_systemd_host(monkeypatch, tmp_path, capsys):
    profile = Profile(name=None, phase_keys=None, config=_plain_config())
    selection, rendered = _governance_selection_rendered("nyxloom-daemon.slice")

    monkeypatch.setattr(
        deploy.governance_mod,
        "check_slice_unit",
        lambda name: (None, "no systemctl on this host — skipping the slice-existence preflight"),
    )
    deploy.governance_slice_preflight(tmp_path, profile, selection, rendered)  # must not raise
    out = capsys.readouterr().out
    assert "no systemctl" in out


def test_governance_slice_preflight_checks_every_resolved_slice_no_default_exemption(monkeypatch, tmp_path):
    """No more "CIU's shipped default is exempt" special case (host dev-tier
    cgroup governance rollout — GOVERNANCE_DEFAULTS no longer hardcodes a
    slice name, so there is nothing left to exempt): even a slice name that
    used to be the shipped default gets probed like any other."""
    profile = Profile(name=None, phase_keys=None, config=_plain_config())
    selection, rendered = _governance_selection_rendered("besteffort.slice")

    checked = []
    monkeypatch.setattr(
        deploy.governance_mod, "check_slice_unit",
        lambda name: checked.append(name) or (True, f"{name}: LoadState=loaded"),
    )
    deploy.governance_slice_preflight(tmp_path, profile, selection, rendered)  # must not raise
    assert checked == ["besteffort.slice"]


def test_governance_slice_preflight_raises_when_stack_relies_on_ambient_default_but_none_set(
    monkeypatch, tmp_path,
):
    """A stack that names no cgroup_parent at all (relying on the ambient
    CGROUP_PARENT_DEV_BACKGROUND) must fail the preflight loudly when that
    env var isn't present either — not silently skip the check."""
    import pytest

    monkeypatch.delenv("CGROUP_PARENT_DEV_BACKGROUND", raising=False)
    profile = Profile(name=None, phase_keys=None, config=_plain_config())
    selection, rendered = _governance_selection_rendered("")

    with pytest.raises(ValueError, match=r"\[S15\.2\].*no cgroup_parent is resolvable"):
        deploy.governance_slice_preflight(tmp_path, profile, selection, rendered)


def test_governance_slice_preflight_noop_when_governance_disabled(monkeypatch, tmp_path):
    profile = Profile(name=None, phase_keys=None, config=_plain_config())
    selection, rendered = _governance_selection_rendered("nyxloom-daemon.slice", enabled=False)

    def fail_check(name):
        raise AssertionError("check_slice_unit must not be called when governance is disabled")

    monkeypatch.setattr(deploy.governance_mod, "check_slice_unit", fail_check)
    deploy.governance_slice_preflight(tmp_path, profile, selection, rendered)  # must not raise


def test_governance_slice_preflight_noop_without_governance_table(tmp_path):
    profile = Profile(name=None, phase_keys=None, config=_plain_config())
    selection = [
        {
            "phase_num": 1,
            "phase_key": "phase_1",
            "path": "applications/app",
            "name": "app",
            "service": {"path": "applications/app", "name": "app", "enabled": True},
        }
    ]
    rendered = {"applications/app": {"app": {"env": {"FOO": "bar"}}}}
    deploy.governance_slice_preflight(tmp_path, profile, selection, rendered)  # must not raise


def test_governance_slice_preflight_honors_no_preflight_flag(monkeypatch, tmp_path, capsys):
    profile = Profile(name=None, phase_keys=None, config=_plain_config())
    selection, rendered = _governance_selection_rendered("nyxloom-daemon.slice")

    def fail_check(name):
        raise AssertionError("check_slice_unit must not be called under --no-preflight")

    monkeypatch.setattr(deploy.governance_mod, "check_slice_unit", fail_check)
    deploy.governance_slice_preflight(
        tmp_path, profile, selection, rendered, no_preflight=True
    )  # must not raise
    assert "skipping" in capsys.readouterr().out


def test_governance_slice_preflight_dedupes_same_slice_across_stacks(monkeypatch, tmp_path):
    """Two stacks sharing the same missing slice → checked ONCE, both named in the error."""
    import pytest

    profile = Profile(name=None, phase_keys=None, config=_plain_config())
    selection = [
        {
            "phase_num": 1, "phase_key": "phase_1", "path": "applications/a", "name": "a",
            "service": {"path": "applications/a", "name": "a", "enabled": True},
        },
        {
            "phase_num": 2, "phase_key": "phase_2", "path": "applications/b", "name": "b",
            "service": {"path": "applications/b", "name": "b", "enabled": True},
        },
    ]
    rendered = {
        "applications/a": {"a": {"governance": {"enabled": True, "cgroup_parent": "shared.slice"}}},
        "applications/b": {"b": {"governance": {"enabled": True, "cgroup_parent": "shared.slice"}}},
    }
    calls: list[str] = []

    def fake_check(name):
        calls.append(name)
        return False, f"{name}: LoadState=not-found"

    monkeypatch.setattr(deploy.governance_mod, "check_slice_unit", fake_check)
    with pytest.raises(ValueError) as exc_info:
        deploy.governance_slice_preflight(tmp_path, profile, selection, rendered)
    assert calls == ["shared.slice"]  # checked ONCE, not once per stack
    assert "applications/a" in str(exc_info.value)
    assert "applications/b" in str(exc_info.value)


def test_governance_slice_preflight_skips_entry_missing_from_rendered(monkeypatch, tmp_path):
    """A selection entry with no corresponding `rendered` key (e.g. render was
    filtered upstream) is skipped, not KeyError'd."""
    profile = Profile(name=None, phase_keys=None, config=_plain_config())
    selection = [
        {
            "phase_num": 1,
            "phase_key": "phase_1",
            "path": "applications/unrendered",
            "name": "unrendered",
            "service": {"path": "applications/unrendered", "name": "unrendered", "enabled": True},
        }
    ]
    rendered: dict = {}  # deliberately missing "applications/unrendered"

    def fail_check(name):
        raise AssertionError("check_slice_unit must not be called with nothing rendered")

    monkeypatch.setattr(deploy.governance_mod, "check_slice_unit", fail_check)
    deploy.governance_slice_preflight(tmp_path, profile, selection, rendered)  # must not raise


def test_governance_slice_preflight_skips_malformed_stack_shape(monkeypatch, tmp_path):
    """A stack config that fails S3.5 shape validation (e.g. two root keys) is
    skipped here — that's a separate validation's job, not this preflight's."""
    profile = Profile(name=None, phase_keys=None, config=_plain_config())
    selection = [
        {
            "phase_num": 1,
            "phase_key": "phase_1",
            "path": "applications/malformed",
            "name": "malformed",
            "service": {"path": "applications/malformed", "name": "malformed", "enabled": True},
        }
    ]
    # Two non-reserved top-level keys → validate_stack_shape raises [S3.5].
    rendered = {
        "applications/malformed": {
            "app_a": {"governance": {"enabled": True, "cgroup_parent": "x.slice"}},
            "app_b": {"governance": {"enabled": True, "cgroup_parent": "y.slice"}},
        }
    }

    def fail_check(name):
        raise AssertionError("check_slice_unit must not be called for a malformed stack shape")

    monkeypatch.setattr(deploy.governance_mod, "check_slice_unit", fail_check)
    deploy.governance_slice_preflight(tmp_path, profile, selection, rendered)  # must not raise


def test_governance_slice_preflight_skips_shipped_stacks(monkeypatch, tmp_path):
    """S8.6 shipped stacks have no CIU config to resolve governance from."""
    profile = Profile(name=None, phase_keys=None, config=_plain_config())
    selection = [
        {
            "phase_num": 1,
            "phase_key": "phase_1",
            "path": "applications/shipped",
            "name": "shipped",
            "service": {"path": "applications/shipped", "name": "shipped", "enabled": True, "shipped": True},
        }
    ]
    # No rendered entry at all for a shipped stack (mirrors render_selected_stacks' skip).
    rendered: dict = {}

    def fail_check(name):
        raise AssertionError("check_slice_unit must not be called for a shipped stack")

    monkeypatch.setattr(deploy.governance_mod, "check_slice_unit", fail_check)
    deploy.governance_slice_preflight(tmp_path, profile, selection, rendered)  # must not raise


# ---------------------------------------------------------------------------
# Governance mem_min preflight (D-G9 check 3, S15.16) — fail CLOSED
# ---------------------------------------------------------------------------


def _governance_selection_rendered_mem_min(cgroup_parent: str, mem_min: str, *, enabled: bool = True):
    selection = [
        {
            "phase_num": 1,
            "phase_key": "phase_1",
            "path": "applications/app",
            "name": "app",
            "service": {"path": "applications/app", "name": "app", "enabled": True},
        }
    ]
    rendered = {
        "applications/app": {
            "app": {
                "governance": {
                    "enabled": enabled,
                    "cgroup_parent": cgroup_parent,
                    "mem_min": mem_min,
                }
            }
        }
    }
    return selection, rendered


def test_governance_slice_preflight_mem_min_inadequate_warns_by_default(
    monkeypatch, tmp_path, capsys,
):
    """deploy.py:1128-1134 (S10.7): the DEFAULT ciu.exit_on (ERROR, i.e. no
    ciu.exit_on set at all) means an [S15.16] mem_min-inadequate finding is a
    logged [WARN] that does NOT stop the deploy; the test below shows
    ciu.exit_on="WARN" restores the fail-fast behavior."""
    profile = Profile(name=None, phase_keys=None, config=_plain_config())
    selection, rendered = _governance_selection_rendered_mem_min("nyxloom-daemon.slice", "2g")

    monkeypatch.setattr(
        deploy.governance_mod, "check_slice_unit",
        lambda name: (True, f"{name}: LoadState=loaded"),
    )
    monkeypatch.setattr(
        deploy.governance_mod, "check_slice_memory_min",
        lambda name, required: (False, f"{name}: MemoryMin=0 — no floor is configured on the slice unit"),
    )
    deploy.governance_slice_preflight(tmp_path, profile, selection, rendered)  # must not raise
    out = capsys.readouterr().out
    assert "[WARN]" in out
    assert "[S15.16]" in out
    assert "nyxloom-daemon.slice" in out


def test_governance_slice_preflight_raises_when_mem_min_inadequate_and_exit_on_warn(
    monkeypatch, tmp_path,
):
    """S10.7: setting ciu.exit_on = "WARN" in config makes the same [S15.16]
    mem_min-inadequate finding fail-fast (raise), unlike the DEFAULT
    (exit_on=ERROR) case in test_..._warns_by_default above."""
    config = _plain_config()
    config["ciu"] = {"exit_on": "WARN"}
    profile = Profile(name=None, phase_keys=None, config=config)
    selection, rendered = _governance_selection_rendered_mem_min("nyxloom-daemon.slice", "2g")

    monkeypatch.setattr(
        deploy.governance_mod, "check_slice_unit",
        lambda name: (True, f"{name}: LoadState=loaded"),
    )
    monkeypatch.setattr(
        deploy.governance_mod, "check_slice_memory_min",
        lambda name, required: (False, f"{name}: MemoryMin=0 — no floor is configured on the slice unit"),
    )
    with pytest.raises(ValueError) as exc_info:
        deploy.governance_slice_preflight(tmp_path, profile, selection, rendered)
    assert "[S15.16]" in str(exc_info.value)
    assert "nyxloom-daemon.slice" in str(exc_info.value)
    assert "applications/app" in str(exc_info.value)


def test_governance_slice_preflight_passes_when_mem_min_adequate(monkeypatch, tmp_path):
    profile = Profile(name=None, phase_keys=None, config=_plain_config())
    selection, rendered = _governance_selection_rendered_mem_min("nyxloom-daemon.slice", "2g")

    monkeypatch.setattr(
        deploy.governance_mod, "check_slice_unit",
        lambda name: (True, f"{name}: LoadState=loaded"),
    )
    monkeypatch.setattr(
        deploy.governance_mod, "check_slice_memory_min",
        lambda name, required: (True, f"{name}: MemoryMin={required} bytes (>= required {required})"),
    )
    deploy.governance_slice_preflight(tmp_path, profile, selection, rendered)  # must not raise


def test_governance_slice_preflight_skips_mem_min_when_not_declared(monkeypatch, tmp_path):
    profile = Profile(name=None, phase_keys=None, config=_plain_config())
    selection, rendered = _governance_selection_rendered("nyxloom-daemon.slice")  # no mem_min key at all

    monkeypatch.setattr(
        deploy.governance_mod, "check_slice_unit",
        lambda name: (True, f"{name}: LoadState=loaded"),
    )

    def fail_mem_min(name, required):
        raise AssertionError("check_slice_memory_min must not be called when mem_min is not declared")

    monkeypatch.setattr(deploy.governance_mod, "check_slice_memory_min", fail_mem_min)
    deploy.governance_slice_preflight(tmp_path, profile, selection, rendered)  # must not raise


def test_governance_slice_preflight_mem_min_skipped_when_slice_missing(monkeypatch, tmp_path):
    """A missing slice already aborts on [S15.G9-1]; mem_min inadequacy for
    that same slice must not ALSO be probed (the existence failure already
    explains everything a mem_min check would add)."""
    import pytest

    profile = Profile(name=None, phase_keys=None, config=_plain_config())
    selection, rendered = _governance_selection_rendered_mem_min("nyxloom-daemon.slice", "2g")

    monkeypatch.setattr(
        deploy.governance_mod, "check_slice_unit",
        lambda name: (False, f"{name}: LoadState=not-found"),
    )

    def fail_mem_min(name, required):
        raise AssertionError("check_slice_memory_min must not be called for a missing slice")

    monkeypatch.setattr(deploy.governance_mod, "check_slice_memory_min", fail_mem_min)
    with pytest.raises(ValueError) as exc_info:
        deploy.governance_slice_preflight(tmp_path, profile, selection, rendered)
    assert "[S15.G9-1]" in str(exc_info.value)


def test_governance_slice_preflight_invalid_mem_min_size_raises(monkeypatch, tmp_path):
    import pytest

    profile = Profile(name=None, phase_keys=None, config=_plain_config())
    selection, rendered = _governance_selection_rendered_mem_min("nyxloom-daemon.slice", "not-a-size")

    monkeypatch.setattr(
        deploy.governance_mod, "check_slice_unit",
        lambda name: (True, f"{name}: LoadState=loaded"),
    )
    with pytest.raises(ValueError, match=r"\[S15\.16\].*not a valid size"):
        deploy.governance_slice_preflight(tmp_path, profile, selection, rendered)


def test_governance_slice_preflight_mem_min_dedupes_and_takes_max_across_stacks(monkeypatch, tmp_path):
    """Two stacks sharing a slice with different mem_min: the check runs ONCE
    per slice, against the MAX of the declared floors."""
    profile = Profile(name=None, phase_keys=None, config=_plain_config())
    selection = [
        {
            "phase_num": 1, "phase_key": "phase_1", "path": "applications/a", "name": "a",
            "service": {"path": "applications/a", "name": "a", "enabled": True},
        },
        {
            "phase_num": 2, "phase_key": "phase_2", "path": "applications/b", "name": "b",
            "service": {"path": "applications/b", "name": "b", "enabled": True},
        },
    ]
    rendered = {
        "applications/a": {"a": {"governance": {"enabled": True, "cgroup_parent": "shared.slice", "mem_min": "1g"}}},
        "applications/b": {"b": {"governance": {"enabled": True, "cgroup_parent": "shared.slice", "mem_min": "4g"}}},
    }
    monkeypatch.setattr(
        deploy.governance_mod, "check_slice_unit",
        lambda name: (True, f"{name}: LoadState=loaded"),
    )
    calls = []

    def fake_mem_min(name, required):
        calls.append((name, required))
        return True, f"{name}: MemoryMin={required} bytes"

    monkeypatch.setattr(deploy.governance_mod, "check_slice_memory_min", fake_mem_min)
    deploy.governance_slice_preflight(tmp_path, profile, selection, rendered)  # must not raise
    assert calls == [("shared.slice", 4 * 1024 ** 3)]  # max(1g, 4g), checked once


def test_governance_slice_preflight_mem_min_skips_on_non_systemd_host(monkeypatch, tmp_path, capsys):
    profile = Profile(name=None, phase_keys=None, config=_plain_config())
    selection, rendered = _governance_selection_rendered_mem_min("nyxloom-daemon.slice", "2g")

    monkeypatch.setattr(
        deploy.governance_mod, "check_slice_unit",
        lambda name: (True, f"{name}: LoadState=loaded"),
    )
    monkeypatch.setattr(
        deploy.governance_mod, "check_slice_memory_min",
        lambda name, required: (None, "no systemctl on this host — skipping the mem_min preflight"),
    )
    deploy.governance_slice_preflight(tmp_path, profile, selection, rendered)  # must not raise
    assert "no systemctl" in capsys.readouterr().out


def test_governance_slice_preflight_skips_non_slice_cgroup_parent(monkeypatch, tmp_path):
    """A resolved cgroup_parent that doesn't end in `.slice` names no systemd
    slice unit to check (e.g. a raw cgroupfs path under a non-systemd cgroup
    driver) — skip the existence/mem_min preflight for it entirely."""
    profile = Profile(name=None, phase_keys=None, config=_plain_config())
    selection, rendered = _governance_selection_rendered("custom-cgroup-path")

    def fail_check(name):
        raise AssertionError("check_slice_unit must not be called for a non-.slice cgroup_parent")

    monkeypatch.setattr(deploy.governance_mod, "check_slice_unit", fail_check)
    deploy.governance_slice_preflight(tmp_path, profile, selection, rendered)  # must not raise


# ---------------------------------------------------------------------------
# CLI helpers (S10.2 action surface; --groups removed)
# ---------------------------------------------------------------------------


def test_build_action_sequence_order_and_no_groups():
    # argv is WITHOUT the program name (sys.argv[1:]); preserves CLI order.
    argv = ["--stop", "--clean", "--deploy", "--healthcheck"]
    assert deploy.build_action_sequence(argv) == ["stop", "clean", "deploy", "healthcheck"]


def test_parse_args_has_no_groups_flag():
    # S7.5 greenfield: --groups does not exist; argparse must reject it (exit 2).
    import pytest

    with pytest.raises(SystemExit):
        deploy.parse_args(["--groups", "infra"])


def test_parse_phase_filter_numeric():
    assert deploy._parse_phase_filter("1,2,10") == {"phase_1", "phase_2", "phase_10"}
    assert deploy._parse_phase_filter(None) is None


def test_parse_phase_filter_rejects_non_numeric():
    import pytest

    with pytest.raises(ValueError):
        deploy._parse_phase_filter("infra")


def test_seconds_parser():
    assert deploy._seconds("30s") == 30.0
    assert deploy._seconds("2m") == 120.0
    assert deploy._seconds("45") == 45.0
    assert deploy._seconds(15) == 15.0
    assert deploy._seconds("bogus", default=7.0) == 7.0


def test_reject_groups_via_load_global_config(monkeypatch, tmp_path):
    # load_global_config must reject [deploy.groups] with the S7.5 pointer.
    import pytest

    monkeypatch.setattr(
        deploy.config_model,
        "render_global_chain",
        lambda working_dir, repo_root: {"deploy": {"groups": {"infra": ["phase_1"]}}},
    )
    with pytest.raises(ValueError) as exc:
        deploy.load_global_config(tmp_path)
    assert "[S7.5]" in str(exc.value)


# ---------------------------------------------------------------------------
# CIU-3 — complete teardown (S6.4 post-clean invariant)
# ---------------------------------------------------------------------------

import pytest  # noqa: E402
from unittest.mock import MagicMock  # noqa: E402


def _proc(returncode=0, stdout="", stderr=""):
    m = MagicMock()
    m.returncode = returncode
    m.stdout = stdout
    m.stderr = stderr
    return m


def _teardown_config() -> dict:
    return {"deploy": {"project_name": "proj", "environment_tag": "env"}}


def test_matching_containers_all_states_adds_dash_a(monkeypatch):
    """CIU-3: all_states=True lists exited containers too (docker ps -a)."""
    calls: list[list[str]] = []

    def fake_docker(args, **kw):
        calls.append(args)
        return _proc(stdout="proj-env-vault-init\nproj-env-vault\n")

    monkeypatch.setattr(deploy.procutil, "docker", fake_docker)
    names = deploy._matching_containers(_teardown_config(), all_states=True)
    assert names == ["proj-env-vault-init", "proj-env-vault"]
    assert calls[0][0] == "ps" and "-a" in calls[0]


def test_matching_containers_default_running_only(monkeypatch):
    """The --stop path must stay running-only (no -a)."""
    calls: list[list[str]] = []
    monkeypatch.setattr(
        deploy.procutil, "docker",
        lambda args, **kw: (calls.append(args), _proc(stdout=""))[1],
    )
    deploy._matching_containers(_teardown_config())
    assert "-a" not in calls[0]


def test_remove_project_volumes_returns_survivors(monkeypatch):
    """CIU-3: a volume still present after rm (in use) is returned as a survivor."""
    seq = iter([
        _proc(stdout="proj-env-vault-data\n"),            # initial ls
        _proc(returncode=1, stderr="volume is in use"),    # rm fails
        _proc(stdout="proj-env-vault-data\n"),            # re-list: still there
    ])
    monkeypatch.setattr(deploy.procutil, "docker", lambda args, **kw: next(seq))
    survivors = deploy._remove_project_volumes(_teardown_config())
    assert survivors == ["proj-env-vault-data"]


def test_remove_project_volumes_clean_returns_empty(monkeypatch):
    seq = iter([
        _proc(stdout="proj-env-vault-data\n"),  # initial ls
        _proc(returncode=0),                     # rm ok
        _proc(stdout=""),                        # re-list: gone
    ])
    monkeypatch.setattr(deploy.procutil, "docker", lambda args, **kw: next(seq))
    assert deploy._remove_project_volumes(_teardown_config()) == []


def test_action_clean_invariant_fails_on_surviving_volume(monkeypatch, tmp_path):
    """CIU-3: a project volume that survives teardown makes clean exit 1 (S6.4)."""
    config = _teardown_config()
    profile = MagicMock()
    profile.config = config

    # No stacks to reset (skip engine/render); focus on the invariant check.
    monkeypatch.setattr(deploy, "render_selected_stacks", lambda *a, **k: {})
    # First container sweep: empty; final invariant sweep: also empty.
    monkeypatch.setattr(deploy, "_matching_containers", lambda *a, **k: [])
    # A volume survives removal.
    monkeypatch.setattr(deploy, "_remove_project_volumes",
                        lambda cfg=None, **_kw: ["proj-env-vault-data"])

    rc = deploy.action_clean(tmp_path, profile, [], ignore_errors=True)
    assert rc == 1


def test_action_clean_invariant_passes_when_clean(monkeypatch, tmp_path):
    config = _teardown_config()
    profile = MagicMock()
    profile.config = config
    monkeypatch.setattr(deploy, "render_selected_stacks", lambda *a, **k: {})
    monkeypatch.setattr(deploy, "_matching_containers", lambda *a, **k: [])
    monkeypatch.setattr(deploy, "_remove_project_volumes", lambda cfg=None, **_kw: [])
    rc = deploy.action_clean(tmp_path, profile, [], ignore_errors=True)
    assert rc == 0


def test_action_clean_preserves_worktree_durable_inputs(monkeypatch, tmp_path):
    """S16: clean removes runtime/rendered state, never instance inputs."""
    config = _teardown_config()
    profile = MagicMock()
    profile.config = config
    durable = {
        "ciu.env": 'export INSTANCE_ID="abc123"\n',
        "ciu.global.worktree.toml.j2": "[ciu.instance]\nservice_profiles = [\"core\"]\n",
        "ciu.worktree-instance.json": '{"schema_version": 1}\n',
    }
    for name, body in durable.items():
        (tmp_path / name).write_text(body, encoding="utf-8")
    monkeypatch.setattr(deploy, "render_selected_stacks", lambda *a, **k: {})
    monkeypatch.setattr(deploy, "_matching_containers", lambda *a, **k: [])
    monkeypatch.setattr(deploy, "_remove_project_volumes", lambda cfg=None, **_kw: [])

    assert deploy.action_clean(tmp_path, profile, [], ignore_errors=True) == 0
    for name, body in durable.items():
        assert (tmp_path / name).read_text(encoding="utf-8") == body


# ---------------------------------------------------------------------------
# Seam 4 — --profile repeatable + comma form (§8 AC#7)
# ---------------------------------------------------------------------------


def test_worktree_local_service_profiles_are_default_selection(monkeypatch):
    cfg = {
        "ciu": {"instance": {"service_profiles": ["core", "db"]}},
        "deploy": {
            "phases": {
                "phase_1": {"enabled": True, "services": []},
                "phase_2": {"enabled": True, "services": []},
            },
            "profiles": {
                "core": {"phases": ["phase_1"]},
                "db": {"phases": ["phase_2"]},
            },
        },
    }
    monkeypatch.setenv("CIU_SERVICES_PROFILE", "ignored-legacy-env")
    profile = deploy.resolve_profiles(cfg, None)
    assert profile.phase_keys == {"phase_1", "phase_2"}


def test_cli_service_profiles_override_worktree_local_selection():
    cfg = {
        "ciu": {"instance": {"service_profiles": ["core"]}},
        "deploy": {
            "phases": {
                "phase_1": {"enabled": True, "services": []},
                "phase_2": {"enabled": True, "services": []},
            },
            "profiles": {
                "core": {"phases": ["phase_1"]},
                "db": {"phases": ["phase_2"]},
            },
        },
    }
    profile = deploy.resolve_profiles(cfg, ["db"])
    assert profile.phase_keys == {"phase_2"}


@pytest.mark.parametrize(
    "instance",
    [
        {"service_profiles": []},
        {"service_profiles": ["core", ""]},
        {"service_profiles": ["core", 3]},
    ],
)
def test_worktree_local_service_profiles_reject_malformed_values(instance):
    with pytest.raises(ValueError, match="non-empty string array"):
        deploy.resolve_profiles({"ciu": {"instance": instance}}, None)


def test_worktree_local_service_profiles_reject_duplicates():
    with pytest.raises(ValueError, match="duplicate"):
        deploy.resolve_profiles(
            {"ciu": {"instance": {"service_profiles": ["core", "core"]}}}, None
        )


def test_non_table_ciu_or_instance_does_not_invent_worktree_selection(monkeypatch):
    monkeypatch.delenv("CIU_SERVICES_PROFILE", raising=False)
    assert deploy.resolve_profiles({"ciu": []}, None).name is None
    assert deploy.resolve_profiles({"ciu": {"instance": []}}, None).name is None

class TestDeployParseArgsProfileSeam4:
    """Tests for the deploy.parse_args --profile repeatable flag."""

    def test_single_profile_produces_list(self):
        args = deploy.parse_args(["--profile", "core"])
        assert args.profile == ["core"]

    def test_repeatable_profile_produces_list(self):
        args = deploy.parse_args(["--profile", "core", "--profile", "db"])
        assert args.profile == ["core", "db"]

    def test_no_profile_produces_none(self):
        args = deploy.parse_args([])
        assert args.profile is None

    def test_comma_form_single_entry(self):
        """--profile core,db is accepted (comma split happens in _run)."""
        args = deploy.parse_args(["--profile", "core,db"])
        # argparse appends the raw entry — splitting happens in _run
        assert args.profile == ["core,db"]

    def test_profile_help_mentions_ciu_services_profile(self, capsys):
        """Help text must reference CIU_SERVICES_PROFILE (not CIU_HOST_PROFILE)."""
        import pytest
        with pytest.raises(SystemExit):
            deploy.parse_args(["--help"])
        out = capsys.readouterr().out
        assert "CIU_SERVICES_PROFILE" in out
        assert "CIU_HOST_PROFILE" not in out


class TestFilterDeploymentPhasesNarrowing:
    """S7.5: filter_deployment_phases must distinguish None from empty set."""

    _PHASES = [{"key": "phase_1"}, {"key": "phase_2"}]

    def test_none_means_unrestricted(self):
        assert deploy.filter_deployment_phases(self._PHASES, None) == self._PHASES

    def test_empty_set_means_no_phases(self):
        assert deploy.filter_deployment_phases(self._PHASES, set()) == []

    def test_subset_filters(self):
        out = deploy.filter_deployment_phases(self._PHASES, {"phase_2"})
        assert out == [{"key": "phase_2"}]


# ===========================================================================
# `ciu check` — full config-validation pipeline (CIU-QOL-12 / S13.4a, ciu-P18)
# ===========================================================================

import json  # noqa: E402
import os  # noqa: E402
import textwrap  # noqa: E402

from ciu import provisioning  # noqa: E402


_CHECK_GLOBAL: dict = {"deploy": {"project_name": "p", "environment_tag": "t"}}


def _check_profile(config: dict | None = None) -> Profile:
    return Profile(
        name=None, phase_keys=None,
        config=config if config is not None else {"deploy": dict(_CHECK_GLOBAL["deploy"])},
    )


def _tree_snapshot(root: Path) -> dict[str, object]:
    """Every path under *root* with its type, mode, size and bytes.

    Deliberately content-sensitive: a created directory, a materialized secret,
    a rendered compose/overlay/configfile, or an in-place rewrite of any
    existing file all change this dict. Used by the O1 oracle below.
    """
    snap: dict[str, object] = {}
    for path in sorted(root.rglob("*")):
        rel = str(path.relative_to(root))
        if path.is_symlink():
            snap[rel] = f"<symlink:{os.readlink(path)}>"
        elif path.is_dir():
            snap[rel] = "<dir>"
        else:
            st = path.stat()
            snap[rel] = (oct(st.st_mode), st.st_size, path.read_bytes())
    return snap


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text), encoding="utf-8")
    return path


def _full_stack_fixture(repo_root: Path, rel: str = "infra/app") -> dict:
    """A stack whose REAL `ciu up` would create dirs, secrets, files and run hooks.

    Mirrors test-repo/applications/app-config: a hostdir, four secret
    directives (incl. GEN_LOCAL, which materializes into the project store), a
    configfile with a schema, a compose template consuming secrets, and hooks
    at all three points whose `run()` writes a marker file. Returns the
    rendered stack config dict `action_check` receives.
    """
    stack_dir = repo_root / rel
    _write(stack_dir / "ciu.compose.yml.j2", """\
        services:
          app:
            image: {{ app_stack.app.image }}
            container_name: {{ deploy.project_name }}-{{ deploy.environment_tag }}-app
            secrets:
              - api_key
              - run_nonce
    """)
    _write(stack_dir / "conf/app.toml.j2", 'name = "{{ app_stack.app.image }}"\n')
    _write(stack_dir / "conf/app.schema.json", '{"type": "object"}\n')
    _write(stack_dir / "files/demo-ca.pem", "-----BEGIN CERTIFICATE-----\n")
    _write(stack_dir / "hooks/preflight_hook.py", """\
        from pathlib import Path


        def run(config, ctx):
            # If `ciu check` ever executes a hook body, this marker appears and
            # BOTH the O1 tree snapshot and the explicit assertion below fail.
            Path(ctx.stack_dir, "HOOK_RUN_MARKER").write_text("ran")
            return {}


        def validate_config(config, ctx):
            errors = []
            # The guarded config is the sanctioned way to see a secret is DECLARED.
            if "api_key" not in config.get("app_stack", {}).get("secrets", {}):
                errors.append("api_key is not declared")
            # `ctx.secret_file` refuses unconditionally during check (S13.4a).
            try:
                ctx.secret_file("api_key")
            except KeyError:
                pass
            else:
                errors.append("secret_file should be unavailable during check")
            return errors
    """)
    return {
        "app_stack": {
            "requires": [],
            "provides": [],
            "governance": {"enabled": True},
            "secrets": {
                "api_key": "GEN_LOCAL:demo/api_key",
                "run_nonce": "GEN_EPHEMERAL",
                "license": {"directive": "ASK_EXTERNAL:CIU_DEMO_LICENSE",
                            "consumed_by": "hook"},
                "ca_bundle": "ASK_FILE:files/demo-ca.pem",
            },
            "app": {
                "image": "busybox",
                "hostdir": {"logs": str(stack_dir / "vol-logs")},
                "configfile": {
                    "main": {
                        "template": "conf/app.toml.j2",
                        "target": "/etc/app/config.toml",
                        "schema": "conf/app.schema.json",
                    }
                },
            },
            "hooks": {
                "pre_secrets": ["hooks/preflight_hook.py"],
                "pre_compose": ["hooks/preflight_hook.py"],
                "post_compose": ["hooks/preflight_hook.py"],
            },
        }
    }


# ---------------------------------------------------------------------------
# O1 — `ciu check` is side-effect-free
# ---------------------------------------------------------------------------


def test_tree_snapshot_helper_actually_detects_a_change(tmp_path: Path):
    """Guard on the O1 oracle itself: the snapshot must not be vacuous."""
    _write(tmp_path / "a.txt", "one")
    before = _tree_snapshot(tmp_path)

    (tmp_path / "b.txt").write_text("two", encoding="utf-8")
    assert _tree_snapshot(tmp_path) != before

    (tmp_path / "b.txt").unlink()
    assert _tree_snapshot(tmp_path) == before

    (tmp_path / "a.txt").write_text("changed", encoding="utf-8")
    assert _tree_snapshot(tmp_path) != before

    (tmp_path / "a.txt").write_text("one", encoding="utf-8")
    (tmp_path / "sub").mkdir()
    assert _tree_snapshot(tmp_path) != before


def test_check_leaves_the_filesystem_byte_for_byte_unchanged(tmp_path: Path, capsys):
    """O1 — the whole reason CIU-QOL-12 exists.

    A stack that on a real `ciu up` (or `ciu up --dry-run`, which still does
    both) would create a hostdir, materialize four secrets, write a rendered
    configfile + compose + overlay, and execute three hook `run()` bodies.
    After `ciu check` the tmp_path tree must be IDENTICAL.
    """
    rendered = {"infra/app": _full_stack_fixture(tmp_path)}
    selection = [{"path": "infra/app"}]

    before = _tree_snapshot(tmp_path)
    rc = deploy.action_check(tmp_path, _check_profile(), selection, rendered)
    after = _tree_snapshot(tmp_path)

    assert rc == 0, capsys.readouterr().out
    assert after == before

    # Named, specific negatives on top of the whole-tree equality, so a
    # failure says WHICH side effect leaked rather than only "tree differs".
    stack_dir = tmp_path / "infra/app"
    assert not (stack_dir / "HOOK_RUN_MARKER").exists()   # no hook run() executed
    assert not (stack_dir / "vol-logs").exists()          # no hostdir created
    assert not (stack_dir / ".ciu").exists()              # no secrets/rendered/overlay
    assert not (stack_dir / "ciu.compose.yml").exists()   # no compose written


def test_check_never_calls_main_execution(tmp_path: Path, monkeypatch):
    """The forbidden implementation shortcut, pinned as a negative.

    `engine.main_execution(dry_run=True)` still creates hostdirs and still runs
    hooks for real — building `ciu check` on it would reproduce the exact
    defect this package removes.
    """
    monkeypatch.setattr(
        deploy.engine, "main_execution",
        lambda *_a, **_kw: pytest.fail("ciu check must never call main_execution"),
    )
    rendered = {"infra/app": _full_stack_fixture(tmp_path)}

    assert deploy.action_check(tmp_path, _check_profile(), [{"path": "infra/app"}], rendered) == 0


# ---------------------------------------------------------------------------
# Per-stage failure fixtures (O2) — every new stage's failure is exit 2
# ---------------------------------------------------------------------------


def _min_stack(repo_root: Path, rel: str, root: dict, *, compose: str | None = None) -> dict:
    """A minimal on-disk stack: a compose template plus the given root table."""
    stack_dir = repo_root / rel
    stack_dir.mkdir(parents=True, exist_ok=True)
    if compose is not None:
        _write(stack_dir / "ciu.compose.yml.j2", compose)
    return {"app_stack": root}


_TRIVIAL_COMPOSE = "services:\n  app:\n    image: busybox\n"


def _stage(out_document: dict, name: str) -> dict:
    return next(s for s in out_document["stages"] if s["stage"] == name)


def _run_check_json(
    repo_root: Path, rendered: dict, capsys, *, profile: Profile | None = None, **kwargs
) -> tuple[int, dict]:
    selection = [{"path": rel} for rel in rendered]
    rc = deploy.action_check(
        repo_root, profile or _check_profile(), selection, rendered,
        json_output=True, **kwargs
    )
    return rc, json.loads(capsys.readouterr().out)


def test_check_stage2_shape_failure_is_exit_2_and_skips_that_stack_only(tmp_path, capsys):
    rendered = {
        "infra/bad": {"state": {}},  # S3.5: no non-reserved root key
        "infra/good": _min_stack(tmp_path, "infra/good", {"app": {}}, compose=_TRIVIAL_COMPOSE),
    }

    rc, doc = _run_check_json(tmp_path, rendered, capsys)

    assert rc == 2
    assert doc["status"] == "fail"
    shape = _stage(doc, "shape")
    assert shape["status"] == "fail"
    assert "[S3.5]" in shape["findings"][0]["message"]
    assert shape["findings"][0]["stack"] == "infra/bad"
    # The healthy stack was still walked — one bad stack does not blind the run.
    assert _stage(doc, "compose-render")["status"] == "pass"


def test_check_stage3_rejects_a_malformed_secret_directive(tmp_path, capsys):
    rendered = {
        "infra/app": _min_stack(
            tmp_path, "infra/app", {"secrets": {"api_key": 123}}, compose=_TRIVIAL_COMPOSE
        )
    }

    rc, doc = _run_check_json(tmp_path, rendered, capsys)

    assert rc == 2
    assert "[S4.4]" in _stage(doc, "secrets")["findings"][0]["message"]
    # Guarding is impossible without specs, so the render stages stand down
    # explicitly rather than rendering against an unguarded config.
    assert "skipped" in _stage(doc, "compose-render")["notes"][0]["message"]


def test_check_stage3_rejects_a_directive_outside_a_secrets_table(tmp_path, capsys):
    rendered = {
        "infra/app": _min_stack(
            tmp_path, "infra/app", {"app": {"token": "GEN_LOCAL:x/y"}},
            compose=_TRIVIAL_COMPOSE,
        )
    }

    rc, doc = _run_check_json(tmp_path, rendered, capsys)

    assert rc == 2
    assert "[S4.5/S4.1]" in _stage(doc, "secrets")["findings"][0]["message"]


def test_check_stage5_reports_a_malformed_governance_table(tmp_path, capsys):
    rendered = {
        "infra/app": _min_stack(
            tmp_path, "infra/app", {"governance": "not-a-table"}, compose=_TRIVIAL_COMPOSE
        )
    }

    rc, doc = _run_check_json(tmp_path, rendered, capsys)

    assert rc == 2
    assert "[S15.2]" in _stage(doc, "governance")["findings"][0]["message"]


def test_check_stage5_never_touches_systemd(tmp_path, monkeypatch):
    """Governance stage is shape/resolution ONLY — no live slice probing."""
    monkeypatch.setattr(
        deploy.governance_mod, "check_slice_unit",
        lambda *_a, **_kw: pytest.fail("ciu check must not probe systemd slices"),
    )
    rendered = {
        "infra/app": _min_stack(
            tmp_path, "infra/app",
            {"governance": {"enabled": True, "cgroup_parent": "x.slice"}},
            compose=_TRIVIAL_COMPOSE,
        )
    }

    assert deploy.action_check(tmp_path, _check_profile(), [{"path": "infra/app"}], rendered) == 0


def test_check_stage6_reports_every_configfile_declaration_defect(tmp_path, capsys):
    stack_dir = tmp_path / "infra/app"
    stack_dir.mkdir(parents=True)
    _write(stack_dir / "ciu.compose.yml.j2", _TRIVIAL_COMPOSE)
    _write(stack_dir / "present.j2", "x = 1\n")
    root = {
        "app": {
            "configfile": {
                "not_a_table": "oops",
                "no_template": {"target": "/etc/a.toml"},
                "empty_template": {"template": "", "target": "/etc/a.toml"},
                "absent_template": {"template": "gone.j2", "target": "/etc/a.toml"},
                "no_target": {"template": "present.j2"},
                "relative_target": {"template": "present.j2", "target": "etc/a.toml"},
                "bad_schema_type": {"template": "present.j2", "target": "/etc/a.toml",
                                    "schema": 7},
                "absent_schema": {"template": "present.j2", "target": "/etc/a.toml",
                                  "schema": "gone.json"},
                "bad_instances": {"template": "present.j2", "target": "/etc/a.toml",
                                  "instances": 0},
                "bool_instances": {"template": "present.j2", "target": "/etc/a.toml",
                                   "instances": True},
                "ok": {"template": "present.j2", "target": "/etc/a.toml",
                       "instances": 2},
            }
        },
        "not_a_service": 5,
        "no_configfile": {"image": "busybox"},
        "configfile_not_a_table": {"configfile": "nope"},
    }
    rendered = {"infra/app": {"app_stack": root}}

    rc, doc = _run_check_json(tmp_path, rendered, capsys)

    assert rc == 2
    messages = [f["message"] for f in _stage(doc, "configfile")["findings"]]
    assert all(m.startswith("[S5.1]") for m in messages)
    joined = "\n".join(messages)
    for expected in (
        "'app.not_a_table' must be a table",
        "'app.no_template' is missing a `template` path",
        "'app.empty_template' is missing a `template` path",
        "template not found for 'app.absent_template'",
        "'app.no_target' is missing a `target` container path",
        "'app.relative_target' has a non-absolute `target`",
        "'app.bad_schema_type': `schema` must be a file path",
        "schema file not found for 'app.absent_schema'",
        "'app.bad_instances': 'instances' must be a positive integer",
        "'app.bool_instances': 'instances' must be a positive integer",
    ):
        assert expected in joined, expected
    assert "'app.ok'" not in joined


def test_check_stage6_tolerates_a_non_table_stack_root(tmp_path):
    """A root key that merges to a non-dict yields no configfile findings."""
    assert deploy._check_configfile_declarations(tmp_path, "app_stack", {"app_stack": 5}) == []


def test_check_stage6_renders_nothing_to_disk(tmp_path):
    """Stage 6 is existence-only: `.ciu/rendered/` must never appear."""
    stack_dir = tmp_path / "infra/app"
    stack_dir.mkdir(parents=True)
    _write(stack_dir / "ciu.compose.yml.j2", _TRIVIAL_COMPOSE)
    _write(stack_dir / "c.j2", "value = 1\n")
    rendered = {"infra/app": {"app_stack": {"app": {"configfile": {
        "main": {"template": "c.j2", "target": "/etc/a.toml"}}}}}}

    before = _tree_snapshot(tmp_path)
    assert deploy.action_check(tmp_path, _check_profile(), [{"path": "infra/app"}], rendered) == 0
    assert _tree_snapshot(tmp_path) == before


# ---------------------------------------------------------------------------
# Stages 8 + 9 — hook load and validate_config preflight (O3)
# ---------------------------------------------------------------------------


def _hook_stack(repo_root: Path, rel: str, hooks: dict, *, extra: dict | None = None) -> dict:
    root: dict = {"hooks": hooks}
    if extra:
        root.update(extra)
    return _min_stack(repo_root, rel, root, compose=_TRIVIAL_COMPOSE)


def test_check_stage8_reports_a_missing_hook_file(tmp_path, capsys):
    rendered = {"infra/app": _hook_stack(tmp_path, "infra/app",
                                         {"pre_compose": ["hooks/gone.py"]})}

    rc, doc = _run_check_json(tmp_path, rendered, capsys)

    assert rc == 2
    finding = _stage(doc, "hooks-load")["findings"][0]
    assert "[S9.2]" in finding["message"]
    assert finding["hook"] == "hooks/gone.py"


def test_check_stage8_reports_a_module_with_no_run(tmp_path, capsys):
    _write(tmp_path / "infra/app/h.py", "X = 1\n")
    rendered = {"infra/app": _hook_stack(tmp_path, "infra/app", {"pre_compose": ["h.py"]})}

    rc, doc = _run_check_json(tmp_path, rendered, capsys)

    assert rc == 2
    assert "does not define a 'run' function" in _stage(doc, "hooks-load")["findings"][0]["message"]


def test_check_stage8_reports_an_import_time_explosion_without_aborting(tmp_path, capsys):
    _write(tmp_path / "infra/boom/h.py", "raise RuntimeError('import blew up')\n")
    rendered = {
        "infra/boom": _hook_stack(tmp_path, "infra/boom", {"pre_compose": ["h.py"]}),
        "infra/ok": _min_stack(tmp_path, "infra/ok", {"app": {}}, compose=_TRIVIAL_COMPOSE),
    }

    rc, doc = _run_check_json(tmp_path, rendered, capsys)

    assert rc == 2
    assert "RuntimeError: import blew up" in _stage(doc, "hooks-load")["findings"][0]["message"]
    assert _stage(doc, "compose-render")["status"] == "pass"


def test_check_stage9_aggregates_validate_config_findings(tmp_path, capsys):
    _write(tmp_path / "infra/app/h.py", """\
        def run(config, ctx):
            raise AssertionError("run() must not execute")

        def validate_config(config, ctx):
            return ["registry.database is missing", "registry.postgresql.users.api is missing"]
    """)
    rendered = {"infra/app": _hook_stack(tmp_path, "infra/app", {"post_compose": ["h.py"]})}

    rc, doc = _run_check_json(tmp_path, rendered, capsys)

    assert rc == 2
    findings = _stage(doc, "hooks-preflight")["findings"]
    assert [f["message"] for f in findings] == [
        "registry.database is missing",
        "registry.postgresql.users.api is missing",
    ]
    assert {f["hook"] for f in findings} == {"h.py"}


# --- CIU-65: validate_config findings carry a severity ---------------------
#
# Oracle: a hook author can report something worth knowing WITHOUT also
# making it must-block. ERROR keeps today's meaning exactly (a bare string is
# an ERROR, unchanged), WARN routes to a stage NOTE, and an unrecognized
# severity is refused rather than guessed at in EITHER direction.


def _severity_stack(tmp_path, body: str) -> dict:
    _write(tmp_path / "infra/app/h.py", f"""\
        def run(config, ctx):
            raise AssertionError("run() must not execute")

        def validate_config(config, ctx):
{body}
    """)
    return {"infra/app": _hook_stack(tmp_path, "infra/app", {"post_compose": ["h.py"]})}


def test_check_stage9_bare_string_finding_is_still_an_error(tmp_path, capsys):
    """CIU-65 backward compatibility, pinned: the pre-CIU-65 shape must not
    change weight. A hook that says nothing about severity still BLOCKS."""
    rendered = _severity_stack(tmp_path, '            return ["old style finding"]')

    rc, doc = _run_check_json(tmp_path, rendered, capsys)

    assert rc == 2
    stage = _stage(doc, "hooks-preflight")
    assert stage["status"] == "fail"
    assert [f["message"] for f in stage["findings"]] == ["old style finding"]
    assert stage["notes"] == []


def test_check_stage9_warn_severity_does_not_fail_the_stage(tmp_path, capsys):
    """CIU-65's whole point: a WARN is recorded, visible, and blocks nothing."""
    rendered = _severity_stack(
        tmp_path, '            return [("WARN", "readonly role is absent")]'
    )

    rc, doc = _run_check_json(tmp_path, rendered, capsys)

    assert rc == 0
    stage = _stage(doc, "hooks-preflight")
    assert stage["status"] == "pass"
    assert stage["findings"] == []
    assert [n["message"] for n in stage["notes"]] == ["[WARN] readonly role is absent"]
    # A WARN names its hook exactly as an ERROR does, or the two tiers would
    # carry different provenance for the same kind of finding.
    assert stage["notes"][0]["hook"] == "h.py"
    assert stage["notes"][0]["stack"] == "infra/app"


def test_check_stage9_error_severity_pair_fails_like_a_bare_string(tmp_path, capsys):
    rendered = _severity_stack(
        tmp_path, '            return [("ERROR", "registry.database is missing")]'
    )

    rc, doc = _run_check_json(tmp_path, rendered, capsys)

    assert rc == 2
    stage = _stage(doc, "hooks-preflight")
    assert [f["message"] for f in stage["findings"]] == ["registry.database is missing"]
    assert stage["notes"] == []


def test_check_stage9_severity_is_case_and_whitespace_insensitive(tmp_path, capsys):
    """The documented normalization — `str(v).strip().upper()`, the same one
    S10.7's ciu.exit_on already applies to this exact vocabulary."""
    rendered = _severity_stack(tmp_path, """\
            return [
                ("warn", "lowercase warn"),
                (" Error ", "padded mixed-case error"),
            ]""")

    rc, doc = _run_check_json(tmp_path, rendered, capsys)

    assert rc == 2
    stage = _stage(doc, "hooks-preflight")
    assert [f["message"] for f in stage["findings"]] == ["padded mixed-case error"]
    assert [n["message"] for n in stage["notes"]] == ["[WARN] lowercase warn"]


def test_check_stage9_a_two_element_list_is_a_severity_pair_too(tmp_path, capsys):
    """Documented on purpose: a hook assembling findings from JSON or a
    comprehension produces lists, and refusing those would be a trap with no
    safety value."""
    rendered = _severity_stack(
        tmp_path, '            return [["WARN", "from a list, not a tuple"]]'
    )

    rc, doc = _run_check_json(tmp_path, rendered, capsys)

    assert rc == 0
    assert [n["message"] for n in _stage(doc, "hooks-preflight")["notes"]] == [
        "[WARN] from a list, not a tuple"
    ]


def test_check_stage9_unknown_severity_is_refused_not_guessed(tmp_path, capsys):
    """The masked-default guard. `"warning"` is a plausible typo for WARN;
    silently reading it AS a warn would downgrade a blocking finding, and the
    run that does so looks identical to a healthy one. It fails loudly and
    names the vocabulary instead."""
    rendered = _severity_stack(
        tmp_path, '            return [("warning", "typo\'d severity")]'
    )

    rc, doc = _run_check_json(tmp_path, rendered, capsys)

    assert rc == 2
    stage = _stage(doc, "hooks-preflight")
    assert stage["status"] == "fail"
    message = stage["findings"][0]["message"]
    assert "'warning'" in message
    assert "WARN or ERROR" in message
    assert stage["notes"] == []


def test_check_stage9_never_is_not_a_finding_severity(tmp_path, capsys):
    """NEVER is in S10.7's exit_on vocabulary but is a THRESHOLD, not a
    property a finding can have — so the finding vocabulary is deliberately
    the SUBSET, and NEVER is refused like any other unknown value."""
    rendered = _severity_stack(
        tmp_path, '            return [("NEVER", "not a finding severity")]'
    )

    rc, doc = _run_check_json(tmp_path, rendered, capsys)

    assert rc == 2
    assert "'NEVER'" in _stage(doc, "hooks-preflight")["findings"][0]["message"]


def test_check_stage9_wrong_length_sequence_stays_a_fail_closed_error(tmp_path, capsys):
    """A 3-tuple is not a severity pair. It falls through to the pre-CIU-65
    treatment — str(item) as an ERROR — so an odd return keeps failing closed
    rather than becoming newly acceptable."""
    rendered = _severity_stack(
        tmp_path, '            return [("WARN", "a", "b")]'
    )

    rc, doc = _run_check_json(tmp_path, rendered, capsys)

    assert rc == 2
    stage = _stage(doc, "hooks-preflight")
    assert stage["notes"] == []
    assert "'WARN'" in stage["findings"][0]["message"]


def test_check_stage9_non_list_return_names_both_accepted_shapes(tmp_path, capsys):
    """The contract-violation message has to describe what IS accepted now,
    not the pre-CIU-65 'list of error strings'."""
    rendered = _severity_stack(tmp_path, "            return True")

    rc, doc = _run_check_json(tmp_path, rendered, capsys)

    assert rc == 2
    message = _stage(doc, "hooks-preflight")["findings"][0]["message"]
    assert "validate_config returned bool" in message
    assert "(severity, message)" in message
    assert "WARN or ERROR" in message


def test_classify_hook_finding_unit_contract():
    """The classifier's own table, independent of the check pipeline."""
    assert deploy.classify_hook_finding("bare") == ("ERROR", "bare")
    assert deploy.classify_hook_finding(("WARN", "w")) == ("WARN", "w")
    assert deploy.classify_hook_finding(["error", "e"]) == ("ERROR", "e")
    # Non-string members are stringified, not rejected.
    assert deploy.classify_hook_finding(("WARN", 7)) == ("WARN", "7")
    assert deploy.classify_hook_finding(17) == ("ERROR", "17")
    with pytest.raises(ValueError, match="WARN or ERROR"):
        deploy.classify_hook_finding(("nope", "m"))


def test_check_stage9_skips_hooks_without_validate_config(tmp_path, capsys):
    _write(tmp_path / "infra/app/h.py", "def run(config, ctx):\n    return {}\n")
    rendered = {"infra/app": _hook_stack(tmp_path, "infra/app", {"pre_secrets": ["h.py"]})}

    rc, doc = _run_check_json(tmp_path, rendered, capsys)

    assert rc == 0
    stage = _stage(doc, "hooks-preflight")
    assert stage["status"] == "pass"
    assert "defines no validate_config" in stage["notes"][0]["message"]


def test_check_stage9_one_hooks_exception_does_not_abort_the_others(tmp_path, capsys):
    """O3/review-focus: a broken preflight is THAT hook's finding, nothing more."""
    _write(tmp_path / "infra/broken/h.py", """\
        def run(config, ctx):
            return {}

        def validate_config(config, ctx):
            raise ZeroDivisionError("preflight exploded")
    """)
    _write(tmp_path / "infra/healthy/h.py", """\
        def run(config, ctx):
            return {}

        def validate_config(config, ctx):
            return ["healthy stack still reported"]
    """)
    rendered = {
        "infra/broken": _hook_stack(tmp_path, "infra/broken", {"pre_compose": ["h.py"]}),
        "infra/healthy": _hook_stack(tmp_path, "infra/healthy", {"pre_compose": ["h.py"]}),
    }

    rc, doc = _run_check_json(tmp_path, rendered, capsys)

    assert rc == 2
    messages = {f["stack"]: f["message"] for f in _stage(doc, "hooks-preflight")["findings"]}
    assert "validate_config raised ZeroDivisionError: preflight exploded" == messages["infra/broken"]
    # The OTHER stack's preflight still ran and still reported.
    assert messages["infra/healthy"] == "healthy stack still reported"
    # And every later stage of the broken stack still ran too.
    assert _stage(doc, "compose-render")["status"] == "pass"


def test_check_stage9_rejects_a_boolean_return(tmp_path, capsys):
    """S9.5 returns list[str] — a bool must never be read as a verdict."""
    _write(tmp_path / "infra/app/h.py", """\
        def run(config, ctx):
            return {}

        def validate_config(config, ctx):
            return True
    """)
    rendered = {"infra/app": _hook_stack(tmp_path, "infra/app", {"pre_compose": ["h.py"]})}

    rc, doc = _run_check_json(tmp_path, rendered, capsys)

    assert rc == 2
    assert "returned bool" in _stage(doc, "hooks-preflight")["findings"][0]["message"]


def test_check_stage9_rejects_a_bare_string_return(tmp_path, capsys):
    """A str is iterable — treating it as a list would report one error per char."""
    _write(tmp_path / "infra/app/h.py", """\
        def run(config, ctx):
            return {}

        def validate_config(config, ctx):
            return "boom"
    """)
    rendered = {"infra/app": _hook_stack(tmp_path, "infra/app", {"pre_compose": ["h.py"]})}

    rc, doc = _run_check_json(tmp_path, rendered, capsys)

    assert rc == 2
    findings = _stage(doc, "hooks-preflight")["findings"]
    assert len(findings) == 1
    assert "returned str" in findings[0]["message"]


def test_check_stage9_treats_none_as_no_findings(tmp_path, capsys):
    _write(tmp_path / "infra/app/h.py", """\
        def run(config, ctx):
            return {}

        def validate_config(config, ctx):
            return None
    """)
    rendered = {"infra/app": _hook_stack(tmp_path, "infra/app", {"pre_compose": ["h.py"]})}

    rc, doc = _run_check_json(tmp_path, rendered, capsys)

    assert rc == 0
    assert "returned None" in _stage(doc, "hooks-preflight")["notes"][0]["message"]


def test_check_stage9_accepts_an_empty_list(tmp_path, capsys):
    _write(tmp_path / "infra/app/h.py", """\
        def run(config, ctx):
            return {}

        def validate_config(config, ctx):
            return []
    """)
    rendered = {"infra/app": _hook_stack(tmp_path, "infra/app", {"pre_compose": ["h.py"]})}

    rc, doc = _run_check_json(tmp_path, rendered, capsys)

    assert rc == 0
    assert _stage(doc, "hooks-preflight")["status"] == "pass"


def test_check_imports_each_hook_file_exactly_once_per_run(tmp_path, capsys):
    """O3: same file at three points AND in a second stack — one import total."""
    counter = tmp_path / "import-count"
    hook = _write(tmp_path / "infra/a/h.py", """\
        from pathlib import Path

        with Path(__file__).parents[2].joinpath("import-count").open("a") as fh:
            fh.write("import\\n")

        def run(config, ctx):
            raise AssertionError("run() must never execute during ciu check")

        def validate_config(config, ctx):
            return []
    """)
    rendered = {
        "infra/a": _hook_stack(tmp_path, "infra/a", {
            "pre_secrets": ["h.py"], "pre_compose": ["h.py"], "post_compose": ["h.py"],
        }),
        # A SECOND stack pointing at the SAME file by absolute path.
        "infra/b": _hook_stack(tmp_path, "infra/b", {"pre_compose": [str(hook)]}),
    }

    rc, _doc = _run_check_json(tmp_path, rendered, capsys)

    assert rc == 0
    assert counter.read_text(encoding="utf-8") == "import\n"


def test_check_suppresses_bytecode_writes_while_importing_hooks(tmp_path):
    """__pycache__ beside a hook file is still a write into the consumer's tree."""
    _write(tmp_path / "infra/app/h.py", "def run(config, ctx):\n    return {}\n")
    rendered = {"infra/app": _hook_stack(tmp_path, "infra/app", {"pre_compose": ["h.py"]})}

    # CIU-78: restore must be compared against the AMBIENT value, not a
    # hardcoded False — assay.toml's own declared gate environment sets
    # PYTHONDONTWRITEBYTECODE=1, so sys.dont_write_bytecode starts True in
    # the real gate.
    ambient = sys.dont_write_bytecode
    before = _tree_snapshot(tmp_path)
    assert deploy.action_check(tmp_path, _check_profile(), [{"path": "infra/app"}], rendered) == 0
    assert _tree_snapshot(tmp_path) == before
    assert sys.dont_write_bytecode is ambient  # restored to whatever it was


def test_check_restores_the_bytecode_flag_after_a_failed_import(tmp_path, capsys):
    _write(tmp_path / "infra/app/h.py", "raise RuntimeError('nope')\n")
    rendered = {"infra/app": _hook_stack(tmp_path, "infra/app", {"pre_compose": ["h.py"]})}

    # CIU-78: see note above — restore must match the ambient value.
    ambient = sys.dont_write_bytecode
    rc, _doc = _run_check_json(tmp_path, rendered, capsys)

    assert rc == 2
    assert sys.dont_write_bytecode is ambient


def test_check_ignores_malformed_hook_declarations(tmp_path, capsys):
    """A non-table `hooks`, or a non-list point, is not this stage's error."""
    rendered = {
        "infra/a": _min_stack(tmp_path, "infra/a", {"hooks": "nope"}, compose=_TRIVIAL_COMPOSE),
        "infra/b": _min_stack(tmp_path, "infra/b", {"hooks": {"pre_compose": "h.py"}},
                              compose=_TRIVIAL_COMPOSE),
    }

    rc, doc = _run_check_json(tmp_path, rendered, capsys)

    assert rc == 0
    assert _stage(doc, "hooks-load")["findings"] == []


def test_check_hook_context_mirrors_a_real_run_minus_secret_files(tmp_path, capsys):
    """The preflight ctx carries S3.12/CIU-41 identity + selection facts."""
    (tmp_path / "ciu.env").write_text(
        "INSTANCE_ID=inst-7\nDOCKER_NETWORK_INTERNAL=net-7\n", encoding="utf-8"
    )
    _write(tmp_path / "infra/app/h.py", """\
        def run(config, ctx):
            return {}

        def validate_config(config, ctx):
            errors = []
            if ctx.instance_id != "inst-7":
                errors.append(f"instance_id={ctx.instance_id!r}")
            if ctx.network != "net-7":
                errors.append(f"network={ctx.network!r}")
            if ctx.deployed_stacks != ("infra/app",):
                errors.append(f"deployed_stacks={ctx.deployed_stacks!r}")
            if ctx.selected_profiles != ():
                errors.append(f"selected_profiles={ctx.selected_profiles!r}")
            if ctx.point != "pre_compose":
                errors.append(f"point={ctx.point!r}")
            if ctx.stack_dir.name != "app":
                errors.append(f"stack_dir={ctx.stack_dir!r}")
            return errors
    """)
    rendered = {"infra/app": _hook_stack(tmp_path, "infra/app", {"pre_compose": ["h.py"]})}

    rc, doc = _run_check_json(tmp_path, rendered, capsys)

    assert rc == 0, _stage(doc, "hooks-preflight")["findings"]


def test_check_identity_survives_an_unreadable_ciu_env(tmp_path, monkeypatch, capsys):
    (tmp_path / "ciu.env").write_text("INSTANCE_ID=x\n", encoding="utf-8")
    monkeypatch.setattr(
        deploy, "parse_workspace_env",
        lambda _p: (_ for _ in ()).throw(OSError("permission denied")),
    )
    _write(tmp_path / "infra/app/h.py", """\
        def run(config, ctx):
            return {}

        def validate_config(config, ctx):
            return [] if ctx.instance_id is None else [f"leaked {ctx.instance_id}"]
    """)
    rendered = {"infra/app": _hook_stack(tmp_path, "infra/app", {"pre_compose": ["h.py"]})}

    rc, doc = _run_check_json(tmp_path, rendered, capsys)

    assert rc == 0, _stage(doc, "hooks-preflight")["findings"]


def test_check_identity_survives_a_non_utf8_ciu_env(tmp_path, capsys):
    """CIU-62 — a non-UTF-8 byte in ciu.env is `UnicodeDecodeError`, which is
    a `ValueError` subclass and therefore caught by NEITHER `OSError` nor
    `WorkspaceEnvError` (its sibling `ValueError` subclass). Before the fix
    this escaped `_workspace_identity` and crashed `ciu check` with a raw
    traceback; the documented contract is "absent or unreadable yields {}",
    i.e. the hook context's identity fields stay None. Written with a REAL
    undecodable file rather than a monkeypatched raiser, so it proves the
    exception type the shipped reader actually produces."""
    (tmp_path / "ciu.env").write_bytes(b'export INSTANCE_ID="\xff\xfe"\n')
    _write(tmp_path / "infra/app/h.py", """\
        def run(config, ctx):
            return {}

        def validate_config(config, ctx):
            return [] if ctx.instance_id is None else [f"leaked {ctx.instance_id}"]
    """)
    rendered = {"infra/app": _hook_stack(tmp_path, "infra/app", {"pre_compose": ["h.py"]})}

    rc, doc = _run_check_json(tmp_path, rendered, capsys)

    assert rc == 0, _stage(doc, "hooks-preflight")["findings"]


def test_check_identity_survives_a_malformed_ciu_env_entry(tmp_path, capsys):
    """CIU-62 sibling of the above: a malformed entry raises
    `WorkspaceEnvError`, the OTHER `ValueError` subclass — also degrades to
    the documented {}."""
    (tmp_path / "ciu.env").write_text('this is not = valid "shell\n', encoding="utf-8")
    _write(tmp_path / "infra/app/h.py", """\
        def run(config, ctx):
            return {}

        def validate_config(config, ctx):
            return [] if ctx.instance_id is None else [f"leaked {ctx.instance_id}"]
    """)
    rendered = {"infra/app": _hook_stack(tmp_path, "infra/app", {"pre_compose": ["h.py"]})}

    rc, doc = _run_check_json(tmp_path, rendered, capsys)

    assert rc == 0, _stage(doc, "hooks-preflight")["findings"]


def test_check_secret_file_callback_refuses_every_name(tmp_path):
    """The named design decision, pinned: KeyError for ANY name during check."""
    with pytest.raises(KeyError, match="unavailable during"):
        deploy._check_secret_file("api_key")


# ---------------------------------------------------------------------------
# Stages 10-12 — guarded compose render, leak scan, consumption cross-check
# ---------------------------------------------------------------------------


def test_check_stage10_render_aborts_when_a_template_stringifies_a_secret(tmp_path, capsys):
    """S4.21: the guard is what makes an in-memory render safe with no values."""
    rendered = {
        "infra/app": _min_stack(
            tmp_path, "infra/app",
            {"secrets": {"pw": "GEN_LOCAL:app/pw"}},
            compose="services:\n  app:\n    image: busybox\n"
                    "    environment:\n      - PW={{ app_stack.secrets.pw }}\n",
        )
    }

    rc, doc = _run_check_json(tmp_path, rendered, capsys)

    assert rc == 2
    message = _stage(doc, "compose-render")["findings"][0]["message"]
    assert "SecretLeakError" in message and "[S4.21]" in message


def test_check_stage10_reports_a_broken_compose_template(tmp_path, capsys):
    rendered = {
        "infra/app": _min_stack(
            tmp_path, "infra/app", {"app": {}},
            compose="services:\n  app:\n    image: {{ app_stack.app.missing.deep }}\n",
        )
    }

    rc, doc = _run_check_json(tmp_path, rendered, capsys)

    assert rc == 2
    assert _stage(doc, "compose-render")["status"] == "fail"
    # A failed render means no text to scan or cross-check — those stay clean.
    assert _stage(doc, "consumption")["status"] == "pass"


def test_check_stage10_falls_back_to_a_shipped_compose_file(tmp_path, capsys):
    stack_dir = tmp_path / "infra/app"
    stack_dir.mkdir(parents=True)
    _write(stack_dir / "docker-compose.yml", "services:\n  app:\n    image: busybox\n")
    rendered = {"infra/app": {"app_stack": {"app": {}}}}

    rc, doc = _run_check_json(tmp_path, rendered, capsys)

    assert rc == 0
    stage = _stage(doc, "compose-render")
    assert stage["findings"] == []
    assert not any("skipped: neither" in n["message"] for n in stage["notes"])


def test_check_stage10_notes_a_stack_with_no_compose_file_at_all(tmp_path, capsys):
    (tmp_path / "infra/app").mkdir(parents=True)
    rendered = {"infra/app": {"app_stack": {"app": {}}}}

    rc, doc = _run_check_json(tmp_path, rendered, capsys)

    assert rc == 0
    notes = [n["message"] for n in _stage(doc, "compose-render")["notes"]]
    assert any("skipped: neither" in n for n in notes)


def test_check_notes_a_stack_that_is_not_on_disk(tmp_path, capsys):
    """Absence is reported, never silently converted into a pass or a failure."""
    rendered = {"infra/absent": {"app_stack": {"app": {}}}}

    rc, doc = _run_check_json(tmp_path, rendered, capsys)

    assert rc == 0
    assert "not present on disk" in _stage(doc, "configfile")["notes"][0]["message"]


def test_check_stage12_fails_on_a_service_referencing_an_undeclared_secret(tmp_path, capsys):
    rendered = {
        "infra/app": _min_stack(
            tmp_path, "infra/app", {"app": {}},
            compose="services:\n  app:\n    image: busybox\n    secrets:\n      - ghost\n",
        )
    }

    rc, doc = _run_check_json(tmp_path, rendered, capsys)

    assert rc == 2
    assert "[S4.20]" in _stage(doc, "consumption")["findings"][0]["message"]


def test_check_stage12_unconsumed_secret_is_a_warning_not_a_failure(tmp_path, capsys):
    """Named decision: match engine.py Step 14's WARN precedent, never exit 2.

    `ciu up` only warns here, so failing the check would make it red where the
    real pipeline is green; and the check cannot render configfiles, so it
    cannot see the S5 consumption channel at all.
    """
    rendered = {
        "infra/app": _min_stack(
            tmp_path, "infra/app",
            {"secrets": {"unused": "GEN_LOCAL:app/unused"}},
            compose=_TRIVIAL_COMPOSE,
        )
    }

    rc, doc = _run_check_json(tmp_path, rendered, capsys)

    assert rc == 0
    stage = _stage(doc, "consumption")
    assert stage["status"] == "pass"
    assert stage["findings"] == []
    assert "[S4.20]" in stage["notes"][0]["message"]
    assert "unused" in stage["notes"][0]["message"]


def test_check_stage12_counts_the_hook_consumption_marker(tmp_path, capsys):
    rendered = {
        "infra/app": _min_stack(
            tmp_path, "infra/app",
            {"secrets": {"tok": {"directive": "GEN_LOCAL:app/tok", "consumed_by": "hook"}}},
            compose=_TRIVIAL_COMPOSE,
        )
    }

    rc, doc = _run_check_json(tmp_path, rendered, capsys)

    assert rc == 0
    assert _stage(doc, "consumption")["notes"] == []


# ---------------------------------------------------------------------------
# O2/O3 — `[service.*]` identity registry consistency lint (S3.15, ciu-P22)
# ---------------------------------------------------------------------------


def _service_check(repo_root, config, selection, capsys, **kwargs):
    """Run `action_check` in JSON mode against a hand-built config's
    `[service.*]` table and *selection* — decoupled from `rendered`
    (this lint reads only `profile.config['service']` and `selection`'s
    paths, per S3.15), returning ``(rc, document)``.
    """
    profile = _check_profile(config)
    rc = deploy.action_check(repo_root, profile, selection, {}, json_output=True, **kwargs)
    return rc, json.loads(capsys.readouterr().out)


def test_service_registry_lint_absent_registry_stage_not_entered(tmp_path, capsys):
    """O3: absent `[service.*]` -> the lint code path is not entered at all."""
    config = {"deploy": dict(_CHECK_GLOBAL["deploy"])}
    rc, doc = _service_check(tmp_path, config, [{"path": "infra/app"}], capsys)

    assert rc == 0
    stage = _stage(doc, "service-registry")
    assert stage["status"] == "pass"
    assert stage["notes"] == []
    assert stage["findings"] == []


def test_service_registry_lint_empty_registry_is_a_no_op(tmp_path, capsys):
    config = {"deploy": dict(_CHECK_GLOBAL["deploy"]), "service": {}}
    rc, doc = _service_check(tmp_path, config, [{"path": "infra/app"}], capsys)

    assert rc == 0
    assert _stage(doc, "service-registry")["notes"] == []


def test_service_registry_lint_registered_entry_not_deployed_warns(tmp_path, capsys):
    """Isolates direction 1 only: a SECOND, consistent registry entry
    ('consistent' / 'infra/other') absorbs the one deployed path so it
    cannot also trigger direction 2 ("deployed but unregistered") —
    proving direction 1 fires independently of direction 2."""
    config = {
        "deploy": dict(_CHECK_GLOBAL["deploy"]),
        "service": {
            "our_db_stack": {"type": "CIU", "location": "infra/db-core"},
            "consistent": {"type": "CIU", "location": "infra/other"},
        },
    }
    rc, doc = _service_check(tmp_path, config, [{"path": "infra/other"}], capsys)

    assert rc == 0
    stage = _stage(doc, "service-registry")
    assert stage["status"] == "pass"
    assert stage["findings"] == []
    assert len(stage["notes"]) == 1
    message = stage["notes"][0]["message"]
    assert "[WARN]" in message
    assert "our_db_stack" in message
    assert "infra/db-core" in message
    assert "consistent" not in message


def test_service_registry_lint_deployed_stack_not_registered_warns(tmp_path, capsys):
    config = {
        "deploy": dict(_CHECK_GLOBAL["deploy"]),
        "service": {"our_db_stack": {"type": "CIU", "location": "infra/db-core"}},
    }
    rc, doc = _service_check(
        tmp_path, config,
        [{"path": "infra/db-core"}, {"path": "infra/unregistered"}],
        capsys,
    )

    assert rc == 0
    stage = _stage(doc, "service-registry")
    assert stage["findings"] == []
    assert len(stage["notes"]) == 1
    message = stage["notes"][0]["message"]
    assert "[WARN]" in message
    assert "infra/unregistered" in message


def test_service_registry_lint_consistent_registry_and_deployment_no_warn(tmp_path, capsys):
    config = {
        "deploy": dict(_CHECK_GLOBAL["deploy"]),
        "service": {"our_db_stack": {"type": "CIU", "location": "infra/db-core"}},
    }
    rc, doc = _service_check(tmp_path, config, [{"path": "infra/db-core"}], capsys)

    assert rc == 0
    stage = _stage(doc, "service-registry")
    assert stage["status"] == "pass"
    assert stage["notes"] == []


def test_service_registry_lint_both_directions_independently(tmp_path, capsys):
    """Negative guard (O3): both directions must be checked and asserted
    independently, not merely 'some [WARN] substring appeared somewhere in
    stdout'."""
    config = {
        "deploy": dict(_CHECK_GLOBAL["deploy"]),
        "service": {
            "our_db_stack": {"type": "CIU", "location": "infra/db-core"},
            "orphan_registration": {"type": "CIU", "location": "infra/never-deployed"},
        },
    }
    rc, doc = _service_check(
        tmp_path, config,
        [{"path": "infra/db-core"}, {"path": "infra/unregistered"}],
        capsys,
    )

    assert rc == 0
    stage = _stage(doc, "service-registry")
    assert stage["findings"] == []
    messages = [n["message"] for n in stage["notes"]]
    assert len(messages) == 2
    assert any(
        "orphan_registration" in m and "infra/never-deployed" in m for m in messages
    )
    assert any("infra/unregistered" in m for m in messages)
    # The consistent pair (our_db_stack / infra/db-core) must NOT appear in
    # either warning — proving this is a genuine two-directional diff, not
    # "every registered location and every deployed path, unconditionally".
    assert not any("our_db_stack" in m for m in messages)
    assert not any("infra/db-core" in m for m in messages)


def test_service_registry_lint_never_fails_the_check(tmp_path, capsys):
    """O2 negative: never a refusal, never exit 2, even with two orphaned
    registrations and two unregistered deployments at once."""
    config = {
        "deploy": dict(_CHECK_GLOBAL["deploy"]),
        "service": {
            "orphan_a": {"type": "CIU", "location": "infra/orphan-a"},
            "orphan_b": {"type": "EXTERNAL"},
        },
    }
    rc, doc = _service_check(
        tmp_path, config,
        [{"path": "infra/unregistered_a"}, {"path": "infra/unregistered_b"}],
        capsys,
    )

    assert rc == 0
    assert doc["status"] == "pass"
    assert _stage(doc, "service-registry")["status"] == "pass"


def test_service_registry_lint_external_entry_without_location_never_warns(tmp_path, capsys):
    """EXTERNAL/IN_PROCESS entries have no `location` (S3.14 forbids it) —
    they must never participate in the "registered but not deployed"
    direction, since they were never eligible to be deployed at all."""
    config = {
        "deploy": dict(_CHECK_GLOBAL["deploy"]),
        "service": {"payment_api": {"type": "EXTERNAL"}},
    }
    rc, doc = _service_check(tmp_path, config, [{"path": "infra/app"}], capsys)

    assert rc == 0
    stage = _stage(doc, "service-registry")
    # Only the "deployed but unregistered" direction fires for infra/app;
    # payment_api (no location) never appears in it.
    assert len(stage["notes"]) == 1
    assert "payment_api" not in stage["notes"][0]["message"]
    assert "infra/app" in stage["notes"][0]["message"]


def test_service_registry_lint_prose_output_carries_the_warn_tag(tmp_path, capsys):
    config = {
        "deploy": dict(_CHECK_GLOBAL["deploy"]),
        "service": {"our_db_stack": {"type": "CIU", "location": "infra/db-core"}},
    }
    profile = _check_profile(config)

    rc = deploy.action_check(tmp_path, profile, [{"path": "infra/other"}], {})

    out = capsys.readouterr().out
    assert rc == 0
    assert "[WARN]" in out
    assert "our_db_stack" in out


# ---------------------------------------------------------------------------
# O4 — CLI surface, JSON envelope, exit-code discipline
# ---------------------------------------------------------------------------


def test_check_json_envelope_is_versioned_and_ordered(tmp_path, capsys):
    rendered = {"infra/app": _min_stack(tmp_path, "infra/app", {"app": {}},
                                        compose=_TRIVIAL_COMPOSE)}

    rc, doc = _run_check_json(tmp_path, rendered, capsys)

    assert rc == 0
    assert doc["schema_version"] == deploy.CHECK_SCHEMA_VERSION == 1
    assert doc["operation"] == "config-check"
    assert doc["status"] == "pass"
    assert doc["profile"] is None
    assert [s["stage"] for s in doc["stages"]] == list(deploy.CHECK_STAGES)
    assert all({"stage", "status", "findings", "notes"} <= set(s) for s in doc["stages"])
    # Stage 7 (registry) landed in ciu-P19 at the position ciu-P18 predicted.
    # This assertion was ciu-P18's forward marker for the gap ("registry" NOT
    # in CHECK_STAGES) and is flipped here by the package that closed it; its
    # positional contract is pinned in test_ciu_provisioning.py's
    # test_check_stage7_is_between_configfile_and_hooks_load.
    assert "registry" in deploy.CHECK_STAGES
    assert "live" not in doc  # no --live in this run


def test_check_json_output_writes_only_the_document(tmp_path, capsys):
    rendered = {"infra/app": _min_stack(tmp_path, "infra/app", {"app": {}},
                                        compose=_TRIVIAL_COMPOSE)}

    deploy.action_check(tmp_path, _check_profile(), [{"path": "infra/app"}], rendered,
                        json_output=True)

    out = capsys.readouterr().out
    assert out.lstrip().startswith("{")
    assert "[INFO]" not in out and "[SUCCESS]" not in out


def test_check_prose_output_lists_every_stage_with_its_verdict(tmp_path, capsys):
    _write(tmp_path / "infra/app/h.py", """\
        def run(config, ctx):
            return {}

        def validate_config(config, ctx):
            return ["a prose finding"]
    """)
    rendered = {"infra/app": _hook_stack(tmp_path, "infra/app", {"pre_compose": ["h.py"]})}

    rc = deploy.action_check(tmp_path, _check_profile(), [{"path": "infra/app"}], rendered)

    out = capsys.readouterr().out
    assert rc == 2
    for stage in deploy.CHECK_STAGES:
        assert f"{stage}: " in out
    assert "[x] hooks-preflight: fail" in out
    assert "a prose finding" in out
    assert "check failed: 1 finding(s)" in out
    assert "check passed" not in out


def test_check_json_reports_the_live_probe_verdict_separately(tmp_path, capsys, monkeypatch):
    """--live is the ONLY exit-1 class, so it is not a stage in the envelope."""
    monkeypatch.setattr(
        "ciu.provisioning.probe_ref",
        lambda ref, _c, _r, **_kw: provisioning.ProbeResult(ref, False, "absent"),
    )
    rendered = {
        "infra/db": _min_stack(tmp_path, "infra/db", {"provides": ["pg:db/app"]},
                               compose=_TRIVIAL_COMPOSE),
        "apps/api": _min_stack(tmp_path, "apps/api", {"requires": ["pg:db/app"]},
                               compose=_TRIVIAL_COMPOSE),
    }

    rc, doc = _run_check_json(tmp_path, rendered, capsys, live=True)

    assert rc == 1
    assert doc["status"] == "pass"          # every STATIC stage is clean
    assert doc["live"] == {"status": "fail", "unsatisfied": ["pg:db/app"]}


def test_check_json_records_a_passing_live_probe(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(
        "ciu.provisioning.probe_ref",
        lambda ref, _c, _r, **_kw: provisioning.ProbeResult(ref, True, "ok"),
    )
    rendered = {
        "infra/db": _min_stack(tmp_path, "infra/db", {"provides": ["pg:db/app"]},
                               compose=_TRIVIAL_COMPOSE),
        "apps/api": _min_stack(tmp_path, "apps/api", {"requires": ["pg:db/app"]},
                               compose=_TRIVIAL_COMPOSE),
    }

    rc, doc = _run_check_json(tmp_path, rendered, capsys, live=True)

    assert rc == 0
    assert doc["live"] == {"status": "pass", "unsatisfied": []}


def test_check_new_stage_failure_never_returns_1_even_with_live(tmp_path, monkeypatch, capsys):
    """Exit-code discipline (O4): 1 is reserved for --live probe failures."""
    monkeypatch.setattr(
        "ciu.provisioning.probe_ref",
        lambda *_a, **_kw: pytest.fail("a static failure must not reach the live probe"),
    )
    rendered = {
        "infra/app": _min_stack(
            tmp_path, "infra/app",
            {"requires": ["pg:db/app"], "secrets": {"pw": 123}},
            compose=_TRIVIAL_COMPOSE,
        ),
        "infra/db": _min_stack(tmp_path, "infra/db", {"provides": ["pg:db/app"]},
                               compose=_TRIVIAL_COMPOSE),
    }

    rc = deploy.action_check(
        tmp_path, _check_profile(), [{"path": r} for r in rendered], rendered, live=True
    )

    assert rc == 2
    assert "check failed" in capsys.readouterr().out


def test_check_graph_lint_failure_is_still_exit_2(tmp_path, capsys):
    rendered = {
        "apps/api": _min_stack(tmp_path, "apps/api", {"requires": ["pg:db/nobody"]},
                               compose=_TRIVIAL_COMPOSE),
    }

    rc, doc = _run_check_json(tmp_path, rendered, capsys)

    assert rc == 2
    prov = _stage(doc, "provisioning")
    assert prov["status"] == "fail"
    assert "stack" not in prov["findings"][0]  # graph-level, not per-stack


def test_check_global_declared_feature_failure_short_circuits(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(
        deploy.config_model, "validate_declared_features",
        lambda *_a, **_kw: (_ for _ in ()).throw(ValueError("[S16.7] bad exec target")),
    )
    monkeypatch.setattr(
        deploy.config_model, "validate_stack_shape",
        lambda *_a, **_kw: pytest.fail("a bad global config must not be walked per-stack"),
    )
    rendered = {"infra/app": {"app_stack": {"app": {}}}}

    rc, doc = _run_check_json(tmp_path, rendered, capsys)

    assert rc == 2
    assert "[S16.7]" in _stage(doc, "shape")["findings"][0]["message"]


def test_check_global_declared_feature_failure_in_prose_mode(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(
        deploy.config_model, "validate_declared_features",
        lambda *_a, **_kw: (_ for _ in ()).throw(deploy.worktree_pkg.WorktreeError("bad tree")),
    )

    rc = deploy.action_check(tmp_path, _check_profile(), [], {})

    assert rc == 2
    assert "bad tree" in capsys.readouterr().out


def test_check_json_flag_reaches_action_check_from_the_cli(tmp_path, monkeypatch):
    """`ciu check --json` (via ciu-deploy --check --json) wires json_output."""
    seen: dict = {}

    def fake_check(_root, _profile, _selection, _rendered, *, live, json_output):
        seen["live"] = live
        seen["json_output"] = json_output
        return 0

    monkeypatch.setattr(deploy, "action_check", fake_check)
    args = deploy.parse_args(["--check", "--json"])
    assert args.json_output is True

    monkeypatch.setattr(deploy, "render_selected_stacks", lambda *_a, **_kw: {})
    profile = _check_profile()
    monkeypatch.setattr(deploy, "load_global_config", lambda *_a, **_kw: profile.config)
    monkeypatch.setattr(deploy, "resolve_profiles", lambda *_a, **_kw: profile)
    monkeypatch.setattr(deploy, "build_selection", lambda *_a, **_kw: [])
    monkeypatch.setattr(deploy, "resolve_repo_root", lambda *_a, **_kw: tmp_path)
    monkeypatch.setattr(deploy, "bootstrap_workspace_env", lambda **_kw: None)
    monkeypatch.setattr(deploy, "enforce_standalone_root", lambda *_a, **_kw: None)

    assert deploy.main(["--check", "--json"]) == 0
    assert seen == {"live": False, "json_output": True}


# ---------------------------------------------------------------------------
# Render-context fidelity: engine Steps 7 and 8's PURE halves, no side effects
# ---------------------------------------------------------------------------


_SHARED_ENV = {"CONTAINER_UID": "1000", "CONTAINER_GID": "1000", "DOCKER_GID": "999"}


def _uid_profile() -> Profile:
    return _check_profile({
        "deploy": {"project_name": "p", "environment_tag": "t",
                   "env": {"shared": dict(_SHARED_ENV)}},
    })


def test_check_render_sees_auto_generated_and_resolved_hostdirs(tmp_path, capsys):
    """A template reading `auto_generated.*` and a hostdir path must render.

    Both are supplied by pipeline steps `ciu check` does not run (Step 7
    auto-generate, Step 8 hostdir creation). Their PURE halves run here so the
    render stage is faithful — without ever creating the directory.
    """
    stack_dir = tmp_path / "infra/app"
    stack_dir.mkdir(parents=True)
    _write(stack_dir / "ciu.compose.yml.j2", """\
        services:
          app:
            image: busybox
            user: "{{ auto_generated.uid }}:{{ auto_generated.gid }}"
            volumes:
              - {{ app_stack.app.hostdir.logs }}:/var/log/app
              - {{ app_stack.app.hostdir.data }}:/data
              - {{ app_stack.app.hostdir.given }}:/given
              - {{ app_stack.sidecars[0].hostdir.cache }}:/cache
    """)
    rendered = {"infra/app": {"app_stack": {
        "app": {
            "name": "app",
            "hostdir": {
                "logs": "",                                  # auto name
                "data": {"path": "", "uid": 70, "mode": "0770"},  # auto, table form
                "given": "vol-explicit",                     # relative override
            },
        },
        "sidecars": [{"name": "side", "hostdir": {"cache": "/abs/cache"}}],
    }}}

    before = _tree_snapshot(tmp_path)
    rc, doc = _run_check_json(tmp_path, rendered, capsys, profile=_uid_profile())
    assert _tree_snapshot(tmp_path) == before  # nothing created

    assert rc == 0, _stage(doc, "compose-render")["findings"]
    assert _stage(doc, "compose-render")["notes"] == []
    assert not (stack_dir / "vol-app-logs").exists()
    assert not (stack_dir / "vol-app-data").exists()


def test_check_hostdir_resolution_uses_the_physical_root_when_available(tmp_path, monkeypatch):
    """S1.4 translation is applied when PHYSICAL_REPO_ROOT is in scope."""
    monkeypatch.setenv("PHYSICAL_REPO_ROOT", "/physical/root")
    config = {"app": {"name": "app", "hostdir": {"logs": ""}}}

    deploy._resolve_hostdirs_for_render(config, tmp_path / "infra/app", tmp_path)

    assert config["app"]["hostdir"]["logs"] == "/physical/root/infra/app/vol-app-logs"


def test_check_hostdir_resolution_falls_back_to_the_logical_path(tmp_path, monkeypatch):
    """No PHYSICAL_REPO_ROOT → the logical path, never a crash."""
    monkeypatch.delenv("PHYSICAL_REPO_ROOT", raising=False)
    config = {"app": {"name": "app", "hostdir": {"logs": ""}}}

    deploy._resolve_hostdirs_for_render(config, tmp_path / "infra/app", tmp_path)

    assert config["app"]["hostdir"]["logs"] == str(tmp_path / "infra/app/vol-app-logs")


def test_check_hostdir_resolution_leaves_unresolvable_declarations_alone(tmp_path):
    """Shape defects are Step 8's error to name — `ciu check` has no S6 stage."""
    config = {
        "svc": {"hostdir": {"nameless": "", "wrong_type": 7,
                            "table_nameless": {"uid": 1}}},
        "other": {"hostdir": "not-a-table"},
    }

    deploy._resolve_hostdirs_for_render(config, tmp_path, tmp_path)

    assert config["svc"]["hostdir"] == {"nameless": "", "wrong_type": 7,
                                        "table_nameless": {"uid": 1}}
    assert config["other"]["hostdir"] == "not-a-table"


def test_check_notes_when_auto_generated_values_are_unavailable(tmp_path, capsys):
    """A missing CONTAINER_UID is an S2 bootstrap problem — a note, not exit 2."""
    rendered = {"infra/app": _min_stack(tmp_path, "infra/app", {"app": {}},
                                        compose=_TRIVIAL_COMPOSE)}

    rc, doc = _run_check_json(tmp_path, rendered, capsys)

    assert rc == 0
    stage = _stage(doc, "compose-render")
    assert stage["status"] == "pass"
    assert "auto_generated values unavailable" in stage["notes"][0]["message"]


def test_check_auto_generate_runs_when_the_shared_env_is_present(tmp_path, capsys):
    stack_dir = tmp_path / "infra/app"
    stack_dir.mkdir(parents=True)
    _write(stack_dir / "ciu.compose.yml.j2",
           "services:\n  app:\n    image: busybox\n"
           "    labels:\n      - uid={{ auto_generated.uid }}\n")
    rendered = {"infra/app": {"app_stack": {"app": {}}}}

    rc = deploy.action_check(tmp_path, _uid_profile(), [{"path": "infra/app"}],
                             rendered, json_output=True)
    doc = json.loads(capsys.readouterr().out)

    assert rc == 0
    assert _stage(doc, "compose-render")["notes"] == []


def test_check_reports_unparseable_compose_without_aborting_the_run(tmp_path, capsys):
    """A YAMLError out of validate_consumption must be a finding, not a crash."""
    rendered = {
        "infra/broken": _min_stack(tmp_path, "infra/broken", {"app": {}},
                                   compose="services: [unterminated\n"),
        "infra/ok": _min_stack(tmp_path, "infra/ok", {"app": {}},
                               compose=_TRIVIAL_COMPOSE),
    }

    rc, doc = _run_check_json(tmp_path, rendered, capsys)

    assert rc == 2
    finding = _stage(doc, "consumption")["findings"][0]
    assert finding["stack"] == "infra/broken"
    assert "not parseable YAML" in finding["message"]
    # The healthy stack was still checked — one bad render does not end the run.
    assert _stage(doc, "compose-render")["status"] == "pass"
