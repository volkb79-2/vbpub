"""Public engine command boundaries that do not require a Docker daemon.

The assertions deliberately sit at CIU's command boundaries: callers see a
compose invocation, secret table, or generated environment result.  Docker and
configuration rendering are replaced only where those external systems begin.
"""

from __future__ import annotations

from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import engine  # noqa: E402


def test_shipped_execution_falls_back_to_legacy_project_and_preserves_compose_boundary(
    tmp_path, monkeypatch, capsys
):
    """S8.5: a minimal shipped file starts without native deploy metadata.

    A maintainer-owned compose file has no CIU stack config, so absent
    ``deploy.project_name/environment_tag`` must select Docker Compose's
    directory project rather than reject the shipped workload.  The command
    boundary remains visibly ``-f <file> up -d`` and does not gain a scoped
    project argument.
    """
    stack = tmp_path / "vendor stack"
    stack.mkdir()
    (stack / "vendor.yml").write_text("services: {legacy: {image: alpine:3}}\n")
    calls: list[dict] = []

    monkeypatch.setattr(engine, "check_runtime_dependencies", lambda: None)
    monkeypatch.setattr(engine, "bootstrap_workspace_env", lambda **kwargs: None)
    monkeypatch.setattr(engine.config_model, "render_global_chain", lambda *args: {"ciu": {}})
    monkeypatch.setattr(engine, "configure_logging", lambda *args: None)
    monkeypatch.setattr(engine, "ensure_workspace_network", lambda **kwargs: None)
    monkeypatch.setattr(engine, "_dood_preflight", lambda *args: None)
    monkeypatch.setattr(engine, "to_physical_path", lambda path, **kwargs: path)
    monkeypatch.setattr(engine.composefile, "compose_process_env", lambda *args, **kwargs: {"X": "1"})
    monkeypatch.setattr(engine, "compose_project_name", lambda *args: (_ for _ in ()).throw(ValueError("no deploy")))
    monkeypatch.setattr(engine, "guard_legacy_compose_project", lambda *args: pytest.fail("legacy guard must not run"))

    def _compose(file_args, **kwargs):
        calls.append({"file_args": file_args, **kwargs})
        return {"status": "success", "stdout": "legacy started\n"}

    monkeypatch.setattr(engine, "execute_docker_compose_with_logs", _compose)

    result = engine.run_shipped(stack, compose_file="vendor.yml", define_root=tmp_path)

    assert result == {
        "status": "success", "dry_run": False, "shipped": True,
        "stdout": "legacy started\n",
    }
    assert calls == [{
        "file_args": ["-f", "vendor.yml"], "cwd": stack.resolve(),
        "env": {"X": "1"}, "project": None,
    }]
    assert "legacy directory-derived compose project" in capsys.readouterr().out


def test_shipped_compose_error_is_mapped_by_public_cli_without_success_output(
    tmp_path, monkeypatch, capsys
):
    """S10.3: a failed shipped compose returns exit 1, not a green CLI result."""
    monkeypatch.setattr(
        engine, "run_shipped",
        lambda **kwargs: {"status": "interrupted", "stdout": "partial output\n"},
    )

    assert engine.main(["--shipped", "-d", str(tmp_path)]) == 1
    assert capsys.readouterr().out == ""


def test_secrets_list_cli_prints_metadata_without_secret_values(tmp_path, monkeypatch, capsys):
    """S4.25: public list emits lifecycle metadata but never a materialized value."""
    spec = SimpleNamespace(name="db_password")
    monkeypatch.setattr(engine, "check_runtime_dependencies", lambda: None)
    monkeypatch.setattr(engine, "bootstrap_workspace_env", lambda **kwargs: None)
    monkeypatch.setattr(engine.config_model, "render_global_chain", lambda *args: {})
    monkeypatch.setattr(engine.config_model, "render_stack", lambda *args, **kwargs: {"demo": {}})
    monkeypatch.setattr(engine.config_model, "deep_merge", lambda *args: {"demo": {}})
    monkeypatch.setattr(engine.config_model, "validate_stack_shape", lambda *args: "demo")
    monkeypatch.setattr(engine.secret_directives, "discover", lambda *args: [spec])
    monkeypatch.setattr(
        engine.secret_materialize, "list_secrets",
        lambda *args: [{
            "name": "db_password", "kind": "ASK_EXTERNAL", "locator": "DB_PASSWORD",
            "store": "environment", "exists": True, "value": "never-print-this",
        }],
    )

    assert engine.main(["secrets", "list", "-d", str(tmp_path), "--define-root", str(tmp_path)]) == 0

    output = capsys.readouterr().out
    assert "NAME" in output and "db_password" in output and "ASK_EXTERNAL" in output
    assert "never-print-this" not in output


def test_unknown_secret_reset_fails_before_confirmation_or_deletion(tmp_path, monkeypatch, capsys):
    """S4.25: a typo cannot prompt for or delete an unrelated secret store."""
    spec = SimpleNamespace(name="real_secret")
    monkeypatch.setattr(engine, "check_runtime_dependencies", lambda: None)
    monkeypatch.setattr(engine, "bootstrap_workspace_env", lambda **kwargs: None)
    monkeypatch.setattr(engine.config_model, "render_global_chain", lambda *args: {})
    monkeypatch.setattr(engine.config_model, "render_stack", lambda *args, **kwargs: {"demo": {}})
    monkeypatch.setattr(engine.config_model, "deep_merge", lambda *args: {"demo": {}})
    monkeypatch.setattr(engine.config_model, "validate_stack_shape", lambda *args: "demo")
    monkeypatch.setattr(engine.secret_directives, "discover", lambda *args: [spec])
    monkeypatch.setattr("builtins.input", lambda *args: pytest.fail("unknown secret must not prompt"))
    monkeypatch.setattr(
        engine.secret_materialize, "reset_secrets", lambda *args, **kwargs: pytest.fail("unknown secret must not delete")
    )

    assert engine.main(["secrets", "reset", "--name", "typo", "-d", str(tmp_path)]) == 2
    assert "no such secret 'typo'" in capsys.readouterr().out


def test_generate_env_fast_path_initializes_once_and_skips_stack_execution(
    tmp_path, monkeypatch, capsys
):
    """S2.8: ``--generate-env`` creates only ciu.env and does not deploy a stack."""
    generated = tmp_path / "ciu.env"
    calls: list[Path] = []
    monkeypatch.setattr(engine, "check_runtime_dependencies", lambda: None)
    monkeypatch.setattr(engine, "resolve_env_root", lambda **kwargs: tmp_path)
    monkeypatch.setattr(engine, "bootstrap_env_init", lambda root: calls.append(root) or generated)
    monkeypatch.setattr(engine, "main_execution", lambda **kwargs: pytest.fail("fast path must not execute a stack"))

    assert engine.main(["--generate-env", "-d", str(tmp_path)]) == 0
    assert calls == [tmp_path]
    assert f"Generated {generated}" in capsys.readouterr().out


def test_generate_env_failure_has_environment_exit_code_and_skips_stack_execution(
    tmp_path, monkeypatch, capsys
):
    """S10.3: bootstrap errors are exit 3 and cannot fall through to deployment."""
    monkeypatch.setattr(engine, "check_runtime_dependencies", lambda: None)
    monkeypatch.setattr(
        engine, "resolve_env_root", lambda **kwargs: (_ for _ in ()).throw(engine.WorkspaceEnvError("bad environment"))
    )
    monkeypatch.setattr(engine, "main_execution", lambda **kwargs: pytest.fail("failed bootstrap must not execute a stack"))

    assert engine.main(["--generate-env", "-d", str(tmp_path)]) == 3
    assert "bad environment" in capsys.readouterr().out
