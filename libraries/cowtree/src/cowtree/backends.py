"""Linux filesystem backends; btrfs requests use raw ioctls, not btrfs-progs."""
from __future__ import annotations

import ctypes
import errno
import fcntl
import logging
import os
import secrets
import shutil
import stat
import struct
from collections.abc import Callable
from typing import Any, Protocol

from .errors import BackendFailed, DestinationExists, PathRefused

FICLONE = 0x40049409
BTRFS_IOC_SUBVOL_CREATE = 0x5000940E
BTRFS_IOC_SNAP_DESTROY = 0x5000940F
BTRFS_IOC_SNAP_CREATE_V2 = 0x50009417
BTRFS_IOC_SUBVOL_GETFLAGS = 0x80089419
BTRFS_IOC_SUBVOL_SETFLAGS = 0x4008941A
BTRFS_IOC_FS_INFO = 0x8400941F
BTRFS_SUBVOL_RDONLY = 1 << 1
_FS_INFO_ARGS_BYTES = 1024
_FS_INFO_FSID_OFFSET = 16
_FSID_BYTES = 16
_VOL_NAME_BYTES = 4088
_VOL_V2_BYTES = 4096
_VOL_V2_NAME_OFFSET = 56
_VOL_V2_NAME_BYTES = 4040
_O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_O_DIRECTORY = getattr(os, "O_DIRECTORY", 0)


class Ops(Protocol):
    """Small OS boundary used by backends and replaceable by tests."""

    open: Callable[..., Any]
    close: Callable[..., Any]
    fstat: Callable[..., Any]
    stat: Callable[..., Any]
    listdir: Callable[..., Any]
    mkdir: Callable[..., Any]
    unlink: Callable[..., Any]
    rmdir: Callable[..., Any]
    readlink: Callable[..., Any]
    symlink: Callable[..., Any]
    fchmod: Callable[..., Any]
    futime: Callable[..., Any]
    utime: Callable[..., Any]
    read: Callable[..., Any]
    write: Callable[..., Any]
    setxattr: Callable[..., Any]
    ficlone: Callable[..., Any]
    subvol_create: Callable[..., Any]
    snapshot_create: Callable[..., Any]
    subvol_destroy: Callable[..., Any]
    subvol_getflags: Callable[..., Any]
    subvol_setflags: Callable[..., Any]
    btrfs_fs_id: Callable[..., Any]


class LinuxOps:
    """Direct wrappers around the Linux/POSIX operations cowtree needs."""

    def open(self, path, flags, mode=0o777, *, dir_fd=None):
        return os.open(path, flags, mode, dir_fd=dir_fd)

    def close(self, fd):
        os.close(fd)

    def fstat(self, fd):
        return os.fstat(fd)

    def stat(self, path, *, dir_fd=None, follow_symlinks=True):
        return os.stat(path, dir_fd=dir_fd, follow_symlinks=follow_symlinks)

    def listdir(self, path_or_fd):
        return os.listdir(path_or_fd)

    def mkdir(self, path, mode=0o777, *, dir_fd=None):
        os.mkdir(path, mode, dir_fd=dir_fd)

    def unlink(self, path, *, dir_fd=None):
        os.unlink(path, dir_fd=dir_fd)

    def rmdir(self, path, *, dir_fd=None):
        os.rmdir(path, dir_fd=dir_fd)

    def readlink(self, path, *, dir_fd=None):
        return os.readlink(path, dir_fd=dir_fd)

    def symlink(self, target, path, *, dir_fd=None):
        os.symlink(target, path, dir_fd=dir_fd)

    def fchmod(self, fd, mode):
        os.fchmod(fd, mode)

    def futime(self, fd, times_ns):
        os.utime(fd, ns=times_ns)

    def utime(self, path, times_ns, *, dir_fd=None, follow_symlinks=True):
        os.utime(path, ns=times_ns, dir_fd=dir_fd, follow_symlinks=follow_symlinks)

    def read(self, fd, size):
        return os.read(fd, size)

    def write(self, fd, data):
        return os.write(fd, data)

    def setxattr(self, path, name, value, *, follow_symlinks=False):
        os.setxattr(path, name, value, follow_symlinks=follow_symlinks)

    def ficlone(self, dst_fd, src_fd):
        fcntl.ioctl(dst_fd, FICLONE, src_fd)

    def subvol_create(self, parent_fd, name):
        fcntl.ioctl(parent_fd, BTRFS_IOC_SUBVOL_CREATE, _old_vol_args(name))

    def snapshot_create(self, parent_fd, src_fd, name):
        fcntl.ioctl(parent_fd, BTRFS_IOC_SNAP_CREATE_V2, _vol_args_v2(name, src_fd))

    def subvol_destroy(self, parent_fd, name):
        fcntl.ioctl(parent_fd, BTRFS_IOC_SNAP_DESTROY, _old_vol_args(name))

    def subvol_getflags(self, fd):
        result = bytearray(8)
        fcntl.ioctl(fd, BTRFS_IOC_SUBVOL_GETFLAGS, result, True)
        return struct.unpack("=Q", result)[0]

    def subvol_setflags(self, fd, flags):
        fcntl.ioctl(fd, BTRFS_IOC_SUBVOL_SETFLAGS, bytearray(struct.pack("=Q", flags)))

    def btrfs_fs_id(self, fd):
        result = bytearray(_FS_INFO_ARGS_BYTES)
        fcntl.ioctl(fd, BTRFS_IOC_FS_INFO, result, True)
        start = _FS_INFO_FSID_OFFSET
        return bytes(result[start:start + _FSID_BYTES])


def _encoded_name(name: str, max_bytes: int) -> bytes:
    encoded = os.fsencode(name) + b"\0"
    if len(encoded) > max_bytes:
        raise ValueError("btrfs subvolume name is too long")
    return encoded


def _old_vol_args(name: str) -> bytearray:
    encoded = _encoded_name(name, _VOL_NAME_BYTES)
    value = bytearray(4096)
    struct.pack_into("=q", value, 0, -1)
    value[8:8 + len(encoded)] = encoded
    return value


def _vol_args_v2(name: str, src_fd: int) -> bytearray:
    encoded = _encoded_name(name, _VOL_V2_NAME_BYTES)
    value = bytearray(_VOL_V2_BYTES)
    struct.pack_into("=qQQ", value, 0, src_fd, 0, 0)
    value[_VOL_V2_NAME_OFFSET:_VOL_V2_NAME_OFFSET + len(encoded)] = encoded
    return value


def _raise_backend(op: str, path: str, exc: OSError) -> BackendFailed:
    return BackendFailed(op, exc.errno, path, str(exc))


def _file_identity(info) -> tuple[int, int]:
    return info.st_dev, info.st_ino


def _path_identity(path: str) -> tuple[int, int]:
    return _file_identity(os.lstat(path))


def _identity_at(parent_fd: int, name: str) -> tuple[int, int]:
    try:
        info = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except OSError as stat_error:
        try:
            descriptor = os.open(name, os.O_RDONLY | _O_DIRECTORY | _O_NOFOLLOW, dir_fd=parent_fd)
        except OSError:
            raise stat_error
        try:
            info = os.fstat(descriptor)
        finally:
            os.close(descriptor)
    if not stat.S_ISDIR(info.st_mode):
        raise PathRefused(f"created tree is not a real directory: {name!r}")
    return _file_identity(info)


def _rename_noreplace(source: str, destination: str) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    try:
        renameat2 = libc.renameat2
    except AttributeError as exc:
        raise OSError(errno.ENOSYS, "renameat2 is not available") from exc
    renameat2.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    renameat2.restype = ctypes.c_int
    result = renameat2(-100, os.fsencode(source), -100, os.fsencode(destination), 1)
    if result != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), destination)


def _restore_quarantined(quarantine: str, path: str, log: Callable[[str], None]) -> None:
    if os.path.lexists(path):
        log(f"cowtree: preserving displaced path at {quarantine!r}; {path!r} was recreated")
        return
    try:
        _rename_noreplace(quarantine, path)
    except OSError as exc:
        log(f"cowtree: cannot restore preserved path {quarantine!r} to {path!r}: {exc}")


def _quarantine_created(path: str, identity, log: Callable[[str], None]) -> str | None:
    if identity is None:
        return None
    try:
        current = os.lstat(path)
    except FileNotFoundError:
        return None
    except OSError as exc:
        log(f"cowtree: cannot verify partial path {path!r} for cleanup: {exc}")
        return None
    if _file_identity(current) != identity:
        log(f"cowtree: preserving replaced partial path {path!r}")
        return None
    parent = os.path.dirname(path) or "."
    quarantine = os.path.join(parent, f".cowtree-cleanup-{secrets.token_hex(16)}")
    try:
        _rename_noreplace(path, quarantine)
    except FileNotFoundError:
        return None
    except OSError as exc:
        log(f"cowtree: cannot isolate partial path {path!r} for cleanup: {exc}")
        return None
    try:
        moved = os.lstat(quarantine)
    except OSError as exc:
        log(f"cowtree: cannot inspect isolated path {quarantine!r}: {exc}")
        _restore_quarantined(quarantine, path, log)
        return None
    if _file_identity(moved) != identity:
        log(f"cowtree: preserving replaced partial path {path!r}")
        _restore_quarantined(quarantine, path, log)
        return None
    return quarantine


def _cleanup_created(path: str, identity, delete, log: Callable[[str], None]) -> None:
    quarantine = _quarantine_created(path, identity, log)
    if quarantine is None:
        return
    try:
        delete(quarantine)
    except Exception as cleanup_error:  # noqa: BLE001 - preserve the primary operation failure.
        log(f"cowtree: cleanup of partial path {path!r} also failed: {cleanup_error}")
        _restore_quarantined(quarantine, path, log)


def _cleanup_empty_created_directory(
    path: str,
    identity: tuple[int, int],
    delete,
    log: Callable[[str], None],
) -> None:
    try:
        with os.scandir(path) as entries:
            if next(entries, None) is not None:
                log(f"cowtree: preserving partial directory {path!r} with unowned entries")
                return
    except FileNotFoundError:
        return
    except OSError as exc:
        log(f"cowtree: cannot inspect partial directory {path!r}: {exc}")
        return
    quarantine = _quarantine_created(path, identity, log)
    if quarantine is None:
        return
    try:
        with os.scandir(quarantine) as entries:
            if next(entries, None) is not None:
                log(f"cowtree: preserving partial directory {path!r} with unowned entries")
                _restore_quarantined(quarantine, path, log)
                return
        delete(quarantine)
    except Exception as cleanup_error:  # noqa: BLE001 - preserve the primary operation failure.
        log(f"cowtree: cleanup of partial path {path!r} also failed: {cleanup_error}")
        _restore_quarantined(quarantine, path, log)


def _make_created_directory_writable(
    path: str,
    identity: tuple[int, int],
    log: Callable[[str], None],
) -> None:
    descriptor = -1
    try:
        descriptor = os.open(path, os.O_RDONLY | _O_DIRECTORY | _O_NOFOLLOW)
        info = os.fstat(descriptor)
        if _file_identity(info) != identity:
            log(f"cowtree: preserving replaced partial directory {path!r}")
            return
        os.fchmod(descriptor, stat.S_IMODE(info.st_mode) | 0o700)
    except FileNotFoundError:
        return
    except OSError as exc:
        log(f"cowtree: cannot make partial directory {path!r} writable for cleanup: {exc}")
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _open_dir(path: str, ops: Ops) -> int:
    return ops.open(path, os.O_RDONLY | _O_DIRECTORY | _O_NOFOLLOW)


def _copy_bytes(src_fd: int, dst_fd: int, ops: Ops) -> None:
    while True:
        block = ops.read(src_fd, 1024 * 1024)
        if not block:
            break
        view = memoryview(block)
        while view:
            written = ops.write(dst_fd, view)
            if written <= 0:
                raise OSError(errno.EIO, "short write while copying a file")
            view = view[written:]


def _clone_contents(
    src_fd: int,
    dst_fd: int,
    ops: Ops,
    *,
    dst_path: str,
    created_entries: list[tuple[str, tuple[int, int], bool]],
    clone: bool,
    allow_exdev_copy: bool = False,
    owner_writable: bool = False,
) -> None:
    for name in sorted(ops.listdir(src_fd)):
        if name in {".", ".."}:
            continue
        info = ops.stat(name, dir_fd=src_fd, follow_symlinks=False)
        if stat.S_ISDIR(info.st_mode):
            mode = stat.S_IMODE(info.st_mode)
            create_mode = mode | stat.S_IWUSR
            final_mode = (mode | stat.S_IWUSR) if owner_writable else mode
            ops.mkdir(name, create_mode & 0o777, dir_fd=dst_fd)
            child_path = os.path.join(dst_path, name)
            child_info = ops.stat(name, dir_fd=dst_fd, follow_symlinks=False)
            created_entries.append((child_path, _file_identity(child_info), True))
            child_src = ops.open(name, os.O_RDONLY | _O_DIRECTORY | _O_NOFOLLOW, dir_fd=src_fd)
            child_dst = ops.open(name, os.O_RDONLY | _O_DIRECTORY | _O_NOFOLLOW, dir_fd=dst_fd)
            try:
                _clone_contents(
                    child_src,
                    child_dst,
                    ops,
                    dst_path=child_path,
                    created_entries=created_entries,
                    clone=clone,
                    allow_exdev_copy=allow_exdev_copy,
                    owner_writable=owner_writable,
                )
                ops.fchmod(child_dst, final_mode)
                ops.futime(child_dst, (info.st_atime_ns, info.st_mtime_ns))
            finally:
                ops.close(child_dst)
                ops.close(child_src)
        elif stat.S_ISLNK(info.st_mode):
            target = ops.readlink(name, dir_fd=src_fd)
            ops.symlink(target, name, dir_fd=dst_fd)
            child_path = os.path.join(dst_path, name)
            child_info = ops.stat(name, dir_fd=dst_fd, follow_symlinks=False)
            created_entries.append((child_path, _file_identity(child_info), False))
            ops.utime(
                name,
                (info.st_atime_ns, info.st_mtime_ns),
                dir_fd=dst_fd,
                follow_symlinks=False,
            )
        elif stat.S_ISREG(info.st_mode):
            src_file = ops.open(name, os.O_RDONLY | _O_NOFOLLOW, dir_fd=src_fd)
            dst_file = -1
            try:
                mode = stat.S_IMODE(info.st_mode)
                create_mode = mode | stat.S_IWUSR
                dst_file = ops.open(
                    name,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | _O_NOFOLLOW,
                    create_mode & 0o777,
                    dir_fd=dst_fd,
                )
                child_path = os.path.join(dst_path, name)
                created_entries.append((child_path, _file_identity(ops.fstat(dst_file)), False))
                if clone:
                    try:
                        ops.ficlone(dst_file, src_file)
                    except OSError as exc:
                        if not (allow_exdev_copy and exc.errno == errno.EXDEV):
                            raise
                        _copy_bytes(src_file, dst_file, ops)
                else:
                    _copy_bytes(src_file, dst_file, ops)
                ops.fchmod(dst_file, (mode | stat.S_IWUSR) if owner_writable else mode)
                ops.futime(dst_file, (info.st_atime_ns, info.st_mtime_ns))
            finally:
                if dst_file >= 0:
                    ops.close(dst_file)
                ops.close(src_file)
        else:
            raise PathRefused(f"special file is not allowed in a cowtree: {name!r}")


def _copy_tree_contents(
    src: str,
    dst: str,
    ops: Ops,
    *,
    destination_identity: tuple[int, int],
    created_entries: list[tuple[str, tuple[int, int], bool]],
    clone: bool,
    allow_exdev_copy: bool = False,
    owner_writable: bool = False,
) -> None:
    source = _open_dir(src, ops)
    destination = _open_dir(dst, ops)
    try:
        if _file_identity(ops.fstat(destination)) != destination_identity:
            raise PathRefused("destination identity changed before tree population")
        _clone_contents(
            source,
            destination,
            ops,
            dst_path=dst,
            created_entries=created_entries,
            clone=clone,
            allow_exdev_copy=allow_exdev_copy,
            owner_writable=owner_writable,
        )
        source_info = ops.fstat(source)
        mode = stat.S_IMODE(source_info.st_mode)
        ops.fchmod(destination, (mode | stat.S_IWUSR) if owner_writable else mode)
        ops.futime(destination, (source_info.st_atime_ns, source_info.st_mtime_ns))
    finally:
        ops.close(destination)
        ops.close(source)


def _create_copy_tree(
    src: str,
    dst: str,
    ops: Ops,
    *,
    clone: bool,
    allow_exdev_copy: bool = False,
    owner_writable: bool = False,
    delete,
    log: Callable[[str], None],
) -> tuple[tuple[int, int], tuple[tuple[str, tuple[int, int], bool], ...]]:
    parent, name = os.path.split(dst)
    parent = parent or "."
    try:
        parent_fd = _open_dir(parent, ops)
    except OSError as exc:
        raise _raise_backend("open-parent", dst, exc) from exc
    identity = None
    try:
        os.mkdir(name, 0o700, dir_fd=parent_fd)
        identity = _identity_at(parent_fd, name)
    except FileExistsError as exc:
        raise DestinationExists(f"destination already exists: {dst!r}") from exc
    finally:
        os.close(parent_fd)
    return _populate_created_tree(
        src,
        dst,
        ops,
        identity,
        clone=clone,
        allow_exdev_copy=allow_exdev_copy,
        owner_writable=owner_writable,
        delete=delete,
        log=log,
    )


def _populate_created_tree(
    src: str,
    dst: str,
    ops: Ops,
    identity: tuple[int, int],
    *,
    clone: bool,
    allow_exdev_copy: bool = False,
    owner_writable: bool = False,
    delete,
    log: Callable[[str], None],
) -> tuple[tuple[int, int], tuple[tuple[str, tuple[int, int], bool], ...]]:
    created_entries: list[tuple[str, tuple[int, int], bool]] = []
    try:
        _copy_tree_contents(
            src,
            dst,
            ops,
            destination_identity=identity,
            created_entries=created_entries,
            clone=clone,
            allow_exdev_copy=allow_exdev_copy,
            owner_writable=owner_writable,
        )
        if _path_identity(dst) != identity:
            raise PathRefused("destination identity changed during tree population")
    except Exception:
        _cleanup_partial_created_tree(dst, identity, created_entries, delete, log)
        raise
    return identity, tuple(created_entries)


def _cleanup_partial_created_tree(
    path: str,
    identity: tuple[int, int],
    entries: list[tuple[str, tuple[int, int], bool]] | tuple[tuple[str, tuple[int, int], bool], ...],
    delete_empty,
    log: Callable[[str], None],
) -> None:
    for child_path, child_identity, is_directory in entries:
        if is_directory:
            _make_created_directory_writable(child_path, child_identity, log)
    for child_path, child_identity, is_directory in reversed(entries):
        if is_directory:
            _cleanup_empty_created_directory(child_path, child_identity, os.rmdir, log)
        else:
            _cleanup_created(child_path, child_identity, os.unlink, log)
    _cleanup_empty_created_directory(path, identity, delete_empty, log)


def _make_dirs_writable(path: str) -> None:
    if not os.path.lexists(path) or os.path.islink(path):
        return
    if not os.path.isdir(path):
        return
    os.chmod(path, stat.S_IMODE(os.lstat(path).st_mode) | 0o700)
    for current, dirs, _files in os.walk(path, topdown=True, followlinks=False):
        kept = []
        for name in dirs:
            child = os.path.join(current, name)
            if os.path.islink(child):
                continue
            os.chmod(child, stat.S_IMODE(os.lstat(child).st_mode) | 0o700)
            kept.append(name)
        dirs[:] = kept


class CopyBackend:
    name = "copy"

    def __init__(self, ops: Ops | None = None, log: Callable[[str], None] | None = None):
        self.ops = ops or LinuxOps()
        self.log = log or logging.getLogger("cowtree").info
        self._sealed_trees: dict[str, tuple[int, int]] = {}
        self._created_paths: dict[str, tuple[int, int]] = {}
        self._created_entries: dict[str, tuple[tuple[str, tuple[int, int], bool], ...] | None] = {}

    def _mark_created(
        self,
        path: str,
        identity: tuple[int, int],
        entries: tuple[tuple[str, tuple[int, int], bool], ...] | None = None,
    ) -> None:
        absolute = os.path.abspath(path)
        self._created_paths[absolute] = identity
        self._created_entries[absolute] = entries

    def created_identity(self, path: str) -> tuple[int, int] | None:
        return self._created_paths.get(os.path.abspath(path))

    def forget_created_identity(self, path: str) -> None:
        absolute = os.path.abspath(path)
        self._created_paths.pop(absolute, None)
        self._created_entries.pop(absolute, None)

    def take_created_identity(self, path: str) -> tuple[int, int] | None:
        return self._created_paths.pop(os.path.abspath(path), None)

    def take_created_entries(self, path: str) -> tuple[tuple[str, tuple[int, int], bool], ...] | None:
        return self._created_entries.pop(os.path.abspath(path), None)

    def cleanup_created(self, path: str) -> None:
        entries = self.take_created_entries(path)
        identity = self.take_created_identity(path)
        if identity is None:
            return
        if entries is not None:
            _cleanup_partial_created_tree(path, identity, entries, self._delete_empty_created_tree, self.log)
            return
        try:
            info = os.lstat(path)
        except FileNotFoundError:
            return
        if stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode):
            _cleanup_created(path, identity, os.unlink, self.log)
        else:
            _cleanup_created(path, identity, self.delete_tree, self.log)

    def _delete_empty_created_tree(self, path: str) -> None:
        os.rmdir(path)

    def _mark_sealed(self, path: str) -> None:
        absolute = os.path.abspath(path)
        try:
            info = os.lstat(absolute)
        except OSError as exc:
            raise _raise_backend("record-sealed-tree", absolute, exc) from exc
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise PathRefused("only a real directory can be sealed as a tree")
        self._sealed_trees[absolute] = _file_identity(info)

    def _is_sealed(self, path: str) -> bool:
        absolute = os.path.abspath(path)
        identity = self._sealed_trees.get(absolute)
        if identity is None:
            return False
        try:
            current = os.lstat(absolute)
        except FileNotFoundError:
            self._sealed_trees.pop(absolute, None)
            return False
        if _file_identity(current) != identity:
            self._sealed_trees.pop(absolute, None)
            return False
        return True

    def _forget_sealed(self, path: str) -> None:
        self._sealed_trees.pop(os.path.abspath(path), None)

    def make_tree(self, path: str, compression: str | None = None) -> str:
        if compression is not None:
            _validate_compression(compression)
        parent, name = os.path.split(path)
        try:
            parent_fd = _open_dir(parent, self.ops)
        except OSError as exc:
            raise _raise_backend("open-parent", path, exc) from exc
        identity = None
        try:
            self.ops.mkdir(name, 0o700, dir_fd=parent_fd)
            identity = _identity_at(parent_fd, name)
            tree_fd = self.ops.open(name, os.O_RDONLY | _O_DIRECTORY | _O_NOFOLLOW, dir_fd=parent_fd)
            try:
                if _file_identity(self.ops.fstat(tree_fd)) != identity:
                    raise PathRefused("destination identity changed during tree creation")
                self.ops.fchmod(tree_fd, 0o700)
            finally:
                self.ops.close(tree_fd)
        except PathRefused:
            if identity is not None:
                _cleanup_empty_created_directory(path, identity, os.rmdir, self.log)
            raise
        except FileExistsError as exc:
            raise DestinationExists(f"destination already exists: {path!r}") from exc
        except OSError as exc:
            if identity is not None:
                _cleanup_empty_created_directory(path, identity, os.rmdir, self.log)
            raise _raise_backend("mkdir", path, exc) from exc
        finally:
            self.ops.close(parent_fd)
        if compression is not None:
            self.log(f"cowtree: copy backend ignores per-tree compression {compression!r}")
        if _path_identity(path) != identity:
            raise PathRefused("destination identity changed during tree creation")
        self._mark_created(path, identity, ())
        return path

    def ingest_tree(self, src: str, dst: str) -> str:
        try:
            identity, entries = _create_copy_tree(
                src, dst, self.ops, clone=False, delete=os.rmdir, log=self.log
            )
        except (PathRefused, BackendFailed):
            raise
        except OSError as exc:
            raise _raise_backend("ingest-tree", dst, exc) from exc
        self._mark_created(dst, identity, entries)
        return dst

    def ingest_file(self, src: str, dst: str) -> None:
        identity = _copy_file(src, dst, self.ops, clone=False, log=self.log)
        self._mark_created(dst, identity)

    def clone_tree(self, src: str, dst: str) -> str:
        try:
            identity, entries = _create_copy_tree(
                src,
                dst,
                self.ops,
                clone=False,
                owner_writable=self._is_sealed(src),
                delete=os.rmdir,
                log=self.log,
            )
        except (PathRefused, BackendFailed):
            raise
        except OSError as exc:
            raise _raise_backend("clone-tree", dst, exc) from exc
        self._mark_created(dst, identity, entries)
        return dst

    def clone_file(self, src: str, dst: str) -> None:
        identity = _copy_file(src, dst, self.ops, clone=False, log=self.log)
        self._mark_created(dst, identity)

    def seal(self, path: str) -> None:
        _seal_modes(path)
        self._mark_sealed(path)

    def delete_tree(self, path: str) -> None:
        _make_dirs_writable(path)
        _delete_path(path)
        self._forget_sealed(path)
        self.forget_created_identity(path)


class ReflinkBackend(CopyBackend):
    name = "reflink"

    def make_tree(self, path: str, compression: str | None = None) -> str:
        if compression is not None:
            _validate_compression(compression)
        result = super().make_tree(path)
        if compression is not None:
            self.log(f"cowtree: reflink backend ignores per-tree compression {compression!r}")
        return result

    def ingest_tree(self, src: str, dst: str) -> str:
        try:
            source_device = os.stat(src, follow_symlinks=False).st_dev
            destination_device = os.stat(os.path.dirname(dst) or ".", follow_symlinks=False).st_dev
            cross_device = source_device != destination_device
            identity, entries = _create_copy_tree(
                src,
                dst,
                self.ops,
                clone=not cross_device,
                allow_exdev_copy=True,
                delete=os.rmdir,
                log=self.log,
            )
        except (PathRefused, BackendFailed):
            raise
        except OSError as exc:
            raise _raise_backend("ingest-tree", dst, exc) from exc
        self._mark_created(dst, identity, entries)
        return dst

    def ingest_file(self, src: str, dst: str) -> None:
        try:
            source_device = os.stat(src, follow_symlinks=False).st_dev
            destination_device = os.stat(os.path.dirname(dst) or ".", follow_symlinks=False).st_dev
        except OSError as exc:
            raise _raise_backend("stat-ingest-file", src, exc) from exc
        if source_device == destination_device:
            self.clone_file(src, dst)
        else:
            identity = _copy_file(src, dst, self.ops, clone=False, log=self.log)
            self._mark_created(dst, identity)

    def clone_tree(self, src: str, dst: str) -> str:
        try:
            identity, entries = _create_copy_tree(
                src,
                dst,
                self.ops,
                clone=True,
                owner_writable=self._is_sealed(src),
                delete=os.rmdir,
                log=self.log,
            )
        except (PathRefused, BackendFailed):
            raise
        except OSError as exc:
            raise _raise_backend("clone-tree", src, exc) from exc
        self._mark_created(dst, identity, entries)
        return dst

    def clone_file(self, src: str, dst: str) -> None:
        identity = _copy_file(src, dst, self.ops, clone=True, log=self.log)
        self._mark_created(dst, identity)

    def delete_tree(self, path: str) -> None:
        _make_dirs_writable(path)
        _delete_path(path)
        self._forget_sealed(path)
        self.forget_created_identity(path)


class BtrfsBackend(ReflinkBackend):
    name = "btrfs"

    def _delete_empty_created_tree(self, path: str) -> None:
        try:
            is_subvolume = os.lstat(path).st_ino == 256
        except FileNotFoundError:
            return
        if not is_subvolume:
            os.rmdir(path)
            return
        parent, name = os.path.split(path)
        parent_fd = _open_dir(parent, self.ops)
        try:
            try:
                self.ops.subvol_destroy(parent_fd, name)
            except OSError as exc:
                if exc.errno != errno.EPERM:
                    raise
                self.ops.rmdir(name, dir_fd=parent_fd)
        finally:
            self.ops.close(parent_fd)

    def make_tree(self, path: str, compression: str | None = None) -> str:
        if compression is not None:
            _validate_compression(compression)
        parent, name = os.path.split(path)
        try:
            parent_fd = _open_dir(parent, self.ops)
        except OSError as exc:
            raise _raise_backend("open-parent", path, exc) from exc
        identity = None
        tree_fd = -1
        try:
            self.ops.subvol_create(parent_fd, name)
            identity = _identity_at(parent_fd, name)
            tree_fd = self.ops.open(name, os.O_RDONLY | _O_DIRECTORY | _O_NOFOLLOW, dir_fd=parent_fd)
            if _file_identity(self.ops.fstat(tree_fd)) != identity:
                raise PathRefused("destination identity changed during subvolume creation")
            self.ops.fchmod(tree_fd, 0o700)
        except PathRefused:
            if tree_fd >= 0:
                try:
                    self.ops.close(tree_fd)
                except OSError as cleanup_error:
                    self.log(f"cowtree: close after subvolume creation failure also failed: {cleanup_error}")
                tree_fd = -1
            if identity is not None:
                _cleanup_empty_created_directory(path, identity, self._delete_empty_created_tree, self.log)
            raise
        except FileExistsError as exc:
            if tree_fd >= 0:
                try:
                    self.ops.close(tree_fd)
                except OSError as cleanup_error:
                    self.log(f"cowtree: close after subvolume creation failure also failed: {cleanup_error}")
                tree_fd = -1
            if identity is not None:
                _cleanup_empty_created_directory(path, identity, self._delete_empty_created_tree, self.log)
            raise DestinationExists(f"destination already exists: {path!r}") from exc
        except OSError as exc:
            if tree_fd >= 0:
                try:
                    self.ops.close(tree_fd)
                except OSError as cleanup_error:
                    self.log(f"cowtree: close after subvolume creation failure also failed: {cleanup_error}")
                tree_fd = -1
            if identity is not None:
                _cleanup_empty_created_directory(path, identity, self._delete_empty_created_tree, self.log)
            raise _raise_backend("subvol-create", path, exc) from exc
        finally:
            if tree_fd >= 0:
                self.ops.close(tree_fd)
            self.ops.close(parent_fd)
        if compression is not None:
            try:
                self.ops.setxattr(path, "btrfs.compression", compression.encode("ascii"), follow_symlinks=False)
            except OSError as exc:
                _cleanup_empty_created_directory(path, identity, self._delete_empty_created_tree, self.log)
                raise _raise_backend("set-compression", path, exc) from exc
        if _path_identity(path) != identity:
            raise PathRefused("destination identity changed during subvolume creation")
        self._mark_created(path, identity, ())
        return path

    def ingest_tree(self, src: str, dst: str) -> str:
        identity = None
        try:
            self.make_tree(dst)
            identity = self.created_identity(dst)
            if identity is None:
                raise PathRefused("backend did not retain the created subvolume identity")
            cross_device = os.stat(src, follow_symlinks=False).st_dev != os.stat(dst).st_dev
            _, entries = _populate_created_tree(
                src,
                dst,
                self.ops,
                identity,
                clone=not cross_device,
                allow_exdev_copy=True,
                delete=self._delete_empty_created_tree,
                log=self.log,
            )
            self._mark_created(dst, identity, entries)
        except (PathRefused, BackendFailed):
            _cleanup_empty_created_directory(dst, identity, self._delete_empty_created_tree, self.log)
            self.forget_created_identity(dst)
            raise
        except OSError as exc:
            _cleanup_empty_created_directory(dst, identity, self._delete_empty_created_tree, self.log)
            self.forget_created_identity(dst)
            raise _raise_backend("ingest-tree", dst, exc) from exc
        return dst

    def clone_tree(self, src: str, dst: str) -> str:
        if os.path.lexists(dst):
            raise DestinationExists(f"destination already exists: {dst!r}")
        try:
            source_info = os.stat(src, follow_symlinks=False)
        except OSError as exc:
            raise _raise_backend("stat-source", src, exc) from exc
        if source_info.st_ino == 256:
            parent, name = os.path.split(dst)
            parent_fd = -1
            src_fd = -1
            try:
                parent_fd = _open_dir(parent, self.ops)
                src_fd = _open_dir(src, self.ops)
            except OSError as exc:
                if parent_fd >= 0:
                    self.ops.close(parent_fd)
                raise _raise_backend("open-snapshot-source", src, exc) from exc
            destination_identity = None
            try:
                self.ops.snapshot_create(parent_fd, src_fd, name)
                destination_identity = _path_identity(dst)
            except FileExistsError as exc:
                raise DestinationExists(f"destination already exists: {dst!r}") from exc
            except OSError as exc:
                raise _raise_backend("snapshot-create", src, exc) from exc
            finally:
                self.ops.close(src_fd)
                self.ops.close(parent_fd)
            try:
                destination_fd = _open_dir(dst, self.ops)
            except OSError as exc:
                _cleanup_created(dst, destination_identity, self.delete_tree, self.log)
                raise _raise_backend("snapshot-writable", dst, exc) from exc
            try:
                if _file_identity(self.ops.fstat(destination_fd)) != destination_identity:
                    raise PathRefused("snapshot destination identity changed during clone")
                flags = self.ops.subvol_getflags(destination_fd)
                if flags & BTRFS_SUBVOL_RDONLY:
                    self.ops.subvol_setflags(destination_fd, flags & ~BTRFS_SUBVOL_RDONLY)
                self.ops.close(destination_fd)
            except PathRefused:
                try:
                    self.ops.close(destination_fd)
                except OSError as cleanup_error:
                    self.log(f"cowtree: close after snapshot identity failure also failed: {cleanup_error}")
                _cleanup_created(dst, destination_identity, self.delete_tree, self.log)
                raise
            except OSError as exc:
                try:
                    self.ops.close(destination_fd)
                except OSError as cleanup_error:
                    self.log(f"cowtree: close after snapshot flag failure also failed: {cleanup_error}")
                _cleanup_created(dst, destination_identity, self.delete_tree, self.log)
                raise _raise_backend("snapshot-writable", dst, exc) from exc
            self._mark_created(dst, destination_identity)
            return dst
        self.make_tree(dst)
        identity = self.created_identity(dst)
        if identity is None:
            raise PathRefused("backend did not retain the created subvolume identity")
        try:
            _, entries = _populate_created_tree(
                src,
                dst,
                self.ops,
                identity,
                clone=True,
                owner_writable=self._is_sealed(src),
                delete=self._delete_empty_created_tree,
                log=self.log,
            )
            self._mark_created(dst, identity, entries)
        except (PathRefused, BackendFailed):
            _cleanup_empty_created_directory(dst, identity, self._delete_empty_created_tree, self.log)
            self.forget_created_identity(dst)
            raise
        except OSError as exc:
            _cleanup_empty_created_directory(dst, identity, self._delete_empty_created_tree, self.log)
            self.forget_created_identity(dst)
            raise _raise_backend("clone-tree", src, exc) from exc
        return dst

    def seal(self, path: str) -> None:
        try:
            fd = _open_dir(path, self.ops)
        except OSError as exc:
            raise _raise_backend("open-subvol", path, exc) from exc
        try:
            flags = self.ops.subvol_getflags(fd)
            self.ops.subvol_setflags(fd, flags | BTRFS_SUBVOL_RDONLY)
            self._mark_sealed(path)
        except OSError as exc:
            raise _raise_backend("subvol-seal", path, exc) from exc
        finally:
            self.ops.close(fd)

    def delete_tree(self, path: str) -> None:
        try:
            target_info = os.stat(path, follow_symlinks=False)
        except FileNotFoundError:
            is_subvolume = False
        except OSError as exc:
            raise _raise_backend("stat-delete-target", path, exc) from exc
        else:
            is_subvolume = stat.S_ISDIR(target_info.st_mode) and target_info.st_ino == 256
        if is_subvolume:
            parent, name = os.path.split(path)
            try:
                parent_fd = _open_dir(parent, self.ops)
            except OSError as exc:
                raise _raise_backend("subvol-delete", path, exc) from exc
            tree_fd = -1
            try:
                tree_fd = _open_dir(path, self.ops)
                flags = self.ops.subvol_getflags(tree_fd)
                if flags & BTRFS_SUBVOL_RDONLY:
                    self.ops.subvol_setflags(tree_fd, flags & ~BTRFS_SUBVOL_RDONLY)
                try:
                    self.ops.subvol_destroy(parent_fd, name)
                except OSError as exc:
                    if exc.errno != errno.EPERM:
                        raise
                    self.ops.close(tree_fd)
                    tree_fd = -1
                    _empty_directory(path)
                    self.ops.rmdir(name, dir_fd=parent_fd)
                self._forget_sealed(path)
                self.forget_created_identity(path)
            except OSError as exc:
                raise _raise_backend("subvol-delete", path, exc) from exc
            finally:
                if tree_fd >= 0:
                    self.ops.close(tree_fd)
                self.ops.close(parent_fd)
            return
        super().delete_tree(path)


def _validate_compression(compression: str) -> None:
    level = compression[5:] if isinstance(compression, str) and compression.startswith("zstd:") else ""
    numeric_level = 0 < len(level) <= 2 and all("0" <= character <= "9" for character in level)
    if not isinstance(compression, str) or (
        compression not in {"zstd", "lzo", "none"}
        and not (numeric_level and 1 <= int(level) <= 15)
    ):
        raise PathRefused("compression must be zstd, zstd:N (1..15), lzo, or none")


def _copy_file(
    src: str,
    dst: str,
    ops: Ops,
    *,
    clone: bool,
    log: Callable[[str], None],
) -> tuple[int, int]:
    destination_identity = None
    try:
        info = os.stat(src, follow_symlinks=False)
        if not stat.S_ISREG(info.st_mode):
            raise PathRefused(f"source file is not a regular file: {src!r}")
        source = ops.open(src, os.O_RDONLY | _O_NOFOLLOW)
        destination = -1
        try:
            mode = stat.S_IMODE(info.st_mode)
            try:
                destination = ops.open(
                    dst,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | _O_NOFOLLOW,
                    (mode | stat.S_IWUSR) & 0o777,
                )
            except FileExistsError as exc:
                raise DestinationExists(f"destination already exists: {dst!r}") from exc
            destination_identity = _file_identity(ops.fstat(destination))
            if clone:
                ops.ficlone(destination, source)
            else:
                _copy_bytes(source, destination, ops)
            ops.fchmod(destination, mode)
            ops.futime(destination, (info.st_atime_ns, info.st_mtime_ns))
            if _path_identity(dst) != destination_identity:
                raise PathRefused("destination identity changed during file creation")
        finally:
            if destination >= 0:
                ops.close(destination)
            ops.close(source)
    except (PathRefused, BackendFailed):
        _cleanup_created(dst, destination_identity, os.unlink, log)
        raise
    except OSError as exc:
        _cleanup_created(dst, destination_identity, os.unlink, log)
        raise _raise_backend("clone-file" if clone else "copy-file", src, exc) from exc
    return destination_identity


def _seal_modes(path: str) -> None:
    if os.path.islink(path):
        return
    if os.path.isdir(path):
        for current, dirs, files in os.walk(path, topdown=True, followlinks=False):
            for name in files:
                target = os.path.join(current, name)
                if not os.path.islink(target):
                    os.chmod(target, stat.S_IMODE(os.lstat(target).st_mode) & ~0o222)
            for name in dirs:
                target = os.path.join(current, name)
                if not os.path.islink(target):
                    os.chmod(target, stat.S_IMODE(os.lstat(target).st_mode) & ~0o222)
        os.chmod(path, stat.S_IMODE(os.lstat(path).st_mode) & ~0o222)
    else:
        os.chmod(path, stat.S_IMODE(os.lstat(path).st_mode) & ~0o222)


def _empty_directory(path: str) -> None:
    _make_dirs_writable(path)
    with os.scandir(path) as entries:
        children = [entry.path for entry in entries]
    for child in children:
        if os.path.islink(child) or not os.path.isdir(child):
            os.unlink(child)
        else:
            _make_dirs_writable(child)
            shutil.rmtree(child)


def _delete_path(path: str) -> None:
    if not os.path.lexists(path):
        return
    if os.path.islink(path) or not os.path.isdir(path):
        os.unlink(path)
    else:
        shutil.rmtree(path)
