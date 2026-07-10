from __future__ import annotations

import json
import math
import socket
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from groop.daemon.component_health import (
    COMPONENT_NAMES,
    HEALTH_PROTOCOL_VERSION,
    MAX_HEALTH_DETAIL_BYTES,
    MAX_HEALTH_ERROR_BYTES,
    MAX_HEALTH_ERROR_CODE_BYTES,
    MAX_HEALTH_RESPONSE_BYTES,
    PROTOCOL_CAPABILITY_HEALTH,
    ComponentError,
    ComponentSnapshot,
    ComponentState,
    HealthSnapshot,
)
from groop.model import Frame, frame_from_jsonable


class DaemonClientError(RuntimeError):
    pass


class DaemonConnectError(DaemonClientError):
    pass


class DaemonProtocolError(DaemonClientError):
    pass


class DaemonResponseError(DaemonClientError):
    pass


class DaemonHistoryGapError(DaemonClientError):
    pass


@dataclass(frozen=True)
class DaemonFrameBatch:
    entries: tuple[tuple[int, Frame], ...]
    oldest_seq: int | None
    latest_seq: int | None
    next_cursor: int | None
    gap: bool

    @property
    def frames(self) -> tuple[Frame, ...]:
        return tuple(frame for _, frame in self.entries)


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
        return list(self.stream_batch(limit=limit).frames)

    def stream_batch(self, limit: int = 1, *, cursor: int | None = None) -> DaemonFrameBatch:
        request: dict[str, Any] = {"op": "stream", "limit": limit}
        if cursor is not None:
            request["cursor"] = cursor
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
                if self.timeout_s is not None:
                    sock.settimeout(self.timeout_s)
                sock.connect(str(self.socket_path))
                sock.sendall(
                    json.dumps(request, sort_keys=True, separators=(",", ":")).encode("utf-8")
                    + b"\n"
                )
                sock.shutdown(socket.SHUT_WR)
                with sock.makefile("r", encoding="utf-8", newline="") as fh:
                    return self._read_stream_batch(fh)
        except OSError as exc:
            raise DaemonConnectError(
                f"cannot connect to {self.socket_path}: {exc.strerror or exc}"
            ) from exc

    def request_health(self) -> HealthSnapshot:
        """Request a component health snapshot from the daemon.

        Raises:
            DaemonResponseError: If the daemon returns an error response
                (e.g. health not available, version mismatch).
            DaemonProtocolError: If the response is malformed.
            DaemonConnectError: On connection failure.
        """
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
                if self.timeout_s is not None:
                    sock.settimeout(self.timeout_s)
                sock.connect(str(self.socket_path))
                sock.sendall(json.dumps({"op": "health"}, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n")
                sock.shutdown(socket.SHUT_WR)
                with sock.makefile("r", encoding="utf-8", newline="") as fh:
                    return self._read_health(fh)
        except OSError as exc:
            raise DaemonConnectError(f"cannot connect to {self.socket_path}: {exc.strerror or exc}") from exc

    def _read_health(self, fh) -> HealthSnapshot:
        try:
            raw_line = fh.readline(MAX_HEALTH_RESPONSE_BYTES + 1)
        except UnicodeError as exc:
            raise DaemonProtocolError(
                f"daemon at {self.socket_path} returned invalid UTF-8 health data"
            ) from exc
        if not raw_line:
            raise DaemonProtocolError(
                f"daemon at {self.socket_path} closed the connection without a health response"
            )
        if len(raw_line.encode("utf-8")) > MAX_HEALTH_RESPONSE_BYTES:
            raise DaemonProtocolError(
                f"daemon at {self.socket_path} returned an oversized health response"
            )
        line = raw_line.strip()
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            raise DaemonProtocolError(
                f"daemon at {self.socket_path} returned malformed JSON on line 1"
            ) from exc
        if not isinstance(payload, dict):
            raise DaemonProtocolError(
                f"daemon at {self.socket_path} returned a non-object response on line 1"
            )
        response_type = payload.get("type")
        if response_type == "error":
            message = payload.get("error") or payload.get("message") or "daemon returned an error"
            raise DaemonResponseError(
                f"daemon at {self.socket_path} returned an error: {message}"
            )
        if response_type != "health":
            raise DaemonProtocolError(
                f"daemon at {self.socket_path} returned unsupported response type {response_type!r} on line 1"
            )
        return self._parse_health_payload(payload)

    def _parse_health_payload(self, payload: dict[str, Any]) -> HealthSnapshot:
        if payload.get("schema_version") != HEALTH_PROTOCOL_VERSION:
            raise self._health_protocol_error("unsupported schema_version")
        if payload.get("capability") != PROTOCOL_CAPABILITY_HEALTH:
            raise self._health_protocol_error("missing or incompatible health capability")
        components = payload.get("components")
        if not isinstance(components, list) or len(components) != len(COMPONENT_NAMES):
            raise self._health_protocol_error("invalid components array")

        snapshots: list[ComponentSnapshot] = []
        for expected_name, component in zip(COMPONENT_NAMES, components, strict=True):
            if not isinstance(component, dict) or component.get("name") != expected_name:
                raise self._health_protocol_error("invalid component name or order")
            state_value = component.get("state")
            try:
                state = ComponentState(state_value)
            except (TypeError, ValueError) as exc:
                raise self._health_protocol_error("invalid component state") from exc
            detail = self._bounded_health_string(
                component.get("detail"), "detail", MAX_HEALTH_DETAIL_BYTES
            )
            last_attempt = self._optional_health_timestamp(
                component.get("last_attempt_ts"), "last_attempt_ts"
            )
            last_success = self._optional_health_timestamp(
                component.get("last_success_ts"), "last_success_ts"
            )
            failures = self._health_counter(
                component.get("consecutive_failures"), "consecutive_failures"
            )
            changes = self._health_counter(
                component.get("state_change_count"), "state_change_count"
            )
            error_payload = component.get("error")
            error: ComponentError | None = None
            if error_payload is not None:
                if not isinstance(error_payload, dict):
                    raise self._health_protocol_error("invalid component error")
                message = self._bounded_health_string(
                    error_payload.get("message"), "error.message", MAX_HEALTH_ERROR_BYTES
                )
                error_code_value = error_payload.get("error_code")
                error_code = None
                if error_code_value is not None:
                    error_code = self._bounded_health_string(
                        error_code_value,
                        "error.error_code",
                        MAX_HEALTH_ERROR_CODE_BYTES,
                    )
                error = ComponentError(message=message, error_code=error_code)
            snapshots.append(
                ComponentSnapshot(
                    name=expected_name,
                    state=state,
                    detail=detail,
                    last_attempt_ts=last_attempt,
                    last_success_ts=last_success,
                    consecutive_failures=failures,
                    error=error,
                    state_change_count=changes,
                )
            )
        return HealthSnapshot(snapshots=tuple(snapshots))

    def _health_protocol_error(self, message: str) -> DaemonProtocolError:
        return DaemonProtocolError(
            f"daemon at {self.socket_path} returned incompatible health-v1 data: {message}"
        )

    def _bounded_health_string(self, value: object, field: str, limit: int) -> str:
        if not isinstance(value, str) or len(value.encode("utf-8")) > limit:
            raise self._health_protocol_error(f"invalid or oversized {field}")
        if any(ord(character) < 32 or ord(character) == 127 for character in value):
            raise self._health_protocol_error(f"control character in {field}")
        return value

    def _optional_health_timestamp(self, value: object, field: str) -> float | None:
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise self._health_protocol_error(f"invalid {field}")
        result = float(value)
        if not math.isfinite(result) or result < 0:
            raise self._health_protocol_error(f"invalid {field}")
        return result

    def _health_counter(self, value: object, field: str) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise self._health_protocol_error(f"invalid {field}")
        return value

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

    def _read_stream_batch(self, fh) -> DaemonFrameBatch:
        entries: list[tuple[int, Frame]] = []
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
            if response_type == "error":
                message = payload.get("error") or payload.get("message") or "daemon returned an error"
                raise DaemonResponseError(
                    f"daemon at {self.socket_path} returned an error: {message}"
                )
            if response_type == "frame":
                seq = payload.get("seq")
                if isinstance(seq, bool) or not isinstance(seq, int) or seq < 0:
                    raise DaemonProtocolError(
                        f"daemon at {self.socket_path} returned an invalid frame sequence on line {line_no}"
                    )
                if entries and seq <= entries[-1][0]:
                    raise DaemonProtocolError(
                        f"daemon at {self.socket_path} returned non-increasing frame sequences"
                    )
                try:
                    frame = frame_from_jsonable(payload.get("frame"))
                except Exception as exc:  # noqa: BLE001
                    raise DaemonProtocolError(
                        f"daemon at {self.socket_path} returned an invalid frame on line {line_no}: {exc}"
                    ) from exc
                entries.append((seq, frame))
                continue
            if response_type != "end":
                raise DaemonProtocolError(
                    f"daemon at {self.socket_path} returned unsupported response type {response_type!r} on line {line_no}"
                )
            count = payload.get("count")
            gap = payload.get("gap")
            if isinstance(count, bool) or not isinstance(count, int) or count != len(entries):
                raise DaemonProtocolError(
                    f"daemon at {self.socket_path} returned an invalid stream end count"
                )
            if not isinstance(gap, bool):
                raise DaemonProtocolError(
                    f"daemon at {self.socket_path} returned an invalid stream gap marker"
                )
            oldest = self._optional_sequence(payload.get("oldest_seq"), "oldest_seq")
            latest = self._optional_sequence(payload.get("latest_seq"), "latest_seq")
            next_cursor = self._optional_sequence(payload.get("next_cursor"), "next_cursor", allow_minus_one=True)
            if (oldest is None) != (latest is None) or (
                oldest is not None and latest is not None and oldest > latest
            ):
                raise DaemonProtocolError(
                    f"daemon at {self.socket_path} returned invalid stream history bounds"
                )
            if entries and next_cursor != entries[-1][0]:
                raise DaemonProtocolError(
                    f"daemon at {self.socket_path} returned an invalid next cursor"
                )
            return DaemonFrameBatch(tuple(entries), oldest, latest, next_cursor, gap)
        raise DaemonProtocolError(
            f"daemon at {self.socket_path} closed the connection without an end response"
        )

    def _optional_sequence(
        self, value: object, field: str, *, allow_minus_one: bool = False
    ) -> int | None:
        minimum = -1 if allow_minus_one else 0
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
            raise DaemonProtocolError(
                f"daemon at {self.socket_path} returned an invalid {field}"
            )
        return value


def current_frame(socket_path: Path, *, timeout_s: float | None = 5.0) -> Frame:
    return DaemonClient(socket_path, timeout_s=timeout_s).current_frame()


def stream_frames(socket_path: Path, *, limit: int = 1, poll_interval_s: float = 5.0) -> Iterator[Frame]:
    client = DaemonClient(socket_path)
    bounded_interval = max(0.1, poll_interval_s)
    bounded_limit = int(limit)
    cursor: int | None = None
    while True:
        batch = client.stream_batch(limit=bounded_limit, cursor=cursor)
        if batch.gap:
            raise DaemonHistoryGapError(
                f"daemon history evicted frames after cursor {cursor}; oldest available is {batch.oldest_seq}"
            )
        for frame in batch.frames:
            yield frame
        cursor = batch.next_cursor
        time.sleep(bounded_interval)


def current_frame_stream(socket_path: Path, *, poll_interval_s: float = 5.0) -> Iterator[Frame]:
    client = DaemonClient(socket_path)
    bounded_interval = max(0.1, poll_interval_s)
    while True:
        yield client.current_frame()
        time.sleep(bounded_interval)
