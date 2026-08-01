"""Vault-preflight ordering contracts for branch-coverage healing."""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import deploy  # noqa: E402
from ciu.deploy_pkg.profiles import Profile  # noqa: E402


def _entry(path: str, phase_num: int) -> dict:
    return {
        "phase_num": phase_num,
        "phase_key": f"phase_{phase_num}",
        "path": path,
        "name": Path(path).name,
        "service": {"path": path, "name": Path(path).name, "enabled": True},
    }


def test_vault_preflight_keeps_first_secret_phase_while_scanning_non_vault_stack(
    monkeypatch, tmp_path: Path
) -> None:
    """Later vault consumers cannot undo an earlier vault-before-app plan."""
    profile = Profile(
        name=None,
        phase_keys=None,
        config={
            "deploy": {"project_name": "ciu", "environment_tag": "test", "phases": {}},
            "vault": {"stack_path": "infra/vault"},
        },
    )
    selection = [
        _entry("applications/first", 2),
        _entry("applications/later", 3),
        _entry("infra/vault", 1),
        _entry("infra/vault-replica", 2),
    ]
    rendered = {
        "applications/first": {"app": {}},
        "applications/later": {"app": {}},
        "infra/vault": {"vault_core": {}},
        "infra/vault-replica": {"vault_replica": {}},
    }

    def discover(root_key, merged):
        return [SimpleNamespace(kind="ASK_VAULT")] if root_key == "app" else []

    monkeypatch.setattr(deploy.secret_directives, "discover", discover)
    monkeypatch.setattr(
        deploy,
        "resolve_vault_token",
        lambda *_args: (_ for _ in ()).throw(AssertionError("ordered vault must not need a token")),
    )

    deploy.vault_preflight(tmp_path, profile, selection, rendered)
