"""Deep lifecycle and source-fact witnesses for transaction/version boundaries."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from cmru import transaction, version


def _git(repo: Path, *args: str, check: bool = True) -> str:
    result = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True)
    if check and result.returncode:
        raise AssertionError(result.stderr)
    return result.stdout.strip()


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "test@example.invalid")
    _git(root, "config", "user.name", "test")
    (root / "demo").mkdir()
    (root / "demo" / "x.py").write_text("x=1\n")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "feat: initial")
    return root


def _project(root: Path, name: str = "demo") -> SimpleNamespace:
    return SimpleNamespace(name=name, project_root=root / name, artifact_dirs=("dist",))


def test_release_lock_rejects_nested_transaction(tmp_path):
    root = _repo(tmp_path)
    with transaction.release_lock(root):
        with pytest.raises(RuntimeError, match="already running"):
            with transaction.release_lock(root):
                pass


def test_create_resume_and_remove_workspace_are_real_git_lifecycle(tmp_path):
    from worktree import create_workspace, remove_workspace

    root = _repo(tmp_path)
    base = _git(root, "rev-parse", "HEAD")
    workspace = transaction.create_workspace(root, base=base, purpose="build")
    assert workspace.branch.startswith("cmru-build-")
    with pytest.raises(ValueError, match="unknown CMRU workspace purpose"):
        transaction.create_workspace(root, base=base, purpose="other")

    legacy_context = create_workspace(
        root,
        root / ".worktrees" / "cmru-build-legacy-purpose",
        branch="cmru-build-legacy-purpose",
        base=base,
        purpose="cmru-legacy",
    )
    with pytest.raises(RuntimeError, match="not a retained cmru release branch"):
        transaction.resume_workspace(root, legacy_context.worktree_path)
    remove_workspace(legacy_context)

    transaction.remove_workspace(workspace)
    assert not workspace.path.exists()


def test_list_cmru_workspaces_accepts_legacy_purpose_with_transaction_branch(tmp_path):
    from worktree import create_workspace, remove_workspace

    root = _repo(tmp_path)
    context = create_workspace(
        root,
        root / ".worktrees" / "cmru-release-legacy-purpose",
        branch="cmru-release-legacy-purpose",
        base="HEAD",
        purpose="cmru-legacy",
    )

    listed = transaction.list_cmru_workspaces(root)

    assert len(listed) == 1
    assert listed[0].branch == context.branch
    assert listed[0].base == context.base_commit
    assert listed[0].context == context
    remove_workspace(context)


def test_create_workspace_captures_reset_diagnostics_and_cleans_failed_allocation(
    monkeypatch, tmp_path
):
    """A failed materialization must preserve either stderr or stdout.

    The reset is the first command in the newly allocated worktree. Its
    captured diagnostic is the only actionable explanation an operator gets;
    the allocation must also be removed before the error escapes.
    """
    class FakeShared:
        WorkspaceError = RuntimeError

        def __init__(self):
            self.removed = []

        def workspace_id_for_path(self, path):
            return "abcdef"

        def create_workspace(self, *args, **kwargs):
            return object()

        def remove_workspace(self, context, *, force=False):
            self.removed.append((context, force))

    shared = FakeShared()

    def fake_run(argv, **kwargs):
        if kwargs.get("capture_output"):
            return subprocess.CompletedProcess(argv, 1, stdout="reset fallback", stderr="")
        return subprocess.CompletedProcess(argv, 1, stdout=None, stderr=None)

    monkeypatch.setattr(transaction, "_shared_worktree", lambda: shared)
    monkeypatch.setattr(transaction.subprocess, "run", fake_run)

    with pytest.raises(RuntimeError, match="reset fallback"):
        transaction.create_workspace(tmp_path, base="a" * 40, purpose="build")
    assert shared.removed and shared.removed[0][1] is True


def test_resume_workspace_refuses_fetch_failure_before_reopening(tmp_path):
    """A failed refresh cannot be mistaken for a successfully resumed tree."""
    root = _repo(tmp_path)
    base = _git(root, "rev-parse", "HEAD")
    retained = tmp_path / "legacy"
    _git(root, "worktree", "add", "-q", "-b", "cmru/release/legacy", str(retained), base)
    try:
        with pytest.raises(subprocess.CalledProcessError):
            transaction.resume_workspace(root, retained)
    finally:
        _git(root, "worktree", "remove", "--force", str(retained))
        _git(root, "branch", "-D", "cmru/release/legacy")


def test_remove_legacy_workspace_forces_cleanup(monkeypatch, tmp_path):
    calls = []

    class FakeShared:
        def remove_unrecorded_workspace(self, *args, **kwargs):
            calls.append((args, kwargs))

    monkeypatch.setattr(transaction, "_shared_worktree", lambda: FakeShared())
    workspace = transaction.ReleaseWorkspace(
        tmp_path, tmp_path / "child", "cmru/build/legacy", "a" * 40,
    )
    transaction.remove_workspace(workspace)
    assert calls and calls[0][1]["force"] is True


def test_scope_and_result_records_fail_closed_on_corrupt_json(tmp_path):
    root = _repo(tmp_path)
    workspace = transaction.ReleaseWorkspace(root, root, "cmru/release/token", "a" * 40)
    transaction.write_release_scope(root, workspace, ["z", "a"])
    assert transaction.read_release_scope(root, workspace) == ["a", "z"]
    path = transaction._scope_dir(root) / "token.json"
    path.write_text("not-json")
    assert transaction.read_release_scope(root, workspace) is None
    transaction.write_release_result(root, workspace, "demo", "demo-v1")
    assert transaction.read_release_results(root, workspace) == {"demo": "demo-v1"}
    results = transaction._scope_dir(root) / "token.results.json"
    results.write_text("[]")
    with pytest.raises(RuntimeError, match="invalid release result"):
        transaction.read_release_results(root, workspace)


def test_plan_refused_marker_round_trips_and_is_forgotten_with_the_rest_of_scope(tmp_path):
    root = _repo(tmp_path)
    workspace = transaction.ReleaseWorkspace(root, root, "cmru/release/token", "a" * 40)
    assert transaction.plan_was_refused(root, workspace) is False  # must-succeed control: unmarked
    transaction.mark_plan_refused(root, workspace)
    assert transaction.plan_was_refused(root, workspace) is True
    transaction.forget_release_scope(root, workspace)
    assert transaction.plan_was_refused(root, workspace) is False  # cleaned up with the rest


def test_build_output_id_is_source_commit_and_date_derived(tmp_path):
    root = _repo(tmp_path)
    workspace = transaction.ReleaseWorkspace(root, root, "cmru/build/token", "base")
    output_id, commit, source_date = transaction.build_output_id(workspace)
    assert commit == _git(root, "rev-parse", "HEAD")
    assert output_id.endswith("_" + commit)
    assert source_date.endswith("Z")


def test_retain_successful_build_outputs_writes_hash_manifest_and_refuses_collision(tmp_path):
    root = _repo(tmp_path)
    workspace_path = tmp_path / "child"
    _git(root, "worktree", "add", "-q", "-b", "cmru/build/retain", str(workspace_path), "main")
    (workspace_path / "demo" / "logs").mkdir()
    (workspace_path / "demo" / "logs" / "gate.log").write_text("PASS\n")
    (workspace_path / "demo" / "dist").mkdir()
    (workspace_path / "demo" / "dist" / "artifact.whl").write_bytes(b"wheel")
    workspace = transaction.ReleaseWorkspace(root, workspace_path, "cmru/build/retain",
                                             _git(root, "rev-parse", "HEAD"))
    project = _project(root)
    retained = transaction.retain_successful_build_outputs(root, workspace, {"demo": project}, ["demo"])
    assert len(retained) == 2
    manifests = list((root / "demo" / "artifacts").rglob("build.json"))
    assert manifests
    payload = json.loads(manifests[0].read_text())
    assert payload["publication"] == "forbidden"
    with pytest.raises(RuntimeError, match="already exists"):
        transaction.retain_successful_build_outputs(root, workspace, {"demo": project}, ["demo"])
    _git(root, "worktree", "remove", "--force", str(workspace_path))
    _git(root, "branch", "-D", "cmru/build/retain")


def test_delete_retained_build_output_requires_exact_authenticated_record(tmp_path):
    root = _repo(tmp_path)
    project = _project(root)
    with pytest.raises(RuntimeError, match="exact"):
        transaction.delete_retained_build_output(root, project, "demo", "latest", dry_run=True)
    output_id = "20260101T000000Z_" + _git(root, "rev-parse", "HEAD")
    logs = root / "demo" / "logs" / output_id
    artifacts = root / "demo" / "artifacts" / output_id
    logs.mkdir(parents=True)
    artifacts.mkdir(parents=True)
    (artifacts / "build.json").write_text(json.dumps({"schema_version": 1, "kind": "wrong"}))
    with pytest.raises(RuntimeError, match="does not authorize"):
        transaction.delete_retained_build_output(root, project, "demo", output_id, dry_run=True)


def test_progress_marker_round_trip_and_forget(tmp_path):
    root = _repo(tmp_path)
    workspace = transaction.ReleaseWorkspace(root, root, "cmru/release/progress", "a" * 40)
    transaction.write_release_progress(root, workspace, "b" * 40)
    assert transaction.read_release_progress(root, workspace) == "b" * 40
    transaction.forget_release_scope(root, workspace)
    assert transaction.read_release_progress(root, workspace) is None


def test_version_change_detection_is_project_scoped_and_excludes_control_files(tmp_path):
    root = _repo(tmp_path)
    _git(root, "tag", "demo-v1.0.0")
    (root / "demo" / "x.py").write_text("x=2\n")
    _git(root, "add", "demo/x.py")
    _git(root, "commit", "-q", "-m", "fix: update")
    (root / "demo" / "cmru.toml").write_text("changed\n")
    _git(root, "add", "demo/cmru.toml")
    _git(root, "commit", "-q", "-m", "chore: config only")
    project = SimpleNamespace(name="demo", cwd="demo", paths=["demo"], prefix="demo-v",
                              version=SimpleNamespace(bump="conventional"))
    changed = version.detect_changed_projects(root, {"demo": project})
    assert changed and changed[0][3] == "patch"


def test_version_file_strategy_commits_version_before_tag(tmp_path):
    root = _repo(tmp_path)
    tag = version._apply_strategy_file(root, "demo-v", "2.0.0", "VERSION", root / "demo")
    assert tag == "demo-v2.0.0"
    assert (root / "demo" / "VERSION").read_text() == "2.0.0\n"
    assert _git(root, "tag", "--list") == "demo-v2.0.0"


def test_release_cmd_dry_run_and_dirty_tree_are_distinct(tmp_path, capsys):
    root = _repo(tmp_path)
    project = SimpleNamespace(name="demo", cwd="demo", paths=["demo"], prefix="demo-v",
                              git_tag=True, version=SimpleNamespace(strategy="scm", bump="conventional"))
    (root / "demo" / "x.py").write_text("x=2\n")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "fix: update")
    assert version.release_cmd(root, {"demo": project}, dry_run=True) == ["demo-v0.1.0"]
    assert _git(root, "tag", "--list") == ""
    (root / "scratch").write_text("dirty")
    with pytest.raises(SystemExit):
        version.release_cmd(root, {"demo": project})
