"""Hook secret-file paths for explicit-file and stack-store directives."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import engine


def test_pre_secret_hook_resolves_relative_ask_file_and_stack_store_paths(monkeypatch, tmp_path: Path):
    """Hooks receive only the directive's real path, never an invented secret location."""

    stack = tmp_path / "stack"
    stack.mkdir()
    ask_file = SimpleNamespace(name="certificate", kind="ASK_FILE", locator="certs/tls.pem")
    generated = SimpleNamespace(name="database", kind="GEN_TO_VAULT", locator="ignored")
    merged = {"demo": {"hooks": {"pre_secrets": ["check-secret-files"]}}}

    monkeypatch.setattr(engine, "check_runtime_dependencies", lambda: None)
    monkeypatch.setattr(engine, "bootstrap_workspace_env", lambda **_kwargs: None)
    monkeypatch.setattr(engine, "enforce_standalone_root", lambda _path: None)
    monkeypatch.setattr(engine.config_model, "render_global_chain", lambda *_args: {})
    monkeypatch.setattr(engine, "configure_logging", lambda _level: None)
    monkeypatch.setattr(engine.config_model, "render_stack", lambda *_args, **_kwargs: {"demo": {}})
    monkeypatch.setattr(engine, "ensure_workspace_network", lambda **_kwargs: None)
    monkeypatch.setattr(engine.config_model, "deep_merge", lambda *_args: merged)
    monkeypatch.setattr(engine.config_model, "validate_stack_shape", lambda _config: "demo")
    monkeypatch.setattr(engine.secret_directives, "discover", lambda *_args: [ask_file, generated])
    monkeypatch.setattr(engine.secret_directives, "find_misplaced", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(engine, "_check_gitignore", lambda _path: None)
    monkeypatch.setattr(engine, "auto_generate_values", lambda config: config)
    monkeypatch.setattr(engine, "create_hostdirs", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(engine, "_dood_preflight", lambda *_args: None)
    monkeypatch.setattr(engine, "to_physical_path", lambda path, **_kwargs: path)
    monkeypatch.setattr(engine.secret_materialize, "stack_store", lambda workdir: workdir / ".ciu" / "secrets")

    def check_secret_files(_hooks, point, _config, ctx, _stack_toml):
        assert point == "pre_secrets"
        assert ctx.secret_file("certificate") == stack / "certs/tls.pem"
        assert ctx.secret_file("database") == stack / ".ciu" / "secrets" / "database"
        raise engine.hooks_runner.HookExecutionError("stop after path checks")

    monkeypatch.setattr(engine.hooks_runner, "run_hooks", check_secret_files)
    monkeypatch.setattr(
        engine.secret_materialize,
        "materialize",
        lambda *_args, **_kwargs: pytest.fail("hook failure must stop before materialization"),
    )
    monkeypatch.delenv("REPO_ROOT", raising=False)

    with pytest.raises(engine.hooks_runner.HookExecutionError, match="stop after path checks"):
        engine.main_execution(stack, define_root=tmp_path)
