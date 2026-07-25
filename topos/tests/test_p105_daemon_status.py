"""P105 exact behavioral coverage for daemon status reporting."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from topos.daemon.client import DaemonClientError
from topos.daemon.status import (
    DaemonStatusReport,
    ProtocolStatus,
    _check_protocol,
    build_daemon_status,
)


def test_protocol_status_to_jsonable_includes_all_optional_fields() -> None:
    status = ProtocolStatus(
        ok=True,
        message="current",
        schema_version=3,
        frame_ts=100.25,
        entity_count=5,
    )

    assert status.to_jsonable() == {
        "ok": True,
        "message": "current",
        "schema_version": 3,
        "frame_ts": 100.25,
        "entity_count": 5,
    }


def test_protocol_status_to_jsonable_omits_all_optional_fields() -> None:
    assert ProtocolStatus(ok=False, message="unavailable").to_jsonable() == {
        "ok": False,
        "message": "unavailable",
    }


def test_status_report_to_jsonable_without_preflight_is_complete() -> None:
    report = DaemonStatusReport(
        socket_path=Path("/run/example.sock"),
        group_name="operators",
        preflight=None,
        protocol=ProtocolStatus(ok=True, message="current"),
    )

    assert report.to_jsonable() == {
        "ok": False,
        "socket": "/run/example.sock",
        "group": "operators",
        "protocol": {"ok": True, "message": "current"},
    }


def test_status_report_to_text_without_preflight_is_complete() -> None:
    report = DaemonStatusReport(
        socket_path=Path("/run/example.sock"),
        group_name="operators",
        preflight=None,
        protocol=ProtocolStatus(ok=True, message="current"),
    )

    assert report.to_text() == "\n".join(
        (
            "topos daemon status: /run/example.sock",
            "Group: operators",
            "",
            "--- Preflight ---",
            "(preflight not run)",
            "",
            "",
            "--- Protocol ---",
            "  current",
            "",
            "Overall: DEGRADED",
        )
    )


def test_check_protocol_returns_exact_base_client_error() -> None:
    socket_path = Path("/run/example.sock")
    with patch(
        "topos.daemon.status.DaemonClient",
        side_effect=DaemonClientError("authorization failed"),
    ) as client:
        result = _check_protocol(socket_path)

    assert result == ProtocolStatus(
        ok=False,
        message="Daemon error: authorization failed",
    )
    client.assert_called_once_with(socket_path)


def test_build_status_swallowing_preflight_error_is_exact() -> None:
    socket_path = Path("/run/example.sock")
    protocol = ProtocolStatus(
        ok=False,
        message="Daemon error: authorization failed",
    )
    with (
        patch(
            "topos.daemon.status.preflight_daemon_deployment",
            side_effect=OSError("identity lookup failed"),
        ) as preflight,
        patch(
            "topos.daemon.status._check_protocol",
            return_value=protocol,
        ) as check_protocol,
    ):
        result = build_daemon_status(socket_path, group_name="operators")

    assert result == DaemonStatusReport(
        socket_path=socket_path,
        group_name="operators",
        preflight=None,
        protocol=protocol,
    )
    preflight.assert_called_once_with(
        socket_path,
        group_name="operators",
        connect_timeout_s=1.0,
    )
    check_protocol.assert_called_once_with(socket_path)
