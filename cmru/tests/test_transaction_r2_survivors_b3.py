"""Caller-visible transaction witnesses for R2 survivor batch 3."""
from __future__ import annotations

import errno
import json
import os
import shutil
import subprocess
from types import SimpleNamespace

import pytest

from cmru import transaction
from tests._git_isolation import git_isolation  # noqa: F401

pytestmark = pytest.mark.usefixtures("git_isolation")


def _init_repo(root: Path) -> None:
    root.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(root)], check=True)
    (root / "seed.txt").write_text("seed\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "seed.txt"], check=True)
    subprocess.run(
        ["git", "-C", str(root), "commit", "-q", "-m", "seed"], check=True,
    )


def _release_retention_fixture(tmp_path: Path):
    repo_root = tmp_path / "repo"
    candidate = tmp_path / "candidate"
    _init_repo(repo_root)
    _init_repo(candidate)
    project_root = repo_root / "demo"
    source_root = candidate / "demo"
    (source_root / "logs").mkdir(parents=True)
    (source_root / "logs" / "build.log").write_text("build\n", encoding="utf-8")
    (source_root / "dist").mkdir()
    (source_root / "dist" / "demo.whl").write_bytes(b"wheel")
    workspace = transaction.ReleaseWorkspace(
        repo_root, candidate, "cmru-release-20261128_120000-demo-ab12cd",
        "a" * 40,
    )
    project = SimpleNamespace(project_root=project_root, artifact_dirs=("dist",))
    return repo_root, workspace, project, "b" * 40


def test_cleanup_rollback_restores_artifacts_after_logs_record_was_removed(
    tmp_path, monkeypatch,
):
    project_root = tmp_path / "demo"
    output_id = "20260101T000000Z_" + "a" * 40
    artifact_root = project_root / "artifacts" / output_id
    logs_root = project_root / "logs" / output_id
    (artifact_root / "dist").mkdir(parents=True)
    logs_root.mkdir(parents=True)
    (artifact_root / "dist" / "demo.whl").write_bytes(b"wheel")
    (artifact_root / "build.json").write_text(json.dumps({
        "schema_version": 1,
        "kind": "cmru-local-build",
        "publication": "forbidden",
        "project": "demo",
        "build_id": output_id,
    }), encoding="utf-8")
    (logs_root / "build.log").write_text("complete\n", encoding="utf-8")
    project = SimpleNamespace(project_root=project_root)

    real_rmtree = shutil.rmtree
    calls = 0

    def remove_logs_then_fail_artifacts(path, *args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("artifact record cleanup failed")
        return real_rmtree(path, *args, **kwargs)

    remove_logs_then_fail_artifacts.avoids_symlink_attacks = real_rmtree.avoids_symlink_attacks
    monkeypatch.setattr(transaction.shutil, "rmtree", remove_logs_then_fail_artifacts)

    with pytest.raises(OSError, match="artifact record cleanup failed"):
        transaction.delete_retained_build_output(
            tmp_path, project, "demo", output_id, dry_run=False,
        )

    assert calls == 2
    assert not logs_root.exists()
    assert (artifact_root / "dist" / "demo.whl").read_bytes() == b"wheel"
    assert (artifact_root / "build.json").is_file()


def test_cleanup_rollback_moves_a_dangling_staged_record_back_to_its_public_name(
    tmp_path,
):
    parent = tmp_path / "parent"
    stage = parent / "stage"
    stage.mkdir(parents=True)
    output_id = "output-1"
    (stage / "record").symlink_to("missing-target")
    parent_fd = os.open(parent, transaction._directory_open_flags())
    stage_fd = os.open(stage, transaction._directory_open_flags())
    try:
        transaction._restore_cleanup_stage_record(parent_fd, stage_fd, output_id)
    finally:
        os.close(stage_fd)
        os.close(parent_fd)

    assert (parent / output_id).is_symlink()
    assert os.readlink(parent / output_id) == "missing-target"
    assert not (stage / "record").exists()


def test_successful_noreplace_restore_reports_the_move_and_moves_the_record(
    monkeypatch, tmp_path,
):
    parent = tmp_path / "parent"
    stage = parent / "stage"
    stage.mkdir(parents=True)
    (stage / "record").write_text("reviewed output\n", encoding="utf-8")
    parent_fd = os.open(parent, transaction._directory_open_flags())
    stage_fd = os.open(stage, transaction._directory_open_flags())

    def renameat2(source_fd, source, destination_fd, destination, _flags):
        os.rename(
            os.fsdecode(source), os.fsdecode(destination),
            src_dir_fd=source_fd, dst_dir_fd=destination_fd,
        )
        return 0

    monkeypatch.setattr(
        transaction.ctypes, "CDLL",
        lambda *_args, **_kwargs: SimpleNamespace(renameat2=renameat2),
    )
    try:
        restored = transaction._restore_cleanup_stage_record(
            parent_fd, stage_fd, "output-2",
        )
    finally:
        os.close(stage_fd)
        os.close(parent_fd)

    assert restored
    assert (parent / "output-2").read_text(encoding="utf-8") == "reviewed output\n"
    assert not (stage / "record").exists()


def test_noreplace_collision_returns_false_and_preserves_both_records(tmp_path):
    """KI-226: exercise Linux renameat2's errno channel, not syscall speed."""
    parent = tmp_path / "parent"
    parent.mkdir()
    (parent / "source").write_text("staged\n", encoding="utf-8")
    (parent / "destination").write_text("concurrent\n", encoding="utf-8")
    parent_fd = os.open(parent, transaction._directory_open_flags())
    try:
        restored = transaction._rename_noreplace_at(
            "source", parent_fd, "destination", parent_fd,
        )
    finally:
        os.close(parent_fd)

    assert not restored
    assert (parent / "source").read_text(encoding="utf-8") == "staged\n"
    assert (parent / "destination").read_text(encoding="utf-8") == "concurrent\n"


def test_release_output_manifest_uses_canonical_sorted_json(tmp_path):
    repo_root, workspace, project, immutable_id = _release_retention_fixture(tmp_path)

    transaction.retain_success_outputs(
        repo_root, workspace, {"demo": project}, {"demo": immutable_id},
        retain_logs=False, retain_artifacts=True, retain_evidence=False,
    )

    manifest_path = project.project_root / "artifacts" / immutable_id / "release.json"
    manifest_text = manifest_path.read_text(encoding="utf-8")
    manifest = json.loads(manifest_text)
    assert manifest_text == json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    assert manifest["artifacts"][0]["files"][0]["path"] == "demo.whl"


@pytest.mark.parametrize(
    ("parent_relative", "retain_logs"),
    [(Path("artifacts"), False), (Path("logs/cmru-release"), True)],
    ids=["preserve-artifact-parent", "preserve-log-parent"],
)
def test_retention_rollback_preserves_a_preexisting_empty_destination_parent(
    tmp_path, monkeypatch, parent_relative, retain_logs,
):
    repo_root, workspace, project, immutable_id = _release_retention_fixture(tmp_path)
    preexisting_parent = project.project_root / parent_relative
    preexisting_parent.mkdir(parents=True)
    real_move = shutil.move

    def fail_artifact_move_after_real_log_move(source, destination):
        if Path(source).name == "dist":
            raise OSError("simulated artifact move failure")
        return real_move(source, destination)

    monkeypatch.setattr(transaction.shutil, "move", fail_artifact_move_after_real_log_move)
    with pytest.raises(OSError, match="simulated artifact move failure"):
        transaction.retain_success_outputs(
            repo_root, workspace, {"demo": project}, {"demo": immutable_id},
            retain_logs=retain_logs, retain_artifacts=True, retain_evidence=False,
        )

    assert preexisting_parent.is_dir()
    if retain_logs:
        assert (workspace.path / "demo" / "logs" / "build.log").read_text(
            encoding="utf-8",
        ) == "build\n"
