from pathlib import Path

import pytest

from cmru import cli


def _config(tmp_path, project):
    return (
        tmp_path, {project.name: project}, [project.name], [project.name], [project.name],
        "project-first", {}, cli.CleanupConfig([], [], [], []),
        cli.GitHubConfig("owner", "repo", "token", "user"), cli.ReleaseEnvConfig({}, None),
    )


def test_cleanup_previews_then_requires_confirmation_unless_yes(monkeypatch, tmp_path, capsys):
    project = cli.ProjectConfig("demo", {}, {}, project_root=tmp_path / "demo")
    monkeypatch.setattr(cli, "_resolve_config", lambda _: tmp_path / "cmru.toml")
    monkeypatch.setattr(cli, "load_config", lambda _: _config(tmp_path, project))
    output_id = "20240101T000000Z_" + "a" * 40
    monkeypatch.setattr(cli.transaction, "retained_build_output_identity", lambda *_args: object())
    actions = []
    monkeypatch.setattr(
        cli.transaction, "delete_retained_build_output",
        lambda *_args, dry_run, **_kwargs: actions.append(dry_run) or [tmp_path / "artifacts" / output_id],
    )

    exc = cli.main(["cleanup", "demo", "--delete-build-output", output_id])
    assert exc == 2
    assert actions == [True]
    assert "confirmation is required" in capsys.readouterr().err

    actions.clear()
    assert cli.main(["cleanup", "demo", "--delete-build-output", output_id, "--dry-run"]) == 0
    assert actions == [True]
    capsys.readouterr()

    actions.clear()
    assert cli.main(["cleanup", "demo", "--delete-build-output", output_id, "--yes"]) == 0
    assert actions == [True, False]
    capsys.readouterr()

    exc = cli.main(["cleanup", "--discard-build-worktree", str(tmp_path / "failed"), "demo", "--dry-run"])
    assert exc == 2
    assert "already exactly scoped" in capsys.readouterr().err


def test_release_child_rejects_non_orchestrated_project_before_release_work(monkeypatch, tmp_path, capsys):
    project = cli.ProjectConfig("demo", {}, {}, prefix="demo-v", github_token="token")
    monkeypatch.setattr(cli, "_resolve_config", lambda _: tmp_path / "cmru.toml")
    monkeypatch.setattr(cli, "load_config", lambda _: _config(tmp_path, project))
    monkeypatch.setattr(cli, "apply_release_env", lambda *_: None)
    monkeypatch.setattr(cli.transaction, "is_transaction_child", lambda _root: True)
    exc = cli.main(["release", "missing", "--config", str(tmp_path / "cmru.toml")])
    assert exc == 2
    assert "unknown project(s): missing" in capsys.readouterr().err


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
