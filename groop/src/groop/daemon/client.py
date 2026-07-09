from __future__ import annotations

import json
import socket
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from groop.model import Frame, frame_from_jsonable


class DaemonClientError(RuntimeError):
    pass


class DaemonConnectError(DaemonClientError):
    pass


class DaemonProtocolError(DaemonClientError):
    pass


class DaemonResponseError(DaemonClientError):
    pass


@dataclass(frozen=True)
class DaemonClient:
    socket_path: Path
    timeout_s: float | None = 5.0

    def current_frame(self) -> Frame:
        frames = self.request_frames({"op": "current"})
        if len(frames) != 1:
            raise DaemonProtocolError(
                f"daemon at {self.socket_path} returned {len(frames)} frame(s) for current; expected exactly 1"
            )
        return frames[0]

    def stream_frames(self, limit: int = 1) -> list[Frame]:
        bounded = max(1, int(limit))
        return self.request_frames({"op": "stream", "limit": bounded})

    def request_frames(self, request: dict[str, Any]) -> list[Frame]:
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
                if self.timeout_s is not None:
                    sock.settimeout(self.timeout_s)
                sock.connect(str(self.socket_path))
                sock.sendall(json.dumps(request, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n")
                sock.shutdown(socket.SHUT_WR)
                with sock.makefile("r", encoding="utf-8", newline="") as fh:
                    return self._read_frames(fh)
        except OSError as exc:
            raise DaemonConnectError(f"cannot connect to {self.socket_path}: {exc.strerror or exc}") from exc

    def _read_frames(self, fh) -> list[Frame]:
        frames: list[Frame] = []
        end_seen = False
        for line_no, raw_line in enumerate(fh, 1):
            line = raw_line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as exc:
                raise DaemonProtocolError(
                    f"daemon at {self.socket_path} returned malformed JSON on line {line_no}"
                ) from exc
            if not isinstance(payload, dict):
                raise DaemonProtocolError(
                    f"daemon at {self.socket_path} returned a non-object response on line {line_no}"
                )
            response_type = payload.get("type")
            if response_type == "frame":
                frame_payload = payload.get("frame")
                try:
                    frame = frame_from_jsonable(frame_payload)
                except Exception as exc:  # noqa: BLE001 - surface protocol errors cleanly.
                    raise DaemonProtocolError(
                        f"daemon at {self.socket_path} returned an invalid frame on line {line_no}: {exc}"
                    ) from exc
                frames.append(frame)
                continue
            if response_type == "end":
                count = payload.get("count")
                if not isinstance(count, int):
                    raise DaemonProtocolError(
                        f"daemon at {self.socket_path} returned an invalid end count on line {line_no}"
                    )
                if count != len(frames):
                    raise DaemonProtocolError(
                        f"daemon at {self.socket_path} ended with count {count}, but {len(frames)} frame(s) were read"
                    )
                end_seen = True
                break
            if response_type == "error":
                message = payload.get("error") or payload.get("message") or "daemon returned an error"
                raise DaemonResponseError(f"daemon at {self.socket_path} returned an error: {message}")
            raise DaemonProtocolError(
                f"daemon at {self.socket_path} returned unsupported response type {response_type!r} on line {line_no}"
            )
        if not end_seen:
            raise DaemonProtocolError(f"daemon at {self.socket_path} closed the connection without an end response")
        return frames


def current_frame(socket_path: Path, *, timeout_s: float | None = 5.0) -> Frame:
    return DaemonClient(socket_path, timeout_s=timeout_s).current_frame()


def stream_frames(socket_path: Path, *, limit: int = 1, poll_interval_s: float = 5.0) -> Iterator[Frame]:
    client = DaemonClient(socket_path)
    bounded_interval = max(0.1, poll_interval_s)
    bounded_limit = max(1, int(limit))
    while True:
        for frame in client.stream_frames(limit=bounded_limit):
            yield frame
        time.sleep(bounded_interval)


def current_frame_stream(socket_path: Path, *, poll_interval_s: float = 5.0) -> Iterator[Frame]:
    client = DaemonClient(socket_path)
    bounded_interval = max(0.1, poll_interval_s)
    while True:
        yield client.current_frame()
        time.sleep(bounded_interval)
