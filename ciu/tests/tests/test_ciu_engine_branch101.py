"""Focused behavioral contracts for remaining CIU engine branches."""

from __future__ import annotations

import builtins
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import engine  # noqa: E402


def _ok(*, stdout: str = "", stderr: str = "") -> SimpleNamespace:
    return SimpleNamespace(returncode=0, stdout=stdout, stderr=stderr)


def test_dependency_check_without_optional_warnings_has_no_warning_report(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    real_import = builtins.__import__

    def importing(name, *args, **kwargs):
        if name == "hvac":
            return SimpleNamespace()
        return real_import(name, *args, **kwargs)

    monkeypatch.delenv("SKIP_DEPENDENCY_CHECK", raising=False)
    monkeypatch.setattr(engine.procutil, "run_cmd", lambda *_args, **_kwargs: _ok())
    monkeypatch.setattr(builtins, "__import__", importing)

    engine.check_runtime_dependencies()

    assert "Optional dependencies missing" not in capsys.readouterr().out


def test_absolute_inline_hostdir_path_stays_absolute(tmp_path: Path) -> None:
    absolute_path = (tmp_path / "outside" / "data").resolve()
    config = {
        "deploy": {"env": {"shared": {"CONTAINER_UID": "1001", "DOCKER_GID": "1002"}}},
        "service": {"name": "api", "hostdir": {"data": {"path": str(absolute_path)}}},
    }

    engine.create_hostdirs(
        config,
        tmp_path / "stack",
        repo_root=tmp_path,
        physical_root=tmp_path,
        chown_fn=lambda *_args: None,
    )

    assert config["service"]["hostdir"]["data"] == str(absolute_path)
    assert absolute_path.is_dir()


def test_reset_ignores_non_directory_vol_entry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    marker = tmp_path / "vol-not-a-directory"
    marker.write_text("preserve", encoding="utf-8")
    config = {"deploy": {"project_name": "project", "labels": {"prefix": "ciu"}}}
    (tmp_path / "ciu.env").write_text(
        'export REPO_NAME="dstdns"\nexport INSTANCE_ID="abc123"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(engine.procutil, "run_cmd", lambda *_args, **_kwargs: _ok())
    monkeypatch.setattr(
        engine,
        "_rmtree_with_fallback",
        lambda *_args, **_kwargs: pytest.fail("a non-directory vol entry must not be removed"),
    )

    engine.reset_service(config, tmp_path, assume_yes=True, repo_root=tmp_path)

    assert marker.read_text(encoding="utf-8") == "preserve"


def test_reset_with_specs_cleans_orphans_without_fallback_secret_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = {"deploy": {"project_name": "project", "labels": {"prefix": "ciu"}}}
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "ciu.env").write_text(
        'export REPO_NAME="dstdns"\nexport INSTANCE_ID="abc123"\n',
        encoding="utf-8",
    )
    events: list[str] = []

    def run_cmd(command, **_kwargs):
        if "ps" in command:
            events.append("orphans")
        return _ok()

    def reset_secrets(*_args, **_kwargs):
        events.append("secrets")
        return []

    monkeypatch.setattr(engine.procutil, "run_cmd", run_cmd)
    monkeypatch.setattr(engine.secret_materialize, "reset_secrets", reset_secrets)

    engine.reset_service(
        config,
        tmp_path,
        assume_yes=True,
        remove_secrets=True,
        specs=[SimpleNamespace(name="token")],
        repo_root=tmp_path / "repo",
    )

    assert events == ["secrets", "orphans"]


def test_dood_preflight_accepts_zero_return_daemon_probe(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("REPO_ROOT", str(tmp_path / "logical"))
    monkeypatch.setenv("PHYSICAL_REPO_ROOT", str(tmp_path / "physical"))
    monkeypatch.delenv("CIU_SKIP_DOOD_PREFLIGHT", raising=False)
    monkeypatch.setattr(engine.procutil, "docker", lambda *_args, **_kwargs: _ok())

    engine._dood_preflight(tmp_path / "physical" / "stack")


def test_compose_keyboard_interrupt_without_process_returns_interrupted(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def start_process(*_args, **_kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(engine.subprocess, "Popen", start_process)

    result = engine.execute_docker_compose_with_logs([], cwd=Path("/unused"), project="test-project")

    assert result["status"] == "interrupted"
    assert result["message"] == "User interrupted execution"
    assert "User interrupted docker compose execution" in capsys.readouterr().out


def _stub_native_pipeline(monkeypatch: pytest.MonkeyPatch, merged: dict) -> None:
    # S16.3/CIU-24: the budget-slot wiring reads DOCKER_NETWORK_INTERNAL
    # directly from os.environ at the (now-reached) real compose-up step;
    # a real ciu.env always carries it, but bootstrap_workspace_env is
    # stubbed to a no-op below, so this fixture must supply it itself.
    monkeypatch.setenv("DOCKER_NETWORK_INTERNAL", "branch101-net")
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
    monkeypatch.setattr(engine.composefile, "generate_overlay", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(engine.composefile, "compose_process_env", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(engine.composefile, "compose_file_args", lambda *_args: ["-f", "compose.yml"])
    monkeypatch.setattr(engine, "compose_project_name", lambda *_args: "project-prod-stack")
    monkeypatch.setattr(engine, "guard_legacy_compose_project", lambda *_args: None)


def test_interrupted_native_compose_preserves_output_and_skips_post_hooks(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    stack = tmp_path / "stack"
    stack.mkdir()
    (stack / "compose.yml").write_text("services: {}\n", encoding="utf-8")
    merged = {"demo": {"hooks": {"post_compose": ["must-not-run"]}}, "ciu": {}, "deploy": {}}
    _stub_native_pipeline(monkeypatch, merged)
    monkeypatch.setattr(
        engine,
        "execute_docker_compose_with_logs",
        lambda *_args, **_kwargs: {"status": "interrupted", "stdout": "partial output\n"},
    )
    monkeypatch.setattr(
        engine.hooks_runner,
        "run_hooks",
        lambda *_args, **_kwargs: pytest.fail("post-compose hooks must not run after interruption"),
    )

    result = engine.main_execution(
        stack,
        compose_file="compose.yml",
        define_root=tmp_path,
        skip_hostdir_check=True,
    )

    assert result["status"] == "interrupted"
    assert result["stdout"] == "partial output\n"


def test_successful_native_compose_preserves_output_and_runs_post_hook(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A normal compose result continues past the interrupted-only branch."""
    stack = tmp_path / "stack"
    stack.mkdir()
    (stack / "compose.yml").write_text("services: {}\n", encoding="utf-8")
    merged = {"demo": {"hooks": {"post_compose": ["record"]}}, "ciu": {}, "deploy": {}}
    _stub_native_pipeline(monkeypatch, merged)
    monkeypatch.setattr(
        engine,
        "execute_docker_compose_with_logs",
        lambda *_args, **_kwargs: {"status": "success", "stdout": "started\n"},
    )
    observed: list[str] = []
    monkeypatch.setattr(
        engine.hooks_runner,
        "run_hooks",
        lambda _hooks, point, *_args: observed.append(point),
    )

    result = engine.main_execution(
        stack,
        compose_file="compose.yml",
        define_root=tmp_path,
        skip_hostdir_check=True,
    )

    assert result["status"] == "success"
    assert result["stdout"] == "started\n"
    assert observed == ["post_compose"]


def test_secret_reset_yes_calls_reset_secrets_and_returns_success(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    spec = SimpleNamespace(name="db_password")
    monkeypatch.setenv("REPO_ROOT", str(tmp_path))
    monkeypatch.setattr(engine, "check_runtime_dependencies", lambda: None)
    monkeypatch.setattr(engine, "bootstrap_workspace_env", lambda **_kwargs: None)
    monkeypatch.setattr(engine.config_model, "render_global_chain", lambda *_args: {})
    monkeypatch.setattr(engine.config_model, "render_stack", lambda *_args, **_kwargs: {"demo": {}})
    monkeypatch.setattr(engine.config_model, "deep_merge", lambda *_args: {"demo": {}})
    monkeypatch.setattr(engine.config_model, "validate_stack_shape", lambda _config: "demo")
    monkeypatch.setattr(engine.secret_directives, "discover", lambda *_args: [spec])
    monkeypatch.setattr("builtins.input", lambda _prompt: "y")
    observed: dict[str, object] = {}
    removed = tmp_path / ".ciu" / "secrets" / "db_password"

    def reset_secrets(stack_dir, repo_root, specs, *, names=None):
        observed.update(stack_dir=stack_dir, repo_root=repo_root, specs=specs, names=names)
        return [removed]

    monkeypatch.setattr(engine.secret_materialize, "reset_secrets", reset_secrets)

    assert engine.main(["secrets", "reset", "-d", str(tmp_path)]) == 0
    assert observed["names"] is None
    assert observed["specs"] == [spec]
    assert "Removed 1 secret store file" in capsys.readouterr().out
