"""P52 — Versioned daemon read API.

Additive envelope over the P51 :class:`~topos.daemon.broker.FrameBroker`. Every
envelope request carries a client ``id`` (echoed verbatim), an ``op`` name, and
a protocol version integer ``v``. Every envelope response carries the echoed
``id``, an ``ok`` boolean, and on failure a typed ``error`` object whose
``code`` belongs to a closed enum and whose ``message`` is public-safe.

The envelope is single-line (one JSON object per response). Legacy requests
without a ``v`` field continue to be served unchanged by the P51 multi-line
protocol — see ``docs/DAEMON.md``.

This module never raises a raw exception across the socket boundary. Every
failure path produces a typed envelope error response.
"""

from __future__ import annotations

import json
import math
import os
import socket
import socketserver
import struct
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from topos import __version__ as TOPOS_VERSION
from topos.daemon.broker import (
    DEFAULT_MAX_CLIENTS,
    DEFAULT_REQUEST_TIMEOUT_S,
    MAX_REQUEST_BYTES,
    MAX_STREAM_LIMIT,
    BrokerUnixServer,
    FrameBroker,
    FrameBrokerError,
    _validate_finite,
    _validate_limit,
)
from topos.daemon.component_health import (
    ComponentHealthRegistry,
    build_health_response,
)
from topos.model import Frame, frame_to_jsonable
from topos.registry import REGISTRY, MetricSpec


# --- Protocol constants ---------------------------------------------------

PROTOCOL_VERSION = 1
PROTOCOL_VERSIONS: tuple[int, ...] = (1,)

# Closed capability set: every op served by this build is listed here, and
# every listed op is served by ``DaemonApi.handle``.
CAPABILITIES: tuple[str, ...] = ("hello", "current", "history", "entity", "health")

DEFAULT_MAX_RESPONSE_ITEMS = MAX_STREAM_LIMIT
DEFAULT_MAX_RESPONSE_BYTES = 4 * 1024 * 1024
DEFAULT_MAX_INFLIGHT_PER_CLIENT = 1  # one request per connection in this build

# Envelope field names (closed set; unknown top-level fields are rejected).
_REQUEST_FIELDS: frozenset[str] = frozenset({"id", "op", "v"})

# Per-op allowed parameter fields (in addition to id/op/v). Closed set per op;
# any other top-level field is rejected with UNKNOWN_FIELD.
_OP_PARAMS: dict[str, frozenset[str]] = {
    "hello": frozenset(),
    "current": frozenset(),
    "history": frozenset({"limit", "cursor", "since_ts", "until_ts"}),
    "entity": frozenset({"key"}),
    "health": frozenset(),
}


class ErrorCode(str, Enum):
    """Closed enum of public error codes."""

    BAD_REQUEST = "bad_request"
    UNKNOWN_OP = "unknown_op"
    UNKNOWN_FIELD = "unknown_field"
    INVALID_TYPE = "invalid_type"
    NON_FINITE = "non_finite"
    OUT_OF_RANGE = "out_of_range"
    MALFORMED_CURSOR = "malformed_cursor"
    OVERSIZED_REQUEST = "oversized_request"
    OVERSIZED_RESPONSE = "oversized_response"
    REQUEST_TIMEOUT = "request_timeout"
    SERVER_BUSY = "server_busy"
    UNAVAILABLE = "unavailable"
    DENIED = "denied"
    NOT_FOUND = "not_found"
    PROTOCOL_VERSION = "protocol_version"
    INTERNAL = "internal"


class Sensitivity(str, Enum):
    """Closed enum of metric sensitivity levels (CONTRACTS.md §10)."""

    PUBLIC = "public"
    OPERATIONAL = "operational"
    SENSITIVE = "sensitive"


# Metrics that reveal process identity / counts → privacy-relevant.
_SENSITIVE_METRICS: frozenset[str] = frozenset(
    {
        "cgroup_procs",
        "pids_current",
        "pids_max",
        "pids_events_max_per_s",
    }
)


def metric_sensitivity(name: str) -> Sensitivity:
    """Return the closed-enum sensitivity for a registry metric name."""
    if name in _SENSITIVE_METRICS:
        return Sensitivity.SENSITIVE
    if name.startswith("host_"):
        return Sensitivity.PUBLIC
    return Sensitivity.OPERATIONAL


def metric_metadata(name: str) -> dict[str, Any]:
    """Registry-derived source/unit/semantic/sensitivity metadata for a metric."""
    spec: MetricSpec = REGISTRY[name]
    return {
        "name": spec.name,
        "unit": spec.unit,
        "kind": spec.kind,
        "locality": spec.locality,
        "sensitivity": metric_sensitivity(spec.name).value,
        "glossary": spec.glossary,
    }


def _metrics_meta_for(names: set[str]) -> dict[str, dict[str, Any]]:
    """Build a bounded metric-metadata map for the given metric names."""
    return {name: metric_metadata(name) for name in sorted(names) if name in REGISTRY}


# --- Peer identity and authorization -------------------------------------


@dataclass(frozen=True)
class PeerCredentials:
    """Unix peer credentials observed at accept time (SO_PEERCRED)."""

    pid: int | None
    uid: int | None
    gid: int | None

    @property
    def anonymous(self) -> bool:
        return self.pid is None and self.uid is None and self.gid is None

    def to_audit(self) -> dict[str, int]:
        out: dict[str, int] = {}
        if self.pid is not None:
            out["pid"] = self.pid
        if self.uid is not None:
            out["uid"] = self.uid
        if self.gid is not None:
            out["gid"] = self.gid
        return out


# Authorization hook seam: receives (peer, op) and may deny.
# Return None to allow; return an (error_code, message) tuple to deny.
AuthorizationHook = Callable[[PeerCredentials, str], "tuple[ErrorCode, str] | None"]


def _default_auth_hook(peer: PeerCredentials, op: str) -> "tuple[ErrorCode, str] | None":
    """Default policy: socket-group read access is enforced by the OS.

    The hook is a no-op allow; mutation-shaped ops are rejected before the
    hook runs (they are not in the capability set).
    """
    return None


def read_peer_credentials(sock: socket.socket) -> PeerCredentials | None:
    """Read SO_PEERCRED from a connected Unix socket.

    Returns None on platform/race failure. The connection is still served
    anonymously — see DAEMON.md.
    """
    try:
        data = sock.getsockopt(socket.SOL_SOCKET, SO_PEERCRED, struct.calcsize("iII"))
        pid, uid, gid = struct.unpack("iII", data)
        return PeerCredentials(pid=int(pid), uid=int(uid), gid=int(gid))
    except (AttributeError, OSError, ValueError):
        return None


# Platform constant. SO_PEERCRED is Linux-only; on other platforms peer
# credential reads return None (anonymous) and the connection is still served.
SO_PEERCRED = getattr(socket, "SO_PEERCRED", 17)


# --- Resource limits -----------------------------------------------------


@dataclass(frozen=True)
class ApiLimits:
    """Enforced resource bounds for the versioned read API.

    Every field is validated at construction; out-of-range values raise and are
    never silently clamped (the optimized-P51 defect where ``max_children`` was
    silently uncapped must not recur).
    """

    max_request_bytes: int = MAX_REQUEST_BYTES
    max_response_items: int = DEFAULT_MAX_RESPONSE_ITEMS
    max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES
    request_timeout_s: float = DEFAULT_REQUEST_TIMEOUT_S
    max_clients: int = DEFAULT_MAX_CLIENTS
    max_inflight_per_client: int = DEFAULT_MAX_INFLIGHT_PER_CLIENT
    history_capacity: int = 120

    def __post_init__(self) -> None:
        _require_positive_int(self, "max_request_bytes", self.max_request_bytes, minimum=64)
        _require_positive_int(self, "max_response_items", self.max_response_items, minimum=1)
        _require_positive_int(self, "max_response_bytes", self.max_response_bytes, minimum=64)
        _require_positive_int(self, "max_clients", self.max_clients, minimum=1)
        _require_positive_int(self, "max_inflight_per_client", self.max_inflight_per_client, minimum=1)
        _require_positive_int(self, "history_capacity", self.history_capacity, minimum=1)
        if isinstance(self.request_timeout_s, bool) or not isinstance(self.request_timeout_s, (int, float)):
            raise TypeError("request_timeout_s must be a number")
        if not 0.0 < float(self.request_timeout_s) <= 300.0:
            raise ValueError("request_timeout_s must be greater than 0 and at most 300 seconds")


def _require_positive_int(limits: ApiLimits, field_name: str, value: object, *, minimum: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{field_name} must be an integer")
    if value < minimum:
        raise ValueError(f"{field_name} must be at least {minimum}")


# --- Audit records -------------------------------------------------------


@dataclass
class AuditRecord:
    """One per-client audit/rate-limit record (peer identity attached)."""

    peer: PeerCredentials | None
    op: str
    allowed: bool
    error_code: str | None = None
    ts: float = 0.0


class AuditLog:
    """Bounded, thread-safe audit log for tests and future rate limiting."""

    def __init__(self, capacity: int = 256) -> None:
        if isinstance(capacity, bool) or not isinstance(capacity, int) or capacity < 1:
            raise ValueError("audit capacity must be a positive integer")
        self._lock = threading.Lock()
        self._records: list[AuditRecord] = []
        self._capacity = capacity

    def record(self, entry: AuditRecord) -> None:
        with self._lock:
            self._records.append(entry)
            if len(self._records) > self._capacity:
                del self._records[: len(self._records) - self._capacity]

    def snapshot(self) -> tuple[AuditRecord, ...]:
        with self._lock:
            return tuple(self._records)

    def clear(self) -> None:
        with self._lock:
            self._records.clear()


# --- The versioned API ---------------------------------------------------


@dataclass
class DaemonApi:
    """Versioned read API over a :class:`FrameBroker`.

    ``handle`` validates an envelope request, dispatches it to a read-only op,
    and returns exactly one envelope response dict. It never raises across the
    socket boundary; every failure path returns a typed error envelope.
    """

    broker: FrameBroker
    limits: ApiLimits = field(default_factory=ApiLimits)
    auth_hook: AuthorizationHook = field(default=_default_auth_hook)
    health_registry: ComponentHealthRegistry | None = None
    audit_log: AuditLog = field(default_factory=AuditLog)
    identity_name: str = "topos-daemon"

    # -- public entry point --

    def handle(self, request: Any, peer: PeerCredentials | None) -> dict[str, Any]:
        """Validate and dispatch one envelope request. Returns one response."""
        request_id: Any = None
        try:
            if not isinstance(request, dict):
                return self._error(ErrorCode.BAD_REQUEST, "request must be an object", None, peer)
            request_id = request.get("id")
            if not self._valid_id(request_id):
                return self._error(ErrorCode.BAD_REQUEST, "id must be a non-empty string", None, peer)
            op = request.get("op")
            if not isinstance(op, str) or not op:
                return self._error(ErrorCode.INVALID_TYPE, "op must be a non-empty string", request_id, peer)
            version = request.get("v")
            if isinstance(version, bool) or not isinstance(version, int):
                return self._error(ErrorCode.INVALID_TYPE, "v must be an integer", request_id, peer, op=op)
            if version not in PROTOCOL_VERSIONS:
                return self._error(
                    ErrorCode.PROTOCOL_VERSION,
                    f"unsupported protocol version {version}; supported: {list(PROTOCOL_VERSIONS)}",
                    request_id,
                    peer,
                    op=op,
                )
            # Mutation-shaped / unknown ops are rejected before the auth hook.
            if op not in CAPABILITIES:
                return self._error(ErrorCode.UNKNOWN_OP, f"unknown op: {op}", request_id, peer, op=op)
            # Strict validation: reject unknown top-level fields. The allowed
            # parameter set is op-specific (closed per op).
            allowed = _OP_PARAMS.get(op, frozenset())
            unknown = set(request) - _REQUEST_FIELDS - allowed
            if unknown:
                return self._error(
                    ErrorCode.UNKNOWN_FIELD,
                    f"unknown field(s): {sorted(unknown)}",
                    request_id,
                    peer,
                    op=op,
                )
            # Authorization hook (after op + field validation, before work).
            deny = self.auth_hook(peer or PeerCredentials(None, None, None), op)
            if deny is not None:
                code, message = deny
                return self._error(code, message, request_id, peer, op=op, allowed=False)
            # Dispatch.
            result = self._dispatch(op, request, peer)
            response = {"id": request_id, "ok": True, "result": result}
            self._audit(peer, op, allowed=True)
            return response
        except _ApiError as exc:
            # _error() audits; no separate _audit here or the record doubles.
            return self._error(exc.code, exc.message, request_id, peer, op=str(request.get("op", "")) if isinstance(request, dict) else None)
        except Exception:
            # Never leak a raw exception across the socket boundary.
            return self._error(ErrorCode.INTERNAL, "internal error", request_id, peer, op=str(request.get("op", "")) if isinstance(request, dict) else None)

    # -- dispatch --

    def _dispatch(self, op: str, request: dict[str, Any], peer: PeerCredentials | None) -> dict[str, Any]:
        if op == "hello":
            return self._op_hello()
        if op == "current":
            return self._op_current()
        if op == "history":
            return self._op_history(request)
        if op == "entity":
            return self._op_entity(request)
        if op == "health":
            return self._op_health()
        # Unreachable: op validated against CAPABILITIES above.
        raise _ApiError(ErrorCode.UNKNOWN_OP, f"unknown op: {op}")

    def _op_hello(self) -> dict[str, Any]:
        return {
            "protocol_versions": list(PROTOCOL_VERSIONS),
            "capabilities": list(CAPABILITIES),
            "identity": {
                "name": self.identity_name,
                "version": TOPOS_VERSION,
            },
            "limits": {
                "max_request_bytes": self.limits.max_request_bytes,
                "max_response_items": self.limits.max_response_items,
                "max_response_bytes": self.limits.max_response_bytes,
                "request_timeout_s": self.limits.request_timeout_s,
                "max_clients": self.limits.max_clients,
                "history_capacity": self.broker.history_capacity(),
            },
        }

    def _op_current(self) -> dict[str, Any]:
        try:
            seq, frame = self.broker.current_entry()
        except FrameBrokerError as exc:
            raise _ApiError(ErrorCode.UNAVAILABLE, _safe_message(str(exc))) from exc
        metric_names = set(frame.host)
        for entity_frame in frame.entities.values():
            metric_names.update(entity_frame.metrics)
        return {
            "seq": seq,
            "frame": frame_to_jsonable(frame),
            "metrics_meta": _metrics_meta_for(metric_names),
        }

    def _op_history(self, request: dict[str, Any]) -> dict[str, Any]:
        params, extras = _pop_params(request, {"limit", "cursor", "since_ts", "until_ts"})
        if extras:
            raise _ApiError(ErrorCode.UNKNOWN_FIELD, f"unknown field(s): {sorted(extras)}")
        limit = params.get("limit", 1)
        cursor = params.get("cursor")
        since_ts = params.get("since_ts")
        until_ts = params.get("until_ts")
        # Exactly one form: sequence cursor OR time window. Both may set limit.
        if cursor is not None and (since_ts is not None or until_ts is not None):
            raise _ApiError(ErrorCode.BAD_REQUEST, "specify either cursor or a time window, not both")
        limit_int = _validate_envelope_limit(limit, self.limits.max_response_items)
        if cursor is not None:
            cursor_int = _validate_cursor_envelope(cursor)
            batch = self.broker.stream(limit=limit_int, cursor=cursor_int)
        elif since_ts is None and until_ts is None:
            # Tail form (no cursor, no window): most recent `limit`.
            batch = self.broker.stream(limit=limit_int, cursor=None)
        else:
            since_val = _validate_envelope_finite(since_ts, "since_ts") if since_ts is not None else None
            until_val = _validate_envelope_finite(until_ts, "until_ts") if until_ts is not None else None
            if since_val is not None and until_val is not None and since_val > until_val:
                raise _ApiError(ErrorCode.BAD_REQUEST, "since_ts must not exceed until_ts")
            batch = self.broker.stream_window(
                since_ts=since_val, until_ts=until_val, limit=limit_int
            )
        metric_names: set[str] = set()
        for _, frame in batch.entries:
            metric_names.update(frame.host)
            for entity_frame in frame.entities.values():
                metric_names.update(entity_frame.metrics)
        return {
            "frames": [{"seq": seq, "frame": frame_to_jsonable(frame)} for seq, frame in batch.entries],
            "oldest_seq": batch.oldest_seq,
            "latest_seq": batch.latest_seq,
            "next_cursor": batch.next_cursor,
            "gap": batch.gap,
            "metrics_meta": _metrics_meta_for(metric_names),
        }

    def _op_entity(self, request: dict[str, Any]) -> dict[str, Any]:
        params, extras = _pop_params(request, {"key"})
        if extras:
            raise _ApiError(ErrorCode.UNKNOWN_FIELD, f"unknown field(s): {sorted(extras)}")
        key = params.get("key")
        if not isinstance(key, str):
            raise _ApiError(ErrorCode.INVALID_TYPE, "key must be a string")
        _validate_entity_key(key)  # raises typed errors for path/injection shapes
        try:
            seq, frame = self.broker.current_entry()
        except FrameBrokerError as exc:
            raise _ApiError(ErrorCode.UNAVAILABLE, _safe_message(str(exc))) from exc
        entity_frame = frame.entities.get(key)
        if entity_frame is None:
            raise _ApiError(ErrorCode.NOT_FOUND, "entity not found")
        from topos.model import entity_frame_to_jsonable

        return {
            "seq": seq,
            "entity": entity_frame_to_jsonable(entity_frame),
            "metrics_meta": _metrics_meta_for(set(entity_frame.metrics)),
        }

    def _op_health(self) -> dict[str, Any]:
        if self.health_registry is None:
            raise _ApiError(ErrorCode.UNAVAILABLE, "health not available")
        return build_health_response(self.health_registry)

    # -- helpers --

    def _valid_id(self, request_id: Any) -> bool:
        # Opaque string, echoed verbatim. Bounded to avoid unbounded memory.
        if not isinstance(request_id, str):
            return False
        return 1 <= len(request_id) <= 256

    def _error(
        self,
        code: ErrorCode,
        message: str,
        request_id: Any,
        peer: PeerCredentials | None,
        *,
        op: str | None = None,
        allowed: bool = False,
    ) -> dict[str, Any]:
        self._audit(peer, op or "", allowed=allowed, code=code.value)
        return {
            "id": request_id,
            "ok": False,
            "error": {"code": code.value, "message": _safe_message(message)},
        }

    def _audit(
        self,
        peer: PeerCredentials | None,
        op: str,
        *,
        allowed: bool,
        code: str | None = None,
    ) -> None:
        import time

        self.audit_log.record(
            AuditRecord(
                peer=peer,
                op=op,
                allowed=allowed,
                error_code=code,
                ts=time.monotonic(),
            )
        )

    def enforce_response_bytes(self, response: dict[str, Any]) -> dict[str, Any]:
        """Bound the serialized response size; return an error envelope if over."""
        payload = json.dumps(response, sort_keys=True, separators=(",", ":")).encode("utf-8")
        if len(payload) <= self.limits.max_response_bytes:
            return response
        return {
            "id": response.get("id"),
            "ok": False,
            "error": {
                "code": ErrorCode.OVERSIZED_RESPONSE.value,
                "message": "response exceeds max_response_bytes",
            },
        }


class _ApiError(Exception):
    def __init__(self, code: ErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


# --- Validation helpers --------------------------------------------------


def _pop_params(request: dict[str, Any], allowed: set[str]) -> "tuple[dict[str, Any], set[str]]":
    """Split request into known params and unknown extras (already validated v/op/id)."""
    params = {k: v for k, v in request.items() if k in allowed}
    extras = set(request) - _REQUEST_FIELDS - allowed
    return params, extras


def _validate_cursor_envelope(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise _ApiError(ErrorCode.MALFORMED_CURSOR, "cursor must be an integer")
    if value < -1:
        raise _ApiError(ErrorCode.MALFORMED_CURSOR, "cursor must be at least -1")
    return value


def _validate_envelope_limit(value: object, max_items: int) -> int:
    """Validate the history ``limit`` parameter with typed envelope errors."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise _ApiError(ErrorCode.INVALID_TYPE, "limit must be an integer")
    if not 1 <= value <= max_items:
        raise _ApiError(ErrorCode.OUT_OF_RANGE, f"limit must be between 1 and {max_items}")
    return value


def _validate_envelope_finite(value: object, field: str) -> float:
    """Validate a finite numeric parameter with typed envelope errors."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _ApiError(ErrorCode.INVALID_TYPE, f"{field} must be a number")
    result = float(value)
    if not math.isfinite(result):
        raise _ApiError(ErrorCode.NON_FINITE, f"{field} must be finite")
    return result


def _validate_entity_key(key: str) -> None:
    """Reject path-shaped and registry-shaped injection inputs.

    EntityKey is a cgroup path relative to the cgroup root (``""`` is the
    root). It never starts with ``/``, never contains ``..`` components, and
    never contains NUL or control characters. This validation is the only
    place user input touches the entity lookup; the lookup itself is a pure
    dict access against the current in-memory frame and never reaches the
    filesystem, registry-by-key, command, or sysfs/procfs.
    """
    if key.startswith("/"):
        raise _ApiError(ErrorCode.INVALID_TYPE, "key must not be an absolute path")
    if "\x00" in key:
        raise _ApiError(ErrorCode.INVALID_TYPE, "key must not contain NUL")
    if any(ord(c) < 32 or ord(c) == 127 for c in key):
        raise _ApiError(ErrorCode.INVALID_TYPE, "key must not contain control characters")
    parts = key.split("/")
    if any(part == ".." for part in parts):
        raise _ApiError(ErrorCode.INVALID_TYPE, "key must not contain parent traversal")


def _safe_message(text: str) -> str:
    """Bound and sanitize a message for public disclosure.

    Reuses the P47 component_health sanitizer for path/secret redaction and
    byte bounding so the P51 safety contract persists through the new envelope.
    """
    from topos.daemon.component_health import sanitize_public_text, MAX_HEALTH_ERROR_BYTES

    return sanitize_public_text(text, limit=MAX_HEALTH_ERROR_BYTES)


# --- Server: envelope + legacy compatibility -----------------------------


class EnvelopeUnixServer(BrokerUnixServer):
    """Unix-socket server serving both the P52 envelope and the P51 legacy protocol.

    Requests carrying a ``v`` field are dispatched through :class:`DaemonApi`
    (single-line envelope response). Requests without ``v`` flow through the
    P51 :meth:`FrameBroker.responses` multi-line protocol unchanged.
    """

    def __init__(
        self,
        socket_path: Path,
        broker: FrameBroker,
        api: DaemonApi,
        *,
        mode: int = 0o660,
    ) -> None:
        self.api = api
        # Reuse the parent's bounded client slot / timeout machinery.
        super().__init__(
            socket_path,
            broker,
            request_timeout_s=api.limits.request_timeout_s,
            max_clients=api.limits.max_clients,
        )
        self.RequestHandlerClass = _EnvelopeHandler


class _EnvelopeHandler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        api: DaemonApi = self.server.api  # type: ignore[attr-defined]
        limits: ApiLimits = api.limits
        self.connection.settimeout(limits.request_timeout_s)
        peer = read_peer_credentials(self.connection)
        try:
            line = self.rfile.readline(limits.max_request_bytes + 1)
        except socket.timeout:
            self._write(self._legacy_or_envelope_error(peer, None, ErrorCode.REQUEST_TIMEOUT, "request timed out"))
            return
        except OSError:
            return
        if not line:
            return
        if len(line) > limits.max_request_bytes or not line.endswith(b"\n"):
            self._write(self._legacy_or_envelope_error(peer, None, ErrorCode.OVERSIZED_REQUEST, "request exceeds maximum size"))
            return
        try:
            request = json.loads(line.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            self._write(self._legacy_or_envelope_error(peer, None, ErrorCode.BAD_REQUEST, f"malformed request: {exc}"))
            return
        # Envelope path: a dict carrying a ``v`` field.
        if isinstance(request, dict) and "v" in request:
            response = api.handle(request, peer)
            response = api.enforce_response_bytes(response)
            self._write(response)
            return
        # Legacy compatibility path: delegate to the P51 broker.responses()
        # multi-line protocol unchanged.
        try:
            if not isinstance(request, dict):
                raise ValueError("request must be an object")
            responses = self.server.broker.responses(request)  # type: ignore[attr-defined]
        except Exception:
            responses = [{"type": "error", "error": "request failed"}]
        for response in responses:
            self._write(response)

    def _write(self, payload: dict[str, Any]) -> None:
        try:
            self.wfile.write(
                json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"
            )
        except OSError:
            pass

    def _legacy_or_envelope_error(
        self,
        peer: PeerCredentials | None,
        request_id: Any,
        code: ErrorCode,
        message: str,
    ) -> dict[str, Any]:
        # Without a parsed envelope we cannot know the client id; emit a legacy
        # error line so P51 clients still see a typed error.
        return {"type": "error", "error": message}


def serve_versioned_unix_socket(
    socket_path: Path,
    broker: FrameBroker,
    api: DaemonApi,
    *,
    mode: int = 0o660,
) -> EnvelopeUnixServer:
    """Create and bind a versioned Unix-socket server (P52)."""
    socket_path.parent.mkdir(parents=True, exist_ok=True)
    socket_path.unlink(missing_ok=True)
    server = EnvelopeUnixServer(socket_path, broker, api, mode=mode)
    os.chmod(socket_path, mode)
    return server


__all__ = [
    "ApiLimits",
    "AuditLog",
    "AuditRecord",
    "AuthorizationHook",
    "CAPABILITIES",
    "DaemonApi",
    "DEFAULT_MAX_INFLIGHT_PER_CLIENT",
    "DEFAULT_MAX_RESPONSE_BYTES",
    "DEFAULT_MAX_RESPONSE_ITEMS",
    "EnvelopeUnixServer",
    "ErrorCode",
    "PeerCredentials",
    "PROTOCOL_VERSION",
    "PROTOCOL_VERSIONS",
    "Sensitivity",
    "SO_PEERCRED",
    "metric_metadata",
    "metric_sensitivity",
    "read_peer_credentials",
    "serve_versioned_unix_socket",
]
