"""P11-strength leftover-children guard and child-subreaper setup (TP-12)."""

from __future__ import annotations

import ctypes
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


def read_process(pid: int, proc_root: Path = DEFAULT_PROC_ROOT) -> ProcessRecord | None:
    """Read ``(pid, ppid, state, comm, starttime)``; None means the process vanished."""
    path = Path(proc_root) / str(pid) / "stat"
    try:
        stat = path.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return None
    except OSError as exc:
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
) -> dict[tuple[int, int], ProcessRecord]:
    """Current direct children, zombies included, keyed by pid and start time."""
    parent = os.getpid() if root_pid is None else root_pid
    try:
        entries = os.listdir(proc_root)
    except OSError as exc:
        raise ProcessGuardError(f"cannot enumerate {proc_root}: {exc}") from exc
    children = {}
    for entry in entries:
        if not _DIGITS.fullmatch(entry):
            continue
        record = read_process(int(entry), proc_root)
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
    clock: Callable[[], float] = time.monotonic,
    sleeper: Callable[[float], None] = time.sleep,
) -> dict[tuple[int, int], ProcessRecord]:
    """Wait at most ``settle_seconds`` for new children to disappear.

    A remaining zombie has already exited but needs the guard's reap, so it
    ends the wait immediately and is returned for reporting.
    """
    current = new_children(before, root_pid=root_pid, proc_root=proc_root)
    if not current or all(record.state == "Z" for record in current.values()):
        return current
    deadline = clock() + settle_seconds
    while True:
        remaining = deadline - clock()
        if remaining <= 0:
            return current
        sleeper(min(SETTLE_POLL_SECONDS, remaining))
        current = new_children(before, root_pid=root_pid, proc_root=proc_root)
        if not current or all(record.state == "Z" for record in current.values()):
            return current


def remove_if_still_ours(
    record: ProcessRecord,
    *,
    root_pid: int | None = None,
    proc_root: Path = DEFAULT_PROC_ROOT,
) -> str | None:
    """Kill/reap one pinned child; None means it vanished or was no longer ours."""
    parent = os.getpid() if root_pid is None else root_pid
    try:
        pidfd = os.pidfd_open(record.pid)
    except ProcessLookupError:
        return None
    try:
        current = read_process(record.pid, proc_root)
        if current is None or current.ppid != parent or current.start_time != record.start_time:
            return None
        zombie = current.state == "Z"
        if not zombie:
            try:
                signal.pidfd_send_signal(pidfd, signal.SIGKILL)
            except ProcessLookupError:  # pragma: no cover - vanished after the identity re-check
                pass
        info = os.waitid(os.P_PIDFD, pidfd, os.WEXITED)
        if info is None:  # pragma: no cover - blocking WEXITED does not return None
            raise ProcessGuardError(f"waitid(P_PIDFD) returned no status for pid {record.pid}")
        command = " ".join(record.command.split())
        if zombie:
            return f"pid {record.pid} comm {command} (zombie; reaped)"
        return f"pid {record.pid} comm {command} (alive; SIGKILL sent and reaped)"
    except (ChildProcessError, ProcessLookupError):  # pragma: no cover - concurrent reap/exit race
        return None
    finally:
        os.close(pidfd)


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
    ) -> None:
        self.root_pid = os.getpid() if root_pid is None else root_pid
        self.proc_root = Path(proc_root)
        self.settle_seconds = settle_seconds
        self.clock = clock
        self.sleeper = sleeper
        self.before = direct_children(self.root_pid, self.proc_root)

    def check(self) -> list[str]:
        current = wait_for_new_children(
            self.before,
            settle_seconds=self.settle_seconds,
            root_pid=self.root_pid,
            proc_root=self.proc_root,
            clock=self.clock,
            sleeper=self.sleeper,
        )
        leaked = []
        for record in sorted(current.values(), key=lambda item: item.pid):
            outcome = remove_if_still_ours(record, root_pid=self.root_pid, proc_root=self.proc_root)
            if outcome is not None:
                leaked.append(outcome)
        return leaked
