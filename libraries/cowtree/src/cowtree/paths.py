"""Lexical path and mountinfo helpers."""
from __future__ import annotations

import os
import re

from .errors import PathRefused, RootInvalid

_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
_MOUNT_ESCAPE = re.compile(r"\\([0-7]{3})")


def validate_name(value: str) -> str:
    if not isinstance(value, str) or _NAME.fullmatch(value) is None:
        raise PathRefused("name must match [A-Za-z0-9][A-Za-z0-9._-]{0,63}")
    return value


def normalized_absolute(value: str) -> bool:
    if not isinstance(value, str) or not value or "\x00" in value or not os.path.isabs(value):
        return False
    return os.path.normpath(value) == value


def within(path: str, parent: str) -> bool:
    try:
        return os.path.commonpath((os.path.abspath(path), os.path.abspath(parent))) == os.path.abspath(parent)
    except ValueError:
        return False


def _unescape_mount_field(value: str) -> str:
    return _MOUNT_ESCAPE.sub(lambda match: chr(int(match.group(1), 8)), value)


def parse_mountinfo(text: str) -> list[tuple[str, str]]:
    """Return (mountpoint, filesystem type) rows, rejecting malformed rows."""
    rows: list[tuple[str, str]] = []
    for line_number, line in enumerate(text.splitlines(), 1):
        if not line:
            continue
        try:
            before, after = line.split(" - ", 1)
            left = before.split()
            right = after.split()
            mountpoint = _unescape_mount_field(left[4])
            filesystem = right[0]
            if not mountpoint.startswith("/") or not filesystem:
                raise ValueError
        except (IndexError, ValueError) as exc:
            raise RootInvalid(f"malformed /proc/self/mountinfo row {line_number}") from exc
        rows.append((mountpoint, filesystem))
    return rows


def mount_fstype(path: str, text: str) -> str:
    """Find the filesystem of the longest mountpoint prefix of *path*."""
    matches = [
        (mountpoint, filesystem)
        for mountpoint, filesystem in parse_mountinfo(text)
        if path == mountpoint or (mountpoint == "/" and path.startswith("/"))
        or path.startswith(mountpoint.rstrip("/") + "/")
    ]
    if not matches:
        raise RootInvalid(f"cannot determine filesystem for {path!r} from mountinfo")
    return max(matches, key=lambda item: len(item[0]))[1]


def require_directory(path: str, *, label: str) -> os.stat_result:
    try:
        result = os.lstat(path)
    except OSError as exc:
        raise RootInvalid(f"{label} does not exist or cannot be inspected: {path!r}") from exc
    if not os.path.isdir(path) or os.path.islink(path):
        raise RootInvalid(f"{label} must be a real directory: {path!r}")
    return result


def validate_root(path: str, *, euid: int, mountinfo: str) -> tuple[os.stat_result, str]:
    if not normalized_absolute(path):
        raise RootInvalid("MDT_COW_ROOT must be an absolute, normalized path without NUL")
    try:
        resolved = os.path.realpath(path)
    except OSError as exc:
        raise RootInvalid(f"cannot resolve MDT_COW_ROOT {path!r}") from exc
    if resolved != path:
        raise RootInvalid(f"MDT_COW_ROOT must not contain symlinks: {path!r}")
    info = require_directory(path, label="MDT_COW_ROOT")
    if euid == 0 or info.st_uid != euid:
        raise RootInvalid(f"MDT_COW_ROOT must be owned by the non-root caller uid {euid}")
    if info.st_mode & 0o002:
        raise RootInvalid("MDT_COW_ROOT must not be world-writable")
    filesystem = mount_fstype(path, mountinfo)
    if filesystem in {"overlay", "tmpfs", "ramfs", "proc", "sysfs"}:
        raise RootInvalid(f"MDT_COW_ROOT filesystem {filesystem!r} is not a persistent CoW area")
    return info, filesystem
