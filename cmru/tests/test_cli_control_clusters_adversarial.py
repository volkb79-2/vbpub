import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from cmru import cli, exit_codes, transaction, version


def _config(tmp_path, project):
    return (
        tmp_path, {project.name: project}, [project.name], [project.name], [project.name],
        "project-first", {}, cli.CleanupConfig([], [], [], []),
        cli.GitHubConfig("owner", "repo", "token", "user"), cli.ReleaseEnvConfig({}, None),
    )


def test_worktrees_existing_release_prints_resume_command(monkeypatch, tmp_path, capsys):
    config = tmp_path / "cmru.orchestration.toml"
    config.write_text("", encoding="utf-8")
    retained = transaction.ReleaseWorkspace(tmp_path, tmp_path / "release", "cmru/release/abc", "a" * 40)
    retained.path.mkdir()
    monkeypatch.setattr(cli.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout=f"{tmp_path}\n"))
    monkeypatch.setattr(transaction, "list_cmru_workspaces", lambda _: [retained])
    cli.main(["worktrees"])
    output = capsys.readouterr().out
    assert "resume: cmru release" in output
    assert "--resume" in output and str(retained.path) in output


def test_dependencies_error_report_exits_config_error_and_renders_cause(monkeypatch, tmp_path, capsys):
    config = tmp_path / "cmru.orchestration.toml"
    config.write_text("", encoding="utf-8")
    forge = SimpleNamespace(
        repo_root=tmp_path,
        orchestration=SimpleNamespace(project_order=["demo"], dependencies={"demo": ["missing"]}),
        projects={"demo": object()},
    )
    monkeypatch.setattr(cli, "_resolve_config", lambda _: config)
    monkeypatch.setattr(cli, "load_forge_config", lambda _: forge)
    with pytest.raises(SystemExit) as exc:
        cli.main(["dependencies", "--config", str(config)])
    assert exc.value.code == exit_codes.CONFIG_ERROR
    output = capsys.readouterr().out
    assert "'demo' declares unknown dependency 'missing'" in output


def test_release_child_non_dry_run_with_no_changes_returns_without_release_calls(monkeypatch, tmp_path, capsys):
    project = cli.ProjectConfig("demo", {}, {}, prefix="demo-v", github_token="token")
    config = _config(tmp_path, project)
    monkeypatch.setattr(cli, "_resolve_config", lambda _: tmp_path / "cmru.toml")
    monkeypatch.setattr(cli, "load_config", lambda _: config)
    monkeypatch.setattr(cli, "apply_release_env", lambda *_: None)
    monkeypatch.setattr(cli, "require_project_publish_credentials", lambda *_: None)
    monkeypatch.setattr(version, "detect_changed_projects", lambda *_, **__: [])
    monkeypatch.setattr(version, "release_cmd", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("release")))
    cli.main(["release", "--_transaction-child", "--config", str(tmp_path / "cmru.toml")])
    output = capsys.readouterr().out
    assert "no changed projects detected" in output
    assert "Nothing to release (no changed projects)." in output
