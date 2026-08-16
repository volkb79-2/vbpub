"""Complete-fixture contracts for residual high-risk branches."""
from __future__ import annotations

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
    git(root, "init", "-q", "-b", "main"); git(root, "config", "user.email", "test@example.invalid"); git(root, "config", "user.name", "test")
    (root / "demo").mkdir(); (root / "demo" / "source.py").write_text("x=1\n")
    git(root, "add", "."); git(root, "commit", "-q", "-m", "initial")
    return root


def project_doc() -> str:
    return '''schema_version=1
[github]
owner="acme"
repo="vbpub"
owner_type="org"
[targets]
host="github"
registry=["ghcr.io"]
[project]
id="demo"
description="demo"
prefix="demo-v"
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
commands=[{label="tests",argv=["echo"],cwd="."}]
[steps.build]
quiet=true
commands=[{label="build",argv=["echo"],cwd="."}]
[steps.push]
quiet=true
commands=[{label="push",argv=["echo"],cwd="."}]
'''


def test_config_project_required_fields_are_rejected_after_full_parse(tmp_path, capsys):
    path = tmp_path / "cmru.toml"
    for fragment, diagnostic in (
        ("description=[]", "description"),
        ("prefix=\"\"", "prefix"),
        ("template_revision=0", "template_revision"),
        ("scm_dist=\"\"", "scm_dist"),
        ("[project.release]\ngit_tag=\"yes\"\nbuild_step=\"build\"\nartifact_dirs=[\"dist\"]", "git_tag"),
    ):
        raw = project_doc()
        if fragment.startswith("[project.release]"):
            raw = raw[:raw.index("[project.release]")] + fragment + "\n" + raw[raw.index("[steps.run-tests]"):]
        elif fragment.startswith("prefix="):
            raw = raw.replace("prefix=\"demo-v\"", fragment)
        else:
            raw = raw.replace("description=\"demo\"", fragment if fragment.startswith("description") else "description=\"demo\"\n" + fragment)
        path.write_text(raw, encoding="utf-8")
        with pytest.raises(SystemExit):
            config.load_forge_config(path)
        assert diagnostic in capsys.readouterr().out


def test_config_orchestration_valid_fixture_rejects_unordered_dependency(tmp_path, capsys):
    project = tmp_path / "demo"; project.mkdir(); (project / "cmru.toml").write_text(project_doc())
    path = tmp_path / "cmru.orchestration.toml"
    path.write_text('''schema_version=1
[orchestration]
project_order=["demo"]
default_projects=["demo"]
default_steps=["build"]
execution_mode="project-first"
[orchestration.project.demo]
config="demo/cmru.toml"
depends_on=["other"]
[cleanup]
release_tag_prefixes=[]
keep_release_tags=[]
ghcr_packages=[]
ghcr_delete_packages=[]
''')
    with pytest.raises(SystemExit):
        config.load_forge_config(path)
    assert "depends_on" in capsys.readouterr().out


def test_transaction_create_workspace_fetches_when_base_is_not_supplied(tmp_path):
    root = repo(tmp_path)
    with patch.object(transaction, "fetch_origin_main", return_value=git(root, "rev-parse", "HEAD")):
        workspace = transaction.create_workspace(root, purpose="build")
    assert workspace.branch.startswith("cmru/build/")
    transaction.remove_workspace(workspace)


def test_transaction_secret_overlay_and_result_record_fail_closed(tmp_path):
    root = repo(tmp_path); workspace = transaction.ReleaseWorkspace(root, root, "cmru/release/x", "a" * 40)
    config_path = tmp_path / "outside" / "cmru.toml"; config_path.parent.mkdir()
    with pytest.raises(RuntimeError, match="outside repository"):
        transaction.copy_secret_overlays(root, workspace, [config_path])
    scope = transaction._scope_dir(root); scope.mkdir(); (scope / "x.results.json").write_text("[]")
    with pytest.raises(RuntimeError, match="invalid release result"):
        transaction.write_release_result(root, workspace, "demo", "tag")


def test_transaction_retention_rejects_unknown_empty_and_collision_outputs(tmp_path):
    root = repo(tmp_path); workspace = transaction.create_workspace(root, base=git(root, "rev-parse", "HEAD"), purpose="build")
    with pytest.raises(RuntimeError, match="unknown project"):
        transaction.retain_successful_build_outputs(root, workspace, {}, ["missing"])
    project = SimpleNamespace(project_root=root / "demo", artifact_dirs=[])
    (workspace.path / "demo" / "logs").mkdir(parents=True)
    with pytest.raises(RuntimeError, match="artifact_dirs"):
        transaction.retain_successful_build_outputs(root, workspace, {"demo": project}, ["demo"])
    transaction.remove_workspace(workspace)


def test_transaction_delete_and_discard_refuse_unsafe_state(tmp_path):
    root = repo(tmp_path); output_id = "20260816T000000Z_" + git(root, "rev-parse", "HEAD")
    project = SimpleNamespace(project_root=root / "demo", artifact_dirs=["dist"])
    with pytest.raises(RuntimeError, match="incomplete or unsafe"):
        transaction.delete_retained_build_output(root, project, "demo", output_id, dry_run=False)
    with pytest.raises(RuntimeError, match="managed .worktrees"):
        transaction.discard_build_workspace(root, tmp_path / "outside", dry_run=False)


def test_version_counter_success_and_external_prepare_value_are_observable(tmp_path):
    with patch.object(version.subprocess, "run", side_effect=[SimpleNamespace(returncode=0, stdout=""), SimpleNamespace(returncode=0)]) as run:
        assert version._apply_strategy_counter(tmp_path, "demo-v", "1.0.0") == "demo-v1.0.0-r1"
    assert run.call_count == 2

    root = repo(tmp_path); (root / "demo" / "cmru.vars").write_text("VERSION=2.3.4\n")
    git(root, "add", "demo/cmru.vars"); git(root, "commit", "-q", "-m", "prepare version")
    project = SimpleNamespace(prefix="demo-v", cwd="demo", git_tag=True, version=SimpleNamespace(strategy="external:VERSION"))
    with patch.object(version, "detect_changed_projects", return_value=[("demo", project, None, "patch")]), patch.object(version, "_apply_strategy_scm", return_value="demo-v2.3.4") as apply:
        assert version.release_cmd(root, {"demo": project}, dry_run=True) == ["demo-v2.3.4"]
    apply.assert_called_once_with(root, "demo-v", "2.3.4", dry_run=True)
