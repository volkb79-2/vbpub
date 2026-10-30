"""Process-tree inspection and leak reporting for CMRU's test suite.

The process-tree API is imported by pytest on every platform, but inspection is
available only on Linux with readable ``/proc/<pid>/task/<tid>/children`` files.
An unavailable view is an error: silently disabling the guard would make its
clean-tree result meaningless.
"""

from __future__ import annotations

import ctypes
import errno
import os
import platform
import signal
import time
from dataclasses import dataclass
from pathlib import Path

_PR_SET_CHILD_SUBREAPER = 36
_PR_GET_CHILD_SUBREAPER = 37
_PROC_ROOT = Path("/proc")


class ProcessTreeUnavailable(RuntimeError):
    """The host cannot provide the process-tree evidence the guard requires."""


@dataclass(frozen=True)
class ProcessRecord:
    pid: int
    ppid: int
    state: str
    command: str
    start_time: int


class DescendantLeakError(RuntimeError):
    """A test left a new process descendant alive at its teardown boundary."""

    def __init__(
        self,
        detected: dict[int, ProcessRecord],
        survivors: dict[int, ProcessRecord],
    ) -> None:
        self.detected = dict(detected)
        self.survivors = dict(survivors)
        details = "; ".join(
            f"pid={record.pid} ppid={record.ppid} state={record.state} "
            f"command={record.command!r}"
            for record in sorted(detected.values(), key=lambda item: item.pid)
        )
        if survivors:
            outcome = f"{len(survivors)} survivor(s) remain after cleanup"
        else:
            outcome = "all detected descendants were killed and reaped"
        super().__init__(
            f"detected new process descendant(s): {details}; {outcome}"
        )


def _require_linux() -> None:
    if platform.system() != "Linux":
        raise ProcessTreeUnavailable(
            "process-tree inspection requires Linux /proc children support"
        )


def _read_children(
    pid: int,
    tid: int,
    proc_root: Path,
) -> list[int]:
    process_dir = proc_root / str(pid)
    task_dir = process_dir / "task" / str(tid)
    children_path = task_dir / "children"
    try:
        contents = children_path.read_text(encoding="ascii")
    except FileNotFoundError as exc:
        if not process_dir.exists() or not task_dir.exists():
            return []
        raise ProcessTreeUnavailable(
            f"Linux /proc children support is unavailable at {children_path}"
        ) from exc
    except OSError as exc:
        raise ProcessTreeUnavailable(
            f"cannot read Linux /proc children file {children_path}: {exc}"
        ) from exc

    try:
        return [int(token) for token in contents.split()]
    except ValueError as exc:
        raise ProcessTreeUnavailable(
            f"malformed Linux /proc children file {children_path}"
        ) from exc


def _task_ids(pid: int, proc_root: Path, *, root_pid: int) -> list[int]:
    process_dir = proc_root / str(pid)
    task_dir = process_dir / "task"
    try:
        entries = list(task_dir.iterdir())
    except FileNotFoundError as exc:
        if pid != root_pid and not process_dir.exists():
            return []
        raise ProcessTreeUnavailable(
            f"cannot enumerate Linux /proc task directory for children: {task_dir}"
        ) from exc
    except OSError as exc:
        raise ProcessTreeUnavailable(
            f"cannot enumerate Linux /proc task directory {task_dir}: {exc}"
        ) from exc

    task_ids: list[int] = []
    try:
        task_ids = [int(entry.name) for entry in entries]
    except ValueError as exc:
        raise ProcessTreeUnavailable(
            f"malformed task entry in Linux /proc directory {task_dir}"
        ) from exc
    if not task_ids and (pid == root_pid or process_dir.exists()):
        raise ProcessTreeUnavailable(
            f"Linux /proc task directory has no readable children files: {task_dir}"
        )
    return task_ids


def _read_process_record(pid: int, proc_root: Path) -> ProcessRecord | None:
    stat_path = proc_root / str(pid) / "stat"
    try:
        stat = stat_path.read_text(encoding="ascii")
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise ProcessTreeUnavailable(
            f"cannot read Linux process stat file {stat_path}: {exc}"
        ) from exc

    open_paren = stat.find("(")
    close_paren = stat.rfind(")")
    fields = stat[close_paren + 2 :].split() if close_paren >= 0 else []
    if open_paren < 0 or close_paren <= open_paren or len(fields) <= 19:
        raise ProcessTreeUnavailable(f"malformed Linux process stat file {stat_path}")
    try:
        ppid = int(fields[1])
        start_time = int(fields[19])
    except ValueError as exc:
        raise ProcessTreeUnavailable(
            f"malformed Linux process identity in {stat_path}"
        ) from exc
    return ProcessRecord(
        pid=pid,
        ppid=ppid,
        state=fields[0],
        command=stat[open_paren + 1 : close_paren],
        start_time=start_time,
    )


def descendants(root_pid: int, *, proc_root: Path = _PROC_ROOT) -> dict[int, ProcessRecord]:
    """Return every live descendant, inspecting children of every process thread."""
    _require_linux()
    proc_root = Path(proc_root)
    # Probe the guarded process's main-thread children file on every scan. A
    # Linux kernel without CONFIG_PROC_CHILDREN, or a missing /proc mount,
    # refuses.
    _read_children(root_pid, root_pid, proc_root)

    result: dict[int, ProcessRecord] = {}
    pending = [root_pid]
    visited = {root_pid}
    while pending:
        parent_pid = pending.pop()
        for task_id in _task_ids(parent_pid, proc_root, root_pid=root_pid):
            for child_pid in _read_children(parent_pid, task_id, proc_root):
                record = _read_process_record(child_pid, proc_root)
                if record is None:
                    continue
                result[child_pid] = record
                if child_pid not in visited:
                    visited.add(child_pid)
                    pending.append(child_pid)
    return result


class DescendantGuard:
    """Snapshot descendants and fail if any new one survives until ``check``."""

    def __init__(
        self,
        *,
        root_pid: int | None = None,
        proc_root: Path = _PROC_ROOT,
        grace_period: float = 1.0,
        poll_interval: float = 0.01,
    ) -> None:
        self.root_pid = os.getpid() if root_pid is None else root_pid
        self.proc_root = Path(proc_root)
        self.grace_period = grace_period
        self.poll_interval = poll_interval
        self.baseline = self.snapshot()

    def snapshot(self) -> dict[int, ProcessRecord]:
        return descendants(self.root_pid, proc_root=self.proc_root)

    def _new_descendants(
        self,
        current: dict[int, ProcessRecord],
    ) -> dict[int, ProcessRecord]:
        return {
            pid: record
            for pid, record in current.items()
            if pid not in self.baseline
            or self.baseline[pid].start_time != record.start_time
        }

    def _kill(self, pid: int) -> None:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

    def _reap(self, pid: int) -> None:
        try:
            os.waitpid(pid, os.WNOHANG)
        except ChildProcessError:
            pass

    def check(self) -> None:
        """Clean up leaked descendants, then fail even if cleanup succeeded."""
        detected = self._new_descendants(self.snapshot())
        if not detected:
            return

        survivors = detected
        deadline = time.monotonic() + self.grace_period
        while survivors and time.monotonic() < deadline:
            for pid in survivors:
                self._kill(pid)
                self._reap(pid)
            time.sleep(self.poll_interval)
            survivors = self._new_descendants(self.snapshot())
        raise DescendantLeakError(detected, survivors)


def _prctl(option: int, argument: int = 0) -> int | None:
    """Call prctl lazily so importing this module is safe outside Linux."""
    _require_linux()
    libc = ctypes.CDLL(None, use_errno=True)
    try:
        prctl = libc.prctl
    except AttributeError as exc:
        raise ProcessTreeUnavailable("libc does not expose prctl") from exc

    previous = ctypes.c_int()
    if option == _PR_GET_CHILD_SUBREAPER:
        result = prctl(option, ctypes.byref(previous), 0, 0, 0)
    else:
        result = prctl(option, argument, 0, 0, 0)
    if result != 0:
        error = ctypes.get_errno() or errno.EIO
        raise OSError(error, os.strerror(error), f"prctl({option})")
    if option == _PR_GET_CHILD_SUBREAPER:
        return previous.value
    return None


def enable_child_subreaper() -> int:
    """Enable orphan adoption and return the process's prior setting."""
    previous = _prctl(_PR_GET_CHILD_SUBREAPER)
    assert previous is not None
    _prctl(_PR_SET_CHILD_SUBREAPER, 1)
    return previous


def restore_child_subreaper(previous: int) -> None:
    """Restore the process's child-subreaper setting."""
    _prctl(_PR_SET_CHILD_SUBREAPER, previous)
