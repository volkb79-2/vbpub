"""Direct public-contract boundaries for the hardened HTTP gateway."""

from __future__ import annotations

import io
import json
from types import SimpleNamespace

import pytest

from topos.daemon.api import Sensitivity
from topos.daemon.client import DaemonConnectError, DaemonProtocolError, DaemonResponseError
from topos.daemon.http_gateway import (
    IDENTITY_HEADER,
    MAX_HTTP_PATH_BYTES,
    GatewayAuthConfig,
    GatewayConfig,
    GatewayStartupError,
    VersionedReadHttpGateway,
    _parse_integer,
    _parse_query,
    _parse_timestamp,
)


class _Headers:
    def __init__(self, values: list[str]) -> None:
        self._values = values

    def get_all(self, name: str, default: list[str]) -> list[str]:
        assert name == IDENTITY_HEADER
        return self._values or default


class _Request:
    def __init__(self, path: str, *, identities: list[str] | None = None) -> None:
        self.path = path
        self.headers = _Headers(["operator"] if identities is None else identities)
        self.client_address = ("127.0.0.1", 1)
        self.wfile = io.BytesIO()
        self.status: int | None = None
        self.headers_sent: dict[str, str] = {}

    def send_response(self, status: int) -> None:
        self.status = status

    def send_header(self, name: str, value: str) -> None:
        self.headers_sent[name] = value

    def end_headers(self) -> None:
        pass

    def body(self) -> dict[str, object]:
        return json.loads(self.wfile.getvalue())


def _gateway(client: object) -> VersionedReadHttpGateway:
    """Build only the request dispatcher; no listener is opened for these tests."""
    gateway = object.__new__(VersionedReadHttpGateway)
    gateway.client = client  # type: ignore[assignment]
    gateway.config = GatewayConfig(auth=GatewayAuthConfig({"operator": "operational"}))
    return gateway


@pytest.mark.parametrize(
    "principals",
    [
        {},
        {"bad space": "public"},
        {"operator": "not-a-sensitivity"},
    ],
)
def test_gateway_auth_config_rejects_empty_invalid_or_unbounded_principals(
    principals: dict[str, str],
) -> None:
    with pytest.raises(GatewayStartupError):
        GatewayAuthConfig(principals)


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"host": ""}, "non-empty"),
        ({"host": "gateway.example"}, "IP address"),
        ({"port": True}, "port"),
        ({"port": 65536}, "port"),
        ({"auth": object()}, "authentication"),
    ],
)
def test_gateway_config_rejects_malformed_listener_boundary(
    kwargs: dict[str, object], message: str
) -> None:
    with pytest.raises(GatewayStartupError, match=message):
        GatewayConfig(**kwargs)  # type: ignore[arg-type]


def test_query_and_scalar_parsers_reject_ambiguous_or_non_finite_input() -> None:
    with pytest.raises(ValueError, match="too large"):
        _parse_query("/v1/current?" + "x" * MAX_HTTP_PATH_BYTES)
    with pytest.raises(ValueError, match="invalid query"):
        _parse_query("/v1/history?limit")
    with pytest.raises(ValueError, match="duplicate"):
        _parse_query("/v1/history?limit=1&limit=2")
    with pytest.raises(ValueError, match="invalid limit"):
        _parse_integer("01", "limit")
    assert _parse_integer("-1", "cursor", allow_minus_one=True) == -1
    with pytest.raises(ValueError, match="invalid since_ts"):
        _parse_timestamp("nan", "since_ts")
    with pytest.raises(ValueError, match="invalid until_ts"):
        _parse_timestamp("not-a-number", "until_ts")


@pytest.mark.parametrize(
    "path",
    [
        "/v1/unknown",
        "/v1/hello?unexpected=1",
        "/v1/current?unexpected=1",
        "/v1/history?cursor=1&since_ts=1.0",
        "/v1/entity?other=key",
    ],
)
def test_invalid_routes_and_query_shapes_return_only_typed_errors(path: str) -> None:
    request = _Request(path)
    _gateway(SimpleNamespace())._handle_get(request)
    expected = "not_found" if path == "/v1/unknown" else "bad_request"
    assert request.status == (404 if expected == "not_found" else 400)
    assert request.body() == {"error": {"code": expected}}
    assert request.headers_sent["Cache-Control"] == "no-store"
    assert request.headers_sent["X-Content-Type-Options"] == "nosniff"


@pytest.mark.parametrize(
    ("error", "status", "code"),
    [
        (DaemonResponseError("not present", code="not_found"), 404, "not_found"),
        (DaemonResponseError("private detail", code="private_internal_code"), 502, "daemon_error"),
        (DaemonConnectError("/private/socket"), 502, "daemon_unavailable"),
        (DaemonProtocolError("private wire detail"), 502, "daemon_protocol_error"),
        (RuntimeError("private implementation detail"), 502, "gateway_error"),
    ],
)
def test_current_route_translates_daemon_and_internal_failures_without_detail_leakage(
    error: Exception, status: int, code: str
) -> None:
    request = _Request("/v1/current")
    _gateway(SimpleNamespace(request_current=lambda: (_ for _ in ()).throw(error)))._handle_get(request)
    assert request.status == status
    assert request.body() == {"error": {"code": code}}
    assert b"private" not in request.wfile.getvalue()


def test_unauthenticated_and_duplicate_forwarded_identity_are_refused_before_dispatch() -> None:
    client = SimpleNamespace(request_current=lambda: pytest.fail("daemon dispatch must not occur"))
    for identities in ([], ["operator", "operator"]):
        request = _Request("/v1/current", identities=identities)
        _gateway(client)._handle_get(request)
        assert request.status == 401
        assert request.body() == {"error": {"code": "unauthenticated"}}


def test_gateway_accepts_only_normalized_sensitivity_values() -> None:
    auth = GatewayAuthConfig({"operator": "public"})
    assert auth.principals == {"operator": Sensitivity.PUBLIC}
