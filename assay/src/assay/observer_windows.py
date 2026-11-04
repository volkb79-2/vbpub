"""cgprofile observer contract 3 client: one measurement window per R2 candidate (B206).

The cgprofile daemon is the SINGLE source of per-candidate resource numbers
(memory peak, CPU, IO bytes and operations, PSI). This module is the only
place in Assay that talks to the observer's ``window-open``/``window-close``
verbs, and the only place that turns a reply into a ``resource-profile/1``
object (design ``docs/design/RESOURCE-PROFILES.md`` section 2).

There is no fallback. If the observer is absent, refuses, errors, or does not
speak contract 3, the candidate's profile is an explicit ``NO_MEASUREMENT``
carrying one reason code, every metric ``null`` and every ``missing`` entry
filled. Assay never reads ``/proc/<pid>/io``, ``/proc/<pid>/statm``,
``memory.peak`` or ``io.stat`` itself; a source test pins that.

Wire (written against cgprofile ``docs/PROTOCOL.md`` section 9 and the golden
fixtures copied into ``tests/fixtures/observer/``)::

    {"verb":"window-open","args":{"thread_id":T,"root_pid":P,"mode":M,"interval_ms":250},"contract":3}
    {"verb":"window-close","args":{"thread_id":T,"window":"w-..."},"contract":3}

One request line (4096 bytes at most) and one reply line per connection.

Where it runs. The window is opened by the process that spawns the candidate
(the isolated window host, or the coordinator for an injected in-process
runner) right after spawn, and closed after the root has exited but before it
is reaped, so the daemon reads exact CPU. The result of that exchange travels
back to the coordinator as a small JSON mapping (:class:`WindowResult`); the
coordinator owns the run-wide policy (ask contract support once, stop asking
after three consecutive failures) in :class:`ProfileCollector`.
"""

from __future__ import annotations

import collections
import contextlib
import ctypes
import json
import math
import os
import select
import socket
import subprocess
import threading
import time
from typing import Any, Iterator, Mapping

from .records import positional_record, record

PROFILE_SCHEMA = "resource-profile/1"
OBSERVER_NAME = "cgprofile"
OBSERVER_CONTRACT = 3
OBSERVER_SOCKET_ENV = "ASSAY_RESOURCE_OBSERVER_SOCKET"

MODE_CGROUP = "cgroup"
MODE_PROCESS_TREE = "process-tree"
MODES = (MODE_CGROUP, MODE_PROCESS_TREE)

DEFAULT_INTERVAL_MS = 250
REQUEST_LIMIT_BYTES = 4096
REPLY_LIMIT_BYTES = 1024 * 1024
#: Absolute deadline for one request/reply exchange; the daemon enforces the
#: same five seconds on its side.
REQUEST_TIMEOUT_SECONDS = 5.0
#: Consecutive refused/erroring windows after which the run stops asking.
MAX_CONSECUTIVE_FAILURES = 3

STATUS_MEASURED = "MEASURED"
STATUS_PARTIAL = "PARTIAL"
STATUS_NO_MEASUREMENT = "NO_MEASUREMENT"
STATUSES = (STATUS_MEASURED, STATUS_PARTIAL, STATUS_NO_MEASUREMENT)

OBSERVER_UNAVAILABLE = "OBSERVER_UNAVAILABLE"
OBSERVER_REFUSED = "OBSERVER_REFUSED"
OBSERVER_CONTRACT_UNSUPPORTED = "OBSERVER_CONTRACT_UNSUPPORTED"
OBSERVER_ERROR = "OBSERVER_ERROR"
WINDOW_LOST = "WINDOW_LOST"
NOT_STARTED = "NOT_STARTED"
#: Per-metric reasons the daemon itself reports in ``missing``.
SHARED_CGROUP = "SHARED_CGROUP"
REQUIRES_CGROUP_MODE = "REQUIRES_CGROUP_MODE"
CONTROLLER_DISABLED = "CONTROLLER_DISABLED"
PEAK_RESET_UNSUPPORTED = "PEAK_RESET_UNSUPPORTED"

CANDIDATE_REASONS = (
    OBSERVER_UNAVAILABLE,
    OBSERVER_REFUSED,
    OBSERVER_CONTRACT_UNSUPPORTED,
    OBSERVER_ERROR,
    WINDOW_LOST,
    NOT_STARTED,
)
METRIC_REASONS = (
    SHARED_CGROUP,
    REQUIRES_CGROUP_MODE,
    CONTROLLER_DISABLED,
    PEAK_RESET_UNSUPPORTED,
)
REASONS = CANDIDATE_REASONS + METRIC_REASONS

#: The fifteen metric keys every reply carries, in the contract's order.
METRIC_KEYS = (
    "rss_peak_bytes",
    "memory_peak_bytes",
    "swap_peak_bytes",
    "cpu_seconds",
    "cpu_user_seconds",
    "cpu_system_seconds",
    "io_read_bytes",
    "io_write_bytes",
    "io_read_ops",
    "io_write_ops",
    "psi_memory_some_seconds",
    "psi_memory_full_seconds",
    "psi_cpu_some_seconds",
    "psi_io_some_seconds",
    "psi_io_full_seconds",
)

_REFUSAL_CODES = frozenset({"peer-refused", "observer-refused"})
_WINDOW_ID_CHARS = frozenset("0123456789abcdef")


class ObserverFailure(Exception):
    """One window request failed; ``code`` is the profile reason code."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


# --------------------------------------------------------------------------
# The wire.
# --------------------------------------------------------------------------


def _exchange(
    socket_path: str, request: Mapping[str, Any], *, timeout: float
) -> dict[str, Any]:
    """Send one request line and return the one reply object.

    Raises :class:`ObserverFailure` with ``OBSERVER_UNAVAILABLE`` when the
    endpoint is absent or refuses connections, and ``OBSERVER_ERROR`` for a
    timeout, a transport failure or a reply that is not exactly one JSON
    object line.
    """
    line = (json.dumps(request, separators=(",", ":"), allow_nan=False) + "\n").encode(
        "ascii"
    )
    if len(line) > REQUEST_LIMIT_BYTES:
        raise ObserverFailure(OBSERVER_ERROR, "observer request exceeds 4096 bytes")
    deadline = time.monotonic() + timeout
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        client.settimeout(timeout)
        try:
            client.connect(socket_path)
        except (FileNotFoundError, ConnectionRefusedError, NotADirectoryError) as exc:
            raise ObserverFailure(
                OBSERVER_UNAVAILABLE, f"observer socket is not available: {exc}"
            ) from exc
        except PermissionError as exc:
            raise ObserverFailure(
                OBSERVER_REFUSED, f"observer socket refused access: {exc}"
            ) from exc
        except OSError as exc:
            raise ObserverFailure(
                OBSERVER_ERROR, f"cannot connect to the observer socket: {exc}"
            ) from exc
        try:
            client.sendall(line)
            chunks: list[bytes] = []
            size = 0
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ObserverFailure(OBSERVER_ERROR, "observer request timed out")
                client.settimeout(remaining)
                chunk = client.recv(65536)
                if not chunk:
                    break
                size += len(chunk)
                if size > REPLY_LIMIT_BYTES:
                    raise ObserverFailure(OBSERVER_ERROR, "observer reply exceeds 1 MiB")
                chunks.append(chunk)
                if b"\n" in chunk:
                    break
        except socket.timeout as exc:
            raise ObserverFailure(OBSERVER_ERROR, "observer request timed out") from exc
        except OSError as exc:
            raise ObserverFailure(
                OBSERVER_ERROR, f"observer request failed: {exc}"
            ) from exc
    finally:
        client.close()
    raw = b"".join(chunks)
    text, newline, rest = raw.partition(b"\n")
    if not newline or rest.strip():
        raise ObserverFailure(OBSERVER_ERROR, "observer reply is not exactly one JSON line")
    try:
        document = json.loads(text.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError) as exc:
        raise ObserverFailure(OBSERVER_ERROR, "observer reply is not valid JSON") from exc
    if not isinstance(document, dict):
        raise ObserverFailure(OBSERVER_ERROR, "observer reply is not an object")
    return document


def _check_reply(document: Mapping[str, Any], *, opening: bool) -> None:
    """Map a refusal or error reply to its reason code; return on ``ok``."""
    if document.get("ok") is True:
        if document.get("contract") != OBSERVER_CONTRACT:
            raise ObserverFailure(
                OBSERVER_CONTRACT_UNSUPPORTED,
                f"observer answered contract {document.get('contract')!r}, not "
                f"{OBSERVER_CONTRACT}",
            )
        return
    error = document.get("error")
    code = error.get("code") if isinstance(error, Mapping) else None
    message = error.get("message") if isinstance(error, Mapping) else None
    detail = f"observer replied {code!r}: {message!r}"
    if code in _REFUSAL_CODES:
        raise ObserverFailure(OBSERVER_REFUSED, detail)
    if code == "window-lost":
        raise ObserverFailure(WINDOW_LOST, detail)
    if code == "bad-argument" and opening:
        # The request is well formed, so a bad-argument on the first verb is
        # a daemon that does not know contract 3.
        raise ObserverFailure(OBSERVER_CONTRACT_UNSUPPORTED, detail)
    raise ObserverFailure(OBSERVER_ERROR, detail)


def _valid_window_id(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 34
        and value.startswith("w-")
        and all(char in _WINDOW_ID_CHARS for char in value[2:])
    )


def open_window(
    socket_path: str,
    *,
    root_pid: int,
    mode: str,
    interval_ms: int = DEFAULT_INTERVAL_MS,
    thread_id: int | None = None,
    timeout: float = REQUEST_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """``window-open`` for *root_pid*; returns the validated reply."""
    if mode not in MODES:
        raise ValueError(f"observer window mode must be one of {list(MODES)}")
    request = {
        "verb": "window-open",
        "args": {
            "thread_id": threading.get_native_id() if thread_id is None else thread_id,
            "root_pid": root_pid,
            "mode": mode,
            "interval_ms": interval_ms,
        },
        "contract": OBSERVER_CONTRACT,
    }
    reply = _exchange(socket_path, request, timeout=timeout)
    _check_reply(reply, opening=True)
    if (
        not _valid_window_id(reply.get("window"))
        or reply.get("mode") not in MODES
        or not isinstance(reply.get("started_at"), str)
    ):
        raise ObserverFailure(OBSERVER_ERROR, "observer window-open reply is malformed")
    return reply


def close_window(
    socket_path: str,
    *,
    window: str,
    thread_id: int | None = None,
    timeout: float = REQUEST_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """``window-close``; returns the validated reply (metrics and context)."""
    request = {
        "verb": "window-close",
        "args": {
            "thread_id": threading.get_native_id() if thread_id is None else thread_id,
            "window": window,
        },
        "contract": OBSERVER_CONTRACT,
    }
    reply = _exchange(socket_path, request, timeout=timeout)
    _check_reply(reply, opening=False)
    if reply.get("window") != window:
        raise ObserverFailure(OBSERVER_ERROR, "observer window-close names another window")
    _validate_close_reply(reply)
    return reply


def _metric_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value >= 0
    )


def _validate_close_reply(reply: Mapping[str, Any]) -> None:
    metrics = reply.get("metrics")
    missing = reply.get("missing")
    sources = reply.get("sources")
    context = reply.get("context")
    attribution = reply.get("attribution")
    if (
        not isinstance(metrics, Mapping)
        or not isinstance(missing, Mapping)
        or not isinstance(sources, Mapping)
        or not isinstance(context, Mapping)
        or not isinstance(attribution, Mapping)
        or set(metrics) != set(METRIC_KEYS)
    ):
        raise ObserverFailure(OBSERVER_ERROR, "observer window-close reply is malformed")
    for key in METRIC_KEYS:
        value = metrics[key]
        if value is None:
            if not isinstance(missing.get(key), str) or not missing[key]:
                raise ObserverFailure(
                    OBSERVER_ERROR, f"observer metric {key} is null without a reason"
                )
        elif not _metric_number(value):
            raise ObserverFailure(OBSERVER_ERROR, f"observer metric {key} is not a number")
    if set(missing) - set(METRIC_KEYS) or set(sources) - set(METRIC_KEYS):
        raise ObserverFailure(OBSERVER_ERROR, "observer reply names an unknown metric")
    if attribution.get("mode") not in MODES:
        raise ObserverFailure(OBSERVER_ERROR, "observer reply has no valid attribution mode")


# --------------------------------------------------------------------------
# The result of one candidate's exchange, and the profile built from it.
# --------------------------------------------------------------------------


@positional_record
class WindowConfig:
    """What the process that spawns a candidate needs to open its window."""

    socket_path: str | None
    mode: str
    interval_ms: int = DEFAULT_INTERVAL_MS
    #: A reason code when the run has already stopped asking: the candidate
    #: then records ``NO_MEASUREMENT`` with this reason and no request is sent.
    skip_reason: str | None = None

    def to_wire(self) -> dict[str, Any]:
        return {
            "socket_path": self.socket_path,
            "mode": self.mode,
            "interval_ms": self.interval_ms,
            "skip_reason": self.skip_reason,
        }

    @classmethod
    def from_wire(cls, value: Any) -> "WindowConfig":
        if not isinstance(value, Mapping) or set(value) != {
            "socket_path", "mode", "interval_ms", "skip_reason",
        }:
            raise ValueError("window-host profile config has an unexpected shape")
        socket_path = value["socket_path"]
        mode = value["mode"]
        interval = value["interval_ms"]
        skip = value["skip_reason"]
        if socket_path is not None and not isinstance(socket_path, str):
            raise ValueError("window-host profile socket path must be a string or null")
        if mode not in MODES:
            raise ValueError("window-host profile mode is invalid")
        if type(interval) is not int or not 100 <= interval <= 5000:
            raise ValueError("window-host profile interval is invalid")
        if skip is not None and skip not in CANDIDATE_REASONS:
            raise ValueError("window-host profile skip reason is invalid")
        return cls(socket_path, mode, interval, skip)


@record
class WindowResult:
    """The outcome of one candidate's open/close exchange.

    Exactly one of ``reason`` (the window produced no numbers) or ``reply``
    (the validated ``window-close`` reply) is set.
    """

    reason: str | None = None
    detail: str | None = None
    reply: dict[str, Any] | None = None
    mode: str | None = None
    window: str | None = None
    interval_ms: int = DEFAULT_INTERVAL_MS
    #: The daemon was actually asked (a request was sent), so the run-wide
    #: failure counter applies to this result.
    asked: bool = False

    def to_wire(self) -> dict[str, Any]:
        return {
            "reason": self.reason,
            "detail": self.detail,
            "reply": self.reply,
            "mode": self.mode,
            "window": self.window,
            "interval_ms": self.interval_ms,
            "asked": self.asked,
        }

    @classmethod
    def from_wire(cls, value: Any) -> "WindowResult":
        if not isinstance(value, Mapping) or set(value) != {
            "reason", "detail", "reply", "mode", "window", "interval_ms", "asked",
        }:
            raise ValueError("window-host profile result has an unexpected shape")
        reason = value["reason"]
        reply = value["reply"]
        if reason is not None and reason not in CANDIDATE_REASONS:
            raise ValueError("window-host profile reason is invalid")
        if (reason is None) == (reply is None):
            raise ValueError("window-host profile result needs exactly one of reason and reply")
        if reply is not None:
            if not isinstance(reply, dict):
                raise ValueError("window-host profile reply must be an object")
            try:
                _validate_close_reply(reply)
            except ObserverFailure as exc:
                raise ValueError(str(exc)) from exc
        detail = value["detail"]
        return cls(
            reason=reason,
            detail=detail if isinstance(detail, str) else None,
            reply=reply,
            mode=value["mode"] if value["mode"] in MODES else None,
            window=value["window"] if isinstance(value["window"], str) else None,
            interval_ms=value["interval_ms"] if type(value["interval_ms"]) is int else DEFAULT_INTERVAL_MS,
            asked=value["asked"] is True,
        )


def _all_missing(reason: str) -> dict[str, str]:
    return {key: reason for key in METRIC_KEYS}


def _no_measurement_profile(
    *,
    subject: Mapping[str, Any],
    wall_seconds: float | None,
    reason: str,
    mode: str | None,
    window: str | None,
    interval_ms: int,
) -> dict[str, Any]:
    return {
        "schema": PROFILE_SCHEMA,
        "subject": dict(subject),
        "wall_seconds": wall_seconds,
        "status": STATUS_NO_MEASUREMENT,
        "reason": reason,
        "observer": {
            "name": OBSERVER_NAME,
            "version": None,
            "contract": OBSERVER_CONTRACT,
            "mode": mode,
            "window": window,
            "sample_interval_seconds": interval_ms / 1000.0,
        },
        "metrics": {key: None for key in METRIC_KEYS},
        "missing": _all_missing(reason),
        "sources": {},
        "context": {},
    }


def build_profile(
    result: WindowResult | None,
    *,
    subject: Mapping[str, Any],
    wall_seconds: float | None,
    not_started: bool = False,
) -> dict[str, Any]:
    """The ``resource-profile/1`` object for one candidate.

    *wall_seconds* is Assay's own monotonic duration and is present whatever
    the status. A candidate that never ran, or whose exchange produced no
    reply, is ``NO_MEASUREMENT`` with one reason; a reply whose metrics are
    all ``null`` is ``NO_MEASUREMENT`` too, with the first metric's reason.
    ``REQUIRES_CGROUP_MODE`` nulls are by design in process-tree mode and do
    not make a profile ``PARTIAL``; any other null does.
    """
    if not_started:
        return _no_measurement_profile(
            subject=subject, wall_seconds=wall_seconds, reason=NOT_STARTED,
            mode=None, window=None, interval_ms=DEFAULT_INTERVAL_MS,
        )
    if result is None:
        return _no_measurement_profile(
            subject=subject, wall_seconds=wall_seconds, reason=WINDOW_LOST,
            mode=None, window=None, interval_ms=DEFAULT_INTERVAL_MS,
        )
    if result.reply is None:
        assert result.reason is not None
        return _no_measurement_profile(
            subject=subject, wall_seconds=wall_seconds, reason=result.reason,
            mode=result.mode, window=result.window, interval_ms=result.interval_ms,
        )
    reply = result.reply
    metrics = {key: reply["metrics"][key] for key in METRIC_KEYS}
    missing = dict(reply["missing"])
    if all(value is None for value in metrics.values()):
        first = missing[METRIC_KEYS[0]]
        return _no_measurement_profile(
            subject=subject, wall_seconds=wall_seconds,
            reason=first if first in REASONS else OBSERVER_ERROR,
            mode=result.mode, window=result.window, interval_ms=result.interval_ms,
        )
    partial = any(
        reason != REQUIRES_CGROUP_MODE for reason in missing.values()
    )
    return {
        "schema": PROFILE_SCHEMA,
        "subject": dict(subject),
        "wall_seconds": wall_seconds,
        "status": STATUS_PARTIAL if partial else STATUS_MEASURED,
        "reason": None,
        "observer": {
            "name": OBSERVER_NAME,
            "version": None,
            "contract": OBSERVER_CONTRACT,
            "mode": reply["attribution"]["mode"],
            "window": result.window,
            "sample_interval_seconds": result.interval_ms / 1000.0,
        },
        "metrics": metrics,
        "missing": missing,
        "sources": dict(reply["sources"]),
        "context": dict(reply["context"]),
        "attribution": dict(reply["attribution"]),
    }


def validate_profile(profile: Any) -> None:
    """Raise ``ValueError`` unless *profile* is a well-formed
    ``resource-profile/1`` object as Assay writes it."""
    if not isinstance(profile, Mapping) or profile.get("schema") != PROFILE_SCHEMA:
        raise ValueError("profile must be a resource-profile/1 object")
    status = profile.get("status")
    if status not in STATUSES:
        raise ValueError(f"profile status must be one of {list(STATUSES)}")
    reason = profile.get("reason")
    if (status == STATUS_NO_MEASUREMENT) != (reason is not None):
        raise ValueError("profile reason is set exactly when status is NO_MEASUREMENT")
    if reason is not None and reason not in REASONS:
        raise ValueError("profile reason is not a known reason code")
    wall = profile.get("wall_seconds")
    if wall is not None and not _metric_number(wall):
        raise ValueError("profile wall_seconds must be a non-negative number or null")
    metrics = profile.get("metrics")
    missing = profile.get("missing")
    if not isinstance(metrics, Mapping) or set(metrics) != set(METRIC_KEYS):
        raise ValueError("profile metrics must carry every metric key")
    if not isinstance(missing, Mapping):
        raise ValueError("profile missing must be an object")
    for key in METRIC_KEYS:
        value = metrics[key]
        if value is None:
            if not isinstance(missing.get(key), str):
                raise ValueError(f"profile metric {key} is null without a reason")
        elif not _metric_number(value):
            raise ValueError(f"profile metric {key} is not a number")
    if status == STATUS_NO_MEASUREMENT and any(v is not None for v in metrics.values()):
        raise ValueError("a NO_MEASUREMENT profile carries no metric values")
    if not isinstance(profile.get("observer"), Mapping):
        raise ValueError("profile observer must be an object")


# --------------------------------------------------------------------------
# Per-thread activation: how a runner finds the window it should open.
# --------------------------------------------------------------------------


class WindowSlot:
    """One candidate's window state, shared by the runner and its caller."""

    def __init__(self, config: WindowConfig) -> None:
        self.config = config
        self.result: WindowResult | None = None

    @property
    def active(self) -> bool:
        """Whether the runner should open a window at all (and therefore set
        the child subreaper and wait without reaping)."""
        return self.config.socket_path is not None and self.config.skip_reason is None


_local = threading.local()


@contextlib.contextmanager
def activate(config: WindowConfig | None) -> Iterator[WindowSlot | None]:
    """Make *config* the current thread's window for the next spawn(s)."""
    if config is None:
        yield None
        return
    slot = WindowSlot(config)
    previous = getattr(_local, "slot", None)
    _local.slot = slot
    try:
        if not slot.active:
            slot.result = WindowResult(
                reason=config.skip_reason or OBSERVER_UNAVAILABLE,
                detail="no observer window requested",
                mode=config.mode,
                interval_ms=config.interval_ms,
                asked=False,
            )
        yield slot
    finally:
        _local.slot = previous


def current_slot() -> WindowSlot | None:
    slot = getattr(_local, "slot", None)
    return slot if slot is not None and slot.active else None


_PR_SET_CHILD_SUBREAPER = 36
_libc: Any = None


def _load_libc() -> Any:
    global _libc
    if _libc is None:
        _libc = ctypes.CDLL(None, use_errno=True)
    return _libc


def subreaper_preexec(slot: WindowSlot | None) -> dict[str, Any]:
    """``Popen`` keyword arguments that make the candidate root a child
    subreaper, so orphaned grandchildren stay in its tree (otherwise they
    reparent to the caller and the daemon sees them as foreign). Empty when
    no window is requested."""
    if slot is None:
        return {}
    libc = _load_libc()
    prctl = libc.prctl

    def _set() -> None:
        prctl(_PR_SET_CHILD_SUBREAPER, 1, 0, 0, 0)

    return {"preexec_fn": _set}


class OpenWindow:
    """An open window; :meth:`close` is idempotent and records the result."""

    def __init__(self, slot: WindowSlot, reply: Mapping[str, Any], thread_id: int) -> None:
        self._slot = slot
        self._window = reply["window"]
        self._mode = reply["mode"]
        self._thread_id = thread_id
        self._closed = False

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        config = self._slot.config
        assert config.socket_path is not None
        try:
            reply = close_window(
                config.socket_path, window=self._window, thread_id=self._thread_id
            )
        except ObserverFailure as exc:
            self._slot.result = WindowResult(
                reason=exc.code, detail=exc.detail, mode=self._mode,
                window=self._window, interval_ms=config.interval_ms, asked=True,
            )
            return
        self._slot.result = WindowResult(
            reply=reply, mode=self._mode, window=self._window,
            interval_ms=config.interval_ms, asked=True,
        )

    def wait_exited(self, proc: "subprocess.Popen[Any]", timeout: float | None) -> bool:
        """Wait until the root has exited WITHOUT reaping it. ``False`` on
        timeout. Used so the window closes while the root is still a zombie."""
        return wait_exited_unreaped(proc.pid, timeout)


def open_for_root(root_pid: int) -> OpenWindow | None:
    """Open the current slot's window on *root_pid*, right after spawn.

    ``None`` when no window is requested or the open failed; a failure is
    recorded in the slot so the candidate's profile carries the reason.
    """
    slot = current_slot()
    if slot is None:
        return None
    config = slot.config
    assert config.socket_path is not None
    thread_id = threading.get_native_id()
    try:
        reply = open_window(
            config.socket_path,
            root_pid=root_pid,
            mode=config.mode,
            interval_ms=config.interval_ms,
            thread_id=thread_id,
        )
    except ObserverFailure as exc:
        slot.result = WindowResult(
            reason=exc.code, detail=exc.detail, mode=config.mode,
            interval_ms=config.interval_ms, asked=True,
        )
        return None
    except (ValueError, TypeError, OverflowError) as exc:
        # A measurement problem is never a candidate failure.
        slot.result = WindowResult(
            reason=OBSERVER_ERROR, detail=f"observer window-open failed: {exc}",
            mode=config.mode, interval_ms=config.interval_ms, asked=True,
        )
        return None
    return OpenWindow(slot, reply, thread_id)


def wait_exited_unreaped(pid: int, timeout: float | None) -> bool:
    """Block until *pid* has exited, without reaping it (``waitid`` with
    ``WNOWAIT`` semantics, via a pidfd when the kernel has one). ``False`` if
    it is still running after *timeout* seconds."""
    deadline = None if timeout is None else time.monotonic() + timeout
    try:
        pidfd = os.pidfd_open(pid)
    except (AttributeError, OSError):
        pidfd = None
    try:
        while True:
            if _exited_unreaped(pid):
                return True
            remaining = None if deadline is None else deadline - time.monotonic()
            if remaining is not None and remaining <= 0:
                return False
            if pidfd is not None:
                wait = 0.5 if remaining is None else min(0.5, remaining)
                select.select([pidfd], [], [], wait)
            else:
                time.sleep(0.02 if remaining is None else min(0.02, remaining))
    finally:
        if pidfd is not None:
            os.close(pidfd)


def _exited_unreaped(pid: int) -> bool:
    try:
        return (
            os.waitid(os.P_PID, pid, os.WEXITED | os.WNOHANG | os.WNOWAIT) is not None
        )
    except ChildProcessError:
        # Already reaped by someone else: it has exited.
        return True


def exited_unreaped(pid: int) -> bool:
    """Whether *pid* has exited, without reaping it."""
    return _exited_unreaped(pid)


# --------------------------------------------------------------------------
# The coordinator: run-wide policy and the run summary.
# --------------------------------------------------------------------------


class ProfileCollector:
    """Run-wide state for one native R2 sweep (lives in the coordinator).

    * the observer socket comes from ``ASSAY_RESOURCE_OBSERVER_SOCKET``; unset
      means ``OBSERVER_UNAVAILABLE`` for every candidate, nothing is asked;
    * contract support is asked exactly once: a contract answer of
      ``OBSERVER_CONTRACT_UNSUPPORTED`` ends all asking;
    * three consecutive refused or erroring windows end all asking, and the
      remaining candidates record ``OBSERVER_ERROR``;
    * a window lost to a daemon restart is not a failure of the next ask.
    """

    def __init__(
        self, socket_path: str | None, jobs: int, interval_ms: int = DEFAULT_INTERVAL_MS
    ) -> None:
        self.socket_path = socket_path
        self.jobs = jobs
        self.interval_ms = interval_ms
        self.requests_asked = 0
        self._lock = threading.Lock()
        self._stopped_reason: str | None = None
        self._consecutive_failures = 0
        self._finished: dict[str, tuple[str, str | None, str | None]] = {}

    @property
    def mode(self) -> str:
        return MODE_CGROUP if self.jobs <= 1 else MODE_PROCESS_TREE

    def config(self) -> WindowConfig:
        """The window configuration for the next candidate to start."""
        with self._lock:
            skip = self._stopped_reason
        if self.socket_path is None:
            skip = OBSERVER_UNAVAILABLE
        return WindowConfig(self.socket_path, self.mode, self.interval_ms, skip)

    def record(self, result: WindowResult | None) -> None:
        """Fold one candidate's exchange into the run-wide policy."""
        if result is None or not result.asked:
            return
        with self._lock:
            self.requests_asked += 1
            if result.reason is None:
                self._consecutive_failures = 0
            elif result.reason == OBSERVER_CONTRACT_UNSUPPORTED:
                self._stopped_reason = OBSERVER_CONTRACT_UNSUPPORTED
            elif result.reason in (OBSERVER_ERROR, OBSERVER_REFUSED, OBSERVER_UNAVAILABLE):
                self._consecutive_failures += 1
                if (
                    self._consecutive_failures >= MAX_CONSECUTIVE_FAILURES
                    and self._stopped_reason is None
                ):
                    self._stopped_reason = (
                        OBSERVER_UNAVAILABLE
                        if result.reason == OBSERVER_UNAVAILABLE
                        else OBSERVER_ERROR
                    )

    def finish(self, candidate: str, profile: Mapping[str, Any]) -> None:
        """Keep one finished candidate's profile outcome for the run summary.

        Keyed by candidate id: a candidate that is re-run (a retried crash)
        counts once, with its final profile.
        """
        with self._lock:
            self._finished[candidate] = (
                profile["status"],
                profile["reason"],
                profile["observer"].get("mode"),
            )

    def counters(self) -> tuple[collections.Counter, collections.Counter, set]:
        """``(status counts, reason counts, modes)`` over this run's candidates."""
        with self._lock:
            statuses: collections.Counter = collections.Counter()
            reasons: collections.Counter = collections.Counter()
            modes: set = set()
            for status, reason, mode in self._finished.values():
                statuses[status] += 1
                if reason is not None:
                    reasons[reason] += 1
                if mode is not None:
                    modes.add(mode)
            return statuses, reasons, modes

    def summary(self) -> dict[str, Any]:
        """``judgment.r2.profile`` (design section 4.2)."""
        return summarize(*self.counters())


def summarize(
    status_counts: Mapping[str, int],
    reason_counts: Mapping[str, int],
    modes: Any,
) -> dict[str, Any]:
    return {
        "status_counts": {name: int(status_counts.get(name, 0)) for name in STATUSES},
        "reason_counts": {
            name: int(count) for name, count in sorted(reason_counts.items()) if count
        },
        "observer": {
            "name": OBSERVER_NAME,
            "version": None,
            "contracts": [OBSERVER_CONTRACT],
            "modes": sorted(modes),
        },
    }
