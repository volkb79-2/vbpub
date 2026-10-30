"""CoW area validation and capability probing in the caller's namespace."""
from __future__ import annotations

import errno
import logging
import os
import secrets
import shutil
import stat
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from .backends import BtrfsBackend, LinuxOps, Ops, ReflinkBackend
from .errors import RootInvalid
from .paths import validate_root

ENV_ROOT = "MDT_COW_ROOT"
ENV_MODE = "COWTREE_MODE"
_UNSUPPORTED_FILESYSTEMS = {"overlay", "tmpfs", "ramfs", "proc", "sysfs"}
_info_lock = threading.Lock()
_info_seen: set[str] = set()


@dataclass(frozen=True)
class Capabilities:
    backend: str
    fs_type: str
    cheap_clone: bool
    tree_snapshot: bool
    readonly_seal: bool
    per_tree_compression: bool
    subvol_delete: bool


@dataclass(frozen=True)
class Area:
    root: str
    caps: Capabilities
    backend: object
    ops: Ops
    private_dir: str
    runs_dir: str
    shared_dir: str


def _log_once(message: str, log: Callable[[str], None] | None) -> None:
    with _info_lock:
        if message in _info_seen:
            return
        _info_seen.add(message)
    if log is None:
        logging.getLogger("cowtree").info(message)
    else:
        log(message)


def _reset_info_for_tests() -> None:
    with _info_lock:
        _info_seen.clear()


def _read_mountinfo(ops: Ops) -> str:
    reader = getattr(ops, "read_mountinfo", None)
    if reader is not None:
        try:
            return reader()
        except OSError as exc:
            raise RootInvalid(f"cannot read /proc/self/mountinfo: {exc}") from exc
    try:
        return Path("/proc/self/mountinfo").read_text(encoding="utf-8")
    except OSError as exc:
        raise RootInvalid(f"cannot read /proc/self/mountinfo: {exc}") from exc


def _ensure_private_layout(root: str) -> tuple[str, str, str]:
    private = os.path.join(root, ".cowtree")
    try:
        os.mkdir(private, 0o700)
    except FileExistsError:
        pass
    except OSError as exc:
        raise RootInvalid(f"cannot create cowtree metadata directory under validated root: {exc}") from exc
    else:
        try:
            os.chmod(private, 0o700)
        except OSError as exc:
            raise RootInvalid(f"cannot secure cowtree metadata directory: {exc}") from exc
    try:
        info = os.lstat(private)
    except OSError as exc:
        raise RootInvalid(f"cannot inspect cowtree metadata directory: {exc}") from exc
    if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
        raise RootInvalid(".cowtree must be a real directory, not a symlink")
    if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise RootInvalid(".cowtree must be owned by the caller and have mode 0700")
    version_path = os.path.join(private, "VERSION")
    try:
        descriptor = os.open(version_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        try:
            version_info = os.lstat(version_path)
            if not stat.S_ISREG(version_info.st_mode) or stat.S_ISLNK(version_info.st_mode):
                raise RootInvalid(".cowtree/VERSION must be a regular file")
            content = Path(version_path).read_text(encoding="ascii")
        except RootInvalid:
            raise
        except OSError as exc:
            raise RootInvalid(f"cannot read .cowtree/VERSION: {exc}") from exc
        if content != "1\n":
            raise RootInvalid("unsupported .cowtree metadata version")
    except OSError as exc:
        raise RootInvalid(f"cannot initialize .cowtree/VERSION: {exc}") from exc
    else:
        try:
            if os.write(descriptor, b"1\n") != 2:
                raise OSError(errno.EIO, "short write while initializing metadata version")
            os.fsync(descriptor)
        except OSError as exc:
            raise RootInvalid(f"cannot initialize .cowtree/VERSION: {exc}") from exc
        finally:
            os.close(descriptor)
    runs = os.path.join(private, "runs")
    shared = os.path.join(root, "shared")
    for path in (runs, shared):
        try:
            os.mkdir(path, 0o700)
        except FileExistsError:
            pass
        except OSError as exc:
            raise RootInvalid(f"cannot create cowtree directory {path!r}: {exc}") from exc
        else:
            try:
                os.chmod(path, 0o700)
            except OSError as exc:
                raise RootInvalid(f"cannot secure cowtree directory {path!r}: {exc}") from exc
        try:
            child = os.lstat(path)
        except OSError as exc:
            raise RootInvalid(f"cannot inspect cowtree directory {path!r}: {exc}") from exc
        if not stat.S_ISDIR(child.st_mode) or stat.S_ISLNK(child.st_mode) or child.st_uid != os.geteuid():
            raise RootInvalid(f"cowtree directory must be caller-owned and not a symlink: {path!r}")
        if stat.S_IMODE(child.st_mode) != 0o700:
            raise RootInvalid(f"cowtree directory must have mode 0700: {path!r}")
    return private, runs, shared


def _try_btrfs_probe(probe_dir: str, ops: Ops) -> bool:
    parent_fd = ops.open(probe_dir, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0))
    source_fd = -1
    created: list[str] = []
    try:
        ops.subvol_create(parent_fd, "source")
        created.append("source")
        source_path = os.path.join(probe_dir, "source")
        source_fd = ops.open(source_path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0))
        ops.snapshot_create(parent_fd, source_fd, "snapshot")
        created.append("snapshot")
        ops.close(source_fd)
        source_fd = -1
        ops.subvol_destroy(parent_fd, "snapshot")
        created.remove("snapshot")
        ops.subvol_destroy(parent_fd, "source")
        created.remove("source")
        return True
    except OSError:
        return False
    finally:
        if source_fd >= 0:
            ops.close(source_fd)
        for name in reversed(created):
            try:
                ops.subvol_destroy(parent_fd, name)
            except OSError:
                try:
                    shutil.rmtree(os.path.join(probe_dir, name))
                except OSError:
                    pass
        ops.close(parent_fd)


def _try_reflink_probe(probe_dir: str, ops: Ops) -> bool:
    source = os.path.join(probe_dir, "reflink-source")
    destination = os.path.join(probe_dir, "reflink-destination")
    source_fd = -1
    destination_fd = -1
    try:
        source_fd = ops.open(source, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        if ops.write(source_fd, b"x" * 4096) != 4096:
            return False
        ops.close(source_fd)
        source_fd = -1
        source_fd = ops.open(source, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        destination_fd = ops.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        ops.ficlone(destination_fd, source_fd)
        return True
    except OSError:
        return False
    finally:
        if source_fd >= 0:
            ops.close(source_fd)
        if destination_fd >= 0:
            ops.close(destination_fd)
        for path in (destination, source):
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass


def _probe(root: str, filesystem: str, ops: Ops) -> str:
    private = os.path.join(root, ".cowtree")
    probe_dir = os.path.join(private, f"probe-{secrets.token_hex(8)}")
    os.mkdir(probe_dir, 0o700)
    try:
        if filesystem == "btrfs" and _try_btrfs_probe(probe_dir, ops):
            return "btrfs"
        if _try_reflink_probe(probe_dir, ops):
            return "reflink"
        raise RootInvalid(
            "MDT_COW_ROOT cannot clone files; use btrfs, XFS(reflink=1) or ZFS(block cloning), "
            "or unset MDT_COW_ROOT"
        )
    finally:
        try:
            shutil.rmtree(probe_dir)
        except FileNotFoundError:
            pass
        except OSError as exc:
            logging.getLogger("cowtree").warning(
                "cowtree: cannot remove capability probe directory %s: %s", probe_dir, exc
            )


def detect(
    *,
    environ: Mapping[str, str] | None = None,
    ops: Ops | None = None,
    log: Callable[[str], None] | None = None,
) -> Area | None:
    environment = os.environ if environ is None else environ
    operations = ops or LinuxOps()
    mode = environment.get(ENV_MODE, "auto")
    if mode not in {"auto", "off"}:
        raise RootInvalid(f"COWTREE_MODE must be 'auto' or 'off', not {mode!r}")
    if mode == "off":
        _log_once("cowtree: COWTREE_MODE=off; using plain directories", log)
        return None
    if ENV_ROOT not in environment:
        _log_once("cowtree: MDT_COW_ROOT unset; using plain directories", log)
        return None
    root = environment[ENV_ROOT]
    euid_reader = getattr(operations, "effective_uid", os.geteuid)
    try:
        euid = euid_reader()
    except OSError as exc:
        raise RootInvalid(f"cannot determine effective uid: {exc}") from exc
    _info, filesystem = validate_root(root, euid=euid, mountinfo=_read_mountinfo(operations))
    private, runs, shared = _ensure_private_layout(root)
    backend_name = _probe(root, filesystem, operations)
    if backend_name == "btrfs":
        backend = BtrfsBackend(operations, log=log)
        caps = Capabilities("btrfs", filesystem, True, True, True, True, True)
    else:
        backend = ReflinkBackend(operations, log=log)
        caps = Capabilities("reflink", filesystem, True, False, True, False, False)
    _log_once(f"cowtree: {backend_name} area at {root} ({filesystem})", log)
    return Area(root, caps, backend, operations, private, runs, shared)
