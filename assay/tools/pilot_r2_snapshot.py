"""Race-aware regular-file and directory snapshots for pilot evidence."""

from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Any


def _identity(info: os.stat_result) -> dict[str, int]:
    return {
        "device": info.st_dev,
        "inode": info.st_ino,
        "links": info.st_nlink,
        "mode": stat.S_IFMT(info.st_mode) | stat.S_IMODE(info.st_mode),
        "size": info.st_size,
        "mtime_ns": info.st_mtime_ns,
        "ctime_ns": info.st_ctime_ns,
    }


def _requested(path: Path) -> Path:
    return Path(os.path.normpath(os.path.abspath(os.path.expanduser(os.fspath(path)))))


def _open_parent(path: Path) -> tuple[Path, int, os.stat_result]:
    requested = _requested(path)
    parent = requested.parent.resolve(strict=True)
    descriptor = os.open(
        parent,
        os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW,
    )
    return parent, descriptor, os.fstat(descriptor)


def _entry_identity(parent_fd: int, name: str) -> os.stat_result:
    info = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise ValueError(f"pilot evidence entry {name!r} is not a regular non-symlink file")
    if info.st_nlink != 1:
        raise ValueError(f"pilot evidence entry {name!r} is not a single-link file")
    return info


def read_file(path: Path, *, maximum: int) -> tuple[bytes, dict[str, Any]]:
    """Read one pinned, single-link regular file and return its path identity."""
    parent, parent_fd, parent_before = _open_parent(path)
    file_fd: int | None = None
    try:
        requested = _requested(path)
        before = _entry_identity(parent_fd, requested.name)
        file_fd = os.open(
            requested.name,
            os.O_RDONLY | os.O_CLOEXEC | os.O_NONBLOCK | os.O_NOFOLLOW,
            dir_fd=parent_fd,
        )
        opened = os.fstat(file_fd)
        if _identity(opened) != _identity(before):
            raise ValueError(f"pilot evidence path changed while opening {path}")
        if opened.st_size > maximum:
            raise ValueError(f"{path} exceeds the {maximum}-byte limit")
        chunks: list[bytes] = []
        size = 0
        while True:
            block = os.read(file_fd, min(1024 * 1024, maximum + 1 - size))
            if not block:
                break
            chunks.append(block)
            size += len(block)
            if size > maximum:
                raise ValueError(f"{path} exceeds the {maximum}-byte limit")
        after = os.fstat(file_fd)
        entry_after = _entry_identity(parent_fd, requested.name)
        parent_after = os.fstat(parent_fd)
        if (
            _identity(before) != _identity(after)
            or _identity(after) != _identity(entry_after)
            or _identity(parent_before) != _identity(parent_after)
            or size != after.st_size
        ):
            raise ValueError(f"{path} changed while it was being read")
        return b"".join(chunks), {
            "path": str(parent / requested.name),
            "parent": {
                "device": parent_before.st_dev,
                "inode": parent_before.st_ino,
            },
            "file": _identity(after),
        }
    finally:
        if file_fd is not None:
            os.close(file_fd)
        os.close(parent_fd)


def stat_file(path: Path) -> dict[str, Any]:
    """Return current identity for a single-link regular file without reading it."""
    parent, parent_fd, parent_before = _open_parent(path)
    file_fd: int | None = None
    try:
        requested = _requested(path)
        before = _entry_identity(parent_fd, requested.name)
        file_fd = os.open(
            requested.name,
            os.O_RDONLY | os.O_CLOEXEC | os.O_NONBLOCK | os.O_NOFOLLOW,
            dir_fd=parent_fd,
        )
        opened = os.fstat(file_fd)
        after = _entry_identity(parent_fd, requested.name)
        parent_after = os.fstat(parent_fd)
        if (
            _identity(before) != _identity(opened)
            or _identity(opened) != _identity(after)
            or _identity(parent_before) != _identity(parent_after)
        ):
            raise ValueError(f"pilot evidence path changed while being checked: {path}")
        return {
            "path": str(parent / requested.name),
            "parent": {"device": parent_before.st_dev, "inode": parent_before.st_ino},
            "file": _identity(opened),
        }
    finally:
        if file_fd is not None:
            os.close(file_fd)
        os.close(parent_fd)


def directory_snapshot(path: Path) -> dict[str, Any]:
    """Capture directory identity and its exact regular-file inventory."""
    requested = _requested(path)
    parent = requested.parent.resolve(strict=True)
    descriptor = os.open(
        requested,
        os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW,
    )
    try:
        before = os.fstat(descriptor)
        path_info = os.stat(requested, follow_symlinks=False)
        if stat.S_ISLNK(path_info.st_mode) or not stat.S_ISDIR(path_info.st_mode):
            raise ValueError(f"pilot evidence directory is not a real directory: {path}")
        if (before.st_dev, before.st_ino) != (path_info.st_dev, path_info.st_ino):
            raise ValueError(f"pilot evidence directory changed while opening: {path}")
        names: list[str] = []
        for entry in os.scandir(descriptor):
            info = os.stat(entry.name, dir_fd=descriptor, follow_symlinks=False)
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
                raise ValueError(f"pilot evidence entry {entry.name!r} is not a regular file")
            if info.st_nlink != 1:
                raise ValueError(f"pilot evidence entry {entry.name!r} is not a single-link file")
            names.append(entry.name)
        after = os.fstat(descriptor)
        if _identity(before) != _identity(after):
            raise ValueError(f"pilot evidence directory changed while listing: {path}")
        return {
            "path": str(parent / requested.name),
            "directory": _identity(after),
            "names": sorted(names),
        }
    finally:
        os.close(descriptor)


def path_is_absent(path: Path) -> dict[str, Any]:
    """Require a leaf to be absent and bind that fact to its parent directory."""
    parent, parent_fd, parent_info = _open_parent(path)
    try:
        requested = _requested(path)
        try:
            os.stat(requested.name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise ValueError(f"pilot verdict must remain absent: {path}")
        after = os.fstat(parent_fd)
        if (parent_info.st_dev, parent_info.st_ino) != (after.st_dev, after.st_ino):
            raise ValueError(f"pilot verdict parent changed while checked: {path}")
        return {
            "path": str(parent / requested.name),
            "parent": {"device": parent_info.st_dev, "inode": parent_info.st_ino},
            "absent": True,
        }
    finally:
        os.close(parent_fd)
