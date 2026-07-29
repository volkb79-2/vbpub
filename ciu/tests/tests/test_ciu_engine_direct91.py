"""Engine secrets-stage skip and exposure-notice contracts."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import engine


def _setup_before_secrets(monkeypatch, tmp_path: Path, specs):
    stack = tmp_path / "stack"
    stack.mkdir()
    merged = {"demo": {}}
    monkeypatch.setattr(engine, "check_runtime_dependencies", lambda: None)
    monkeypatch.setattr(engine, "bootstrap_workspace_env", lambda **_kwargs: None)
    monkeypatch.setattr(engine, "enforce_standalone_root", lambda _path: None)
    monkeypatch.setattr(engine.config_model, "render_global_chain", lambda *_args: {})
    monkeypatch.setattr(engine, "configure_logging", lambda _level: None)
    monkeypatch.setattr(engine.config_model, "render_stack", lambda *_args, **_kwargs: {"demo": {}})
    monkeypatch.setattr(engine, "ensure_workspace_network", lambda **_kwargs: None)
    monkeypatch.setattr(engine.config_model, "deep_merge", lambda *_args: merged)
    monkeypatch.setattr(engine.config_model, "validate_stack_shape", lambda _config: "demo")
    monkeypatch.setattr(engine.secret_directives, "discover", lambda *_args: specs)
    monkeypatch.setattr(engine.secret_directives, "find_misplaced", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(engine, "_check_gitignore", lambda _path: None)
    monkeypatch.setattr(engine, "auto_generate_values", lambda config: config)
    monkeypatch.setattr(engine, "create_hostdirs", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(engine, "_dood_preflight", lambda *_args: None)
    monkeypatch.setattr(engine, "to_physical_path", lambda path, **_kwargs: path)
    monkeypatch.setattr(engine.composefile, "render_configfiles", lambda *_args: (_ for _ in ()).throw(RuntimeError("stop at configfiles")))
    monkeypatch.delenv("REPO_ROOT", raising=False)
    return stack


def test_main_execution_skip_secrets_bypasses_materialization(monkeypatch, tmp_path: Path, capsys):
    """Break-glass skip must not instantiate a Vault client or materialize stores."""

    stack = _setup_before_secrets(monkeypatch, tmp_path, [])
    monkeypatch.setattr(
        engine.secret_materialize,
        "materialize",
        lambda *_args, **_kwargs: pytest.fail("--skip-secrets must not materialize"),
    )

    with pytest.raises(RuntimeError, match="stop at configfiles"):
        engine.main_execution(stack, define_root=tmp_path, skip_secrets=True)

    assert "--skip-secrets: skipping materialization and overlay" in capsys.readouterr().out


def test_main_execution_notices_environment_exposed_secret(monkeypatch, tmp_path: Path, capsys):
    """The discouraged environment escape hatch is always conspicuous to operators."""

    spec = SimpleNamespace(name="api_token", kind="GEN_LOCAL", locator="token", expose_env="API_TOKEN")
    stack = _setup_before_secrets(monkeypatch, tmp_path, [spec])
    monkeypatch.setattr(
        engine.secret_materialize,
        "materialize",
        lambda *_args, **_kwargs: {"api_token": SimpleNamespace(value="secret")},
    )

    with pytest.raises(RuntimeError, match="stop at configfiles"):
        engine.main_execution(stack, define_root=tmp_path)

    output = capsys.readouterr().out
    assert "api_token" in output
    assert "${API_TOKEN}" in output
    assert "discouraged escape hatch" in output
