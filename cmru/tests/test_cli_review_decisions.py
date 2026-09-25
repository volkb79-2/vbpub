from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from cmru import cli, transaction
from cmru.runner import StepConfig


def _loaded(root: Path, *projects):
    configs = {project.name: project for project in projects}
    names = list(configs)
    return (
        root, configs, names, names, ["build"], "project-first", {},
        cli.CleanupConfig([], [], [], []),
        cli.GitHubConfig("owner", "repo", "token", "user"),
        cli.ReleaseEnvConfig({}, None),
    )


def _step(name: str, *, cwd: str = "src") -> StepConfig:
    return StepConfig(
        name=name,
        commands=[{"label": "compile", "argv": ["python", "build.py"], "cwd": cwd}],
        bake_set_prefix=None, bake_set_vars=[], no_cache_env=None,
        clean_dirs=["dist"], required_env=["BUILD_TOKEN"], login=None,
        step_env={"MODE": "release"}, env_command=["./resolve-env"], quiet=True,
    )


def test_run_keeps_configured_default_steps_and_dry_run_never_executes(monkeypatch, tmp_path, capsys):
    project_root = tmp_path / "demo"
    project = SimpleNamespace(
        name="demo", cwd="demo", project_root=project_root, env={}, build_metadata={},
        runtime_kind="none", runner_steps={"build": _step("build"), "validate": _step("validate")},
    )
    monkeypatch.setattr(cli, "_resolve_config", lambda _: tmp_path / "cmru.orchestration.toml")
    monkeypatch.setattr(cli, "load_config", lambda _: _loaded(tmp_path, project))
    monkeypatch.setattr(cli, "run_project_step", lambda *_args: (_ for _ in ()).throw(AssertionError("dry-run executed a step")))

    assert cli.main(["run", "demo", "--dry-run"]) == 0
    output = capsys.readouterr().out
    assert "Run plan: build; projects: demo" in output
    assert "demo:build: Would run declared step build" in output
    assert f"cwd={project_root / 'src'}" in output
    assert "resolve-env (not executed during dry-run)" in output
    assert "No project command was started" in output

    assert cli.main(["run", "demo", "--validate", "--dry-run"]) == 0
    output = capsys.readouterr().out
    assert "Run plan: validate; projects: demo" in output
    assert "demo:validate" in output
    assert "demo:build" not in output


def test_version_overrides_are_exclusive_and_refused_for_untagged_projects(
    monkeypatch, tmp_path, capsys,
):
    assert cli.main(["release", "--minor", "--major"]) == 2
    assert "not allowed with argument" in capsys.readouterr().err

    project = cli.ProjectConfig(
        "demo", {}, {}, prefix="demo-v", git_tag=False, project_root=tmp_path,
    )
    monkeypatch.setattr(cli, "_resolve_config", lambda _: tmp_path / "cmru.toml")
    monkeypatch.setattr(cli, "load_config", lambda _: _loaded(tmp_path, project))
    monkeypatch.setattr(cli, "apply_release_env", lambda *_: None)
    monkeypatch.setattr(transaction, "is_transaction_child", lambda _root: True)
    assert cli.main(["status", "demo", "--minor"]) == 2
    assert "do not apply to external-version or no-tag" in capsys.readouterr().err


def test_tool_deps_refresh_rejects_output_and_freshness_flags(monkeypatch, capsys):
    assert cli.main(["tool-deps", "--refresh", "provider", "--json"]) == 2
    assert "cannot be combined with --json" in capsys.readouterr().err
    assert cli.main(["tool-deps", "--refresh", "provider", "--allow-stale-tool-deps"]) == 2
    assert "cannot be combined with --json or --allow-stale-tool-deps" in capsys.readouterr().err


def test_user_parser_rejects_transaction_switch_and_removed_aliases(capsys):
    for argv in (
        ["build", "--_transaction-child"],
        ["get", "demo"],
        ["graph"],
        ["dependency-graph"],
        ["release", "--allow-tag-at-head"],
        ["init", "--layout", "1"],
    ):
        assert cli.main(argv) == 2
        capsys.readouterr()


def test_transaction_child_environment_needs_registered_isolated_worktree(
    monkeypatch, tmp_path,
):
    source = tmp_path / "source"
    source.mkdir()
    common = tmp_path / ".git"

    class Shared:
        def discover_git_context(self, path):
            return source, common, "cmru-release-fake", "a" * 40

        def list_git_worktrees(self, _path):
            return [SimpleNamespace(path=source, is_primary=True, branch="cmru-release-fake")]

        def find_workspace(self, *_args):
            return None

    monkeypatch.setattr(transaction, "_shared_worktree", lambda: Shared())
    monkeypatch.setenv(transaction.CHILD_ENV, "1")
    monkeypatch.setenv("CMRU_WORKSPACE_PATH", str(source))
    monkeypatch.setenv("CMRU_SOURCE_GIT_ROOT", str(source))
    monkeypatch.setenv(transaction.BRANCH_ENV, "cmru-release-fake")
    with pytest.raises(RuntimeError, match="not a registered secondary worktree"):
        transaction.is_transaction_child(source)


def test_repack_is_rejected_before_external_side_effects(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(
        "cmru.handlers.subprocess.run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("Docker was called")),
    )
    monkeypatch.setattr(
        "cmru.handlers._docker_login",
        lambda: (_ for _ in ()).throw(AssertionError("Docker login was called")),
    )
    assert cli.main([
        "handler", "oci-image-build", "--cwd", str(tmp_path),
        "--bake-file", "docker-bake.hcl", "--target", "demo", "--repack",
    ]) == 2
    assert "disabled" in capsys.readouterr().err.lower()
