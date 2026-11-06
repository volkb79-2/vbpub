"""P105 behavioral coverage for daemon client status."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from topos.daemon.client import DaemonClientError
from topos.daemon.status import (
    DaemonStatusReport,
    ProtocolStatus,
    SocketNodeStatus,
    _check_protocol,
    build_daemon_status,
)


def test_protocol_status_to_jsonable_includes_all_optional_fields() -> None:
    status = ProtocolStatus(ok=True, message="current", schema_version=3, frame_ts=100.25, entity_count=5)
    assert status.to_jsonable() == {
        "ok": True,
        "message": "current",
        "schema_version": 3,
        "frame_ts": 100.25,
        "entity_count": 5,
    }


def test_protocol_status_to_jsonable_omits_optional_fields() -> None:
    assert ProtocolStatus(ok=False, message="unavailable").to_jsonable() == {
        "ok": False,
        "message": "unavailable",
    }


def test_socket_node_status_serializes() -> None:
    assert SocketNodeStatus(True, "mounted socket is current").to_jsonable() == {
        "ok": True,
        "message": "mounted socket is current",
    }


def test_status_report_json_and_text_combine_socket_and_protocol() -> None:
    report = DaemonStatusReport(
        socket_path=Path("/run/example.sock"),
        group_name="operators",
        socket_node=SocketNodeStatus(True, "node ok"),
        protocol=ProtocolStatus(ok=True, message="hello ok"),
    )
    assert report.ok is True
    assert report.to_jsonable() == {
        "ok": True,
        "socket": "/run/example.sock",
        "group": "operators",
        "socket_node": {"ok": True, "message": "node ok"},
        "protocol": {"ok": True, "message": "hello ok"},
    }
    assert "--- Socket node ---" in report.to_text()
    assert "Overall: OK" in report.to_text()


def test_status_report_is_degraded_when_either_check_fails() -> None:
    report = DaemonStatusReport(
        socket_path=Path("/run/example.sock"),
        group_name="operators",
        socket_node=SocketNodeStatus(False, "not a socket"),
        protocol=ProtocolStatus(ok=True, message="hello ok", schema_version=1, frame_ts=3.0, entity_count=2),
    )
    assert report.ok is False
    assert "schema version: 1" in report.to_text()
    assert "frame timestamp: 3.0" in report.to_text()
    assert "entities: 2" in report.to_text()
    assert "Overall: DEGRADED" in report.to_text()


def test_check_protocol_returns_exact_base_client_error() -> None:
    socket_path = Path("/run/example.sock")
    with patch("topos.daemon.status.DaemonClient", side_effect=DaemonClientError("authorization failed")) as client:
        result = _check_protocol(socket_path)
    assert result == ProtocolStatus(ok=False, message="Daemon error: authorization failed")
    client.assert_called_once_with(socket_path)


def test_build_status_combines_node_and_protocol_checks() -> None:
    socket_path = Path("/run/example.sock")
    node = SocketNodeStatus(False, "node metadata differs")
    protocol = ProtocolStatus(ok=True, message="hello ok")
    with (
        patch("topos.daemon.status._check_socket_node", return_value=node) as check_node,
        patch("topos.daemon.status._check_protocol", return_value=protocol) as check_protocol,
    ):
        result = build_daemon_status(socket_path, group_name="operators")
    assert result == DaemonStatusReport(socket_path, "operators", node, protocol)
    check_node.assert_called_once_with(socket_path, "operators")
    check_protocol.assert_called_once_with(socket_path)
