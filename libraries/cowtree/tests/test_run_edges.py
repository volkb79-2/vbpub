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

from .fakes import FakeOps, StatResultProxy


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


def test_run_record_identity_rejects_unavailable_and_nonowned_shapes(tmp_path, monkeypatch):
    run = open_run("identity-record", estimate=SpaceEstimate(0, 0, 0), environ={}, plain_dir=tmp_path)
    tree = Path(run.path) / "trees" / "candidate"
    tree.symlink_to(tmp_path, target_is_directory=True)
    info = os.lstat(tree)
    with pytest.raises(PathRefused, match="real directory or regular file"):
        run._record_identity(str(tree), expected_identity=(info.st_dev, info.st_ino))
    tree.unlink()
    original_lstat = run_module.os.lstat

    def fail_candidate_lstat(value, *args, **kwargs):
        if os.fspath(value) == str(tree):
            raise PermissionError(errno.EACCES, "candidate stat denied")
        return original_lstat(value, *args, **kwargs)

    monkeypatch.setattr(run_module.os, "lstat", fail_candidate_lstat)
    with pytest.raises(PathRefused, match="run-owned path is unavailable"):
        run._record_identity(str(tree), expected_identity=(1, 2))
    monkeypatch.setattr(run_module.os, "lstat", original_lstat)
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


def test_run_refuses_non_directory_and_unavailable_owned_paths(tmp_path, monkeypatch):
    run = open_run("path-shape", estimate=SpaceEstimate(0, 0, 0), environ={}, plain_dir=tmp_path)
    tree = run.make_tree("tree")
    original_lstat = run_module.os.lstat

    def non_directory_run(value, *args, **kwargs):
        info = original_lstat(value, *args, **kwargs)
        if os.fspath(value) == run.path:
            return StatResultProxy(info, st_mode=0o100600)
        return info

    monkeypatch.setattr(run_module.os, "lstat", non_directory_run)
    with pytest.raises(PathRefused, match="run path must be a real directory"):
        run._require_trees_directory()
    monkeypatch.setattr(run_module.os, "lstat", original_lstat)

    def fail_owned_lstat(value, *args, **kwargs):
        if os.fspath(value) == str(tree):
            raise PermissionError(errno.EACCES, "owned path unavailable")
        return original_lstat(value, *args, **kwargs)

    monkeypatch.setattr(run_module.os, "lstat", fail_owned_lstat)
    with pytest.raises(PathRefused, match="run-owned path is unavailable"):
        run._require_owned_identity(str(tree))
    monkeypatch.setattr(run_module.os, "lstat", original_lstat)

    saved = Path(run.path) / "trees" / "saved"
    tree.rename(saved)
    tree.mkdir()
    with pytest.raises(PathRefused, match="identity changed"):
        run._require_owned_identity(str(tree))
    tree.rmdir()
    saved.rename(tree)
    run.close()


def test_run_rejects_invalid_ingest_and_allowed_symlink_clone_sources(tmp_path):
    run = open_run("source-shapes", estimate=SpaceEstimate(0, 0, 0), environ={}, plain_dir=tmp_path)
    with pytest.raises(PathRefused, match="ingest source"):
        run.ingest_tree(tmp_path / "missing", "missing")
    target = run.make_tree("target")
    link = Path(run.path) / "trees" / "link"
    link.symlink_to(target, target_is_directory=True)
    with pytest.raises(PathRefused, match="clone source must be a real directory"):
        run.clone_tree(link, "clone")
    link.unlink()
    run.close()


def test_run_clone_backend_failure_cleans_receipt_and_reopens(tmp_path, monkeypatch):
    run = open_run("clone-failure", estimate=SpaceEstimate(0, 0, 0), environ={}, plain_dir=tmp_path)
    source = run.make_tree("source")

    def fail_clone(_source, _destination):
        raise BackendFailed("clone", errno.EIO, str(source), "copy failed")

    monkeypatch.setattr(run._backend, "clone_tree", fail_clone)
    with pytest.raises(BackendFailed, match="copy failed"):
        run.clone_tree(source, "target")
    assert str(Path(run.path) / "trees" / "target") not in run._owned
    run.close()


def test_run_seal_delete_and_file_receipt_errors_are_typed(tmp_path, monkeypatch):
    run = open_run("typed-operations", estimate=SpaceEstimate(0, 0, 0), environ={}, plain_dir=tmp_path)
    tree = run.make_tree("tree")

    monkeypatch.setattr(run._backend, "seal", lambda _path: (_ for _ in ()).throw(OSError(errno.EIO, "seal denied")))
    with pytest.raises(BackendFailed, match="seal"):
        run.seal(tree)
    monkeypatch.setattr(run._backend, "seal", lambda _path: None)

    monkeypatch.setattr(run._backend, "delete_tree", lambda _path: (_ for _ in ()).throw(OSError(errno.EIO, "delete denied")))
    with pytest.raises(BackendFailed, match="delete-tree"):
        run.delete_tree(tree)
    monkeypatch.setattr(run._backend, "delete_tree", lambda path: run_module.CopyBackend().delete_tree(path))
    run.delete_tree(tree)
    run.close()


def test_nested_clone_file_requires_backend_receipt_and_cleans_failed_output(tmp_path, monkeypatch):
    run = open_run("file-receipt", estimate=SpaceEstimate(0, 0, 0), environ={}, plain_dir=tmp_path)
    tree = run.make_tree("tree")
    source = tree / "source"
    source.write_text("input")
    real_created_identity = run._backend.created_identity
    monkeypatch.setattr(run._backend, "created_identity", lambda _path: None)
    destination = tree / "copy"
    with pytest.raises(PathRefused, match="did not record the created file identity"):
        run.clone_file(source, destination)
    assert not destination.exists() and source.read_text() == "input"
    monkeypatch.setattr(run._backend, "created_identity", real_created_identity)
    run.close()


def test_open_run_allocation_and_lock_failures_clean_partial_runs(tmp_path, monkeypatch):
    root = tmp_path / "area"
    root.mkdir()
    ops = FakeOps(filesystem="xfs")
    monkeypatch.setattr(run_module, "_utc_now", lambda: ("stamp", "created"))
    monkeypatch.setattr(run_module.secrets, "token_hex", lambda _n: "a" * 16)
    detect(environ={"MDT_COW_ROOT": str(root)}, ops=ops, log=lambda _message: None)
    runs_dir = root / ".cowtree" / "runs"
    expected = runs_dir / "collision-stamp-aaaaaaaaaaaaaaaa"
    expected.mkdir(parents=True)
    with pytest.raises(BackendFailed, match="run id collision"):
        open_run("collision", estimate=SpaceEstimate(0, 0, 0), environ={"MDT_COW_ROOT": str(root)}, ops=ops)
    expected.rmdir()

    real_open = run_module.os.open

    def fail_lock_open(path, *args, **kwargs):
        if os.fspath(path).endswith("/owner.lock"):
            raise PermissionError(errno.EACCES, "lock create denied")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(run_module.os, "open", fail_lock_open)
    with pytest.raises(PermissionError, match="lock create denied"):
        open_run("lock-open", estimate=SpaceEstimate(0, 0, 0), environ={}, plain_dir=tmp_path)


def test_top_level_clone_file_failure_removes_ownership_tracking(tmp_path, monkeypatch):
    run = open_run("top-level-file", estimate=SpaceEstimate(0, 0, 0), environ={}, plain_dir=tmp_path)
    source_tree = run.make_tree("source")
    source = source_tree / "payload"
    source.write_text("payload")
    destination = Path(run.path) / "trees" / "copy"
    real_created_identity = run._backend.created_identity
    monkeypatch.setattr(run._backend, "created_identity", lambda _path: None)

    with pytest.raises(PathRefused, match="did not record"):
        run.clone_file(source, destination)

    assert not destination.exists()
    assert str(destination) not in run._owned
    assert str(destination) not in run._owned_identities
    monkeypatch.setattr(run._backend, "created_identity", real_created_identity)
    run.close()


@pytest.mark.parametrize("shape", ["unavailable", "symlink", "directory"])
def test_record_created_file_identity_refuses_unavailable_or_nonfile_paths(tmp_path, monkeypatch, shape):
    run = open_run("file-shape", estimate=SpaceEstimate(0, 0, 0), environ={}, plain_dir=tmp_path)
    tree = run.make_tree("tree")
    destination = tree / "candidate"
    destination.write_text("payload")
    info = os.lstat(destination)
    identity = (info.st_dev, info.st_ino)
    real_created_identity = run._backend.created_identity
    real_lstat = run_module.os.lstat
    monkeypatch.setattr(run._backend, "created_identity", lambda _path: identity)

    def altered_lstat(value, *args, **kwargs):
        if os.fspath(value) == str(destination):
            if shape == "unavailable":
                raise PermissionError(errno.EACCES, "file stat denied")
            mode = 0o120777 if shape == "symlink" else 0o040700
            return StatResultProxy(real_lstat(value, *args, **kwargs), st_mode=mode)
        return real_lstat(value, *args, **kwargs)

    monkeypatch.setattr(run_module.os, "lstat", altered_lstat)
    expected = "unavailable" if shape == "unavailable" else "regular file"
    with pytest.raises(PathRefused, match=expected):
        run._record_created_file_identity(str(destination))

    monkeypatch.setattr(run_module.os, "lstat", real_lstat)
    monkeypatch.setattr(run._backend, "created_identity", real_created_identity)
    run._backend.forget_created_identity(str(destination))
    destination.unlink()
    run.close()


@pytest.mark.parametrize("operation", ["mkdir", "chmod"])
def test_area_run_allocation_os_errors_remove_partial_directory(tmp_path, monkeypatch, operation):
    root = tmp_path / "area"
    root.mkdir()
    ops = FakeOps(filesystem="xfs")
    detect(environ={"MDT_COW_ROOT": str(root)}, ops=ops, log=lambda _message: None)
    monkeypatch.setattr(run_module, "_utc_now", lambda: ("stamp", "created"))
    monkeypatch.setattr(run_module.secrets, "token_hex", lambda _n: "b" * 16)
    tool = f"allocation-{operation}"
    expected = root / ".cowtree" / "runs" / f"{tool}-stamp-{'b' * 16}"

    if operation == "mkdir":
        real_mkdir = run_module.os.mkdir

        def fail_mkdir(value, *args, **kwargs):
            if os.fspath(value) == str(expected):
                raise PermissionError(errno.EACCES, "run directory create denied")
            return real_mkdir(value, *args, **kwargs)

        monkeypatch.setattr(run_module.os, "mkdir", fail_mkdir)
    else:
        real_chmod = run_module.os.chmod

        def fail_chmod(value, *args, **kwargs):
            if os.fspath(value) == str(expected):
                raise PermissionError(errno.EACCES, "run directory chmod denied")
            return real_chmod(value, *args, **kwargs)

        monkeypatch.setattr(run_module.os, "chmod", fail_chmod)

    with pytest.raises(BackendFailed, match="allocate-run"):
        open_run(tool, estimate=SpaceEstimate(0, 0, 0), environ={"MDT_COW_ROOT": str(root)}, ops=ops)
    assert not expected.exists()


def test_open_run_preserves_setup_error_when_partial_cleanup_fails(tmp_path, monkeypatch):
    original_write_manifest = run_module._write_manifest
    original_rmtree = shutil.rmtree
    partial_path = []

    def fail_manifest(path, _document, *, create):
        assert create is True
        partial_path.append(path)
        raise OSError(errno.EIO, "manifest write failed")

    def fail_cleanup(path, *args, **kwargs):
        if os.fspath(path) == partial_path[0]:
            raise OSError(errno.EACCES, "partial cleanup denied")
        return original_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(run_module, "_write_manifest", fail_manifest)
    monkeypatch.setattr(shutil, "rmtree", fail_cleanup)
    with pytest.raises(OSError, match="manifest write failed"):
        open_run("setup-cleanup", estimate=SpaceEstimate(0, 0, 0), environ={}, plain_dir=tmp_path)
    assert partial_path and Path(partial_path[0]).is_dir()
    monkeypatch.setattr(run_module, "_write_manifest", original_write_manifest)
    monkeypatch.setattr(shutil, "rmtree", original_rmtree)
    original_rmtree(partial_path[0])



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

