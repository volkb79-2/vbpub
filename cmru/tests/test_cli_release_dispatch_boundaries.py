from pathlib import Path
from types import SimpleNamespace

import pytest

from cmru import cli


def test_child_release_args_replaces_parent_only_options_and_preserves_operations(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    config = repo / "cmru.orchestration.toml"
    config.write_text("[projects]\n", encoding="utf-8")
    args = cli._child_release_args(
        ["release", "--config", "/old/config.toml", "--project", "demo",
         "--resume=/old/worktree", "--abandon", "/old/worktree", "--dry-run"],
        config, repo,
    )
    assert args == ["release", "--project", "demo", "--dry-run", "--config", "cmru.orchestration.toml"]
    with pytest.raises(ValueError, match="tracked inside"):
        cli._child_release_args([], tmp_path / "outside.toml", repo)


def test_cleanup_project_step_dry_run_and_execution_pass_version_and_environment(monkeypatch, tmp_path, capsys):
    command = cli.Command("clean", ["echo", "clean"], tmp_path)
    project = cli.ProjectConfig("demo", {"BASE": "yes"}, {"clean": [command]})
    assert cli.cleanup_project_step(tmp_path, project, "2.3.4", True) is False
    assert "DRY RUN" in capsys.readouterr().out

    seen = {}
    monkeypatch.setattr(cli, "_build_step_config", lambda name, commands: (name, commands))
    monkeypatch.setattr("cmru.runner.execute_step", lambda step, root, logs, extra_env: seen.update(
        step=step, root=root, logs=logs, env=extra_env) or None)
    assert cli.cleanup_project_step(tmp_path, project, "2.3.4", False) is True
    assert seen == {
        "step": ("clean", [command]), "root": tmp_path, "logs": tmp_path / "logs",
        "env": {"BASE": "yes", "CMRU_VERSION": "2.3.4"},
    }


def test_cleanup_commit_deletions_refuses_empty_staging_and_reports_commit_failure(monkeypatch, tmp_path, capsys):
    calls = []
    monkeypatch.setattr(cli, "_git", lambda *args: "dirty" if args[-1] == "--porcelain" else "")
    monkeypatch.setattr(cli.subprocess, "run", lambda argv, **kwargs: calls.append(argv) or SimpleNamespace(returncode=0))
    cli.cleanup_commit_deletions(tmp_path, "demo", ["v1", "v2"], False)
    assert calls == [["git", "-C", str(tmp_path), "add", "-A"]]
    assert "nothing staged" in capsys.readouterr().out

    calls.clear()
    monkeypatch.setattr(cli, "_git", lambda *args: "dirty" if args[-1] == "--porcelain" else "file.py")
    monkeypatch.setattr(cli.subprocess, "run", lambda argv, **kwargs: calls.append(argv) or SimpleNamespace(returncode=1))
    cli.cleanup_commit_deletions(tmp_path, "demo", ["v1", "v2", "v3", "v4", "v5", "v6"], False)
    assert calls[-1][-1] == "chore(demo): cleanup deleted v1, v2, v3, v4, v5 (+1 more)"
    assert "commit failed" in capsys.readouterr().out


def test_source_tree_version_accepts_exact_tag_and_dev_describe(monkeypatch):
    results = iter([
        SimpleNamespace(returncode=0, stdout="cmru-v1.2.3\n"),
    ])
    monkeypatch.setattr(cli.subprocess, "run", lambda *args, **kwargs: next(results))
    assert cli._source_tree_version() == "1.2.3"

    results = iter([
        SimpleNamespace(returncode=1, stdout=""),
        SimpleNamespace(returncode=0, stdout="cmru-v1.2.3-4-gabcdef\n"),
    ])
    monkeypatch.setattr(cli.subprocess, "run", lambda *args, **kwargs: next(results))
    assert cli._source_tree_version() == "1.2.4.dev4+gabcdef"
