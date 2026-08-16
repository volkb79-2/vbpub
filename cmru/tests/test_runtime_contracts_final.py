"""Contract tests for remaining CMRU runtime/config/version boundaries."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from cmru import config, runner, transaction, version


def git(root: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.email", "test@example.invalid")
    git(root, "config", "user.name", "test")
    (root / "demo").mkdir()
    (root / "demo" / "cmru.toml").write_text("placeholder\n", encoding="utf-8")
    git(root, "add", ".")
    git(root, "commit", "-q", "-m", "initial")
    return root


def project_toml(name: str = "demo") -> str:
    return f'''schema_version = 1
[github]
owner = "acme"
repo = "vbpub"
owner_type = "org"
[targets]
host = "github"
registry = []
[project]
id = "{name}"
description = "test"
prefix = "{name}-v"
artifacts = ["wheel"]
[project.version]
strategy = "scm"
bump = "patch"
[project.release]
git_tag = true
build_step = "build"
artifact_dirs = ["dist"]
[steps.build]
quiet = true
commands = [{{label = "build", argv = ["echo", "ok"], cwd = "."}}]
[steps.run-tests]
quiet = true
commands = [{{label = "tests", argv = ["echo", "ok"], cwd = "."}}]
[steps.push]
quiet = true
commands = [{{label = "push", argv = ["echo", "ok"], cwd = "."}}]
'''


def orchestration_toml(entry: str = 'config = "demo/cmru.toml"', *, order: str = '["demo"]') -> str:
    return f'''schema_version = 1
[orchestration]
project_order = {order}
default_projects = ["demo"]
default_steps = ["build"]
execution_mode = "project-first"
[orchestration.project.demo]
{entry}
[cleanup]
release_tag_prefixes = []
keep_release_tags = []
ghcr_packages = []
ghcr_delete_packages = []
'''


def test_config_orchestration_refuses_invalid_schema_entries_and_dependency_order(tmp_path):
    project = tmp_path / "demo"; project.mkdir()
    (project / "cmru.toml").write_text(project_toml(), encoding="utf-8")
    path = tmp_path / "cmru.orchestration.toml"
    cases = [
        orchestration_toml().replace("schema_version = 1", "schema_version = 2"),
        orchestration_toml().replace('execution_mode = "project-first"', 'execution_mode = "bad"'),
        orchestration_toml(entry="config = \"../demo/cmru.toml\""),
        orchestration_toml(entry='config = "demo/cmru.toml"\ndepends_on = ["missing"]'),
        orchestration_toml(entry='config = "demo/cmru.toml"\ndepends_on = ["demo"]'),
    ]
    for raw in cases:
        path.write_text(raw, encoding="utf-8")
        with pytest.raises(SystemExit):
            config.load_forge_config(path)


def test_config_orchestration_accepts_project_and_cleanup_contract(tmp_path):
    project = tmp_path / "demo"; project.mkdir()
    (project / "cmru.toml").write_text(project_toml(), encoding="utf-8")
    path = tmp_path / "cmru.orchestration.toml"
    path.write_text(orchestration_toml(), encoding="utf-8")
    loaded = config.load_forge_config(path)
    assert loaded.orchestration.project_order == ["demo"]
    assert loaded.projects["demo"].project_root == project.resolve()


def test_config_read_toml_requires_named_table_and_cleanup_requires_lists(tmp_path):
    wrong = tmp_path / "wrong.toml"; wrong.write_text("[]\n", encoding="utf-8")
    with pytest.raises(SystemExit):
        config._read_toml(wrong, "cmru.toml")
    table = tmp_path / "cmru.toml"; table.write_text("'not-a-table'\n", encoding="utf-8")
    with pytest.raises(config.tomllib.TOMLDecodeError):
        config._read_toml(table, "cmru.toml")
    with pytest.raises(SystemExit):
        config._parse_cleanup({"max_age_days": 0, "release_tag_prefixes": [], "keep_release_tags": [], "ghcr_packages": [], "ghcr_delete_packages": []})


def test_runner_parse_step_rejects_each_declared_shape_and_preserves_date_contract(tmp_path, monkeypatch):
    base = {"steps": {"build": {"commands": [{"label": "x", "argv": ["echo"], "cwd": "."}], "quiet": True}}}
    for key, value in (("env", ["BAD"]), ("env_command", "echo"), ("login", []), ("quiet", "yes")):
        raw = {"steps": {"build": {**base["steps"]["build"], key: value}}}
        with pytest.raises(ValueError):
            runner.parse_step(raw, "build")
    monkeypatch.delenv("SOURCE_DATE_EPOCH", raising=False)
    monkeypatch.delenv("BUILD_DATE", raising=False)
    with patch.object(runner, "_git_out", return_value=None):
        with pytest.raises(RuntimeError, match="SOURCE_DATE_EPOCH"):
            runner.compute_build_date({"build_metadata": {"date_env": "BUILD_DATE"}}, tmp_path)


def test_runner_git_boundary_returns_none_on_missing_git_and_env_command_rejects_bad_line(tmp_path, monkeypatch):
    with patch.object(runner.subprocess, "run", side_effect=FileNotFoundError):
        assert runner._git_out(tmp_path, "status") is None
    with patch.object(runner.subprocess, "run", return_value=SimpleNamespace(stdout="not-an-assignment\n")):
        with pytest.raises(ValueError, match="KEY=VALUE"):
            runner.apply_env_command(["fake"], tmp_path)
    monkeypatch.setenv("CMRU_LOG_APPEND", "0")


def test_transaction_source_and_workspace_refusals_are_explicit(tmp_path):
    root = repo(tmp_path)
    with pytest.raises(ValueError, match="purpose"):
        transaction.create_workspace(root, base=git(root, "rev-parse", "HEAD"), purpose="publish")
    with pytest.raises(RuntimeError, match="does not exist"):
        transaction.resume_workspace(root, tmp_path / "missing")
    (root / "cmru.secret.toml").mkdir()
    workspace = transaction.ReleaseWorkspace(root, root, "cmru/release/x", "a" * 40)
    with pytest.raises(RuntimeError, match="regular file"):
        transaction.copy_secret_overlays(root, workspace, [])


def test_transaction_build_output_and_project_root_require_authenticated_source(tmp_path):
    root = repo(tmp_path)
    workspace = transaction.ReleaseWorkspace(root, root, "cmru/build/x", "base")
    with patch.object(transaction, "_git", side_effect=["not-a-sha"]):
        with pytest.raises(RuntimeError, match="source commit"):
            transaction.build_output_id(workspace)
    project = SimpleNamespace(project_root=tmp_path / "outside")
    with pytest.raises(RuntimeError, match="outside repository"):
        transaction._project_roots_for_retention(root, workspace, project, "demo")


def test_transaction_release_records_reject_non_string_result_shapes(tmp_path):
    root = repo(tmp_path)
    workspace = transaction.ReleaseWorkspace(root, root, "cmru/release/x", "a" * 40)
    scope = transaction._scope_dir(root); scope.mkdir()
    (scope / "x.results.json").write_text('{"demo": 3}', encoding="utf-8")
    with pytest.raises(RuntimeError, match="invalid release result"):
        transaction.read_release_results(root, workspace)
    (scope / "x.results.json").write_text('{"demo": "tag"}', encoding="utf-8")
    assert transaction.read_release_results(root, workspace) == {"demo": "tag"}


def test_version_file_and_counter_tag_failures_are_not_reported_as_success(tmp_path):
    (tmp_path / "VERSION").write_text("1.0.0\n", encoding="utf-8")
    responses = [SimpleNamespace(returncode=0), SimpleNamespace(returncode=0), SimpleNamespace(returncode=2)]
    with patch.object(version.subprocess, "run", side_effect=responses):
        with pytest.raises(SystemExit) as error:
            version._apply_strategy_file(tmp_path, "demo-v", "1.1.0", "VERSION", tmp_path)
        assert error.value.code == 1
    with patch.object(version.subprocess, "run", side_effect=[SimpleNamespace(returncode=0, stdout=""), SimpleNamespace(returncode=2)]):
        with pytest.raises(SystemExit) as error:
            version._apply_strategy_counter(tmp_path, "demo-v", "1.0.0")
        assert error.value.code == 1


def test_version_release_cmd_set_version_and_no_change_contracts(tmp_path):
    tmp_path = repo(tmp_path)
    project = SimpleNamespace(prefix="demo-v", version=SimpleNamespace(strategy="scm"), git_tag=True, cwd="demo")
    with patch.object(version, "detect_changed_projects", return_value=[]):
        assert version.release_cmd(tmp_path, {"demo": project}) == []
    with patch.object(version, "detect_changed_projects", return_value=[("demo", project, None, "patch")]), \
         patch.object(version, "_apply_strategy_scm", return_value="demo-v2.0.0") as apply:
        assert version.release_cmd(tmp_path, {"demo": project}, set_version="2.0.0", dry_run=True) == ["demo-v2.0.0"]
    apply.assert_called_once_with(tmp_path, "demo-v", "2.0.0", dry_run=True)
