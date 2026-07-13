from __future__ import annotations

import json
import math
import socket
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from groop.daemon.api import (
    DEFAULT_MAX_RESPONSE_BYTES,
    PROTOCOL_VERSION,
    Sensitivity,
)
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
from groop.model import (
    EntityFrame,
    Frame,
    entity_frame_from_jsonable,
    frame_from_jsonable,
)


class DaemonClientError(RuntimeError):
    pass


class DaemonConnectError(DaemonClientError):
    pass


class DaemonProtocolError(DaemonClientError):
    pass


class DaemonResponseError(DaemonClientError):
    """Typed daemon error, carrying the P52 ErrorCode string on ``.code``.

    Callers can branch on ``err.code`` to distinguish ``not_found``,
    ``invalid_type``, ``out_of_range``, ``unavailable``, etc.
    """

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        self.code = code


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
class DaemonCurrentResult:
    """Typed result for the ``current`` versioned envelope op."""

    seq: int
    frame: Frame
    metrics_meta: dict[str, dict[str, object]]


@dataclass(frozen=True)
class DaemonHistoryResult:
    """Typed result for the ``history`` versioned envelope op."""

    entries: tuple[tuple[int, Frame], ...]
    oldest_seq: int | None
    latest_seq: int | None
    next_cursor: int | None
    gap: bool
    metrics_meta: dict[str, dict[str, object]]

    @property
    def frames(self) -> tuple[Frame, ...]:
        return tuple(frame for _, frame in self.entries)


@dataclass(frozen=True)
class DaemonEntityResult:
    """Typed result for the ``entity`` versioned envelope op."""

    seq: int
    entity: EntityFrame
    metrics_meta: dict[str, dict[str, object]]


@dataclass(frozen=True)
class DaemonHello:
    """Typed result for the ``hello`` versioned envelope op."""

    protocol_versions: tuple[int, ...]
    capabilities: tuple[str, ...]
    identity: dict[str, str]
    limits: dict[str, object]


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

    # -- versioned envelope helper --

    def _request_envelope(self, op: str, *, params: dict[str, object] | None = None) -> dict[str, Any]:
        """Send one versioned envelope request and return the decoded result dict.

        Builds the envelope with a fresh opaque id, sends it, reads exactly one
        response line, validates the envelope wrapper (id echo, ok flag, max
        response bytes), and returns the ``result`` dict on success.

        Raises:
            DaemonConnectError: On connection failure.
            DaemonProtocolError: On malformed/oversized/non-JSON/non-object/
                id-mismatch/unknown-response-shape.
            DaemonResponseError: On ``ok:false`` envelopes, with the typed
                ``.code`` set to the P52 ``ErrorCode`` value.
        """
        request_id = uuid.uuid4().hex
        envelope: dict[str, object] = {"id": request_id, "op": op, "v": PROTOCOL_VERSION}
        if params:
            envelope.update(params)
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
                if self.timeout_s is not None:
                    sock.settimeout(self.timeout_s)
                sock.connect(str(self.socket_path))
                sock.sendall(
                    json.dumps(envelope, sort_keys=True, separators=(",", ":")).encode("utf-8")
                    + b"\n"
                )
                sock.shutdown(socket.SHUT_WR)
                with sock.makefile("r", encoding="utf-8", newline="") as fh:
                    return self._read_envelope_response(fh, request_id)
        except OSError as exc:
            raise DaemonConnectError(
                f"cannot connect to {self.socket_path}: {exc.strerror or exc}"
            ) from exc

    def _read_envelope_response(self, fh, expected_id: str) -> dict[str, Any]:
        try:
            raw_line = fh.readline(DEFAULT_MAX_RESPONSE_BYTES + 1)
        except UnicodeError as exc:
            raise DaemonProtocolError(
                f"daemon at {self.socket_path} returned invalid UTF-8 envelope data"
            ) from exc
        if not raw_line:
            raise DaemonProtocolError(
                f"daemon at {self.socket_path} closed the connection without a response"
            )
        if len(raw_line.encode("utf-8")) > DEFAULT_MAX_RESPONSE_BYTES:
            raise DaemonProtocolError(
                f"daemon at {self.socket_path} returned an oversized envelope response"
            )
        line = raw_line.strip()
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            raise DaemonProtocolError(
                f"daemon at {self.socket_path} returned malformed JSON envelope"
            ) from exc
        if not isinstance(payload, dict):
            raise DaemonProtocolError(
                f"daemon at {self.socket_path} returned a non-object envelope"
            )
        # Assert id echo.
        echoed = payload.get("id")
        if echoed != expected_id:
            raise DaemonProtocolError(
                f"daemon at {self.socket_path} returned id {echoed!r}, expected {expected_id!r}"
            )
        ok = payload.get("ok")
        if ok is True:
            result = payload.get("result")
            if not isinstance(result, dict):
                raise DaemonProtocolError(
                    f"daemon at {self.socket_path} returned a non-object result"
                )
            return result
        if ok is False:
            error_obj = payload.get("error")
            code: str | None = None
            message: str = "daemon returned an error"
            if isinstance(error_obj, dict):
                code = error_obj.get("code")
                if not isinstance(code, str):
                    code = None
                msg_val = error_obj.get("message")
                if isinstance(msg_val, str):
                    message = msg_val
            raise DaemonResponseError(
                f"daemon at {self.socket_path} returned an error: {message}",
                code=code,
            )
        raise DaemonProtocolError(
            f"daemon at {self.socket_path} returned an envelope with ok={ok!r}"
        )

    # -- versioned envelope methods --

    def request_hello(self) -> DaemonHello:
        """Call the ``hello`` versioned op; returns protocol info and limits."""
        result = self._request_envelope("hello")
        pv = result.get("protocol_versions")
        if not isinstance(pv, list):
            raise DaemonProtocolError(
                f"daemon at {self.socket_path} returned invalid protocol_versions"
            )
        caps = result.get("capabilities")
        if not isinstance(caps, list):
            raise DaemonProtocolError(
                f"daemon at {self.socket_path} returned invalid capabilities"
            )
        identity = result.get("identity")
        if not isinstance(identity, dict):
            raise DaemonProtocolError(
                f"daemon at {self.socket_path} returned invalid identity"
            )
        limits = result.get("limits")
        if not isinstance(limits, dict):
            raise DaemonProtocolError(
                f"daemon at {self.socket_path} returned invalid limits"
            )
        return DaemonHello(
            protocol_versions=tuple(int(v) for v in pv),
            capabilities=tuple(str(c) for c in caps),
            identity=dict(identity),  # type: ignore[arg-type]
            limits=dict(limits),  # type: ignore[arg-type]
        )

    def request_current(self) -> DaemonCurrentResult:
        """Call the ``current`` versioned op; returns the latest atomic frame."""
        result = self._request_envelope("current")
        seq = result.get("seq")
        if isinstance(seq, bool) or not isinstance(seq, int) or seq < 0:
            raise DaemonProtocolError(
                f"daemon at {self.socket_path} returned invalid seq"
            )
        frame_payload = result.get("frame")
        try:
            frame = frame_from_jsonable(frame_payload)
        except Exception as exc:  # noqa: BLE001
            raise DaemonProtocolError(
                f"daemon at {self.socket_path} returned an invalid frame: {exc}"
            ) from exc
        metrics_meta = self._validate_metrics_meta(result.get("metrics_meta"))
        return DaemonCurrentResult(seq=seq, frame=frame, metrics_meta=metrics_meta)

    def request_history(
        self,
        *,
        limit: int = 1,
        cursor: int | None = None,
        since_ts: float | None = None,
        until_ts: float | None = None,
    ) -> DaemonHistoryResult:
        """Call the ``history`` versioned op.

        Raises ``ValueError`` (fast-fail) if both cursor and a time window are
        set, since the server would reject that as ``bad_request``.
        """
        if cursor is not None and (since_ts is not None or until_ts is not None):
            raise ValueError("specify either cursor or a time window, not both")
        params: dict[str, object] = {"limit": limit}
        if cursor is not None:
            params["cursor"] = cursor
        if since_ts is not None:
            params["since_ts"] = since_ts
        if until_ts is not None:
            params["until_ts"] = until_ts
        result = self._request_envelope("history", params=params)
        raw_frames = result.get("frames")
        if not isinstance(raw_frames, list):
            raise DaemonProtocolError(
                f"daemon at {self.socket_path} returned invalid history frames"
            )
        entries: list[tuple[int, Frame]] = []
        for i, item in enumerate(raw_frames):
            if not isinstance(item, dict):
                raise DaemonProtocolError(
                    f"daemon at {self.socket_path} returned invalid history entry {i}"
                )
            seq = item.get("seq")
            if isinstance(seq, bool) or not isinstance(seq, int) or seq < 0:
                raise DaemonProtocolError(
                    f"daemon at {self.socket_path} returned invalid seq at entry {i}"
                )
            if entries and seq <= entries[-1][0]:
                raise DaemonProtocolError(
                    f"daemon at {self.socket_path} returned non-increasing frame sequences"
                )
            try:
                frame = frame_from_jsonable(item.get("frame"))
            except Exception as exc:  # noqa: BLE001
                raise DaemonProtocolError(
                    f"daemon at {self.socket_path} returned an invalid frame at entry {i}: {exc}"
                ) from exc
            entries.append((seq, frame))
        oldest = self._optional_sequence(result.get("oldest_seq"), "oldest_seq")
        latest = self._optional_sequence(result.get("latest_seq"), "latest_seq")
        next_cursor = self._optional_sequence(result.get("next_cursor"), "next_cursor", allow_minus_one=True)
        gap = result.get("gap")
        if not isinstance(gap, bool):
            raise DaemonProtocolError(
                f"daemon at {self.socket_path} returned invalid gap marker"
            )
        if (oldest is None) != (latest is None) or (
            oldest is not None and latest is not None and oldest > latest
        ):
            raise DaemonProtocolError(
                f"daemon at {self.socket_path} returned invalid history bounds"
            )
        if entries and next_cursor != entries[-1][0]:
            raise DaemonProtocolError(
                f"daemon at {self.socket_path} returned an invalid next cursor"
            )
        metrics_meta = self._validate_metrics_meta(result.get("metrics_meta"))
        return DaemonHistoryResult(
            entries=tuple(entries),
            oldest_seq=oldest,
            latest_seq=latest,
            next_cursor=next_cursor,
            gap=gap,
            metrics_meta=metrics_meta,
        )

    def request_entity(self, key: str) -> DaemonEntityResult:
        """Call the ``entity`` versioned op; returns one entity's frame data."""
        result = self._request_envelope("entity", params={"key": key})
        seq = result.get("seq")
        if isinstance(seq, bool) or not isinstance(seq, int) or seq < 0:
            raise DaemonProtocolError(
                f"daemon at {self.socket_path} returned invalid seq"
            )
        raw_entity = result.get("entity")
        try:
            entity = entity_frame_from_jsonable(raw_entity)
        except Exception as exc:  # noqa: BLE001
            raise DaemonProtocolError(
                f"daemon at {self.socket_path} returned an invalid entity frame: {exc}"
            ) from exc
        metrics_meta = self._validate_metrics_meta(result.get("metrics_meta"))
        return DaemonEntityResult(seq=seq, entity=entity, metrics_meta=metrics_meta)

    # -- validation helpers (shared with versioned methods) --

    def _validate_metrics_meta(self, value: object) -> dict[str, dict[str, object]]:
        """Validate that ``metrics_meta`` is a dict-of-dicts with a
        ``sensitivity`` value in the closed ``Sensitivity`` enum."""
        if not isinstance(value, dict):
            raise DaemonProtocolError(
                f"daemon at {self.socket_path} returned invalid metrics_meta"
            )
        valid_sensitivities = {s.value for s in Sensitivity}
        out: dict[str, dict[str, object]] = {}
        for k, v in value.items():
            if not isinstance(k, str):
                raise DaemonProtocolError(
                    f"daemon at {self.socket_path} returned non-string metric key in metrics_meta"
                )
            if not isinstance(v, dict):
                raise DaemonProtocolError(
                    f"daemon at {self.socket_path} returned non-dict metrics_meta entry for {k!r}"
                )
            sens = v.get("sensitivity")
            if sens not in valid_sensitivities:
                raise DaemonProtocolError(
                    f"daemon at {self.socket_path} returned invalid sensitivity {sens!r} for {k!r}"
                )
            out[k] = dict(v)
        return out

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
