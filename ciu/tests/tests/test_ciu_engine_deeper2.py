"""Early public engine pipeline contracts (S1.2, S2.3, S3.1).

These tests deliberately stop before Docker and rendering side effects.  They
exercise the user-visible validation/short-circuit boundary immediately after
environment bootstrap, where an invocation must not silently use a different
repository, start a network for ``--render-toml``, or accept a required public
name that is absent from the environment.
"""

from __future__ import annotations

from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import engine  # noqa: E402


def _early_pipeline(monkeypatch: pytest.MonkeyPatch, global_config: dict) -> None:
    """Replace only the external early-pipeline boundaries."""
    monkeypatch.setattr(engine, "check_runtime_dependencies", lambda: None)
    monkeypatch.setattr(engine, "bootstrap_workspace_env", lambda **kwargs: None)
    monkeypatch.setattr(engine, "enforce_standalone_root", lambda _path: None)
    monkeypatch.setattr(engine.config_model, "render_global_chain", lambda *_: global_config)
    monkeypatch.setattr(engine, "configure_logging", lambda *_: None)


def test_define_root_must_match_bootstrapped_repository(tmp_path, monkeypatch):
    """S1.2: an explicit root cannot quietly operate against another ciu.env."""
    _early_pipeline(monkeypatch, {})
    (tmp_path / "stack").mkdir()
    other_root = tmp_path / "other"
    monkeypatch.setenv("REPO_ROOT", str(other_root))

    with pytest.raises(ValueError, match="does not match REPO_ROOT"):
        engine.main_execution(tmp_path / "stack", define_root=tmp_path)


def test_render_toml_stops_before_network_merge_and_stack_execution(tmp_path, monkeypatch, capsys):
    """S3.1: render-only writes config then stops before any deploy-side action."""
    _early_pipeline(monkeypatch, {"ciu": {"auto_connect_network": True}})
    (tmp_path / "stack").mkdir()
    stack_config = {"demo": {"name": "demo"}}
    monkeypatch.setattr(engine.config_model, "render_stack", lambda *_args, **_kwargs: stack_config)
    monkeypatch.setattr(
        engine, "ensure_workspace_network", lambda **_kwargs: pytest.fail("render-only must not create a network")
    )
    monkeypatch.setattr(
        engine.config_model, "deep_merge", lambda *_: pytest.fail("render-only must not merge/deploy")
    )
    monkeypatch.delenv("REPO_ROOT", raising=False)

    result = engine.main_execution(tmp_path / "stack", define_root=tmp_path, render_toml=True)

    assert result == {"status": "success", "dry_run": False}
    assert "Rendered CIU TOML files" in capsys.readouterr().out


def test_required_fqdn_rejects_empty_public_name_before_secret_or_compose_work(tmp_path, monkeypatch):
    """S2.3: require_fqdn is a preflight, not a late compose-time failure."""
    _early_pipeline(monkeypatch, {"ciu": {"require_fqdn": True}})
    (tmp_path / "stack").mkdir()
    monkeypatch.setattr(engine.config_model, "render_stack", lambda *_args, **_kwargs: {"demo": {}})
    monkeypatch.setattr(engine, "ensure_workspace_network", lambda **_kwargs: None)
    monkeypatch.setattr(engine.config_model, "deep_merge", lambda *_: {"ciu": {"require_fqdn": True}})
    monkeypatch.setattr(engine.config_model, "validate_stack_shape", lambda *_: "demo")
    monkeypatch.setattr(engine.secret_directives, "discover", lambda *_: [])
    monkeypatch.setattr(engine.secret_directives, "find_misplaced", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(engine, "_check_gitignore", lambda *_: None)
    monkeypatch.setattr(
        engine, "auto_generate_values", lambda *_: pytest.fail("FQDN failure must stop before generation")
    )
    monkeypatch.delenv("PUBLIC_FQDN", raising=False)
    monkeypatch.delenv("REPO_ROOT", raising=False)

    with pytest.raises(ValueError, match=r"\[S2.3\].*PUBLIC_FQDN"):
        engine.main_execution(tmp_path / "stack", define_root=tmp_path)
