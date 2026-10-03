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
        ["release", "--config", "/old/config.toml", "demo",
         "--resume=/old/worktree", "--dry-run"],
        config, repo,
    )
    assert args == ["release", "demo", "--dry-run", "--config", "cmru.orchestration.toml"]
    outside = tmp_path / "outside.toml"
    assert cli._child_release_args([], outside, repo) == ["--config", str(outside.resolve())]


def test_resume_target_defaults_to_the_saved_scope_and_rejects_widening(tmp_path):
    config_path = tmp_path / "cmru.orchestration.toml"
    configs = {"alpha": object(), "beta": object()}
    project_order = ["alpha", "beta"]

    assert cli._release_resume_target(
        config_path, None, ["beta"], configs, project_order,
    ) == "beta"
    assert cli._release_resume_target(
        config_path, "beta", ["beta"], configs, project_order,
    ) == "beta"
    with pytest.raises(RuntimeError, match="does not match the retained transaction scope"):
        cli._release_resume_target(
            config_path, "alpha,beta", ["beta"], configs, project_order,
        )
    with pytest.raises(RuntimeError, match="no recorded project scope"):
        cli._release_resume_target(
            config_path, None, None, configs, project_order,
        )
    assert cli._release_resume_target(
        config_path, "alpha", None, configs, project_order,
    ) == "alpha"


def test_cleanup_project_step_dry_run_and_execution_pass_version_and_environment(monkeypatch, tmp_path, capsys):
    command = cli.Command("clean", ["echo", "clean"], tmp_path)
    project = cli.ProjectConfig("demo", {"BASE": "yes"}, {"clean": [command]})
    assert cli.cleanup_project_step(tmp_path, project, "2.3.4", True) is False
    assert "DRY RUN" in capsys.readouterr().out

    seen = {}
    monkeypatch.setattr(cli, "_build_step_config", lambda name, commands: (name, commands))
    monkeypatch.setattr(
        "cmru.runner.execute_step",
        lambda step, root, logs, extra_env, protected_env=None: seen.update(
            step=step, root=root, logs=logs, env=extra_env,
            protected_env=protected_env,
        ) or None,
    )
    assert cli.cleanup_project_step(tmp_path, project, "2.3.4", False) is True
    assert seen == {
        "step": ("clean", [command]), "root": tmp_path, "logs": tmp_path / "logs",
        "env": {"BASE": "yes", "CMRU_VERSION": "2.3.4"},
        "protected_env": None,
    }


def test_cleanup_commit_deletions_refuses_empty_staging_and_reports_commit_failure(monkeypatch, tmp_path, capsys):
    calls = []
    monkeypatch.setattr(cli, "_cleanup_worktree_paths", lambda _root: {"generated.py"})
    cached_outputs = iter([""])
    monkeypatch.setattr(cli, "_git", lambda *args: next(cached_outputs))
    monkeypatch.setattr(cli.subprocess, "run", lambda argv, **kwargs: calls.append(argv) or SimpleNamespace(returncode=0))
    cli.cleanup_commit_deletions(
        tmp_path, "demo", ["v1", "v2"], False, before_paths=set(),
    )
    assert calls == [[
        "git", "-C", str(tmp_path), "add", "-A", "--", ":(literal)generated.py",
    ]]
    assert "nothing staged" in capsys.readouterr().out

    calls.clear()
    monkeypatch.setattr(cli, "_git", lambda *args: "generated.py\n")
    monkeypatch.setattr(
        cli.subprocess, "run",
        lambda argv, **kwargs: calls.append(argv) or SimpleNamespace(
            returncode=1 if "commit" in argv else 0, stderr="", stdout="",
        ),
    )
    cli.cleanup_commit_deletions(
        tmp_path, "demo", ["v1", "v2", "v3", "v4", "v5", "v6"], False,
        before_paths=set(),
    )
    assert calls[-1][4] == "chore(demo): cleanup deleted v1, v2, v3, v4, v5 (+1 more)"
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
