from __future__ import annotations

import errno
import os
import stat
from pathlib import Path

import cowtree.backends as backend_module
import pytest
from cowtree.backends import (
    BtrfsBackend,
    CopyBackend,
    LinuxOps,
    _cleanup_created,
    _cleanup_empty_created_directory,
    _clone_contents,
    _copy_bytes,
    _copy_tree_contents,
    _delete_path,
    _empty_directory,
    _encoded_name,
    _identity_at,
    _make_created_directory_writable,
    _make_dirs_writable,
    _populate_created_tree,
    _quarantine_created,
    _restore_quarantined,
    _seal_modes,
)
from cowtree.errors import BackendFailed, DestinationExists, PathRefused

from .fakes import FakeOps, StatResultProxy
from .test_backends import _magic_inode


def _identity(path: Path) -> tuple[int, int]:
    info = os.lstat(path)
    return info.st_dev, info.st_ino


def _mark_regular_directory(monkeypatch, path: Path) -> None:
    real_stat = os.stat

    def regular_directory(value, *args, **kwargs):
        result = real_stat(value, *args, **kwargs)
        if isinstance(value, (str, bytes, os.PathLike)) and os.path.abspath(os.fspath(value)) == str(path):
            return StatResultProxy(result, st_ino=257)
        return result

    monkeypatch.setattr(backend_module.os, "stat", regular_directory)


def test_linux_ops_wraps_descriptor_and_path_operations(tmp_path, monkeypatch):
    ops = LinuxOps()
    root_fd = ops.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    ops.mkdir("sub", 0o700, dir_fd=root_fd)
    sub_fd = ops.open("sub", os.O_RDONLY | os.O_DIRECTORY, dir_fd=root_fd)
    file_fd = ops.open("data", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=sub_fd)
    assert ops.write(file_fd, b"payload") == 7
    ops.fchmod(file_fd, 0o640)
    ops.futime(file_fd, (1_600_000_000_000_000_000, 1_600_000_001_000_000_000))
    ops.close(file_fd)
    file_fd = ops.open("data", os.O_RDONLY, dir_fd=sub_fd)
    assert ops.read(file_fd, 20) == b"payload"
    assert ops.fstat(file_fd).st_size == 7
    ops.close(file_fd)
    assert ops.stat("data", dir_fd=sub_fd, follow_symlinks=False).st_size == 7
    assert ops.listdir(sub_fd) == ["data"]
    ops.symlink("data", "link", dir_fd=sub_fd)
    assert ops.readlink("link", dir_fd=sub_fd) == "data"
    ops.utime("link", (1_600_000_002_000_000_000, 1_600_000_003_000_000_000), dir_fd=sub_fd,
              follow_symlinks=False)
    xattrs = []
    monkeypatch.setattr(backend_module.os, "setxattr", lambda *args, **kwargs: xattrs.append((args, kwargs)))
    ops.setxattr("data", "user.test", b"v", follow_symlinks=False)
    assert xattrs == [(("data", "user.test", b"v"), {"follow_symlinks": False})]
    ops.unlink("link", dir_fd=sub_fd)
    ops.unlink("data", dir_fd=sub_fd)
    ops.close(sub_fd)
    ops.rmdir("sub", dir_fd=root_fd)
    ops.close(root_fd)


def test_encoded_subvolume_name_has_kernel_limit():
    assert _encoded_name("a", 2) == b"a\0"
    with pytest.raises(ValueError, match="too long"):
        _encoded_name("ab", 2)


def test_identity_at_rejects_a_non_directory(tmp_path):
    (tmp_path / "file").write_text("x")
    parent_fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        with pytest.raises(PathRefused, match="not a real directory"):
            _identity_at(parent_fd, "file")
    finally:
        os.close(parent_fd)


def test_copy_bytes_retries_partial_and_rejects_zero_write():
    class IO:
        def __init__(self, writes):
            self.reads = [b"abc", b""]
            self.writes = list(writes)

        def read(self, _fd, _size):
            return self.reads.pop(0)

        def write(self, _fd, _data):
            return self.writes.pop(0)

    ops = IO([1, 2])
    _copy_bytes(1, 2, ops)
    with pytest.raises(OSError, match="short write"):
        _copy_bytes(1, 2, IO([0]))


def test_clone_contents_skips_dot_entries_and_tree_copy_checks_destination_identity(tmp_path):
    class DotOps:
        def listdir(self, _fd):
            return [".", ".."]

    _clone_contents(1, 2, DotOps(), dst_path="unused", created_entries=[], clone=False)
    source = tmp_path / "source"
    source.mkdir()
    destination = tmp_path / "destination"
    destination.mkdir()
    with pytest.raises(PathRefused, match="identity changed before tree population"):
        _copy_tree_contents(str(source), str(destination), LinuxOps(), destination_identity=(0, 0),
                            created_entries=[], clone=False)


def test_clone_contents_closes_source_when_destination_create_fails():
    class Ops:
        def __init__(self):
            self.closed = []
            self.opens = 0

        def listdir(self, _fd):
            return ["file"]

        def stat(self, _name, **_kwargs):
            return type("Info", (), {"st_mode": stat.S_IFREG | 0o600})()

        def open(self, _name, *_args, **_kwargs):
            self.opens += 1
            if self.opens == 1:
                return 3
            raise FileExistsError(errno.EEXIST, "destination collision")

        def close(self, fd):
            self.closed.append(fd)

    ops = Ops()
    with pytest.raises(FileExistsError):
        _clone_contents(1, 2, ops, dst_path="tree", created_entries=[], clone=False)
    assert ops.closed == [3]


def test_populate_created_tree_cleans_when_final_identity_changes(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    (source / "file").write_text("owned")
    destination = tmp_path / "destination"
    destination.mkdir()
    identity = _identity(destination)
    real_path_identity = backend_module._path_identity

    def replaced_identity(path):
        if path == str(destination):
            return (-1, -1)
        return real_path_identity(path)

    monkeypatch.setattr(backend_module, "_path_identity", replaced_identity)
    with pytest.raises(PathRefused, match="identity changed during tree population"):
        _populate_created_tree(
            str(source), str(destination), LinuxOps(), identity, clone=False,
            delete=os.rmdir, log=lambda _message: None,
        )
    assert not destination.exists()


def test_cleanup_created_noops_and_preserves_mismatched_identity(tmp_path):
    path = tmp_path / "partial"
    logs = []
    delete_calls = []
    assert _quarantine_created(str(path), None, logs.append) is None
    assert _quarantine_created(str(path), (0, 0), logs.append) is None
    path.mkdir()
    assert _quarantine_created(str(path), (0, 0), logs.append) is None
    assert path.is_dir()
    assert any("preserving replaced partial path" in item for item in logs)
    _cleanup_created(str(path), None, lambda value: delete_calls.append(value), logs.append)
    assert delete_calls == []


def test_quarantine_created_logs_inspection_and_rename_failures(tmp_path, monkeypatch):
    path = tmp_path / "partial"
    path.mkdir()
    identity = _identity(path)
    logs = []
    real_lstat = os.lstat
    real_rename = backend_module._rename_noreplace

    def fail_inspect(value, *args, **kwargs):
        if os.fspath(value) == str(path):
            raise PermissionError(errno.EACCES, "cannot inspect")
        return real_lstat(value, *args, **kwargs)

    monkeypatch.setattr(backend_module.os, "lstat", fail_inspect)
    assert _quarantine_created(str(path), identity, logs.append) is None
    monkeypatch.setattr(backend_module.os, "lstat", real_lstat)

    def fail_rename(_source, _destination):
        raise OSError(errno.EIO, "rename failed")

    monkeypatch.setattr(backend_module, "_rename_noreplace", fail_rename)
    assert _quarantine_created(str(path), identity, logs.append) is None
    assert any("cannot isolate partial path" in item for item in logs)
    monkeypatch.setattr(backend_module, "_rename_noreplace", real_rename)


def test_quarantine_created_restores_when_isolated_path_changes(tmp_path, monkeypatch):
    path = tmp_path / "partial"
    path.mkdir()
    identity = _identity(path)
    logs = []
    real_rename = backend_module._rename_noreplace
    real_lstat = os.lstat

    def move_then_report_replacement(source, destination):
        real_rename(source, destination)

    monkeypatch.setattr(backend_module, "_rename_noreplace", move_then_report_replacement)
    original_quarantine = None

    def changed_lstat(value, *args, **kwargs):
        nonlocal original_quarantine
        if isinstance(value, (str, bytes, os.PathLike)) and os.fspath(value).startswith(str(tmp_path / ".cowtree-cleanup-")):
            original_quarantine = os.fspath(value)
            result = real_lstat(value, *args, **kwargs)
            return StatResultProxy(result, st_ino=result.st_ino + 1)
        return real_lstat(value, *args, **kwargs)

    monkeypatch.setattr(backend_module.os, "lstat", changed_lstat)
    assert _quarantine_created(str(path), identity, logs.append) is None
    assert original_quarantine is not None and path.is_dir()
    assert any("preserving replaced partial path" in item for item in logs)


def test_quarantine_created_restores_when_isolated_path_cannot_be_inspected(tmp_path, monkeypatch):
    path = tmp_path / "partial"
    path.mkdir()
    identity = _identity(path)
    logs = []
    real_lstat = os.lstat

    def fail_quarantine_stat(value, *args, **kwargs):
        if isinstance(value, (str, bytes, os.PathLike)) and ".cowtree-cleanup-" in os.fspath(value):
            raise PermissionError(errno.EACCES, "quarantine stat denied")
        return real_lstat(value, *args, **kwargs)

    monkeypatch.setattr(backend_module.os, "lstat", fail_quarantine_stat)
    assert _quarantine_created(str(path), identity, logs.append) is None
    assert path.is_dir()
    assert any("cannot inspect isolated path" in item for item in logs)


def test_cleanup_empty_directory_preserves_unowned_entries_added_during_isolation(tmp_path, monkeypatch):
    path = tmp_path / "partial"
    path.mkdir()
    identity = _identity(path)
    logs = []
    real_rename = backend_module._rename_noreplace

    def move_and_add_foreign_child(source, destination):
        real_rename(source, destination)
        (Path(destination) / "foreign").write_text("keep")

    monkeypatch.setattr(backend_module, "_rename_noreplace", move_and_add_foreign_child)
    _cleanup_empty_created_directory(str(path), identity, os.rmdir, logs.append)
    assert (path / "foreign").read_text() == "keep"
    assert any("unowned entries" in item for item in logs)


def test_cleanup_empty_directory_preserves_existing_entries_and_missing_path(tmp_path):
    path = tmp_path / "partial"
    path.mkdir()
    (path / "foreign").write_text("preserve")
    logs = []
    _cleanup_empty_created_directory(str(path), _identity(path), os.rmdir, logs.append)
    assert (path / "foreign").read_text() == "preserve"
    _cleanup_empty_created_directory(str(tmp_path / "missing"), (0, 0), os.rmdir, logs.append)
    assert any("unowned entries" in item for item in logs)


def test_make_created_directory_writable_handles_missing_replaced_and_oserror(tmp_path, monkeypatch):
    path = tmp_path / "directory"
    path.mkdir()
    identity = _identity(path)
    logs = []
    _make_created_directory_writable(str(path), (0, 0), logs.append)
    assert any("preserving replaced partial directory" in item for item in logs)
    _make_created_directory_writable(str(tmp_path / "missing"), identity, logs.append)

    real_open = os.open

    def fail_open(value, *args, **kwargs):
        if value == str(path):
            raise PermissionError(errno.EACCES, "cannot open")
        return real_open(value, *args, **kwargs)

    monkeypatch.setattr(backend_module.os, "open", fail_open)
    _make_created_directory_writable(str(path), identity, logs.append)
    assert any("cannot make partial directory" in item for item in logs)


def test_cleanup_created_logs_failure_and_restores_path(tmp_path):
    path = tmp_path / "partial"
    path.mkdir()
    identity = _identity(path)
    logs = []

    def fail_delete(_path):
        raise OSError(errno.EIO, "delete failed")

    _cleanup_created(str(path), identity, fail_delete, logs.append)
    assert path.is_dir()
    assert any("cleanup of partial path" in item for item in logs)


def test_restore_quarantined_preserves_preexisting_destination(tmp_path):
    quarantine = tmp_path / "quarantine"
    quarantine.write_text("cowtree")
    destination = tmp_path / "destination"
    destination.write_text("other writer")
    logs = []
    _restore_quarantined(str(quarantine), str(destination), logs.append)
    assert quarantine.read_text() == "cowtree"
    assert destination.read_text() == "other writer"
    assert any("was recreated" in item for item in logs)


def test_btrfs_empty_created_tree_uses_subvolume_destroy_and_eperm_rmdir(tmp_path, monkeypatch):
    path = tmp_path / "tree"
    path.mkdir()
    real_lstat = os.lstat

    def magic_lstat(value, *args, **kwargs):
        result = real_lstat(value, *args, **kwargs)
        if os.fspath(value) == str(path):
            return StatResultProxy(result, st_ino=256)
        return result

    monkeypatch.setattr(backend_module.os, "lstat", magic_lstat)
    ops = FakeOps(destroy_error=errno.EPERM)
    BtrfsBackend(ops)._delete_empty_created_tree(str(path))
    assert not path.exists()
    assert ops.destroy_calls == ["tree"]


def test_btrfs_make_tree_wraps_creation_and_compression_failures(tmp_path):
    path = tmp_path / "tree"
    with pytest.raises(BackendFailed, match="open-parent"):
        BtrfsBackend(FakeOps()).make_tree(str(path / "child"))

    ops = FakeOps(subvol_error=errno.EIO)
    with pytest.raises(BackendFailed, match="subvol-create"):
        BtrfsBackend(ops).make_tree(str(path))

    class ExistingOps(FakeOps):
        def subvol_create(self, _parent_fd, _name):
            raise FileExistsError(errno.EEXIST, "already there")

    with pytest.raises(DestinationExists):
        BtrfsBackend(ExistingOps()).make_tree(str(path))

    ops = FakeOps()
    backend = BtrfsBackend(ops)
    original_xattr = ops.setxattr

    def fail_xattr(*_args, **_kwargs):
        raise OSError(errno.EIO, "xattr denied")

    ops.setxattr = fail_xattr
    with pytest.raises(BackendFailed, match="set-compression"):
        backend.make_tree(str(path), compression="zstd")
    assert not path.exists()
    ops.setxattr = original_xattr


def test_btrfs_make_tree_refuses_changed_identity_and_logs_close_error(tmp_path):
    logs = []
    ops = FakeOps()
    backend = BtrfsBackend(ops, log=logs.append)
    original_fstat = ops.fstat
    original_close = ops.close
    leaked_fds = []

    def wrong_tree_identity(fd):
        result = original_fstat(fd)
        if ops._fd_path(fd) == str(tmp_path / "tree"):
            return StatResultProxy(result, st_ino=result.st_ino + 1)
        return result

    def fail_tree_close(fd):
        if ops._fd_path(fd) == str(tmp_path / "tree"):
            leaked_fds.append(fd)
            raise OSError(errno.EIO, "close failed")
        original_close(fd)

    ops.fstat = wrong_tree_identity
    ops.close = fail_tree_close
    with pytest.raises(PathRefused, match="identity changed"):
        backend.make_tree(str(tmp_path / "tree"))
    assert any("close after subvolume creation failure" in item for item in logs)
    original_close(leaked_fds[0])


@pytest.mark.parametrize("failure_kind", ["already-open", "fchmod", "fchmod-exists"])
def test_btrfs_make_tree_cleans_known_identity_after_open_or_mode_failure(tmp_path, failure_kind):
    path = tmp_path / "tree"
    ops = FakeOps()
    backend = BtrfsBackend(ops)
    original_open = ops.open
    original_fchmod = ops.fchmod

    if failure_kind == "already-open":
        def fail_open(name, *args, **kwargs):
            if name == "tree":
                raise FileExistsError(errno.EEXIST, "raced tree open")
            return original_open(name, *args, **kwargs)

        ops.open = fail_open
        expected = DestinationExists
    elif failure_kind == "fchmod":
        def fail_chmod(_fd, _mode):
            raise OSError(errno.EIO, "mode update failed")

        ops.fchmod = fail_chmod
        expected = BackendFailed
    else:
        def race_chmod(_fd, _mode):
            raise FileExistsError(errno.EEXIST, "destination raced")

        ops.fchmod = race_chmod
        expected = DestinationExists

    with pytest.raises(expected):
        backend.make_tree(str(path))
    assert not path.exists()
    ops.open = original_open
    ops.fchmod = original_fchmod


def test_btrfs_make_tree_refuses_path_identity_change_after_creation(tmp_path, monkeypatch):
    path = tmp_path / "tree"
    backend = BtrfsBackend(FakeOps())
    real_identity = backend_module._path_identity

    def changed_identity(value):
        if value == str(path):
            return (0, 0)
        return real_identity(value)

    monkeypatch.setattr(backend_module, "_path_identity", changed_identity)
    with pytest.raises(PathRefused, match="identity changed during subvolume creation"):
        backend.make_tree(str(path))
    assert path.is_dir()


def test_reflink_tree_creation_compression_is_reported_as_ignored(tmp_path):
    logs = []
    backend = backend_module.ReflinkBackend(FakeOps(), log=logs.append)
    tree = backend.make_tree(str(tmp_path / "tree"), compression="zstd")
    assert Path(tree).is_dir()
    assert logs == ["cowtree: reflink backend ignores per-tree compression 'zstd'"]


@pytest.mark.parametrize("failure_kind", ["identity", "chmod"])
def test_copy_backend_make_tree_cleans_after_identity_or_chmod_failure(tmp_path, failure_kind):
    path = tmp_path / "tree"
    ops = FakeOps()
    backend = CopyBackend(ops)
    original_fstat = ops.fstat
    original_fchmod = ops.fchmod

    if failure_kind == "identity":
        def changed_fstat(fd):
            result = original_fstat(fd)
            if ops._fd_path(fd) == str(path):
                return StatResultProxy(result, st_ino=result.st_ino + 1)
            return result

        ops.fstat = changed_fstat
        expected = PathRefused
    else:
        def fail_fchmod(_fd, _mode):
            raise OSError(errno.EIO, "chmod failed")

        ops.fchmod = fail_fchmod
        expected = BackendFailed

    with pytest.raises(expected):
        backend.make_tree(str(path))
    assert not path.exists()
    ops.fstat = original_fstat
    ops.fchmod = original_fchmod


def test_copy_backend_refuses_post_creation_path_identity_change(tmp_path, monkeypatch):
    path = tmp_path / "tree"
    backend = CopyBackend(FakeOps())
    real_identity = backend_module._path_identity

    def changed_identity(value):
        if value == str(path):
            return (0, 0)
        return real_identity(value)

    monkeypatch.setattr(backend_module, "_path_identity", changed_identity)
    with pytest.raises(PathRefused, match="identity changed during tree creation"):
        backend.make_tree(str(path))
    assert path.is_dir()


def test_copy_and_reflink_backends_wrap_source_and_parent_errors(tmp_path, monkeypatch):
    missing = tmp_path / "missing-source"
    with pytest.raises(BackendFailed, match="open-parent"):
        CopyBackend(FakeOps()).make_tree(str(tmp_path / "absent-parent" / "tree"))
    with pytest.raises(BackendFailed, match="ingest-tree"):
        CopyBackend(FakeOps()).ingest_tree(str(missing), str(tmp_path / "ingest"))
    with pytest.raises(BackendFailed, match="clone-tree"):
        CopyBackend(FakeOps()).clone_tree(str(missing), str(tmp_path / "clone"))
    with pytest.raises(BackendFailed, match="copy-file"):
        CopyBackend(FakeOps()).ingest_file(str(missing), str(tmp_path / "file"))

    real_stat = backend_module.os.stat

    def fail_stat(path, *args, **kwargs):
        if path == str(missing):
            raise OSError(errno.EACCES, "stat denied")
        return real_stat(path, *args, **kwargs)

    monkeypatch.setattr(backend_module.os, "stat", fail_stat)
    with pytest.raises(BackendFailed, match="ingest-tree"):
        backend_module.ReflinkBackend(FakeOps()).ingest_tree(str(missing), str(tmp_path / "reflink-ingest"))


def test_btrfs_seal_wraps_open_and_ioctl_failures(tmp_path):
    path = tmp_path / "tree"
    path.mkdir()
    ops = FakeOps()
    backend = BtrfsBackend(ops)
    original_open = ops.open

    def fail_open(value, *args, **kwargs):
        if value == str(path):
            raise OSError(errno.EACCES, "open denied")
        return original_open(value, *args, **kwargs)

    ops.open = fail_open
    with pytest.raises(BackendFailed, match="open-subvol"):
        backend.seal(str(path))
    ops.open = original_open

    def fail_getflags(_fd):
        raise OSError(errno.EIO, "getflags denied")

    ops.subvol_getflags = fail_getflags
    with pytest.raises(BackendFailed, match="subvol-seal"):
        backend.seal(str(path))
    ops.subvol_getflags = lambda _fd: 0

    def fail_setflags(_fd, _flags):
        raise OSError(errno.EIO, "setflags denied")

    ops.subvol_setflags = fail_setflags
    with pytest.raises(BackendFailed, match="subvol-seal"):
        backend.seal(str(path))


def test_btrfs_clone_tree_refuses_existing_and_wraps_source_stat(tmp_path):
    backend = BtrfsBackend(FakeOps())
    source = tmp_path / "source"
    source.mkdir()
    destination = tmp_path / "destination"
    destination.mkdir()
    with pytest.raises(DestinationExists):
        backend.clone_tree(str(source), str(destination))

    destination.rmdir()
    original_stat = backend_module.os.stat

    def fail_source(path, *args, **kwargs):
        if path == str(source):
            raise OSError(errno.EIO, "source stat failed")
        return original_stat(path, *args, **kwargs)

    backend_module.os.stat = fail_source
    try:
        with pytest.raises(BackendFailed, match="stat-source"):
            backend.clone_tree(str(source), str(destination))
    finally:
        backend_module.os.stat = original_stat


@pytest.mark.parametrize("failure_stage", ["parent-open", "source-open", "snapshot-exists", "snapshot-error"])
def test_btrfs_snapshot_creation_failures_release_fds(tmp_path, monkeypatch, failure_stage):
    source = tmp_path / "source"
    source.mkdir()
    destination = tmp_path / "snapshot"
    _magic_inode(monkeypatch, source)
    ops = FakeOps()
    backend = BtrfsBackend(ops)
    original_open = ops.open
    original_close = ops.close
    closed = []

    def instrument_close(fd):
        closed.append(ops._fd_path(fd))
        original_close(fd)

    ops.close = instrument_close
    if failure_stage == "parent-open":
        def fail_parent(path, *args, **kwargs):
            if path == str(tmp_path):
                raise OSError(errno.EIO, "parent open failed")
            return original_open(path, *args, **kwargs)

        ops.open = fail_parent
    elif failure_stage == "source-open":
        def fail_source(path, *args, **kwargs):
            if path == str(source):
                raise OSError(errno.EIO, "source open failed")
            return original_open(path, *args, **kwargs)

        ops.open = fail_source
    elif failure_stage in {"snapshot-exists", "snapshot-error"}:
        def fail_snapshot(_parent_fd, _source_fd, _name):
            if failure_stage == "snapshot-exists":
                raise FileExistsError(errno.EEXIST, "snapshot appeared")
            raise OSError(errno.EIO, "snapshot failed")

        ops.snapshot_create = fail_snapshot

    if failure_stage == "snapshot-exists":
        with pytest.raises(DestinationExists):
            backend.clone_tree(str(source), str(destination))
    else:
        with pytest.raises(BackendFailed, match="open-snapshot-source" if failure_stage in {"parent-open", "source-open"} else "snapshot-create"):
            backend.clone_tree(str(source), str(destination))
    assert not destination.exists()
    assert len(closed) == (1 if failure_stage == "source-open" else 0 if failure_stage == "parent-open" else 2)


def test_btrfs_snapshot_destination_identity_failure_is_cleaned(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    destination = tmp_path / "snapshot"
    _magic_inode(monkeypatch, source)
    ops = FakeOps()
    backend = BtrfsBackend(ops)
    original_fstat = ops.fstat

    def changed_destination(fd):
        result = original_fstat(fd)
        if ops._fd_path(fd) == str(destination):
            return StatResultProxy(result, st_ino=result.st_ino + 1)
        return result

    ops.fstat = changed_destination
    with pytest.raises(PathRefused, match="identity changed"):
        backend.clone_tree(str(source), str(destination))
    assert not destination.exists()


def test_btrfs_snapshot_destination_open_failure_cleans_created_snapshot(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    destination = tmp_path / "snapshot"
    _magic_inode(monkeypatch, source)
    ops = FakeOps()
    original_open = ops.open

    def fail_destination(path, *args, **kwargs):
        if path == str(destination):
            raise OSError(errno.EIO, "destination open failed")
        return original_open(path, *args, **kwargs)

    ops.open = fail_destination
    with pytest.raises(BackendFailed, match="snapshot-writable"):
        BtrfsBackend(ops).clone_tree(str(source), str(destination))
    assert not destination.exists()


@pytest.mark.parametrize("failure", [BackendFailed("copy", 5, "dst"), OSError(errno.EIO, "copy failed")])
def test_btrfs_walk_clone_cleans_typed_and_os_failures(tmp_path, monkeypatch, failure):
    source = tmp_path / "regular-source"
    source.mkdir()
    (source / "file").write_text("content")
    _mark_regular_directory(monkeypatch, source)
    destination = tmp_path / "copy"
    ops = FakeOps()
    backend = BtrfsBackend(ops)

    def fail_population(*_args, **_kwargs):
        raise failure

    monkeypatch.setattr(backend_module, "_populate_created_tree", fail_population)
    with pytest.raises((BackendFailed, OSError)):
        backend.clone_tree(str(source), str(destination))
    assert not destination.exists()


def test_btrfs_clone_walk_requires_created_identity(tmp_path, monkeypatch):
    source = tmp_path / "regular-source"
    source.mkdir()
    _mark_regular_directory(monkeypatch, source)
    backend = BtrfsBackend(FakeOps())
    monkeypatch.setattr(backend, "make_tree", lambda _path, compression=None: _path)
    with pytest.raises(PathRefused, match="did not retain"):
        backend.clone_tree(str(source), str(tmp_path / "destination"))


def test_btrfs_ingest_tree_falls_back_across_devices_and_requires_identity(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    (source / "file").write_text("input")
    destination = tmp_path / "ingested"
    ops = FakeOps()
    backend = BtrfsBackend(ops)
    real_stat = os.stat

    def cross_device(path, *args, **kwargs):
        result = real_stat(path, *args, **kwargs)
        if os.path.abspath(os.fspath(path)) == str(source):
            return StatResultProxy(result, st_dev=result.st_dev + 10)
        return result

    monkeypatch.setattr(backend_module.os, "stat", cross_device)
    backend.ingest_tree(str(source), str(destination))
    assert (destination / "file").read_text() == "input"
    assert ops.clone_calls == 0
    backend.delete_tree(str(destination))

    backend = BtrfsBackend(FakeOps())
    monkeypatch.setattr(backend, "make_tree", lambda _path, compression=None: _path)
    with pytest.raises(PathRefused, match="did not retain"):
        backend.ingest_tree(str(source), str(tmp_path / "missing-identity"))


def test_btrfs_delete_reports_inspection_and_destroy_errors(tmp_path, monkeypatch):
    backend = BtrfsBackend(FakeOps())
    path = tmp_path / "tree"
    path.mkdir()
    real_stat = backend_module.os.stat

    def fail_stat(value, *args, **kwargs):
        if value == str(path):
            raise OSError(errno.EIO, "stat denied")
        return real_stat(value, *args, **kwargs)

    monkeypatch.setattr(backend_module.os, "stat", fail_stat)
    with pytest.raises(BackendFailed, match="stat-delete-target"):
        backend.delete_tree(str(path))
    monkeypatch.setattr(backend_module.os, "stat", real_stat)

    ops = FakeOps(destroy_error=errno.EIO)
    backend = BtrfsBackend(ops)
    from .test_backends import _magic_inode

    _magic_inode(monkeypatch, path)
    with pytest.raises(BackendFailed, match="subvol-delete"):
        backend.delete_tree(str(path))


@pytest.mark.parametrize("failure_stage", ["parent-open", "tree-open", "getflags"])
def test_btrfs_delete_closes_only_descriptors_that_were_opened(tmp_path, monkeypatch, failure_stage):
    path = tmp_path / "tree"
    path.mkdir()
    _magic_inode(monkeypatch, path)
    ops = FakeOps()
    backend = BtrfsBackend(ops)
    original_open = ops.open
    original_close = ops.close
    closed = []

    def track_close(fd):
        closed.append(ops._fd_path(fd))
        original_close(fd)

    ops.close = track_close
    if failure_stage == "parent-open":
        def fail_parent(value, *args, **kwargs):
            if value == str(tmp_path):
                raise OSError(errno.EIO, "parent open failed")
            return original_open(value, *args, **kwargs)

        ops.open = fail_parent
    elif failure_stage == "tree-open":
        def fail_tree(value, *args, **kwargs):
            if value == str(path):
                raise OSError(errno.EIO, "tree open failed")
            return original_open(value, *args, **kwargs)

        ops.open = fail_tree
    else:
        ops.subvol_getflags = lambda _fd: (_ for _ in ()).throw(OSError(errno.EIO, "getflags failed"))

    with pytest.raises(BackendFailed, match="subvol-delete"):
        backend.delete_tree(str(path))
    assert len(closed) == {"parent-open": 0, "tree-open": 1, "getflags": 2}[failure_stage]


def test_copy_backend_seal_and_created_identity_cleanup(tmp_path):
    backend = CopyBackend(FakeOps())
    tree = tmp_path / "tree"
    backend.make_tree(str(tree))
    assert backend.created_identity(str(tree)) == _identity(tree)
    backend.cleanup_created(str(tree))
    assert not tree.exists()

    tree.mkdir()
    backend._mark_sealed(str(tree))
    assert backend._is_sealed(str(tree))
    moved = tmp_path / "old-tree"
    tree.rename(moved)
    tree.mkdir()
    assert not backend._is_sealed(str(tree))
    assert str(tree) not in backend._sealed_trees
    missing_sealed = tmp_path / "missing-sealed"
    missing_sealed.mkdir()
    backend._mark_sealed(str(missing_sealed))
    missing_sealed.rmdir()
    assert not backend._is_sealed(str(missing_sealed))
    single_file = tmp_path / "single-file"
    single_file.write_text("immutable")
    with pytest.raises(PathRefused, match="real directory"):
        backend.seal(str(single_file))
    assert stat.S_IMODE(single_file.stat().st_mode) & 0o222 == 0


def test_cleanup_created_handles_files_symlinks_missing_paths_and_unknown_identity(tmp_path):
    backend = CopyBackend(FakeOps())
    regular = tmp_path / "regular"
    regular.write_text("owned")
    backend._mark_created(str(regular), _identity(regular))
    backend.cleanup_created(str(regular))
    assert not regular.exists()

    target = tmp_path / "target"
    target.write_text("outside")
    link = tmp_path / "link"
    link.symlink_to(target)
    backend._mark_created(str(link), _identity(link))
    backend.cleanup_created(str(link))
    assert not link.exists() and target.read_text() == "outside"

    directory = tmp_path / "directory"
    directory.mkdir()
    backend._mark_created(str(directory), _identity(directory))
    backend.cleanup_created(str(directory))
    assert not directory.exists()

    missing = tmp_path / "missing"
    backend._mark_created(str(missing), (1, 2))
    backend.cleanup_created(str(missing))
    backend.cleanup_created(str(tmp_path / "unknown"))


def test_file_copy_refuses_nonregular_source_and_existing_destination(tmp_path):
    backend = CopyBackend(FakeOps())
    directory = tmp_path / "directory"
    directory.mkdir()
    with pytest.raises(PathRefused, match="not a regular file"):
        backend.clone_file(str(directory), str(tmp_path / "copy"))
    source = tmp_path / "source"
    source.write_text("input")
    destination = tmp_path / "destination"
    destination.write_text("foreign")
    with pytest.raises(DestinationExists):
        backend.clone_file(str(source), str(destination))
    assert destination.read_text() == "foreign"


def test_seal_modes_handles_symlink_file_and_empty_directory(tmp_path):
    backend = CopyBackend(FakeOps())
    target = tmp_path / "target"
    target.write_text("data")
    link = tmp_path / "link"
    link.symlink_to(target)
    _seal_modes(str(link))
    assert link.is_symlink()
    tree = tmp_path / "tree"
    tree.mkdir()
    tree_link = tree / "linked-directory"
    tree_link.symlink_to(tmp_path, target_is_directory=True)
    _seal_modes(str(tree))
    assert stat.S_IMODE(tmp_path.stat().st_mode) & 0o222
    with pytest.raises(PathRefused, match="real directory"):
        backend.seal(str(target))
    assert stat.S_IMODE(target.stat().st_mode) & 0o222 == 0
    empty = tmp_path / "empty"
    empty.mkdir()
    backend.delete_tree(str(empty))
    assert not empty.exists()
    empty.mkdir()
    _empty_directory(str(empty))


def test_directory_cleanup_helpers_handle_missing_leaf_and_nested_symlink(tmp_path):
    absent = tmp_path / "absent"
    _delete_path(str(absent))

    root = tmp_path / "tree"
    nested = root / "nested"
    nested.mkdir(parents=True)
    (nested / "file").write_text("remove")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep").write_text("preserve")
    (root / "link").symlink_to(outside, target_is_directory=True)
    _make_dirs_writable(str(root))
    _empty_directory(str(root))
    assert list(root.iterdir()) == []
    assert (outside / "keep").read_text() == "preserve"
    _make_dirs_writable(str(tmp_path / "missing"))
    _make_dirs_writable(str(outside / "keep"))
    symlink = tmp_path / "directory-link"
    symlink.symlink_to(outside, target_is_directory=True)
    _make_dirs_writable(str(symlink))
    _delete_path(str(symlink))
    assert outside.is_dir()

