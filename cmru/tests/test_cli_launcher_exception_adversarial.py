from contextlib import nullcontext

import pytest

from cmru import cli, transaction


def test_release_launcher_surfaces_workspace_creation_failure_and_stops(monkeypatch, tmp_path, capsys):
    project = cli.ProjectConfig("demo", {}, {}, project_root=tmp_path / "demo", prefix="demo-v", github_token="token")
    config = (
        tmp_path, {"demo": project}, ["demo"], ["demo"], ["demo"], "project-first", {},
        cli.CleanupConfig([], [], [], []), cli.GitHubConfig("o", "r", "token", "user"),
        cli.ReleaseEnvConfig({}, None),
    )
    monkeypatch.setattr(cli, "_resolve_config", lambda _: tmp_path / "cmru.toml")
    monkeypatch.setattr(cli, "load_config", lambda _: config)
    monkeypatch.setattr(cli, "apply_release_env", lambda *_: None)
    monkeypatch.setattr(cli.transaction, "release_lock", lambda _: nullcontext())
    monkeypatch.setattr(cli, "_uncommitted_release_paths", lambda *args: {})
    monkeypatch.setattr(cli.transaction, "fetch_origin_main", lambda *_: "a" * 40)
    monkeypatch.setattr(cli.transaction, "assert_local_main_not_ahead", lambda *_: 0)
    monkeypatch.setattr(cli.transaction, "create_workspace", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("worktree unavailable")))
    monkeypatch.setattr(cli.transaction, "run_child", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("child")))
    with pytest.raises(SystemExit) as exc:
        cli.main(["release", "--project", "demo", "--config", str(tmp_path / "cmru.toml")])
    assert exc.value.code == 1
    assert "worktree unavailable" in capsys.readouterr().err
