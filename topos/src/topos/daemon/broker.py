from __future__ import annotations

import json
import math
import os
import secrets
import socket
import socketserver
import threading
import time
from collections import deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from topos.daemon.component_health import (
    ComponentError,
    ComponentHealthRegistry,
    build_health_response,
)
from topos.model import Frame, frame_to_jsonable


MAX_STREAM_LIMIT = 1000
MAX_REQUEST_BYTES = 1024 * 1024
DEFAULT_REQUEST_TIMEOUT_S = 5.0
DEFAULT_MAX_CLIENTS = 32


class FrameBrokerError(RuntimeError):
    """Base for public-safe broker lifecycle errors."""


class FrameUnavailableError(FrameBrokerError):
    """No valid frame is available."""


class FrameProducerError(FrameBrokerError):
    """The producer failed; the original exception is intentionally hidden."""


class FrameProducerTimeoutError(FrameBrokerError):
    """The producer did not stop before a bounded join deadline."""


@dataclass(frozen=True)
class FrameBatch:
    """A bounded history read plus explicit cursor/eviction metadata."""

    entries: tuple[tuple[int, Frame], ...]
    oldest_seq: int | None
    latest_seq: int | None
    next_cursor: int | None
    gap: bool = False


_TerminalKind = Literal["exhausted", "failed", "stopped"]


class FrameBroker:
    """One request-independent producer with bounded sequenced fan-out.

    ``stop_callback`` should interrupt waits inside a production iterator. An
    arbitrary iterator can still block forever inside ``next()``; in that case
    ``join(timeout=...)`` raises :class:`FrameProducerTimeoutError` rather than
    falsely reporting a clean shutdown.
    """

    def __init__(
        self,
        frame_source: Iterable[Frame],
        *,
        history_size: int = 120,
        startup_timeout_s: float = 5.0,
        health_registry: ComponentHealthRegistry | None = None,
        stop_callback: Callable[[], None] | None = None,
        terminal_callback: Callable[[str], None] | None = None,
    ) -> None:
        if isinstance(history_size, bool) or not isinstance(history_size, int):
            raise TypeError("history_size must be an integer")
        if history_size < 1:
            raise ValueError("history_size must be at least 1")
        if isinstance(startup_timeout_s, bool) or not isinstance(startup_timeout_s, (int, float)):
            raise TypeError("startup_timeout_s must be a number")
        if not 0.0 < float(startup_timeout_s) <= 60.0:
            raise ValueError("startup_timeout_s must be greater than 0 and at most 60 seconds")

        self._source = iter(frame_source)
        self._history: deque[tuple[int, Frame]] = deque(maxlen=history_size)
        self._condition = threading.Condition(threading.Lock())
        self._lifecycle_lock = threading.Lock()
        self._startup_timeout_s = float(startup_timeout_s)
        self._health_registry = health_registry
        self._stop_callback = stop_callback
        # Called once, from the producer thread, when the producer ends on its
        # own (kind "failed" or "exhausted"); lets the daemon exit non-zero
        # instead of serving a frozen last frame forever.
        self._terminal_callback = terminal_callback
        # Per-start identity: sequence numbers restart at 0 on every daemon
        # start, so clients compare epochs to detect a restart.
        self.epoch = secrets.token_hex(8)
        self._stop_requested = threading.Event()
        self._started = False
        self._sequence = 0
        self._terminal: _TerminalKind | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """Start exactly one producer, even under concurrent callers."""
        with self._lifecycle_lock:
            if self._started:
                return
            if self._stop_requested.is_set():
                raise FrameBrokerError("frame producer cannot restart after stop")
            self._started = True
            thread = threading.Thread(
                target=self._producer_loop,
                name="topos-broker-producer",
                daemon=True,
            )
            self._thread = thread
            try:
                thread.start()
            except BaseException:
                self._thread = None
                self._started = False
                raise

    def stop(self) -> None:
        """Signal producer shutdown and interrupt the configured source wait."""
        self._stop_requested.set()
        callback = self._stop_callback
        if callback is not None:
            try:
                callback()
            except Exception:
                # Shutdown must still wake readers and attempt to join. Source
                # callbacks are cleanup aids, never a reason to lose state.
                pass
        with self._condition:
            self._condition.notify_all()

    def join(self, timeout: float | None = None) -> None:
        """Join the producer or raise a persistent, typed terminal error."""
        thread = self._thread
        if thread is not None:
            thread.join(timeout=timeout)
            if thread.is_alive():
                raise FrameProducerTimeoutError("frame producer shutdown timed out")
        with self._condition:
            if self._terminal == "failed":
                raise FrameProducerError("frame producer failed")

    @property
    def producer_alive(self) -> bool:
        thread = self._thread
        return bool(thread is not None and thread.is_alive())

    @property
    def terminal_kind(self) -> str | None:
        with self._condition:
            return self._terminal

    def _ensure_started(self) -> None:
        with self._lifecycle_lock:
            started = self._started
        if not started:
            self.start()

    def current_entry(self) -> tuple[int, Frame]:
        """Return the latest valid entry, waiting boundedly for the first."""
        self._ensure_started()
        deadline = time.monotonic() + self._startup_timeout_s
        with self._condition:
            while not self._history and self._terminal is None and not self._stop_requested.is_set():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self._condition.wait(timeout=remaining)
            if self._history:
                return self._history[-1]
            if self._terminal == "failed":
                raise FrameProducerError("frame producer failed before publishing a frame")
            if self._terminal == "exhausted":
                raise FrameUnavailableError("frame source exhausted before publishing a frame")
            if self._terminal == "stopped" or self._stop_requested.is_set():
                raise FrameUnavailableError("frame producer stopped before publishing a frame")
            raise FrameUnavailableError(
                f"no frame available within {self._startup_timeout_s:g}s startup timeout"
            )

    def current(self) -> Frame:
        return self.current_entry()[1]

    def stream(self, limit: int = 1, cursor: int | None = None) -> FrameBatch:
        """Read published history without consuming it or waiting for sampling."""
        limit = _validate_limit(limit)
        cursor = _validate_cursor(cursor)
        self._ensure_started()
        with self._condition:
            if not self._history:
                # A cursor ahead of everything this daemon has published can
                # only come from a previous run (sequences restart at 0).
                stale = cursor is not None and cursor > self._sequence - 1
                return FrameBatch((), None, None, cursor, stale)
            oldest = self._history[0][0]
            latest = self._history[-1][0]
            if cursor is None:
                entries = tuple(list(self._history)[-limit:])
                gap = False
            else:
                gap = cursor < oldest - 1 or cursor > latest
                entries = tuple((seq, frame) for seq, frame in self._history if seq > cursor)[:limit]
            next_cursor = entries[-1][0] if entries else cursor
            return FrameBatch(entries, oldest, latest, next_cursor, gap)

    def stream_window(
        self,
        *,
        since_ts: float | None = None,
        until_ts: float | None = None,
        limit: int = 1,
    ) -> FrameBatch:
        """Read published history filtered by a time window (P52).

        ``since_ts`` is inclusive, ``until_ts`` is exclusive. Either may be
        omitted to mean unbounded on that side. ``gap`` is True when the
        window's lower bound precedes the oldest retained frame's timestamp
        (history was evicted inside the requested window). Cursor metadata is
        identical to :meth:`stream`.
        """
        limit = _validate_limit(limit)
        if since_ts is not None:
            since_ts = _validate_finite(since_ts, "since_ts")
        if until_ts is not None:
            until_ts = _validate_finite(until_ts, "until_ts")
        if since_ts is not None and until_ts is not None and since_ts > until_ts:
            raise ValueError("since_ts must not exceed until_ts")
        self._ensure_started()
        with self._condition:
            if not self._history:
                return FrameBatch((), None, None, None, False)
            oldest_seq, oldest_frame = self._history[0]
            latest_seq = self._history[-1][0]
            gap = since_ts is not None and since_ts < oldest_frame.ts
            selected: list[tuple[int, Frame]] = []
            for seq, frame in self._history:
                if since_ts is not None and frame.ts < since_ts:
                    continue
                if until_ts is not None and frame.ts >= until_ts:
                    continue
                selected.append((seq, frame))
            entries = tuple(selected[-limit:]) if selected else ()
            next_cursor = entries[-1][0] if entries else None
            return FrameBatch(entries, oldest_seq, latest_seq, next_cursor, gap)

    def history_capacity(self) -> int:
        """Return the configured history capacity (bounded retention bound)."""
        return self._history.maxlen or 0

    def _set_terminal(self, kind: _TerminalKind) -> None:
        newly_set = False
        with self._condition:
            if self._terminal is None:
                self._terminal = kind
                newly_set = True
            self._condition.notify_all()
        callback = self._terminal_callback
        if newly_set and callback is not None and kind != "stopped":
            try:
                callback(kind)
            except Exception:
                pass

    def _producer_loop(self) -> None:
        try:
            while not self._stop_requested.is_set():
                try:
                    frame = next(self._source)
                except StopIteration:
                    self._set_terminal("exhausted")
                    self._record_source_exhausted()
                    return
                except Exception:
                    self._set_terminal("failed")
                    self._record_source_failed()
                    return
                if not isinstance(frame, Frame):
                    self._set_terminal("failed")
                    self._record_source_failed()
                    return
                with self._condition:
                    seq = self._sequence
                    self._sequence += 1
                    self._history.append((seq, frame))
                    self._condition.notify_all()
                if self._health_registry is not None:
                    self._health_registry.record_success(
                        "collector", detail="frame collection succeeded"
                    )
            self._set_terminal("stopped")
        finally:
            if self._stop_requested.is_set():
                self._set_terminal("stopped")

    def _record_source_exhausted(self) -> None:
        if self._health_registry is not None:
            self._health_registry.record_failure(
                "collector",
                detail="frame source exhausted",
                error=ComponentError(
                    message="frame source exhausted",
                    error_code="collector_source_exhausted",
                ),
            )

    def _record_source_failed(self) -> None:
        if self._health_registry is not None:
            self._health_registry.record_failure(
                "collector",
                detail="frame collection failed",
                error=ComponentError(
                    message="frame collection failed",
                    error_code="collector_collection_failed",
                ),
            )

    def responses(self, request: dict[str, Any]) -> list[dict[str, Any]]:
        op = request.get("op")
        if op == "current":
            if set(request) != {"op"}:
                return [_error_response("invalid current request")]
            try:
                seq, frame = self.current_entry()
                return [_frame_response(frame, seq=seq), {"type": "end", "count": 1, "next_cursor": seq}]
            except FrameBrokerError as exc:
                return [_error_response(str(exc))]
        if op == "stream":
            if not set(request).issubset({"op", "limit", "cursor"}):
                return [_error_response("invalid stream request")]
            try:
                batch = self.stream(
                    limit=_validate_limit(request.get("limit", 1)),
                    cursor=_validate_cursor(request.get("cursor")),
                )
            except (TypeError, ValueError) as exc:
                return [_error_response(str(exc))]
            end: dict[str, Any] = {
                "type": "end",
                "count": len(batch.entries),
                "gap": batch.gap,
                "oldest_seq": batch.oldest_seq,
                "latest_seq": batch.latest_seq,
                "next_cursor": batch.next_cursor,
                "epoch": self.epoch,
            }
            return [*[_frame_response(frame, seq=seq) for seq, frame in batch.entries], end]
        if op == "health":
            if set(request) != {"op"}:
                return [_error_response("invalid health request")]
            return [self._health_response()]
        return [_error_response("unsupported operation")]

    def _health_response(self) -> dict[str, Any]:
        if self._health_registry is not None:
            return build_health_response(self._health_registry)
        return _error_response("health not available: daemon was started without a health registry")


def _validate_limit(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("stream limit must be an integer")
    if not 1 <= value <= MAX_STREAM_LIMIT:
        raise ValueError(f"stream limit must be between 1 and {MAX_STREAM_LIMIT}")
    return value


def _validate_cursor(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("stream cursor must be an integer")
    if value < -1:
        raise ValueError("stream cursor must be at least -1")
    return value


def _validate_finite(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{field} must be a number")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{field} must be finite")
    return result


class BrokerUnixServer(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True
    block_on_close = False

    def __init__(
        self,
        socket_path: Path,
        broker: FrameBroker,
        *,
        request_timeout_s: float = DEFAULT_REQUEST_TIMEOUT_S,
        max_clients: int = DEFAULT_MAX_CLIENTS,
    ) -> None:
        self.socket_path = socket_path
        self.broker = broker
        self.request_timeout_s = request_timeout_s
        self._client_slots = threading.BoundedSemaphore(max_clients)
        super().__init__(str(socket_path), _BrokerHandler)

    def busy_payload(self, request: socket.socket) -> dict[str, Any]:
        """Reply body for a rejected connection (legacy shape; envelope server overrides)."""
        return _error_response("server busy")

    def process_request(self, request: socket.socket, client_address: Any) -> None:
        if not self._client_slots.acquire(blocking=False):
            try:
                request.sendall(json.dumps(self.busy_payload(request), separators=(",", ":")).encode() + b"\n")
            except (OSError, ValueError, TypeError):
                pass
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._client_slots.release()
            raise

    def process_request_thread(self, request: socket.socket, client_address: Any) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._client_slots.release()

    def server_close(self) -> None:
        super().server_close()
        self.socket_path.unlink(missing_ok=True)


class TotalDeadline:
    """A whole-phase time bound for a socket, not a per-``recv`` one.

    ``socket.settimeout`` restarts on every ``recv``/``send``, so a peer that
    trickles one byte (or drains one byte) per timeout period holds a client
    slot indefinitely. This context manager shuts the socket down when the
    total budget is spent, which unblocks a blocked read or write at once.
    ``how`` is ``socket.SHUT_RD`` for the request phase (the error reply can
    still be written) and ``socket.SHUT_RDWR`` for the response phase.
    """

    def __init__(self, sock: Any, seconds: float, how: int) -> None:
        self.fired = False
        self._sock = sock
        self._how = how
        self._timer = threading.Timer(float(seconds), self._fire)
        self._timer.daemon = True

    def _fire(self) -> None:
        self.fired = True
        try:
            self._sock.shutdown(self._how)
        except Exception:
            pass

    def __enter__(self) -> "TotalDeadline":
        self._timer.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self._timer.cancel()


class _BrokerHandler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        timeout_s = self.server.request_timeout_s  # type: ignore[attr-defined]
        self.connection.settimeout(timeout_s)
        try:
            with TotalDeadline(self.connection, timeout_s, socket.SHUT_RD) as read_deadline:
                line = self.rfile.readline(MAX_REQUEST_BYTES + 1)
            if read_deadline.fired and not line.endswith(b"\n"):
                raise socket.timeout("request deadline exceeded")
            if not line:
                return
            if len(line) > MAX_REQUEST_BYTES or not line.endswith(b"\n"):
                responses = [_error_response("request exceeds maximum size")]
            else:
                request = json.loads(line.decode("utf-8"))
                if not isinstance(request, dict):
                    raise ValueError("request must be an object")
                responses = self.server.broker.responses(request)  # type: ignore[attr-defined]
        except socket.timeout:
            responses = [_error_response("request timed out")]
        except (UnicodeError, json.JSONDecodeError, ValueError) as exc:
            responses = [_error_response(str(exc))]
        except Exception:
            responses = [_error_response("request failed")]
        try:
            with TotalDeadline(self.connection, timeout_s, socket.SHUT_RDWR):
                for response in responses:
                    self.wfile.write(
                        json.dumps(response, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"
                    )
        except OSError:
            # Peer went away or the total write deadline closed the socket.
            pass


def serve_unix_socket(
    socket_path: Path,
    broker: FrameBroker,
    *,
    mode: int = 0o660,
    request_timeout_s: float = DEFAULT_REQUEST_TIMEOUT_S,
    max_clients: int = DEFAULT_MAX_CLIENTS,
) -> BrokerUnixServer:
    socket_path.parent.mkdir(parents=True, exist_ok=True)
    socket_path.unlink(missing_ok=True)
    server = BrokerUnixServer(
        socket_path,
        broker,
        request_timeout_s=request_timeout_s,
        max_clients=max_clients,
    )
    os.chmod(socket_path, mode)
    return server


def _frame_response(frame: Frame, seq: int | None = None) -> dict[str, Any]:
    response: dict[str, Any] = {"type": "frame", "frame": frame_to_jsonable(frame)}
    if seq is not None:
        response["seq"] = seq
    return response


def _error_response(message: str) -> dict[str, Any]:
    return {"type": "error", "error": message}
