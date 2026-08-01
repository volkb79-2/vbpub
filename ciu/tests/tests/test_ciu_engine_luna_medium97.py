"""Remaining executable-path contracts for the CIU engine."""

from __future__ import annotations

import runpy
import sys
import warnings
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import engine  # noqa: E402


def _stub_native_pipeline(monkeypatch: pytest.MonkeyPatch, observed: dict[str, object]) -> None:
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
    monkeypatch.setattr(engine.composefile, "redact_config", lambda config, _specs: config)
    def generate_overlay(*_args, **kwargs):
        observed["overlay"] = kwargs["compose_yaml_text"]
        return None

    monkeypatch.setattr(engine.composefile, "generate_overlay", generate_overlay)


def test_main_execution_consumes_literal_compose_text_in_dry_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    stack = tmp_path / "stack"
    stack.mkdir()
    compose_text = "services:\n  api:\n    image: example:literal\n"
    (stack / "compose.yml").write_text(compose_text, encoding="utf-8")
    observed: dict[str, object] = {}
    _stub_native_pipeline(monkeypatch, observed)

    result = engine.main_execution(
        stack,
        compose_file="compose.yml",
        dry_run=True,
        define_root=tmp_path,
        skip_hostdir_check=True,
        skip_hooks=True,
        skip_secrets=True,
    )

    assert result["status"] == "success"
    assert observed["overlay"] == compose_text


def test_run_shipped_without_define_root_uses_repo_root_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    stack = tmp_path / "stack"
    stack.mkdir()
    (stack / "vendor.yml").write_text("services: {}\n", encoding="utf-8")
    repo_root = tmp_path / "repository"
    repo_root.mkdir()
    observed: dict[str, object] = {}
    monkeypatch.setattr(engine, "check_runtime_dependencies", lambda: None)
    monkeypatch.setattr(engine, "bootstrap_workspace_env", lambda **_kwargs: None)
    def render_global_chain(_working_dir, root):
        observed["repo_root"] = root
        return {}

    monkeypatch.setattr(engine.config_model, "render_global_chain", render_global_chain)
    monkeypatch.setattr(engine, "configure_logging", lambda _level: None)
    monkeypatch.setattr(engine, "ensure_workspace_network", lambda **_kwargs: None)
    monkeypatch.setattr(engine, "_dood_preflight", lambda *_args: None)
    monkeypatch.setattr(engine, "to_physical_path", lambda path, **_kwargs: path)
    monkeypatch.setattr(engine.composefile, "compose_process_env", lambda *_args, **_kwargs: {})
    monkeypatch.setenv("REPO_ROOT", str(repo_root))

    result = engine.run_shipped(stack, compose_file="vendor.yml", dry_run=True)

    assert result["status"] == "success"
    assert observed["repo_root"] == repo_root.resolve()


@pytest.mark.parametrize("status", ["interrupted", "unexpected"])
def test_main_maps_every_non_success_execution_status_to_one(
    monkeypatch: pytest.MonkeyPatch, status: str
) -> None:
    args = type("Args", (), {
        "generate_env": False, "dry_run": False, "reset": False,
        "print_context": False, "render_toml": False, "update_cert_permission": False,
        "skip_hostdir_check": False, "skip_hooks": False, "skip_secrets": False,
        "shipped": False, "dir": Path("."), "file": "ciu.defaults.toml.j2",
        "define_root": None, "yes": False, "secrets": False,
        "auto_connect_network": None,
    })()
    monkeypatch.setattr(engine, "parse_arguments", lambda _argv: args)
    monkeypatch.setattr(engine, "main_execution", lambda **_kwargs: {"status": status})

    assert engine.main([]) == 1


def test_engine_module_guard_help_exits_cleanly_without_warning_summary(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sys, "argv", ["ciu.engine", "--help"])
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        with pytest.raises(SystemExit) as exit_info:
            runpy.run_module("ciu.engine", run_name="__main__")

    assert exit_info.value.code == 0
    assert "usage:" in capsys.readouterr().out
    assert all(item.category is RuntimeWarning for item in caught)
