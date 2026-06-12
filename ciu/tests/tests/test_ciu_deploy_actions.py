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
    # selection, and no token resolves → abort (S7.6).
    config = {
        **_vault_topology(),
        "deploy": {"project_name": "p", "environment_tag": "t", "phases": {}},
        "vault": {"stack_path": "infra/vault-core"},
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
    err = deploy.vault_preflight(tmp_path, profile, selection, rendered)
    assert err is not None
    assert "[S7.6]" in err


def test_vault_preflight_passes_when_vault_stack_precedes(monkeypatch, tmp_path):
    # Vault stack in phase_1, vault-consuming app in phase_2 → ordering satisfied
    # even with NO token (S7.6).
    config = {
        **_vault_topology(),
        "deploy": {"project_name": "p", "environment_tag": "t", "phases": {}},
        "vault": {"stack_path": "infra/vault-core"},
    }
    profile = Profile(name=None, phase_keys=None, config=config)
    selection = [
        {
            "phase_num": 1,
            "phase_key": "phase_1",
            "path": "infra/vault-core",
            "name": "vault",
            "service": {"path": "infra/vault-core", "name": "vault", "enabled": True},
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
        "infra/vault-core": {"vault_core": {"name": "vault"}},
        "applications/app": {"app": {"secrets": {"db_password": "ASK_VAULT:secret/db"}}},
    }

    # No token available, but ordering alone must satisfy the gate.
    monkeypatch.setattr(deploy, "resolve_vault_token", lambda cfg, root: None)
    err = deploy.vault_preflight(tmp_path, profile, selection, rendered)
    assert err is None


def test_vault_preflight_passes_with_token(monkeypatch, tmp_path):
    # No vault stack in the selection, but a token resolves → gate passes (S7.6/S4.16).
    config = {
        **_vault_topology(),
        "deploy": {"project_name": "p", "environment_tag": "t", "phases": {}},
        "vault": {"stack_path": "infra/vault-core"},
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
    err = deploy.vault_preflight(tmp_path, profile, selection, rendered)
    assert err is None


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
    assert deploy.vault_preflight(tmp_path, profile, selection, rendered) is None


def test_vault_preflight_flags_misplaced_directive(tmp_path):
    # A directive string OUTSIDE a secrets table is a violation (S4.5).
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
    err = deploy.vault_preflight(tmp_path, profile, selection, rendered)
    assert err is not None
    assert "S4.5" in err or "S4.1" in err


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
