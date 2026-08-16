from contextlib import nullcontext

import pytest

from cmru import cli, transaction


def test_release_resume_cleans_workspace_and_reports_sync_conflict(monkeypatch, tmp_path, capsys):
    project = cli.ProjectConfig("demo", {}, {}, project_root=tmp_path / "demo", prefix="demo-v", github_token="token")
    config = (
        tmp_path, {"demo": project}, ["demo"], ["demo"], ["demo"], "project-first", {},
        cli.CleanupConfig([], [], [], []), cli.GitHubConfig("o", "r", "token", "user"),
        cli.ReleaseEnvConfig({}, None),
    )
    workspace = transaction.ReleaseWorkspace(tmp_path, tmp_path / "retained", "cmru/release/resume", "a" * 40)
    monkeypatch.setattr(cli, "_resolve_config", lambda _: tmp_path / "cmru.toml")
    monkeypatch.setattr(cli, "load_config", lambda _: config)
    monkeypatch.setattr(cli, "apply_release_env", lambda *_: None)
    monkeypatch.setattr(cli.transaction, "release_lock", lambda _: nullcontext())
    monkeypatch.setattr(cli, "_uncommitted_release_paths", lambda *args: {})
    monkeypatch.setattr(cli.transaction, "resume_workspace", lambda *args: workspace)
    calls = []
    monkeypatch.setattr(cli.transaction, "copy_secret_overlays", lambda *args: calls.append("copy"))
    monkeypatch.setattr(cli.transaction, "run_child", lambda *args, **kwargs: calls.append(("child", args[1], kwargs)) or 0)
    monkeypatch.setattr(cli.transaction, "remove_backup_branch", lambda w: calls.append("backup"))
    monkeypatch.setattr(cli.transaction, "remove_workspace", lambda w: calls.append("workspace"))
    monkeypatch.setattr(cli.transaction, "forget_release_scope", lambda *args: calls.append("forget"))
    monkeypatch.setattr(cli.transaction, "sync_local_main", lambda *args: False)
    with pytest.raises(SystemExit) as exc:
        cli.main(["release", "--resume", str(workspace.path), "--config", str(tmp_path / "cmru.toml")])
    assert exc.value.code == 0
    assert calls[:2] == ["copy", ("child", ["--config", "cmru.toml"], {})]
    assert calls[2:] == ["backup", "workspace", "forget"]
    output = capsys.readouterr().out
    assert "Could not sync local main automatically" in output
    assert "isolated worktree removed" in output
