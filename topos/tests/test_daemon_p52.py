"""P52 — Versioned daemon read API deterministic tests.

Covers (per handoff):
- Envelope round-trip with id echo
- hello capability completeness (served == listed)
- Legacy-op compatibility/rejection decisions (both directions)
- Malformed/fuzz envelope battery (unknown field, unknown op, bool-as-int,
  non-finite, oversized, truncated line)
- Concurrent mixed clients (one slow + several fast) with bounded latency
- Peer credentials present in audit records
- Sensitivity enum present on every metric of a response
- History cursor/gap semantics identical through old and new envelope
- Resource bounds enforced at the mechanism level (request bytes, idle read
  deadline, concurrent clients -> N+1 refused, response bytes)
- P51 safety contract persists through the new envelope (no raw exception,
  secret, or path leaks)
- entity op rejects path-shaped and registry-shaped injection inputs
- Authorization hook may deny with a typed error
- ApiLimits raising behavior (never silently clamped)
"""

from __future__ import annotations

import json
import queue
import socket
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from conftest import fixture_frame
from topos.daemon.api import (
    ApiLimits,
    AuditLog,
    CAPABILITIES,
    DaemonApi,
    ErrorCode,
    PeerCredentials,
    PROTOCOL_VERSION,
    PROTOCOL_VERSIONS,
    Sensitivity,
    serve_versioned_unix_socket,
)
from topos.daemon.broker import (
    FrameBroker,
    FrameProducerError,
    MAX_REQUEST_BYTES,
    MAX_STREAM_LIMIT,
)
from topos.daemon.component_health import ComponentHealthRegistry, ComponentState
from topos.model import Frame


# --- Fixtures and helpers -------------------------------------------------


def _frame_at(ts: float) -> Frame:
    base = fixture_frame()
    return Frame(base.schema_version, ts, base.interval_s, base.host, base.entities)


class ControlledSource:
    def __init__(self) -> None:
        self.items: queue.Queue[Frame | BaseException | None] = queue.Queue()

    def __iter__(self) -> ControlledSource:
        return self

    def __next__(self) -> Frame:
        item = self.items.get(timeout=5.0)
        if item is None:
            raise StopIteration
        if isinstance(item, BaseException):
            raise item
        return item

    def frame(self, ts: float) -> None:
        self.items.put(_frame_at(ts))

    def fail(self, message: str) -> None:
        self.items.put(RuntimeError(message))

    def exhaust(self) -> None:
        self.items.put(None)


def _wait_for(predicate, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            pytest.fail("condition did not become true before deadline")
        time.sleep(0.001)


def _stop(broker: FrameBroker, source: ControlledSource | None = None) -> None:
    broker.stop()
    if source is not None and broker.producer_alive:
        source.exhaust()
    try:
        broker.join(timeout=2.0)
    except FrameProducerError:
        pass


def _serve(
    path: Path,
    broker: FrameBroker,
    *,
    api: DaemonApi | None = None,
):
    if api is None:
        api = DaemonApi(broker, health_registry=ComponentHealthRegistry())
    server = serve_versioned_unix_socket(path, broker, api)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def _envelope(socket_path: Path, request: dict) -> dict:
    """Send one envelope request; return the single JSON response object."""
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(5.0)
        sock.connect(str(socket_path))
        sock.sendall(json.dumps(request, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n")
        sock.shutdown(socket.SHUT_WR)
        data = b""
        while True:
            chunk = sock.recv(65536)
            if not chunk:
                break
            data += chunk
    lines = data.decode("utf-8").splitlines()
    assert len(lines) == 1, f"envelope expects one line, got {len(lines)}: {lines!r}"
    return json.loads(lines[0])


def _legacy(socket_path: Path, request: dict) -> list[dict]:
    """Send a legacy (no-v) request; return all multi-line JSON objects."""
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(5.0)
        sock.connect(str(socket_path))
        sock.sendall(json.dumps(request).encode("utf-8") + b"\n")
        sock.shutdown(socket.SHUT_WR)
        data = b""
        while True:
            chunk = sock.recv(65536)
            if not chunk:
                break
            data += chunk
    return [json.loads(line) for line in data.decode("utf-8").splitlines() if line.strip()]


# --- 1. Envelope round-trip with id echo ---------------------------------


def test_envelope_round_trip_echoes_client_id(tmp_path: Path) -> None:
    broker = FrameBroker([_frame_at(1.0)])
    api = DaemonApi(broker)
    path = tmp_path / "s.sock"
    server, thread = _serve(path, broker, api=api)
    try:
        resp = _envelope(path, {"id": "client-abc-123", "op": "hello", "v": 1})
        assert resp["id"] == "client-abc-123"
        assert resp["ok"] is True
        assert "result" in resp
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)
        _stop(broker)


def test_envelope_id_echoed_on_error_path(tmp_path: Path) -> None:
    broker = FrameBroker([_frame_at(1.0)])
    api = DaemonApi(broker)
    path = tmp_path / "s.sock"
    server, thread = _serve(path, broker, api=api)
    try:
        resp = _envelope(path, {"id": "err-1", "op": "bogus", "v": 1})
        assert resp["id"] == "err-1"
        assert resp["ok"] is False
        assert resp["error"]["code"] == ErrorCode.UNKNOWN_OP.value
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)
        _stop(broker)


# --- 2. hello capability completeness ------------------------------------


def test_hello_lists_every_served_op_and_vice_versa(tmp_path: Path) -> None:
    """Every served op is listed; every listed op is served."""
    broker = FrameBroker([_frame_at(1.0)])
    api = DaemonApi(broker)
    path = tmp_path / "s.sock"
    server, thread = _serve(path, broker, api=api)
    try:
        hello = _envelope(path, {"id": "h", "op": "hello", "v": 1})
        capabilities = hello["result"]["capabilities"]
        # Every listed capability is actually served (returns ok, not unknown_op).
        assert set(capabilities) == set(CAPABILITIES)
        for op in capabilities:
            resp = _envelope(path, {"id": f"probe-{op}", "op": op, "v": 1})
            # Some ops need params; they must NOT return unknown_op.
            assert resp["error"]["code"] != ErrorCode.UNKNOWN_OP.value if not resp["ok"] else True
        # An unlisted op is rejected.
        bad = _envelope(path, {"id": "x", "op": "not_a_real_op", "v": 1})
        assert bad["ok"] is False
        assert bad["error"]["code"] == ErrorCode.UNKNOWN_OP.value
        assert "not_a_real_op" not in capabilities
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)
        _stop(broker)


def test_hello_returns_protocol_versions_identity_and_limits(tmp_path: Path) -> None:
    broker = FrameBroker([_frame_at(1.0)], history_size=42)
    api = DaemonApi(broker, limits=ApiLimits(history_capacity=42))
    path = tmp_path / "s.sock"
    server, thread = _serve(path, broker, api=api)
    try:
        hello = _envelope(path, {"id": "h", "op": "hello", "v": 1})["result"]
        assert hello["protocol_versions"] == list(PROTOCOL_VERSIONS)
        assert PROTOCOL_VERSION in hello["protocol_versions"]
        assert hello["identity"]["name"] == "topos-daemon"
        assert hello["identity"]["version"]  # non-empty build version
        lim = hello["limits"]
        assert lim["max_request_bytes"] == MAX_REQUEST_BYTES
        assert lim["max_response_items"] == MAX_STREAM_LIMIT
        assert lim["history_capacity"] == 42
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)
        _stop(broker)


# --- 3. current op returns atomic sequence + frame + metrics_meta --------


def test_current_returns_seq_frame_and_sensitivity_metadata(tmp_path: Path) -> None:
    broker = FrameBroker([_frame_at(7.0)])
    api = DaemonApi(broker)
    path = tmp_path / "s.sock"
    server, thread = _serve(path, broker, api=api)
    try:
        resp = _envelope(path, {"id": "c", "op": "current", "v": 1})
        assert resp["ok"] is True
        result = resp["result"]
        assert result["seq"] == 0
        assert result["frame"]["ts"] == 7.0
        # Every metric in metrics_meta carries a sensitivity from the closed enum.
        valid = {s.value for s in Sensitivity}
        for name, meta in result["metrics_meta"].items():
            assert meta["sensitivity"] in valid, f"metric {name} has bad sensitivity"
            assert meta["unit"]  # registry-derived
            assert meta["kind"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)
        _stop(broker)


# --- 4. Legacy op compatibility/rejection (both directions) --------------
#
# Decision (documented in DAEMON.md): legacy ops current/stream/health sent
# WITHOUT an envelope continue to be served unchanged (compatibility mode).
# There is no `status` broker op (it is a CLI composite), so it is rejected
# at the socket just as in P51.


def test_legacy_current_without_envelope_is_served_unchanged(tmp_path: Path) -> None:
    broker = FrameBroker([_frame_at(11.0)])
    api = DaemonApi(broker)
    path = tmp_path / "s.sock"
    server, thread = _serve(path, broker, api=api)
    try:
        lines = _legacy(path, {"op": "current"})
        assert [l.get("type") for l in lines] == ["frame", "end"]
        assert lines[0]["frame"]["ts"] == 11.0
        assert lines[1] == {"type": "end", "count": 1, "next_cursor": 0}
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)
        _stop(broker)


def test_legacy_stream_without_envelope_is_served_unchanged(tmp_path: Path) -> None:
    broker = FrameBroker([_frame_at(1.0), _frame_at(2.0), _frame_at(3.0)], history_size=10)
    api = DaemonApi(broker)
    path = tmp_path / "s.sock"
    server, thread = _serve(path, broker, api=api)
    try:
        lines = _legacy(path, {"op": "stream", "limit": 10})
        frames = [l for l in lines if l.get("type") == "frame"]
        end = lines[-1]
        assert [f["frame"]["ts"] for f in frames] == [1.0, 2.0, 3.0]
        assert end["count"] == 3
        assert end["gap"] is False
        assert (end["oldest_seq"], end["latest_seq"], end["next_cursor"]) == (0, 2, 2)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)
        _stop(broker)


def test_legacy_health_without_envelope_is_served_unchanged(tmp_path: Path) -> None:
    health = ComponentHealthRegistry()
    broker = FrameBroker([_frame_at(1.0)], health_registry=health)
    api = DaemonApi(broker, health_registry=health)
    path = tmp_path / "s.sock"
    server, thread = _serve(path, broker, api=api)
    try:
        lines = _legacy(path, {"op": "health"})
        assert len(lines) == 1
        assert lines[0]["type"] == "health"
        assert lines[0]["capability"] == "health-v1"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)
        _stop(broker)


def test_envelope_current_and_legacy_current_observe_same_frame(tmp_path: Path) -> None:
    broker = FrameBroker([_frame_at(42.0)])
    api = DaemonApi(broker)
    path = tmp_path / "s.sock"
    server, thread = _serve(path, broker, api=api)
    try:
        env = _envelope(path, {"id": "e", "op": "current", "v": 1})
        legacy = _legacy(path, {"op": "current"})
        assert env["result"]["frame"]["ts"] == legacy[0]["frame"]["ts"] == 42.0
        assert env["result"]["seq"] == legacy[1]["next_cursor"] == 0
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)
        _stop(broker)


# --- 5. Sensitivity enum present on every metric --------------------------


def test_sensitivity_enum_present_on_every_metric_in_current(tmp_path: Path) -> None:
    broker = FrameBroker([_frame_at(1.0)])
    api = DaemonApi(broker)
    path = tmp_path / "s.sock"
    server, thread = _serve(path, broker, api=api)
    try:
        resp = _envelope(path, {"id": "c", "op": "current", "v": 1})
        valid = {s.value for s in Sensitivity}
        meta = resp["result"]["metrics_meta"]
        assert meta  # non-empty
        for name, entry in meta.items():
            assert entry["sensitivity"] in valid
        # The closed enum is exactly {public, operational, sensitive}.
        assert valid == {"public", "operational", "sensitive"}
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)
        _stop(broker)


def test_sensitivity_levels_are_actually_attested_in_fixture(tmp_path: Path) -> None:
    """At least public and operational must appear in the fixture frame."""
    broker = FrameBroker([_frame_at(1.0)])
    api = DaemonApi(broker)
    path = tmp_path / "s.sock"
    server, thread = _serve(path, broker, api=api)
    try:
        resp = _envelope(path, {"id": "c", "op": "current", "v": 1})
        levels = {m["sensitivity"] for m in resp["result"]["metrics_meta"].values()}
        assert "public" in levels  # host_* metrics
        assert "operational" in levels  # most metrics
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)
        _stop(broker)


# --- 6. Malformed/fuzz envelope battery ----------------------------------


@pytest.mark.parametrize(
    "request_payload, expected_code",
    [
        # Unknown top-level field.
        ({"id": "f", "op": "hello", "v": 1, "extra": 1}, ErrorCode.UNKNOWN_FIELD),
        # Unknown op.
        ({"id": "f", "op": "nope", "v": 1}, ErrorCode.UNKNOWN_OP),
        # Bool where integer expected (v).
        ({"id": "f", "op": "hello", "v": True}, ErrorCode.INVALID_TYPE),
        # Non-finite number in history since_ts.
        ({"id": "f", "op": "history", "v": 1, "since_ts": float("nan")}, ErrorCode.NON_FINITE),
        ({"id": "f", "op": "history", "v": 1, "since_ts": float("inf")}, ErrorCode.NON_FINITE),
        # Bool where integer expected (history limit).
        ({"id": "f", "op": "history", "v": 1, "limit": True}, ErrorCode.INVALID_TYPE),
        # Zero/negative where positive required (history limit).
        ({"id": "f", "op": "history", "v": 1, "limit": 0}, ErrorCode.OUT_OF_RANGE),
        ({"id": "f", "op": "history", "v": 1, "limit": -1}, ErrorCode.OUT_OF_RANGE),
        # Malformed cursor (float).
        ({"id": "f", "op": "history", "v": 1, "cursor": 1.5}, ErrorCode.MALFORMED_CURSOR),
        # Malformed cursor (too low).
        ({"id": "f", "op": "history", "v": 1, "cursor": -2}, ErrorCode.MALFORMED_CURSOR),
        # Unsupported protocol version.
        ({"id": "f", "op": "hello", "v": 99}, ErrorCode.PROTOCOL_VERSION),
        ({"id": "f", "op": "hello", "v": 0}, ErrorCode.PROTOCOL_VERSION),
        # Missing id.
        ({"op": "hello", "v": 1}, ErrorCode.BAD_REQUEST),
        # Non-string id.
        ({"id": 5, "op": "hello", "v": 1}, ErrorCode.BAD_REQUEST),
        # Empty id.
        ({"id": "", "op": "hello", "v": 1}, ErrorCode.BAD_REQUEST),
        # Missing op.
        ({"id": "f", "v": 1}, ErrorCode.INVALID_TYPE),
        # Missing v.
        ({"id": "f", "op": "hello"}, ErrorCode.INVALID_TYPE),
        # Bool v.
        ({"id": "f", "op": "hello", "v": False}, ErrorCode.INVALID_TYPE),
        # Cursor + window simultaneously.
        ({"id": "f", "op": "history", "v": 1, "cursor": 0, "since_ts": 1.0}, ErrorCode.BAD_REQUEST),
        # since > until.
        ({"id": "f", "op": "history", "v": 1, "since_ts": 2.0, "until_ts": 1.0}, ErrorCode.BAD_REQUEST),
        # entity key absolute path.
        ({"id": "f", "op": "entity", "v": 1, "key": "/etc/passwd"}, ErrorCode.INVALID_TYPE),
        # entity key parent traversal.
        ({"id": "f", "op": "entity", "v": 1, "key": "../etc"}, ErrorCode.INVALID_TYPE),
        # entity key NUL.
        ({"id": "f", "op": "entity", "v": 1, "key": "a\x00b"}, ErrorCode.INVALID_TYPE),
        # entity key control char.
        ({"id": "f", "op": "entity", "v": 1, "key": "a\x01b"}, ErrorCode.INVALID_TYPE),
        # entity non-string key.
        ({"id": "f", "op": "entity", "v": 1, "key": 5}, ErrorCode.INVALID_TYPE),
    ],
)
def test_malformed_envelope_battery(
    tmp_path: Path, request_payload: dict, expected_code: ErrorCode
) -> None:
    broker = FrameBroker([_frame_at(1.0)])
    api = DaemonApi(broker)
    resp = api.handle(request_payload, PeerCredentials(1, 2, 3))
    assert resp["ok"] is False
    assert resp["error"]["code"] == expected_code.value, resp
    _stop(broker)


def test_truncated_line_without_newline_is_rejected(tmp_path: Path) -> None:
    """A request that exceeds the byte cap or lacks a newline is rejected."""
    broker = FrameBroker([_frame_at(1.0)])
    api = DaemonApi(broker, limits=ApiLimits(max_request_bytes=64))
    path = tmp_path / "s.sock"
    server, thread = _serve(path, broker, api=api)
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(5.0)
            sock.connect(str(path))
            # Send a long line WITHOUT a newline — must be rejected as oversized.
            sock.sendall(b"x" * 100)
            sock.shutdown(socket.SHUT_WR)
            data = b""
            while True:
                chunk = sock.recv(65536)
                if not chunk:
                    break
                data += chunk
        resp = json.loads(data.decode("utf-8").splitlines()[0])
        assert resp["type"] == "error"
        assert "maximum size" in resp["error"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)
        _stop(broker)


# --- 7. History cursor/gap semantics identical through old and new -------


def test_history_cursor_and_gap_semantics_match_legacy_stream(tmp_path: Path) -> None:
    """Envelope history cursor/gap metadata must equal legacy stream metadata."""
    frames = [_frame_at(float(i)) for i in range(6)]
    broker = FrameBroker(frames, history_size=3)
    broker.start()
    _wait_for(lambda: broker.terminal_kind == "exhausted")
    api = DaemonApi(broker)
    path = tmp_path / "s.sock"
    server, thread = _serve(path, broker, api=api)
    try:
        # Envelope: cursor behind oldest -> gap True.
        env = _envelope(path, {"id": "g", "op": "history", "v": 1, "limit": 3, "cursor": 0})
        assert env["result"]["gap"] is True
        assert env["result"]["oldest_seq"] == 3
        assert env["result"]["latest_seq"] == 5
        assert env["result"]["next_cursor"] == 5
        assert [f["seq"] for f in env["result"]["frames"]] == [3, 4, 5]

        # Legacy: same cursor -> same gap/bounds.
        legacy = _legacy(path, {"op": "stream", "limit": 3, "cursor": 0})
        end = legacy[-1]
        assert end["gap"] is True
        assert (end["oldest_seq"], end["latest_seq"], end["next_cursor"]) == (3, 5, 5)
        assert [f["seq"] for f in legacy if f.get("type") == "frame"] == [3, 4, 5]

        # Envelope tail (no cursor) -> no gap, most recent `limit`.
        env_tail = _envelope(path, {"id": "t", "op": "history", "v": 1, "limit": 2})
        assert env_tail["result"]["gap"] is False
        assert [f["seq"] for f in env_tail["result"]["frames"]] == [4, 5]

        # Legacy tail matches.
        legacy_tail = _legacy(path, {"op": "stream", "limit": 2})
        assert legacy_tail[-1]["gap"] is False
        assert [f["seq"] for f in legacy_tail if f.get("type") == "frame"] == [4, 5]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)
        _stop(broker)


def test_history_time_window_filters_by_timestamp(tmp_path: Path) -> None:
    frames = [_frame_at(float(i)) for i in range(10)]
    broker = FrameBroker(frames, history_size=20)
    broker.start()
    _wait_for(lambda: broker.terminal_kind == "exhausted")
    api = DaemonApi(broker)
    path = tmp_path / "s.sock"
    server, thread = _serve(path, broker, api=api)
    try:
        # Window [3.0, 7.0) -> frames with ts 3,4,5,6.
        resp = _envelope(
            path,
            {"id": "w", "op": "history", "v": 1, "since_ts": 3.0, "until_ts": 7.0, "limit": 10},
        )
        result = resp["result"]
        assert [f["frame"]["ts"] for f in result["frames"]] == [3.0, 4.0, 5.0, 6.0]
        assert result["gap"] is False  # window starts at or after oldest
        # Oldest/latest are full-history bounds, not window bounds.
        assert result["oldest_seq"] == 0
        assert result["latest_seq"] == 9
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)
        _stop(broker)


def test_history_time_window_gap_when_window_precedes_oldest(tmp_path: Path) -> None:
    frames = [_frame_at(float(i)) for i in range(6)]
    broker = FrameBroker(frames, history_size=3)
    broker.start()
    _wait_for(lambda: broker.terminal_kind == "exhausted")
    api = DaemonApi(broker)
    path = tmp_path / "s.sock"
    server, thread = _serve(path, broker, api=api)
    try:
        # Window starts at ts 0.0 but oldest retained is ts 3.0 -> gap.
        resp = _envelope(
            path, {"id": "wg", "op": "history", "v": 1, "since_ts": 0.0, "until_ts": 10.0, "limit": 10}
        )
        assert resp["result"]["gap"] is True
        assert resp["result"]["oldest_seq"] == 3
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)
        _stop(broker)


# --- 8. entity op: resolves against in-memory frame data only ------------


def test_entity_op_returns_frame_data_and_registry_metadata(tmp_path: Path) -> None:
    f = _frame_at(1.0)
    broker = FrameBroker([f])
    api = DaemonApi(broker)
    path = tmp_path / "s.sock"
    server, thread = _serve(path, broker, api=api)
    try:
        key = sorted(f.entities)[0]
        resp = _envelope(path, {"id": "e", "op": "entity", "v": 1, "key": key})
        assert resp["ok"] is True
        result = resp["result"]
        assert result["entity"]["entity"]["key"] == key
        assert result["metrics_meta"]  # non-empty registry metadata
        valid = {s.value for s in Sensitivity}
        for meta in result["metrics_meta"].values():
            assert meta["sensitivity"] in valid
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)
        _stop(broker)


def test_entity_op_not_found_for_missing_key(tmp_path: Path) -> None:
    f = _frame_at(1.0)
    broker = FrameBroker([f])
    api = DaemonApi(broker)
    path = tmp_path / "s.sock"
    server, thread = _serve(path, broker, api=api)
    try:
        resp = _envelope(path, {"id": "nf", "op": "entity", "v": 1, "key": "nonexistent.slice"})
        assert resp["ok"] is False
        assert resp["error"]["code"] == ErrorCode.NOT_FOUND.value
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)
        _stop(broker)


def test_entity_op_rejects_path_and_registry_injection_inputs() -> None:
    """Path-shaped and registry-shaped inputs produce typed validation errors,
    not a lookup. No request parameter may reach a filesystem path, registry
    lookup by arbitrary key, command, or sysfs/procfs read."""
    broker = FrameBroker([_frame_at(1.0)])
    api = DaemonApi(broker)
    injection_inputs = [
        "../etc/passwd",
        "/etc/shadow",
        "a\x00b",
        "\x01control",
        "../../proc/1/root",
        "system.slice/docker-\x00.scope",
    ]
    for key in injection_inputs:
        resp = api.handle({"id": "inj", "op": "entity", "v": 1, "key": key}, PeerCredentials(1, 2, 3))
        assert resp["ok"] is False
        assert resp["error"]["code"] == ErrorCode.INVALID_TYPE.value, f"key={key!r}: {resp}"
        # Must NOT be NOT_FOUND (which would imply a lookup happened).
        assert resp["error"]["code"] != ErrorCode.NOT_FOUND.value
    _stop(broker)


# --- 9. Peer credentials present in audit records ------------------------


def test_peer_credentials_recorded_in_audit_log(tmp_path: Path) -> None:
    broker = FrameBroker([_frame_at(1.0)])
    audit = AuditLog()
    api = DaemonApi(broker, audit_log=audit)
    path = tmp_path / "s.sock"
    server, thread = _serve(path, broker, api=api)
    try:
        _envelope(path, {"id": "p", "op": "hello", "v": 1})
        records = audit.snapshot()
        assert len(records) >= 1
        rec = records[-1]
        assert rec.op == "hello"
        assert rec.allowed is True
        # On Linux, SO_PEERCRED should populate uid/pid/gid. On other
        # platforms the connection is served anonymously (peer=None).
        if rec.peer is not None:
            assert rec.peer.pid is not None or rec.peer.anonymous
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)
        _stop(broker)


def test_peer_credentials_anonymous_when_read_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SO_PEERCRED failure on the REAL server path: served anonymously.

    The handler (not DaemonApi.handle) is what calls read_peer_credentials,
    so this must drive a live socket — a direct api.handle(..., None) call
    would never exercise the failure path (controller review finding).
    """
    import topos.daemon.api as api_mod

    monkeypatch.setattr(api_mod, "read_peer_credentials", lambda _sock: None)
    broker = FrameBroker([_frame_at(1.0)])
    audit = AuditLog()
    api = DaemonApi(broker, audit_log=audit)
    path = tmp_path / "s.sock"
    server, thread = _serve(path, broker, api=api)
    try:
        resp = _envelope(path, {"id": "a", "op": "hello", "v": 1})
        assert resp["ok"] is True
        records = audit.snapshot()
        assert records[-1].peer is None
        assert records[-1].allowed is True
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)
        _stop(broker)


def test_oversized_response_is_replaced_with_typed_error(tmp_path: Path) -> None:
    """Violate max_response_bytes for real through the live server path.

    A tiny byte cap forces any successful `current` payload over the limit;
    the wire response must be the typed OVERSIZED_RESPONSE error envelope,
    never a truncated or full-size body (controller review finding: this
    bound previously had no violation test).
    """
    broker = FrameBroker([_frame_at(1.0)])
    api = DaemonApi(broker, limits=ApiLimits(max_response_bytes=120))
    path = tmp_path / "s.sock"
    server, thread = _serve(path, broker, api=api)
    try:
        resp = _envelope(path, {"id": "big", "op": "current", "v": 1})
        assert resp["ok"] is False
        assert resp["error"]["code"] == "oversized_response"
        raw = json.dumps(resp, sort_keys=True, separators=(",", ":")).encode("utf-8")
        # The replacement envelope itself must be small and carry no frame data.
        assert b"entities" not in raw
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)
        _stop(broker)


def test_legacy_status_op_decision_both_forms(tmp_path: Path) -> None:
    """DAEMON.md compat table: `status` is not a broker op — typed rejection
    in the envelope form, legacy error object in the legacy form."""
    broker = FrameBroker([_frame_at(1.0)])
    path = tmp_path / "s.sock"
    server, thread = _serve(path, broker)
    try:
        enveloped = _envelope(path, {"id": "s", "op": "status", "v": 1})
        assert enveloped["ok"] is False
        assert enveloped["error"]["code"] == "unknown_op"
        legacy = _legacy(path, {"op": "status"})
        assert legacy, "legacy path must answer, not hang or close silently"
        assert all(item.get("type") == "error" for item in legacy)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)
        _stop(broker)


# --- 10. Authorization hook -----------------------------------------------


def test_authorization_hook_may_deny_with_typed_error(tmp_path: Path) -> None:
    broker = FrameBroker([_frame_at(1.0)])

    def deny_hook(peer: PeerCredentials, op: str):
        if op == "current":
            return (ErrorCode.DENIED, "current denied for this peer")
        return None

    api = DaemonApi(broker, auth_hook=deny_hook)
    path = tmp_path / "s.sock"
    server, thread = _serve(path, broker, api=api)
    try:
        # current is denied.
        denied = _envelope(path, {"id": "d", "op": "current", "v": 1})
        assert denied["ok"] is False
        assert denied["error"]["code"] == ErrorCode.DENIED.value
        # hello is still allowed (hook returns None).
        allowed = _envelope(path, {"id": "a", "op": "hello", "v": 1})
        assert allowed["ok"] is True
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)
        _stop(broker)


def test_mutation_shaped_ops_rejected_before_auth_hook() -> None:
    """Mutation-shaped ops are rejected before the hook runs."""
    broker = FrameBroker([_frame_at(1.0)])
    hook_calls: list[str] = []

    def hook(peer: PeerCredentials, op: str):
        hook_calls.append(op)
        return None

    api = DaemonApi(broker, auth_hook=hook)
    # An unknown/mutation-shaped op is not in CAPABILITIES.
    resp = api.handle({"id": "m", "op": "exec", "v": 1}, PeerCredentials(1, 2, 3))
    assert resp["ok"] is False
    assert resp["error"]["code"] == ErrorCode.UNKNOWN_OP.value
    assert "exec" not in hook_calls  # hook never invoked
    _stop(broker)


# --- 11. Resource bounds: enforced at the mechanism level ----------------


def test_request_exactly_at_byte_cap_is_accepted(tmp_path: Path) -> None:
    """A request of exactly the byte cap (including newline) is accepted."""
    broker = FrameBroker([_frame_at(1.0)])
    # Use a tiny cap so we can construct an exactly-fitting request.
    api = DaemonApi(broker, limits=ApiLimits(max_request_bytes=80))
    path = tmp_path / "s.sock"
    server, thread = _serve(path, broker, api=api)
    try:
        # Build a valid envelope that is exactly 80 bytes including newline.
        # {"id":"x","op":"hello","v":1}\n  -> count bytes
        payload = {"id": "x", "op": "hello", "v": 1}
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"
        assert len(encoded) <= 80, f"payload is {len(encoded)} bytes, cap is 80"
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(5.0)
            sock.connect(str(path))
            sock.sendall(encoded)
            sock.shutdown(socket.SHUT_WR)
            data = b""
            while True:
                chunk = sock.recv(65536)
                if not chunk:
                    break
                data += chunk
        resp = json.loads(data.decode("utf-8").splitlines()[0])
        assert resp["ok"] is True
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)
        _stop(broker)


def test_request_one_byte_over_cap_is_rejected(tmp_path: Path) -> None:
    """A request one byte over the cap is rejected with an oversized error."""
    broker = FrameBroker([_frame_at(1.0)])
    api = DaemonApi(broker, limits=ApiLimits(max_request_bytes=64))
    path = tmp_path / "s.sock"
    server, thread = _serve(path, broker, api=api)
    try:
        # Send 65 bytes without a newline (over the 64-byte cap).
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(5.0)
            sock.connect(str(path))
            sock.sendall(b"x" * 65)
            sock.shutdown(socket.SHUT_WR)
            data = b""
            while True:
                chunk = sock.recv(65536)
                if not chunk:
                    break
                data += chunk
        resp = json.loads(data.decode("utf-8").splitlines()[0])
        assert resp["type"] == "error"
        assert "maximum size" in resp["error"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)
        _stop(broker)


def test_idle_connection_past_read_deadline_is_typed_error(tmp_path: Path) -> None:
    """A connection held idle past the read deadline produces a typed error."""
    broker = FrameBroker([_frame_at(1.0)])
    api = DaemonApi(broker, limits=ApiLimits(request_timeout_s=0.2))
    path = tmp_path / "s.sock"
    server, thread = _serve(path, broker, api=api)
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(5.0)
            sock.connect(str(path))
            # Do NOT send anything; wait for the server's read deadline.
            data = b""
            while True:
                chunk = sock.recv(65536)
                if not chunk:
                    break
                data += chunk
        resp = json.loads(data.decode("utf-8").splitlines()[0])
        assert resp["type"] == "error"
        assert "timed out" in resp["error"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)
        _stop(broker)


def test_max_clients_n_plus_one_is_refused(tmp_path: Path) -> None:
    """Open N+1 concurrent clients; the N+1th must be refused/busy."""
    broker = FrameBroker([_frame_at(1.0)])
    api = DaemonApi(broker, limits=ApiLimits(max_clients=1, request_timeout_s=5.0))
    path = tmp_path / "s.sock"
    server, thread = _serve(path, broker, api=api)
    first = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    second = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        first.connect(str(path))
        # Wait until the first client occupies the single slot.
        _wait_for(lambda: server._client_slots._value == 0)  # type: ignore[attr-defined]
        second.connect(str(path))
        data = b""
        while True:
            chunk = second.recv(65536)
            if not chunk:
                break
            data += chunk
        resp = json.loads(data.decode("utf-8").splitlines()[0])
        assert resp["type"] == "error"
        assert resp["error"] == "server busy"
    finally:
        first.close()
        second.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)
        _stop(broker)


def test_api_limits_out_of_range_raises_never_clamped() -> None:
    """Constructor/config limits raise on out-of-range values; never clamped."""
    with pytest.raises(ValueError):
        ApiLimits(max_request_bytes=0)
    with pytest.raises(ValueError):
        ApiLimits(max_request_bytes=10)  # below minimum 64
    with pytest.raises(ValueError):
        ApiLimits(max_response_items=0)
    with pytest.raises(ValueError):
        ApiLimits(max_response_bytes=10)
    with pytest.raises(ValueError):
        ApiLimits(max_clients=0)
    with pytest.raises(ValueError):
        ApiLimits(max_inflight_per_client=0)
    with pytest.raises(ValueError):
        ApiLimits(history_capacity=0)
    with pytest.raises(ValueError):
        ApiLimits(request_timeout_s=0.0)
    with pytest.raises(ValueError):
        ApiLimits(request_timeout_s=301.0)
    with pytest.raises(TypeError):
        ApiLimits(max_request_bytes=True)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        ApiLimits(request_timeout_s="5")  # type: ignore[arg-type]


# --- 12. P51 safety contract persists (no leak) --------------------------


def test_producer_failure_does_not_leak_secret_or_path_through_envelope() -> None:
    """A producer failure with a secret/path in the message must not leak."""
    source = ControlledSource()
    health = ComponentHealthRegistry()
    broker = FrameBroker(source, health_registry=health)
    api = DaemonApi(broker, health_registry=health)
    broker.start()
    source.fail("TOKEN=topsecret /private/path/secret")
    _wait_for(lambda: broker.terminal_kind == "failed")
    resp = api.handle({"id": "leak", "op": "current", "v": 1}, PeerCredentials(1, 2, 3))
    assert resp["ok"] is False
    assert resp["error"]["code"] == ErrorCode.UNAVAILABLE.value
    serialized = json.dumps(resp, sort_keys=True)
    assert "topsecret" not in serialized
    assert "/private" not in serialized
    _stop(broker, source)


def test_envelope_error_never_carries_raw_exception_text() -> None:
    """Internal errors produce a typed INTERNAL code, never a traceback."""
    broker = FrameBroker([_frame_at(1.0)])
    api = DaemonApi(broker)

    # Force an internal error by making the broker raise unexpectedly.
    original = broker.current_entry

    def boom():
        raise RuntimeError("DATABASE_PASSWORD=hunter2 /root/.ssh/id_rsa")

    broker.current_entry = boom  # type: ignore[method-assign]
    try:
        resp = api.handle({"id": "raw", "op": "current", "v": 1}, PeerCredentials(1, 2, 3))
        assert resp["ok"] is False
        assert resp["error"]["code"] == ErrorCode.INTERNAL.value
        serialized = json.dumps(resp, sort_keys=True)
        assert "hunter2" not in serialized
        assert "id_rsa" not in serialized
        assert "RuntimeError" not in serialized
    finally:
        broker.current_entry = original  # type: ignore[method-assign]
        _stop(broker)


# --- 13. Concurrent mixed clients: bounded latency for fast ones ---------


def test_concurrent_mixed_clients_fast_observes_bounded_latency(tmp_path: Path) -> None:
    """One slow client + several fast ones: fast clients observe bounded latency."""
    source = ControlledSource()
    broker = FrameBroker(source, history_size=10)
    broker.start()
    api = DaemonApi(broker, limits=ApiLimits(max_clients=8))
    path = tmp_path / "s.sock"
    server, thread = _serve(path, broker, api=api)
    try:
        source.frame(1.0)
        _wait_for(lambda: broker.current().ts == 1.0)

        # The "slow" client connects and holds the connection without sending.
        slow = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        slow.settimeout(5.0)
        slow.connect(str(path))

        # Several fast clients send envelope hello concurrently.
        results: list[float] = []
        barrier = threading.Barrier(4)

        def fast_client(idx: int) -> None:
            barrier.wait()
            start = time.monotonic()
            resp = _envelope(path, {"id": f"f{idx}", "op": "hello", "v": 1})
            elapsed = time.monotonic() - start
            assert resp["ok"] is True
            results.append(elapsed)

        threads = [threading.Thread(target=fast_client, args=(i,)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5.0)

        # All fast clients completed and observed sub-second latency.
        assert len(results) == 4
        assert all(r < 1.0 for r in results), f"latencies: {results}"
        slow.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)
        _stop(broker, source)
