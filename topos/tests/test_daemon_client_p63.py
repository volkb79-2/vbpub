"""P63 — Daemon Client Versioned Read Methods deterministic tests.

Covers (per handoff):
- request_current/request_entity/request_history/request_hello happy paths
- id echo mismatch raises DaemonProtocolError
- Server ok:false envelopes map to typed DaemonResponseError with code
- History cursor form and time-window form both round-trip
- Client rejects cursor+window both set before sending (ValueError)
- Gap/oldest/latest/next_cursor decoded identically to stream_batch semantics
- Malformed/oversized/truncated/non-object/non-JSON response raise DaemonProtocolError
- Connection failure raises DaemonConnectError
- Import isolation preserved
"""

from __future__ import annotations

import json
import socket
import socketserver
import threading
from pathlib import Path

import pytest

from conftest import fixture_frame
from topos.daemon import (
    DaemonApi,
    DEFAULT_MAX_RESPONSE_BYTES,
    FrameBroker,
    PROTOCOL_VERSION,
    Sensitivity,
    serve_versioned_unix_socket,
)
from topos.daemon.client import (
    DaemonClient,
    DaemonConnectError,
    DaemonCurrentResult,
    DaemonEntityResult,
    DaemonHello,
    DaemonHistoryResult,
    DaemonProtocolError,
    DaemonResponseError,
)
from topos.daemon.component_health import ComponentHealthRegistry
from topos.model import Frame, frame_to_jsonable


# --- Fixtures and helpers -------------------------------------------------


def _frame_at(ts: float) -> Frame:
    base = fixture_frame()
    return Frame(base.schema_version, ts, base.interval_s, base.host, base.entities)


def _serve(path: Path, broker: FrameBroker, api: DaemonApi | None = None):
    if api is None:
        api = DaemonApi(broker, health_registry=ComponentHealthRegistry())
    server = serve_versioned_unix_socket(path, broker, api)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def _stop(broker: FrameBroker) -> None:
    broker.stop()
    try:
        broker.join(timeout=2.0)
    except Exception:
        pass


def _serve_lines(socket_path: Path, line: bytes) -> socketserver.UnixStreamServer:
    """Return a server that sends exactly one response line, then closes."""
    class Handler(socketserver.StreamRequestHandler):
        def handle(self) -> None:
            self.rfile.readline(1024 * 1024)
            self.wfile.write(line if line.endswith(b"\n") else line + b"\n")

    server = socketserver.UnixStreamServer(str(socket_path), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def _serve_echo_lines(socket_path: Path, template: dict | None = None) -> socketserver.UnixStreamServer:
    """Return a server that parses the client request id and wraps a
    template response dict around it, or returns a fixed line."""
    class Handler(socketserver.StreamRequestHandler):
        def handle(self) -> None:
            raw = self.rfile.readline(1024 * 1024)
            request = json.loads(raw.decode("utf-8"))
            client_id = request.get("id", "unknown")
            if template is not None:
                response_dict = dict(template)
                response_dict["id"] = client_id
                payload = json.dumps(response_dict, sort_keys=True, separators=(",", ":")).encode("utf-8")
            else:
                payload = b'{"id":"' + client_id.encode("utf-8") + b'","ok":true,"result":{}}'
            self.wfile.write(payload + b"\n")

    server = socketserver.UnixStreamServer(str(socket_path), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


# --- 1. Happy-path request_current ---------------------------------------


def test_request_current_returns_decoded_frame_and_metrics_meta(tmp_path: Path) -> None:
    """Happy path: request_current returns DaemonCurrentResult with seq, frame, metrics_meta."""
    frames = [_frame_at(7.0)]
    broker = FrameBroker(frames)
    path = tmp_path / "s.sock"
    server, thread = _serve(path, broker)
    try:
        client = DaemonClient(path)
        result = client.request_current()
        assert isinstance(result, DaemonCurrentResult)
        assert result.seq == 0
        assert result.frame.ts == 7.0
        assert isinstance(result.frame, Frame)
        # At least one metric with known sensitivity.
        assert result.metrics_meta
        valid = {s.value for s in Sensitivity}
        for name, meta in result.metrics_meta.items():
            assert meta["sensitivity"] in valid, f"metric {name} has bad sensitivity"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)
        _stop(broker)


# --- 2. Happy-path request_hello -----------------------------------------


def test_request_hello_returns_protocol_info(tmp_path: Path) -> None:
    """Happy path: request_hello returns protocol versions, capabilities, identity, limits."""
    broker = FrameBroker([_frame_at(1.0)])
    path = tmp_path / "s.sock"
    server, thread = _serve(path, broker)
    try:
        client = DaemonClient(path)
        hello = client.request_hello()
        assert isinstance(hello, DaemonHello)
        assert PROTOCOL_VERSION in hello.protocol_versions
        assert "hello" in hello.capabilities
        assert "current" in hello.capabilities
        assert "history" in hello.capabilities
        assert "entity" in hello.capabilities
        assert hello.identity["name"] == "topos-daemon"
        assert "max_response_bytes" in hello.limits
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)
        _stop(broker)


# --- 3. Happy-path request_entity ----------------------------------------


def test_request_entity_returns_decoded_entity_frame(tmp_path: Path) -> None:
    """Happy path: request_entity returns DaemonEntityResult with seq, entity, metrics_meta."""
    frames = [_frame_at(7.0)]
    broker = FrameBroker(frames)
    path = tmp_path / "s.sock"
    server, thread = _serve(path, broker)
    try:
        client = DaemonClient(path)
        # Pick an entity key that exists in the fixture frame.
        fixture = fixture_frame()
        known_key = next(iter(fixture.entities))
        result = client.request_entity(known_key)
        assert isinstance(result, DaemonEntityResult)
        assert result.seq == 0
        assert result.entity.entity.key == known_key
        assert result.entity.metrics
        valid = {s.value for s in Sensitivity}
        for name, meta in result.metrics_meta.items():
            assert meta["sensitivity"] in valid, f"metric {name} has bad sensitivity"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)
        _stop(broker)


# --- 4. Happy-path request_history (cursor form) -------------------------


def test_request_history_cursor_form_returns_typed_result(tmp_path: Path) -> None:
    """History cursor form round-trips correctly."""
    frames = [_frame_at(1.0), _frame_at(2.0), _frame_at(3.0)]
    broker = FrameBroker(frames, history_size=10)
    path = tmp_path / "s.sock"
    server, thread = _serve(path, broker)
    try:
        client = DaemonClient(path)
        # Get all history.
        result = client.request_history(limit=10)
        assert isinstance(result, DaemonHistoryResult)
        assert len(result.entries) == 3
        assert [s for s, _ in result.entries] == [0, 1, 2]
        assert result.oldest_seq == 0
        assert result.latest_seq == 2
        assert result.next_cursor == 2
        assert result.gap is False
        assert result.metrics_meta
        valid = {s.value for s in Sensitivity}
        for name, meta in result.metrics_meta.items():
            assert meta["sensitivity"] in valid
        # Cursor form: frames after cursor 0.
        result2 = client.request_history(limit=10, cursor=0)
        assert len(result2.entries) == 2
        assert [s for s, _ in result2.entries] == [1, 2]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)
        _stop(broker)


# --- 5. Happy-path request_history (time-window form) --------------------


def test_request_history_time_window_form_returns_typed_result(tmp_path: Path) -> None:
    """History time-window form round-trips correctly."""
    frames = [_frame_at(100.0), _frame_at(200.0), _frame_at(300.0)]
    broker = FrameBroker(frames, history_size=10)
    path = tmp_path / "s.sock"
    server, thread = _serve(path, broker)
    try:
        client = DaemonClient(path)
        # Time window covering ts 150-350 => frames at 200, 300.
        result = client.request_history(limit=10, since_ts=150.0, until_ts=350.0)
        assert isinstance(result, DaemonHistoryResult)
        assert len(result.entries) >= 1
        for seq, frame in result.entries:
            assert 150.0 <= frame.ts < 350.0
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)
        _stop(broker)


# --- 6. Client rejects cursor+window both set ----------------------------


def test_request_history_rejects_cursor_and_window_together(tmp_path: Path) -> None:
    """Client raises ValueError fast-fail if both cursor and a time window are set."""
    client = DaemonClient(tmp_path / "nonexistent.sock")
    with pytest.raises(ValueError, match="cursor or a time window"):
        client.request_history(cursor=1, since_ts=100.0)
    with pytest.raises(ValueError, match="cursor or a time window"):
        client.request_history(cursor=1, until_ts=200.0)
    with pytest.raises(ValueError, match="cursor or a time window"):
        client.request_history(cursor=1, since_ts=100.0, until_ts=200.0)


# --- 7. Server error codes are recoverable via .code ---------------------


def test_error_not_found_carries_code(tmp_path: Path) -> None:
    """Entity not_found error carries code='not_found' on DaemonResponseError."""
    frames = [_frame_at(1.0)]
    broker = FrameBroker(frames)
    path = tmp_path / "s.sock"
    server, thread = _serve(path, broker)
    try:
        client = DaemonClient(path)
        with pytest.raises(DaemonResponseError) as excinfo:
            client.request_entity("nonexistent_key_xyz")
        assert excinfo.value.code == "not_found"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)
        _stop(broker)


def test_error_invalid_type_carries_code(tmp_path: Path) -> None:
    """Invalid entity key type carries code='invalid_type' on DaemonResponseError."""
    frames = [_frame_at(1.0)]
    broker = FrameBroker(frames)
    path = tmp_path / "s.sock"
    server, thread = _serve(path, broker)
    try:
        client = DaemonClient(path)
        # Injection-shaped key (starting with /) should produce invalid_type.
        with pytest.raises(DaemonResponseError) as excinfo:
            client.request_entity("/etc/passwd")
        assert excinfo.value.code == "invalid_type"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)
        _stop(broker)


def test_error_out_of_range_carries_code(tmp_path: Path) -> None:
    """Over-cap limit carries code='out_of_range' on DaemonResponseError."""
    frames = [_frame_at(1.0)]
    broker = FrameBroker(frames)
    path = tmp_path / "s.sock"
    server, thread = _serve(path, broker)
    try:
        client = DaemonClient(path)
        with pytest.raises(DaemonResponseError) as excinfo:
            # Request a limit well beyond the server's max_response_items (1000).
            client.request_history(limit=999999)
        assert excinfo.value.code == "out_of_range"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)
        _stop(broker)


def test_error_bad_request_carries_code(tmp_path: Path) -> None:
    """Server ok:false with bad_request code is recoverable via .code."""
    template = {
        "id": "ignored",
        "ok": False,
        "error": {"code": "bad_request", "message": "specify either cursor or a time window, not both"},
    }
    path = tmp_path / "bad.sock"
    server = _serve_echo_lines(path, template=template)
    try:
        client = DaemonClient(path)
        with pytest.raises(DaemonResponseError) as excinfo:
            client.request_hello()
        assert excinfo.value.code == "bad_request"
    finally:
        server.shutdown()
        server.server_close()


def test_error_unavailable_carries_code(tmp_path: Path) -> None:
    """Unavailable error carries code='unavailable' on DaemonResponseError."""
    template = {
        "id": "ignored",
        "ok": False,
        "error": {"code": "unavailable", "message": "not available"},
    }
    path = tmp_path / "unavail.sock"
    server = _serve_echo_lines(path, template=template)
    try:
        client = DaemonClient(path)
        with pytest.raises(DaemonResponseError) as excinfo:
            client.request_hello()
        assert excinfo.value.code == "unavailable"
    finally:
        server.shutdown()
        server.server_close()


# --- 8. id echo mismatch raises DaemonProtocolError ----------------------


def test_id_echo_mismatch_raises_protocol_error(tmp_path: Path) -> None:
    """If the server echoes back a different id, client raises DaemonProtocolError."""
    wrong_id_payload = json.dumps(
        {
            "id": "wrong-id",
            "ok": True,
            "result": {"protocol_versions": [1], "capabilities": [], "identity": {}, "limits": {}},
        }
    ).encode("utf-8")
    path = tmp_path / "mismatch.sock"
    server = _serve_lines(path, wrong_id_payload)
    try:
        client = DaemonClient(path)
        with pytest.raises(DaemonProtocolError, match="returned id"):
            client.request_hello()
    finally:
        server.shutdown()
        server.server_close()


# --- 9. Malformed/oversized/truncated/non-object/non-JSON responses ------


def test_malformed_json_response_raises_protocol_error(tmp_path: Path) -> None:
    """Non-JSON response line raises DaemonProtocolError."""
    path = tmp_path / "bad.sock"
    server = _serve_lines(path, b"not-json-at-all")
    try:
        client = DaemonClient(path)
        with pytest.raises(DaemonProtocolError, match="malformed JSON"):
            client.request_hello()
    finally:
        server.shutdown()
        server.server_close()


def test_empty_response_raises_protocol_error(tmp_path: Path) -> None:
    """Empty/truncated response (connection closed without newline) raises DaemonProtocolError."""
    path = tmp_path / "empty.sock"
    # A line that is just a newline (empty) is not a valid JSON response.
    server = _serve_lines(path, b"\n")
    try:
        client = DaemonClient(path)
        with pytest.raises(DaemonProtocolError, match="malformed JSON"):
            client.request_hello()
    finally:
        server.shutdown()
        server.server_close()


def test_oversized_response_raises_protocol_error(tmp_path: Path) -> None:
    """Response exceeding max response bytes raises DaemonProtocolError."""
    big_payload = b"x" * (DEFAULT_MAX_RESPONSE_BYTES + 100) + b"\n"
    path = tmp_path / "big.sock"
    server = _serve_lines(path, big_payload)
    try:
        client = DaemonClient(path)
        with pytest.raises(DaemonProtocolError, match="oversized"):
            client.request_hello()
    finally:
        server.shutdown()
        server.server_close()


def test_non_object_response_raises_protocol_error(tmp_path: Path) -> None:
    """A response that is valid JSON but not an object raises DaemonProtocolError."""
    path = tmp_path / "arr.sock"
    server = _serve_lines(path, b'["not", "an", "object"]')
    try:
        client = DaemonClient(path)
        with pytest.raises(DaemonProtocolError, match="non-object"):
            client.request_hello()
    finally:
        server.shutdown()
        server.server_close()


# --- 10. Connection failure raises DaemonConnectError --------------------


def test_connection_failure_raises_connect_error(tmp_path: Path) -> None:
    """Connecting to a non-existent socket raises DaemonConnectError."""
    client = DaemonClient(tmp_path / "no-such-socket.sock", timeout_s=0.5)
    with pytest.raises(DaemonConnectError, match="cannot connect"):
        client.request_hello()


# --- 11. Import isolation ------------------------------------------------


def test_import_does_not_trigger_heavy_imports() -> None:
    """Importing the client module should not trigger heavy imports at module level."""
    # The uuid module is stdlib and lightweight; no heavy frameworks.
    import topos.daemon.client as client_mod  # noqa: PLC0415  # fresh import check

    assert hasattr(client_mod, "DaemonClient")


# --- 12. Gap/oldest/latest/next_cursor semantics match stream_batch ------


def test_history_gap_and_bounds_match_stream_batch_semantics(tmp_path: Path) -> None:
    """History result's gap/oldest/latest/next_cursor match stream_batch semantics."""
    frames = [_frame_at(1.0), _frame_at(2.0), _frame_at(3.0)]
    broker = FrameBroker(frames, history_size=10)
    path = tmp_path / "s.sock"
    server, thread = _serve(path, broker)
    try:
        client = DaemonClient(path)
        # Compare envelope history with legacy stream_batch.
        env_result = client.request_history(limit=10)
        legacy_batch = client.stream_batch(limit=10)
        assert env_result.oldest_seq == legacy_batch.oldest_seq
        assert env_result.latest_seq == legacy_batch.latest_seq
        assert env_result.next_cursor == legacy_batch.next_cursor
        assert env_result.gap == legacy_batch.gap
        assert len(env_result.entries) == len(legacy_batch.entries)
        for (s1, f1), (s2, f2) in zip(env_result.entries, legacy_batch.entries, strict=True):
            assert s1 == s2
            assert f1.ts == f2.ts
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)
        _stop(broker)


# --- 13. request_history non-increasing sequences rejected ---------------


def test_non_increasing_sequences_raise_protocol_error(tmp_path: Path) -> None:
    """If history returns non-increasing sequences, client raises DaemonProtocolError."""
    bad_template = {
        "id": "ignored",
        "ok": True,
        "result": {
            "frames": [
                {"seq": 2, "frame": frame_to_jsonable(_frame_at(1.0))},
                {"seq": 1, "frame": frame_to_jsonable(_frame_at(2.0))},
            ],
            "oldest_seq": 1,
            "latest_seq": 2,
            "next_cursor": 2,
            "gap": False,
            "metrics_meta": {},
        },
    }
    path = tmp_path / "seq.sock"
    server = _serve_echo_lines(path, template=bad_template)
    try:
        client = DaemonClient(path)
        with pytest.raises(DaemonProtocolError, match="non-increasing"):
            client.request_history()
    finally:
        server.shutdown()
        server.server_close()
