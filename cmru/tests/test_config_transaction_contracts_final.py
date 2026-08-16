"""Contract witnesses for strict config policy and transaction safety edges."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from cmru import config, transaction


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
    (root / "demo" / "source.py").write_text("x = 1\n", encoding="utf-8")
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
[steps.run-tests]
quiet = true
commands = [{{label = "tests", argv = ["echo", "ok"], cwd = "."}}]
[steps.build]
quiet = true
commands = [{{label = "build", argv = ["echo", "ok"], cwd = "."}}]
[steps.push]
quiet = true
commands = [{{label = "push", argv = ["echo", "ok"], cwd = "."}}]
'''


def orchestration(entry: str = 'config = "demo/cmru.toml"', order: str = '["demo"]') -> str:
    return f'''schema_version = 1
[orchestration]
project_order = {order}
default_projects = ["demo"]
default_steps = ["run-tests", "build", "push"]
execution_mode = "project-first"
[orchestration.project.demo]
{entry}
[cleanup]
release_tag_prefixes = []
keep_release_tags = []
ghcr_packages = []
ghcr_delete_packages = []
'''


def test_config_rejects_project_metadata_and_runner_schema_shapes(tmp_path, capsys):
    project = tmp_path / "cmru.toml"
    base = project_toml()
    cases = (
        (base + "\nproject_metadata = []\n", "project_metadata"),
        (base + "\nbuild_metadata = { unsupported = \"x\" }\n", "build_metadata"),
        (
            base.replace(
                'commands = [{label = "build", argv = ["echo", "ok"], cwd = "."}]',
                'commands = ["bad"]',
            ),
            "commands[0]",
        ),
    )
    for raw, diagnostic in cases:
        project.write_text(raw, encoding="utf-8")
        with pytest.raises(SystemExit) as error:
            config.load_forge_config(project)
        assert diagnostic in capsys.readouterr().out


def test_config_orchestration_refuses_unknown_and_misordered_dependencies(tmp_path):
    project = tmp_path / "demo"; project.mkdir()
    (project / "cmru.toml").write_text(project_toml(), encoding="utf-8")
    path = tmp_path / "cmru.orchestration.toml"
    cases = [
        orchestration(entry='config = "demo/cmru.toml"\ndepends_on = ["missing"]'),
        orchestration(entry='config = "demo/cmru.toml"\ndepends_on = ["demo"]'),
        orchestration(order='["other"]'),
        orchestration(entry='config = "demo/cmru.toml"\ndepends_on = ["other"]', order='["demo"]'),
    ]
    for raw in cases:
        path.write_text(raw, encoding="utf-8")
        with pytest.raises(SystemExit):
            config.load_forge_config(path)


def test_transaction_fetch_and_divergence_fail_closed(tmp_path):
    root = repo(tmp_path)
    with patch.object(transaction.subprocess, "run", side_effect=subprocess.CalledProcessError(1, "git fetch")):
        with pytest.raises(subprocess.CalledProcessError):
            transaction.fetch_origin_main(root)
    with patch.object(transaction, "_git", return_value="not two counts"):
        with pytest.raises(RuntimeError, match="Cannot compare"):
            transaction.local_main_divergence(root)
    with patch.object(transaction, "local_main_divergence", return_value=(2, 0)):
        with pytest.raises(RuntimeError, match="ahead"):
            transaction.assert_local_main_not_ahead(root)


def test_transaction_resume_validates_branch_and_refreshes_base(tmp_path):
    root = repo(tmp_path)
    workspace = transaction.create_workspace(root, base=git(root, "rev-parse", "HEAD"), purpose="release")
    original_run = transaction.subprocess.run
    def fake_run(argv, **kwargs):
        if argv[:2] == ["git", "fetch"]:
            return subprocess.CompletedProcess(argv, 0)
        return original_run(argv, **kwargs)
    with patch.object(transaction.subprocess, "run", side_effect=fake_run):
        resumed = transaction.resume_workspace(root, workspace.path)
    assert resumed.branch == workspace.branch
    assert resumed.base == git(root, "rev-parse", "HEAD")
    transaction.remove_workspace(workspace)


def test_transaction_release_lock_refuses_second_holder(tmp_path):
    root = repo(tmp_path)
    with transaction.release_lock(root):
        with pytest.raises(RuntimeError, match="already running"):
            with transaction.release_lock(root):
                pass


def test_transaction_release_retention_preflight_preserves_all_sources(tmp_path):
    root = repo(tmp_path)
    workspace = transaction.create_workspace(root, base=git(root, "rev-parse", "HEAD"), purpose="release")
    project_root = root / "demo"
    child = workspace.path / "demo"
    (child / "logs").mkdir(parents=True)
    (child / "logs" / "step.log").write_text("log", encoding="utf-8")
    project = SimpleNamespace(project_root=project_root, artifact_dirs=["dist"])
    with pytest.raises(RuntimeError, match="missing"):
        transaction.retain_success_outputs(root, workspace, {"demo": project}, {"demo": "tag"}, retain_logs=True, retain_artifacts=True)
    assert (child / "logs" / "step.log").is_file()
    assert not (project_root / "logs" / "cmru-release" / "tag").exists()
    assert not (project_root / "artifacts" / "tag").exists()
    transaction.remove_workspace(workspace)


def test_transaction_retain_release_artifacts_writes_authenticated_manifest(tmp_path):
    root = repo(tmp_path)
    workspace = transaction.create_workspace(root, base=git(root, "rev-parse", "HEAD"), purpose="release")
    child = workspace.path / "demo"
    (child / "dist").mkdir(parents=True)
    (child / "dist" / "demo.whl").write_bytes(b"wheel")
    project = SimpleNamespace(project_root=root / "demo", artifact_dirs=["dist"])
    retained = transaction.retain_success_outputs(root, workspace, {"demo": project}, {"demo": "tag"}, retain_logs=False, retain_artifacts=True)
    manifest = json.loads((retained[0] / "release.json").read_text(encoding="utf-8"))
    assert manifest["project"] == "demo"
    assert manifest["artifacts"][0]["files"][0]["bytes"] == "5"
    transaction.remove_workspace(workspace)


def test_transaction_list_workspaces_keeps_missing_recorded_paths_visible(tmp_path):
    root = repo(tmp_path)
    missing = tmp_path / "gone"
    raw = f"worktree {missing}\nHEAD {'a' * 40}\nbranch refs/heads/cmru/release/old\n"
    with patch.object(transaction, "_git", return_value=raw):
        listed = transaction.list_cmru_workspaces(root)
    assert listed[0].path == missing
    assert listed[0].base == ""
