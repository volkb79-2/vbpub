"""Engine hook secret-file resolution failure contract."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import engine  # noqa: E402
from ciu.secrets.materialize import project_store  # noqa: E402


def test_hook_secret_lookup_denies_unknown_name_before_secret_materialization(
    tmp_path, monkeypatch, capsys
):
    """S4.9/S9.3: hooks can resolve declared stores, never an invented secret path.

    A declared ``GEN_LOCAL`` secret belongs in the repository-level store.  An
    undeclared name must raise ``KeyError`` rather than yield a writable path;
    the hook failure is red and the pipeline cannot materialize or deploy.
    """
    stack = tmp_path / "stack"
    stack.mkdir()
    spec = SimpleNamespace(name="shared", kind="GEN_LOCAL", locator="team/token")
    merged = {
        "deploy": {"project_name": "demo", "environment_tag": "test"},
        "demo": {"hooks": {"pre_secrets": ["secret-check"]}},
    }

    monkeypatch.setattr(engine, "check_runtime_dependencies", lambda: None)
    monkeypatch.setattr(engine, "bootstrap_workspace_env", lambda **_kwargs: None)
    monkeypatch.setattr(engine, "enforce_standalone_root", lambda _path: None)
    monkeypatch.setattr(engine.config_model, "render_global_chain", lambda *_args: {})
    monkeypatch.setattr(engine, "configure_logging", lambda *_args: None)
    monkeypatch.setattr(engine.config_model, "render_stack", lambda *_args, **_kwargs: {"demo": {}})
    monkeypatch.setattr(engine, "ensure_workspace_network", lambda **_kwargs: None)
    monkeypatch.setattr(engine.config_model, "deep_merge", lambda *_args: merged)
    monkeypatch.setattr(engine.config_model, "validate_stack_shape", lambda _stack: "demo")
    monkeypatch.setattr(engine.secret_directives, "discover", lambda *_args: [spec])
    monkeypatch.setattr(engine.secret_directives, "find_misplaced", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(engine, "_check_gitignore", lambda _path: None)
    monkeypatch.setattr(engine, "auto_generate_values", lambda config: config)
    monkeypatch.setattr(engine, "create_hostdirs", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(engine, "_dood_preflight", lambda *_args: None)
    monkeypatch.setattr(engine, "to_physical_path", lambda path, **_kwargs: path)

    def secret_check(_hooks, point, _config, ctx, _stack_toml):
        assert point == "pre_secrets"
        assert ctx.secret_file("shared") == project_store(tmp_path) / "team/token"
        with pytest.raises(KeyError, match="missing"):
            ctx.secret_file("missing")
        raise engine.hooks_runner.HookExecutionError("unknown secret denied")

    monkeypatch.setattr(engine.hooks_runner, "run_hooks", secret_check)
    monkeypatch.setattr(
        engine.secret_materialize,
        "materialize",
        lambda *_args, **_kwargs: pytest.fail("failed secret lookup must not materialize secrets"),
    )

    assert engine.main(["--dry-run", "-d", str(stack), "--define-root", str(tmp_path)]) == 1
    assert "unknown secret denied" in capsys.readouterr().out
