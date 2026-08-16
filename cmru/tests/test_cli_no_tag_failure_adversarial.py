import pytest

from cmru import cli, transaction, version


def test_sequential_tagged_release_fails_closed_when_no_tag_reaches_head(monkeypatch, tmp_path):
    project = cli.ProjectConfig("demo", {}, {}, prefix="demo-v", git_tag=True, build_step="build")
    workspace = transaction.ReleaseWorkspace(tmp_path, tmp_path / "child", "cmru/release/x", "a" * 40)
    monkeypatch.setattr(transaction, "write_release_progress", lambda *args: None)
    monkeypatch.setattr(cli, "apply_project_release_env", lambda *args: None)
    monkeypatch.setattr(cli, "_prepare_release_projects", lambda *args, **kwargs: None)
    monkeypatch.setattr(cli, "_run_release_gates", lambda *args: None)
    monkeypatch.setattr(transaction, "promote_workspace", lambda *args: None)
    monkeypatch.setattr(transaction, "push_backup_branch", lambda *args: None)
    monkeypatch.setattr(version, "release_cmd", lambda *args, **kwargs: None)
    monkeypatch.setattr(cli, "_tag_on_head", lambda *args: None)
    monkeypatch.setattr(cli, "_run_project_steps", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("build/publish")))
    with pytest.raises(RuntimeError, match="no tag ended up on HEAD"):
        cli._release_projects_sequentially(
            tmp_path, {"demo": project}, workspace, ["demo"],
            github_config=cli.GitHubConfig("o", "r", "t", "user"),
            env_config=cli.ReleaseEnvConfig({}, None),
        )
