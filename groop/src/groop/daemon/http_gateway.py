"""Hardened stdlib HTTP adapter for the versioned daemon read surface.

The daemon's Unix socket is group-authorized.  This module deliberately does
not extend that trust to HTTP: every request needs an authenticated principal
from a trusted local reverse proxy, and metric values are redacted before the
HTTP response is serialized.
"""

from __future__ import annotations

import ipaddress
import json
import math
import re
import socket
from collections.abc import Mapping
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlsplit

from groop.daemon.api import Sensitivity
from groop.daemon.client import (
    DaemonClient,
    DaemonConnectError,
    DaemonProtocolError,
    DaemonResponseError,
)
from groop.model import entity_frame_to_jsonable, frame_to_jsonable


IDENTITY_HEADER = "X-Groop-Principal"
MAX_HTTP_PATH_BYTES = 8 * 1024
_PRINCIPAL_RE = re.compile(r"[A-Za-z0-9._-]{1,128}\Z")
_SENSITIVITY_RANK = {
    Sensitivity.PUBLIC.value: 0,
    Sensitivity.OPERATIONAL.value: 1,
    Sensitivity.SENSITIVE.value: 2,
}
_PUBLIC_DAEMON_CODES = frozenset(
    {
        "bad_request",
        "unknown_op",
        "unknown_field",
        "invalid_type",
        "non_finite",
        "out_of_range",
        "malformed_cursor",
        "oversized_request",
        "oversized_response",
        "request_timeout",
        "server_busy",
        "unavailable",
        "denied",
        "not_found",
        "protocol_version",
        "internal",
    }
)


class GatewayStartupError(RuntimeError):
    """Typed refusal to start an unsafe HTTP listener."""


class _RouteNotFound(ValueError):
    """Internal route miss that maps to an HTTP 404 without client dispatch."""


@dataclass(frozen=True)
class GatewayAuthConfig:
    """Trusted-proxy principals and their maximum visible sensitivity."""

    principals: Mapping[str, Sensitivity | str]

    def __post_init__(self) -> None:
        if not isinstance(self.principals, Mapping):
            raise GatewayStartupError("authentication principals must be a mapping")
        normalized: dict[str, Sensitivity] = {}
        for principal, ceiling in self.principals.items():
            if not isinstance(principal, str) or not _PRINCIPAL_RE.fullmatch(principal):
                raise GatewayStartupError("principal names must be 1-128 ASCII letters, digits, dot, dash, or underscore")
            try:
                normalized[principal] = Sensitivity(ceiling)
            except (TypeError, ValueError) as exc:
                raise GatewayStartupError("principal sensitivity ceilings must be public, operational, or sensitive") from exc
        if not normalized:
            raise GatewayStartupError("authentication configuration must contain at least one principal")
        object.__setattr__(self, "principals", normalized)


@dataclass(frozen=True)
class GatewayConfig:
    """Listener and trusted-proxy configuration.

    ``allow_non_loopback`` is the explicit operator opt-in required before a
    non-loopback listener can be created.  It is useful only with a local
    authenticated reverse proxy: forwarded identities are accepted exclusively
    from loopback peers.
    """

    host: str = "127.0.0.1"
    port: int = 0
    auth: GatewayAuthConfig | None = None
    allow_non_loopback: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.host, str) or not self.host:
            raise GatewayStartupError("HTTP bind host must be a non-empty address")
        try:
            ipaddress.ip_address(self.host)
        except ValueError as exc:
            raise GatewayStartupError("HTTP bind host must be an IP address") from exc
        if isinstance(self.port, bool) or not isinstance(self.port, int) or not 0 <= self.port <= 65535:
            raise GatewayStartupError("HTTP port must be an integer between 0 and 65535")
        if self.auth is not None and not isinstance(self.auth, GatewayAuthConfig):
            raise GatewayStartupError("authentication configuration is invalid")
        if not isinstance(self.allow_non_loopback, bool):
            raise GatewayStartupError("allow_non_loopback must be a boolean")


def _is_loopback_address(address: str) -> bool:
    try:
        return ipaddress.ip_address(address).is_loopback
    except ValueError:
        return False


def _bind_is_loopback(host: str) -> bool:
    """Return whether every concrete address for a bind host is loopback."""
    return _is_loopback_address(host)


def _validate_startup(config: GatewayConfig) -> None:
    if _bind_is_loopback(config.host):
        return
    if not config.allow_non_loopback:
        raise GatewayStartupError("refusing non-loopback HTTP bind without --allow-non-loopback")
    if config.auth is None:
        raise GatewayStartupError("refusing non-loopback HTTP bind without authentication configuration")


def _principal_for_peer(
    peer_host: str,
    identity_values: list[str],
    auth: GatewayAuthConfig | None,
) -> tuple[str, Sensitivity] | None:
    """Authenticate a reverse-proxy identity only over a loopback hop."""
    if auth is None or not _is_loopback_address(peer_host):
        return None
    if len(identity_values) != 1:
        return None
    principal = identity_values[0]
    ceiling = auth.principals.get(principal)
    if not isinstance(ceiling, Sensitivity):
        return None
    return principal, ceiling


def _redaction_marker(sensitivity: str) -> dict[str, object]:
    return {"redacted": True, "sensitivity": sensitivity}


def _redact_metrics(
    metrics: dict[str, Any], metrics_meta: Mapping[str, Mapping[str, object]], ceiling: Sensitivity
) -> None:
    ceiling_rank = _SENSITIVITY_RANK[ceiling.value]
    for metric_name in list(metrics):
        metadata = metrics_meta.get(metric_name)
        raw_sensitivity = metadata.get("sensitivity") if isinstance(metadata, Mapping) else None
        sensitivity = raw_sensitivity if raw_sensitivity in _SENSITIVITY_RANK else Sensitivity.SENSITIVE.value
        if _SENSITIVITY_RANK[sensitivity] > ceiling_rank:
            metrics[metric_name] = _redaction_marker(sensitivity)


def _redact_frame(frame: dict[str, Any], metrics_meta: Mapping[str, Mapping[str, object]], ceiling: Sensitivity) -> None:
    host = frame.get("host")
    if isinstance(host, dict):
        _redact_metrics(host, metrics_meta, ceiling)
    entities = frame.get("entities")
    if not isinstance(entities, dict):
        return
    for entity_frame in entities.values():
        if isinstance(entity_frame, dict) and isinstance(entity_frame.get("metrics"), dict):
            _redact_metrics(entity_frame["metrics"], metrics_meta, ceiling)


def _parse_query(path: str) -> tuple[str, dict[str, str]]:
    if len(path.encode("utf-8")) > MAX_HTTP_PATH_BYTES:
        raise ValueError("request target is too large")
    split = urlsplit(path)
    try:
        pairs = parse_qsl(split.query, keep_blank_values=True, strict_parsing=True, max_num_fields=5)
    except ValueError as exc:
        raise ValueError("invalid query string") from exc
    query: dict[str, str] = {}
    for key, value in pairs:
        if key in query:
            raise ValueError("duplicate query field")
        query[key] = value
    return split.path, query


def _parse_integer(value: str, field: str, *, allow_minus_one: bool = False) -> int:
    pattern = r"-1|0|[1-9][0-9]*" if allow_minus_one else r"0|[1-9][0-9]*"
    if not re.fullmatch(pattern, value):
        raise ValueError(f"invalid {field}")
    return int(value)


def _parse_timestamp(value: str, field: str) -> float:
    try:
        parsed = float(value)
    except ValueError as exc:
        raise ValueError(f"invalid {field}") from exc
    if not math.isfinite(parsed):
        raise ValueError(f"invalid {field}")
    return parsed


def _daemon_error_status(code: str | None) -> HTTPStatus:
    if code == "not_found":
        return HTTPStatus.NOT_FOUND
    if code in {"bad_request", "unknown_field", "invalid_type", "non_finite", "out_of_range", "malformed_cursor"}:
        return HTTPStatus.BAD_REQUEST
    if code == "denied":
        return HTTPStatus.FORBIDDEN
    if code in {"unavailable", "server_busy", "request_timeout"}:
        return HTTPStatus.SERVICE_UNAVAILABLE
    return HTTPStatus.BAD_GATEWAY


class _GatewayHttpServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class _GatewayHttpServerV6(_GatewayHttpServer):
    address_family = socket.AF_INET6


@dataclass
class VersionedReadHttpGateway:
    """A running or runnable HTTP adapter backed only by ``DaemonClient`` methods."""

    client: DaemonClient
    config: GatewayConfig = field(default_factory=GatewayConfig)
    _server: _GatewayHttpServer = field(init=False, repr=False)

    def __post_init__(self) -> None:
        _validate_startup(self.config)
        handler = self._handler_type()
        server_class = _GatewayHttpServerV6 if ":" in self.config.host else _GatewayHttpServer
        try:
            self._server = server_class((self.config.host, self.config.port), handler)
        except OSError as exc:
            raise GatewayStartupError("could not bind HTTP listener") from exc

    @property
    def server_address(self) -> tuple[str, int]:
        address = self._server.server_address
        return str(address[0]), int(address[1])

    def serve_forever(self) -> None:
        self._server.serve_forever()

    def shutdown(self) -> None:
        self._server.shutdown()

    def server_close(self) -> None:
        self._server.server_close()

    def _handler_type(self) -> type[BaseHTTPRequestHandler]:
        gateway = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "groop-gateway"
            sys_version = ""

            def do_GET(self) -> None:  # noqa: N802 - stdlib HTTP handler hook.
                gateway._handle_get(self)

            def do_POST(self) -> None:  # noqa: N802 - stdlib HTTP handler hook.
                gateway._write_error(self, HTTPStatus.METHOD_NOT_ALLOWED, "method_not_allowed")

            do_PUT = do_POST
            do_PATCH = do_POST
            do_DELETE = do_POST
            do_HEAD = do_POST
            do_OPTIONS = do_POST

            def log_message(self, _format: str, *_args: object) -> None:
                """Do not emit request paths or headers through the process log."""

        return Handler

    def _handle_get(self, request: BaseHTTPRequestHandler) -> None:
        identity_values = request.headers.get_all(IDENTITY_HEADER, [])
        authenticated = _principal_for_peer(str(request.client_address[0]), identity_values, self.config.auth)
        if authenticated is None:
            self._write_error(request, HTTPStatus.UNAUTHORIZED, "unauthenticated")
            return
        _principal, ceiling = authenticated
        try:
            path, query = _parse_query(request.path)
            payload = self._route(path, query, ceiling)
        except _RouteNotFound:
            self._write_error(request, HTTPStatus.NOT_FOUND, "not_found")
            return
        except ValueError:
            self._write_error(request, HTTPStatus.BAD_REQUEST, "bad_request")
            return
        except DaemonResponseError as exc:
            code = exc.code if exc.code in _PUBLIC_DAEMON_CODES else "daemon_error"
            self._write_error(request, _daemon_error_status(code), code)
            return
        except DaemonConnectError:
            self._write_error(request, HTTPStatus.BAD_GATEWAY, "daemon_unavailable")
            return
        except DaemonProtocolError:
            self._write_error(request, HTTPStatus.BAD_GATEWAY, "daemon_protocol_error")
            return
        except Exception:  # noqa: BLE001 - no internal details may cross HTTP.
            self._write_error(request, HTTPStatus.BAD_GATEWAY, "gateway_error")
            return
        self._write_json(request, HTTPStatus.OK, payload)

    def _route(self, path: str, query: dict[str, str], ceiling: Sensitivity) -> dict[str, object]:
        if path == "/v1/hello":
            if query:
                raise ValueError("hello takes no query fields")
            hello = self.client.request_hello()
            return {
                "capabilities": list(hello.capabilities),
                "identity": hello.identity,
                "limits": hello.limits,
                "protocol_versions": list(hello.protocol_versions),
            }
        if path == "/v1/current":
            if query:
                raise ValueError("current takes no query fields")
            current = self.client.request_current()
            frame = frame_to_jsonable(current.frame)
            _redact_frame(frame, current.metrics_meta, ceiling)
            return {"frame": frame, "metrics_meta": current.metrics_meta, "seq": current.seq}
        if path == "/v1/history":
            allowed = {"limit", "cursor", "since_ts", "until_ts"}
            if set(query) - allowed:
                raise ValueError("unknown history query field")
            limit = _parse_integer(query["limit"], "limit") if "limit" in query else 1
            cursor = _parse_integer(query["cursor"], "cursor", allow_minus_one=True) if "cursor" in query else None
            since_ts = _parse_timestamp(query["since_ts"], "since_ts") if "since_ts" in query else None
            until_ts = _parse_timestamp(query["until_ts"], "until_ts") if "until_ts" in query else None
            if cursor is not None and (since_ts is not None or until_ts is not None):
                raise ValueError("cursor and time window are mutually exclusive")
            history = self.client.request_history(
                limit=limit, cursor=cursor, since_ts=since_ts, until_ts=until_ts
            )
            entries: list[dict[str, object]] = []
            for seq, entry_frame in history.entries:
                frame = frame_to_jsonable(entry_frame)
                _redact_frame(frame, history.metrics_meta, ceiling)
                entries.append({"frame": frame, "seq": seq})
            return {
                "frames": entries,
                "gap": history.gap,
                "latest_seq": history.latest_seq,
                "metrics_meta": history.metrics_meta,
                "next_cursor": history.next_cursor,
                "oldest_seq": history.oldest_seq,
            }
        if path == "/v1/entity":
            if set(query) != {"key"}:
                raise ValueError("entity requires exactly one key query field")
            entity = self.client.request_entity(query["key"])
            entity_payload = entity_frame_to_jsonable(entity.entity)
            metrics = entity_payload.get("metrics")
            if isinstance(metrics, dict):
                _redact_metrics(metrics, entity.metrics_meta, ceiling)
            return {"entity": entity_payload, "metrics_meta": entity.metrics_meta, "seq": entity.seq}
        raise _RouteNotFound("unknown route")

    def _write_error(self, request: BaseHTTPRequestHandler, status: HTTPStatus, code: str) -> None:
        headers = {"Allow": "GET"} if status is HTTPStatus.METHOD_NOT_ALLOWED else None
        self._write_json(request, status, {"error": {"code": code}}, headers=headers)

    def _write_json(
        self,
        request: BaseHTTPRequestHandler,
        status: HTTPStatus,
        payload: dict[str, object],
        *,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        request.send_response(status.value)
        request.send_header("Content-Type", "application/json; charset=utf-8")
        request.send_header("Content-Length", str(len(body)))
        request.send_header("Cache-Control", "no-store")
        request.send_header("X-Content-Type-Options", "nosniff")
        if headers is not None:
            for name, value in headers.items():
                request.send_header(name, value)
        request.end_headers()
        request.wfile.write(body)


def serve_versioned_http_gateway(
    daemon_socket: Path,
    *,
    config: GatewayConfig = GatewayConfig(),
    timeout_s: float | None = 5.0,
) -> VersionedReadHttpGateway:
    """Build a gateway backed by a real typed ``DaemonClient``.

    Call ``serve_forever`` on the result, or run it in a managed thread in an
    embedding service.  This intentionally does not probe or re-open the Unix
    socket at startup; every route invokes exactly one typed client method.
    """
    return VersionedReadHttpGateway(DaemonClient(daemon_socket, timeout_s=timeout_s), config)


__all__ = [
    "GatewayAuthConfig",
    "GatewayConfig",
    "GatewayStartupError",
    "IDENTITY_HEADER",
    "VersionedReadHttpGateway",
    "serve_versioned_http_gateway",
]
