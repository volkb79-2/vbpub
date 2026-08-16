from pathlib import Path

import pytest

from cmru import cli, transaction, version


def _config(tmp_path, project, *, token="token"):
    return (
        tmp_path, {project.name: project}, [project.name], [project.name], [project.name],
        "project-first", {}, cli.CleanupConfig([], [], [], []),
        cli.GitHubConfig("owner", "repo", token, "user"), cli.ReleaseEnvConfig({}, None),
    )


def test_cleanup_unmanaged_release_rejects_unknown_project_and_missing_credential(monkeypatch, tmp_path):
    project = cli.ProjectConfig("demo", {}, {}, prefix="demo-v", github_token="token")
    monkeypatch.setattr(cli, "_resolve_config", lambda _: tmp_path / "cmru.toml")
    monkeypatch.setattr(cli, "load_config", lambda _: _config(tmp_path, project))
    with pytest.raises(SystemExit) as exc:
        cli.main(["cleanup", "--delete-unmanaged-release-tag", "demo-old", "--project", "missing", "--yes"])
    assert exc.value.code == 2

    no_token = cli.ProjectConfig("demo", {}, {}, prefix="demo-v", github_token="")
    monkeypatch.setattr(cli, "load_config", lambda _: _config(tmp_path, no_token, token=""))
    with pytest.raises(SystemExit) as exc:
        cli.main(["cleanup", "--delete-unmanaged-release-tag", "demo-old", "--project", "demo", "--yes"])
    assert exc.value.code == 2


def test_cleanup_delete_build_output_and_discard_worktree_dispatch_exact_targets(monkeypatch, tmp_path, capsys):
    project = cli.ProjectConfig("demo", {}, {}, project_root=tmp_path / "demo")
    monkeypatch.setattr(cli, "_resolve_config", lambda _: tmp_path / "cmru.toml")
    monkeypatch.setattr(cli, "load_config", lambda _: _config(tmp_path, project))
    build_targets = [tmp_path / "demo" / "logs" / "id", tmp_path / "demo" / "artifacts" / "id"]
    seen = []
    monkeypatch.setattr(transaction, "delete_retained_build_output", lambda *args, **kwargs: seen.append((args, kwargs)) or build_targets)
    cli.main(["cleanup", "--delete-build-output", "id", "--project", "demo", "--dry-run"])
    assert seen[0][0][1:] == (project, "demo", "id")
    assert seen[0][1] == {"dry_run": True}
    assert "Would delete retained local build output" in capsys.readouterr().out

    workspace = transaction.ReleaseWorkspace(tmp_path, tmp_path / "failed", "cmru/build/x", "a" * 40)
    seen.clear()
    monkeypatch.setattr(transaction, "discard_build_workspace", lambda *args, **kwargs: seen.append((args, kwargs)) or workspace)
    cli.main(["cleanup", "--discard-build-worktree", str(workspace.path), "--dry-run"])
    assert seen[0][0][1] == workspace.path
    assert seen[0][1] == {"dry_run": True}
    assert "Would discard retained build worktree" in capsys.readouterr().out


def test_release_dry_run_reports_no_changed_projects_without_transaction_side_effect(monkeypatch, tmp_path, capsys):
    project = cli.ProjectConfig("demo", {}, {}, prefix="demo-v", github_token="token")
    monkeypatch.setattr(cli, "_resolve_config", lambda _: tmp_path / "cmru.toml")
    monkeypatch.setattr(cli, "load_config", lambda _: _config(tmp_path, project))
    monkeypatch.setattr(cli, "apply_release_env", lambda *_: None)
    monkeypatch.setattr(version, "detect_changed_projects", lambda *_: [])
    calls = []
    monkeypatch.setattr(version, "release_cmd", lambda *args, **kwargs: calls.append((args, kwargs)))
    cli.main(["release", "--dry-run", "--_transaction-child", "--config", str(tmp_path / "cmru.toml")])
    assert calls[0][1]["dry_run"] is True
    assert calls[0][1]["project_filter"] is None
    output = capsys.readouterr().out
    assert "no changed projects detected" in output
    assert "No tags pushed" in output
