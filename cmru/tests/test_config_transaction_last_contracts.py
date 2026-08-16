"""Last high-signal configuration and transaction refusal contracts."""
from __future__ import annotations

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
    root = tmp_path / "repo"; root.mkdir()
    git(root, "init", "-q", "-b", "main"); git(root, "config", "user.email", "test@example.invalid"); git(root, "config", "user.name", "test")
    (root / "demo").mkdir(); (root / "demo" / "source.py").write_text("x=1\n")
    git(root, "add", "."); git(root, "commit", "-q", "-m", "initial")
    return root


def project_doc(name="demo", targets='registry=["ghcr.io"]'):
    return f'''schema_version=1
[github]
owner="acme"
repo="vbpub"
owner_type="org"
[targets]
host="github"
{targets}
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


def orch(entry='config="demo/cmru.toml"', order='["demo"]', projects='demo'):
    return f'''schema_version=1
[orchestration]
project_order={order}
default_projects=["demo"]
default_steps=["build"]
execution_mode="project-first"
[orchestration.project.{projects}]
{entry}
[cleanup]
release_tag_prefixes=[]
keep_release_tags=[]
ghcr_packages=[]
ghcr_delete_packages=[]
'''


def test_config_secret_and_project_missing_facts_fail_closed(tmp_path, capsys):
    secret = tmp_path / "cmru.secret.toml"; secret.write_text("", encoding="utf-8")
    with pytest.raises(SystemExit):
        config._read_secret_document(secret)
    path = tmp_path / "cmru.toml"
    cases = (
        (project_doc() + "build_metadata={unsupported=\"x\"}\n", "build_metadata"),
        (project_doc() + "project_metadata=[]\n", "project_metadata"),
        (project_doc().replace('id="demo"', 'id="Bad_Name"'), "project.id"),
        (project_doc().replace('[project.release]\n', '[project.release]\nchangelog="../bad"\n'), "changelog"),
    )
    for raw, diagnostic in cases:
        path.write_text(raw, encoding="utf-8")
        with pytest.raises(SystemExit):
            config.load_forge_config(path)
        assert diagnostic in capsys.readouterr().out


def test_config_orchestration_rejects_entry_shape_order_and_cross_project_targets(tmp_path, capsys):
    demo = tmp_path / "demo"; demo.mkdir(); (demo / "cmru.toml").write_text(project_doc())
    other = tmp_path / "other"; other.mkdir(); (other / "cmru.toml").write_text(project_doc("other", targets='registry=["other.example"]'))
    path = tmp_path / "cmru.orchestration.toml"
    cases = (
        (orch().replace('[orchestration.project.demo]\n', '[orchestration.project.demo]\nvalue=1\n'), "unknown keys"),
        (orch(entry='config="../demo/cmru.toml"'), "project-relative"),
        (orch(entry='config="demo/wrong.toml"'), "cmru.toml"),
        (orch(order='["missing"]'), "unknown project"),
    )
    for raw, diagnostic in cases:
        path.write_text(raw, encoding="utf-8")
        with pytest.raises(SystemExit):
            config.load_forge_config(path)
        assert diagnostic in capsys.readouterr().out
    path.write_text(orch() + '\n[orchestration.project.other]\nconfig="other/cmru.toml"\n', encoding="utf-8")
    with pytest.raises(SystemExit):
        config.load_forge_config(path)
    assert "identical [targets]" in capsys.readouterr().out


def test_transaction_overlay_and_record_write_reject_nonregular_inputs(tmp_path):
    root = repo(tmp_path); workspace = transaction.ReleaseWorkspace(root, root, "cmru/release/x", "a" * 40)
    cfg = root / "demo" / "cmru.toml"; cfg.with_name("cmru.secret.toml").mkdir()
    with pytest.raises(RuntimeError, match="regular file"):
        transaction.copy_secret_overlays(root, workspace, [cfg])
    scope = transaction._scope_dir(root); scope.mkdir(); (scope / "x.results.json").write_text("[]")
    with pytest.raises(RuntimeError, match="invalid release result"):
        transaction.write_release_result(root, workspace, "demo", "tag")


def test_transaction_retention_empty_and_collision_outputs_are_refused(tmp_path):
    root = repo(tmp_path); workspace = transaction.create_workspace(root, base=git(root, "rev-parse", "HEAD"), purpose="build")
    child = workspace.path / "demo"; (child / "logs").mkdir(parents=True); (child / "dist").mkdir()
    project = SimpleNamespace(project_root=root / "demo", artifact_dirs=["dist"])
    with pytest.raises(RuntimeError, match="empty"):
        transaction.retain_successful_build_outputs(root, workspace, {"demo": project}, ["demo"])
    (child / "dist" / "demo.whl").write_bytes(b"wheel")
    output_id, _, _ = transaction.build_output_id(workspace)
    (root / "demo" / "artifacts" / output_id).mkdir(parents=True)
    with pytest.raises(RuntimeError, match="already exists"):
        transaction.retain_successful_build_outputs(root, workspace, {"demo": project}, ["demo"])
    transaction.remove_workspace(workspace)


def test_transaction_discard_build_rejects_release_branch_and_missing_artifacts(tmp_path):
    root = repo(tmp_path); workspace = transaction.create_workspace(root, base=git(root, "rev-parse", "HEAD"), purpose="release")
    with pytest.raises(RuntimeError, match="retained cmru build"):
        transaction.discard_build_workspace(root, workspace.path, dry_run=True)
    transaction.remove_workspace(workspace)
    ws = transaction.ReleaseWorkspace(root, root, "cmru/release/x", "a" * 40)
    project = SimpleNamespace(project_root=root / "demo", artifact_dirs=[])
    with pytest.raises(RuntimeError, match="artifact_dirs"):
        transaction.retain_success_outputs(root, ws, {"demo": project}, {"demo": "tag"}, retain_logs=False, retain_artifacts=True)
