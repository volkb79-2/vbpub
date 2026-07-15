"""P66 — Daemon Client Versioned Health Method deterministic tests.

Covers (per handoff):
1. Happy path: known component states decode correctly (name/state/detail/
   error) and ``overall_ok`` reflects the aggregate correctly for an
   all-healthy registry.
2. Happy path: a DEGRADED component decodes with its error payload intact
   and flips ``overall_ok`` to False.
3. Happy path: a FAILED component flips ``overall_ok`` to False.
4. An ok:false envelope (health unavailable) surfaces DaemonResponseError
   with ``.code == "unavailable"``.
5. Malformed (non-JSON) response raises DaemonProtocolError.
6. Oversized response raises DaemonProtocolError.
7. Non-object response raises DaemonProtocolError.
8. id echo mismatch raises DaemonProtocolError.
9. A health payload with an invalid component state value raises
   DaemonProtocolError (health-content-level validation, not just envelope-
   level validation).
10. A health payload with an incompatible schema_version raises
    DaemonProtocolError.
11. Parity: request_health_versioned() and the legacy request_health()
    decode the same underlying registry to equivalent component data (single
    shared decode path, no second implementation).
12. The legacy request_health() keeps returning HealthSnapshot (not the new
    type) -- proves the new method is additive, not a replacement.
13. request_health_versioned() returns DaemonVersionedHealthResult, which is
    a distinct type from the legacy HealthSnapshot (no collision).
14. Connection failure raises DaemonConnectError.
"""

from __future__ import annotations

import json
import socket
import socketserver
import threading
from pathlib import Path

import pytest

from conftest import fixture_frame
from groop.daemon import DaemonApi, FrameBroker, serve_versioned_unix_socket
from groop.daemon.client import (
    DaemonClient,
    DaemonConnectError,
    DaemonProtocolError,
    DaemonResponseError,
    DaemonVersionedHealthResult,
)
from groop.daemon.component_health import (
    ComponentError,
    ComponentHealthRegistry,
    ComponentState,
    HealthSnapshot,
    build_health_response,
)
from groop.model import Frame


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


def _serve_echo_lines(socket_path: Path, template: dict) -> socketserver.UnixStreamServer:
    """Return a server that parses the client request id and wraps a
    template response dict around it."""

    class Handler(socketserver.StreamRequestHandler):
        def handle(self) -> None:
            raw = self.rfile.readline(1024 * 1024)
            request = json.loads(raw.decode("utf-8"))
            client_id = request.get("id", "unknown")
            response_dict = dict(template)
            response_dict["id"] = client_id
            payload = json.dumps(response_dict, sort_keys=True, separators=(",", ":")).encode("utf-8")
            self.wfile.write(payload + b"\n")

    server = socketserver.UnixStreamServer(str(socket_path), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def _registry_with_states(**states: ComponentState) -> ComponentHealthRegistry:
    reg = ComponentHealthRegistry()
    for name, state in states.items():
        if state is ComponentState.HEALTHY:
            reg.record_success(name, detail=f"{name} ok")
        elif state is ComponentState.DEGRADED:
            reg.record_degraded(
                name,
                detail=f"{name} degraded",
                error=ComponentError(message=f"{name} degraded error", error_code="degraded_err"),
            )
        elif state is ComponentState.FAILED:
            reg.record_failure(
                name,
                detail=f"{name} failed",
                error=ComponentError(message=f"{name} failed error", error_code="failed_err"),
            )
        elif state is ComponentState.DISABLED:
            reg.mark_disabled(name, detail=f"{name} disabled")
        elif state is ComponentState.STARTING:
            reg.mark_starting(name, detail=f"{name} starting")
        else:
            raise AssertionError(f"unhandled state {state!r}")
    return reg


# --- 1. Happy path: all-healthy registry decodes + overall_ok True -------


def test_request_health_versioned_all_healthy(tmp_path: Path) -> None:
    reg = _registry_with_states(
        collector=ComponentState.HEALTHY,
        bpf_snapshot_bridge=ComponentState.DISABLED,
        paddr_lifecycle=ComponentState.DISABLED,
    )
    broker = FrameBroker([_frame_at(1.0)], health_registry=reg)
    path = tmp_path / "s.sock"
    server, thread = _serve(path, broker, api=DaemonApi(broker, health_registry=reg))
    try:
        client = DaemonClient(path)
        result = client.request_health_versioned()
        assert isinstance(result, DaemonVersionedHealthResult)
        collector = result.snapshot.by_name("collector")
        assert collector is not None
        assert collector.state is ComponentState.HEALTHY
        assert collector.detail == "collector ok"
        bridge = result.snapshot.by_name("bpf_snapshot_bridge")
        assert bridge is not None
        assert bridge.state is ComponentState.DISABLED
        assert result.overall_ok is True
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)
        _stop(broker)


# --- 2. Happy path: DEGRADED component decodes + overall_ok False --------


def test_request_health_versioned_degraded_component(tmp_path: Path) -> None:
    reg = _registry_with_states(
        collector=ComponentState.HEALTHY,
        bpf_snapshot_bridge=ComponentState.DEGRADED,
        paddr_lifecycle=ComponentState.DISABLED,
    )
    broker = FrameBroker([_frame_at(1.0)], health_registry=reg)
    path = tmp_path / "s.sock"
    server, thread = _serve(path, broker, api=DaemonApi(broker, health_registry=reg))
    try:
        client = DaemonClient(path)
        result = client.request_health_versioned()
        bridge = result.snapshot.by_name("bpf_snapshot_bridge")
        assert bridge is not None
        assert bridge.state is ComponentState.DEGRADED
        assert bridge.error is not None
        assert bridge.error.message == "bpf_snapshot_bridge degraded error"
        assert bridge.error.error_code == "degraded_err"
        assert result.overall_ok is False
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)
        _stop(broker)


# --- 3. Happy path: FAILED component -> overall_ok False -----------------


def test_request_health_versioned_failed_component(tmp_path: Path) -> None:
    reg = _registry_with_states(
        collector=ComponentState.FAILED,
        bpf_snapshot_bridge=ComponentState.DISABLED,
        paddr_lifecycle=ComponentState.DISABLED,
    )
    broker = FrameBroker([_frame_at(1.0)], health_registry=reg)
    path = tmp_path / "s.sock"
    server, thread = _serve(path, broker, api=DaemonApi(broker, health_registry=reg))
    try:
        client = DaemonClient(path)
        result = client.request_health_versioned()
        collector = result.snapshot.by_name("collector")
        assert collector is not None
        assert collector.state is ComponentState.FAILED
        assert result.overall_ok is False
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)
        _stop(broker)


# --- 4. ok:false (health unavailable) -> DaemonResponseError with .code --


def test_request_health_versioned_unavailable_carries_code(tmp_path: Path) -> None:
    broker = FrameBroker([_frame_at(1.0)])
    path = tmp_path / "s.sock"
    # No health_registry on the API -> _op_health raises UNAVAILABLE.
    server, thread = _serve(path, broker, api=DaemonApi(broker, health_registry=None))
    try:
        client = DaemonClient(path)
        with pytest.raises(DaemonResponseError) as excinfo:
            client.request_health_versioned()
        assert excinfo.value.code == "unavailable"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)
        _stop(broker)


# --- 5-7. Malformed/oversized/non-object envelope responses --------------


def test_request_health_versioned_malformed_json_raises_protocol_error(tmp_path: Path) -> None:
    path = tmp_path / "bad.sock"
    server = _serve_lines(path, b"not-json-at-all")
    try:
        client = DaemonClient(path)
        with pytest.raises(DaemonProtocolError, match="malformed JSON"):
            client.request_health_versioned()
    finally:
        server.shutdown()
        server.server_close()


def test_request_health_versioned_oversized_raises_protocol_error(tmp_path: Path) -> None:
    from groop.daemon.api import DEFAULT_MAX_RESPONSE_BYTES

    big_payload = b"x" * (DEFAULT_MAX_RESPONSE_BYTES + 100) + b"\n"
    path = tmp_path / "big.sock"
    server = _serve_lines(path, big_payload)
    try:
        client = DaemonClient(path)
        with pytest.raises(DaemonProtocolError, match="oversized"):
            client.request_health_versioned()
    finally:
        server.shutdown()
        server.server_close()


def test_request_health_versioned_non_object_raises_protocol_error(tmp_path: Path) -> None:
    path = tmp_path / "arr.sock"
    server = _serve_lines(path, b'["not", "an", "object"]')
    try:
        client = DaemonClient(path)
        with pytest.raises(DaemonProtocolError, match="non-object"):
            client.request_health_versioned()
    finally:
        server.shutdown()
        server.server_close()


# --- 8. id echo mismatch raises DaemonProtocolError -----------------------


def test_request_health_versioned_id_echo_mismatch_raises_protocol_error(tmp_path: Path) -> None:
    reg = ComponentHealthRegistry()
    payload = build_health_response(reg)
    wrong_id_payload = json.dumps({"id": "wrong-id", "ok": True, "result": payload}).encode("utf-8")
    path = tmp_path / "mismatch.sock"
    server = _serve_lines(path, wrong_id_payload)
    try:
        client = DaemonClient(path)
        with pytest.raises(DaemonProtocolError, match="returned id"):
            client.request_health_versioned()
    finally:
        server.shutdown()
        server.server_close()


# --- 9. Health-content-level malformed: invalid component state ----------


def test_request_health_versioned_invalid_component_state_raises_protocol_error(
    tmp_path: Path,
) -> None:
    payload = build_health_response(ComponentHealthRegistry())
    payload["components"][0]["state"] = "future-state"
    template = {"id": "ignored", "ok": True, "result": payload}
    path = tmp_path / "badstate.sock"
    server = _serve_echo_lines(path, template=template)
    try:
        client = DaemonClient(path)
        with pytest.raises(DaemonProtocolError, match="incompatible health-v1"):
            client.request_health_versioned()
    finally:
        server.shutdown()
        server.server_close()


# --- 10. Health-content-level malformed: incompatible schema_version -----


def test_request_health_versioned_incompatible_schema_version_raises_protocol_error(
    tmp_path: Path,
) -> None:
    payload = build_health_response(ComponentHealthRegistry())
    payload["schema_version"] = 999
    template = {"id": "ignored", "ok": True, "result": payload}
    path = tmp_path / "badschema.sock"
    server = _serve_echo_lines(path, template=template)
    try:
        client = DaemonClient(path)
        with pytest.raises(DaemonProtocolError, match="incompatible health-v1"):
            client.request_health_versioned()
    finally:
        server.shutdown()
        server.server_close()


# --- 11. Parity: versioned and legacy decode the same registry -----------


def test_request_health_versioned_matches_legacy_request_health(tmp_path: Path) -> None:
    reg = _registry_with_states(
        collector=ComponentState.HEALTHY,
        bpf_snapshot_bridge=ComponentState.DEGRADED,
        paddr_lifecycle=ComponentState.STARTING,
    )
    broker = FrameBroker([_frame_at(1.0)], health_registry=reg)
    path = tmp_path / "s.sock"
    server, thread = _serve(path, broker, api=DaemonApi(broker, health_registry=reg))
    try:
        client = DaemonClient(path)
        versioned = client.request_health_versioned()
        legacy = client.request_health()
        assert isinstance(legacy, HealthSnapshot)
        assert len(versioned.snapshot.snapshots) == len(legacy.snapshots)
        for name in ("collector", "bpf_snapshot_bridge", "paddr_lifecycle"):
            v = versioned.snapshot.by_name(name)
            l = legacy.by_name(name)
            assert v is not None and l is not None
            assert v.state is l.state
            assert v.detail == l.detail
            assert v.consecutive_failures == l.consecutive_failures
            assert v.state_change_count == l.state_change_count
            assert v.error == l.error
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)
        _stop(broker)


# --- 12. Legacy request_health() keeps returning HealthSnapshot ----------


def test_legacy_request_health_still_returns_health_snapshot(tmp_path: Path) -> None:
    reg = ComponentHealthRegistry()
    reg.record_success("collector", detail="legacy path untouched")
    broker = FrameBroker([_frame_at(1.0)], health_registry=reg)
    path = tmp_path / "s.sock"
    server, thread = _serve(path, broker, api=DaemonApi(broker, health_registry=reg))
    try:
        client = DaemonClient(path)
        legacy = client.request_health()
        assert type(legacy) is HealthSnapshot
        assert not isinstance(legacy, DaemonVersionedHealthResult)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)
        _stop(broker)


# --- 13. New result type is distinct from the legacy HealthSnapshot ------


def test_request_health_versioned_result_type_does_not_collide_with_legacy(
    tmp_path: Path,
) -> None:
    reg = ComponentHealthRegistry()
    reg.record_success("collector", detail="ok")
    broker = FrameBroker([_frame_at(1.0)], health_registry=reg)
    path = tmp_path / "s.sock"
    server, thread = _serve(path, broker, api=DaemonApi(broker, health_registry=reg))
    try:
        client = DaemonClient(path)
        result = client.request_health_versioned()
        assert type(result) is DaemonVersionedHealthResult
        assert not isinstance(result, HealthSnapshot)
        assert isinstance(result.snapshot, HealthSnapshot)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)
        _stop(broker)


# --- 14. Connection failure raises DaemonConnectError ---------------------


def test_request_health_versioned_connection_failure_raises_connect_error(
    tmp_path: Path,
) -> None:
    client = DaemonClient(tmp_path / "no-such-socket.sock", timeout_s=0.5)
    with pytest.raises(DaemonConnectError, match="cannot connect"):
        client.request_health_versioned()
