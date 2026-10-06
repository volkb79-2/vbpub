"""CLI dispatch protocol witnesses for supported non-publishing modes."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from cmru import cli, transaction


def _config_tuple(tmp_path, projects=None, order=None, defaults=None, steps=None):
    projects = projects or {}
    return (
        tmp_path, projects, order or list(projects), defaults or list(projects),
        steps or [], "project-first", {}, SimpleNamespace(),
        cli.GitHubConfig("owner", "repo", "token", "user"),
        cli.ReleaseEnvConfig({}, None),
    )


def test_worktrees_json_dispatch_emits_machine_readable_records(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(cli, "_current_git_root", lambda: tmp_path)
    workspaces = [transaction.ReleaseWorkspace(tmp_path, tmp_path, "cmru/release/abc", "a" * 40)]
    monkeypatch.setattr(cli.transaction, "list_cmru_workspaces", lambda root: workspaces)
    monkeypatch.setattr(
        cli.transaction, "read_release_scope_for_workspace",
        lambda _root, _workspace: ["demo"],
    )
    cli.main(["worktrees", "--json"])
    record = json.loads(capsys.readouterr().out)
    assert record == [{
        "branch": "cmru/release/abc",
        "path": str(tmp_path),
        "purpose": "release",
        "source_commit": "a" * 40,
        "prunable": False,
        "project_scope": ["demo"],
        "project_scope_state": "recorded",
    }]


@pytest.mark.parametrize(
    ("scope_state", "scope"),
    [("missing", None), ("unreadable", RuntimeError("corrupt scope"))],
)
def test_worktrees_json_reports_unavailable_scope_without_guessing(
    monkeypatch, capsys, tmp_path, scope_state, scope,
):
    monkeypatch.setattr(cli, "_current_git_root", lambda: tmp_path)
    workspace = transaction.ReleaseWorkspace(
        tmp_path, tmp_path / "foreign-namespace" / "missing",
        "cmru-release-20261001_120000-demo-abcdef", "a" * 40,
    )
    monkeypatch.setattr(cli.transaction, "list_cmru_workspaces", lambda _root: [workspace])

    def read_scope(_root, _workspace):
        if isinstance(scope, Exception):
            raise scope
        return scope

    monkeypatch.setattr(cli.transaction, "read_release_scope_for_workspace", read_scope)

    cli.main(["worktrees", "--json"])

    record = json.loads(capsys.readouterr().out)[0]
    assert record["project_scope_state"] == scope_state
    assert record["project_scope"] is None


def test_dependencies_dispatch_writes_and_reports_config_errors(monkeypatch, capsys, tmp_path):
    cfg = tmp_path / "cmru.orchestration.toml"; cfg.write_text("[orchestration]\n")
    forge = SimpleNamespace(repo_root=tmp_path, orchestration=SimpleNamespace(project_order=["demo"], dependencies={}), projects={})
    report = SimpleNamespace(errors=(), as_dict=lambda: {"ok": True})
    monkeypatch.setattr(cli, "_resolve_config", lambda value: cfg)
    monkeypatch.setattr(cli, "load_forge_config", lambda path: forge)
    monkeypatch.setattr(cli, "build_report", lambda **kwargs: report)
    written = []
    monkeypatch.setattr("cmru.dependencies.write_comment_block", lambda path, value: written.append(path))
    cli.main(["dependencies", "--config", str(cfg), "--write", "--json"])
    output = capsys.readouterr().out
    assert written == [cfg] and json.loads(output[output.index("{"):])["ok"] is True
    forge.orchestration = None
    error = cli.main(["dependencies", "--config", str(cfg)])
    assert error == 2


def test_changelog_dispatch_refuses_unknown_or_disabled_project(monkeypatch, capsys, tmp_path):
    cfg = tmp_path / "cmru.toml"; cfg.write_text("[project]\n")
    disabled = SimpleNamespace(changelog=None, prefix="demo-v")
    monkeypatch.setattr(cli, "_resolve_config", lambda value: cfg)
    monkeypatch.setattr(cli, "load_config", lambda path: _config_tuple(tmp_path, {"demo": disabled}))
    unknown = cli.main(["changelog", "--config", str(cfg), "missing", "--backfill-tag", "demo-v1"])
    assert unknown == 2
    disabled_error = cli.main(["changelog", "--config", str(cfg), "demo", "--backfill-tag", "demo-v1"])
    assert disabled_error == 2
    assert "history is explicitly disabled" in capsys.readouterr().err


def test_status_dispatch_selects_orchestrated_project_and_forwards_version_flags(monkeypatch, tmp_path):
    cfg = tmp_path / "cmru.toml"; cfg.write_text("[project]\n")
    project = cli.ProjectConfig(name="demo", env={}, steps={})
    monkeypatch.setattr(cli, "_resolve_config", lambda value: cfg)
    monkeypatch.setattr(cli, "load_config", lambda path: _config_tuple(tmp_path, {"demo": project}, ["demo"]))
    monkeypatch.setattr(cli, "apply_release_env", lambda *args: None)
    monkeypatch.setattr(cli.transaction, "project_git_family_groups", lambda root, projects: {root: list(projects)})
    calls = []
    monkeypatch.setattr("cmru.version.status_cmd", lambda root, projects, **kwargs: calls.append((root, projects, kwargs)))
    cli.main(["status", "--config", str(cfg), "demo", "--major"])
    assert calls[0][1]["demo"].name == project.name
    assert calls[0][1]["demo"].project_root == tmp_path
    assert calls[0][2] == {"minor": False, "major": True, "set_version": None, "ref": "HEAD"}


def test_status_dispatch_forwards_a_custom_ref(monkeypatch, tmp_path):
    # KI-20: --ref origin/main reaches status_cmd instead of the default HEAD.
    cfg = tmp_path / "cmru.toml"; cfg.write_text("[project]\n")
    project = cli.ProjectConfig(name="demo", env={}, steps={})
    monkeypatch.setattr(cli, "_resolve_config", lambda value: cfg)
    monkeypatch.setattr(cli, "load_config", lambda path: _config_tuple(tmp_path, {"demo": project}, ["demo"]))
    monkeypatch.setattr(cli, "apply_release_env", lambda *args: None)
    monkeypatch.setattr(cli.transaction, "project_git_family_groups", lambda root, projects: {root: list(projects)})
    calls = []
    monkeypatch.setattr("cmru.version.status_cmd", lambda root, projects, **kwargs: calls.append((root, projects, kwargs)))
    cli.main(["status", "--config", str(cfg), "--ref", "origin/main"])
    assert calls[0][2]["ref"] == "origin/main"


def test_build_transaction_child_dispatches_isolated_phases_without_transaction(monkeypatch, tmp_path):
    cfg = tmp_path / "cmru.toml"; cfg.write_text("[project]\n")
    project = SimpleNamespace(name="demo", github_token="token")
    monkeypatch.setattr(cli, "_resolve_config", lambda value: cfg)
    monkeypatch.setattr(cli, "load_config", lambda path: _config_tuple(tmp_path, {"demo": project}, ["demo"]))
    monkeypatch.setattr(cli, "apply_release_env", lambda *args: None)
    monkeypatch.setattr(cli, "_run_isolated_build_projects", lambda root, configs, names: calls.append((root, names)))
    calls = []
    monkeypatch.setattr(cli.transaction, "is_transaction_child", lambda _root: True)
    cli.main(["build", "--config", str(cfg), "demo"])
    assert calls == [(tmp_path, ["demo"])]


def test_publish_dispatch_refuses_missing_project_credential_before_runner(monkeypatch, tmp_path, capsys):
    cfg = tmp_path / "cmru.toml"; cfg.write_text("[project]\n")
    project = SimpleNamespace(name="demo", github_token="")
    monkeypatch.setattr(cli, "_resolve_config", lambda value: cfg)
    monkeypatch.setattr(cli, "load_config", lambda path: _config_tuple(tmp_path, {"demo": project}, ["demo"]))
    monkeypatch.setattr(cli, "apply_release_env", lambda *args: None)
    ran = []
    monkeypatch.setattr(cli, "_run_project_steps", lambda *args, **kwargs: ran.append(True))
    assert cli.main(["publish", "--config", str(cfg), "--from-checkout", "demo"]) == 1
    assert "Publishing requires" in capsys.readouterr().err
    assert ran == []


def test_release_protocol_rejects_removed_abandon_option_and_checks_child_provenance(monkeypatch, tmp_path):
    cfg = tmp_path / "cmru.toml"; cfg.write_text("[project]\n")
    monkeypatch.setattr(cli, "_resolve_config", lambda value: cfg)
    assert cli.main(["release", "--config", str(cfg), "--resume", "/tmp/x", "--abandon", "/tmp/y"]) == 2
    monkeypatch.delenv(transaction.BRANCH_ENV, raising=False)
    monkeypatch.delenv(transaction.BASE_ENV, raising=False)
    with pytest.raises(RuntimeError, match="missing transaction provenance"):
        cli._transaction_workspace_from_env(tmp_path)


def test_tag_on_head_uses_semver_and_ignores_latest_pointer(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "config", "user.email", "t@x"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    (tmp_path / "x").write_text("x")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "initial"], cwd=tmp_path, check=True)
    for tag in ("demo-v1.2.0", "demo-v1.10.0", "demo-latest"):
        subprocess.run(["git", "tag", tag], cwd=tmp_path, check=True)
    assert cli._tag_on_head(tmp_path, "demo-v") == "demo-v1.10.0"
