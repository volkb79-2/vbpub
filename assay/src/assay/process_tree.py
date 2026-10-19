"""Bounded Linux process-tree isolation for native mutation attempts.

The caller process becomes a child subreaper for one command window. The
command leader runs in a fresh process group and writes to regular files, so a
detached descendant cannot hold a pipe open. After the leader is reaped, every
remaining descendant is adopted by the caller; direct-child PIDs from procfs
are safe to signal until reaped. ``waitid(P_ALL, ...)`` is the final proof that
the caller has no children.

This is process isolation, not a cgroup boundary. It does not contain work
started outside the command's process tree.
"""

from __future__ import annotations

import ctypes
import math
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Mapping, Sequence

from .records import record

__all__ = [
    "ProcessTreeError",
    "ProcessTreeEvidence",
    "ProcessTreeProcessRunner",
    "ProcessTreeSpawnError",
    "ProcessTreeWindow",
]


_PR_SET_CHILD_SUBREAPER = 36
_PR_GET_CHILD_SUBREAPER = 37
_CLEANUP_SECONDS = 10.0
_OUTPUT_TAIL_BYTES = 64 * 1024
_WINDOW_LOCK = threading.RLock()
_UNPROVEN_CLEANUP = False


def _finite_number(value: object) -> bool:
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


class ProcessTreeError(RuntimeError):
    """The command tree could not be proved empty within the cleanup bound."""


class ProcessTreeSpawnError(OSError):
    """A command could not start; includes evidence for the empty window."""

    process_tree_evidence: dict[str, object]

    @classmethod
    def from_oserror(cls, error: OSError) -> "ProcessTreeSpawnError":
        """Preserve the original message and structured OSError fields."""
        wrapped = cls(*error.args)
        if error.errno is not None:
            wrapped.errno = error.errno
            wrapped.strerror = error.strerror
            wrapped.filename = error.filename
            if hasattr(error, "filename2"):
                wrapped.filename2 = error.filename2
        return wrapped


@record
class ProcessTreeEvidence:
    """Wire evidence for one process window."""

    leader_status: str
    group_signalled_before_reap: bool
    outliving_descendants: int
    descendants_signalled: int
    cleanup_seconds: float
    clean: bool
    protocol: str = "assay-process-tree/1"

    def __post_init__(self) -> None:
        if not isinstance(self.leader_status, str) or self.leader_status not in {
            "exited", "signaled", "timeout-killed", "not-started"
        }:
            raise ValueError(f"invalid process-tree leader status: {self.leader_status!r}")
        if type(self.group_signalled_before_reap) is not bool:
            raise ValueError("group_signalled_before_reap must be a boolean")
        for name in ("outliving_descendants", "descendants_signalled"):
            value = getattr(self, name)
            if type(value) is not int or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        if self.descendants_signalled > self.outliving_descendants:
            raise ValueError(
                "descendants_signalled cannot exceed outliving_descendants"
            )
        if (
            not _finite_number(self.cleanup_seconds)
            or self.cleanup_seconds < 0
        ):
            raise ValueError("cleanup_seconds must be non-negative")
        if type(self.clean) is not bool:
            raise ValueError("clean must be a boolean")
        if self.protocol != "assay-process-tree/1":
            raise ValueError("unsupported process-tree evidence protocol")
        if self.clean is False:
            raise ValueError("a recorded process-tree result must prove a clean window")

    @classmethod
    def not_started(cls) -> "ProcessTreeEvidence":
        return cls(
            leader_status="not-started",
            group_signalled_before_reap=False,
            outliving_descendants=0,
            descendants_signalled=0,
            cleanup_seconds=0.0,
            clean=True,
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "ProcessTreeEvidence":
        expected = {
            "protocol", "leader_status", "group_signalled_before_reap",
            "outliving_descendants", "descendants_signalled", "cleanup_seconds",
            "clean",
        }
        if set(value) != expected:
            raise ValueError("process-tree evidence has an invalid field set")
        return cls(
            protocol=value["protocol"],
            leader_status=value["leader_status"],
            group_signalled_before_reap=value["group_signalled_before_reap"],
            outliving_descendants=value["outliving_descendants"],
            descendants_signalled=value["descendants_signalled"],
            cleanup_seconds=value["cleanup_seconds"],
            clean=value["clean"],
        )  # type: ignore[arg-type]

    def to_dict(self) -> dict[str, object]:
        return {
            "protocol": self.protocol,
            "leader_status": self.leader_status,
            "group_signalled_before_reap": self.group_signalled_before_reap,
            "outliving_descendants": self.outliving_descendants,
            "descendants_signalled": self.descendants_signalled,
            "cleanup_seconds": round(self.cleanup_seconds, 6),
            "clean": self.clean,
        }


def _subreaper_state() -> bool:
    libc = ctypes.CDLL(None, use_errno=True)
    prior = ctypes.c_int(0)
    if libc.prctl(
        _PR_GET_CHILD_SUBREAPER, ctypes.byref(prior), 0, 0, 0
    ) != 0:
        error = ctypes.get_errno()
        raise ProcessTreeError(
            f"cannot inspect child-subreaper state: {os.strerror(error)}"
        )
    return bool(prior.value)


def _set_subreaper(enabled: bool) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(_PR_SET_CHILD_SUBREAPER, int(enabled), 0, 0, 0) != 0:
        error = ctypes.get_errno()
        raise ProcessTreeError(
            f"cannot {'enable' if enabled else 'restore'} child-subreaper mode: "
            f"{os.strerror(error)}"
        )


def _has_any_children() -> bool:
    try:
        os.waitid(
            os.P_ALL,
            0,
            os.WEXITED | os.WNOHANG | os.WNOWAIT,
        )
    except ChildProcessError:
        return False
    return True


def _sigchld_is_ignored() -> bool:
    """Read the kernel's SIGCHLD ignore disposition from procfs."""
    try:
        status = Path("/proc/self/status").read_text(encoding="ascii")
    except OSError as exc:
        raise ProcessTreeError(f"cannot inspect SIGCHLD disposition in procfs: {exc}") from exc
    for line in status.splitlines():
        if line.startswith("SigIgn:"):
            try:
                ignored = int(line.partition(":")[2].strip(), 16)
            except ValueError as exc:
                raise ProcessTreeError("procfs reported a malformed SigIgn mask") from exc
            return bool(ignored & (1 << (int(signal.SIGCHLD) - 1)))
    raise ProcessTreeError("procfs did not report the SigIgn mask")


def _ppid_children_from_proc(parent_pid: int) -> list[int]:
    children: list[int] = []
    try:
        entries = os.scandir("/proc")
    except OSError as exc:
        raise ProcessTreeError(f"cannot scan procfs for child processes: {exc}") from exc
    with entries:
        for entry in entries:
            if not entry.name.isdecimal():
                continue
            try:
                stat = Path(entry.path, "stat").read_text(encoding="ascii")
            except (FileNotFoundError, ProcessLookupError):
                continue
            except OSError as exc:
                raise ProcessTreeError(
                    f"cannot inspect procfs process {entry.name}: {exc}"
                ) from exc
            close = stat.rfind(")")
            if close < 0:
                raise ProcessTreeError(f"malformed procfs stat for pid {entry.name}")
            fields = stat[close + 1 :].split()
            if len(fields) < 2:
                raise ProcessTreeError(f"truncated procfs stat for pid {entry.name}")
            try:
                ppid = int(fields[1])
                pid = int(entry.name)
            except ValueError as exc:
                raise ProcessTreeError(
                    f"malformed procfs pid fields for {entry.name}"
                ) from exc
            if ppid == parent_pid:
                children.append(pid)
    return children


def _direct_child_pids() -> list[int]:
    """Return direct children using procfs task hints, then PPID fallback."""
    root = os.getpid()
    found: set[int] = set()
    task_root = Path(f"/proc/{root}/task")
    try:
        tasks = list(task_root.iterdir())
    except OSError:
        tasks = []
    for task in tasks:
        try:
            raw = Path(task, "children").read_text(encoding="ascii")
        except (FileNotFoundError, ProcessLookupError):
            continue
        except OSError:
            continue
        try:
            found.update(int(token) for token in raw.split())
        except ValueError as exc:
            raise ProcessTreeError(
                f"malformed procfs children list for task {task.name}"
            ) from exc
    if found:
        return sorted(found)
    return _ppid_children_from_proc(root)


def _kill_process_group(pgid: int) -> bool:
    try:
        os.killpg(pgid, signal.SIGKILL)
    except ProcessLookupError:
        return False
    except OSError as exc:
        raise ProcessTreeError(
            f"cannot signal candidate process group {pgid}: {exc}"
        ) from exc
    return True


class ProcessTreeWindow:
    """One serialized command window guarded by subreaper and ECHILD proof."""

    def __init__(self, *, cleanup_seconds: float = _CLEANUP_SECONDS) -> None:
        if (
            not _finite_number(cleanup_seconds)
            or cleanup_seconds <= 0
        ):
            raise ValueError("cleanup_seconds must be positive")
        self.cleanup_seconds = float(cleanup_seconds)
        self._prior_subreaper = False
        self._changed_subreaper = False
        self._entered = False
        self._finished = False
        self._locked = False
        self._proc: subprocess.Popen[bytes] | None = None
        self.evidence: ProcessTreeEvidence | None = None

    def __enter__(self) -> "ProcessTreeWindow":
        if not sys.platform.startswith("linux"):
            raise ProcessTreeError("native process-tree isolation requires Linux")
        _WINDOW_LOCK.acquire()
        self._locked = True
        try:
            self._prior_subreaper = _subreaper_state()
            if _UNPROVEN_CLEANUP:
                raise ProcessTreeError(
                    "an earlier native R2 window could not prove cleanup; "
                    "this process remains a subreaper"
                )
            if _sigchld_is_ignored():
                raise ProcessTreeError(
                    "native process-tree isolation requires SIGCHLD to be waitable"
                )
            if not self._prior_subreaper:
                _set_subreaper(True)
                self._changed_subreaper = True
            if _has_any_children():
                raise ProcessTreeError(
                    "cannot start an isolated command while the caller already "
                    "has child processes"
                )
            self._entered = True
            return self
        except BaseException:
            if self._changed_subreaper:
                try:
                    _set_subreaper(False)
                except ProcessTreeError:
                    pass
                self._changed_subreaper = False
            self._locked = False
            _WINDOW_LOCK.release()
            raise

    def spawn(self, argv: Sequence[str], **kwargs: object) -> subprocess.Popen[bytes]:
        if not self._entered or self._finished or self._proc is not None:
            raise ProcessTreeError("process-tree window is not ready to spawn")
        if kwargs.get("start_new_session") is not True:
            raise ProcessTreeError("isolated command must start a new session")
        try:
            proc = subprocess.Popen(list(argv), **kwargs)  # type: ignore[arg-type]
        except OSError as exc:
            raise exc
        self._proc = proc
        return proc

    def attach(self, proc: subprocess.Popen[bytes]) -> None:
        """Register a Popen created by an injectable caller-side seam."""
        if not self._entered or self._finished or self._proc is not None:
            raise ProcessTreeError("process-tree window is not ready to attach")
        self._proc = proc

    def finish(
        self,
        *,
        timed_out: bool = False,
        force: bool = False,
    ) -> ProcessTreeEvidence:
        if not self._entered or self._finished:
            if self.evidence is not None:
                return self.evidence
            raise ProcessTreeError("process-tree window is not active")
        started = time.monotonic()
        group_signalled = False
        outliving: set[int] = set()
        signalled: set[int] = set()
        proc = self._proc
        if proc is not None and proc.returncode is None:
            if not force:
                raise ProcessTreeError("cannot finish a process-tree window with a live leader")
            group_signalled = _kill_process_group(proc.pid)
            try:
                proc.wait(timeout=self.cleanup_seconds)
            except subprocess.TimeoutExpired as exc:
                raise ProcessTreeError(
                    f"candidate leader {proc.pid} did not exit during cleanup"
                ) from exc
            except OSError as exc:
                raise ProcessTreeError(
                    f"cannot reap candidate leader {proc.pid}: {exc}"
                ) from exc

        deadline = started + self.cleanup_seconds
        while True:
            if not _has_any_children():
                break
            child_pids = _direct_child_pids()
            for pid in child_pids:
                outliving.add(pid)
                try:
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    continue
                except OSError as exc:
                    raise ProcessTreeError(
                        f"cannot signal adopted candidate descendant {pid}: {exc}"
                    ) from exc
                signalled.add(pid)
            for pid in child_pids:
                try:
                    reaped_pid, _status = os.waitpid(pid, os.WNOHANG)
                except ChildProcessError:
                    # A different waiter in the parent may have reaped this
                    # exact child after the procfs snapshot. Never broaden
                    # this to waitpid(-1): that could steal an unrelated
                    # child's status from the embedding application.
                    continue
                except OSError as exc:
                    raise ProcessTreeError(f"cannot reap candidate descendants: {exc}") from exc
                if reaped_pid > 0:
                    outliving.add(reaped_pid)
            if time.monotonic() >= deadline:
                remaining = _direct_child_pids()
                raise ProcessTreeError(
                    "candidate process tree did not become empty within "
                    f"{self.cleanup_seconds:g}s; remaining direct children: {remaining}"
                )
            time.sleep(0.01)

        cleanup_elapsed = max(0.0, time.monotonic() - started)
        leader_status = "not-started"
        if proc is not None:
            if timed_out:
                leader_status = "timeout-killed"
            elif proc.returncode is not None and proc.returncode < 0:
                leader_status = "signaled"
            else:
                leader_status = "exited"
        self.evidence = ProcessTreeEvidence(
            leader_status=leader_status,
            group_signalled_before_reap=group_signalled,
            outliving_descendants=len(outliving),
            descendants_signalled=len(signalled),
            cleanup_seconds=cleanup_elapsed,
            clean=True,
        )
        self._finished = True
        return self.evidence

    def __exit__(self, exc_type, exc, traceback) -> bool:
        global _UNPROVEN_CLEANUP
        cleanup_error: BaseException | None = None
        try:
            if not self._finished:
                try:
                    self.finish(force=exc_type is not None)
                except BaseException as failure:
                    cleanup_error = failure
            if not self._finished:
                _UNPROVEN_CLEANUP = True
            if self._finished and self._changed_subreaper:
                try:
                    _set_subreaper(False)
                    self._changed_subreaper = False
                except BaseException as failure:
                    cleanup_error = cleanup_error or failure
        finally:
            self._entered = False
            if self._locked:
                self._locked = False
                _WINDOW_LOCK.release()
        if cleanup_error is not None:
            if exc is not None:
                raise ProcessTreeError(
                    f"process-tree cleanup failed while handling {exc!r}: {cleanup_error}"
                ) from exc
            if isinstance(cleanup_error, ProcessTreeError):
                raise cleanup_error
            raise ProcessTreeError(f"process-tree cleanup failed: {cleanup_error}") from cleanup_error
        return False


def _read_tail(
    stream,
    *,
    limit: int = _OUTPUT_TAIL_BYTES,
    sensitive_values: Sequence[str] = (),
) -> tuple[str, int]:
    """Return a redacted, bounded UTF-8 tail and the omitted byte count.

    Read enough prefix context to recognize a secret that straddles the
    retained-tail boundary. Redacting only after seeking to ``size - limit``
    can expose the suffix of such a secret.
    """
    size = os.fstat(stream.fileno()).st_size
    retained_start = max(0, size - limit)
    normalized_values = tuple(
        value.encode("utf-8", errors="surrogateescape").decode(
            "utf-8", errors="replace"
        )
        for value in sensitive_values if value
    )
    overlap = max(
        (len(value.encode("utf-8")) for value in normalized_values),
        default=0,
    )
    start = max(0, retained_start - max(0, overlap - 1))
    stream.seek(start)
    raw = stream.read().decode("utf-8", "replace")
    if normalized_values:
        from .redaction import redact_passthrough_text

        raw = redact_passthrough_text(raw, normalized_values)
    encoded = raw.encode("utf-8")
    if len(encoded) > limit:
        cutoff = len(encoded) - limit
        while cutoff < len(encoded) and (encoded[cutoff] & 0xC0) == 0x80:
            cutoff += 1
        raw = encoded[cutoff:].decode("utf-8")
    return raw, retained_start


class ProcessTreeProcessRunner:
    """ProcessRunner-compatible command isolation for native R2 calls."""

    def __init__(
        self,
        *,
        cleanup_seconds: float = _CLEANUP_SECONDS,
        sensitive_values: Sequence[str] = (),
    ) -> None:
        self.cleanup_seconds = cleanup_seconds
        self.sensitive_values = tuple(value for value in sensitive_values if value)

    def __call__(
        self,
        argv: Sequence[str],
        *,
        env: Mapping[str, str],
        cwd: Path,
        timeout: float | None,
    ) -> subprocess.CompletedProcess[str]:
        from .mutation_witness import WITNESS_FD_ENV
        from . import liveness

        descriptor = env.get(WITNESS_FD_ENV)
        pass_fds = () if descriptor is None else (int(descriptor),)
        command = list(argv)
        with tempfile.TemporaryFile(mode="w+b") as stdout, tempfile.TemporaryFile(
            mode="w+b"
        ) as stderr:
            window = ProcessTreeWindow(cleanup_seconds=self.cleanup_seconds)
            registered_group: int | None = None
            try:
                with window:
                    try:
                        proc = window.spawn(
                            command,
                            cwd=cwd,
                            env=dict(env),
                            stdin=subprocess.DEVNULL,
                            stdout=stdout,
                            stderr=stderr,
                            start_new_session=True,
                            pass_fds=pass_fds,
                        )
                    except OSError as exc:
                        evidence = window.finish()
                        wrapped = ProcessTreeSpawnError.from_oserror(exc)
                        wrapped.process_tree_evidence = evidence.to_dict()
                        raise wrapped from exc
                    liveness.register_live_group(proc.pid)
                    registered_group = proc.pid
                    try:
                        returncode = proc.wait(timeout=timeout)
                    except subprocess.TimeoutExpired as exc:
                        evidence = window.finish(timed_out=True, force=True)
                        out, out_dropped = _read_tail(
                            stdout, sensitive_values=self.sensitive_values
                        )
                        err, err_dropped = _read_tail(
                            stderr, sensitive_values=self.sensitive_values
                        )
                        timeout_error = subprocess.TimeoutExpired(
                            command, timeout, output=out, stderr=err
                        )
                        timeout_error.process_tree_evidence = evidence.to_dict()
                        timeout_error.assay_stdout_dropped_bytes = out_dropped
                        timeout_error.assay_stderr_dropped_bytes = err_dropped
                        raise timeout_error from exc
                    evidence = window.finish()
                    out, out_dropped = _read_tail(
                        stdout, sensitive_values=self.sensitive_values
                    )
                    err, err_dropped = _read_tail(
                        stderr, sensitive_values=self.sensitive_values
                    )
                    result = subprocess.CompletedProcess(command, returncode, out, err)
                    result.process_tree_evidence = evidence.to_dict()
                    result.assay_stdout_dropped_bytes = out_dropped
                    result.assay_stderr_dropped_bytes = err_dropped
                    return result
            except ProcessTreeError:
                raise
            except ProcessTreeSpawnError:
                raise
            except OSError as exc:
                raise ProcessTreeError(
                    f"native process-tree runner failed after starting the command: {exc}"
                ) from exc
            finally:
                if registered_group is not None:
                    liveness.unregister_live_group(registered_group)
