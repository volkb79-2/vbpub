from __future__ import annotations

import json
import os
import socketserver
import threading
from collections import deque
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from groop.model import Frame, frame_to_jsonable


class FrameBroker:
    def __init__(self, frame_source: Iterable[Frame], *, history_size: int = 120) -> None:
        self._source = iter(frame_source)
        self._history: deque[Frame] = deque(maxlen=max(1, history_size))
        self._lock = threading.Lock()

    def current(self) -> Frame:
        with self._lock:
            if self._history:
                return self._history[-1]
            return self._collect_locked()

    def stream(self, limit: int) -> list[Frame]:
        bounded = min(max(1, limit), 1000)
        with self._lock:
            frames = []
            for _ in range(bounded):
                frames.append(self._collect_locked())
            return frames

    def _collect_locked(self) -> Frame:
        frame = next(self._source)
        self._history.append(frame)
        return frame

    def responses(self, request: dict[str, Any]) -> list[dict[str, Any]]:
        op = request.get("op")
        if op == "current":
            return [_frame_response(self.current()), {"type": "end", "count": 1}]
        if op == "stream":
            limit = int(request.get("limit", 1))
            frames = self.stream(limit)
            return [*[_frame_response(frame) for frame in frames], {"type": "end", "count": len(frames)}]
        return [{"type": "error", "error": "unsupported operation"}]


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
            responses = [{"type": "error", "error": str(exc)}]
        for response in responses:
            self.wfile.write(json.dumps(response, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n")


def serve_unix_socket(socket_path: Path, broker: FrameBroker, *, mode: int = 0o660) -> BrokerUnixServer:
    socket_path.parent.mkdir(parents=True, exist_ok=True)
    socket_path.unlink(missing_ok=True)
    server = BrokerUnixServer(socket_path, broker)
    os.chmod(socket_path, mode)
    return server


def _frame_response(frame: Frame) -> dict[str, Any]:
    return {"type": "frame", "frame": frame_to_jsonable(frame)}
