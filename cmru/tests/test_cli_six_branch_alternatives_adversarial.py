from types import SimpleNamespace

from cmru import cli, transaction, version


def _config(tmp_path):
    project = cli.ProjectConfig("demo", {}, {}, prefix="demo-v", github_token="token")
    return (
        tmp_path, {"demo": project}, ["demo"], ["demo"], ["demo"], "project-first", {},
        cli.CleanupConfig([], [], [], []), cli.GitHubConfig("o", "r", "token", "user"),
        cli.ReleaseEnvConfig({}, None),
    )


def test_cleanup_project_with_no_step_or_deletions_is_safe_noop(monkeypatch, tmp_path):
    project = cli.ProjectConfig("demo", {}, {}, prefix=None, github_token="token")
    github = cli.GitHubConfig("o", "r", "token", "user")
    monkeypatch.setattr(cli, "resolve_versions_from_git", lambda *_: None)
    monkeypatch.setattr(cli, "github_for_project", lambda *args: github)
    monkeypatch.setattr(cli, "apply_project_release_env", lambda *args: None)
    cli.run_cleanup_verb(tmp_path, {"demo": project}, ["demo"], cli.CleanupConfig([], [], [], []), github, cli.ReleaseEnvConfig({}, None), None, False)


def test_cleanup_prefixed_project_with_no_deletions_skips_commit(monkeypatch, tmp_path):
    project = cli.ProjectConfig("demo", {}, {}, prefix="demo-v", github_token="token")
    github = cli.GitHubConfig("o", "r", "token", "user")
    monkeypatch.setattr(cli, "resolve_versions_from_git", lambda *_: None)
    monkeypatch.setattr(cli, "github_for_project", lambda *args: github)
    monkeypatch.setattr(cli, "apply_project_release_env", lambda *args: None)
    monkeypatch.setattr(cli, "cleanup_project_releases_and_tags", lambda *args: [])
    monkeypatch.setattr(cli, "_latest_version_for_prefix", lambda *args: "1.0.0")
    monkeypatch.setattr(cli, "cleanup_project_step", lambda *args: False)
    monkeypatch.setattr(cli, "cleanup_commit_deletions", lambda *args: (_ for _ in ()).throw(AssertionError("commit")))
    cli.run_cleanup_verb(tmp_path, {"demo": project}, ["demo"], cli.CleanupConfig([], [], [], []), github, cli.ReleaseEnvConfig({}, None), None, False)


def test_source_tree_invalid_exact_tag_falls_back_to_no_version(monkeypatch):
    results = iter([
        SimpleNamespace(returncode=0, stdout="not-a-cmru-tag\n"),
        SimpleNamespace(returncode=1, stdout=""),
    ])
    monkeypatch.setattr(cli.subprocess, "run", lambda *args, **kwargs: next(results))
    assert cli._source_tree_version() is None


def test_sequential_no_tag_no_build_skips_build_and_checkpoints(monkeypatch, tmp_path):
    project = cli.ProjectConfig("demo", {}, {}, prefix="demo-v", git_tag=True)
    workspace = transaction.ReleaseWorkspace(tmp_path, tmp_path / "child", "cmru/release/x", "a" * 40)
    monkeypatch.setattr(transaction, "write_release_progress", lambda *args: None)
    monkeypatch.setattr(cli, "apply_project_release_env", lambda *args: None)
    monkeypatch.setattr(cli, "_prepare_release_projects", lambda *args, **kwargs: None)
    monkeypatch.setattr(cli, "_run_release_gates", lambda *args: None)
    monkeypatch.setattr(transaction, "promote_workspace", lambda *args: None)
    monkeypatch.setattr(transaction, "push_backup_branch", lambda *args: None)
    monkeypatch.setattr(version, "release_cmd", lambda *args, **kwargs: None)
    monkeypatch.setattr(cli, "_tag_on_head", lambda *args: None)
    monkeypatch.setattr(cli, "_run_project_steps", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("build")))
    monkeypatch.setattr(cli, "_git", lambda *args, **kwargs: "b" * 40)
    assert cli._release_projects_sequentially(tmp_path, {"demo": project}, workspace, ["demo"], github_config=cli.GitHubConfig("o", "r", "t", "user"), env_config=cli.ReleaseEnvConfig({}, None), no_build=True) == []


def test_worktrees_reports_missing_workspace_action(monkeypatch, tmp_path, capsys):
    workspace = transaction.ReleaseWorkspace(tmp_path, tmp_path / "missing", "cmru/release/x", "a" * 40)
    monkeypatch.setattr(cli.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout=f"{tmp_path}\n"))
    monkeypatch.setattr(transaction, "list_cmru_workspaces", lambda _: [workspace])
    cli.main(["worktrees"])
    assert "action: unavailable here" in capsys.readouterr().out


def test_worktrees_unknown_purpose_missing_path_reports_unavailable(monkeypatch, tmp_path, capsys):
    workspace = transaction.ReleaseWorkspace(tmp_path, tmp_path / "missing", "cmru/other/x", "a" * 40)
    monkeypatch.setattr(cli.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout=f"{tmp_path}\n"))
    monkeypatch.setattr(transaction, "list_cmru_workspaces", lambda _: [workspace])
    cli.main(["worktrees"])
    assert "action: unavailable here" in capsys.readouterr().out


def test_worktrees_unknown_purpose_existing_path_has_no_action_hint(monkeypatch, tmp_path, capsys):
    path = tmp_path / "existing"
    path.mkdir()
    workspace = transaction.ReleaseWorkspace(tmp_path, path, "cmru/other/x", "a" * 40)
    monkeypatch.setattr(cli.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout=f"{tmp_path}\n"))
    monkeypatch.setattr(transaction, "list_cmru_workspaces", lambda _: [workspace])
    cli.main(["worktrees"])
    assert "other: cmru/other/x" in capsys.readouterr().out


def test_status_without_project_delegates_all_ordered_projects(monkeypatch, tmp_path):
    config = _config(tmp_path)
    monkeypatch.setattr(cli, "_resolve_config", lambda _: tmp_path / "cmru.toml")
    monkeypatch.setattr(cli, "load_config", lambda _: config)
    monkeypatch.setattr(cli, "apply_release_env", lambda *_: None)
    seen = []
    monkeypatch.setattr(version, "status_cmd", lambda *args, **kwargs: seen.append(args[1]))
    cli.main(["status", "--_transaction-child", "--config", str(tmp_path / "cmru.toml")])
    assert seen == [{"demo": config[1]["demo"]}]


def test_release_dry_run_without_project_filters_detected_projects(monkeypatch, tmp_path):
    config = _config(tmp_path)
    monkeypatch.setattr(cli, "_resolve_config", lambda _: tmp_path / "cmru.toml")
    monkeypatch.setattr(cli, "load_config", lambda _: config)
    monkeypatch.setattr(cli, "apply_release_env", lambda *_: None)
    monkeypatch.setenv(transaction.BRANCH_ENV, "cmru/release/x")
    monkeypatch.setenv(transaction.BASE_ENV, "a" * 40)
    monkeypatch.setattr(version, "detect_changed_projects", lambda *args: [("demo", "changed")])
    calls = []
    monkeypatch.setattr(version, "release_cmd", lambda *args, **kwargs: calls.append(kwargs))
    cli.main(["release", "--_transaction-child", "--dry-run", "--config", str(tmp_path / "cmru.toml")])
    assert calls == [{"project_filter": None, "minor": False, "major": False, "set_version": None, "dry_run": True}]


def test_release_dry_run_project_filter_applies_to_detected_projects(monkeypatch, tmp_path):
    config = _config(tmp_path)
    monkeypatch.setattr(cli, "_resolve_config", lambda _: tmp_path / "cmru.toml")
    monkeypatch.setattr(cli, "load_config", lambda _: config)
    monkeypatch.setattr(cli, "apply_release_env", lambda *_: None)
    monkeypatch.setattr(version, "detect_changed_projects", lambda *args: [("demo", "changed")])
    calls = []
    monkeypatch.setattr(version, "release_cmd", lambda *args, **kwargs: calls.append(kwargs))
    cli.main(["release", "--_transaction-child", "--dry-run", "--project", "demo", "--config", str(tmp_path / "cmru.toml")])
    assert calls[0]["project_filter"] == "demo"
