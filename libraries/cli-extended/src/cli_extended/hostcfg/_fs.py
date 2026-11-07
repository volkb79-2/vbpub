"""Symlink-safe filesystem operations shared by hostcfg resources.

Every helper takes the fake or real ``root`` explicitly, refuses symlinks in
managed path components, and takes its lock path as a parameter so each
resource (and a future hostcfg transaction) chooses its own lock file.
"""

from __future__ import annotations

import errno
import fcntl
import os
import stat
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Callable

from ..parser import CliFailure

_DIR_MODE = 0o755
_open = os.open
_fstat = os.fstat
_fchmod = os.fchmod
_fchown = os.fchown
_close = os.close
_fdopen = os.fdopen
_fsync = os.fsync
_replace = os.replace
_unlink = os.unlink
_listdir = os.listdir
_mkstemp = tempfile.mkstemp


class PathRefusal(CliFailure):
    """A managed path was refused; ``kind`` is ``symlink``, ``type`` or ``outside``."""

    def __init__(self, message: str, *, kind: str, path: str) -> None:
        super().__init__(message, exit_code=2)
        self.kind = kind
        self.path = path


def display(path: Path, root: Path) -> str:
    try:
        relative = path.relative_to(root)
    except ValueError:
        return str(path)
    return "/" + relative.as_posix()


def _symlink_refusal(path: Path, root: Path) -> PathRefusal:
    shown = display(path, root)
    return PathRefusal(f"refusing symlinked target: {shown}", kind="symlink", path=shown)


def _directory_refusal(path: Path, root: Path) -> PathRefusal:
    shown = display(path, root)
    return PathRefusal(
        f"managed directory is not a directory: {shown}", kind="type", path=shown
    )


def safe_components(root: Path, path: Path) -> None:
    """Refuse symlinks in each managed path component below ``root``."""

    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise PathRefusal(
            f"managed path is outside root: {path}", kind="outside", path=str(path)
        ) from exc
    cursor = root
    for index, part in enumerate(relative.parts):
        cursor = cursor / part
        try:
            mode = cursor.lstat().st_mode
        except FileNotFoundError:
            continue
        except NotADirectoryError as exc:
            raise _directory_refusal(cursor.parent, root) from exc
        if stat.S_ISLNK(mode):
            raise _symlink_refusal(cursor, root)
        if index < len(relative.parts) - 1 and not stat.S_ISDIR(mode):
            raise _directory_refusal(cursor, root)


def regular_or_missing(root: Path, path: Path) -> os.stat_result | None:
    safe_components(root, path)
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(info.st_mode):
        shown = display(path, root)
        raise PathRefusal(
            f"managed target is not a regular file: {shown}", kind="type", path=shown
        )
    return info


def ensure_directory(root: Path, path: Path, *, mode: int | None = None) -> None:
    """Create missing directories with an explicit mode, independent of umask."""

    safe_components(root, path)
    missing: list[Path] = []
    cursor = path
    while cursor != root:
        try:
            info = cursor.lstat()
        except FileNotFoundError:
            missing.append(cursor)
            cursor = cursor.parent
            continue
        if stat.S_ISLNK(info.st_mode):
            raise _symlink_refusal(cursor, root)
        if not stat.S_ISDIR(info.st_mode):
            raise _directory_refusal(cursor, root)
        break

    created_mode = _DIR_MODE if mode is None else mode
    for directory in reversed(missing):
        try:
            directory.mkdir(mode=created_mode)
        except FileExistsError:
            pass
        else:
            os.chmod(directory, created_mode)

    safe_components(root, path)
    if not stat.S_ISDIR(path.lstat().st_mode):
        raise _directory_refusal(path, root)
    if mode is not None:
        os.chmod(path, mode)


def fsync_directory(
    path: Path,
    *,
    fsync_fn: Callable[[int], None] | None = None,
    open_fn: Callable[..., int] | None = None,
) -> None:
    sync = _fsync if fsync_fn is None else fsync_fn
    open_file = _open if open_fn is None else open_fn
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    descriptor = open_file(path, flags)
    try:
        sync(descriptor)
    finally:
        _close(descriptor)


def atomic_write(
    root: Path,
    path: Path,
    data: bytes,
    mode: int,
    *,
    temp_prefix: str,
    owner: tuple[int, int] | None = None,
) -> bool:
    """Atomically replace a regular file and durably sync its parent.

    Returns ``False`` without touching the file when bytes, mode and (when
    ``owner`` is given as ``(uid, gid)``) ownership already match; otherwise
    the replacement is created with that owner.
    """

    info = regular_or_missing(root, path)
    if (
        info is not None
        and path.read_bytes() == data
        and stat.S_IMODE(info.st_mode) == mode
        and (owner is None or (info.st_uid, info.st_gid) == owner)
    ):
        return False
    ensure_directory(root, path.parent)
    descriptor, temporary_name = _mkstemp(prefix=temp_prefix, dir=path.parent)
    temporary = Path(temporary_name)
    descriptor_owned = True
    try:
        _fchmod(descriptor, mode)
        if owner is not None:
            _fchown(descriptor, owner[0], owner[1])
        stream = _fdopen(descriptor, "wb")
        descriptor_owned = False
        with stream:
            stream.write(data)
            stream.flush()
            _fsync(stream.fileno())
        regular_or_missing(root, path)
        _replace(temporary, path)
        fsync_directory(path.parent)
    except BaseException:
        if descriptor_owned:
            try:
                _close(descriptor)
            except OSError:
                pass
        try:
            _unlink(temporary)
        except OSError:
            pass
        raise
    return True


def unlink_and_sync(root: Path, path: Path) -> bool:
    """Unlink one regular managed file and sync its containing directory."""

    info = regular_or_missing(root, path)
    if info is None:
        return False
    _unlink(path)
    fsync_directory(path.parent)
    return True


def stale_temporaries(root: Path, directory: Path, prefix: str) -> list[Path]:
    """Return leftover temporary names below ``directory`` (never follows links)."""

    try:
        safe_components(root, directory)
        names = sorted(_listdir(directory))
    except (CliFailure, FileNotFoundError, NotADirectoryError):
        return []
    return [directory / name for name in names if name.startswith(prefix)]


def remove_temporaries(paths: list[Path]) -> None:
    """Best-effort removal of temporaries found while holding the writer lock."""

    for path in paths:
        try:
            _unlink(path)
        except OSError:
            pass


@contextmanager
def lock(
    root: Path,
    lock_path: Path,
    *,
    exclusive: bool,
    create: bool,
    mode: int = 0o600,
    fstat_fn: Callable[[int], os.stat_result] | None = None,
    open_fn: Callable[..., int] | None = None,
) -> Iterator[bool]:
    """Take a shared or exclusive flock without following a lock symlink.

    A lock that does not exist and is not to be created yields ``False`` and
    holds nothing, so a read-only caller never creates filesystem state.
    """

    info = regular_or_missing(root, lock_path)
    if info is None and not create:
        yield False
        return
    open_file = _open if open_fn is None else open_fn
    fstat = _fstat if fstat_fn is None else fstat_fn
    flags = (os.O_RDWR if exclusive else os.O_RDONLY) | getattr(os, "O_NOFOLLOW", 0)
    if create:
        flags |= os.O_CREAT
    try:
        descriptor = open_file(lock_path, flags, mode)
    except FileNotFoundError:
        if create:
            raise
        descriptor = None
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise _symlink_refusal(lock_path, root) from exc
        raise
    if descriptor is None:
        yield False
        return

    locked = False
    try:
        if not stat.S_ISREG(fstat(descriptor).st_mode):
            shown = display(lock_path, root)
            raise PathRefusal(
                f"managed target is not a regular file: {shown}", kind="type", path=shown
            )
        if exclusive:
            _fchmod(descriptor, mode)
        fcntl.flock(descriptor, fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
        locked = True
        yield True
    finally:
        if locked:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        _close(descriptor)
