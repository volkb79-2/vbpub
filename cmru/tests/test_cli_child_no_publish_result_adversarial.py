from cmru import cli, transaction, version


def test_child_release_reports_nothing_built_when_sequential_result_is_empty(monkeypatch, tmp_path, capsys):
    project = cli.ProjectConfig("demo", {}, {}, prefix="demo-v", github_token="token")
    config = (
        tmp_path, {"demo": project}, ["demo"], ["demo"], ["demo"], "project-first", {},
        cli.CleanupConfig([], [], [], []), cli.GitHubConfig("o", "r", "token", "user"),
        cli.ReleaseEnvConfig({}, None),
    )
    monkeypatch.setattr(cli, "_resolve_config", lambda _: tmp_path / "cmru.toml")
    monkeypatch.setattr(cli, "load_config", lambda _: config)
    monkeypatch.setattr(cli, "apply_release_env", lambda *_: None)
    monkeypatch.setattr(version, "detect_changed_projects", lambda *args: [("demo", "changed")])
    monkeypatch.setenv(transaction.BRANCH_ENV, "cmru/release/child")
    monkeypatch.setenv(transaction.BASE_ENV, "a" * 40)
    monkeypatch.setattr(transaction, "write_release_scope", lambda *args: None)
    monkeypatch.setattr(transaction, "push_backup_branch", lambda *args: None)
    monkeypatch.setattr(cli, "_release_projects_sequentially", lambda *args, **kwargs: [])
    cli.main(["release", "--_transaction-child", "--config", str(tmp_path / "cmru.toml")])
    assert "Nothing built or published (see per-project log above for why)." in capsys.readouterr().out
