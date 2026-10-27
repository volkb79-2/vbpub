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
import posixpath
import re
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
# Every spelling of a systemd IO limit: `IOWeight=`, `-pIOWriteBandwidthMax=`, `--property=BlockIOWeight=`, `-p=IO...`, any case.
_IO_LIMIT_TOKEN = re.compile(r"^(?:-p=?|--property=)?(?:block)?io(?:device)?(?:weight|read|write|latency)[a-z]*=", re.IGNORECASE)


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
        if _IO_LIMIT_TOKEN.match(token):
            raise HostError(f"forbidden argument: {token}")
    if not is_allowed_argv(argv):
        raise HostError(f"argv outside the allowlist: {' '.join(argv[:3])} ...")


_FIO_FORBIDDEN = ("--exec_prerun", "--exec_postrun", "--daemonize", "--server", "--client", "--write_iolog", "--read_iolog", "--ioscheduler",
                  "--output", "--append-terse", "--status-interval", "--lockfile")
_FIO_ENGINES = ("--ioengine=libaio",)  # the only engine iobench builds; `exec` (and any other) runs or reaches outside the planned job (review A N-r3-4)


def _fio_allowed(a: list[str]) -> bool:
    keys = [t.split("=")[0] for t in a[1:]]
    if not all(t.startswith("--") for t in a[1:]) or len(a) < 2:
        return False
    if len(a) == 2 and keys[0] in ("--version", "--cmdhelp"):
        return True
    if keys.count("--filename") != 1:  # exactly one target: a repeated --filename= would add files the extent checks never saw
        return False
    if any(f.startswith(k) for k in keys for f in _FIO_FORBIDDEN):  # getopt accepts unambiguous abbreviations: `--out=` must not slip past `--output`
        return False
    return all(t in _FIO_ENGINES for t in a[1:] if t.split("=")[0] == "--ioengine")


_ALLOWED = {
    "fio": _fio_allowed,
    "blkdiscard": lambda a: a[1] == "-o" and a[3] == "-l" and len(a) == 6,
    "blkid": lambda a: a[1:4] == ["-p", "-o", "export"],
    "wipefs": lambda a: a[1] in ("-n", "--no-act"),
    "swapon": lambda a: a[1].startswith("--show"),
    "fallocate": lambda a: a[1] in ("-l", "-p"),
    "docker": lambda a: a[1] == "ps",
    "systemctl": lambda a: a[1] == "is-active" or a[1:3] == ["show", "-p"] or (a[1] == "stop" and len(a) == 3 and a[2].startswith("iobench-probe-")),
    "systemd-run": lambda a: any(t.startswith("RuntimeMaxSec=") for t in a),
}


def is_allowed_argv(argv: Sequence[str]) -> bool:
    check = _ALLOWED.get(posixpath.basename(argv[0]))
    try:
        return bool(check and check(list(argv)))
    except IndexError:  # an argv too short for its tool's shape is not an allowed argv
        return False



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
    def readlink(self, path: str) -> str: ...
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


def _die_with_parent(parent: int) -> None:
    """Child side of the spawn: SIGKILL this process when iobench itself dies (an orphan fio must not keep writing to the device)."""
    import ctypes
    ctypes.CDLL(None, use_errno=True).prctl(1, signal.SIGKILL)  # PR_SET_PDEATHSIG
    if os.getppid() != parent:  # iobench died between fork and the prctl: the signal will never come
        os._exit(1)


REAP_TIMEOUT_S = 5.0


def _reap(proc: "subprocess.Popen[str]") -> None:
    """Collect a killed command's output, bounded: a stalled device (D state) or a pipe-holding survivor must not hang the run (review A N-r3-2)."""
    try:
        proc.communicate(timeout=REAP_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        pass  # leave it to the kernel; the caller raises its own error


class _ProcHandle:
    def __init__(self, argv: Sequence[str]) -> None:
        self._out = tempfile.TemporaryFile("w+")
        self._err = tempfile.TemporaryFile("w+")
        # its own session/process group (the whole group is killed) and a parent-death signal (S8)
        self._p = subprocess.Popen(list(argv), stdout=self._out, stderr=self._err, text=True, start_new_session=True,
                                   preexec_fn=lambda parent=os.getpid(): _die_with_parent(parent))

    def poll(self) -> int | None:
        return self._p.poll()

    def wait(self) -> RunResult:
        rc = self._p.wait()
        self._out.seek(0)
        self._err.seek(0)
        return RunResult(rc, self._out.read(), self._err.read())

    def kill(self) -> None:
        if self._p.poll() is None:
            try:
                os.killpg(self._p.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        try:
            self._p.wait(timeout=5)  # reap; a process stuck in D state is left to the kernel rather than blocking the restore
        except subprocess.TimeoutExpired:
            pass


class RealHost:
    """Operating-system backed Host."""

    def __init__(self) -> None:
        self._lock_fd: Any = None
        self._signal: str | None = None
        self._escalated = False  # a second signal arrived while the first was still pending

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
        return self._run_polled(argv, timeout)

    def _run_polled(self, argv: Sequence[str], timeout: float | None) -> RunResult:
        """A blocking command in slices of 0.5 s: a SECOND signal (operator insistence) kills it and aborts instead of being deferred
        until a multi-second blkdiscard returns (the first signal is only a flag the run loop honours at its next tick)."""
        from .errors import Aborted
        proc = subprocess.Popen(list(argv), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
        waited = 0.0
        while True:
            try:
                out, err = proc.communicate(timeout=0.5)
                return RunResult(proc.returncode, out, err)
            except subprocess.TimeoutExpired:
                waited += 0.5
            if self._escalated:
                os.killpg(proc.pid, signal.SIGKILL)
                _reap(proc)
                raise Aborted("second signal during a blocking command")
            if timeout is not None and waited >= timeout:
                os.killpg(proc.pid, signal.SIGKILL)
                _reap(proc)
                raise subprocess.TimeoutExpired(list(argv), timeout)

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

    def readlink(self, path: str) -> str:
        return os.readlink(path)

    def devno(self, path: str) -> str | None:
        import stat as _stat
        try:
            st = os.stat(path)
        except (FileNotFoundError, NotADirectoryError, PermissionError):
            return None  # not a usable block device: the caller turns None into a refusal (exit 2), never a traceback
        if not _stat.S_ISBLK(st.st_mode):
            return None
        return f"{os.major(st.st_rdev)}:{os.minor(st.st_rdev)}"

    def makedirs(self, path: str) -> None:
        os.makedirs(path, exist_ok=True)

    def probe_odirect(self, directory: str) -> bool:
        probe = os.path.join(directory, f".iobench-odirect-probe-{os.getpid()}")
        try:  # O_EXCL | O_NOFOLLOW: never opens (and then unlinks) a file or link that was already there
            fd = os.open(probe, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | getattr(os, "O_DIRECT", 0), 0o600)
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
        probe = subprocess.run(["blkid", "-p", "-o", "export", target.path], capture_output=True, text=True, check=False)
        if probe.returncode not in (0, 2):  # 2 = no signature; anything else is a failed probe, not an empty device
            raise HostError(f"blkid failed (exit {probe.returncode}) while verifying {target.path}: {probe.stderr.strip()}")
        live = dict(line.split("=", 1) for line in probe.stdout.splitlines() if "=" in line)
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
        if target.kind == "device":
            flags |= os.O_EXCL  # the kernel refuses (EBUSY) a device that is mounted, swap-active or claimed by md/dm
        try:
            fd = os.open(target.path, flags)
        except OSError as exc:  # EBUSY: the kernel's exclusive open found the device mounted, swap-active or claimed (md/dm)
            raise HostError(f"{target.path} cannot be opened exclusively ({exc.strerror}): it is mounted, swap-active or claimed by md/dm") from exc
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
        from . import devio  # late: devio imports this module
        self._verify_live(target)
        self._check_extent(target, offset, length)
        devio.assert_writer_bound(argv, target, offset, length)  # the argv writes exactly the authorised extent
        check_argv(argv)
        return _ProcHandle(argv)

    def discard_range(self, argv: Sequence[str], target: TargetRef, offset: int, length: int) -> RunResult:
        from . import devio
        self._verify_live(target)
        self._check_extent(target, offset, length)
        devio.assert_discard_bound(argv, target, offset, length)
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
            if self._signal is not None:
                self._escalated = True
            self._signal = signal.Signals(signum).name
        for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            signal.signal(sig, handler)
