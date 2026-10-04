from contextlib import nullcontext

import pytest

from cmru import cli, transaction


@pytest.fixture(autouse=True)
def fake_git_family(monkeypatch):
    monkeypatch.setattr(
        cli.transaction,
        "project_git_family_groups",
        lambda root, projects: {root: list(projects)},
    )
    monkeypatch.setattr(cli, "_require_local_tag_inspection_support", lambda _root: None)
    monkeypatch.setattr(
        cli, "_project_git_tag_policy_at_snapshot",
        lambda _root, _base, project: getattr(project, "git_tag", True),
    )


def test_release_resume_cleans_workspace_and_reports_sync_failure(monkeypatch, tmp_path, capsys):
    project = cli.ProjectConfig("demo", {}, {}, project_root=tmp_path / "demo", prefix="demo-v", github_token="token")
    config = (
        tmp_path, {"demo": project}, ["demo"], ["demo"], ["demo"], "project-first", {},
        cli.CleanupConfig([], [], [], []), cli.GitHubConfig("o", "r", "token", "user"),
        cli.ReleaseEnvConfig({}, None),
    )
    workspace = transaction.ReleaseWorkspace(tmp_path, tmp_path / "retained", "cmru/release/resume", "a" * 40)
    monkeypatch.setattr(cli, "_resolve_config", lambda _: tmp_path / "cmru.toml")
    monkeypatch.setattr(cli, "load_config", lambda _: config)
    monkeypatch.setattr(cli.transaction, "project_git_family_groups", lambda root, projects: {root: list(projects)})
    monkeypatch.setattr(cli.transaction, "read_release_scope_for_path", lambda _path: ["demo"])
    monkeypatch.setattr(cli, "apply_release_env", lambda *_: None)
    monkeypatch.setattr(cli.transaction, "release_lock", lambda _: nullcontext())
    monkeypatch.setattr(cli, "_uncommitted_release_paths", lambda *args: {})
    monkeypatch.setattr(cli.transaction, "resume_workspace", lambda *args, **kwargs: workspace)
    monkeypatch.setattr(cli.transaction, "assert_resume_workspace_committed", lambda _path: None)
    calls = []
    monkeypatch.setattr(cli.transaction, "copy_secret_overlays", lambda *args, **kwargs: calls.append("copy"))
    monkeypatch.setattr(cli.transaction, "run_child", lambda *args, **kwargs: calls.append(("child", args[1], kwargs)) or 0)
    monkeypatch.setattr(cli.transaction, "remove_backup_branch", lambda w, **kwargs: calls.append("backup"))
    monkeypatch.setattr(cli.transaction, "remove_workspace", lambda w, **kwargs: calls.append("workspace"))
    monkeypatch.setattr(cli.transaction, "forget_release_scope", lambda *args, **kwargs: calls.append("forget"))
    monkeypatch.setattr(
        cli.transaction, "_sync_local_main_result",
        lambda *args, **kwargs: transaction._SyncLocalMainResult(
            False,
            "Could not sync local main automatically: caller checkout is dirty; local main was left untouched.",
        ),
    )
    exc = cli.main([
            "release", "--resume", str(workspace.path), "--config", str(tmp_path / "cmru.toml"),
            "--discard-logs-on-release", "--discard-artifacts-on-release",
        ])
    assert exc == 0
    assert calls[:2] == [
        "copy",
        ("child", ["demo", "--discard-logs-on-release", "--discard-artifacts-on-release", "--config", "cmru.toml"], {"project_names": ["demo"]}),
    ]
    assert calls[2:] == ["backup", "workspace", "forget"]
    output = capsys.readouterr().out
    assert "Could not sync local main automatically" in output
    assert "caller checkout is dirty" in output
    assert "rebase conflict" not in output
    assert "isolated worktree removed" in output
