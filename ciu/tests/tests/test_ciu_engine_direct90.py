"""Hook readiness adapter Docker-outcome contracts."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import engine


def test_hook_readiness_adapters_normalize_docker_outcomes_and_forward_tcp(monkeypatch, tmp_path: Path):
    """Hooks see stable statuses rather than raw Docker failures or JSON details."""

    stack = tmp_path / "stack"
    stack.mkdir()
    merged = {"deploy": {"project_name": "project", "environment_tag": "prod"}, "demo": {"hooks": {"pre_secrets": ["ready"]}}}
    outcomes = iter([
        FileNotFoundError("docker"),
        SimpleNamespace(returncode=1, stdout="", stderr="missing"),
        SimpleNamespace(returncode=0, stdout='{"Status":"running"}', stderr=""),
    ])
    statuses: list[str] = []
    tcp_calls: list[tuple[str, int, float, float]] = []

    monkeypatch.setattr(engine, "check_runtime_dependencies", lambda: None)
    monkeypatch.setattr(engine, "bootstrap_workspace_env", lambda **_kwargs: None)
    monkeypatch.setattr(engine, "enforce_standalone_root", lambda _path: None)
    monkeypatch.setattr(engine.config_model, "render_global_chain", lambda *_args: {})
    monkeypatch.setattr(engine, "configure_logging", lambda _level: None)
    monkeypatch.setattr(engine.config_model, "render_stack", lambda *_args, **_kwargs: {"demo": {}})
    monkeypatch.setattr(engine, "ensure_workspace_network", lambda **_kwargs: None)
    monkeypatch.setattr(engine.config_model, "deep_merge", lambda *_args: merged)
    monkeypatch.setattr(engine.config_model, "validate_stack_shape", lambda _config: "demo")
    monkeypatch.setattr(engine.secret_directives, "discover", lambda *_args: [])
    monkeypatch.setattr(engine.secret_directives, "find_misplaced", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(engine, "_check_gitignore", lambda _path: None)
    monkeypatch.setattr(engine, "auto_generate_values", lambda config: config)
    monkeypatch.setattr(engine, "create_hostdirs", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(engine, "_dood_preflight", lambda *_args: None)
    monkeypatch.setattr(engine, "to_physical_path", lambda path, **_kwargs: path)
    def docker(*_args, **_kwargs):
        outcome = next(outcomes)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    monkeypatch.setattr(engine.procutil, "docker", docker)
    monkeypatch.setattr(engine._health, "classify", lambda state: "healthy" if state["Status"] == "running" else "unknown")

    def wait_healthy(status, **_kwargs):
        statuses.append(status())
        return statuses[-1] == "healthy"

    monkeypatch.setattr(engine._health, "wait_healthy", wait_healthy)
    monkeypatch.setattr(
        engine._health,
        "wait_tcp",
        lambda host, port, *, timeout_s, interval_s: tcp_calls.append((host, port, timeout_s, interval_s)) or True,
    )

    def hook(_hooks, point, _config, ctx, _stack_toml, **_kw):
        assert point == "pre_secrets"
        assert ctx.wait_healthy("api", timeout_s=1, interval_s=0.1) is False
        assert ctx.wait_healthy("api", timeout_s=1, interval_s=0.1) is False
        assert ctx.wait_healthy("api", timeout_s=1, interval_s=0.1) is True
        assert ctx.wait_tcp("db", 5432, timeout_s=3, interval_s=0.2) is True
        raise engine.hooks_runner.HookExecutionError("stop after readiness checks")

    monkeypatch.setattr(engine.hooks_runner, "run_hooks", hook)
    monkeypatch.setattr(
        engine.secret_materialize,
        "materialize",
        lambda *_args, **_kwargs: pytest.fail("hook failure must stop before materialization"),
    )
    monkeypatch.delenv("REPO_ROOT", raising=False)

    with pytest.raises(engine.hooks_runner.HookExecutionError, match="stop after readiness checks"):
        engine.main_execution(stack, define_root=tmp_path)

    assert statuses == ["not-found", "not-found", "healthy"]
    assert tcp_calls == [("db", 5432, 3, 0.2)]
