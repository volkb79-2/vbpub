"""Public deploy rejection for malformed rendered stack configuration."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import deploy  # noqa: E402
from ciu.deploy_pkg.profiles import Profile  # noqa: E402


def test_deploy_rejects_invalid_rendered_stack_before_compose_dispatch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """S3.5 configuration corruption cannot bypass preflight and reach Compose."""
    profile = Profile(
        name=None,
        phase_keys=None,
        config={"deploy": {"project_name": "ciu", "environment_tag": "test", "phases": {}}},
    )
    selection = [{"path": "applications/api", "service": {"enabled": True}}]
    monkeypatch.setattr(deploy, "bootstrap_workspace_env", lambda **_kwargs: None)
    monkeypatch.setattr(deploy, "enforce_standalone_root", lambda _cwd: None)
    monkeypatch.setattr(deploy, "resolve_repo_root", lambda _root: tmp_path)
    monkeypatch.setattr(deploy, "load_global_config", lambda _root: profile.config)
    monkeypatch.setattr(deploy, "resolve_profiles", lambda _config, _names: profile)
    monkeypatch.setattr(deploy, "build_selection", lambda _profile, _phases: selection)
    monkeypatch.setattr(
        deploy,
        "render_selected_stacks",
        lambda *_args: {"applications/api": {"state": {}}},
    )
    monkeypatch.setattr(deploy, "vault_preflight", lambda *_args: None)
    monkeypatch.setattr(deploy, "registry_preflight", lambda *_args: None)
    monkeypatch.setattr(deploy, "governance_slice_preflight", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(deploy, "ensure_workspace_network", lambda **_kwargs: None)
    monkeypatch.setattr(
        deploy,
        "action_deploy",
        lambda *_args, **_kwargs: pytest.fail("invalid rendered config must not reach Compose dispatch"),
    )

    assert deploy.main(["--deploy"]) == 2
    assert "[S3.5] Stack config has no non-reserved top-level key" in capsys.readouterr().out
