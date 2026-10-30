from __future__ import annotations

import errno
import os
import shutil
from pathlib import Path

import cowtree.run as run_module
import pytest
from cowtree import SpaceEstimate, open_run
from cowtree.detect import detect
from cowtree.errors import BackendFailed, PathRefused, RootInvalid
from cowtree.gc import make_shared

from .fakes import FakeOps


def test_owner_and_plain_filesystem_detection_errors_are_typed(monkeypatch, tmp_path):
    real_read_text = run_module.Path.read_text

    def fail_boot_id(path, *args, **kwargs):
        if str(path) == "/proc/sys/kernel/random/boot_id":
            raise OSError(errno.EACCES, "boot id denied")
        return real_read_text(path, *args, **kwargs)

    monkeypatch.setattr(run_module.Path, "read_text", fail_boot_id)
    with pytest.raises(BackendFailed, match="owner-identity"):
        run_module._owner_identity()

    monkeypatch.setattr(run_module.Path, "read_text", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("mountinfo")))
    assert run_module._plain_fstype(str(tmp_path)) == "unknown"


def test_failed_identity_adoption_cleans_backend_created_path(tmp_path, monkeypatch):
    run = open_run("missing-receipt", estimate=SpaceEstimate(0, 0, 0), environ={}, plain_dir=tmp_path)
    real_created_identity = run._backend.created_identity
    monkeypatch.setattr(run._backend, "created_identity", lambda _path: None)
    with pytest.raises(PathRefused, match="did not record"):
        run.make_tree("tree")
    assert not (Path(run.path) / "trees" / "tree").exists()
    monkeypatch.setattr(run._backend, "created_identity", real_created_identity)
    run.close()


def test_run_identity_registry_is_idempotent_and_refuses_unknown_paths(tmp_path):
    run = open_run("identity-registry", estimate=SpaceEstimate(0, 0, 0), environ={}, plain_dir=tmp_path)
    tree = run.make_tree("tree")
    owned = str(tree)
    run._remember(owned)
    assert run._owned.count(owned) == 1
    with pytest.raises(PathRefused, match="no recorded identity"):
        run._require_owned_identity(str(Path(run.path) / "trees" / "foreign"))
    run.close()


def test_shared_source_is_authorized_for_tree_and_file_clones(tmp_path):
    root = tmp_path / "cow-area"
    root.mkdir(mode=0o700)
    ops = FakeOps(filesystem="xfs")
    area = detect(environ={"MDT_COW_ROOT": str(root)}, ops=ops, log=lambda _message: None)
    assert area is not None
    shared = Path(make_shared(area, "template"))
    (shared / "file").write_text("shared")
    run = open_run("shared-clone", estimate=SpaceEstimate(0, 0, 0), environ={"MDT_COW_ROOT": str(root)},
                   ops=ops, log=lambda _message: None)
    clone = run.clone_tree(shared, "tree")
    assert (clone / "file").read_text() == "shared"
    file_clone = Path(run.path) / "trees" / "file-copy"
    run.clone_file(shared / "file", file_clone)
    assert file_clone.read_text() == "shared"
    run.close()
    shutil.rmtree(root / "shared")
    shutil.rmtree(root / ".cowtree")


def test_reserve_refuses_missing_plain_admission_directory(tmp_path):
    run = open_run("reserve-without-base", estimate=SpaceEstimate(0, 0, 0), environ={}, plain_dir=tmp_path)
    run._plain_base = None
    with pytest.raises(RootInvalid, match="no space-admission directory"):
        run.reserve(SpaceEstimate(0, 0, 0))
    run._plain_base = str(tmp_path)
    run.close()


def test_close_reports_failure_while_removing_manifest(tmp_path, monkeypatch):
    run = open_run("manifest-close", estimate=SpaceEstimate(0, 0, 0), environ={}, plain_dir=tmp_path)
    path = Path(run.path)
    real_unlink = run_module.os.unlink

    def fail_manifest(value, *args, **kwargs):
        if os.fspath(value) == str(path / "owner.json"):
            raise PermissionError(errno.EACCES, "manifest removal denied")
        return real_unlink(value, *args, **kwargs)

    monkeypatch.setattr(run_module.os, "unlink", fail_manifest)
    with pytest.raises(BackendFailed, match="close"):
        run.close()
    assert path.exists()
    monkeypatch.setattr(run_module.os, "unlink", real_unlink)
    shutil.rmtree(path)


def test_close_reports_unlock_failure_and_releases_descriptor(tmp_path, monkeypatch):
    import fcntl

    run = open_run("unlock-close", estimate=SpaceEstimate(0, 0, 0), environ={}, plain_dir=tmp_path)
    path = Path(run.path)
    real_flock = run_module.fcntl.flock

    def fail_unlock(fd, operation):
        if operation == fcntl.LOCK_UN:
            raise OSError(errno.EIO, "unlock denied")
        return real_flock(fd, operation)

    monkeypatch.setattr(run_module.fcntl, "flock", fail_unlock)
    with pytest.raises(BackendFailed, match="close"):
        run.close()
    assert not path.exists()


@pytest.mark.parametrize("failure_stage", ["trees", "run"])
def test_close_reports_directory_removal_failures(tmp_path, monkeypatch, failure_stage):
    run = open_run(f"{failure_stage}-rmdir", estimate=SpaceEstimate(0, 0, 0), environ={}, plain_dir=tmp_path)
    path = Path(run.path)
    real_rmdir = run_module.os.rmdir
    target = path / "trees" if failure_stage == "trees" else path

    def fail_target(value, *args, **kwargs):
        if os.fspath(value) == str(target):
            raise OSError(errno.EIO, "directory removal denied")
        return real_rmdir(value, *args, **kwargs)

    monkeypatch.setattr(run_module.os, "rmdir", fail_target)
    with pytest.raises(BackendFailed, match="close"):
        run.close()
    monkeypatch.setattr(run_module.os, "rmdir", real_rmdir)
    shutil.rmtree(path)


def test_close_reports_owner_lock_unlink_and_descriptor_close_errors(tmp_path, monkeypatch):
    run = open_run("lock-unlink", estimate=SpaceEstimate(0, 0, 0), environ={}, plain_dir=tmp_path)
    path = Path(run.path)
    real_unlink = run_module.os.unlink

    def fail_lock_unlink(value, *args, **kwargs):
        if os.fspath(value) == str(path / "owner.lock"):
            raise PermissionError(errno.EACCES, "lock unlink denied")
        return real_unlink(value, *args, **kwargs)

    monkeypatch.setattr(run_module.os, "unlink", fail_lock_unlink)
    with pytest.raises(BackendFailed, match="close"):
        run.close()
    monkeypatch.setattr(run_module.os, "unlink", real_unlink)
    shutil.rmtree(path)

    run = open_run("fd-close", estimate=SpaceEstimate(0, 0, 0), environ={}, plain_dir=tmp_path)
    path = Path(run.path)
    lock_fd = run._lock_fd
    real_close = run_module.os.close

    def fail_lock_close(fd):
        if fd == lock_fd:
            raise OSError(errno.EIO, "lock close failed")
        return real_close(fd)

    monkeypatch.setattr(run_module.os, "close", fail_lock_close)
    with pytest.raises(BackendFailed, match="close"):
        run.close()
    monkeypatch.setattr(run_module.os, "close", real_close)
    real_close(lock_fd)
    assert not path.exists()

