"""Public hostdir-validation failure contract."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import engine  # noqa: E402


def test_cli_rejects_anonymous_generated_hostdir_before_daemon_or_hook_side_effects(
    tmp_path, monkeypatch, capsys
):
    """S6.3/S10.3: an unnamed automatic hostdir is a red validation failure.

    An empty hostdir path asks CIU to generate a path from the containing
    service name.  Without that name, inventing a destination risks writing to
    an unintended location.  The command must therefore fail before Docker
    reachability or hook/deployment work begins.
    """
    stack = tmp_path / "stack"
    stack.mkdir()
    merged = {
        "deploy": {"env": {"shared": {"CONTAINER_UID": "1000", "DOCKER_GID": "994"}}},
        "service": {"hostdir": {"data": ""}},
    }

    monkeypatch.setattr(engine, "check_runtime_dependencies", lambda: None)
    monkeypatch.setattr(engine, "bootstrap_workspace_env", lambda **_kwargs: None)
    monkeypatch.setattr(engine, "enforce_standalone_root", lambda _path: None)
    monkeypatch.setattr(engine.config_model, "render_global_chain", lambda *_args: {})
    monkeypatch.setattr(engine, "configure_logging", lambda *_args: None)
    monkeypatch.setattr(engine, "ensure_workspace_network", lambda **_kwargs: None)
    monkeypatch.setattr(engine.config_model, "render_stack", lambda *_args, **_kwargs: {"service": {}})
    monkeypatch.setattr(engine.config_model, "deep_merge", lambda *_args: merged)
    monkeypatch.setattr(engine.config_model, "validate_stack_shape", lambda _stack: "service")
    monkeypatch.setattr(engine.secret_directives, "discover", lambda *_args: [])
    monkeypatch.setattr(engine.secret_directives, "find_misplaced", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(engine, "_check_gitignore", lambda _path: None)
    monkeypatch.setattr(engine, "auto_generate_values", lambda config: config)
    monkeypatch.setattr(
        engine,
        "_dood_preflight",
        lambda *_args: pytest.fail("invalid hostdir must not reach daemon preflight"),
    )
    monkeypatch.setattr(
        engine.hooks_runner,
        "run_hooks",
        lambda *_args, **_kwargs: pytest.fail("invalid hostdir must not run hooks"),
    )

    assert engine.main(["--dry-run", "-d", str(stack), "--define-root", str(tmp_path)]) == 2
    assert "hostdir section found without service name" in capsys.readouterr().out
    assert list(stack.iterdir()) == []
