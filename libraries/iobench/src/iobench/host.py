"""The Host protocol (IOB-ARC-02) and the real host.

Every side effect of iobench goes through a ``Host``: files, sysfs/sysctl,
subprocesses, clock, sleep, environment, signals and unique tokens.  This module
is the only core module allowed to touch the operating system.  It is exercised
by the FakeHost-parity tests and by the r1002 acceptance probe (IOB-ACC-01), and
is therefore the one core module excluded from the coverage gate.
"""
from __future__ import annotations

import fcntl
import os
import secrets
import signal
import subprocess
import tempfile
import time
from dataclasses import dataclass
from typing import Any, Protocol, Sequence

TAIL_BYTES = 1024 * 1024


class HostError(RuntimeError):
    """A Host refused or failed an operation (never a measurement result)."""


@dataclass(frozen=True)
class RunResult:
    returncode: int
    stdout: str
    stderr: str


@dataclass(frozen=True)
class StatInfo:
    size: int
    inode: int
    nlink: int
    is_dir: bool
    is_file: bool
    is_symlink: bool


@dataclass(frozen=True)
class TargetRef:
    """The verified identity a writer must still match on the live device."""

    kind: str  # "device" | "file"
    path: str
    size_bytes: int
    devno: str | None = None
    partuuid: str | None = None
    serial: str | None = None
    fs_uuid: str | None = None
    inode: int | None = None


class Handle(Protocol):
    def poll(self) -> int | None: ...
    def wait(self) -> RunResult: ...
    def kill(self) -> None: ...


FORBIDDEN_ARGV_TOKENS = ("set-property", "daemon-reload")
FORBIDDEN_ARGV0 = ("mkfs", "mount", "umount", "fstrim", "dd")


def check_argv(argv: Sequence[str]) -> None:
    """Refuse argv that could change an io.max-style limit or a filesystem."""
    base = os.path.basename(argv[0])
    if base.startswith("mkfs") or base in FORBIDDEN_ARGV0:
        raise HostError(f"forbidden command: {base}")
    if base == "wipefs" and "-n" not in argv and "--no-act" not in argv:
        raise HostError("forbidden command: wipefs without -n")
    for token in argv:
        if token in FORBIDDEN_ARGV_TOKENS:
            raise HostError(f"forbidden argument: {token}")
        if token.startswith(("IO", "-pIO")) and ("Max=" in token or "Weight=" in token):
            raise HostError(f"forbidden argument: {token}")


def check_sysfs_path(path: str) -> None:
    if path.endswith("io.max"):
        raise HostError(f"io.max is never written: {path}")


class Host(Protocol):
    def now(self) -> float: ...
    def sleep(self, seconds: float) -> None: ...
    def token(self) -> str: ...
    def getenv(self, name: str) -> str | None: ...
    def run(self, argv: Sequence[str], timeout: float | None = None) -> RunResult: ...
    def spawn(self, argv: Sequence[str]) -> Handle: ...
    def read_text(self, path: str) -> str: ...
    def exists(self, path: str) -> bool: ...
    def listdir(self, path: str) -> list[str]: ...
    def stat(self, path: str) -> StatInfo | None: ...
    def statfs(self, path: str) -> tuple[int, int]: ...
    def realpath(self, path: str) -> str: ...
    def devno(self, path: str) -> str | None: ...
    def makedirs(self, path: str) -> None: ...
    def probe_odirect(self, directory: str) -> bool: ...
    def write_atomic(self, path: str, text: str, mode: int = 0o644) -> None: ...
    def append_line(self, path: str, line: str) -> None: ...
    def remove(self, path: str) -> None: ...
    def write_sysfs(self, path: str, value: str) -> None: ...
    def write_marker(self, target: TargetRef, text: str) -> None: ...
    def read_marker(self, target: TargetRef) -> str: ...
    def write_range(self, argv: Sequence[str], target: TargetRef, offset: int, length: int) -> Handle: ...
    def discard_range(self, argv: Sequence[str], target: TargetRef, offset: int, length: int) -> RunResult: ...
    def lock(self, path: str) -> bool: ...
    def unlock(self) -> None: ...
    def signal_pending(self) -> str | None: ...
    def install_signals(self) -> None: ...


class _ProcHandle:
    def __init__(self, argv: Sequence[str]) -> None:
        self._out = tempfile.TemporaryFile("w+")
        self._err = tempfile.TemporaryFile("w+")
        self._p = subprocess.Popen(list(argv), stdout=self._out, stderr=self._err, text=True)

    def poll(self) -> int | None:
        return self._p.poll()

    def wait(self) -> RunResult:
        rc = self._p.wait()
        self._out.seek(0)
        self._err.seek(0)
        return RunResult(rc, self._out.read(), self._err.read())

    def kill(self) -> None:
        if self._p.poll() is None:
            self._p.kill()


class RealHost:
    """Operating-system backed Host."""

    def __init__(self) -> None:
        self._lock_fd: Any = None
        self._signal: str | None = None

    def now(self) -> float:
        return time.time()

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)

    def token(self) -> str:
        return secrets.token_hex(2)

    def getenv(self, name: str) -> str | None:
        return os.environ.get(name)

    def run(self, argv: Sequence[str], timeout: float | None = None) -> RunResult:
        check_argv(argv)
        p = subprocess.run(list(argv), capture_output=True, text=True, timeout=timeout, check=False)
        return RunResult(p.returncode, p.stdout, p.stderr)

    def spawn(self, argv: Sequence[str]) -> Handle:
        check_argv(argv)
        return _ProcHandle(argv)

    def read_text(self, path: str) -> str:
        with open(path, encoding="utf-8") as fh:
            return fh.read()

    def exists(self, path: str) -> bool:
        return os.path.lexists(path)

    def listdir(self, path: str) -> list[str]:
        return sorted(os.listdir(path))

    def stat(self, path: str) -> StatInfo | None:
        try:
            st = os.lstat(path)
        except FileNotFoundError:
            return None
        import stat as _stat
        return StatInfo(st.st_size, st.st_ino, st.st_nlink, _stat.S_ISDIR(st.st_mode),
                        _stat.S_ISREG(st.st_mode), _stat.S_ISLNK(st.st_mode))

    def statfs(self, path: str) -> tuple[int, int]:
        v = os.statvfs(path)
        return v.f_blocks * v.f_frsize, v.f_bavail * v.f_frsize

    def realpath(self, path: str) -> str:
        return os.path.realpath(path)

    def devno(self, path: str) -> str | None:
        import stat as _stat
        st = os.stat(path)
        if not _stat.S_ISBLK(st.st_mode):
            return None
        return f"{os.major(st.st_rdev)}:{os.minor(st.st_rdev)}"

    def makedirs(self, path: str) -> None:
        os.makedirs(path, exist_ok=True)

    def probe_odirect(self, directory: str) -> bool:
        probe = os.path.join(directory, ".iobench-odirect-probe")
        try:
            fd = os.open(probe, os.O_WRONLY | os.O_CREAT | getattr(os, "O_DIRECT", 0), 0o600)
        except OSError:
            return False
        os.close(fd)
        os.unlink(probe)
        return True

    def write_atomic(self, path: str, text: str, mode: int = 0o644) -> None:
        directory = os.path.dirname(path) or "."
        fd, tmp = tempfile.mkstemp(dir=directory, prefix=".iobench-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(text)
                fh.flush()
                os.fsync(fh.fileno())
            os.chmod(tmp, mode)
            os.replace(tmp, path)
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise
        dfd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)

    def append_line(self, path: str, line: str) -> None:
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
        try:
            os.write(fd, (line.rstrip("\n") + "\n").encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)

    def remove(self, path: str) -> None:
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass

    def write_sysfs(self, path: str, value: str) -> None:
        check_sysfs_path(path)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(value)

    # -- target access ---------------------------------------------------
    def _verify_live(self, target: TargetRef) -> None:
        st = os.stat(target.path)
        if target.kind == "file":
            if st.st_ino != target.inode or st.st_size != target.size_bytes:
                raise HostError(f"file target identity changed: {target.path}")
            return
        devno = f"{os.major(st.st_rdev)}:{os.minor(st.st_rdev)}"
        if devno != target.devno:
            raise HostError(f"device number changed: {target.path} {devno} != {target.devno}")
        with open(f"/sys/dev/block/{devno}/size", encoding="utf-8") as fh:
            live_size = int(fh.read()) * 512
        if live_size != target.size_bytes:
            raise HostError(f"device size changed: {target.path}")
        out = subprocess.run(["blkid", "-p", "-o", "export", target.path], capture_output=True, text=True, check=False).stdout
        live = dict(line.split("=", 1) for line in out.splitlines() if "=" in line)
        if target.partuuid is not None and live.get("PART_ENTRY_UUID") != target.partuuid:
            raise HostError(f"PARTUUID changed: {target.path}")

    @staticmethod
    def _check_extent(target: TargetRef, offset: int, length: int) -> None:
        if offset < 0 or length <= 0 or offset + length > target.size_bytes - TAIL_BYTES:
            raise HostError(f"extent [{offset}, {offset + length}) overlaps the reserved tail or the end of {target.path}")

    def write_marker(self, target: TargetRef, text: str) -> None:
        self._verify_live(target)
        data = text.encode("utf-8").ljust(4096, b"\0")
        flags = os.O_WRONLY | getattr(os, "O_DIRECT", 0)
        fd = os.open(target.path, flags)
        try:
            import mmap
            buf = mmap.mmap(-1, 4096)
            buf.write(data)
            os.pwritev(fd, [buf], target.size_bytes - TAIL_BYTES)
            os.fsync(fd)
        finally:
            os.close(fd)

    def read_marker(self, target: TargetRef) -> str:
        with open(target.path, "rb") as fh:
            fh.seek(target.size_bytes - TAIL_BYTES)
            return fh.read(4096).split(b"\0", 1)[0].decode("utf-8", "replace")

    def write_range(self, argv: Sequence[str], target: TargetRef, offset: int, length: int) -> Handle:
        self._verify_live(target)
        self._check_extent(target, offset, length)
        check_argv(argv)
        return _ProcHandle(argv)

    def discard_range(self, argv: Sequence[str], target: TargetRef, offset: int, length: int) -> RunResult:
        self._verify_live(target)
        self._check_extent(target, offset, length)
        return self.run(argv)

    # -- single instance and signals --------------------------------------
    def lock(self, path: str) -> bool:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self._lock_fd = open(path, "w")
        try:
            fcntl.flock(self._lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self._lock_fd.close()
            self._lock_fd = None
            return False
        return True

    def unlock(self) -> None:
        if self._lock_fd is not None:
            self._lock_fd.close()
            self._lock_fd = None

    def signal_pending(self) -> str | None:
        return self._signal

    def install_signals(self) -> None:
        def handler(signum: int, _frame: Any) -> None:
            self._signal = signal.Signals(signum).name
        for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            signal.signal(sig, handler)
