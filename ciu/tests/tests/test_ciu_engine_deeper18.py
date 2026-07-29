"""Vault-backed secret missing-token preflight contract."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import engine  # noqa: E402


def test_vault_secret_without_token_fails_before_materialization_or_rendering(
    tmp_path, monkeypatch, capsys
):
    """S4.16/S10.3: a Vault-backed declaration cannot run without a credential.

    CIU must fail at the token preflight rather than contact Vault, materialize
    a partial secret store, or render configuration that could start a stack.
    """
    stack = tmp_path / "stack"
    stack.mkdir()
    spec = SimpleNamespace(name="api_key", kind="ASK_VAULT", locator="apps/api", expose_env=None)
    merged = {
        "deploy": {"project_name": "demo", "environment_tag": "test"},
        "topology": {"services": {"vault": {"internal_host": "vault", "internal_port": 8200}}},
        "demo": {},
    }

    monkeypatch.delenv("VAULT_TOKEN", raising=False)
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
    monkeypatch.setattr(
        engine.secret_materialize,
        "materialize",
        lambda *_args, **_kwargs: pytest.fail("missing Vault token must not materialize secrets"),
    )
    monkeypatch.setattr(
        engine.composefile,
        "render_configfiles",
        lambda *_args, **_kwargs: pytest.fail("missing Vault token must not render configfiles"),
    )

    assert engine.main(["--dry-run", "-d", str(stack), "--define-root", str(tmp_path)]) == 1
    assert "vault-backed secrets are declared but no Vault token resolved" in capsys.readouterr().out
