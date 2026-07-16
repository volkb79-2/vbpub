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
    assert selection[-1]["phase_key"] == deploy.EXTRA_STACKS_KEY


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
        tmp_path, profile, deploy.build_selection(profile)
    )

    assert targets == ["p-t-postgres", "p-t-minio"]
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
        tmp_path, profile, deploy.build_selection(profile)
    )

    assert targets == ["p-t-always", "p-t-debug", "p-t-metrics"]


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
            tmp_path, profile, deploy.build_selection(profile)
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
                        lambda cfg: ["proj-env-vault-data"])

    rc = deploy.action_clean(tmp_path, profile, [], ignore_errors=True)
    assert rc == 1


def test_action_clean_invariant_passes_when_clean(monkeypatch, tmp_path):
    config = _teardown_config()
    profile = MagicMock()
    profile.config = config
    monkeypatch.setattr(deploy, "render_selected_stacks", lambda *a, **k: {})
    monkeypatch.setattr(deploy, "_matching_containers", lambda *a, **k: [])
    monkeypatch.setattr(deploy, "_remove_project_volumes", lambda cfg: [])
    rc = deploy.action_clean(tmp_path, profile, [], ignore_errors=True)
    assert rc == 0


# ---------------------------------------------------------------------------
# Seam 4 — --profile repeatable + comma form (§8 AC#7)
# ---------------------------------------------------------------------------

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
