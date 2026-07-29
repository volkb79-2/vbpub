"""Configfile secret-resolution rejection contracts."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import engine


def _setup(monkeypatch, tmp_path: Path, materialized: dict):
    stack = tmp_path / "stack"
    stack.mkdir()
    monkeypatch.setattr(engine, "check_runtime_dependencies", lambda: None)
    monkeypatch.setattr(engine, "bootstrap_workspace_env", lambda **_kwargs: None)
    monkeypatch.setattr(engine, "enforce_standalone_root", lambda _path: None)
    monkeypatch.setattr(engine.config_model, "render_global_chain", lambda *_args: {})
    monkeypatch.setattr(engine, "configure_logging", lambda _level: None)
    monkeypatch.setattr(engine.config_model, "render_stack", lambda *_args, **_kwargs: {"demo": {}})
    monkeypatch.setattr(engine, "ensure_workspace_network", lambda **_kwargs: None)
    monkeypatch.setattr(engine.config_model, "deep_merge", lambda *_args: {"demo": {}})
    monkeypatch.setattr(engine.config_model, "validate_stack_shape", lambda _config: "demo")
    monkeypatch.setattr(engine.secret_directives, "discover", lambda *_args: [])
    monkeypatch.setattr(engine.secret_directives, "find_misplaced", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(engine, "_check_gitignore", lambda _path: None)
    monkeypatch.setattr(engine, "auto_generate_values", lambda config: config)
    monkeypatch.setattr(engine, "create_hostdirs", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(engine, "_dood_preflight", lambda *_args: None)
    monkeypatch.setattr(engine, "to_physical_path", lambda path, **_kwargs: path)
    monkeypatch.setattr(engine.secret_materialize, "materialize", lambda *_args, **_kwargs: materialized)
    monkeypatch.delenv("REPO_ROOT", raising=False)
    return stack


def test_configfile_rejects_embedding_ask_file_secret(monkeypatch, tmp_path: Path):
    """ASK_FILE values remain referenced by path and can never leak into rendered text."""

    stack = _setup(monkeypatch, tmp_path, {"tls_key": SimpleNamespace(value=None)})

    def render_configfiles(_stack, _root, _merged, secret_value):
        return secret_value("tls_key")

    monkeypatch.setattr(engine.composefile, "render_configfiles", render_configfiles)

    with pytest.raises(ValueError, match=r"\[S5\.4\].*ASK_FILE"):
        engine.main_execution(stack, define_root=tmp_path)


def test_configfile_rejects_undeclared_secret_reference(monkeypatch, tmp_path: Path):
    """A template cannot obtain an undeclared secret by receiving an empty fallback."""

    stack = _setup(monkeypatch, tmp_path, {})

    def render_configfiles(_stack, _root, _merged, secret_value):
        return secret_value("invented")

    monkeypatch.setattr(engine.composefile, "render_configfiles", render_configfiles)

    with pytest.raises(ValueError, match=r"\[S5\.4\].*invented.*not materialized"):
        engine.main_execution(stack, define_root=tmp_path)
