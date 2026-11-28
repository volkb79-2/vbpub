"""Caller-visible transaction witnesses for R2 survivor batch 3."""
from __future__ import annotations

import json
import os
import shutil
from types import SimpleNamespace

import pytest

from cmru import transaction
from tests._git_isolation import git_isolation  # noqa: F401

pytestmark = pytest.mark.usefixtures("git_isolation")


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
