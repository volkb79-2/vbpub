"""Public render-action failure boundary for :mod:`ciu.deploy`."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import deploy  # noqa: E402
from ciu.deploy_pkg.profiles import Profile  # noqa: E402


def test_render_validation_failure_is_red_before_subsequent_clean_action(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A malformed template cannot be presented as a render success or reach cleanup."""
    profile = Profile(
        name=None,
        phase_keys=None,
        config={"deploy": {"project_name": "ciu", "environment_tag": "test", "phases": {}}},
    )
    monkeypatch.setattr(deploy, "bootstrap_workspace_env", lambda **_kwargs: None)
    monkeypatch.setattr(deploy, "enforce_standalone_root", lambda _cwd: None)
    monkeypatch.setattr(deploy, "resolve_repo_root", lambda _root: tmp_path)
    monkeypatch.setattr(deploy, "load_global_config", lambda _root: profile.config)
    monkeypatch.setattr(deploy, "resolve_profiles", lambda _config, _names: profile)
    monkeypatch.setattr(deploy, "build_selection", lambda _profile, _phases: [])

    def fail_render(*_args: object, **_kw: object) -> dict:
        raise ValueError("[S3.2] invalid unresolved template value")

    monkeypatch.setattr(deploy, "render_selected_stacks", fail_render)
    monkeypatch.setattr(
        deploy,
        "action_clean",
        lambda *_args, **_kwargs: pytest.fail("clean must not follow a failed render"),
    )

    assert deploy.main(["--render-toml", "--clean"]) == 2
    output = capsys.readouterr().out
    assert "[S3.2] invalid unresolved template value" in output
    assert "Rendered 0 stack config(s)" not in output
