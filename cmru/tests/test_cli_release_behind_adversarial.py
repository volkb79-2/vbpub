from contextlib import nullcontext

import pytest

from cmru import cli, transaction


def test_release_uses_fetched_origin_when_local_main_is_behind(monkeypatch, tmp_path, capsys):
    project = cli.ProjectConfig("demo", {}, {}, project_root=tmp_path / "demo", prefix="demo-v", github_token="token")
    config = (
        tmp_path, {"demo": project}, ["demo"], ["demo"], ["demo"], "project-first", {},
        cli.CleanupConfig([], [], [], []), cli.GitHubConfig("o", "r", "token", "user"),
        cli.ReleaseEnvConfig({}, None),
    )
    workspace = transaction.ReleaseWorkspace(tmp_path, tmp_path / "release", "cmru/release/x", "b" * 40)
    monkeypatch.setattr(cli, "_resolve_config", lambda _: tmp_path / "cmru.toml")
    monkeypatch.setattr(cli, "load_config", lambda _: config)
    monkeypatch.setattr(cli, "apply_release_env", lambda *_: None)
    monkeypatch.setattr(cli.transaction, "release_lock", lambda _: nullcontext())
    monkeypatch.setattr(cli, "_uncommitted_release_paths", lambda *args: {})
    monkeypatch.setattr(cli.transaction, "fetch_origin_main", lambda *_: "b" * 40)
    monkeypatch.setattr(cli.transaction, "assert_local_main_not_ahead", lambda *_: 1)
    workspace_args = {}
    monkeypatch.setattr(cli.transaction, "create_workspace", lambda *args, **kwargs: workspace_args.update(kwargs) or workspace)
    monkeypatch.setattr(cli.transaction, "copy_secret_overlays", lambda *args: None)
    monkeypatch.setattr(cli.transaction, "run_child", lambda *args, **kwargs: 0)
    monkeypatch.setattr(cli.transaction, "remove_backup_branch", lambda *args: None)
    monkeypatch.setattr(cli.transaction, "remove_workspace", lambda *args: None)
    monkeypatch.setattr(cli.transaction, "forget_release_scope", lambda *args: None)
    monkeypatch.setattr(cli.transaction, "sync_local_main", lambda *args: True)
    with pytest.raises(SystemExit) as exc:
        cli.main(["release", "--project", "demo", "--config", str(tmp_path / "cmru.toml")])
    assert exc.value.code == 0
    assert workspace_args == {"base": "b" * 40, "scope": "demo"}
    output = capsys.readouterr().out
    assert "1 commit(s) behind origin/main" in output
    assert "Release transaction complete" in output
