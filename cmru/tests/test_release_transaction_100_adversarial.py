"""Final transaction release boundary witnesses."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from cmru import release, transaction, version


def git(root: Path, *args: str) -> str:
    r = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.email", "test@example.invalid")
    git(root, "config", "user.name", "test")
    (root / "x").write_text("x")
    git(root, "add", ".")
    git(root, "commit", "-q", "-m", "initial")
    return root


def test_transaction_promotion_non_rejection_fails_without_rebase(tmp_path):
    root = repo(tmp_path)
    workspace = transaction.ReleaseWorkspace(root, root, "cmru/release/x", git(root, "rev-parse", "HEAD"))
    result = SimpleNamespace(returncode=1, stderr="protected branch", stdout="")
    original = transaction.subprocess.run
    def fail_push(argv, **kwargs):
        if len(argv) > 1 and argv[1] == "push":
            return result
        return original(argv, **kwargs)
    with patch.object(transaction.subprocess, "run", side_effect=fail_push):
        with pytest.raises(RuntimeError, match="push"):
            transaction.promote_workspace(workspace)


def test_transaction_backup_branch_uses_force_and_cleanup_is_best_effort(tmp_path):
    root = repo(tmp_path)
    workspace = transaction.ReleaseWorkspace(root, root, "cmru/release/x", git(root, "rev-parse", "HEAD"))
    calls = []
    real_run = transaction.subprocess.run

    def fake(argv, **kw):
        if argv[:2] == ["git", "push"]:
            calls.append(argv)
            return SimpleNamespace(returncode=0)
        return real_run(argv, **kw)  # the internal git-common-dir lookups mark/backup_was_pushed make

    with patch.object(transaction.subprocess, "run", side_effect=fake):
        transaction.push_backup_branch(workspace)
        assert transaction.backup_was_pushed(root, workspace) is True  # KI-15: recorded as pushed
        transaction.remove_backup_branch(workspace)
    assert ["--force", "origin", "HEAD:refs/heads/cmru/release/x"][-3:] == calls[0][-3:]
    assert "--delete" in calls[1]


def test_transaction_retain_release_logs_only_and_uses_immutable_destination(tmp_path):
    root = repo(tmp_path)
    child = tmp_path / "child"
    git(root, "worktree", "add", "-q", "-b", "cmru/release/log", str(child), "main")
    (child / "demo").mkdir(exist_ok=True)
    (child / "demo" / "logs").mkdir()
    (child / "demo" / "logs" / "gate.log").write_text("pass")
    project = SimpleNamespace(name="demo", project_root=root / "demo", artifact_dirs=())
    workspace = transaction.ReleaseWorkspace(root, child, "cmru/release/log", git(child, "rev-parse", "HEAD"))
    retained = transaction.retain_success_outputs(root, workspace, {"demo": project}, {"demo": "demo-v1"}, retain_logs=True, retain_artifacts=False)
    assert retained and (root / "demo" / "logs" / "cmru-release" / "demo-v1" / "gate.log").exists()
    git(root, "worktree", "remove", "--force", str(child)); git(root, "branch", "-D", "cmru/release/log")


def test_version_release_file_dry_run_does_not_write(tmp_path):
    target = tmp_path / "VERSION"
    result = version._apply_strategy_file(tmp_path, "demo-v", "1.2.3", "VERSION", tmp_path, dry_run=True)
    assert result == "demo-v1.2.3" and not target.exists()


def test_version_detect_changed_first_release_and_clean_tagged_project(tmp_path):
    root = repo(tmp_path)
    first = SimpleNamespace(name="new", cwd="new", paths=["new"], prefix="new-v", version=SimpleNamespace(bump="conventional"))
    (root / "new").mkdir(); (root / "new" / "x").write_text("x")
    assert version.detect_changed_projects(root, {"new": first})[0][0] == "new"
    tagged = SimpleNamespace(name="demo", cwd=".", paths=["."], prefix="demo-v", version=SimpleNamespace(bump="conventional"))
    git(root, "tag", "demo-v1.0.0")
    assert version.detect_changed_projects(root, {"demo": tagged}) == []


def test_release_latest_validation_refuses_missing_sha_sidecar():
    fake = SimpleNamespace(resolve_latest=lambda _: {"version": "1", "tag": "demo-v1", "assets": [{"name": "demo.whl", "url": "u"}]})
    with pytest.raises(SystemExit):
        release.validate_latest_release(fake, "demo", retries=1, delay=0)
