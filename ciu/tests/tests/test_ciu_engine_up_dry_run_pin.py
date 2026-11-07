"""Pin ``ciu up --dry-run`` behaviour across the CIU-141 prepare-only seam.

The render-without-start seam (``_prepare_only``) must not change what a plain
``up --dry-run`` evaluates: it never derived the compose environment, file
arguments or project name, and it never required ``deploy.project_name`` /
``deploy.environment_tag``.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import engine


def _stub_pipeline(monkeypatch: pytest.MonkeyPatch) -> None:
    # Deliberately NO deploy.project_name / deploy.environment_tag anywhere.
    merged = {"demo": {}, "ciu": {}, "deploy": {}}
    monkeypatch.setattr(engine, "check_runtime_dependencies", lambda: None)
    monkeypatch.setattr(engine, "bootstrap_workspace_env", lambda **_kwargs: None)
    monkeypatch.setattr(engine, "enforce_standalone_root", lambda _path: None)
    monkeypatch.setattr(engine.config_model, "render_global_chain", lambda *_args: {})
    monkeypatch.setattr(engine, "configure_logging", lambda _level: None)
    monkeypatch.setattr(
        engine.config_model, "render_stack", lambda *_args, **_kwargs: {"demo": {}}
    )
    monkeypatch.setattr(engine, "ensure_workspace_network", lambda **_kwargs: None)
    monkeypatch.setattr(engine.config_model, "deep_merge", lambda *_args: merged)
    monkeypatch.setattr(engine.config_model, "validate_stack_shape", lambda _config: "demo")
    monkeypatch.setattr(engine.secret_directives, "discover", lambda *_args: [])
    monkeypatch.setattr(
        engine.secret_directives, "find_misplaced", lambda *_args, **_kwargs: []
    )
    monkeypatch.setattr(engine, "_check_gitignore", lambda _path: None)
    monkeypatch.setattr(engine, "auto_generate_values", lambda config: config)
    monkeypatch.setattr(engine, "create_hostdirs", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(engine, "_dood_preflight", lambda *_args: None)
    monkeypatch.setattr(engine, "to_physical_path", lambda path, **_kwargs: path)
    monkeypatch.setattr(engine.composefile, "render_configfiles", lambda *_args: [])
    monkeypatch.setattr(engine.composefile, "guard_config", lambda config, _specs: config)
    monkeypatch.setattr(
        engine.composefile, "validate_consumption", lambda *_args, **_kwargs: []
    )
    monkeypatch.setattr(engine.governance, "resolve_stack_governance", lambda *_args: None)
    monkeypatch.setattr(engine.composefile, "redact_config", lambda config, _specs: config)
    monkeypatch.setattr(
        engine.composefile, "generate_overlay", lambda *_args, **_kwargs: None
    )


def test_up_dry_run_evaluates_no_compose_inputs_and_starts_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    (tmp_path / "ciu.global.defaults.toml.j2").write_text("", encoding="utf-8")
    stack = tmp_path / "stack"
    stack.mkdir()
    (stack / "compose.yml").write_text(
        "services:\n  api:\n    image: example:literal\n", encoding="utf-8"
    )
    _stub_pipeline(monkeypatch)
    evaluated: list[str] = []

    def spy(name: str):
        def inner(*_args, **_kwargs):
            evaluated.append(name)
            raise AssertionError(f"up --dry-run must not evaluate {name}")
        return inner

    monkeypatch.setattr(engine.composefile, "compose_process_env", spy("env"))
    monkeypatch.setattr(engine.composefile, "compose_file_args", spy("file_args"))
    monkeypatch.setattr(engine, "compose_project_name", spy("project"))
    monkeypatch.setattr(engine, "execute_docker_compose_with_logs", spy("compose up"))
    monkeypatch.setattr(engine, "guard_container_name_owners", spy("name guard"))

    result = engine.main_execution(
        stack,
        compose_file="compose.yml",
        dry_run=True,
        define_root=tmp_path,
        skip_hostdir_check=True,
        skip_hooks=True,
        skip_secrets=True,
    )

    out = capsys.readouterr().out
    assert evaluated == []
    assert result["status"] == "success"
    assert result["dry_run"] is True
    assert "prepared_stack" not in result
    assert "[STEP 16/17] --dry-run: skipping docker compose up" in out
    assert "Starting stack" not in out
    assert "Preparing rendered service" not in out
