"""Focused contracts for commit-bound release-gate evidence retention."""
from __future__ import annotations

import hashlib
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


def project_document() -> str:
    return '''schema_version = 1
[github]
owner = "acme"
repo = "vbpub"
owner_type = "org"
[targets]
host = "github"
registry = []
[project]
id = "demo"
description = "demo"
prefix = "demo-v"
artifacts = ["wheel"]
[project.version]
strategy = "scm"
bump = "patch"
[project.release]
git_tag = true
build_step = "build"
artifact_dirs = ["dist"]
evidence_paths = ["coverage.json", ".assay"]
[steps.run-tests]
quiet = true
commands = [{label = "tests", argv = ["true"], cwd = "."}]
[steps.build]
quiet = true
commands = [{label = "build", argv = ["true"], cwd = "."}]
[steps.push]
quiet = true
commands = [{label = "push", argv = ["true"], cwd = "."}]
'''


def test_config_loads_evidence_paths_against_project_root_and_rejects_unsafe_paths(tmp_path, capsys):
    project_root = tmp_path / "projects" / "demo"
    project_root.mkdir(parents=True)
    # A path with this name at the orchestration root must not affect the
    # project-local declaration in projects/demo.
    (tmp_path / "outside").mkdir()
    (tmp_path / "coverage.json").symlink_to(tmp_path / "outside" / "coverage.json")
    path = project_root / "cmru.toml"
    path.write_text(project_document(), encoding="utf-8")
    loaded = config.load_forge_config(path)
    assert loaded.projects["demo"].evidence_paths == ["coverage.json", ".assay"]

    path.write_text(project_document().replace('"coverage.json", ".assay"', '"../coverage.json"'), encoding="utf-8")
    with pytest.raises(SystemExit):
        config.load_forge_config(path)
    assert "evidence_paths" in capsys.readouterr().err

    linked = project_root / "linked"
    linked.mkdir()
    (linked / "report.json").symlink_to(tmp_path / "outside" / "report.json")
    path.write_text(
        project_document().replace('"coverage.json", ".assay"', '"linked/report.json"'),
        encoding="utf-8",
    )
    with pytest.raises(SystemExit):
        config.load_forge_config(path)
    assert "symlink" in capsys.readouterr().err

    path.write_text(
        project_document().replace(
            '"coverage.json", ".assay"', '"coverage.json", "coverage.json/nested"',
        ),
        encoding="utf-8",
    )
    with pytest.raises(SystemExit):
        config.load_forge_config(path)
    assert "overlapping" in capsys.readouterr().err


def test_release_retains_files_and_directories_with_commit_hash_manifest(tmp_path):
    root = repo(tmp_path)
    workspace = transaction.create_workspace(root, base=git(root, "rev-parse", "HEAD"), purpose="release")
    child = workspace.path / "demo"
    (child / "coverage.json").write_text('{"percent": 100}\n', encoding="utf-8")
    (child / ".assay").mkdir()
    (child / ".assay" / "verdict-demo.json").write_text('{"status":"passed"}\n', encoding="utf-8")
    project = SimpleNamespace(
        project_root=root / "demo",
        artifact_dirs=(),
        evidence_paths=("coverage.json", ".assay"),
    )

    retained = transaction.retain_success_outputs(
        root,
        workspace,
        {"demo": project},
        {"demo": "demo-v1"},
        retain_logs=False,
        retain_artifacts=False,
    )

    evidence_root = root / "demo" / "evidence" / "cmru-release" / "demo-v1"
    assert retained == [evidence_root]
    assert (evidence_root / "coverage.json").read_text(encoding="utf-8") == '{"percent": 100}\n'
    assert (evidence_root / ".assay" / "verdict-demo.json").is_file()
    manifest = json.loads((evidence_root / "evidence.json").read_text(encoding="utf-8"))
    assert manifest["kind"] == "cmru-release-evidence"
    assert manifest["source_commit"] == git(root, "rev-parse", "HEAD")
    files = {
        entry["path"]: entry
        for item in manifest["paths"]
        for entry in item["files"]
    }
    assert files["coverage.json"]["sha256"] == hashlib.sha256(
        b'{"percent": 100}\n'
    ).hexdigest()
    assert files[".assay/verdict-demo.json"]["bytes"] == str(len(b'{"status":"passed"}\n'))
    assert not (child / "coverage.json").exists()
    assert not (child / ".assay").exists()
    transaction.remove_workspace(workspace)


def test_missing_or_symlinked_evidence_preflight_preserves_logs_and_sources(tmp_path):
    root = repo(tmp_path)
    workspace = transaction.ReleaseWorkspace(root, tmp_path / "release", "cmru/release/x", "a" * 40)
    child = workspace.path / "demo"
    (child / "logs").mkdir(parents=True)
    (child / "logs" / "step.log").write_text("passed\n", encoding="utf-8")
    (child / "outside.json").write_text("outside\n", encoding="utf-8")
    (child / "coverage.json").symlink_to(child / "outside.json")
    project = SimpleNamespace(
        project_root=root / "demo",
        artifact_dirs=(),
        evidence_paths=("coverage.json",),
    )

    with pytest.raises(RuntimeError, match="symlink"):
        transaction.retain_success_outputs(
            root, workspace, {"demo": project}, {"demo": "demo-v1"},
            retain_logs=True, retain_artifacts=False,
        )

    assert (child / "logs" / "step.log").is_file()
    assert (child / "coverage.json").is_symlink()
    assert not (root / "demo" / "evidence").exists()


def test_missing_evidence_is_refused_before_retention_moves_anything(tmp_path):
    root = repo(tmp_path)
    workspace = transaction.ReleaseWorkspace(root, tmp_path / "release", "cmru/release/x", "a" * 40)
    child = workspace.path / "demo"
    (child / "logs").mkdir(parents=True)
    (child / "logs" / "step.log").write_text("passed\n", encoding="utf-8")
    project = SimpleNamespace(
        project_root=root / "demo",
        artifact_dirs=(),
        evidence_paths=("coverage.json",),
    )

    with pytest.raises(RuntimeError, match="declared evidence path is missing"):
        transaction.retain_success_outputs(
            root, workspace, {"demo": project}, {"demo": "demo-v1"},
            retain_logs=True, retain_artifacts=False,
        )

    assert (child / "logs" / "step.log").is_file()
    assert not (root / "demo" / "evidence").exists()


def test_overlapping_evidence_paths_are_refused_before_moves(tmp_path):
    root = repo(tmp_path)
    workspace = transaction.ReleaseWorkspace(root, tmp_path / "release", "cmru/release/x", "a" * 40)
    child = workspace.path / "demo"
    (child / "coverage.json").mkdir(parents=True)
    (child / "coverage.json" / "nested.json").write_text("coverage\n", encoding="utf-8")
    project = SimpleNamespace(
        project_root=root / "demo",
        artifact_dirs=(),
        evidence_paths=("coverage.json", "coverage.json/nested.json"),
    )

    with pytest.raises(RuntimeError, match="collision/overlap"):
        transaction.retain_success_outputs(
            root, workspace, {"demo": project}, {"demo": "demo-v1"},
            retain_logs=False, retain_artifacts=False,
        )

    assert (child / "coverage.json" / "nested.json").is_file()
    assert not (root / "demo" / "evidence").exists()


def test_evidence_destination_collision_is_refused_without_moving_sources(tmp_path):
    root = repo(tmp_path)
    workspace = transaction.ReleaseWorkspace(root, tmp_path / "release", "cmru/release/x", "a" * 40)
    child = workspace.path / "demo"
    child.mkdir(parents=True)
    (child / "coverage.json").write_text("coverage\n", encoding="utf-8")
    destination = root / "demo" / "evidence" / "cmru-release" / "demo-v1"
    destination.mkdir(parents=True)
    project = SimpleNamespace(
        project_root=root / "demo",
        artifact_dirs=(),
        evidence_paths=("coverage.json",),
    )

    with pytest.raises(RuntimeError, match="retained evidence destination already exists"):
        transaction.retain_success_outputs(
            root, workspace, {"demo": project}, {"demo": "demo-v1"},
            retain_logs=False, retain_artifacts=False,
        )
    assert (child / "coverage.json").is_file()


def test_evidence_move_failure_rolls_back_sources_and_destination(tmp_path):
    root = repo(tmp_path)
    workspace = transaction.ReleaseWorkspace(root, tmp_path / "release", "cmru/release/x", "a" * 40)
    child = workspace.path / "demo"
    child.mkdir(parents=True)
    (child / "one.json").write_text("one\n", encoding="utf-8")
    (child / "two.json").write_text("two\n", encoding="utf-8")
    project = SimpleNamespace(
        project_root=root / "demo",
        artifact_dirs=(),
        evidence_paths=("one.json", "two.json"),
    )
    real_move = transaction.shutil.move
    calls = 0

    def fail_second_evidence(source, target):
        nonlocal calls
        if Path(source).parent == child:
            calls += 1
            if calls == 2:
                raise OSError("simulated evidence move failure")
        return real_move(source, target)

    with patch.object(transaction.shutil, "move", side_effect=fail_second_evidence):
        with pytest.raises(OSError, match="simulated evidence move failure"):
            transaction.retain_success_outputs(
                root, workspace, {"demo": project}, {"demo": "demo-v1"},
                retain_logs=False, retain_artifacts=False,
            )

    assert (child / "one.json").is_file()
    assert (child / "two.json").is_file()
    assert not (root / "demo" / "evidence").exists()


def test_discard_evidence_opt_out_leaves_generated_paths_in_worktree(tmp_path):
    root = repo(tmp_path)
    workspace = transaction.ReleaseWorkspace(root, tmp_path / "release", "cmru/release/x", "a" * 40)
    child = workspace.path / "demo"
    child.mkdir(parents=True)
    (child / "coverage.json").write_text("coverage\n", encoding="utf-8")
    project = SimpleNamespace(
        project_root=root / "demo",
        artifact_dirs=(),
        evidence_paths=("coverage.json",),
    )

    retained = transaction.retain_success_outputs(
        root, workspace, {"demo": project}, {"demo": "demo-v1"},
        retain_logs=False, retain_artifacts=False, retain_evidence=False,
    )
    assert retained == []
    assert (child / "coverage.json").is_file()
    assert not (root / "demo" / "evidence").exists()
