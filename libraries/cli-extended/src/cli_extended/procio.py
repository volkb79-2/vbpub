"""Bounded child-process supervision: read a child's output without waiting for pipe EOF.

A loop that reads a child's output pipe until EOF waits for *every* holder of the write end to
close it, and a grandchild that inherited the pipe can keep it open forever, so a finished
child can hang its caller silently (cmru KI-178, ciu CIU-220, mdt-cockpit MDT-104, nyxloom
NL-83). This module is the one implementation the estate shares:

* :func:`run_streaming` reads a child's combined stdout/stderr line by line, stops
  ``drain_seconds`` after the child has exited, keeps the child's own exit code, names the
  processes still holding the pipe, and ends a child that produces no progress for
  ``no_progress_seconds`` (the no-progress watchdog).
* :func:`run_capture` is the bounded replacement for
  ``subprocess.run(capture_output=True)``: both pipes share one selector, the same drain
  applies, and a timeout reaps the whole process group.
* :func:`terminate_as_exit`, :func:`reap_process_group`, :func:`pipe_holder_pids` and
  :func:`process_tree` are the building blocks, public so a caller can compose its own loop.

Every child starts in its own session and process group (``start_new_session``), so a reap
signals exactly the child and what it spawned and nothing the caller owns. Such a child has no
controlling terminal: reads on an inherited stdin work, but opening ``/dev/tty`` (sudo, ssh and
git prompts) fails. The module reads ``/proc`` (Linux), uses only the standard library and
never prints: the WARN and ERROR texts are returned as data for the caller's own logger.
"""

from __future__ import annotations

import codecs
import io
import locale
import math
import os
import selectors
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from typing import IO, Any, Literal

__all__ = [
    "DEFAULT_DRAIN_SECONDS",
    "DEFAULT_TERM_GRACE_SECONDS",
    "OUTCOMES",
    "Outcome",
    "CaptureResult",
    "ProcessEntry",
    "StreamResult",
    "WatchdogReport",
    "pipe_holder_pids",
    "process_tree",
    "reap_process_group",
    "run_capture",
    "run_streaming",
    "terminate_as_exit",
]

DEFAULT_DRAIN_SECONDS = 2.0
DEFAULT_TERM_GRACE_SECONDS = 5.0
Outcome = Literal["exited", "watchdog"]
OUTCOMES: tuple[Outcome, ...] = ("exited", "watchdog")

_PROC_ROOT = "/proc"
_POLL_SECONDS = 0.2
_READ_BYTES = 65536
_WRITE_BYTES = 65536
_ARGV_LIMIT = 200
_REAP_STEP_SECONDS = 0.02
_TERMINATION_SIGNALS = (signal.SIGTERM, signal.SIGHUP)
_SUPERVISION_SIGNALS = (*_TERMINATION_SIGNALS, signal.SIGINT)


# --------------------------------------------------------------------------- results


@dataclass(frozen=True)
class ProcessEntry:
    """One process of a :func:`process_tree` snapshot."""

    pid: int
    ppid: int
    state: str
    pgid: int
    argv: str

    @property
    def line(self) -> str:
        """``pid ppid state argv`` with argv already cut to 200 characters."""
        return f"{self.pid} {self.ppid} {self.state} {self.argv}"


@dataclass(frozen=True)
class WatchdogReport:
    """What the no-progress watchdog saw when it fired (the tree is taken before any signal)."""

    argv0: str
    limit_seconds: float
    last_line: str | None
    process_tree: tuple[ProcessEntry, ...]
    escaped_pids: tuple[int, ...]

    def render(self, setting: str = "no_progress_seconds") -> str:
        """The one-line error text; ``setting`` names the caller's own configuration key."""
        last = self.last_line if self.last_line is not None else "<none>"
        return (
            f"'{self.argv0}' produced no output for {self.limit_seconds:g}s "
            f"({setting} = {self.limit_seconds:g}); last output line: {last}"
        )

    @property
    def tree_lines(self) -> tuple[str, ...]:
        """The live process tree, one ``pid ppid state argv`` line per process."""
        return tuple(entry.line for entry in self.process_tree)


@dataclass(frozen=True)
class StreamResult:
    """The outcome of :func:`run_streaming`.

    ``returncode`` is always the child's own (a negative signal number after a watchdog kill).
    ``abandoned`` is true exactly when reading stopped with an output pipe still open, whether
    or not the holder scan found a pid. ``abandoned_holders`` is ``()`` when nothing was
    abandoned, the pids that still held the pipe when reading stopped, or ``None`` when the
    ``/proc`` scan failed. ``warning`` is the one-line WARN text (without a level prefix) when
    reading was abandoned, else ``None``.
    """

    returncode: int
    outcome: Outcome
    abandoned_holders: tuple[int, ...] | None
    warning: str | None
    last_line: str | None
    watchdog: WatchdogReport | None

    @property
    def abandoned(self) -> bool:
        """Whether reading stopped while an output pipe was still open."""

        return self.warning is not None


class CaptureResult(subprocess.CompletedProcess):
    """``subprocess.CompletedProcess`` plus the drain facts of :func:`run_capture`."""

    def __init__(
        self,
        args: Any,
        returncode: int,
        stdout: Any,
        stderr: Any,
        abandoned_holders: tuple[int, ...] | None,
        warning: str | None,
    ) -> None:
        super().__init__(args, returncode, stdout, stderr)
        self.abandoned_holders = abandoned_holders
        self.warning = warning

    @property
    def abandoned(self) -> bool:
        """Whether reading stopped while an output pipe was still open."""

        return self.warning is not None


# ---------------------------------------------------------------------- /proc helpers


def pipe_holder_pids(inode: int) -> list[int]:
    """Pids (other than ours) with the pipe ``inode`` open, found through ``/proc/<pid>/fd``."""
    wanted = f"pipe:[{inode}]"
    holders: list[int] = []
    for entry in os.listdir(_PROC_ROOT):
        if not entry.isdigit() or int(entry) == os.getpid():
            continue
        try:
            descriptors = os.listdir(f"{_PROC_ROOT}/{entry}/fd")
        except OSError:
            continue  # exited meanwhile, or owned by another user: not ours to inspect
        for descriptor in descriptors:
            try:
                target = os.readlink(f"{_PROC_ROOT}/{entry}/fd/{descriptor}")
            except OSError:
                # Closed since the listing (a child inside execve drops its close-on-exec
                # descriptors after Popen returns): skip that descriptor only. Giving up on the
                # whole process here would hide a holder whose pipe end is still open.
                continue
            if target == wanted:
                holders.append(int(entry))
                break
    return sorted(holders)


def _read_stat(pid: int) -> tuple[str, str, int, int] | None:
    """``(comm, state, ppid, pgid)`` from ``/proc/<pid>/stat``, ``None`` when unreadable."""
    try:
        with open(f"{_PROC_ROOT}/{pid}/stat", encoding="utf-8", errors="replace") as handle:
            stat = handle.read()
        head, _, tail = stat.rpartition(")")  # the command name may contain spaces and parens
        fields = tail.split()
        return head.partition("(")[2], fields[0], int(fields[1]), int(fields[2])
    except (OSError, IndexError, ValueError):
        return None  # exited meanwhile, or not the layout we know


def _read_argv(pid: int, comm: str) -> str:
    try:
        with open(f"{_PROC_ROOT}/{pid}/cmdline", "rb") as handle:
            raw = handle.read()
    except OSError:
        raw = b""
    words = [word.decode("utf-8", "replace") for word in raw.split(b"\0") if word]
    return (" ".join(words) if words else f"[{comm}]")[:_ARGV_LIMIT]


def process_tree(pid: int) -> list[ProcessEntry]:
    """``pid`` and its descendants (``/proc/<pid>/stat`` ppid links), parents before children.

    Empty when ``pid`` is not running. A process that was reparented away from the tree (its
    parent exited) is not reachable through ppid links and is not listed.
    """
    stats: dict[int, tuple[str, str, int, int]] = {}
    children: dict[int, list[int]] = {}
    for name in os.listdir(_PROC_ROOT):
        if not name.isdigit():
            continue
        stat = _read_stat(int(name))
        if stat is not None:
            stats[int(name)] = stat
            children.setdefault(stat[2], []).append(int(name))
    entries: list[ProcessEntry] = []
    seen: set[int] = set()
    stack = [pid] if pid in stats else []
    while stack:
        current = stack.pop()
        if current in seen:
            continue
        seen.add(current)
        comm, state, ppid, pgid = stats[current]
        entries.append(ProcessEntry(current, ppid, state, pgid, _read_argv(current, comm)))
        stack.extend(sorted(children.get(current, ()), reverse=True))
    return entries


# --------------------------------------------------------------------------- signals


class _SignalScope:
    """Turn termination signals into exceptions so ``finally`` cleanup (the group reap) runs.

    SIGTERM and SIGHUP become ``SystemExit(128 + signum)``; SIGINT (only when it still has
    Python's default handler) becomes ``KeyboardInterrupt``. Handlers are installed from the
    main thread only. A signal that is ``SIG_IGN`` when the scope starts, such as the SIGHUP
    ``nohup`` sets, stays ignored, for the caller and, since the disposition survives exec, for
    the child. Only what was installed here is restored on exit, even when a later install
    fails.

    While :meth:`spawn` runs, a signal is *deferred* instead of raised and delivered as soon as
    the new process is recorded in :attr:`process`, so a signal can never land between the fork
    and the point where the caller can reap the child. (Blocking the signals with
    ``pthread_sigmask`` would close the same window, but the child inherits the blocked mask
    across ``exec`` and would then ignore the group reap.)
    """

    def __init__(self, signals: Sequence[int]) -> None:
        self._signals = tuple(signals)
        self._previous: dict[int, Any] = {}
        self._deferring = False
        self._restoring = False
        self._pending: int | None = None
        self.process: subprocess.Popen | None = None

    def __enter__(self) -> _SignalScope:
        if threading.current_thread() is threading.main_thread():
            try:
                for signum in self._signals:
                    current = signal.getsignal(signum)
                    if signum == signal.SIGINT:
                        skip = current is not signal.default_int_handler
                    else:
                        skip = current == signal.SIG_IGN
                    if not skip:
                        self._previous[signum] = signal.signal(signum, self)
            except BaseException:
                self._restore()
                raise
        return self

    def __exit__(self, exc_type: object, *_exc: object) -> None:
        self._restore()
        if exc_type is None and self._pending is not None:
            pending, self._pending = self._pending, None
            raise self._exception(pending)  # a signal that arrived while restoring is not lost

    def _restore(self) -> None:
        """Put every installed handler back; one failing or interrupted restore never skips the rest.

        While restoring, our own handler records a signal instead of raising it, so it cannot
        abort the loop half way; the first error is re-raised once everything is restored.
        """
        self._restoring = True
        previous, self._previous = self._previous, {}
        first_error: BaseException | None = None
        for signum, handler in previous.items():
            try:
                signal.signal(signum, handler if handler is not None else signal.SIG_DFL)
            except BaseException as error:  # noqa: BLE001 - re-raised below
                first_error = first_error or error
        self._restoring = False
        if first_error is not None:
            raise first_error

    @staticmethod
    def _exception(signum: int) -> BaseException:
        if signum == signal.SIGINT:
            return KeyboardInterrupt()
        return SystemExit(128 + signum)

    def __call__(self, signum: int, _frame: object) -> None:
        if self._deferring or self._restoring:
            if self._pending is None:
                self._pending = signum
            return
        raise self._exception(signum)

    def _take_pending(self) -> BaseException | None:
        pending, self._pending = self._pending, None
        return None if pending is None else self._exception(pending)

    def spawn(self, argv: Sequence[Any], **kwargs: Any) -> subprocess.Popen:
        """``subprocess.Popen`` in its own session; a signal during it is delivered afterwards."""
        self._deferring = True
        try:
            self.process = subprocess.Popen(argv, start_new_session=True, **kwargs)
        except BaseException as error:
            self._deferring = False
            pending = self._take_pending()
            if pending is not None:
                raise pending from error
            raise
        self._deferring = False
        pending = self._take_pending()
        if pending is not None:
            raise pending
        return self.process


@contextmanager
def terminate_as_exit() -> Iterator[None]:
    """Turn SIGTERM/SIGHUP into ``SystemExit`` for the duration of the ``with`` block.

    A child that runs in its own session is not reached by a terminal hang-up, and a SIGTERM
    or SIGHUP that kills its caller by the default action would orphan it; the ``SystemExit``
    instead takes the caller's ``finally``/reap path. ``signal.signal`` only works in the main
    thread: in any other thread nothing is installed and the caller's signal policy stands. A
    signal that is ``SIG_IGN`` at entry (a SIGHUP set by ``nohup``) stays ignored, for the
    caller and for children. Only what was installed here is restored, even when a later
    install fails. :func:`run_streaming` and :func:`run_capture` already do this (and the same
    for SIGINT) around their own work.
    """
    with _SignalScope(_TERMINATION_SIGNALS):
        yield


def _signal_group(group: int, signum: int) -> None:
    try:
        os.killpg(group, signum)
    except ProcessLookupError:
        pass  # nothing left in the group


def reap_process_group(proc: subprocess.Popen, grace: float) -> None:
    """SIGTERM then, after ``grace`` seconds, SIGKILL the child's whole group and reap its leader.

    ``proc`` must have been started with ``start_new_session=True`` so its process group is
    exactly the child and whatever it spawned; nothing the caller owns is signalled. Returns
    early once the leader has exited and the group is empty; a leader that was already reaped
    whose group is gone is not signalled at all (its pid may have been reused). If the wait
    itself is interrupted the group is SIGKILLed and the leader waited for before the
    interruption propagates, so no zombie is left behind.
    """
    group = proc.pid
    if proc.returncode is not None:
        try:
            os.killpg(group, 0)
        except ProcessLookupError:
            return
    _signal_group(group, signal.SIGTERM)
    deadline = time.monotonic() + grace
    try:
        while time.monotonic() < deadline:
            if proc.poll() is not None:  # leader reaped: the group is done once it is empty
                try:
                    os.killpg(group, 0)
                except ProcessLookupError:
                    return
            time.sleep(_REAP_STEP_SECONDS)
    except BaseException:
        _signal_group(group, signal.SIGKILL)
        proc.wait()
        raise
    _signal_group(group, signal.SIGKILL)
    proc.wait()


# ------------------------------------------------------------------- shared machinery


def _require_seconds(name: str, value: Any, *, positive: bool) -> None:
    ok = (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and (value > 0 if positive else value >= 0)
    )
    if not ok:
        kind = "a positive" if positive else "a non-negative"
        extra = " (there is no 'off' value)" if name == "no_progress_seconds" else ""
        raise ValueError(f"{name} must be {kind} finite number of seconds{extra}; got {value!r}")


def _make_decoder(errors: str) -> io.IncrementalNewlineDecoder:
    """Text-pipe decoding: locale encoding, universal newlines, ``errors`` for bad bytes."""
    encoding = "utf-8" if sys.flags.utf8_mode else locale.getencoding()
    return io.IncrementalNewlineDecoder(
        codecs.getincrementaldecoder(encoding)(errors=errors), translate=True,
    )


class _Lines:
    """Split decoded output into lines (terminator kept) and remember the last non-blank one."""

    def __init__(self, on_line: Callable[[str], object]) -> None:
        self._on_line = on_line
        # Log lines are not parsed: undecodable bytes become U+FFFD rather than failing a child.
        self._decoder = _make_decoder("replace")
        self._pieces: list[str] = []  # the unterminated tail, kept as pieces (no quadratic copy)
        self.last_line: str | None = None

    def feed(self, chunk: bytes, *, final: bool = False) -> None:
        text = self._decoder.decode(chunk, final)
        if "\n" in text:
            *complete, tail = text.split("\n")
            complete[0] = "".join(self._pieces) + complete[0]
            self._pieces = [tail] if tail else []
            for line in complete:
                self._emit(line + "\n")
        elif text:
            self._pieces.append(text)
        if final and self._pieces:
            tail, self._pieces = "".join(self._pieces), []
            self._emit(tail)

    def _emit(self, line: str) -> None:
        if line.strip():
            self.last_line = line.rstrip("\r\n")
        self._on_line(line)


class _StdinWriter:
    """Feed ``data`` into the child's stdin without blocking the selector loop."""

    def __init__(self, stream: IO[bytes], data: bytes) -> None:
        self.stream = stream
        self._view = memoryview(data)
        self._offset = 0
        self.active = False

    def start(self, selector: selectors.BaseSelector) -> None:
        if len(self._view):
            os.set_blocking(self.stream.fileno(), False)
            selector.register(self.stream.fileno(), selectors.EVENT_WRITE, self)
            self.active = True
        else:
            self.stream.close()

    def step(self, selector: selectors.BaseSelector) -> None:
        try:
            self._offset += os.write(
                self.stream.fileno(), self._view[self._offset:self._offset + _WRITE_BYTES],
            )
        except BlockingIOError:
            return
        except BrokenPipeError:
            self._offset = len(self._view)  # the child stopped reading: not an error
        if self._offset >= len(self._view):
            self.stop(selector)

    def stop(self, selector: selectors.BaseSelector) -> None:
        if self.active:
            self.active = False
            selector.unregister(self.stream.fileno())
            self.stream.close()


def _pump(
    proc: subprocess.Popen,
    read_fds: Sequence[int],
    on_chunk: Callable[[int, bytes], None],
    *,
    drain_seconds: float,
    guard: Callable[[float, bool], float] | None = None,
    writer: _StdinWriter | None = None,
) -> frozenset[int]:
    """Read ``read_fds`` until the child has exited and every pipe is at EOF or drained.

    ``on_chunk(fd, chunk)`` gets each read and ``b""`` at EOF. Reading stops
    ``drain_seconds`` after the child exits; the descriptors still open then are returned
    (empty means a clean end). While the child is alive ``guard(now, progressed)`` runs on
    every iteration and returns how many seconds may pass before it must run again; it may
    end the child itself. An EOF with a live child just keeps waiting for the exit.
    """
    open_fds = set(read_fds)
    exited_at: float | None = None
    progressed = False
    with selectors.DefaultSelector() as selector:
        # epoll/poll based: the fd-set select(2) API refuses descriptors >= FD_SETSIZE (1024).
        for fd in read_fds:
            selector.register(fd, selectors.EVENT_READ)
        if writer is not None:
            writer.start(selector)
        while True:
            now = time.monotonic()
            if exited_at is None and proc.poll() is not None:
                exited_at = now
            wait = _POLL_SECONDS
            if exited_at is None:
                if guard is not None:
                    wait = min(wait, guard(now, progressed))
                progressed = False
            else:
                if writer is not None:
                    writer.stop(selector)
                if not open_fds:
                    return frozenset()
                remaining = exited_at + drain_seconds - now
                if remaining <= 0:
                    return frozenset(open_fds)
                wait = min(wait, remaining)
            for key, _mask in selector.select(max(wait, 0.0)):
                if key.data is not None:
                    key.data.step(selector)
                    continue
                chunk = os.read(key.fd, _READ_BYTES)
                if not chunk:
                    selector.unregister(key.fd)
                    open_fds.discard(key.fd)
                else:
                    progressed = True
                on_chunk(key.fd, chunk)


def _abandonment(
    argv0: str, returncode: int, open_fds: frozenset[int], drain_seconds: float,
) -> tuple[tuple[int, ...] | None, str | None]:
    """``(holders, warning)`` for pipes still open when the drain ended; ``((), None)`` if none."""
    if not open_fds:
        return (), None
    try:
        holders: tuple[int, ...] | None = tuple(sorted({
            pid for fd in open_fds for pid in pipe_holder_pids(os.fstat(fd).st_ino)
        }))
    except OSError:
        holders = None
    named = (
        f"still held open by pid(s) {', '.join(map(str, holders))}"
        if holders else "still held open by another process"
    )
    return holders, (
        f"'{argv0}' exited with code {returncode} but its output pipe is {named}; "
        f"stopped reading after {drain_seconds:g}s and kept the exit code."
    )


def _close_pipes(*streams: IO[bytes] | None) -> None:
    for stream in streams:
        if stream is not None and not stream.closed:
            stream.close()


def _argv0(argv: Sequence[Any]) -> str:
    return str(os.fspath(argv[0])) if argv else ""


def _stat_signature(path: str) -> tuple[int, int] | None:
    try:
        status = os.stat(path)
    except OSError:
        return None
    return status.st_size, status.st_mtime_ns


# --------------------------------------------------------------------------- streaming


def run_streaming(
    argv: Sequence[Any],
    *,
    cwd: Any,
    env: Mapping[str, str] | None,
    on_line: Callable[[str], object],
    drain_seconds: float = DEFAULT_DRAIN_SECONDS,
    no_progress_seconds: float,
    progress_paths: Sequence[Any] = (),
    term_grace_seconds: float = DEFAULT_TERM_GRACE_SECONDS,
) -> StreamResult:
    """Run ``argv`` and feed its combined stdout/stderr to ``on_line``, line by line.

    The child starts in its own session with ``stdout=PIPE, stderr=STDOUT`` and an inherited
    stdin. Output is decoded like a text pipe (locale encoding, universal newlines) but
    undecodable bytes become U+FFFD, so lines are for logs and display: a caller that parses
    output uses :func:`run_capture`, which is strict. Every line reaches ``on_line`` with its
    terminator; an unterminated last line is delivered without one. Reading ends ``drain_seconds`` after the child exits, even
    when a grandchild still holds the pipe; the result then names the holders and carries the
    WARN text, and the exit code stays the child's own.

    **Watchdog.** Progress is bytes read from the pipe or a size or mtime change of any path in
    ``progress_paths``. When ``no_progress_seconds`` (a positive number: there is no 'off' and
    the library has no default) pass without progress, the live process tree is captured
    first, then the child's own process group gets SIGTERM and, after ``term_grace_seconds``,
    SIGKILL. The result has ``outcome == "watchdog"`` and a :class:`WatchdogReport`; a process
    that escaped the group with ``setsid`` is named in ``escaped_pids`` and never killed.

    Ctrl-C, SIGTERM and SIGHUP delivered to the caller (and any exception from ``on_line``)
    reap the whole group before propagating. A command that cannot start raises the plain
    ``subprocess.Popen`` error.
    """
    _require_seconds("no_progress_seconds", no_progress_seconds, positive=True)
    _require_seconds("drain_seconds", drain_seconds, positive=False)
    _require_seconds("term_grace_seconds", term_grace_seconds, positive=False)
    if isinstance(progress_paths, (str, bytes, os.PathLike)):
        raise TypeError("progress_paths must be a sequence of paths, not a single path")
    paths = tuple(os.fspath(path) for path in progress_paths)
    lines = _Lines(on_line)
    signatures = {path: _stat_signature(path) for path in paths}
    state: dict[str, Any] = {"last_progress": time.monotonic(), "report": None}
    with _SignalScope(_SUPERVISION_SIGNALS) as scope:
        try:
            try:
                proc = scope.spawn(
                    argv, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    bufsize=0,
                )
                assert proc.stdout is not None
                argv0 = _argv0(argv)

                def guard(now: float, progressed: bool) -> float:
                    for path in paths:
                        signature = _stat_signature(path)
                        if signature != signatures[path]:
                            signatures[path] = signature
                            progressed = True
                    if progressed:
                        state["last_progress"] = now
                    due = state["last_progress"] + no_progress_seconds - now
                    if due > 0:
                        return due
                    # Fired: capture the tree BEFORE anything is signalled.
                    tree = process_tree(proc.pid)
                    state["report"] = WatchdogReport(
                        argv0=argv0,
                        limit_seconds=no_progress_seconds,
                        last_line=lines.last_line,
                        process_tree=tuple(tree),
                        escaped_pids=tuple(
                            entry.pid for entry in tree if entry.pgid != proc.pid
                        ),
                    )
                    reap_process_group(proc, term_grace_seconds)
                    return 0.0

                open_fds = _pump(
                    proc,
                    [proc.stdout.fileno()],
                    lambda _fd, chunk: lines.feed(chunk, final=not chunk),
                    drain_seconds=drain_seconds,
                    guard=guard,
                )
                lines.feed(b"", final=True)  # a pipe abandoned mid-line still delivers its tail
                returncode = proc.returncode
                holders, warning = _abandonment(argv0, returncode, open_fds, drain_seconds)
            except BaseException:
                # Ctrl-C, SystemExit from a termination signal, a failing on_line: never leave
                # the child, or anything in its process group, running behind us.
                if scope.process is not None:
                    reap_process_group(scope.process, term_grace_seconds)
                raise
        finally:
            if scope.process is not None:
                _close_pipes(scope.process.stdout)
    report = state["report"]
    return StreamResult(
        returncode=returncode,
        outcome="watchdog" if report is not None else "exited",
        abandoned_holders=holders,
        warning=warning,
        last_line=lines.last_line,
        watchdog=report,
    )


# ----------------------------------------------------------------------------- capture


def run_capture(
    argv: Sequence[Any],
    *,
    cwd: Any,
    env: Mapping[str, str] | None,
    input: str | bytes | None = None,
    stdin: int | None = None,
    timeout: float | None = None,
    text: bool = True,
    drain_seconds: float = DEFAULT_DRAIN_SECONDS,
) -> CaptureResult:
    """Run ``argv`` and capture stdout and stderr: ``subprocess.run(capture_output=True)``, bounded.

    Both output pipes (and ``input``, when given, written without blocking) share one selector,
    so a child that fills one pipe while the other is idle cannot deadlock. Reading ends
    ``drain_seconds`` after the child exits even if a grandchild holds a pipe; the returned
    :class:`CaptureResult` (a ``CompletedProcess``) then names the holders in
    ``abandoned_holders`` and carries the WARN text in ``warning``. ``text=True`` decodes like
    a text pipe (locale encoding, universal newlines) and, like ``subprocess.run``, is strict:
    undecodable output raises ``UnicodeDecodeError`` after the child has been reaped; ``False``
    returns the ``bytes`` intact. ``stdin=None`` inherits the caller's stdin when ``input`` is
    also ``None``; ``input`` opens a pipe. ``stdin=subprocess.DEVNULL`` gives the child immediate
    EOF, and no other value is accepted. ``timeout`` (seconds, from the start) reaps the whole
    process group and raises ``subprocess.TimeoutExpired`` with the partial output as bytes.
    Signals to the caller reap the group like :func:`run_streaming`.
    """
    _require_seconds("drain_seconds", drain_seconds, positive=False)
    if timeout is not None:
        _require_seconds("timeout", timeout, positive=True)
    if stdin is not None and stdin != subprocess.DEVNULL:
        raise ValueError("stdin must be None or subprocess.DEVNULL")
    if stdin == subprocess.DEVNULL and input is not None:
        raise ValueError("input cannot be combined with stdin=subprocess.DEVNULL")
    encoding = "utf-8" if sys.flags.utf8_mode else locale.getencoding()
    if input is None:
        data = None
    elif text and isinstance(input, str):
        data = input.encode(encoding)
    elif not text and isinstance(input, (bytes, bytearray, memoryview)):
        data = bytes(input)
    else:
        raise TypeError(
            f"input must be {'str' if text else 'bytes'} when text={text}; got {type(input).__name__}"
        )
    child_stdin = (
        subprocess.PIPE
        if data is not None
        else subprocess.DEVNULL
        if stdin == subprocess.DEVNULL
        else None
    )
    start = time.monotonic()
    buffers: dict[int, list[bytes]] = {}
    state = {"timed_out": False}
    with _SignalScope(_SUPERVISION_SIGNALS) as scope:
        try:
            try:
                proc = scope.spawn(
                    argv, cwd=cwd, env=env, stdin=child_stdin,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0,
                )
                assert proc.stdout is not None and proc.stderr is not None
                fds = [proc.stdout.fileno(), proc.stderr.fileno()]
                buffers = {fd: [] for fd in fds}
                writer = (
                    None if data is None or proc.stdin is None else _StdinWriter(proc.stdin, data)
                )

                def guard(now: float, _progressed: bool) -> float:
                    due = start + timeout - now  # only installed when a timeout is set
                    if due > 0:
                        return due
                    state["timed_out"] = True
                    reap_process_group(proc, DEFAULT_TERM_GRACE_SECONDS)
                    return 0.0

                open_fds = _pump(
                    proc,
                    fds,
                    lambda fd, chunk: buffers[fd].append(chunk),
                    drain_seconds=drain_seconds,
                    guard=guard if timeout is not None else None,
                    writer=writer,
                )
                returncode = proc.returncode
                holders, warning = _abandonment(_argv0(argv), returncode, open_fds, drain_seconds)
            except BaseException:
                if scope.process is not None:
                    reap_process_group(scope.process, DEFAULT_TERM_GRACE_SECONDS)
                raise
        finally:
            if scope.process is not None:
                _close_pipes(scope.process.stdin, scope.process.stdout, scope.process.stderr)
    out, err = (b"".join(buffers[fd]) for fd in fds)
    if state["timed_out"]:
        raise subprocess.TimeoutExpired(proc.args, timeout, output=out, stderr=err)
    if text:
        out, err = (_make_decoder("strict").decode(part, True) for part in (out, err))
    return CaptureResult(proc.args, returncode, out, err, holders, warning)
