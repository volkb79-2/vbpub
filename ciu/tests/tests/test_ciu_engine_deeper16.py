"""Engine hook-readiness malformed-inspect failure contract."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import engine  # noqa: E402


def test_pre_secret_hook_treats_malformed_inspect_as_not_found_and_stops_red(
    tmp_path, monkeypatch, capsys
):
    """S9.3/S10.3: untrustworthy daemon state cannot green-light later work.

    The engine wires a readiness helper for hooks.  Docker's malformed inspect
    response must become ``not-found`` rather than an exception or a healthy
    result; a hook rejecting that state returns the runtime failure code and
    secret materialization never starts.
    """
    stack = tmp_path / "stack"
    stack.mkdir()
    merged = {
        "deploy": {"project_name": "demo", "environment_tag": "test"},
        "demo": {"hooks": {"pre_secrets": ["readiness-check"]}},
    }
    observed: dict[str, object] = {}

    monkeypatch.setattr(engine, "check_runtime_dependencies", lambda: None)
    monkeypatch.setattr(engine, "bootstrap_workspace_env", lambda **_kwargs: None)
    monkeypatch.setattr(engine, "enforce_standalone_root", lambda _path: None)
    monkeypatch.setattr(engine.config_model, "render_global_chain", lambda *_args: {})
    monkeypatch.setattr(engine, "configure_logging", lambda *_args: None)
    monkeypatch.setattr(engine.config_model, "render_stack", lambda *_args, **_kwargs: {"demo": {}})
    monkeypatch.setattr(engine, "ensure_workspace_network", lambda **_kwargs: None)
    monkeypatch.setattr(engine.config_model, "deep_merge", lambda *_args: merged)
    monkeypatch.setattr(engine.config_model, "validate_stack_shape", lambda _stack: "demo")
    monkeypatch.setattr(engine.secret_directives, "discover", lambda *_args: [])
    monkeypatch.setattr(engine.secret_directives, "find_misplaced", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(engine, "_check_gitignore", lambda _path: None)
    monkeypatch.setattr(engine, "auto_generate_values", lambda config: config)
    monkeypatch.setattr(engine, "create_hostdirs", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(engine, "_dood_preflight", lambda *_args: None)
    monkeypatch.setattr(engine, "to_physical_path", lambda path, **_kwargs: path)
    monkeypatch.setattr(
        engine.procutil,
        "docker",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout="not-json"),
    )

    def wait_once(status, **_kwargs):
        observed["status"] = status()
        return False

    monkeypatch.setattr(engine._health, "wait_healthy", wait_once)

    def reject_unready_hook(_hooks, point, _config, ctx, _stack_toml, **_kw):
        assert point == "pre_secrets"
        assert ctx.wait_healthy is not None
        assert ctx.wait_healthy("api", timeout_s=0, interval_s=0) is False
        raise engine.hooks_runner.HookExecutionError("api is not ready")

    monkeypatch.setattr(engine.hooks_runner, "run_hooks", reject_unready_hook)
    monkeypatch.setattr(
        engine.secret_materialize,
        "materialize",
        lambda *_args, **_kwargs: pytest.fail("failed pre-secrets hook must not materialize secrets"),
    )

    assert engine.main(["--dry-run", "-d", str(stack), "--define-root", str(tmp_path)]) == 1
    assert observed == {"status": "not-found"}
    assert "api is not ready" in capsys.readouterr().out
