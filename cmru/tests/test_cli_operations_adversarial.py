"""CLI operation-level witnesses for orchestration and release boundaries."""
from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from cmru import cli


class _Parser:
    def __init__(self, args): self.args = args
    def parse_args(self): return self.args


def _project(name="demo", **overrides):
    values = dict(name=name, cwd=name, paths=[name], runner_steps={"test": object()},
                  env={}, project_root=None, build_step="build", steps={}, github_token="token")
    values.update(overrides)
    return SimpleNamespace(**values)


def _config(tmp_path, project, *, mode="project-first"):
    return (tmp_path, {project.name: project}, [project.name], [project.name], ["test"], mode, {},
            SimpleNamespace(), cli.GitHubConfig("o", "r", "token", "user"), cli.ReleaseEnvConfig({}, None))


def test_orchestrate_project_first_runs_selected_steps_in_declared_order(monkeypatch, tmp_path):
    project = _project(project_root=tmp_path / "demo")
    args = SimpleNamespace(project=["demo"], run_tests=True, build=False, push=False, validate=False,
                            remove_assets=None, dry_run=False, show_run_details=False, log_append=False, config=None)
    calls = []
    monkeypatch.setattr(cli, "build_arg_parser", lambda: _Parser(args))
    monkeypatch.setattr(cli, "_resolve_config", lambda value: tmp_path / "cmru.toml")
    monkeypatch.setattr(cli, "load_config", lambda path: _config(tmp_path, project))
    monkeypatch.setattr(cli, "resolve_versions_from_git", lambda *a: None)
    monkeypatch.setattr(cli, "apply_project_release_env", lambda *a: None)
    monkeypatch.setattr(cli, "run_project_step", lambda project, step, root, logs: calls.append((project.name, step)))
    cli._orchestrate()
    assert calls == [("demo", "run-tests")]


def test_orchestrate_step_first_uses_step_order_and_rejects_unknown_project(monkeypatch, tmp_path):
    project = _project(project_root=tmp_path / "demo", runner_steps={"test": object(), "build": object()})
    args = SimpleNamespace(project=["missing"], run_tests=False, build=True, push=False, validate=False,
                            remove_assets=None, dry_run=False, show_run_details=False, log_append=False, config=None)
    monkeypatch.setattr(cli, "build_arg_parser", lambda: _Parser(args))
    monkeypatch.setattr(cli, "_resolve_config", lambda value: tmp_path / "cmru.toml")
    monkeypatch.setattr(cli, "load_config", lambda path: _config(tmp_path, project, mode="step-first"))
    with pytest.raises(ValueError, match="Unknown project"):
        cli._orchestrate()


def test_untagged_release_requires_push_and_detects_post_build_source_mutation(monkeypatch, tmp_path):
    project = _project(build_step=None, runner_steps={"push": object()})
    monkeypatch.setattr(cli, "resolve_versions_from_git", lambda *a: None)
    monkeypatch.setattr(cli, "apply_project_release_env", lambda *a: None)
    with pytest.raises(RuntimeError, match="build_step is absent"):
        cli._run_untagged_project(tmp_path, {"demo": project}, "demo", github_config=None, env_config=None)
    project.build_step = "build"
    project.runner_steps = {"build": object()}
    monkeypatch.setattr(cli, "apply_project_release_env", lambda *a: None)
    monkeypatch.setattr(cli, "run_project_step", lambda *a: None)
    monkeypatch.setattr(cli, "_worktree_changed_paths", lambda root: [])
    with pytest.raises(RuntimeError, match="required push step is absent"):
        cli._run_untagged_project(tmp_path, {"demo": project}, "demo", github_config=None, env_config=None)


def test_cleanup_commit_deletions_commits_only_real_changes(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "config", "user.email", "t@x"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    file = tmp_path / "generated"; file.write_text("x")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "initial"], cwd=tmp_path, check=True)
    file.unlink()
    cli.cleanup_commit_deletions(tmp_path, "demo", ["demo-v1"], False)
    assert "cleanup deleted demo-v1" in subprocess.check_output(["git", "log", "-1", "--format=%s"], cwd=tmp_path, text=True)


def test_push_tags_is_nonfatal_on_remote_failure_and_noop_for_empty(monkeypatch, tmp_path, capsys):
    calls = []
    monkeypatch.setattr(cli.subprocess, "run", lambda argv, **kwargs: calls.append(argv) or SimpleNamespace(returncode=1))
    cli._push_tags(tmp_path, [])
    assert calls == []
    cli._push_tags(tmp_path, ["demo-v1"])
    assert calls == [["git", "-C", str(tmp_path), "push", "origin", "demo-v1"]]
    assert "continuing" in capsys.readouterr().out
