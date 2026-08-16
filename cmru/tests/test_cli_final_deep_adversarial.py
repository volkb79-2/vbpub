"""Final whole-family behavioural tests for CMRU CLI dispatch/release paths."""
from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from cmru import cli, transaction


def _project(name="demo", *, git_tag=True, build_step="build", steps=None, strategy="scm"):
    return SimpleNamespace(
        name=name, cwd=name, project_root=Path(name), paths=[name], runner_steps=steps or {},
        steps=steps or {}, env={}, github_token="tok", prefix=f"{name}-v", scm_dist=None,
        git_tag=git_tag, build_step=build_step, version=SimpleNamespace(strategy=strategy),
        changelog=None, commit_generated=(),
    )


def test_transaction_workspace_provenance_requires_both_child_environment_values(monkeypatch, tmp_path):
    monkeypatch.delenv(transaction.BRANCH_ENV, raising=False)
    monkeypatch.delenv(transaction.BASE_ENV, raising=False)
    with pytest.raises(RuntimeError, match="provenance"):
        cli._transaction_workspace_from_env(tmp_path)
    monkeypatch.setenv(transaction.BRANCH_ENV, "cmru/release/x")
    monkeypatch.setenv(transaction.BASE_ENV, "a" * 40)
    workspace = cli._transaction_workspace_from_env(tmp_path)
    assert workspace.path == tmp_path and workspace.branch == "cmru/release/x"


def test_declared_generated_paths_match_exact_file_or_child_only():
    assert cli._is_declared_generated("demo/CHANGES.md", ["demo/CHANGES.md"])
    assert cli._is_declared_generated("demo/generated/out.txt", ["demo/generated"])
    assert not cli._is_declared_generated("demo/generated-other", ["demo/generated"])


def test_release_sequentially_no_tag_no_build_records_nothing_but_checkpoints(monkeypatch, tmp_path):
    project = _project(git_tag=False)
    workspace = transaction.ReleaseWorkspace(tmp_path, tmp_path, "cmru/release/x", "a" * 40)
    progress, calls = [], []
    monkeypatch.setattr(cli.transaction, "write_release_progress", lambda *args: progress.append(args[-1]))
    monkeypatch.setattr(cli, "apply_project_release_env", lambda *args: None)
    monkeypatch.setattr(cli, "_prepare_release_projects", lambda *args, **kwargs: calls.append("prepare"))
    monkeypatch.setattr(cli, "_run_release_gates", lambda *args: calls.append("gate"))
    monkeypatch.setattr(cli.transaction, "promote_workspace", lambda *_: calls.append("promote"))
    monkeypatch.setattr(cli.transaction, "push_backup_branch", lambda *_: calls.append("backup"))
    monkeypatch.setattr(cli, "_git", lambda *_args: "b" * 40)
    result = cli._release_projects_sequentially(
        tmp_path, {"demo": project}, workspace, ["demo"],
        github_config=SimpleNamespace(), env_config=SimpleNamespace(), no_build=True,
    )
    assert result == []
    assert calls == ["prepare", "gate", "promote", "backup"]
    assert progress == ["a" * 40, "b" * 40]


def test_release_sequentially_tagged_no_build_records_tag_and_skips_artifact_steps(monkeypatch, tmp_path):
    project = _project(git_tag=True)
    workspace = transaction.ReleaseWorkspace(tmp_path, tmp_path, "cmru/release/x", "a" * 40)
    results, pushed = [], []
    monkeypatch.setattr(cli.transaction, "write_release_progress", lambda *args: None)
    monkeypatch.setattr(cli, "apply_project_release_env", lambda *args: None)
    monkeypatch.setattr(cli, "_prepare_release_projects", lambda *args, **kwargs: None)
    monkeypatch.setattr(cli, "_run_release_gates", lambda *args: None)
    monkeypatch.setattr(cli.transaction, "promote_workspace", lambda *_: None)
    monkeypatch.setattr(cli.transaction, "push_backup_branch", lambda *_: None)
    monkeypatch.setattr(cli, "_git", lambda *_args: "b" * 40)
    monkeypatch.setattr(cli, "_tag_on_head", lambda *_: "demo-v1.2.3")
    monkeypatch.setattr(cli, "_push_tags", lambda _root, tags: pushed.extend(tags))
    monkeypatch.setattr(cli.transaction, "write_release_result", lambda *args: results.append(args[-1]))
    monkeypatch.setattr("cmru.version.release_cmd", lambda *args, **kwargs: None)
    assert cli._release_projects_sequentially(
        tmp_path, {"demo": project}, workspace, ["demo"],
        github_config=SimpleNamespace(), env_config=SimpleNamespace(), no_build=True,
    ) == []
    assert pushed == ["demo-v1.2.3"] and results == ["demo-v1.2.3"]


def test_isolated_build_refuses_missing_build_step_and_does_not_invent_phase(monkeypatch, tmp_path):
    project = _project(build_step="")
    with pytest.raises(RuntimeError, match="build_step is absent"):
        cli._run_isolated_build_projects(tmp_path, {"demo": project}, ["demo"])
    project = _project(build_step="prepare", steps={"prepare": object(), "run-tests": object()})
    calls = []
    monkeypatch.setattr(cli, "_run_project_steps", lambda *args: calls.append(args[3]))
    cli._run_isolated_build_projects(tmp_path, {"demo": project}, ["demo"])
    assert calls == [["prepare", "run-tests"]]


def test_untagged_project_prepared_build_does_not_rebuild_but_requires_push(monkeypatch, tmp_path):
    project = _project(git_tag=False, build_step="prepare", steps={"prepare": object(), "push": object()})
    calls = []
    monkeypatch.setattr(cli, "resolve_versions_from_git", lambda *_: None)
    monkeypatch.setattr(cli, "apply_project_release_env", lambda *_: None)
    monkeypatch.setattr(cli, "run_project_step", lambda project, step, *_: calls.append(step))
    monkeypatch.setattr(cli, "_worktree_changed_paths", lambda *_: [])
    cli._run_untagged_project(tmp_path, {"demo": project}, "demo", github_config=SimpleNamespace(), env_config=SimpleNamespace())
    assert calls == ["push"]


def test_orchestrate_rejects_unknown_selected_project_before_running_steps(monkeypatch, tmp_path):
    loaded = (tmp_path, {"demo": _project()}, ["demo"], ["demo"], ["run-tests"], "project-first", {}, SimpleNamespace(), SimpleNamespace(), SimpleNamespace())
    monkeypatch.setattr(cli, "_resolve_config", lambda _: tmp_path / "cmru.toml")
    monkeypatch.setattr(cli, "load_config", lambda _: loaded)
    monkeypatch.setattr(cli.sys, "argv", ["cmru", "--project", "missing"])
    with pytest.raises(ValueError, match="Unknown project"):
        cli._orchestrate()


def test_main_changelog_dispatch_distinguishes_unknown_disabled_and_unchanged(monkeypatch, tmp_path, capsys):
    project = _project()
    loaded = (tmp_path, {"demo": project}, ["demo"], ["demo"], [], "project-first", {}, SimpleNamespace(), SimpleNamespace(), SimpleNamespace())
    monkeypatch.setattr(cli, "_resolve_config", lambda _: tmp_path / "cmru.toml")
    monkeypatch.setattr(cli, "load_config", lambda _: loaded)
    with pytest.raises(SystemExit) as exc:
        cli.main(["changelog", "--project", "missing", "--backfill-tag", "demo-v1"])
    assert exc.value.code == 2 and "Unknown project" in capsys.readouterr().err
    project.changelog = None
    with pytest.raises(SystemExit) as exc:
        cli.main(["changelog", "--project", "demo", "--backfill-tag", "demo-v1"])
    assert exc.value.code == 2 and "disabled" in capsys.readouterr().err
    project.changelog = "CHANGES.md"
    monkeypatch.setattr("cmru.changelog.backfill_release_changelog", lambda *_: False)
    cli.main(["changelog", "--project", "demo", "--backfill-tag", "demo-v1"])
    assert "already records" in capsys.readouterr().out


def test_main_release_rejects_mutually_exclusive_resume_and_abandon(monkeypatch, tmp_path, capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["release", "--resume", "/tmp/x", "--abandon", "/tmp/y", "--config", str(tmp_path / "cmru.toml")])
    assert exc.value.code == 2
    assert "mutually exclusive" in capsys.readouterr().err


def test_main_build_unknown_project_exits_before_external_transaction(monkeypatch, tmp_path, capsys):
    loaded = (tmp_path, {"demo": _project()}, ["demo"], ["demo"], [], "project-first", {}, SimpleNamespace(), cli.GitHubConfig("o", "r", "", "user"), cli.ReleaseEnvConfig({}, None))
    monkeypatch.setattr(cli, "_resolve_config", lambda _: tmp_path / "cmru.toml")
    monkeypatch.setattr(cli, "load_config", lambda _: loaded)
    with pytest.raises(SystemExit) as exc:
        cli.main(["build", "--project", "missing", "--config", str(tmp_path / "cmru.toml")])
    assert exc.value.code == 2 and "Unknown project" in capsys.readouterr().err


def test_main_publish_requires_project_credentials_before_running_push(monkeypatch, tmp_path):
    project = _project()
    project.github_token = ""
    loaded = (tmp_path, {"demo": project}, ["demo"], ["demo"], [], "project-first", {}, SimpleNamespace(), cli.GitHubConfig("o", "r", "", "user"), cli.ReleaseEnvConfig({}, None))
    monkeypatch.setattr(cli, "_resolve_config", lambda _: tmp_path / "cmru.toml")
    monkeypatch.setattr(cli, "load_config", lambda _: loaded)
    with pytest.raises(RuntimeError, match="Publishing requires"):
        cli.main(["publish", "--project", "demo", "--config", str(tmp_path / "cmru.toml")])
