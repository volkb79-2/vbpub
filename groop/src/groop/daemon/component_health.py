"""Thread-safe daemon component health registry.

Provides a typed, bounded health snapshot for daemon-owned background
components — collector, BPF snapshot bridge, and paddr lifecycle —
with stable states, deterministic concurrency, and bounded public error
detail.  Never exposes tracebacks, environment variables, arbitrary paths,
command output, or secrets.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from threading import Lock
from typing import Callable


class ComponentState(Enum):
    """Stable state of a daemon-owned component."""

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

# ---------------------------------------------------------------------------
# Public, bounded error detail — never tracebacks, env vars, paths, secrets
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ComponentError:
    """Bounded, public-safe error detail for a component.

    The *message* is a short, static-safe string (no traceback, no
    environment, no secret, no arbitrary path).  The *error_code* is an
    opaque identifier the caller can use for grouping or log correlation.
    """

    message: str
    error_code: str | None = None


# ---------------------------------------------------------------------------
# Snapshot types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ComponentSnapshot:
    """Bounded health snapshot for a single component at a point in time."""

    name: str
    state: ComponentState
    detail: str = ""  # static-safe one-line summary
    last_attempt_ts: float | None = None
    last_success_ts: float | None = None
    consecutive_failures: int = 0
    error: ComponentError | None = None
    # Transitions tracked since the registry was created (monotonic counter)
    state_change_count: int = 0

    def to_jsonable(self) -> dict:
        d: dict = {
            "name": self.name,
            "state": self.state.value,
            "detail": self.detail,
        }
        if self.last_attempt_ts is not None:
            d["last_attempt_ts"] = self.last_attempt_ts
        if self.last_success_ts is not None:
            d["last_success_ts"] = self.last_success_ts
        if self.consecutive_failures:
            d["consecutive_failures"] = self.consecutive_failures
        if self.error is not None:
            d["error"] = {"message": self.error.message}
            if self.error.error_code is not None:
                d["error"]["error_code"] = self.error.error_code
        if self.state_change_count:
            d["state_change_count"] = self.state_change_count
        return d


@dataclass(frozen=True)
class HealthSnapshot:
    """Atomic, deterministic health snapshot of all tracked components."""

    snapshots: tuple[ComponentSnapshot, ...] = ()
    schema_version: int = 1

    def to_jsonable(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "components": [s.to_jsonable() for s in self.snapshots],
        }

    def by_name(self, name: str) -> ComponentSnapshot | None:
        for s in self.snapshots:
            if s.name == name:
                return s
        return None


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class ComponentHealthRegistry:
    """Thread-safe registry for daemon component health.

    All public methods acquire the internal lock so snapshots are
    deterministic even during concurrent updates and shutdown.
    """

    def __init__(self, *, now: Callable[[], float] | None = None) -> None:
        self._lock = Lock()
        self._now = now or time.time
        # Internal mutable records keyed by component name
        self._records: dict[str, _ComponentRecord] = {}
        for name in COMPONENT_NAMES:
            self._records[name] = _ComponentRecord(name=name)

    # ------------------------------------------------------------------
    # State transitions
    # ------------------------------------------------------------------

    def set_state(
        self,
        name: str,
        state: ComponentState,
        *,
        detail: str = "",
        error: ComponentError | None = None,
    ) -> None:
        """Set *name* to *state* with bounded detail.

        Consecutive-failure count is automatically incremented when the
        state is FAILED and the previous state was also FAILED (or was a
        transition *into* failed).  It is reset to 0 when the state becomes
        HEALTHY.
        """
        with self._lock:
            rec = self._records.get(name)
            if rec is None:
                return  # unknown component — silently ignored
            ts = self._now()

            rec.state = state
            rec.detail = detail
            last_change = rec.last_state_change_ts
            rec.last_state_change_ts = ts
            rec.previous_state = rec.state  # store for failure tracking
            rec.state_change_count += 1

            if state is ComponentState.HEALTHY:
                rec.consecutive_failures = 0
                rec.error = None
            elif state is ComponentState.FAILED:
                rec.consecutive_failures += 1
                rec.error = error

            rec.last_attempt_ts = ts

    def record_success(self, name: str, *, detail: str = "") -> None:
        """Mark *name* as healthy and record the success timestamp."""
        with self._lock:
            rec = self._records.get(name)
            if rec is None:
                return
            ts = self._now()
            rec.state = ComponentState.HEALTHY
            rec.detail = detail or ""
            rec.last_attempt_ts = ts
            rec.last_success_ts = ts
            rec.consecutive_failures = 0
            rec.error = None
            rec.state_change_count += 1

    def record_failure(
        self,
        name: str,
        *,
        detail: str = "",
        error: ComponentError | None = None,
    ) -> None:
        """Mark *name* as failed and increment consecutive-failure count."""
        with self._lock:
            rec = self._records.get(name)
            if rec is None:
                return
            ts = self._now()
            rec.state = ComponentState.FAILED
            rec.detail = detail or ""
            rec.last_attempt_ts = ts
            rec.consecutive_failures += 1
            rec.error = error
            rec.state_change_count += 1

    def record_degraded(
        self,
        name: str,
        *,
        detail: str = "",
        error: ComponentError | None = None,
    ) -> None:
        """Mark *name* as degraded (healthy-but-impaired)."""
        with self._lock:
            rec = self._records.get(name)
            if rec is None:
                return
            ts = self._now()
            rec.state = ComponentState.DEGRADED
            rec.detail = detail or ""
            rec.last_attempt_ts = ts
            rec.error = error
            rec.state_change_count += 1

    # ------------------------------------------------------------------
    # Snapshot
    # ------------------------------------------------------------------

    def snapshot(self) -> HealthSnapshot:
        """Return a deterministic, atomic health snapshot."""
        with self._lock:
            snapshots: list[ComponentSnapshot] = []
            for name in COMPONENT_NAMES:
                rec = self._records.get(name)
                if rec is None:
                    continue
                snapshots.append(
                    ComponentSnapshot(
                        name=rec.name,
                        state=rec.state,
                        detail=rec.detail,
                        last_attempt_ts=rec.last_attempt_ts,
                        last_success_ts=rec.last_success_ts,
                        consecutive_failures=rec.consecutive_failures,
                        error=rec.error,
                        state_change_count=rec.state_change_count,
                    )
                )
            return HealthSnapshot(snapshots=tuple(snapshots))

    # ------------------------------------------------------------------
    # Mark components starting/stopping for deterministic lifecycle
    # ------------------------------------------------------------------

    def mark_starting(self, name: str, *, detail: str = "") -> None:
        with self._lock:
            rec = self._records.get(name)
            if rec is None:
                return
            rec.state = ComponentState.STARTING
            rec.detail = detail or ""
            rec.state_change_count += 1

    def mark_stopping(self, name: str, *, detail: str = "") -> None:
        with self._lock:
            rec = self._records.get(name)
            if rec is None:
                return
            rec.state = ComponentState.STOPPING
            rec.detail = detail or ""
            rec.state_change_count += 1

    def mark_stopped(self, name: str, *, detail: str = "") -> None:
        with self._lock:
            rec = self._records.get(name)
            if rec is None:
                return
            rec.state = ComponentState.STOPPED
            rec.detail = detail or ""
            rec.state_change_count += 1

    def mark_disabled(self, name: str, *, detail: str = "") -> None:
        """Explicitly set a component to disabled state."""
        with self._lock:
            rec = self._records.get(name)
            if rec is None:
                return
            rec.state = ComponentState.DISABLED
            rec.detail = detail or ""
            rec.error = None
            rec.state_change_count += 1


# ---------------------------------------------------------------------------
# Internal mutable record (private — not exposed outside the module)
# ---------------------------------------------------------------------------


class _ComponentRecord:
    """Mutable, lock-guarded record for one component's health."""

    __slots__ = (
        "name",
        "state",
        "detail",
        "last_attempt_ts",
        "last_success_ts",
        "consecutive_failures",
        "error",
        "state_change_count",
        "previous_state",
        "last_state_change_ts",
    )

    def __init__(self, name: str) -> None:
        self.name: str = name
        self.state: ComponentState = ComponentState.DISABLED
        self.detail: str = ""
        self.last_attempt_ts: float | None = None
        self.last_success_ts: float | None = None
        self.consecutive_failures: int = 0
        self.error: ComponentError | None = None
        self.state_change_count: int = 0
        self.previous_state: ComponentState = ComponentState.DISABLED
        self.last_state_change_ts: float | None = None


# ---------------------------------------------------------------------------
# Convenience — HealthStatus enum used in broker responses
# ---------------------------------------------------------------------------

HEALTH_PROTOCOL_VERSION = 1

# Protocol capability string used in responses / version gating
PROTOCOL_CAPABILITY_HEALTH = "health-v1"


def build_health_response(registry: ComponentHealthRegistry) -> dict:
    """Build a protocol response dict for the ``health`` operation."""
    snap = registry.snapshot()
    return {
        "type": "health",
        "schema_version": HEALTH_PROTOCOL_VERSION,
        "capability": PROTOCOL_CAPABILITY_HEALTH,
        "components": [s.to_jsonable() for s in snap.snapshots],
    }
