"""P67 adversarial tests for the hardened versioned-read HTTP gateway."""

from __future__ import annotations

import http.client
import ipaddress
import json
import subprocess
import sys
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from conftest import fixture_frame
from topos.daemon import DaemonApi, FrameBroker, serve_versioned_unix_socket
from topos.daemon.client import DaemonClient
from topos.daemon.http_gateway import (
    IDENTITY_HEADER,
    GatewayAuthConfig,
    GatewayConfig,
    GatewayStartupError,
    VersionedReadHttpGateway,
    _daemon_error_status,
    _principal_for_peer,
    serve_versioned_http_gateway,
)
from topos.cli import _main_gateway, parse_gateway_args


@contextmanager
def _live_gateway(tmp_path: Path, *, ceiling: str = "operational") -> Iterator[VersionedReadHttpGateway]:
    """Stand up the real DaemonApi -> DaemonClient -> HTTP gateway stack."""
    daemon_socket = tmp_path / "daemon.sock"
    broker = FrameBroker([fixture_frame()])
    daemon = serve_versioned_unix_socket(daemon_socket, broker, DaemonApi(broker))
    daemon_thread = threading.Thread(target=daemon.serve_forever, daemon=True)
    daemon_thread.start()
    gateway = serve_versioned_http_gateway(
        daemon_socket,
        config=GatewayConfig(auth=GatewayAuthConfig({"operator": ceiling})),
    )
    gateway_thread = threading.Thread(target=gateway.serve_forever, daemon=True)
    gateway_thread.start()
    try:
        yield gateway
    finally:
        gateway.shutdown()
        gateway.server_close()
        gateway_thread.join(timeout=2.0)
        daemon.shutdown()
        daemon.server_close()
        daemon_thread.join(timeout=2.0)
        broker.stop()
        broker.join(timeout=2.0)


def _request(
    gateway: VersionedReadHttpGateway,
    method: str,
    target: str,
    *,
    authenticated: bool = True,
) -> tuple[int, dict[str, str], bytes]:
    host, port = gateway.server_address
    connection = http.client.HTTPConnection(host, port, timeout=2.0)
    headers = {IDENTITY_HEADER: "operator"} if authenticated else {}
    connection.request(method, target, headers=headers)
    response = connection.getresponse()
    body = response.read()
    returned_headers = {key.lower(): value for key, value in response.getheaders()}
    status = response.status
    connection.close()
    return status, returned_headers, body


def test_default_bind_is_loopback(tmp_path: Path) -> None:
    gateway = VersionedReadHttpGateway(DaemonClient(tmp_path / "daemon.sock"))
    try:
        host, _port = gateway.server_address
        assert ipaddress.ip_address(host).is_loopback
    finally:
        gateway.server_close()


def test_non_loopback_bind_requires_explicit_opt_in_and_auth(tmp_path: Path) -> None:
    client = DaemonClient(tmp_path / "daemon.sock")
    with pytest.raises(GatewayStartupError, match="allow-non-loopback"):
        VersionedReadHttpGateway(client, GatewayConfig(host="0.0.0.0"))
    with pytest.raises(GatewayStartupError, match="authentication"):
        VersionedReadHttpGateway(
            client, GatewayConfig(host="0.0.0.0", allow_non_loopback=True)
        )
    gateway = VersionedReadHttpGateway(
        client,
        GatewayConfig(
            host="0.0.0.0",
            allow_non_loopback=True,
            auth=GatewayAuthConfig({"operator": "operational"}),
        ),
    )
    gateway.server_close()


def test_gateway_configuration_rejects_untyped_trust_boundary_inputs() -> None:
    with pytest.raises(GatewayStartupError, match="principals must be a mapping"):
        GatewayAuthConfig([("operator", "operational")])
    with pytest.raises(GatewayStartupError, match="must be a boolean"):
        GatewayConfig(allow_non_loopback="yes")


def test_daemon_import_does_not_load_the_http_gateway() -> None:
    result = subprocess.run(
        [sys.executable, "-c", "import topos.daemon, sys; assert 'topos.daemon.http_gateway' not in sys.modules"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_gateway_cli_has_an_explicit_non_loopback_opt_in(capsys: pytest.CaptureFixture[str]) -> None:
    args = parse_gateway_args(["serve", "--principal", "operator:operational"])
    assert args.host == "127.0.0.1"
    assert args.allow_non_loopback is False
    assert _main_gateway(
        [
            "serve",
            "--host",
            "0.0.0.0",
            "--principal",
            "operator:operational",
        ]
    ) == 2
    assert "allow-non-loopback" in capsys.readouterr().err


def test_unauthenticated_request_has_no_telemetry_bytes(tmp_path: Path) -> None:
    with _live_gateway(tmp_path) as gateway:
        status, headers, body = _request(gateway, "GET", "/v1/current", authenticated=False)
    assert status == 401
    assert headers["content-type"].startswith("application/json")
    assert b"cgroup_procs" not in body
    assert b"4096000000" not in body


def test_server_side_redaction_preserves_key_and_metadata(tmp_path: Path) -> None:
    with _live_gateway(tmp_path) as gateway:
        status, _headers, body = _request(
            gateway,
            "GET",
            "/v1/entity?key=system.slice%2Fdocker-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.scope",
        )
    decoded = json.loads(body)
    assert status == 200
    assert b'"pids_max":[512,"exact"]' not in body
    assert decoded["entity"]["metrics"]["pids_max"] == {
        "redacted": True,
        "sensitivity": "sensitive",
    }
    assert decoded["metrics_meta"]["pids_max"]["sensitivity"] == "sensitive"
    assert decoded["metrics_meta"]["pids_max"]["unit"]


@pytest.mark.parametrize("target", ["/v1/current", "/v1/history?limit=1"])
def test_frame_routes_redact_server_side_above_the_ceiling(tmp_path: Path, target: str) -> None:
    """The frame walker must redact too, not just the single-entity route.

    The shared enforcement point's ``FRAME`` visitor traverses a shape (``host``
    map plus ``entities`` map) the ``ENTITY_FRAME`` visitor never sees, so a
    shape drift in ``frame_to_jsonable`` would silently disarm redaction on
    exactly the two routes that carry the most telemetry while every other
    oracle stayed green.
    """
    with _live_gateway(tmp_path) as gateway:
        status, _headers, body = _request(gateway, "GET", target)
    assert status == 200
    assert b'"pids_max":[512,"exact"]' not in body
    decoded = json.loads(body)
    frame = decoded["frame"] if target == "/v1/current" else decoded["frames"][0]["frame"]
    sensitive = {
        name
        for name, meta in decoded["metrics_meta"].items()
        if meta["sensitivity"] == "sensitive"
    }
    assert sensitive, "fixture must carry a sensitive metric or this oracle proves nothing"
    seen = 0
    for entity_frame in frame["entities"].values():
        for name, value in entity_frame["metrics"].items():
            if name in sensitive:
                assert value == {"redacted": True, "sensitivity": "sensitive"}
                seen += 1
    assert seen, "no sensitive metric reached the frame walker"


def test_forwarded_identity_from_non_loopback_peer_is_not_trusted() -> None:
    auth = GatewayAuthConfig({"operator": "operational"})
    assert _principal_for_peer("203.0.113.9", ["operator"], auth) is None
    assert _principal_for_peer("127.0.0.1", ["operator"], auth) == (
        "operator",
        auth.principals["operator"],
    )


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
@pytest.mark.parametrize(
    "target",
    [
        "/v1/hello",
        "/v1/current",
        "/v1/history?limit=1",
        "/v1/entity?key=system.slice",
    ],
)
def test_every_mutating_method_is_rejected_on_every_route(
    tmp_path: Path, method: str, target: str
) -> None:
    with _live_gateway(tmp_path) as gateway:
        status, headers, body = _request(gateway, method, target)
    assert status == 405
    assert headers["allow"] == "GET"
    assert b"cgroup_procs" not in body


def test_routes_return_decoded_json_and_metrics_meta_without_cors(tmp_path: Path) -> None:
    with _live_gateway(tmp_path) as gateway:
        hello_status, hello_headers, hello_body = _request(gateway, "GET", "/v1/hello")
        current_status, current_headers, current_body = _request(gateway, "GET", "/v1/current")
        history_status, _history_headers, history_body = _request(gateway, "GET", "/v1/history?limit=1")
        entity_status, _entity_headers, entity_body = _request(gateway, "GET", "/v1/entity?key=system.slice")

    assert hello_status == current_status == history_status == entity_status == 200
    assert "access-control-allow-origin" not in hello_headers
    assert "access-control-allow-origin" not in current_headers
    assert json.loads(hello_body)["identity"]["name"] == "topos-daemon"
    current = json.loads(current_body)
    history = json.loads(history_body)
    entity = json.loads(entity_body)
    assert current["metrics_meta"]["cgroup_procs"]["sensitivity"] == "sensitive"
    assert history["metrics_meta"]["cgroup_procs"]["sensitivity"] == "sensitive"
    assert entity["metrics_meta"]["cgroup_procs"]["sensitivity"] == "sensitive"


def test_entity_route_preserves_the_valid_empty_root_key(tmp_path: Path) -> None:
    with _live_gateway(tmp_path) as gateway:
        status, _headers, body = _request(gateway, "GET", "/v1/entity?key=")
    assert status == 200
    assert json.loads(body)["entity"]["entity"]["key"] == ""


@pytest.mark.parametrize(
    ("target", "expected_status"),
    [
        ("/v1/entity?key=not-present.slice", 404),
        ("/v1/entity?key=%2Fetc%2Fpasswd", 400),
        ("/v1/history?limit=0", 400),
        ("/v1/history?limit=1&unexpected=value", 400),
    ],
)
def test_typed_daemon_errors_and_closed_query_mapping(
    tmp_path: Path, target: str, expected_status: int
) -> None:
    with _live_gateway(tmp_path) as gateway:
        status, _headers, body = _request(gateway, "GET", target)
    assert status == expected_status
    assert b"daemon.sock" not in body
    assert b"Traceback" not in body


def test_down_daemon_maps_to_safe_connect_error(tmp_path: Path) -> None:
    gateway = serve_versioned_http_gateway(
        tmp_path / "missing.sock",
        config=GatewayConfig(auth=GatewayAuthConfig({"operator": "operational"})),
    )
    gateway_thread = threading.Thread(target=gateway.serve_forever, daemon=True)
    gateway_thread.start()
    try:
        status, _headers, body = _request(gateway, "GET", "/v1/current")
    finally:
        gateway.shutdown()
        gateway.server_close()
        gateway_thread.join(timeout=2.0)
    assert status == 502
    assert b"missing.sock" not in body


@pytest.mark.parametrize(
    ("code", "status"),
    [
        ("bad_request", 400),
        ("unknown_op", 502),
        ("unknown_field", 400),
        ("not_found", 404),
        ("invalid_type", 400),
        ("non_finite", 400),
        ("out_of_range", 400),
        ("malformed_cursor", 400),
        ("oversized_request", 502),
        ("oversized_response", 502),
        ("request_timeout", 503),
        ("unavailable", 503),
        ("server_busy", 503),
        ("denied", 403),
        ("protocol_version", 502),
        ("internal", 502),
    ],
)
def test_daemon_error_status_mapping_is_deterministic(code: str, status: int) -> None:
    assert _daemon_error_status(code).value == status
