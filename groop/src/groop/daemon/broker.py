from __future__ import annotations

import json
import os
import socketserver
import threading
from collections import deque
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

from groop.model import Frame, frame_to_jsonable

# ── typed errors ──────────────────────────────────────────────────────────


class FrameBrokerError(RuntimeError):
    """Base for FrameBroker errors exposed to clients."""


class FrameUnavailableError(FrameBrokerError):
    """No frame is available yet or the source is exhausted or failed."""


class FrameProducerError(FrameBrokerError):
    """The background producer exited with an error."""


# ── broker ────────────────────────────────────────────────────────────────


class FrameBroker:
    """Thread-safe frame broker with a request-independent background producer.

    A single thread continuously advances *frame_source* and publishes each
    frame into a bounded sequenced history.  Read operations (current, stream)
    never call ``next()`` on the source — they consume from the shared history.

    Lifecycle
    ---------
    ``start()`` must be called once before any requests are served; it spawns
    the producer thread.  ``stop()`` signals the thread to exit; ``join()``
    waits for it.  Calling ``start()`` more than once is safe (no-op after the
    first).  The producer is automatically ``daemon=True`` so a crashed call
    site does not leak the process, but ``stop()+join()`` should still be
    called for deterministic teardown.
    """

    def __init__(
        self,
        frame_source: Iterable[Frame],
        *,
        history_size: int = 120,
        startup_timeout_s: float = 5.0,
        source_error_limit: int = 5,
    ) -> None:
        self._source = iter(frame_source)
        self._history: deque[tuple[int, Frame]] = deque(maxlen=max(1, history_size))
        self._lock = threading.Lock()
        self._new_frame = threading.Condition(self._lock)
        self._startup_timeout_s = max(0.1, startup_timeout_s)
        self._source_error_limit = max(1, source_error_limit)
        self._started = threading.Event()
        self._stopped = threading.Event()
        self._producer_exc: BaseException | None = None
        self._sequence: int = 0
        self._thread: threading.Thread | None = None

    # ── lifecycle ─────────────────────────────────────────────────────

    def start(self) -> None:
        """Start the background producer thread.

        Safe to call multiple times — subsequent calls are a no-op.
        """
        if self._started.is_set():
            return
        self._started.set()
        self._thread = threading.Thread(
            target=self._producer_loop,
            name="groop-broker-producer",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        """Signal the producer to stop after its current frame."""
        self._stopped.set()

    def join(self, timeout: float | None = None) -> None:
        """Join the producer thread, re-raising any captured exception."""
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=timeout)
        self._rethrow()

    def _rethrow(self) -> None:
        exc = self._producer_exc
        if exc is not None:
            self._producer_exc = None
            raise exc  # noqa: B904 - explicit re-raise of captured error

    # ── read operations ───────────────────────────────────────────────

    def _ensure_started(self) -> None:
        if not self._started.is_set():
            self.start()

    def current(self) -> Frame:
        """Return the latest published frame.

        Blocks until the first frame is available (bounded by
        *startup_timeout_s*) and then returns the most recently published
        frame on every call without blocking again — a reader always sees
        the freshest data the producer has written.

        Raises
        ------
        FrameUnavailableError
            No frame was produced within the startup timeout, the source
            is exhausted, or the producer thread terminated without
            producing any frame.
        FrameProducerError
            The producer thread raised an unexpected exception.
        """
        self._ensure_started()
        self._rethrow()
        with self._new_frame:
            if self._history:
                return self._history[-1][1]
            # Wait for the first frame within the bounded startup window.
            self._new_frame.wait(timeout=self._startup_timeout_s)
            if self._history:
                return self._history[-1][1]
            self._rethrow()
            exc = self._producer_exc
            if isinstance(exc, StopIteration):
                raise FrameUnavailableError("frame source is exhausted") from exc
            if exc is not None:
                raise FrameUnavailableError(
                    f"producer failed before producing a frame: {exc}"
                ) from exc
            if self._stopped.is_set():
                raise FrameUnavailableError("producer was stopped before producing a frame")
            raise FrameUnavailableError(
                f"no frame available within {self._startup_timeout_s}s startup timeout"
            )

    def stream(
        self,
        limit: int = 1,
        cursor: int | None = None,
    ) -> list[tuple[int, Frame]]:
        """Return up to *limit* published frames, optionally after *cursor*.

        Parameters
        ----------
        limit : int
            Maximum number of frames to return (capped at 1000).
        cursor : int or None
            If given, only frames with a sequence number greater than
            *cursor* are returned.  ``None`` (default) returns the most
            recent *limit* frames (i.e. the tail of history).

        Returns
        -------
        list[(int, Frame)]
            Sequence of ``(seq, frame)`` pairs in ascending order.
            Empty when no matching frames exist.
        """
        self._ensure_started()
        bounded = min(max(1, limit), 1000)
        with self._lock:
            # Ensure at least one frame has been produced (don't block here;
            # stream is best-effort over already-published history).
            if not self._history:
                return []
            if cursor is None:
                # Tail: return the last *bounded* frames.
                return list(self._history)[-bounded:]
            # Cursor-based: return frames strictly after cursor.
            result: list[tuple[int, Frame]] = []
            for seq, frame in self._history:
                if seq > cursor:
                    result.append((seq, frame))
                    if len(result) >= bounded:
                        break
            return result

    # ── producer internals ────────────────────────────────────────────

    def _producer_loop(self) -> None:
        """Background loop: advance the source and publish frames."""
        consecutive_errors = 0
        try:
            for frame in self._source:
                with self._lock:
                    seq = self._sequence
                    self._sequence += 1
                    self._history.append((seq, frame))
                # Wake waiters *after* releasing the lock so they can
                # immediately acquire it to read.
                with self._new_frame:
                    self._new_frame.notify_all()
                consecutive_errors = 0
                if self._stopped.is_set():
                    return
        except StopIteration:
            self._producer_exc = StopIteration("frame source is exhausted")
        except BaseException as exc:
            if isinstance(exc, (SystemExit, KeyboardInterrupt, GeneratorExit)):
                raise
            # Bounded consecutive errors: after *source_error_limit*
            # consecutive failures we let the exception propagate.
            consecutive_errors += 1
            if consecutive_errors >= self._source_error_limit:
                self._producer_exc = exc
            # Wake any waiters so current() can report the error.
            with self._new_frame:
                self._new_frame.notify_all()

    # ── protocol dispatch (backward-compatible) ───────────────────────

    def responses(self, request: dict[str, Any]) -> list[dict[str, Any]]:
        """Dispatch an incoming request dict to a list of response dicts.

        Supported operations (backward-compatible):
        - ``{"op": "current"}`` — latest frame or error.
        - ``{"op": "stream", "limit": N, "cursor": K}`` — frames.
        - ``{"op": "stream", "limit": N}`` — tail-of-history frames.
        """
        op = request.get("op")
        if op == "current":
            try:
                frame = self.current()
                return [_frame_response(frame), {"type": "end", "count": 1}]
            except FrameBrokerError as exc:
                return [_error_response(str(exc))]
        if op == "stream":
            limit = int(request.get("limit", 1))
            cursor = request.get("cursor")
            if cursor is not None:
                cursor = int(cursor)
            pairs = self.stream(limit=limit, cursor=cursor)
            return [
                *[_frame_response(frame, seq=seq) for seq, frame in pairs],
                {"type": "end", "count": len(pairs)},
            ]
        return [_error_response("unsupported operation")]


# ── Unix server ───────────────────────────────────────────────────────────


class BrokerUnixServer(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True

    def __init__(self, socket_path: Path, broker: FrameBroker) -> None:
        self.socket_path = socket_path
        self.broker = broker
        super().__init__(str(socket_path), _BrokerHandler)

    def server_close(self) -> None:
        super().server_close()
        self.socket_path.unlink(missing_ok=True)


class _BrokerHandler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        line = self.rfile.readline(1024 * 1024)
        if not line:
            return
        try:
            request = json.loads(line.decode("utf-8"))
            if not isinstance(request, dict):
                raise ValueError("request must be an object")
            responses = self.server.broker.responses(request)  # type: ignore[attr-defined]
        except Exception as exc:
            responses = [_error_response(str(exc))]
        for response in responses:
            self.wfile.write(
                json.dumps(response, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"
            )


def serve_unix_socket(socket_path: Path, broker: FrameBroker, *, mode: int = 0o660) -> BrokerUnixServer:
    socket_path.parent.mkdir(parents=True, exist_ok=True)
    socket_path.unlink(missing_ok=True)
    server = BrokerUnixServer(socket_path, broker)
    os.chmod(socket_path, mode)
    return server


def _frame_response(frame: Frame, seq: int | None = None) -> dict[str, Any]:
    d: dict[str, Any] = {"type": "frame", "frame": frame_to_jsonable(frame)}
    if seq is not None:
        d["seq"] = seq
    return d


def _error_response(message: str) -> dict[str, Any]:
    return {"type": "error", "error": message}
