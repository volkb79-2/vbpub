"""Focused deploy action contracts for no-work and service environment paths."""

from __future__ import annotations

from pathlib import Path

from ciu import deploy
from ciu.deploy_pkg.profiles import Profile


def test_action_deploy_empty_selection_is_successful_noop(monkeypatch, tmp_path: Path, capsys):
    """No selected phases must not construct an environment or call a runner."""

    monkeypatch.setattr(deploy, "profile_env", lambda _profile: (_ for _ in ()).throw(AssertionError()))

    assert deploy.action_deploy(
        tmp_path,
        Profile(),
        [],
        dry_run=False,
        ignore_errors=False,
        health_after_phase=False,
        update_cert_permission=False,
    ) == 0

    assert "No phases/stacks selected to deploy" in capsys.readouterr().out


def test_action_deploy_stringifies_service_environment_overrides(monkeypatch, tmp_path: Path):
    """Compose receives a string-only environment after service overrides merge."""

    profile = Profile(
        config={
            "deploy": {
                "phases": {
                    "phase_1": {
                        "services": [
                            {
                                "path": "apps/api",
                                "name": "api",
                                "enabled": True,
                                "env_overrides": {"PORT": 8080, "DEBUG": False},
                            }
                        ]
                    }
                }
            }
        }
    )
    received: list[dict[str, str]] = []

    def run_stack(_stack_dir, *, env, **_kwargs):
        received.append(env)
        return True

    monkeypatch.setattr(deploy, "_run_stack", run_stack)
    monkeypatch.setattr(deploy, "profile_env", lambda _profile: {"BASE": "kept"})

    assert deploy.action_deploy(
        tmp_path,
        profile,
        deploy.build_selection(profile),
        dry_run=False,
        ignore_errors=False,
        health_after_phase=False,
        update_cert_permission=False,
    ) == 0

    assert received == [{"BASE": "kept", "PORT": "8080", "DEBUG": "False"}]
