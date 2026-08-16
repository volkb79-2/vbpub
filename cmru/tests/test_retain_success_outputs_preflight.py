"""Atomic preflight contracts for release-output retention."""
from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from cmru import transaction


def git(root: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"; root.mkdir()
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.email", "test@example.invalid")
    git(root, "config", "user.name", "test")
    (root / "demo").mkdir(); (root / "demo" / "source.py").write_text("x=1\n")
    git(root, "add", "."); git(root, "commit", "-q", "-m", "initial")
    return root


def test_missing_artifact_preflight_does_not_move_logs_or_create_destinations(tmp_path):
    root = repo(tmp_path)
    workspace = transaction.ReleaseWorkspace(root, root, "cmru/release/x", "a" * 40)
    child = workspace.path / "demo"
    (child / "logs").mkdir(parents=True)
    (child / "logs" / "step.log").write_text("log", encoding="utf-8")
    project = SimpleNamespace(project_root=root / "demo", artifact_dirs=["dist"])

    with pytest.raises(RuntimeError, match="declared artifact directory is missing"):
        transaction.retain_success_outputs(
            root, workspace, {"demo": project}, {"demo": "tag"},
            retain_logs=True, retain_artifacts=True,
        )

    assert (child / "logs" / "step.log").is_file()
    assert not (root / "demo" / "logs" / "cmru-release" / "tag").exists()
    assert not (root / "demo" / "artifacts" / "tag").exists()


def test_duplicate_artifact_basenames_preflight_does_not_move_any_source(tmp_path):
    root = repo(tmp_path)
    workspace = transaction.ReleaseWorkspace(root, root, "cmru/release/x", "a" * 40)
    child = workspace.path / "demo"
    (child / "logs").mkdir(parents=True)
    (child / "logs" / "step.log").write_text("log", encoding="utf-8")
    for directory, filename in (("one/dist", "one.whl"), ("two/dist", "two.whl")):
        artifact = child / directory
        artifact.mkdir(parents=True)
        (artifact / filename).write_text("artifact", encoding="utf-8")
    project = SimpleNamespace(project_root=root / "demo", artifact_dirs=["one/dist", "two/dist"])

    with pytest.raises(RuntimeError, match="artifact directory name collision: dist"):
        transaction.retain_success_outputs(
            root, workspace, {"demo": project}, {"demo": "tag"},
            retain_logs=True, retain_artifacts=True,
        )

    assert (child / "logs" / "step.log").is_file()
    assert (child / "one" / "dist" / "one.whl").is_file()
    assert (child / "two" / "dist" / "two.whl").is_file()
    assert not (root / "demo" / "logs" / "cmru-release" / "tag").exists()
    assert not (root / "demo" / "artifacts" / "tag").exists()
