"""File-system hygiene: identity, directory checks, no-follow opens verified through the open descriptor, advisory locks.

Rules (each is tested):
  * never run as root unless explicitly allowed (config `allow_root` / `--allow-root`); as root the scratch dir must be root-owned 0700;
  * state/journal/scratch directories must be real directories (not symlinks) owned by the running uid, not group/world writable;
  * every file is opened O_NOFOLLOW (O_EXCL when it is created) and then verified through the descriptor: regular file, owned by the
    running uid, no group/world write (0600 for state, lock and scratch files), and still the file the path names;
  * a file is never re-opened by name: the scratch file is used through the one descriptor, removal is by name only after the
    path is checked to still name that inode.
"""

from __future__ import annotations

import fcntl
import json
import os
import stat

NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)


class UnsafePath(Exception):
    pass


def euid() -> int:
    return os.geteuid()


def check_identity(allow_root: bool) -> None:
    if euid() == 0 and not allow_root:
        raise UnsafePath("refusing to run as root (set allow_root = true or pass --allow-root to override)")


def ensure_dir(path: str, *, strict: bool = False) -> None:
    """Create (0700) if missing, then require: a real directory, owned by us, not group/world writable (strict: 0700)."""

    try:
        lst = os.lstat(path)
    except FileNotFoundError:
        parent = os.path.dirname(path.rstrip("/")) or "/"
        if not os.path.isdir(parent):
            os.makedirs(parent, mode=0o755, exist_ok=True)
        try:
            os.mkdir(path, 0o700)
        except FileExistsError:
            pass
        lst = os.lstat(path)
    if stat.S_ISLNK(lst.st_mode) or not stat.S_ISDIR(lst.st_mode):
        raise UnsafePath(f"{path}: not a real directory (symlink or other file type)")
    if lst.st_uid != euid():
        raise UnsafePath(f"{path}: not owned by the running user (uid {lst.st_uid} != {euid()})")
    if lst.st_mode & 0o022:
        raise UnsafePath(f"{path}: group/world writable (mode {oct(lst.st_mode & 0o7777)})")
    if (strict or euid() == 0) and lst.st_mode & 0o077:
        raise UnsafePath(f"{path}: must be mode 0700 (is {oct(lst.st_mode & 0o7777)})")


def verify_fd(fd: int, path: str | None = None, *, private: bool = True) -> os.stat_result:
    st = os.fstat(fd)
    if not stat.S_ISREG(st.st_mode):
        raise UnsafePath(f"{path or fd}: not a regular file")
    if st.st_uid != euid():
        raise UnsafePath(f"{path or fd}: not owned by the running user")
    bad = st.st_mode & (0o077 if private else 0o022)
    if bad:
        raise UnsafePath(f"{path or fd}: unsafe mode {oct(st.st_mode & 0o7777)}")
    if path is not None:
        try:
            lst = os.lstat(path)
        except OSError as exc:
            raise UnsafePath(f"{path}: vanished after open ({exc.strerror})")
        if (lst.st_dev, lst.st_ino) != (st.st_dev, st.st_ino) or stat.S_ISLNK(lst.st_mode):
            raise UnsafePath(f"{path}: the name no longer refers to the file that was opened (swapped?)")
    return st


def open_file(path: str, flags: int, mode: int = 0o600, *, private: bool = True) -> int:
    """open(2) with O_NOFOLLOW|O_CLOEXEC, then verify through the descriptor. Closes the fd on any failure."""

    try:
        fd = os.open(path, flags | NOFOLLOW | os.O_CLOEXEC, mode)
    except OSError as exc:
        if exc.errno == 40:  # ELOOP: a symlink where a file should be
            raise UnsafePath(f"{path}: is a symlink")
        raise
    try:
        verify_fd(fd, path, private=private)
    except BaseException:
        os.close(fd)
        raise
    return fd


def unlink_if_same(path: str, fd: int) -> bool:
    """Remove `path` only if it still names the inode behind `fd`."""

    try:
        lst = os.lstat(path)
        st = os.fstat(fd)
    except OSError:
        return False
    if (lst.st_dev, lst.st_ino) == (st.st_dev, st.st_ino) and not stat.S_ISLNK(lst.st_mode):
        os.unlink(path)
        return True
    return False


class FileLock:
    """Advisory flock on a private lock file (opened no-follow, verified)."""

    def __init__(self, path: str):
        self.path = path
        self.fd = -1

    def acquire(self, blocking: bool = False, timeout_s: float = 0.0, sleep=None) -> bool:
        ensure_dir(os.path.dirname(self.path))
        fd = open_file(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        import time
        deadline = time.monotonic() + timeout_s
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | (0 if blocking and timeout_s <= 0 else fcntl.LOCK_NB))
                self.fd = fd
                return True
            except OSError:
                if not (blocking and timeout_s > 0) or time.monotonic() >= deadline:
                    os.close(fd)
                    return False
                time.sleep(0.05)

    def release(self) -> None:
        if self.fd >= 0:
            try:
                fcntl.flock(self.fd, fcntl.LOCK_UN)
            finally:
                os.close(self.fd)
                self.fd = -1

    def __enter__(self):
        if not self.acquire(blocking=True, timeout_s=30.0):
            raise UnsafePath(f"{self.path}: lock busy")
        return self

    def __exit__(self, *exc) -> None:
        self.release()


# --- small JSON files (state, aggregates), atomic and race-free ---------------------------------------------


def load_json(path: str) -> dict:
    try:
        fd = open_file(path, os.O_RDONLY)
    except FileNotFoundError:
        return {}
    try:
        with os.fdopen(fd, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def save_json(path: str, data: dict, token_hex) -> None:
    """Write via a random-named O_EXCL|O_NOFOLLOW temp file in the same (checked) directory, fsync, atomic replace."""

    d = os.path.dirname(path)
    ensure_dir(d)
    tmp = os.path.join(d, f".{os.path.basename(path)}.{token_hex(6)}.tmp")
    fd = open_file(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        payload = json.dumps(data, sort_keys=True, separators=(",", ":")).encode()
        view = memoryview(payload)
        while view:
            n = os.write(fd, view)
            view = view[n:]
        os.fsync(fd)
    except BaseException:
        unlink_if_same(tmp, fd)  # best effort, and only if the random name still names our inode
        os.close(fd)
        raise
    os.close(fd)
    os.replace(tmp, path)
