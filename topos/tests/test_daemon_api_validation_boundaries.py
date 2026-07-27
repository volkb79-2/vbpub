"""P198 — daemon API request validation and typed-error boundaries.

These tests exercise the public envelope/wire contracts at boundaries that
cannot be represented by ordinary successful client requests: opaque id
limits, unavailable entity reads, and malformed legacy input.
"""

from __future__ import annotations

import json
import socket
import threading
from pathlib import Path

import pytest

from conftest import fixture_frame
from topos.daemon.api import AuditLog, DaemonApi, ErrorCode, PeerCredentials, serve_versioned_unix_socket
from topos.daemon.broker import FrameBroker


def _serve(path: Path, broker: FrameBroker, api: DaemonApi):
    server = serve_versioned_unix_socket(path, broker, api)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def _one_wire_response(path: Path, payload: bytes) -> dict[str, object]:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(5.0)
        sock.connect(str(path))
        sock.sendall(payload)
        sock.shutdown(socket.SHUT_WR)
        data = b""
        while chunk := sock.recv(65536):
            data += chunk
    lines = data.decode("utf-8").splitlines()
    assert len(lines) == 1
    return json.loads(lines[0])


@pytest.mark.parametrize(
    ("request_value", "expected_code"),
    [
        (["not", "an", "object"], ErrorCode.BAD_REQUEST),
        ({"id": "x" * 257, "op": "hello", "v": 1}, ErrorCode.BAD_REQUEST),
    ],
)
def test_invalid_request_shape_or_oversized_id_has_typed_error_and_audit(
    request_value: object, expected_code: ErrorCode
) -> None:
    broker = FrameBroker([fixture_frame()])
    audit = AuditLog()
    peer = PeerCredentials(pid=41, uid=42, gid=43)
    api = DaemonApi(broker, audit_log=audit)

    response = api.handle(request_value, peer)

    assert response == {
        "id": None,
        "ok": False,
        "error": {"code": expected_code.value, "message": "request must be an object"}
        if not isinstance(request_value, dict)
        else {"code": expected_code.value, "message": "id must be a non-empty string"},
    }
    (record,) = audit.snapshot()
    assert record.peer == peer
    assert record.op == ""
    assert record.allowed is False
    assert record.error_code == expected_code.value


def test_entity_read_without_a_current_frame_is_typed_unavailable() -> None:
    """The entity endpoint preserves the envelope error contract on broker failure."""
    broker = FrameBroker([])
    api = DaemonApi(broker)

    response = api.handle({"id": "entity", "op": "entity", "v": 1, "key": ""}, None)

    assert response["id"] == "entity"
    assert response["ok"] is False
    assert response["error"]["code"] == ErrorCode.UNAVAILABLE.value
    assert response["error"]["message"] == "frame source exhausted before publishing a frame"


@pytest.mark.parametrize("payload", [b"[\"not-an-object\"]\n", b"\xff\n"])
def test_malformed_or_non_object_wire_request_returns_one_safe_legacy_error(
    tmp_path: Path, payload: bytes
) -> None:
    """Unparseable/non-object pre-envelope input must answer, never escape or hang."""
    broker = FrameBroker([fixture_frame()])
    path = tmp_path / "daemon.sock"
    server, thread = _serve(path, broker, DaemonApi(broker))
    try:
        response = _one_wire_response(path, payload)
        assert response["type"] == "error"
        if payload.startswith(b"["):
            assert response["error"] == "request failed"
        else:
            assert str(response["error"]).startswith("malformed request:")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)
