"""Behavioral tests for CMRU's isolated orchestration and cleanup flows."""
from __future__ import annotations

import subprocess
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from cmru import cli, transaction


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=root, text=True, capture_output=True)
    if result.returncode:
        raise AssertionError(result.stderr)
    return result.stdout.strip()


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"; root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "test@example.invalid")
    _git(root, "config", "user.name", "test")
    (root / "demo").mkdir(); (root / "demo" / "x").write_text("x\n")
    _git(root, "add", "."); _git(root, "commit", "-qm", "feat: initial")
    return root


def _project(name="demo", **kwargs):
    defaults = dict(name=name, cwd=name, paths=[name], runner_steps={}, steps={},
                    build_step="build", commit_generated=[], changelog=None,
                    project_root=None, env={}, version=SimpleNamespace(strategy="scm"),
                    git_tag=True, prefix=f"{name}-v", github_token="token")
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def test_run_project_steps_requires_declared_step_and_routes_order(monkeypatch, tmp_path):
    project = _project(project_root=tmp_path / "demo")
    project.runner_steps = {"test": object(), "build": object()}
    calls = []
    monkeypatch.setattr(cli, "resolve_versions_from_git", lambda *args: None)
    monkeypatch.setattr(cli, "run_project_step", lambda p, step, root, logs: calls.append(step))
    cli._run_project_steps(tmp_path, {"demo": project}, ["demo"], ["test", "build"])
    assert calls == ["test", "build"]
    with pytest.raises(RuntimeError, match="not declared"):
        cli._run_project_steps(tmp_path, {"demo": project}, ["demo"], ["push"])


def test_isolated_build_includes_prepare_gate_and_artifact_once(monkeypatch, tmp_path):
    project = _project(build_step="wheel", runner_steps={"prepare": object(), "run-tests": object(), "wheel": object()})
    calls = []
    monkeypatch.setattr(cli, "_run_project_steps", lambda root, cfg, names, steps: calls.append(steps))
    cli._run_isolated_build_projects(tmp_path, {"demo": project}, ["demo"])
    assert calls == [["prepare", "run-tests", "wheel"]]
    project.build_step = None
    with pytest.raises(RuntimeError, match="build_step is absent"):
        cli._run_isolated_build_projects(tmp_path, {"demo": project}, ["demo"])


def test_untagged_project_uses_prepare_output_or_refuses_post_tag_mutation(monkeypatch, tmp_path):
    project = _project(git_tag=False, build_step="prepare", runner_steps={"prepare": object(), "push": object()})
    calls = []
    monkeypatch.setattr(cli, "resolve_versions_from_git", lambda *args: None)
    monkeypatch.setattr(cli, "apply_project_release_env", lambda *args: None)
    monkeypatch.setattr(cli, "run_project_step", lambda p, step, root, logs: calls.append(step))
    monkeypatch.setattr(cli, "_worktree_changed_paths", lambda root: [])
    cli._run_untagged_project(tmp_path, {"demo": project}, "demo", github_config=None, env_config=None)
    assert calls == ["push"]
    project.build_step = "build"
    calls.clear(); monkeypatch.setattr(cli, "_worktree_changed_paths", lambda root: ["demo/generated"])
    with pytest.raises(RuntimeError, match="changed tracked source"):
        cli._run_untagged_project(tmp_path, {"demo": project}, "demo", github_config=None, env_config=None)
    assert calls == ["build"]


def test_release_gate_refuses_missing_run_tests_before_runner(monkeypatch, tmp_path):
    project = _project(steps={}, project_root=tmp_path / "demo")
    with pytest.raises(RuntimeError, match="no release gate"):
        cli._run_release_gates(tmp_path, {"demo": project}, ["demo"])
    project.steps = {"run-tests": [object()]}
    calls = []
    monkeypatch.setattr(cli, "run_project_step", lambda *args: calls.append(args[1]))
    cli._run_release_gates(tmp_path, {"demo": project}, ["demo"])
    assert calls == ["run-tests"]


def test_child_release_args_removes_caller_paths_and_adds_snapshot_relative_config(tmp_path):
    config = tmp_path / "cmru.toml"; config.write_text("x")
    args = cli._child_release_args(
        ["--config", "/caller/cmru.toml", "--resume", "/tmp/w", "--abandon=all-previous", "--project", "demo"],
        config, tmp_path,
    )
    assert args == ["--project", "demo", "--config", "cmru.toml"]
    with pytest.raises(ValueError, match="tracked inside"):
        cli._child_release_args([], tmp_path.parent / "outside.toml", tmp_path)


def test_worktree_change_detection_and_generated_commit_are_scoped(tmp_path):
    root = _repo(tmp_path)
    project = _project(project_root=root / "demo", commit_generated=["generated.txt"])
    (root / "demo" / "generated.txt").write_text("generated\n")
    assert cli._worktree_changed_paths(root) == ["demo/generated.txt"]
    assert cli._commit_prepared_generated(root, project) is True
    assert _git(root, "status", "--porcelain") == ""
    assert "prepare release inputs" in _git(root, "log", "-1", "--format=%s")


def test_generated_commit_rejects_unexpected_prepare_mutation(tmp_path):
    root = _repo(tmp_path)
    project = _project(project_root=root / "demo", commit_generated=["generated.txt"])
    (root / "demo" / "generated.txt").write_text("generated\n")
    (root / "demo" / "unexpected.txt").write_text("unexpected\n")
    with pytest.raises(RuntimeError, match="undeclared paths"):
        cli._commit_prepared_generated(root, project)
    assert _git(root, "status", "--porcelain")


def test_cleanup_release_tag_union_keeps_latest_and_deletes_tag_only(monkeypatch, tmp_path):
    deleted = []
    monkeypatch.setattr(cli, "list_releases", lambda *args: [
        {"tag_name": "demo-v1.0.0", "id": 1}, {"tag_name": "demo-latest", "id": 2},
    ])
    monkeypatch.setattr(cli, "list_remote_tags_matching", lambda *args: ["demo-v1.0.0", "demo-v2.0.0"])
    monkeypatch.setattr(cli, "delete_release", lambda *args, **kwargs: deleted.append(("release", args[3])))
    monkeypatch.setattr(cli, "delete_git_tag_remote", lambda root, tag, dry_run: deleted.append(("remote", tag)))
    monkeypatch.setattr(cli, "delete_git_tag_local", lambda root, tag, dry_run: deleted.append(("local", tag)))
    result = cli.cleanup_project_releases_and_tags(tmp_path, "o", "r", "t", "demo", [], False)
    assert result == ["demo-v1.0.0", "demo-v2.0.0"]
    assert ("release", 1) in deleted and ("remote", "demo-latest") not in deleted


def test_cleanup_unmanaged_release_is_idempotent_and_preserves_tag(monkeypatch):
    monkeypatch.setattr(cli, "list_releases", lambda *args: [])
    assert cli.delete_unmanaged_release_tag("o", "r", "t", "old", dry_run=False) is False
    monkeypatch.setattr(cli, "list_releases", lambda *args: [{"tag_name": "old", "id": 4}])
    calls = []
    monkeypatch.setattr(cli, "delete_release", lambda *args, **kwargs: calls.append(args[3]))
    assert cli.delete_unmanaged_release_tag("o", "r", "t", "old", dry_run=True) is True
    assert calls == []
    assert cli.delete_unmanaged_release_tag("o", "r", "t", "old", dry_run=False) is True
    assert calls == [4]


def test_cleanup_ghcr_handles_protected_and_forbidden_versions(monkeypatch, capsys):
    # Exercise the real destructive boundary with controlled HTTP responses.
    monkeypatch.setattr(cli, "http_request", lambda *args: (400, "cannot be deleted", {}))
    cli.delete_package_version("o", "pkg", "t", 1, "org", False)
    first = capsys.readouterr().out
    assert "Skipping GHCR cleanup" in first and "cannot be deleted" in first
    monkeypatch.setattr(cli, "http_request", lambda *args: (403, "forbidden", {}))
    cli.delete_package_version("o", "pkg", "t", 2, "org", False)
    second = capsys.readouterr().out
    assert "missing package delete scope" in second
    cli.delete_package("o", "pkg", "t", "org", False)
    third = capsys.readouterr().out
    assert "Skipping GHCR package delete" in third and "missing package delete scope" in third


def test_cleanup_project_step_dry_run_has_no_runner_side_effect(monkeypatch, tmp_path):
    project = _project(steps={"clean": [SimpleNamespace(label="clean", argv=["true"], cwd=tmp_path)]})
    assert cli.cleanup_project_step(tmp_path, project, "1.2.3", True) is False
    calls = []
    monkeypatch.setattr("cmru.runner.execute_step", lambda *args, **kwargs: calls.append(kwargs["extra_env"]))
    assert cli.cleanup_project_step(tmp_path, project, "1.2.3", False) is True
    assert calls == [{"CMRU_VERSION": "1.2.3"}]
