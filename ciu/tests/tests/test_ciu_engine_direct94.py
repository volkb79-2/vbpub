"""Shipped-mode root, network, project, and compose-outcome contracts."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import engine


def _prepare(monkeypatch, stack: Path, global_config: dict):
    monkeypatch.setattr(engine, "check_runtime_dependencies", lambda: None)
    monkeypatch.setattr(engine, "bootstrap_workspace_env", lambda **_kwargs: None)
    monkeypatch.setattr(engine.config_model, "render_global_chain", lambda *_args: global_config)
    monkeypatch.setattr(engine, "configure_logging", lambda _level: None)
    monkeypatch.setattr(engine, "_dood_preflight", lambda *_args: None)
    monkeypatch.setattr(engine, "to_physical_path", lambda path, **_kwargs: path)


def test_shipped_without_define_root_requires_bootstrapped_repo_root(monkeypatch, tmp_path: Path):
    """Shipped mode cannot guess a repository if bootstrap did not establish one."""

    stack = tmp_path / "stack"
    stack.mkdir()
    _prepare(monkeypatch, stack, {})
    monkeypatch.delenv("REPO_ROOT", raising=False)
    monkeypatch.setattr(
        engine.config_model,
        "render_global_chain",
        lambda *_args: pytest.fail("must reject before global rendering"),
    )

    with pytest.raises(engine.WorkspaceEnvError, match="REPO_ROOT not set"):
        engine.run_shipped(stack)


def test_shipped_override_uses_scoped_project_guard_and_returns_interrupted(monkeypatch, tmp_path: Path):
    """A supplied network choice wins and an interrupted compose is represented, not raised."""

    stack = tmp_path / "stack"
    stack.mkdir()
    (stack / "vendor.yml").write_text("services: {}\n", encoding="utf-8")
    _prepare(monkeypatch, stack, {"ciu": {"auto_connect_network": True}, "deploy": {}})
    observed: dict[str, object] = {}
    monkeypatch.setattr(engine, "ensure_workspace_network", lambda *, auto_connect: observed.setdefault("network", auto_connect))
    monkeypatch.setattr(engine.composefile, "compose_process_env", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(engine, "compose_project_name", lambda *_args: "project-prod-stack")
    monkeypatch.setattr(engine, "guard_legacy_compose_project", lambda workdir, project: observed.update(guard=(workdir, project)))
    monkeypatch.setattr(
        engine,
        "execute_docker_compose_with_logs",
        lambda *_args, **_kwargs: {"status": "interrupted", "stdout": "partial"},
    )

    result = engine.run_shipped(
        stack,
        compose_file="vendor.yml",
        define_root=tmp_path,
        auto_connect_network=False,
    )

    assert observed == {"network": False, "guard": (stack.resolve(), "project-prod-stack")}
    assert result["status"] == "interrupted"
    assert result["message"] == "User aborted shipped deployment"
    assert result["stdout"] == "partial"


def test_shipped_compose_error_propagates_as_typed_runtime_failure(monkeypatch, tmp_path: Path):
    """A failed shipped compose cannot become a successful result dictionary."""

    stack = tmp_path / "stack"
    stack.mkdir()
    (stack / "vendor.yml").write_text("services: {}\n", encoding="utf-8")
    _prepare(monkeypatch, stack, {"ciu": {}, "deploy": {"project_name": "p", "environment_tag": "e"}})
    monkeypatch.setattr(engine, "ensure_workspace_network", lambda **_kwargs: None)
    monkeypatch.setattr(engine.composefile, "compose_process_env", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(engine, "compose_project_name", lambda *_args: "p-e-stack")
    monkeypatch.setattr(engine, "guard_legacy_compose_project", lambda *_args: None)
    monkeypatch.setattr(
        engine,
        "execute_docker_compose_with_logs",
        lambda *_args, **_kwargs: {"status": "error", "message": "compose rejected"},
    )

    with pytest.raises(engine.ComposeError, match="compose rejected"):
        engine.run_shipped(stack, compose_file="vendor.yml", define_root=tmp_path)
