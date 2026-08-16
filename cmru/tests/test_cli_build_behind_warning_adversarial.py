from contextlib import nullcontext

import pytest

from cmru import cli, transaction


def test_build_warns_when_local_main_is_behind_but_uses_fetched_origin(monkeypatch, tmp_path, capsys):
    project = cli.ProjectConfig("demo", {}, {}, project_root=tmp_path / "demo", build_step="build")
    config = (
        tmp_path, {"demo": project}, ["demo"], ["demo"], ["demo"], "project-first", {},
        cli.CleanupConfig([], [], [], []), cli.GitHubConfig("o", "r", "", "user"),
        cli.ReleaseEnvConfig({}, None),
    )
    workspace = transaction.ReleaseWorkspace(tmp_path, tmp_path / "child", "cmru/build/x", "b" * 40)
    retained = [tmp_path / "demo" / "artifacts" / "build-id"]
    monkeypatch.setattr(cli, "_resolve_config", lambda _: tmp_path / "cmru.toml")
    monkeypatch.setattr(cli, "load_config", lambda _: config)
    monkeypatch.setattr(cli, "apply_release_env", lambda *_: None)
    monkeypatch.setattr(cli.transaction, "release_lock", lambda _: nullcontext())
    monkeypatch.setattr(cli, "_uncommitted_release_paths", lambda *args: {})
    monkeypatch.setattr(cli.transaction, "fetch_origin_main", lambda *_: "b" * 40)
    monkeypatch.setattr(cli.transaction, "assert_local_main_not_ahead", lambda *_: 2)
    workspace_args = {}
    monkeypatch.setattr(cli.transaction, "create_workspace", lambda *args, **kwargs: workspace_args.update(kwargs) or workspace)
    monkeypatch.setattr(cli.transaction, "copy_secret_overlays", lambda *args: None)
    child = []
    monkeypatch.setattr(cli.transaction, "run_child", lambda *args, **kwargs: child.append(args) or 0)
    monkeypatch.setattr(cli.transaction, "retain_successful_build_outputs", lambda *args: retained)
    monkeypatch.setattr(cli.transaction, "remove_workspace", lambda *args: None)
    with pytest.raises(SystemExit) as exc:
        cli.main(["build", "--project", "demo", "--config", str(tmp_path / "cmru.toml")])
    assert exc.value.code == 0
    assert child
    assert workspace_args == {"base": "b" * 40, "purpose": "build"}
    output = capsys.readouterr().out
    assert "2 commit(s) behind origin/main" in output
    assert "uses fetched origin/main bbbbbbbbbbbb" in output
