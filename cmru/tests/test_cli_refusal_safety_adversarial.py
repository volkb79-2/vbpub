from pathlib import Path

import pytest

from cmru import cli


def _config(tmp_path, project):
    return (
        tmp_path, {project.name: project}, [project.name], [project.name], [project.name],
        "project-first", {}, cli.CleanupConfig([], [], [], []),
        cli.GitHubConfig("owner", "repo", "token", "user"), cli.ReleaseEnvConfig({}, None),
    )


def test_cleanup_destructive_modes_require_scope_and_confirmation(monkeypatch, tmp_path, capsys):
    project = cli.ProjectConfig("demo", {}, {}, project_root=tmp_path / "demo")
    monkeypatch.setattr(cli, "_resolve_config", lambda _: tmp_path / "cmru.toml")
    monkeypatch.setattr(cli, "load_config", lambda _: _config(tmp_path, project))
    for args, message in (
        (["cleanup", "--delete-build-output", "id", "--project", "demo"], "requires --yes"),
        (["cleanup", "--discard-build-worktree", str(tmp_path / "failed")], "requires --yes"),
        (["cleanup", "--discard-build-worktree", str(tmp_path / "failed"), "--project", "demo", "--dry-run"], "already exactly scoped"),
    ):
        with pytest.raises(SystemExit) as exc:
            cli.main(args)
        assert exc.value.code == 2
        assert message in capsys.readouterr().err


def test_release_child_rejects_non_orchestrated_project_before_release_work(monkeypatch, tmp_path, capsys):
    project = cli.ProjectConfig("demo", {}, {}, prefix="demo-v", github_token="token")
    monkeypatch.setattr(cli, "_resolve_config", lambda _: tmp_path / "cmru.toml")
    monkeypatch.setattr(cli, "load_config", lambda _: _config(tmp_path, project))
    monkeypatch.setattr(cli, "apply_release_env", lambda *_: None)
    with pytest.raises(SystemExit) as exc:
        cli.main(["release", "--_transaction-child", "--project", "missing", "--config", str(tmp_path / "cmru.toml")])
    assert exc.value.code == 2
    assert "Unknown or non-orchestrated project" in capsys.readouterr().err


def test_untagged_release_requires_push_step_and_runs_build_then_push(monkeypatch, tmp_path):
    base = cli.ProjectConfig("demo", {}, {}, build_step="build", runner_steps={})
    monkeypatch.setattr(cli, "resolve_versions_from_git", lambda *_: None)
    monkeypatch.setattr(cli, "apply_project_release_env", lambda *_: None)
    monkeypatch.setattr(cli, "_worktree_changed_paths", lambda *_: [])
    ran = []
    monkeypatch.setattr(cli, "run_project_step", lambda project, step, root, logs: ran.append(step))
    with pytest.raises(RuntimeError, match="required push step is absent"):
        cli._run_untagged_project(tmp_path, {"demo": base}, "demo", github_config=cli.GitHubConfig("o", "r", "t", "user"), env_config=cli.ReleaseEnvConfig({}, None))
    assert ran == ["build"]

    project = cli.ProjectConfig("demo", {}, {}, build_step="build", runner_steps={"push": []})
    ran.clear()
    cli._run_untagged_project(tmp_path, {"demo": project}, "demo", github_config=cli.GitHubConfig("o", "r", "t", "user"), env_config=cli.ReleaseEnvConfig({}, None))
    assert ran == ["build", "push"]
