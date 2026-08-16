"""High-risk residual contracts: secret/schema refusal and atomic retention."""
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
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.email", "test@example.invalid"); git(root, "config", "user.name", "test")
    (root / "demo").mkdir(); (root / "demo" / "source.py").write_text("x=1\n")
    git(root, "add", "."); git(root, "commit", "-q", "-m", "initial")
    return root


def test_secret_document_refuses_scalar_and_missing_github_table(tmp_path):
    path = tmp_path / "cmru.secret.toml"
    with patch.object(config.tomllib, "load", return_value="scalar"):
        path.write_text("ignored", encoding="utf-8")
        with pytest.raises(SystemExit):
            config._read_secret_document(path)
    path.write_text("token = \"wrong-level\"\n", encoding="utf-8")
    with pytest.raises(SystemExit):
        config._read_secret_document(path)


def test_transaction_build_retention_rolls_back_first_rename_on_second_rename_failure(tmp_path):
    root = repo(tmp_path)
    workspace = transaction.create_workspace(root, base=git(root, "rev-parse", "HEAD"), purpose="build")
    child = workspace.path / "demo"
    (child / "logs").mkdir(parents=True); (child / "logs" / "step.log").write_text("log")
    (child / "dist").mkdir(); (child / "dist" / "demo.whl").write_bytes(b"wheel")
    project = SimpleNamespace(project_root=root / "demo", artifact_dirs=["dist"])
    original_replace = Path.replace
    def fail_artifact_rename(self, target):
        if self.name == "artifacts" and Path(target).parent.name == "artifacts":
            raise OSError("simulated artifact rename failure")
        return original_replace(self, target)
    with patch.object(Path, "replace", new=fail_artifact_rename):
        with pytest.raises(OSError, match="artifact rename"):
            transaction.retain_successful_build_outputs(root, workspace, {"demo": project}, ["demo"])
    output_root = root / "demo" / "artifacts"
    assert not list(output_root.glob("*/build.json")) if output_root.exists() else True
    assert (child / "logs").is_dir()
    transaction.remove_workspace(workspace)


def test_transaction_delete_retained_build_rejects_symlink_manifest(tmp_path):
    root = repo(tmp_path)
    output_id = "20260816T000000Z_" + git(root, "rev-parse", "HEAD")
    artifact = root / "demo" / "artifacts" / output_id; logs = root / "demo" / "logs" / output_id
    artifact.mkdir(parents=True); logs.mkdir(parents=True)
    target = tmp_path / "real-manifest"; target.write_text("{}")
    (artifact / "build.json").symlink_to(target)
    project = SimpleNamespace(project_root=root / "demo", artifact_dirs=["dist"])
    with pytest.raises(RuntimeError, match="incomplete or unsafe"):
        transaction.delete_retained_build_output(root, project, "demo", output_id, dry_run=False)


def test_version_external_release_requires_prepare_fact(tmp_path):
    root = repo(tmp_path)
    project = SimpleNamespace(prefix="demo-v", cwd="demo", git_tag=True, version=SimpleNamespace(strategy="external:VERSION"))
    with patch.object(version, "detect_changed_projects", return_value=[("demo", project, None, "patch")]):
        with pytest.raises(RuntimeError, match="prepare step"):
            version.release_cmd(root, {"demo": project})
