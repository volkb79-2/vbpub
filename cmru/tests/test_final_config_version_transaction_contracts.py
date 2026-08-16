"""Final source-guided contracts for config, version, and transaction edges."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from cmru import config, transaction, version


def git(root: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"; root.mkdir()
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.email", "test@example.invalid"); git(root, "config", "user.name", "test")
    (root / "demo").mkdir(); (root / "demo" / "source.py").write_text("x=1\n")
    git(root, "add", "."); git(root, "commit", "-q", "-m", "initial")
    return root


def project_doc(name="demo", owner="acme", registry="ghcr.io"):
    return f'''schema_version=1
[github]
owner="{owner}"
repo="vbpub"
owner_type="org"
[targets]
host="github"
registry=["{registry}"]
[project]
id="{name}"
description="demo"
prefix="{name}-v"
artifacts=["wheel"]
[project.version]
strategy="scm"
bump="patch"
[project.release]
git_tag=true
build_step="build"
artifact_dirs=["dist"]
[steps.run-tests]
quiet=true
commands=[{{label="tests",argv=["echo"],cwd="."}}]
[steps.build]
quiet=true
commands=[{{label="build",argv=["echo"],cwd="."}}]
[steps.push]
quiet=true
commands=[{{label="push",argv=["echo"],cwd="."}}]
'''


def orch(entry='config="demo/cmru.toml"', order='["demo"]'):
    return f'''schema_version=1
[orchestration]
project_order={order}
default_projects=["demo"]
default_steps=["run-tests","build","push"]
execution_mode="project-first"
[orchestration.project.demo]
{entry}
[cleanup]
release_tag_prefixes=[]
keep_release_tags=[]
ghcr_packages=[]
ghcr_delete_packages=[]
'''


def test_config_orchestration_rejects_missing_tables_and_cross_project_facts(tmp_path, capsys):
    project = tmp_path / "demo"; project.mkdir(); (project / "cmru.toml").write_text(project_doc())
    path = tmp_path / "cmru.orchestration.toml"
    cases = [
        (orch().replace("[orchestration]\n", "[orchestration]\ndefaults=[]\n"), "defaults"),
        (orch().replace("[orchestration.project.demo]\n", "[orchestration.project.demo]\nextra=true\n"), "unknown keys"),
        (orch(entry='config="demo/cmru.toml"', order='["demo","demo"]'), "duplicate"),
        (orch(entry='config="demo/cmru.toml"\ndepends_on=["missing"]'), "depends_on"),
    ]
    for raw, diagnostic in cases:
        path.write_text(raw, encoding="utf-8")
        with pytest.raises(SystemExit):
            config.load_forge_config(path)
        assert diagnostic in capsys.readouterr().out

    other = tmp_path / "other"; other.mkdir(); (other / "cmru.toml").write_text(project_doc(owner="different"))
    path.write_text(orch(entry='config="demo/cmru.toml"') .replace("[orchestration.project.demo]", "[orchestration.project.demo]\n"), encoding="utf-8")
    path.write_text(path.read_text() + '\n[orchestration.project.other]\nconfig="other/cmru.toml"\n', encoding="utf-8")
    with pytest.raises(SystemExit):
        config.load_forge_config(path)


def test_transaction_build_retention_rejects_empty_artifacts_and_cleanup_manifest(tmp_path):
    root = repo(tmp_path)
    workspace = transaction.create_workspace(root, base=git(root, "rev-parse", "HEAD"), purpose="build")
    child = workspace.path / "demo"; (child / "logs").mkdir(parents=True); (child / "dist").mkdir()
    project = SimpleNamespace(project_root=root / "demo", artifact_dirs=["dist"])
    with pytest.raises(RuntimeError, match="empty"):
        transaction.retain_successful_build_outputs(root, workspace, {"demo": project}, ["demo"])
    transaction.remove_workspace(workspace)

    output_id = "20260816T000000Z_" + git(root, "rev-parse", "HEAD")
    artifact = root / "demo" / "artifacts" / output_id; logs = root / "demo" / "logs" / output_id
    artifact.mkdir(parents=True); logs.mkdir(parents=True)
    (artifact / "build.json").write_text("not-json", encoding="utf-8")
    with pytest.raises(RuntimeError, match="invalid retained build manifest"):
        transaction.delete_retained_build_output(root, project, "demo", output_id, dry_run=False)


def test_transaction_retained_release_errors_are_explicit_and_listing_skips_malformed_blocks(tmp_path):
    root = repo(tmp_path)
    workspace = transaction.ReleaseWorkspace(root, root, "cmru/release/x", "a" * 40)
    with pytest.raises(RuntimeError, match="unknown project"):
        transaction.retain_success_outputs(root, workspace, {}, {"missing": "tag"}, retain_logs=False, retain_artifacts=False)
    project = SimpleNamespace(project_root=None, artifact_dirs=[])
    with pytest.raises(RuntimeError, match="project_root"):
        transaction.retain_success_outputs(root, workspace, {"demo": project}, {"demo": "tag"}, retain_logs=False, retain_artifacts=True)
    raw = "worktree /tmp/not-a-worktree\nHEAD " + "a" * 40 + "\nbranch refs/heads/cmru/release/x\n\nworktree /tmp/no-branch\nHEAD " + "b" * 40 + "\n"
    with patch.object(transaction, "_git", return_value=raw):
        listed = transaction.list_cmru_workspaces(root)
    assert len(listed) == 1 and listed[0].base == ""


def test_version_change_detection_and_file_release_strategy_use_observable_paths(tmp_path):
    project = SimpleNamespace(prefix="demo-v", cwd="demo", paths=["demo"], version=SimpleNamespace(bump="patch"))
    with patch.object(version, "_git_log", return_value=[]), patch.object(version, "_latest_tag_for_prefix", return_value="demo-v1.0.0"):
        assert version.detect_changed_projects(tmp_path, {"demo": project}) == []
    assert version._git_has_changes(tmp_path, "HEAD", "demo") is False

    root = repo(tmp_path)
    file_project = SimpleNamespace(prefix="demo-v", cwd="demo", version=SimpleNamespace(strategy="file:VERSION"), git_tag=True)
    with patch.object(version, "detect_changed_projects", return_value=[("demo", file_project, "demo-v1.0.0", "patch")]), patch.object(version, "_apply_strategy_file", return_value="demo-v1.1.0") as apply:
        assert version.release_cmd(root, {"demo": file_project}, project_filter="demo", dry_run=True) == ["demo-v1.1.0"]
    apply.assert_called_once_with(root, "demo-v", "1.0.1", "VERSION", root / "demo", dry_run=True)


def test_version_release_first_release_bump_path_calls_scm_with_zero_one_zero(tmp_path):
    root = repo(tmp_path)
    project = SimpleNamespace(prefix="demo-v", cwd="demo", version=SimpleNamespace(strategy="scm"), git_tag=True)
    with patch.object(version, "detect_changed_projects", return_value=[("demo", project, None, "patch")]), patch.object(version, "_apply_strategy_scm", return_value="demo-v0.1.0") as apply:
        assert version.release_cmd(root, {"demo": project}, major=True, dry_run=True) == ["demo-v0.1.0"]
    apply.assert_called_once_with(root, "demo-v", "0.1.0", dry_run=True)
