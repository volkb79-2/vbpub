"""Engine pipeline explicit network-override contract."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import engine


def test_main_execution_explicit_network_override_reaches_network_boundary(monkeypatch, tmp_path: Path):
    """A CLI override wins over global auto-connect configuration before deployment work."""

    stack = tmp_path / "stack"
    stack.mkdir()
    observed: list[bool] = []

    monkeypatch.setattr(engine, "check_runtime_dependencies", lambda: None)
    monkeypatch.setattr(engine, "bootstrap_workspace_env", lambda **_kwargs: None)
    monkeypatch.setattr(engine, "enforce_standalone_root", lambda _path: None)
    monkeypatch.setattr(
        engine.config_model,
        "render_global_chain",
        lambda *_args: {"ciu": {"auto_connect_network": True}},
    )
    monkeypatch.setattr(engine, "configure_logging", lambda _level: None)
    monkeypatch.setattr(engine.config_model, "render_stack", lambda *_args, **_kwargs: {"api": {}})
    monkeypatch.setattr(
        engine,
        "ensure_workspace_network",
        lambda *, auto_connect: observed.append(auto_connect),
    )
    monkeypatch.setattr(
        engine.config_model,
        "deep_merge",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("stop after network boundary")),
    )
    monkeypatch.delenv("REPO_ROOT", raising=False)

    with pytest.raises(RuntimeError, match="stop after network boundary"):
        engine.main_execution(stack, define_root=tmp_path, auto_connect_network=False)

    assert observed == [False]
