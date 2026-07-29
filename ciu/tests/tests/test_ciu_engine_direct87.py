"""Engine misplaced-secret fail-closed pipeline contract."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import engine


def test_main_execution_rejects_misplaced_secret_before_side_effect_pipeline(monkeypatch, tmp_path: Path):
    """S4.5 rejects plausible-looking but wrongly scoped secret declarations early."""

    stack = tmp_path / "stack"
    stack.mkdir()
    monkeypatch.setattr(engine, "check_runtime_dependencies", lambda: None)
    monkeypatch.setattr(engine, "bootstrap_workspace_env", lambda **_kwargs: None)
    monkeypatch.setattr(engine, "enforce_standalone_root", lambda _path: None)
    monkeypatch.setattr(engine.config_model, "render_global_chain", lambda *_args: {})
    monkeypatch.setattr(engine, "configure_logging", lambda _level: None)
    monkeypatch.setattr(engine.config_model, "render_stack", lambda *_args, **_kwargs: {"api": {}})
    monkeypatch.setattr(engine, "ensure_workspace_network", lambda **_kwargs: None)
    monkeypatch.setattr(engine.config_model, "deep_merge", lambda *_args: {"api": {}})
    monkeypatch.setattr(engine.config_model, "validate_stack_shape", lambda _config: "api")
    monkeypatch.setattr(engine.secret_directives, "discover", lambda *_args: [])
    monkeypatch.setattr(engine.secret_directives, "find_misplaced", lambda *_args, **_kwargs: [("outside.token", object())])
    monkeypatch.setattr(
        engine,
        "_check_gitignore",
        lambda *_args: (_ for _ in ()).throw(AssertionError("must stop before later validation")),
    )
    monkeypatch.delenv("REPO_ROOT", raising=False)

    with pytest.raises(ValueError, match=r"\[S4\.5/S4\.1\].*outside\.token"):
        engine.main_execution(stack, define_root=tmp_path)
