from contextlib import nullcontext

import pytest

from cmru import cli, transaction


def test_resumed_release_retains_requested_outputs_and_reports_each_path(monkeypatch, tmp_path, capsys):
    project = cli.ProjectConfig("demo", {}, {}, project_root=tmp_path / "demo", prefix="demo-v", github_token="token")
    config = (
        tmp_path, {"demo": project}, ["demo"], ["demo"], ["demo"], "project-first", {},
        cli.CleanupConfig([], [], [], []), cli.GitHubConfig("o", "r", "token", "user"),
        cli.ReleaseEnvConfig({}, None),
    )
    workspace = transaction.ReleaseWorkspace(tmp_path, tmp_path / "retained", "cmru/release/resume", "a" * 40)
    retained = [tmp_path / "demo" / "logs" / "cmru-release" / "demo-v1"]
    monkeypatch.setattr(cli, "_resolve_config", lambda _: tmp_path / "cmru.toml")
    monkeypatch.setattr(cli, "load_config", lambda _: config)
    monkeypatch.setattr(cli, "apply_release_env", lambda *_: None)
    monkeypatch.setattr(cli.transaction, "release_lock", lambda _: nullcontext())
    monkeypatch.setattr(cli, "_uncommitted_release_paths", lambda *args: {})
    monkeypatch.setattr(cli.transaction, "resume_workspace", lambda *args: workspace)
    monkeypatch.setattr(cli.transaction, "copy_secret_overlays", lambda *args: None)
    monkeypatch.setattr(cli.transaction, "run_child", lambda *args, **kwargs: 0)
    monkeypatch.setattr(cli.transaction, "read_release_results", lambda *args: {"demo": "demo-v1"})
    seen = []
    monkeypatch.setattr(cli.transaction, "retain_success_outputs", lambda *args, **kwargs: seen.append((args, kwargs)) or retained)
    monkeypatch.setattr(cli.transaction, "remove_backup_branch", lambda *args: None)
    monkeypatch.setattr(cli.transaction, "remove_workspace", lambda *args: None)
    monkeypatch.setattr(cli.transaction, "forget_release_scope", lambda *args: None)
    monkeypatch.setattr(cli.transaction, "sync_local_main", lambda *args: True)
    with pytest.raises(SystemExit) as exc:
        cli.main(["release", "--resume", str(workspace.path), "--retain-logs-on-release", "--config", str(tmp_path / "cmru.toml")])
    assert exc.value.code == 0
    assert seen[0][0][3] == {"demo": "demo-v1"}
    assert seen[0][1] == {"retain_logs": True, "retain_artifacts": False}
    assert f"Retained release output: {retained[0]}" in capsys.readouterr().out
