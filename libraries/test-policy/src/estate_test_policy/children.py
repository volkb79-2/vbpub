"""P11-strength leftover-children guard and child-subreaper setup (TP-12)."""

from __future__ import annotations

import ctypes
import errno
import os
import re
import signal
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

DEFAULT_PROC_ROOT = Path("/proc")
SETTLE_POLL_SECONDS = 0.05
REAP_GRACE_SECONDS = 30.0
REAP_POLL_SECONDS = 0.01
MAX_CLEANUP_PASSES = 10
PR_SET_CHILD_SUBREAPER = 36
PR_GET_CHILD_SUBREAPER = 37
_DIGITS = re.compile(r"[0-9]+$")


class ProcessGuardError(RuntimeError):
    """The process facts needed by the guard are unavailable or malformed."""


@dataclass(frozen=True)
class ProcessRecord:
    pid: int
    ppid: int
    state: str
    command: str
    start_time: int

    @property
    def identity(self) -> tuple[int, int]:
        return self.pid, self.start_time


def _open_pidfd(pid: int) -> int:
    return os.pidfd_open(pid)


@dataclass(frozen=True)
class PidfdOps:
    """PIDFD open/close seam; tests can use fake descriptors without patching ``os.close``."""

    open: Callable[[int], int] = _open_pidfd
    close: Callable[[int], None] = os.close


_SYSTEM_PIDFD_OPS = PidfdOps()


def is_linux() -> bool:
    return sys.platform.startswith("linux")


def _prctl(option: int, argument: int = 0, pointer: ctypes.c_int | None = None) -> int:
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        function = libc.prctl
    except (AttributeError, OSError) as exc:
        raise ProcessGuardError(f"libc does not expose prctl: {exc}") from exc
    if pointer is None:
        result = function(option, argument, 0, 0, 0)
    else:
        result = function(option, ctypes.byref(pointer), 0, 0, 0)
    if result != 0:
        error = ctypes.get_errno()
        raise ProcessGuardError(f"prctl({option}) failed: {os.strerror(error)}")
    return result


def enable_subreaper() -> bool:
    """Enable orphan adoption; return False on non-Linux without touching libc."""
    if not is_linux():
        return False
    _prctl(PR_SET_CHILD_SUBREAPER, 1)
    return True


def subreaper_enabled() -> bool:
    """Read the process's current child-subreaper setting (Linux only)."""
    if not is_linux():
        return False
    current = ctypes.c_int()
    _prctl(PR_GET_CHILD_SUBREAPER, pointer=current)
    return bool(current.value)


def _read_stat(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def read_process(
    pid: int,
    proc_root: Path = DEFAULT_PROC_ROOT,
    *,
    reader: Callable[[Path], str] = _read_stat,
) -> ProcessRecord | None:
    """Read process identity; None means its stat path is absent or the process vanished."""
    path = Path(proc_root) / str(pid) / "stat"
    try:
        stat = reader(path)
    except (FileNotFoundError, ProcessLookupError):
        return None
    except OSError as exc:
        if exc.errno == errno.EACCES:
            # Under hidepid=1, a credential-changed child can be unreadable; procfs alone cannot establish ownership.
            try:
                os.waitid(os.P_PID, pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
            except OSError as ownership_error:
                if ownership_error.errno in (errno.ECHILD, errno.ESRCH):
                    return None
                raise ProcessGuardError(
                    f"cannot verify whether process {pid} is our child after EACCES reading {path}: {ownership_error}"
                ) from ownership_error
            raise ProcessGuardError(
                f"cannot read process stat file {path}: {exc}; waitid confirms pid {pid} is our child, "
                "so refusing to skip it (the child may have changed credentials)"
            ) from exc
        raise ProcessGuardError(f"cannot read process stat file {path}: {exc}") from exc
    open_paren = stat.find("(")
    close_paren = stat.rfind(")")
    if open_paren < 0 or close_paren <= open_paren:
        raise ProcessGuardError(f"malformed process stat file {path}")
    fields = stat[close_paren + 1 :].split()
    if len(fields) < 20:
        raise ProcessGuardError(f"malformed process stat file {path}")
    try:
        recorded_pid = int(stat[:open_paren].strip())
        if recorded_pid != pid:
            raise ValueError(f"stat pid {recorded_pid} does not match directory pid {pid}")
        ppid = int(fields[1])
        start_time = int(fields[19])
    except ValueError as exc:
        raise ProcessGuardError(f"malformed process identity in {path}") from exc
    return ProcessRecord(pid, ppid, fields[0], stat[open_paren + 1 : close_paren], start_time)


def direct_children(
    root_pid: int | None = None,
    proc_root: Path = DEFAULT_PROC_ROOT,
    *,
    reader: Callable[[Path], str] = _read_stat,
) -> dict[tuple[int, int], ProcessRecord]:
    """Visible direct children, zombies included, keyed by pid and start time.

    PIDs omitted from procfs enumeration, as with hidepid=2 for another user's process, cannot be detected here.
    """
    parent = os.getpid() if root_pid is None else root_pid
    try:
        entries = os.listdir(proc_root)
    except OSError as exc:
        raise ProcessGuardError(f"cannot enumerate {proc_root}: {exc}") from exc
    children = {}
    for entry in entries:
        if not _DIGITS.fullmatch(entry):
            continue
        record = read_process(int(entry), proc_root, reader=reader)
        if record is not None and record.ppid == parent:
            children[record.identity] = record
    return children


def new_children(
    before: dict[tuple[int, int], ProcessRecord],
    *,
    root_pid: int | None = None,
    proc_root: Path = DEFAULT_PROC_ROOT,
) -> dict[tuple[int, int], ProcessRecord]:
    """Direct children whose ``(pid, starttime)`` identity was absent at setup."""
    return {
        identity: record
        for identity, record in direct_children(root_pid, proc_root).items()
        if identity not in before
    }


def wait_for_new_children(
    before: dict[tuple[int, int], ProcessRecord],
    *,
    settle_seconds: float,
    root_pid: int | None = None,
    proc_root: Path = DEFAULT_PROC_ROOT,
    ignore: set[tuple[int, int]] | frozenset[tuple[int, int]] = frozenset(),
    clock: Callable[[], float] = time.monotonic,
    sleeper: Callable[[float], None] = time.sleep,
) -> dict[tuple[int, int], ProcessRecord]:
    """Wait at most ``settle_seconds`` for new children to disappear.

    A remaining zombie has already exited but needs the guard's reap, so it
    ends the wait immediately and is returned for reporting.
    """
    current = {
        identity: record
        for identity, record in new_children(before, root_pid=root_pid, proc_root=proc_root).items()
        if identity not in ignore
    }
    if not current or all(record.state == "Z" for record in current.values()):
        return current
    deadline = clock() + settle_seconds
    while True:
        remaining = deadline - clock()
        if remaining <= 0:
            return current
        sleeper(min(SETTLE_POLL_SECONDS, remaining))
        current = {
            identity: record
            for identity, record in new_children(before, root_pid=root_pid, proc_root=proc_root).items()
            if identity not in ignore
        }
        if not current or all(record.state == "Z" for record in current.values()):
            return current


def remove_if_still_ours(
    record: ProcessRecord,
    *,
    root_pid: int | None = None,
    proc_root: Path = DEFAULT_PROC_ROOT,
    reap_timeout: float = REAP_GRACE_SECONDS,
    clock: Callable[[], float] = time.monotonic,
    sleeper: Callable[[float], None] = time.sleep,
    waiter: Callable[[int, int, int], os.siginfo_t | None] = os.waitid,
    reader: Callable[[Path], str] = _read_stat,
    pidfd_ops: PidfdOps = _SYSTEM_PIDFD_OPS,
) -> str | None:
    """Kill/reap one pinned child; None means it vanished or was no longer ours."""
    parent = os.getpid() if root_pid is None else root_pid
    try:
        pidfd = pidfd_ops.open(record.pid)
    except ProcessLookupError:
        return None
    try:
        current = read_process(record.pid, proc_root, reader=reader)
        if current is None or current.ppid != parent or current.start_time != record.start_time:
            return None
        zombie = current.state == "Z"
        if not zombie:
            try:
                signal.pidfd_send_signal(pidfd, signal.SIGKILL)
            except ProcessLookupError:  # pragma: no cover - vanished after the identity re-check
                pass
        command = " ".join(record.command.split())
        deadline = clock() + reap_timeout
        while True:
            info = waiter(os.P_PIDFD, pidfd, os.WEXITED | os.WNOHANG)
            if info is not None:
                break
            remaining = deadline - clock()
            if remaining <= 0:
                kind = "unreaped after SIGKILL" if not zombie else "unreaped zombie"
                return f"pid {record.pid} comm {command} ({kind})"
            sleeper(min(REAP_POLL_SECONDS, remaining))
        if zombie:
            return f"pid {record.pid} comm {command} (zombie; reaped)"
        return f"pid {record.pid} comm {command} (alive; SIGKILL sent and reaped)"
    except (ChildProcessError, ProcessLookupError):  # pragma: no cover - concurrent reap/exit race
        return None
    finally:
        pidfd_ops.close(pidfd)


def reap_new_zombies(
    before: dict[tuple[int, int], ProcessRecord],
    *,
    root_pid: int | None = None,
    proc_root: Path = DEFAULT_PROC_ROOT,
    reader: Callable[[Path], str] = _read_stat,
    waiter: Callable[[int, int, int], os.siginfo_t | None] = os.waitid,
    pidfd_ops: PidfdOps = _SYSTEM_PIDFD_OPS,
) -> int:
    """Reap only new zombie direct children; live allowlisted children are untouched."""
    parent = os.getpid() if root_pid is None else root_pid
    reaped = 0
    current = direct_children(parent, proc_root, reader=reader)
    for identity, record in current.items():
        if identity in before or record.state != "Z":
            continue
        try:
            pidfd = pidfd_ops.open(record.pid)
        except ProcessLookupError:
            continue
        try:
            latest = read_process(record.pid, proc_root, reader=reader)
            if latest is None or latest.ppid != parent or latest.start_time != record.start_time or latest.state != "Z":
                continue
            if waiter(os.P_PIDFD, pidfd, os.WEXITED | os.WNOHANG) is not None:
                reaped += 1
        except (ChildProcessError, ProcessLookupError):  # pragma: no cover - concurrent reap/exit race
            continue
        finally:
            pidfd_ops.close(pidfd)
    return reaped


class ChildGuard:
    """Remember direct children at setup, then clean up and report every new one."""

    def __init__(
        self,
        *,
        root_pid: int | None = None,
        proc_root: Path = DEFAULT_PROC_ROOT,
        settle_seconds: float,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
        pidfd_ops: PidfdOps = _SYSTEM_PIDFD_OPS,
    ) -> None:
        self.root_pid = os.getpid() if root_pid is None else root_pid
        self.proc_root = Path(proc_root)
        self.settle_seconds = settle_seconds
        self.clock = clock
        self.sleeper = sleeper
        self.pidfd_ops = pidfd_ops
        self.before = direct_children(self.root_pid, self.proc_root)

    def check(self) -> list[str]:
        leaked = []
        unreaped: set[tuple[int, int]] = set()
        for _pass in range(MAX_CLEANUP_PASSES):
            current = wait_for_new_children(
                self.before,
                settle_seconds=self.settle_seconds,
                root_pid=self.root_pid,
                proc_root=self.proc_root,
                ignore=unreaped,
                clock=self.clock,
                sleeper=self.sleeper,
            )
            current = {identity: record for identity, record in current.items() if identity not in unreaped}
            if not current:
                return leaked
            for record in sorted(current.values(), key=lambda item: item.pid):
                outcome = remove_if_still_ours(
                    record,
                    root_pid=self.root_pid,
                    proc_root=self.proc_root,
                    reap_timeout=self.settle_seconds + REAP_GRACE_SECONDS,
                    clock=self.clock,
                    sleeper=self.sleeper,
                    pidfd_ops=self.pidfd_ops,
                )
                if outcome is not None:
                    leaked.append(outcome)
                    if "unreaped" in outcome:
                        unreaped.add(record.identity)
        remaining = new_children(self.before, root_pid=self.root_pid, proc_root=self.proc_root)
        details = "; ".join(
            f"pid {record.pid} comm {record.command}"
            for record in sorted(remaining.values(), key=lambda item: item.pid)
            if record.identity not in unreaped
        )
        if details:
            leaked.append(f"child cleanup reached the {MAX_CLEANUP_PASSES}-pass bound; remaining new children: {details}")
        return leaked
