"""Remaining engine safety-boundary contracts.

These tests keep external processes at CIU's boundary while exercising the
observable outcomes operators rely on: a daemon-visible workspace must be
reachable, machine-owned secret state must be ignored by git, and the secrets
command must never delete state without an affirmative answer.
"""

from __future__ import annotations

from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import engine  # noqa: E402


def _result(returncode: int, stdout: str = "", stderr: str = ""):
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


def test_dood_preflight_rejects_daemon_unreachable_physical_workspace(monkeypatch, tmp_path):
    """S1.5: a logical workspace is not enough when Docker sees another root."""
    monkeypatch.setenv("REPO_ROOT", str(tmp_path / "logical"))
    monkeypatch.setenv("PHYSICAL_REPO_ROOT", str(tmp_path / "physical"))
    monkeypatch.delenv("CIU_SKIP_DOOD_PREFLIGHT", raising=False)
    monkeypatch.setattr(engine.procutil, "docker", lambda *args, **kwargs: _result(1))

    with pytest.raises(engine.DooDPreflightError, match="cannot reach PHYSICAL_REPO_ROOT"):
        engine._dood_preflight(tmp_path / "physical" / "stack")


def test_dood_preflight_skips_native_root_without_contacting_docker(monkeypatch, tmp_path):
    """S1.5: native deployments do not need a redundant daemon probe."""
    monkeypatch.setenv("REPO_ROOT", str(tmp_path))
    monkeypatch.setenv("PHYSICAL_REPO_ROOT", str(tmp_path))
    monkeypatch.delenv("CIU_SKIP_DOOD_PREFLIGHT", raising=False)
    monkeypatch.setattr(engine.procutil, "docker", lambda *args, **kwargs: pytest.fail("native root must not probe Docker"))

    engine._dood_preflight(tmp_path / "stack")


def test_gitignore_guard_rejects_secret_artifact_path_in_a_git_worktree(monkeypatch, tmp_path):
    """S1.7: an unignored .ciu directory blocks rendering before it can leak."""
    replies = iter((_result(0, "true\n"), _result(1), _result(1)))
    monkeypatch.setattr(engine.procutil, "run_cmd", lambda *args, **kwargs: next(replies))

    with pytest.raises(ValueError, match="not gitignored"):
        engine._check_gitignore(tmp_path / "stack")


def test_gitignore_guard_allows_ignored_machine_artifacts(monkeypatch, tmp_path):
    """S1.7: an ignored representative secret path permits the normal pipeline."""
    replies = iter((_result(0, "true\n"), _result(0)))
    monkeypatch.setattr(engine.procutil, "run_cmd", lambda *args, **kwargs: next(replies))

    engine._check_gitignore(tmp_path / "stack")


def _prepare_secrets_command(monkeypatch, tmp_path, spec):
    monkeypatch.setattr(engine, "check_runtime_dependencies", lambda: None)
    monkeypatch.setattr(engine, "bootstrap_workspace_env", lambda **kwargs: None)
    monkeypatch.setattr(engine.config_model, "render_global_chain", lambda *args: {})
    monkeypatch.setattr(engine.config_model, "render_stack", lambda *args, **kwargs: {"demo": {}})
    monkeypatch.setattr(engine.config_model, "deep_merge", lambda *args: {"demo": {}})
    monkeypatch.setattr(engine.config_model, "validate_stack_shape", lambda *args: "demo")
    monkeypatch.setattr(engine.secret_directives, "discover", lambda *args: [spec])


def test_secrets_reset_decline_preserves_store_and_returns_success(monkeypatch, tmp_path, capsys):
    """S4.25: declining a reset is a no-op rather than a partial deletion."""
    _prepare_secrets_command(monkeypatch, tmp_path, SimpleNamespace(name="db_password"))
    monkeypatch.setattr("builtins.input", lambda *args: "no")
    monkeypatch.setattr(
        engine.secret_materialize, "reset_secrets",
        lambda *args, **kwargs: pytest.fail("declined reset must not delete secret stores"),
    )

    assert engine.main(["secrets", "reset", "-d", str(tmp_path)]) == 0
    assert "Aborted" in capsys.readouterr().out


def test_secrets_reset_affirmative_removes_only_requested_secret(monkeypatch, tmp_path, capsys):
    """S4.25: named reset forwards a one-name selection and reports the file, not its value."""
    spec = SimpleNamespace(name="db_password")
    _prepare_secrets_command(monkeypatch, tmp_path, spec)
    removed = tmp_path / ".ciu" / "secrets" / "db_password"
    monkeypatch.setattr("builtins.input", lambda *args: "yes")
    captured = {}

    def _reset(stack_dir, repo_root, specs, *, names=None):
        captured.update(stack_dir=stack_dir, repo_root=repo_root, specs=specs, names=names)
        return [removed]

    monkeypatch.setattr(engine.secret_materialize, "reset_secrets", _reset)

    assert engine.main(["secrets", "reset", "--name", "db_password", "-d", str(tmp_path), "--yes"]) == 0
    assert captured["names"] == ["db_password"]
    output = capsys.readouterr().out
    assert str(removed) in output
    assert "db_password" in output


def test_shipped_main_forwards_network_override_and_maps_compose_error(monkeypatch, tmp_path, capsys):
    """S8.5/S10.3: public shipped mode preserves an explicit network choice and returns red on compose failure."""
    observed = {}

    def _shipped(**kwargs):
        observed.update(kwargs)
        raise engine.ComposeError("compose rejected config")

    monkeypatch.setattr(engine, "run_shipped", _shipped)

    assert engine.main(["--shipped", "--no-auto-connect-network", "-d", str(tmp_path)]) == 1
    assert observed["auto_connect_network"] is False
    assert "compose rejected config" in capsys.readouterr().out
