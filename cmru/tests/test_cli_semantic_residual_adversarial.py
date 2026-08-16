from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

from cmru import cli, transaction


def _config(tmp_path, project):
    return (
        tmp_path, {project.name: project}, [project.name], [project.name], [],
        "project-first", {}, cli.CleanupConfig([], [], [], []),
        cli.GitHubConfig("owner", "repo", "token", "user"),
        cli.ReleaseEnvConfig({}, None),
    )


def test_build_refuses_uncommitted_snapshot_before_fetch_or_workspace(monkeypatch, tmp_path):
    project = cli.ProjectConfig("demo", {}, {}, project_root=tmp_path / "demo", build_step="build")
    monkeypatch.setattr(cli, "_resolve_config", lambda _: tmp_path / "cmru.toml")
    monkeypatch.setattr(cli, "load_config", lambda _: _config(tmp_path, project))
    monkeypatch.setattr(cli, "apply_release_env", lambda *_: None)
    monkeypatch.setattr(cli.transaction, "release_lock", lambda _: nullcontext())
    monkeypatch.setattr(cli, "_uncommitted_release_paths", lambda *args: {"demo": ["demo/input.py"]})
    monkeypatch.setattr(cli.transaction, "fetch_origin_main", lambda *_: (_ for _ in ()).throw(AssertionError("fetch")))
    with pytest.raises(SystemExit) as exc:
        cli.main(["build", "--config", str(tmp_path / "cmru.toml"), "--project", "demo"])
    assert exc.value.code == 1


def test_build_success_runs_child_retains_outputs_and_reports_cleanup_command(monkeypatch, tmp_path, capsys):
    project = cli.ProjectConfig("demo", {}, {}, project_root=tmp_path / "demo", build_step="build")
    monkeypatch.setattr(cli, "_resolve_config", lambda _: tmp_path / "cmru.toml")
    monkeypatch.setattr(cli, "load_config", lambda _: _config(tmp_path, project))
    monkeypatch.setattr(cli, "apply_release_env", lambda *_: None)
    monkeypatch.setattr(cli.transaction, "release_lock", lambda _: nullcontext())
    monkeypatch.setattr(cli, "_uncommitted_release_paths", lambda *args: {})
    workspace = transaction.ReleaseWorkspace(tmp_path, tmp_path / "child", "cmru/build/abc", "a" * 40)
    calls = []
    monkeypatch.setattr(cli.transaction, "fetch_origin_main", lambda *_: "b" * 40)
    monkeypatch.setattr(cli.transaction, "assert_local_main_not_ahead", lambda *_: 0)
    monkeypatch.setattr(cli.transaction, "create_workspace", lambda *args, **kwargs: workspace)
    monkeypatch.setattr(cli.transaction, "copy_secret_overlays", lambda *args: None)
    monkeypatch.setattr(cli.transaction, "run_child", lambda w, args, **kwargs: calls.append((w, args, kwargs)) or 0)
    retained = [tmp_path / "demo" / "artifacts" / "build-1"]
    monkeypatch.setattr(cli.transaction, "retain_successful_build_outputs", lambda *args: retained)
    monkeypatch.setattr(cli.transaction, "remove_workspace", lambda w: calls.append(("removed", w)))
    with pytest.raises(SystemExit) as exc:
        cli.main(["build", "--config", str(tmp_path / "cmru.toml"), "--project", "demo"])
    assert exc.value.code == 0
    assert calls[0][1] == ["--project", "demo", "--config", "cmru.toml"]
    assert calls[0][2] == {"verb": "build"}
    assert calls[-1] == ("removed", workspace)
    assert "--delete-build-output build-1 --yes" in capsys.readouterr().out


def test_isolated_build_requires_declared_artifact_step_and_orders_prepare_gate_build(monkeypatch, tmp_path):
    missing = cli.ProjectConfig("missing", {}, {}, build_step="")
    with pytest.raises(RuntimeError, match="build_step is absent"):
        cli._run_isolated_build_projects(tmp_path, {"missing": missing}, ["missing"])

    project = cli.ProjectConfig("demo", {}, {}, build_step="build", runner_steps={"prepare": [], "run-tests": [], "build": []})
    seen = []
    monkeypatch.setattr(cli, "_run_project_steps", lambda root, configs, names, phases: seen.append(phases))
    cli._run_isolated_build_projects(tmp_path, {"demo": project}, ["demo"])
    assert seen == [["prepare", "run-tests", "build"]]


def test_worktrees_dispatch_reports_empty_and_inaccessible_records(monkeypatch, capsys):
    monkeypatch.setattr(cli.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout="/repo\n"))
    monkeypatch.setattr(cli.transaction, "list_cmru_workspaces", lambda _: [])
    cli.main(["worktrees"])
    assert "No retained CMRU" in capsys.readouterr().out

    workspace = transaction.ReleaseWorkspace(Path("/repo"), Path("/missing"), "cmru/release/x", "a" * 40)
    monkeypatch.setattr(cli.transaction, "list_cmru_workspaces", lambda _: [workspace])
    cli.main(["worktrees"])
    output = capsys.readouterr().out
    assert "action: unavailable here" in output
