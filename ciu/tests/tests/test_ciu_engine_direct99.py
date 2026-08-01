"""Final engine dependency and native compose-failure contracts."""

from __future__ import annotations

import builtins
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import engine


def test_missing_pyyaml_reports_overlay_remediation(monkeypatch, capsys):
    """An unavailable YAML parser is a typed dependency failure with remediation."""

    real_import = builtins.__import__

    def importing(name, *args, **kwargs):
        if name == "yaml":
            raise ImportError("No module named 'yaml'", name="yaml")
        return real_import(name, *args, **kwargs)

    monkeypatch.delenv("SKIP_DEPENDENCY_CHECK", raising=False)
    monkeypatch.setattr(
        engine.procutil,
        "run_cmd",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0),
    )
    monkeypatch.setattr(builtins, "__import__", importing)

    with pytest.raises(engine.DependencyError):
        engine.check_runtime_dependencies()

    output = capsys.readouterr().out
    assert "PyYAML (overlay generation)" in output
    assert "pip install pyyaml" in output


def test_native_compose_error_propagates_after_rendered_pipeline(monkeypatch, tmp_path: Path):
    """A failed native compose invocation remains a ComposeError after safe render stages."""

    stack = tmp_path / "stack"
    stack.mkdir()
    (stack / "compose.yml").write_text("services: {}\n", encoding="utf-8")
    merged = {"demo": {}, "ciu": {}, "deploy": {}}
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
    monkeypatch.setattr(engine.composefile, "render_configfiles", lambda *_args: [])
    monkeypatch.setattr(engine.composefile, "guard_config", lambda config, _specs: config)
    monkeypatch.setattr(engine.composefile, "validate_consumption", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(engine.governance, "resolve_stack_governance", lambda *_args: None)
    monkeypatch.setattr(engine.composefile, "generate_overlay", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(engine.composefile, "compose_process_env", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(engine.composefile, "compose_file_args", lambda *_args: ["-f", "compose.yml"])
    monkeypatch.setattr(engine, "compose_project_name", lambda *_args: "project-prod-stack")
    monkeypatch.setattr(engine, "guard_legacy_compose_project", lambda *_args: None)
    monkeypatch.setattr(
        engine,
        "execute_docker_compose_with_logs",
        lambda *_args, **_kwargs: {"status": "error", "message": "daemon rejected compose"},
    )
    monkeypatch.delenv("REPO_ROOT", raising=False)

    with pytest.raises(engine.ComposeError, match="daemon rejected compose"):
        engine.main_execution(
            stack,
            compose_file="compose.yml",
            define_root=tmp_path,
            skip_hostdir_check=True,
            skip_hooks=True,
            skip_secrets=True,
        )
