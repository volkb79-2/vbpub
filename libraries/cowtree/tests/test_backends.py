from __future__ import annotations

import errno
import os
import shutil
import stat
import struct
import subprocess
import tempfile
from pathlib import Path

import cowtree.backends as backend_module
import pytest
from cowtree.backends import (
    BTRFS_IOC_SNAP_CREATE_V2,
    BTRFS_IOC_SNAP_DESTROY,
    BTRFS_IOC_SUBVOL_CREATE,
    BTRFS_IOC_SUBVOL_GETFLAGS,
    BTRFS_IOC_SUBVOL_SETFLAGS,
    BTRFS_SUBVOL_RDONLY,
    FICLONE,
    BtrfsBackend,
    CopyBackend,
    LinuxOps,
    ReflinkBackend,
    _cleanup_created,
    _identity_at,
    _old_vol_args,
    _rename_noreplace,
    _vol_args_v2,
)
from cowtree.detect import _reset_info_for_tests, detect
from cowtree.errors import BackendFailed, PathRefused, RootInvalid
from cowtree.paths import mount_fstype

from .fakes import FakeOps, StatResultProxy


def _magic_inode(monkeypatch, *paths):
    real_stat = os.stat
    targets = {os.path.abspath(os.fspath(path)) for path in paths}

    def fake_stat(path, *args, **kwargs):
        result = real_stat(path, *args, **kwargs)
        if isinstance(path, (str, bytes, os.PathLike)) and os.path.abspath(os.fspath(path)) in targets:
            return StatResultProxy(result, st_ino=256)
        return result

    monkeypatch.setattr(backend_module.os, "stat", fake_stat)


def test_subvol_root_snapshot_else_walk(tmp_path, monkeypatch):
    ops = FakeOps()
    backend = BtrfsBackend(ops)
    source = tmp_path / "source"
    (source / "dir").mkdir(parents=True)
    (source / "dir" / "data").write_text("snapshot")
    destination = tmp_path / "snapshot"
    _magic_inode(monkeypatch, source)
    backend.clone_tree(str(source), str(destination))
    assert ops.snapshot_calls and (destination / "dir" / "data").read_text() == "snapshot"

    monkeypatch.undo()
    regular_source = tmp_path / "regular"
    regular_source.mkdir()
    (regular_source / "data").write_bytes(b"walk")
    walk_destination = tmp_path / "walk-copy"
    backend.clone_tree(str(regular_source), str(walk_destination))
    assert (walk_destination / "data").read_bytes() == b"walk"
    assert ops.clone_calls == 1


def test_snapshot_of_sealed_template_is_made_writable(tmp_path, monkeypatch):
    ops = FakeOps()
    backend = BtrfsBackend(ops)
    source = tmp_path / "sealed-template"
    source.mkdir()
    _magic_inode(monkeypatch, source)
    backend.seal(str(source))
    source_inode = os.lstat(source).st_ino
    destination = tmp_path / "candidate"

    backend.clone_tree(str(source), str(destination))

    destination_inode = os.lstat(destination).st_ino
    assert ops.flags[source_inode] & BTRFS_SUBVOL_RDONLY
    assert not ops.flags[destination_inode] & BTRFS_SUBVOL_RDONLY


def test_snapshot_flag_failure_cleans_candidate_and_raises_typed_error(tmp_path, monkeypatch):
    ops = FakeOps()
    backend = BtrfsBackend(ops)
    source = tmp_path / "sealed-template"
    source.mkdir()
    destination = tmp_path / "candidate"
    _magic_inode(monkeypatch, source, destination)
    backend.seal(str(source))
    original_setflags = ops.subvol_setflags
    fail_once = True

    def fail_first_unseal(fd, flags):
        nonlocal fail_once
        if fail_once:
            fail_once = False
            raise OSError(errno.EIO, "cannot make snapshot writable")
        original_setflags(fd, flags)

    ops.subvol_setflags = fail_first_unseal
    with pytest.raises(BackendFailed) as caught:
        backend.clone_tree(str(source), str(destination))
    assert caught.value.op == "snapshot-writable"
    assert not destination.exists()


def test_snapshot_open_failure_cleans_candidate_without_leaked_fd(tmp_path, monkeypatch):
    ops = FakeOps()
    backend = BtrfsBackend(ops)
    source = tmp_path / "template"
    source.mkdir()
    destination = tmp_path / "candidate"
    _magic_inode(monkeypatch, source, destination)
    original_open = ops.open
    fail_destination_once = True

    def fail_first_destination_open(path, *args, **kwargs):
        nonlocal fail_destination_once
        if path == str(destination) and fail_destination_once:
            fail_destination_once = False
            raise OSError(errno.EIO, "cannot inspect snapshot")
        return original_open(path, *args, **kwargs)

    ops.open = fail_first_destination_open
    with pytest.raises(BackendFailed) as caught:
        backend.clone_tree(str(source), str(destination))
    assert caught.value.op == "snapshot-writable"
    assert not destination.exists()


def test_snapshot_cleanup_logs_secondary_delete_failure(tmp_path, monkeypatch):
    logs = []
    ops = FakeOps()
    backend = BtrfsBackend(ops, log=logs.append)
    source = tmp_path / "sealed-template"
    source.mkdir()
    destination = tmp_path / "candidate"
    _magic_inode(monkeypatch, source, destination)
    backend.seal(str(source))
    original_setflags = ops.subvol_setflags
    fail_flag_clear = True

    def fail_flag_clear_once(fd, flags):
        nonlocal fail_flag_clear
        if fail_flag_clear and not flags & BTRFS_SUBVOL_RDONLY:
            fail_flag_clear = False
            raise OSError(errno.EIO, "cannot make snapshot writable")
        original_setflags(fd, flags)

    ops.subvol_setflags = fail_flag_clear_once

    def fail_cleanup(_path):
        raise OSError(errno.EIO, "cleanup unavailable")

    backend.delete_tree = fail_cleanup
    with pytest.raises(BackendFailed) as caught:
        backend.clone_tree(str(source), str(destination))
    assert caught.value.op == "snapshot-writable"
    assert any("cleanup of partial path" in line for line in logs)
    shutil.rmtree(destination)


def test_snapshot_close_failure_preserves_error_and_logs_retry_failure(tmp_path, monkeypatch):
    logs = []
    ops = FakeOps()
    backend = BtrfsBackend(ops, log=logs.append)
    source = tmp_path / "template"
    source.mkdir()
    destination = tmp_path / "candidate"
    _magic_inode(monkeypatch, source, destination)
    original_close = ops.close
    failed_closes = 0

    def fail_twice_for_candidate(fd):
        nonlocal failed_closes
        if ops._fd_path(fd) == str(destination) and failed_closes < 2:
            failed_closes += 1
            raise OSError(errno.EIO, "close failed")
        original_close(fd)

    ops.close = fail_twice_for_candidate
    with pytest.raises(BackendFailed) as caught:
        backend.clone_tree(str(source), str(destination))
    assert caught.value.op == "snapshot-writable"
    assert any("close after snapshot flag failure also failed" in line for line in logs)
    assert not destination.exists()


def test_destroy_eperm_rmdir_path(tmp_path, monkeypatch):
    ops = FakeOps(destroy_error=errno.EPERM)
    backend = BtrfsBackend(ops)
    root = tmp_path / "tree"
    root.mkdir()
    sealed = root / "sealed"
    sealed.mkdir()
    (sealed / "file").write_text("x")
    sealed.chmod(0o500)
    outside = tmp_path / "outside"
    outside.write_text("keep")
    (root / "sealed-link").symlink_to(outside)
    _magic_inode(monkeypatch, root)
    backend.delete_tree(str(root))
    assert not root.exists()
    assert ops.destroy_calls == ["tree"]
    assert outside.read_text() == "keep"


def test_seal_rdonly_and_unseal_on_delete(tmp_path, monkeypatch):
    ops = FakeOps()
    backend = BtrfsBackend(ops)
    root = tmp_path / "tree"
    root.mkdir()
    inode = os.stat(root).st_ino
    _magic_inode(monkeypatch, root)
    backend.seal(str(root))
    assert next(iter(ops.flags.values())) == BTRFS_SUBVOL_RDONLY
    backend.delete_tree(str(root))
    assert ops.flags[inode] == 0
    assert not root.exists()


@pytest.mark.parametrize("compression", ["zstd", "zstd:3", "lzo", "none"])
def test_compression_xattr_values(tmp_path, compression):
    ops = FakeOps()
    backend = BtrfsBackend(ops)
    tree = backend.make_tree(str(tmp_path / "tree"), compression=compression)
    assert Path(tree).is_dir()
    assert ops.xattrs == [(tree, "btrfs.compression", compression.encode(), False)]


@pytest.mark.parametrize("compression", ["zstd:0", "zstd:99", "zstd:", "zstd:000", "other", "zstd:abc", 123])
def test_invalid_compression_refused(tmp_path, compression):
    ops = FakeOps()
    backend = BtrfsBackend(ops)
    with pytest.raises(PathRefused):
        backend.make_tree(str(tmp_path / "tree"), compression=compression)
    assert not (tmp_path / "tree").exists()
    assert ops.create_calls == []


def test_compression_rejects_non_ascii_digits_before_subvolume_create(tmp_path):
    ops = FakeOps()
    with pytest.raises(PathRefused, match="compression"):
        BtrfsBackend(ops).make_tree(str(tmp_path / "tree"), compression="zstd:٣")
    assert ops.create_calls == []


@pytest.mark.parametrize("backend_type", [CopyBackend, ReflinkBackend])
def test_clone_of_sealed_tree_is_writable(backend_type, tmp_path):
    backend = backend_type(FakeOps())
    source = tmp_path / "sealed"
    nested = source / "nested"
    nested.mkdir(parents=True)
    source_file = nested / "payload"
    source_file.write_text("original")
    backend.seal(str(source))

    clone = tmp_path / "clone"
    backend.clone_tree(str(source), str(clone))

    cloned_file = clone / "nested" / "payload"
    assert stat.S_IMODE(os.stat(clone).st_mode) & stat.S_IWUSR
    assert stat.S_IMODE(os.stat(clone / "nested").st_mode) & stat.S_IWUSR
    assert stat.S_IMODE(cloned_file.stat().st_mode) & stat.S_IWUSR
    (clone / "nested" / "new-file").write_text("candidate")
    cloned_file.write_text("changed")
    assert source_file.read_text() == "original"


def test_reflink_clone_tree_uses_ficlone_for_regular_files(tmp_path):
    ops = FakeOps()
    backend = ReflinkBackend(ops)
    source = tmp_path / "source"
    source.mkdir()
    (source / "payload").write_bytes(b"clone through fake FICLONE")
    destination = tmp_path / "clone"

    backend.clone_tree(str(source), str(destination))

    assert ops.clone_calls == 1
    assert (destination / "payload").read_bytes() == b"clone through fake FICLONE"


@pytest.mark.parametrize("backend_type", [CopyBackend, ReflinkBackend])
def test_clone_of_unsealed_readonly_tree_preserves_modes(backend_type, tmp_path):
    backend = backend_type(FakeOps())
    source = tmp_path / "readonly-source"
    nested = source / "nested"
    nested.mkdir(parents=True)
    payload = nested / "payload"
    payload.write_text("input")
    payload.chmod(0o440)
    nested.chmod(0o550)
    source.chmod(0o550)

    destination = tmp_path / "clone"
    backend.clone_tree(str(source), str(destination))

    assert stat.S_IMODE(destination.stat().st_mode) == 0o550
    assert stat.S_IMODE((destination / "nested").stat().st_mode) == 0o550
    assert stat.S_IMODE((destination / "nested" / "payload").stat().st_mode) == 0o440
    source.chmod(0o750)
    nested.chmod(0o750)
    payload.chmod(0o640)
    destination.chmod(0o750)
    (destination / "nested").chmod(0o750)
    (destination / "nested" / "payload").chmod(0o640)


@pytest.mark.parametrize("backend_type", [CopyBackend, ReflinkBackend])
def test_ingest_readonly_directories_preserves_final_modes(backend_type, tmp_path):
    backend = backend_type(FakeOps())
    source = tmp_path / "readonly-source"
    nested = source / "nested"
    nested.mkdir(parents=True)
    payload = nested / "payload"
    payload.write_text("input")
    payload.chmod(0o440)
    nested.chmod(0o550)

    destination = tmp_path / "ingested"
    backend.ingest_tree(str(source), str(destination))

    assert stat.S_IMODE((destination / "nested").stat().st_mode) == 0o550
    assert stat.S_IMODE((destination / "nested" / "payload").stat().st_mode) == 0o440
    nested.chmod(0o750)
    payload.chmod(0o640)
    (destination / "nested").chmod(0o750)
    (destination / "nested" / "payload").chmod(0o640)


def test_backend_preserves_a_destination_that_won_the_create_race(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    destination = tmp_path / "destination"
    destination.mkdir()
    marker = destination / "keep"
    marker.write_text("other writer")

    with pytest.raises(PathRefused, match="already exists"):
        CopyBackend().ingest_tree(str(source), str(destination))

    assert marker.read_text() == "other writer"


def test_nested_file_collision_preserves_the_competing_child(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    destination = tmp_path / "destination"

    def nested_collision(_src, dst, *_args, **_kwargs):
        (Path(dst) / "other-writer").write_text("preserve")
        raise FileExistsError(errno.EEXIST, "another writer inserted a child")

    monkeypatch.setattr(backend_module, "_copy_tree_contents", nested_collision)
    with pytest.raises(BackendFailed) as caught:
        CopyBackend(FakeOps()).ingest_tree(str(source), str(destination))

    assert caught.value.op == "ingest-tree"
    assert (destination / "other-writer").read_text() == "preserve"
    (destination / "other-writer").unlink()
    destination.rmdir()


def test_cleanup_rechecks_identity_after_isolating_a_partial_path(tmp_path, monkeypatch):
    path = tmp_path / "partial"
    path.mkdir()
    identity = (os.lstat(path).st_dev, os.lstat(path).st_ino)
    saved = tmp_path / "created-by-cowtree"
    real_rename_noreplace = backend_module._rename_noreplace
    injected = False

    def replace_before_quarantine(source, destination):
        nonlocal injected
        if source == str(path) and not injected:
            injected = True
            real_rename_noreplace(source, str(saved))
            path.mkdir()
            (path / "keep").write_text("other writer")
        real_rename_noreplace(source, destination)

    monkeypatch.setattr(backend_module, "_rename_noreplace", replace_before_quarantine)
    _cleanup_created(str(path), identity, shutil.rmtree, lambda _message: None)

    assert injected
    assert (path / "keep").read_text() == "other writer"
    saved.rmdir()


def test_identity_at_falls_back_to_open_and_fstat(tmp_path, monkeypatch):
    tree = tmp_path / "tree"
    tree.mkdir()
    parent_fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    real_stat = os.stat

    def fail_stat(path, *args, **kwargs):
        if path == "tree" and kwargs.get("dir_fd") == parent_fd:
            raise OSError(errno.EIO, "statat unavailable")
        return real_stat(path, *args, **kwargs)

    monkeypatch.setattr(backend_module.os, "stat", fail_stat)
    try:
        assert _identity_at(parent_fd, "tree") == (os.lstat(tree).st_dev, os.lstat(tree).st_ino)
    finally:
        os.close(parent_fd)


def test_identity_at_preserves_stat_error_when_fd_fallback_fails(tmp_path, monkeypatch):
    parent_fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    real_stat = os.stat
    real_open = os.open

    def fail_stat(path, *args, **kwargs):
        if path == "missing" and kwargs.get("dir_fd") == parent_fd:
            raise OSError(errno.EIO, "statat unavailable")
        return real_stat(path, *args, **kwargs)

    def fail_open(path, *args, **kwargs):
        if path == "missing" and kwargs.get("dir_fd") == parent_fd:
            raise OSError(errno.EACCES, "openat unavailable")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(backend_module.os, "stat", fail_stat)
    monkeypatch.setattr(backend_module.os, "open", fail_open)
    try:
        with pytest.raises(OSError, match="statat unavailable"):
            _identity_at(parent_fd, "missing")
    finally:
        os.close(parent_fd)


def test_rename_noreplace_moves_without_replacing(tmp_path):
    source = tmp_path / "source"
    source.write_text("source")
    destination = tmp_path / "destination"
    _rename_noreplace(str(source), str(destination))
    assert not source.exists() and destination.read_text() == "source"

    source.write_text("keep source")
    with pytest.raises(OSError) as caught:
        _rename_noreplace(str(source), str(destination))
    assert caught.value.errno == errno.EEXIST
    assert source.read_text() == "keep source" and destination.read_text() == "source"


def test_rename_noreplace_reports_unavailable_libc_symbol(monkeypatch, tmp_path):
    monkeypatch.setattr(backend_module.ctypes, "CDLL", lambda *_args, **_kwargs: object())
    with pytest.raises(OSError) as caught:
        _rename_noreplace(str(tmp_path / "source"), str(tmp_path / "destination"))
    assert caught.value.errno == errno.ENOSYS


def test_restore_quarantined_does_not_replace_a_recreated_path(tmp_path, monkeypatch):
    quarantine = tmp_path / "quarantine"
    quarantine.write_text("preserved source")
    destination = tmp_path / "destination"
    logs = []
    real_rename_noreplace = backend_module._rename_noreplace

    def recreate_then_restore(source, target):
        destination.write_text("other writer")
        real_rename_noreplace(source, target)

    monkeypatch.setattr(backend_module, "_rename_noreplace", recreate_then_restore)
    backend_module._restore_quarantined(str(quarantine), str(destination), logs.append)

    assert destination.read_text() == "other writer"
    assert quarantine.read_text() == "preserved source"
    assert any("cannot restore preserved path" in message for message in logs)


@pytest.mark.parametrize("failing_operation", ["open", "fstat"])
def test_btrfs_make_tree_cleans_after_descriptor_inspection_failure(tmp_path, failing_operation):
    ops = FakeOps(filesystem="btrfs")
    backend = BtrfsBackend(ops)
    destination = tmp_path / "tree"
    original_open = ops.open
    original_fstat = ops.fstat

    if failing_operation == "open":
        def fail_tree_open(path, *args, **kwargs):
            if path == "tree":
                raise OSError(errno.EIO, "open created tree failed")
            return original_open(path, *args, **kwargs)

        ops.open = fail_tree_open
    else:
        def fail_tree_fstat(fd):
            if ops._fd_path(fd) == str(destination):
                raise OSError(errno.EIO, "fstat created tree failed")
            return original_fstat(fd)

        ops.fstat = fail_tree_fstat

    with pytest.raises(BackendFailed):
        backend.make_tree(str(destination))
    assert ops.create_calls == ["tree"]
    assert not destination.exists()


def test_ficlone_eopnotsupp_root_invalid(tmp_path):
    _reset_info_for_tests()
    root = tmp_path / "area"
    root.mkdir(mode=0o700)
    ops = FakeOps(filesystem="ext4", clone_error=errno.EOPNOTSUPP)
    with pytest.raises(RootInvalid, match="cannot clone files"):
        detect(environ={"MDT_COW_ROOT": str(root)}, ops=ops, log=lambda _message: None)
    assert (root / ".cowtree" / "runs").is_dir()
    assert list((root / ".cowtree" / "runs").iterdir()) == []


def test_exdev_backend_failed_not_copy(tmp_path):
    ops = FakeOps(clone_error=errno.EXDEV)
    backend = ReflinkBackend(ops)
    source = tmp_path / "source"
    source.mkdir()
    (source / "f").write_text("hello")
    with pytest.raises(BackendFailed) as caught:
        backend.clone_tree(str(source), str(tmp_path / "clone"))
    assert caught.value.errno == errno.EXDEV
    assert not (tmp_path / "clone" / "f").exists()


def test_ingest_tree_falls_back_to_bytes_on_exdev_only(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "f").write_bytes(b"input")
    destination = tmp_path / "ingested"
    ops = FakeOps(clone_error=errno.EXDEV)
    ReflinkBackend(ops).ingest_tree(str(source), str(destination))
    assert (destination / "f").read_bytes() == b"input"
    assert ops.clone_calls == 1


def test_plain_backend_and_cp_reflink_auto_fallback(tmp_path):
    cp = "cp"
    source_root = Path(__file__).parent
    mountinfo = Path("/proc/self/mountinfo").read_text(encoding="utf-8")
    assert mount_fstype(str(source_root), mountinfo) == "ext4", "registered tester-unified lane needs ext4 for this fallback probe"
    with tempfile.TemporaryDirectory(dir=source_root) as scratch:
        scratch_dir = Path(scratch)
        source = scratch_dir / "source"
        source.write_bytes(b"ext4 fallback" * 4096)
        always_destination = scratch_dir / "always-copy"
        always = subprocess.run([cp, "--reflink=always", str(source), str(always_destination)], capture_output=True, check=False)
        assert always.returncode != 0
        cp_destination = scratch_dir / "cp-copy"
        completed = subprocess.run([cp, "--reflink=auto", str(source), str(cp_destination)], capture_output=True, check=False)
        assert completed.returncode == 0 and cp_destination.read_bytes() == source.read_bytes()

        plain_destination = scratch_dir / "plain-copy"
        CopyBackend().clone_file(str(source), str(plain_destination))
        assert plain_destination.read_bytes() == source.read_bytes()

        strict = ReflinkBackend(FakeOps(clone_error=errno.EOPNOTSUPP))
        with pytest.raises(BackendFailed) as caught:
            strict.clone_file(str(source), str(scratch_dir / "strict-clone"))
        assert caught.value.errno == errno.EOPNOTSUPP


def test_ingest_cross_fs_copies_once(tmp_path, monkeypatch):
    ops = FakeOps()
    backend = ReflinkBackend(ops)
    source = tmp_path / "source"
    source.mkdir()
    (source / "data").write_bytes(b"input")
    destination = tmp_path / "destination"
    real_stat = os.stat

    def cross_device(path, *args, **kwargs):
        result = real_stat(path, *args, **kwargs)
        if os.path.abspath(os.fspath(path)) == str(source):
            return StatResultProxy(result, st_dev=result.st_dev + 100)
        return result

    monkeypatch.setattr(backend_module.os, "stat", cross_device)
    backend.ingest_tree(str(source), str(destination))
    assert (destination / "data").read_bytes() == b"input"
    assert ops.clone_calls == 0


def test_ingest_file_copies_across_devices_and_reflinks_on_same_device(tmp_path, monkeypatch):
    ops = FakeOps()
    backend = ReflinkBackend(ops)
    source = tmp_path / "source"
    source.write_bytes(b"input-file")
    cross_destination = tmp_path / "cross" / "copy"
    cross_destination.parent.mkdir()
    real_stat = os.stat

    def cross_device(path, *args, **kwargs):
        result = real_stat(path, *args, **kwargs)
        if os.path.abspath(os.fspath(path)) == str(source):
            return StatResultProxy(result, st_dev=result.st_dev + 100)
        return result

    monkeypatch.setattr(backend_module.os, "stat", cross_device)
    backend.ingest_file(str(source), str(cross_destination))
    assert cross_destination.read_bytes() == b"input-file"
    assert ops.clone_calls == 0

    monkeypatch.setattr(backend_module.os, "stat", real_stat)
    same_destination = tmp_path / "same" / "copy"
    same_destination.parent.mkdir()
    backend.ingest_file(str(source), str(same_destination))
    assert same_destination.read_bytes() == b"input-file"
    assert ops.clone_calls == 1


def test_ingest_file_device_inspection_failure_is_typed(monkeypatch):
    backend = ReflinkBackend(FakeOps())

    def fail_stat(*_args, **_kwargs):
        raise PermissionError(errno.EACCES, "stat denied")

    monkeypatch.setattr(backend_module.os, "stat", fail_stat)
    with pytest.raises(BackendFailed) as caught:
        backend.ingest_file("source", "destination")
    assert caught.value.op == "stat-ingest-file"
    assert caught.value.errno == errno.EACCES


def test_ioctl_numbers_and_vol_args_v2_layout(monkeypatch):
    assert FICLONE == 0x40049409
    assert BTRFS_IOC_SUBVOL_CREATE == 0x5000940E
    assert BTRFS_IOC_SNAP_DESTROY == 0x5000940F
    assert BTRFS_IOC_SNAP_CREATE_V2 == 0x50009417
    assert BTRFS_IOC_SUBVOL_GETFLAGS == 0x80089419
    assert BTRFS_IOC_SUBVOL_SETFLAGS == 0x4008941A
    old = _old_vol_args("tree")
    assert len(old) == 4096 and struct.unpack_from("=q", old)[0] == -1
    assert old[8:13] == b"tree\0"
    v2 = _vol_args_v2("snap", 42)
    assert len(v2) == 4096 and struct.unpack_from("=q", v2)[0] == 42
    assert v2[56:61] == b"snap\0"

    calls = []

    def ioctl(fd, request, argument, mutate=False):
        calls.append((fd, request, argument, mutate))
        if request == BTRFS_IOC_SUBVOL_GETFLAGS:
            argument[:] = struct.pack("=Q", BTRFS_SUBVOL_RDONLY)
        return 0

    monkeypatch.setattr(backend_module.fcntl, "ioctl", ioctl)
    ops = LinuxOps()
    ops.subvol_create(11, "tree")
    ops.snapshot_create(12, 42, "snap")
    ops.subvol_destroy(13, "dead")
    assert ops.subvol_getflags(14) == BTRFS_SUBVOL_RDONLY
    ops.subvol_setflags(15, 0)
    ops.ficlone(16, 17)
    assert [call[1] for call in calls] == [
        BTRFS_IOC_SUBVOL_CREATE,
        BTRFS_IOC_SNAP_CREATE_V2,
        BTRFS_IOC_SNAP_DESTROY,
        BTRFS_IOC_SUBVOL_GETFLAGS,
        BTRFS_IOC_SUBVOL_SETFLAGS,
        FICLONE,
    ]
