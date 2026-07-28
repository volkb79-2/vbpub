"""Daemon API identity, audit, and closed-envelope boundary contracts."""

from __future__ import annotations

import struct

import pytest

from conftest import fixture_frame
from topos.daemon.api import (
    AuditLog,
    AuditRecord,
    DaemonApi,
    ErrorCode,
    PeerCredentials,
    read_peer_credentials,
)
from topos.daemon.broker import FrameBroker


class _PeerSocket:
    def __init__(self, value: bytes | OSError) -> None:
        self._value = value

    def getsockopt(self, _level: int, _option: int, _size: int) -> bytes:
        if isinstance(self._value, OSError):
            raise self._value
        return self._value


def test_peer_credentials_preserve_partial_identity_and_anonymous_audit_shape() -> None:
    """Audit serialization must omit unavailable fields rather than inventing IDs."""
    anonymous = PeerCredentials(pid=None, uid=None, gid=None)
    partial = PeerCredentials(pid=None, uid=1001, gid=None)

    assert anonymous.anonymous is True
    assert anonymous.to_audit() == {}
    assert partial.anonymous is False
    assert partial.to_audit() == {"uid": 1001}


def test_read_peer_credentials_decodes_linux_shape_and_serves_os_errors_anonymously() -> None:
    packed = struct.pack("iII", 77, 1001, 1002)

    assert read_peer_credentials(_PeerSocket(packed)) == PeerCredentials(77, 1001, 1002)  # type: ignore[arg-type]
    assert read_peer_credentials(_PeerSocket(OSError("peer vanished"))) is None  # type: ignore[arg-type]


@pytest.mark.parametrize("capacity", [0, -1, True, 1.5, "1"])
def test_audit_log_rejects_invalid_capacity(capacity: object) -> None:
    with pytest.raises(ValueError, match="positive integer"):
        AuditLog(capacity=capacity)  # type: ignore[arg-type]


def test_audit_log_evicts_oldest_record_and_clear_removes_retained_records() -> None:
    audit = AuditLog(capacity=2)
    entries = [
        AuditRecord(peer=None, op="hello", allowed=True),
        AuditRecord(peer=None, op="current", allowed=False, error_code="unavailable"),
        AuditRecord(peer=None, op="history", allowed=True),
    ]

    for entry in entries:
        audit.record(entry)

    assert audit.snapshot() == tuple(entries[1:])
    audit.clear()
    assert audit.snapshot() == ()


@pytest.mark.parametrize(
    "envelope",
    [
        {"id": "history-extra", "op": "history", "v": 1, "limit": 1, "surplus": "x"},
        {"id": "entity-extra", "op": "entity", "v": 1, "key": "", "surplus": "x"},
    ],
)
def test_closed_envelopes_reject_extra_fields_before_broker_dispatch(envelope: dict[str, object]) -> None:
    """Unknown inputs are typed validation failures, never implicit broker work."""
    broker = FrameBroker([fixture_frame()])
    api = DaemonApi(broker)

    response = api.handle(envelope, None)

    assert response["id"] == envelope["id"]
    assert response["ok"] is False
    assert response["error"] == {
        "code": ErrorCode.UNKNOWN_FIELD.value,
        "message": "unknown field(s): ['surplus']",
    }
