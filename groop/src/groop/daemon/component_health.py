"""Bounded, thread-safe health for daemon-owned components."""

from __future__ import annotations

import math
import re
import time
from dataclasses import dataclass
from enum import Enum
from threading import Lock
from typing import Callable


HEALTH_PROTOCOL_VERSION = 1
PROTOCOL_CAPABILITY_HEALTH = "health-v1"
MAX_HEALTH_DETAIL_BYTES = 256
MAX_HEALTH_ERROR_BYTES = 256
MAX_HEALTH_ERROR_CODE_BYTES = 64
MAX_HEALTH_RESPONSE_BYTES = 16 * 1024


class ComponentState(Enum):
    DISABLED = "disabled"
    STARTING = "starting"
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    FAILED = "failed"
    STOPPING = "stopping"
    STOPPED = "stopped"


COMPONENT_NAMES: tuple[str, ...] = (
    "collector",
    "bpf_snapshot_bridge",
    "paddr_lifecycle",
)

_SENSITIVE_ASSIGNMENT = re.compile(
    r"(?i)\b(password|passwd|secret|token|api[_-]?key|credential|authorization|cookie)\s*[:=]\s*\S+"
)
_ABSOLUTE_PATH = re.compile(r"(?<![\w.])/(?:[^\s/]+/)*[^\s,;:)]*")
_ERROR_CODE = re.compile(r"[^a-zA-Z0-9_.-]+")


def _truncate_utf8(value: str, limit: int) -> str:
    raw = value.encode("utf-8")
    if len(raw) <= limit:
        return value
    suffix = "..."
    kept = raw[: max(0, limit - len(suffix))]
    while kept:
        try:
            return kept.decode("utf-8") + suffix
        except UnicodeDecodeError:
            kept = kept[:-1]
    return suffix[:limit]


def sanitize_public_text(value: object, *, limit: int) -> str:
    """Return bounded, single-line text with common sensitive forms redacted."""
    text = str(value)
    text = " ".join(text.split())
    text = _SENSITIVE_ASSIGNMENT.sub(lambda match: f"{match.group(1)}=<redacted>", text)
    text = _ABSOLUTE_PATH.sub("<path>", text)
    return _truncate_utf8(text, limit)


def _sanitize_error_code(value: str | None) -> str | None:
    if value is None:
        return None
    safe = _ERROR_CODE.sub("_", value).strip("_")
    return _truncate_utf8(safe, MAX_HEALTH_ERROR_CODE_BYTES) or None


@dataclass(frozen=True)
class ComponentError:
    """Public-safe, bounded error information."""

    message: str
    error_code: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "message",
            sanitize_public_text(self.message, limit=MAX_HEALTH_ERROR_BYTES),
        )
        object.__setattr__(self, "error_code", _sanitize_error_code(self.error_code))


@dataclass(frozen=True)
class ComponentSnapshot:
    name: str
    state: ComponentState
    detail: str = ""
    last_attempt_ts: float | None = None
    last_success_ts: float | None = None
    consecutive_failures: int = 0
    error: ComponentError | None = None
    state_change_count: int = 0

    def to_jsonable(self) -> dict:
        result: dict = {
            "name": self.name,
            "state": self.state.value,
            "detail": self.detail,
            "consecutive_failures": self.consecutive_failures,
            "state_change_count": self.state_change_count,
        }
        if self.last_attempt_ts is not None:
            result["last_attempt_ts"] = self.last_attempt_ts
        if self.last_success_ts is not None:
            result["last_success_ts"] = self.last_success_ts
        if self.error is not None:
            result["error"] = {"message": self.error.message}
            if self.error.error_code is not None:
                result["error"]["error_code"] = self.error.error_code
        return result


@dataclass(frozen=True)
class HealthSnapshot:
    snapshots: tuple[ComponentSnapshot, ...] = ()
    schema_version: int = HEALTH_PROTOCOL_VERSION
    capability: str = PROTOCOL_CAPABILITY_HEALTH

    def to_jsonable(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "capability": self.capability,
            "components": [snapshot.to_jsonable() for snapshot in self.snapshots],
        }

    def by_name(self, name: str) -> ComponentSnapshot | None:
        return next((snapshot for snapshot in self.snapshots if snapshot.name == name), None)


class _ComponentRecord:
    def __init__(self, name: str) -> None:
        self.name = name
        self.state = ComponentState.DISABLED
        self.detail = ""
        self.last_attempt_ts: float | None = None
        self.last_success_ts: float | None = None
        self.consecutive_failures = 0
        self.error: ComponentError | None = None
        self.state_change_count = 0


class ComponentHealthRegistry:
    """Atomic registry whose every public value is bounded at ingestion."""

    def __init__(self, *, now: Callable[[], float] | None = None) -> None:
        self._lock = Lock()
        self._now = now or time.time
        self._records = {name: _ComponentRecord(name) for name in COMPONENT_NAMES}

    def _update(
        self,
        name: str,
        state: ComponentState,
        *,
        detail: str = "",
        error: ComponentError | None = None,
        success: bool = False,
        failed_attempt: bool = False,
    ) -> None:
        with self._lock:
            record = self._records.get(name)
            if record is None:
                return
            timestamp = float(self._now())
            if not math.isfinite(timestamp):
                timestamp = 0.0
            if record.state is not state:
                record.state_change_count += 1
            record.state = state
            record.detail = sanitize_public_text(detail, limit=MAX_HEALTH_DETAIL_BYTES)
            record.last_attempt_ts = timestamp
            if success:
                record.last_success_ts = timestamp
                record.consecutive_failures = 0
                record.error = None
            elif failed_attempt:
                record.consecutive_failures += 1
                record.error = error
            elif state in {ComponentState.DISABLED, ComponentState.STARTING}:
                record.error = None

    def set_state(
        self,
        name: str,
        state: ComponentState,
        *,
        detail: str = "",
        error: ComponentError | None = None,
    ) -> None:
        self._update(
            name,
            state,
            detail=detail,
            error=error,
            success=state is ComponentState.HEALTHY,
            failed_attempt=state in {ComponentState.DEGRADED, ComponentState.FAILED},
        )

    def record_success(self, name: str, *, detail: str = "") -> None:
        self._update(name, ComponentState.HEALTHY, detail=detail, success=True)

    def record_failure(
        self, name: str, *, detail: str = "", error: ComponentError | None = None
    ) -> None:
        self._update(
            name,
            ComponentState.FAILED,
            detail=detail,
            error=error,
            failed_attempt=True,
        )

    def record_degraded(
        self, name: str, *, detail: str = "", error: ComponentError | None = None
    ) -> None:
        self._update(
            name,
            ComponentState.DEGRADED,
            detail=detail,
            error=error,
            failed_attempt=True,
        )

    def mark_starting(self, name: str, *, detail: str = "") -> None:
        self._update(name, ComponentState.STARTING, detail=detail)

    def mark_stopping(self, name: str, *, detail: str = "") -> None:
        self._update(name, ComponentState.STOPPING, detail=detail)

    def mark_stopped(self, name: str, *, detail: str = "") -> None:
        self._update(name, ComponentState.STOPPED, detail=detail)

    def mark_disabled(self, name: str, *, detail: str = "") -> None:
        self._update(name, ComponentState.DISABLED, detail=detail)

    def snapshot(self) -> HealthSnapshot:
        with self._lock:
            return HealthSnapshot(
                snapshots=tuple(
                    ComponentSnapshot(
                        name=record.name,
                        state=record.state,
                        detail=record.detail,
                        last_attempt_ts=record.last_attempt_ts,
                        last_success_ts=record.last_success_ts,
                        consecutive_failures=record.consecutive_failures,
                        error=record.error,
                        state_change_count=record.state_change_count,
                    )
                    for name in COMPONENT_NAMES
                    if (record := self._records.get(name)) is not None
                )
            )


def build_health_response(registry: ComponentHealthRegistry) -> dict:
    return {"type": "health", **registry.snapshot().to_jsonable()}
