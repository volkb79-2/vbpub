"""Engine certificate validation and reset-ordering contracts."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import engine


def test_main_execution_validates_required_certs_before_reset(monkeypatch, tmp_path: Path):
    """Certificate requirements gate destructive reset before auto-generation/compose work."""

    stack = tmp_path / "stack"
    stack.mkdir()
    events: list[object] = []
    merged = {"api": {}, "ciu": {"require_certs": True}}

    monkeypatch.setattr(engine, "check_runtime_dependencies", lambda: None)
    monkeypatch.setattr(engine, "bootstrap_workspace_env", lambda **_kwargs: None)
    monkeypatch.setattr(engine, "enforce_standalone_root", lambda _path: None)
    monkeypatch.setattr(engine.config_model, "render_global_chain", lambda *_args: {})
    monkeypatch.setattr(engine, "configure_logging", lambda _level: None)
    monkeypatch.setattr(engine.config_model, "render_stack", lambda *_args, **_kwargs: {"api": {}})
    monkeypatch.setattr(engine, "ensure_workspace_network", lambda **_kwargs: None)
    monkeypatch.setattr(engine.config_model, "deep_merge", lambda *_args: merged)
    monkeypatch.setattr(engine.config_model, "validate_stack_shape", lambda _config: "api")
    monkeypatch.setattr(engine.secret_directives, "discover", lambda *_args: [])
    monkeypatch.setattr(engine.secret_directives, "find_misplaced", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(engine, "_check_gitignore", lambda _path: None)
    monkeypatch.setattr(engine, "validate_required_certs", lambda: events.append("certs"))

    def reset(config, workdir, **kwargs):
        events.append(("reset", config, workdir, kwargs))

    monkeypatch.setattr(engine, "reset_service", reset)
    monkeypatch.setattr(
        engine,
        "auto_generate_values",
        lambda _config: (_ for _ in ()).throw(RuntimeError("stop after reset")),
    )
    monkeypatch.delenv("REPO_ROOT", raising=False)

    with pytest.raises(RuntimeError, match="stop after reset"):
        engine.main_execution(
            stack,
            define_root=tmp_path,
            reset=True,
            yes=True,
            remove_secrets=True,
        )

    assert events[0] == "certs"
    kind, config, workdir, kwargs = events[1]
    assert kind == "reset"
    assert config is merged
    assert workdir == stack.resolve()
    assert kwargs["remove_secrets"] is True
    assert kwargs["assume_yes"] is True
    assert kwargs["repo_root"] == tmp_path.resolve()
