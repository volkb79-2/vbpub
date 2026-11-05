"""Daemon robustness fixes (B-01/D-01, B-02, B-04, B-05, B-06, D-18).

Every test asserts an outcome (what the daemon does, what a client sees); none
depends on host load, wall-clock speed or real cgroupfs/docker.
"""

from __future__ import annotations

import dataclasses
import json
import os
import signal
import socket
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from conftest import fixture_frame
from topos.daemon.api import (
    ApiLimits,
    DaemonApi,
    ErrorCode,
    PeerCredentials,
    serve_versioned_unix_socket,
)
from topos.daemon.broker import (
    FrameBroker,
    TotalDeadline,
    serve_unix_socket,
)
from topos.daemon.lifecycle import ShutdownCoordinator
from topos.daemon.status import ProtocolStatus, _check_protocol


def _frame_at(ts: float):
    return dataclasses.replace(fixture_frame(), ts=ts)


def _wait_for(predicate, *, timeout_s: float = 10.0) -> None:
    deadline = time.monotonic() + timeout_s
    while not predicate():
        assert time.monotonic() < deadline, "condition not reached"
        time.sleep(0.01)


# --- B-01 / D-01: SIGTERM drives the shutdown path -------------------------


class _FakeBroker:
    def __init__(self, _frames, *, health_registry, terminal_callback=None, **_kwargs) -> None:
        self.health_registry = health_registry
        self.terminal_callback = terminal_callback
        self.events: list[str] = []

    def start(self) -> None:
        self.events.append("start")

    def stop(self) -> None:
        self.events.append("stop")

    def join(self, *, timeout: float) -> None:
        self.events.append("join")


def _wire_serve(monkeypatch, tmp_path: Path, serve_forever_body):
    import topos.cli as cli
    from topos.config import ToposConfig

    observed: dict = {"stopped": threading.Event()}

    class _Collector:
        def __init__(self, cgroup_root, config) -> None:
            self.cgroup_root = cgroup_root or config.cgroup_root
            self.network_providers = ()

    class _Server:
        def serve_forever(self) -> None:
            serve_forever_body(observed)
            # Like socketserver: return only once shutdown() was requested.
            assert observed["stopped"].wait(timeout=10.0), "shutdown() never requested"

        def shutdown(self) -> None:
            observed["stopped"].set()

        def server_close(self) -> None:
            observed["closed"] = True

    def _make_broker(*args, **kwargs):
        broker = _FakeBroker(*args, **kwargs)
        observed["broker"] = broker
        return broker

    config = ToposConfig(cgroup_root=tmp_path / "cgroup")
    monkeypatch.setattr(cli, "load", lambda _path: config)
    monkeypatch.setattr(cli, "Collector", _Collector)
    monkeypatch.setattr(cli, "FrameBroker", _make_broker)
    monkeypatch.setattr(cli, "DaemonApi", lambda *_a, **_k: object())
    monkeypatch.setattr(cli, "serve_versioned_unix_socket", lambda *_a, **_k: _Server())
    return cli, observed


def test_sigterm_runs_the_shutdown_path_and_restores_handlers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cli, observed = _wire_serve(
        monkeypatch, tmp_path, lambda _o: os.kill(os.getpid(), signal.SIGTERM)
    )
    before = signal.getsignal(signal.SIGTERM)

    assert cli._main_daemon(["serve", "--socket", str(tmp_path / "t.sock")]) == 0

    assert observed["closed"] is True
    assert observed["broker"].events == ["start", "stop", "join"]
    assert signal.getsignal(signal.SIGTERM) is before


# --- B-02: producer death exits non-zero -----------------------------------


def test_producer_death_stops_the_daemon_with_nonzero_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def body(observed) -> None:
        # The collector thread dies while the server is serving.
        observed["broker"].terminal_callback("failed")

    cli, observed = _wire_serve(monkeypatch, tmp_path, body)

    assert cli._main_daemon(["serve", "--socket", str(tmp_path / "t.sock")]) == 1

    assert observed["closed"] is True
    assert observed["broker"].events == ["start", "stop", "join"]
    assert "frame producer failed" in capsys.readouterr().err


def test_real_broker_reports_producer_failure_and_exhaustion_to_callback() -> None:
    def failing():
        yield _frame_at(1.0)
        raise RuntimeError("cgroup vanished")

    seen: list[str] = []
    broker = FrameBroker(failing(), terminal_callback=seen.append)
    broker.start()
    _wait_for(lambda: seen)
    assert seen == ["failed"]

    ended: list[str] = []
    finite = FrameBroker([_frame_at(1.0)], terminal_callback=ended.append)
    finite.start()
    _wait_for(lambda: ended)
    assert ended == ["exhausted"]


def test_requested_stop_does_not_count_as_producer_failure() -> None:
    class _Server:
        shutdown_calls = 0

        def shutdown(self) -> None:
            _Server.shutdown_calls += 1

    coordinator = ShutdownCoordinator(_Server())
    coordinator.on_producer_terminal("stopped")
    assert coordinator.reason is None and not coordinator.producer_failed


def test_status_flags_a_frame_older_than_three_intervals_as_stale() -> None:
    frame = dataclasses.replace(_frame_at(1000.0), interval_s=5.0)

    class _Client:
        def __init__(self, _path) -> None:
            pass

        def current_frame(self):
            return frame

    with patch("topos.daemon.status.DaemonClient", _Client):
        stale = _check_protocol(Path("/run/x.sock"), now=lambda: 1016.0)
        fresh = _check_protocol(Path("/run/x.sock"), now=lambda: 1014.0)

    assert stale.ok is False and "Stale frame" in stale.message
    assert fresh == ProtocolStatus(
        ok=True,
        message="Current frame retrieved successfully.",
        schema_version=frame.schema_version,
        frame_ts=1000.0,
        entity_count=len(frame.entities),
    )


# --- B-04: stale cursor and daemon epoch -----------------------------------


def test_cursor_beyond_latest_is_a_gap_not_silence() -> None:
    broker = FrameBroker([_frame_at(float(i)) for i in range(3)])
    broker.start()
    _wait_for(lambda: broker.stream(limit=1).latest_seq == 2 or broker.terminal_kind)

    stale = broker.stream(limit=10, cursor=4000)
    assert stale.gap is True and stale.entries == ()

    ok = broker.stream(limit=10, cursor=1)
    assert ok.gap is False and [seq for seq, _ in ok.entries] == [2]


def test_cursor_from_previous_run_is_a_gap_before_first_frame() -> None:
    gate = threading.Event()

    def blocked():
        gate.wait(timeout=10.0)
        yield _frame_at(1.0)

    broker = FrameBroker(blocked())
    assert broker.stream(limit=1, cursor=4000).gap is True
    assert broker.stream(limit=1, cursor=-1).gap is False
    gate.set()


def test_epoch_is_per_broker_and_present_in_both_protocols(tmp_path: Path) -> None:
    first = FrameBroker([_frame_at(1.0)])
    second = FrameBroker([_frame_at(1.0)])
    assert first.epoch != second.epoch

    api = DaemonApi(first)
    first.start()
    _wait_for(lambda: first.stream(limit=1).entries)
    history = api.handle({"id": "a", "op": "history", "v": 1, "limit": 1}, None)
    assert history["result"]["epoch"] == first.epoch
    legacy = first.responses({"op": "stream", "limit": 1})
    assert legacy[-1]["epoch"] == first.epoch


def test_client_stream_raises_on_daemon_restart(tmp_path: Path) -> None:
    from topos.daemon.client import DaemonHistoryGapError, DaemonFrameBatch, stream_frames

    batches = [
        DaemonFrameBatch((), 0, 0, 0, False, epoch="old"),
        DaemonFrameBatch((), 0, 0, 0, False, epoch="new"),
    ]

    class _Client:
        def __init__(self, _path) -> None:
            pass

        def stream_batch(self, limit, cursor):
            return batches.pop(0)

    with patch("topos.daemon.client.DaemonClient", _Client), patch("topos.daemon.client.time.sleep"):
        with pytest.raises(DaemonHistoryGapError, match="restarted"):
            list(stream_frames(tmp_path / "s.sock"))


# --- B-05: total deadlines and the busy reply ------------------------------


def _read_all(sock: socket.socket) -> bytes:
    data = b""
    while True:
        chunk = sock.recv(65536)
        if not chunk:
            return data
        data += chunk


def test_a_trickling_client_is_cut_off_by_the_total_request_deadline(tmp_path: Path) -> None:
    broker = FrameBroker([_frame_at(1.0)])
    path = tmp_path / "s.sock"
    server = serve_unix_socket(path, broker, request_timeout_s=0.3)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.connect(str(path))
            sock.settimeout(0.02)
            guard = time.monotonic() + 15.0
            reply = b""
            # One byte every ~20 ms never lets a per-recv timeout fire; only a
            # total deadline can end this request.
            while time.monotonic() < guard and not reply:
                try:
                    sock.sendall(b" ")
                except OSError:
                    break
                try:
                    reply = sock.recv(65536)
                except socket.timeout:
                    pass
        assert json.loads(reply.decode().splitlines()[0]) == {
            "type": "error",
            "error": "request timed out",
        }
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)


def test_total_deadline_unblocks_a_write_to_a_peer_that_never_reads() -> None:
    server_end, peer_end = socket.socketpair()
    try:
        server_end.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4096)
        outcome: dict = {}

        def writer() -> None:
            with TotalDeadline(server_end, 0.3, socket.SHUT_RDWR) as deadline:
                try:
                    server_end.sendall(b"x" * (16 * 1024 * 1024))
                    outcome["error"] = None
                except OSError as exc:
                    outcome["error"] = exc
                outcome["fired"] = deadline.fired

        thread = threading.Thread(target=writer, daemon=True)
        thread.start()
        thread.join(timeout=15.0)
        assert not thread.is_alive(), "write was not bounded by the total deadline"
        assert isinstance(outcome["error"], OSError) and outcome["fired"] is True
    finally:
        server_end.close()
        peer_end.close()


def test_busy_reply_is_an_envelope_for_envelope_clients(tmp_path: Path) -> None:
    broker = FrameBroker([_frame_at(1.0)])
    api = DaemonApi(broker, limits=ApiLimits(max_clients=1, request_timeout_s=5.0))
    path = tmp_path / "s.sock"
    server = serve_versioned_unix_socket(path, broker, api)
    server.busy_peek_wait_s = 5.0  # wait for the request however slow the host is
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    holder = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    second = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        holder.connect(str(path))
        _wait_for(lambda: server._client_slots._value == 0)  # type: ignore[attr-defined]
        second.connect(str(path))
        second.settimeout(5.0)
        second.sendall(b'{"id":"req-7","op":"current","v":1}\n')
        reply = json.loads(_read_all(second).decode().splitlines()[0])
        assert reply == {
            "id": "req-7",
            "ok": False,
            "error": {"code": ErrorCode.SERVER_BUSY.value, "message": "server busy"},
        }
    finally:
        holder.close()
        second.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)


# --- B-06: legacy ops go through hook, audit and byte cap ------------------


def test_legacy_ops_hit_the_auth_hook_audit_and_byte_cap() -> None:
    broker = FrameBroker([_frame_at(1.0)])
    broker.start()
    _wait_for(lambda: broker.stream(limit=1).entries)
    seen: list[str] = []

    def deny_stream(peer, op):
        seen.append(op)
        return (ErrorCode.DENIED, "no stream for you") if op == "stream" else None

    api = DaemonApi(broker, auth_hook=deny_stream)
    peer = PeerCredentials(pid=1, uid=2, gid=3)

    denied = api.handle_legacy({"op": "stream", "limit": 1}, peer)
    allowed = api.handle_legacy({"op": "current"}, peer)

    assert denied == [{"type": "error", "error": "no stream for you"}]
    assert allowed[0]["type"] == "frame"
    assert seen == ["stream", "current"]
    records = api.audit_log.snapshot()
    assert [(r.op, r.allowed, r.peer) for r in records] == [
        ("stream", False, peer),
        ("current", True, peer),
    ]

    capped = DaemonApi(broker, limits=ApiLimits(max_response_bytes=64))
    assert capped.handle_legacy({"op": "current"}, peer) == [
        {"type": "error", "error": "response exceeds max_response_bytes"}
    ]


def test_legacy_requests_over_the_socket_are_audited(tmp_path: Path) -> None:
    broker = FrameBroker([_frame_at(1.0)])
    api = DaemonApi(broker)
    path = tmp_path / "s.sock"
    server = serve_versioned_unix_socket(path, broker, api)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(5.0)
            sock.connect(str(path))
            sock.sendall(b'{"op":"current"}\n')
            sock.shutdown(socket.SHUT_WR)
            lines = _read_all(sock).decode().splitlines()
        assert json.loads(lines[0])["type"] == "frame"
        _wait_for(lambda: any(r.op == "current" for r in api.audit_log.snapshot()))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)


def test_deeply_nested_request_gets_an_error_reply_not_a_dropped_connection(
    tmp_path: Path,
) -> None:
    broker = FrameBroker([_frame_at(1.0)])
    api = DaemonApi(broker)
    path = tmp_path / "s.sock"
    server = serve_versioned_unix_socket(path, broker, api)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(5.0)
            sock.connect(str(path))
            sock.sendall(b"[" * 200000 + b"\n")
            sock.shutdown(socket.SHUT_WR)
            reply = json.loads(_read_all(sock).decode().splitlines()[0])
        assert reply["type"] == "error"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)


# --- D-18: health messages carry the cause ---------------------------------


def test_bpf_start_failure_health_message_names_the_cause(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from topos.config import BpfSnapshotConfig, ToposConfig

    cli, observed = _wire_serve(
        monkeypatch, tmp_path, lambda _o: os.kill(os.getpid(), signal.SIGTERM)
    )
    config = ToposConfig(
        cgroup_root=tmp_path / "cgroup",
        bpf_snapshot=BpfSnapshotConfig(enabled=True, root=tmp_path / "pins"),
    )

    class _FailingBridge:
        def __init__(self, *_a, **_k) -> None:
            raise RuntimeError("bridge setup denied")

    monkeypatch.setattr(cli, "load", lambda _path: config)
    monkeypatch.setattr(cli, "BpfSnapshotBridge", _FailingBridge)

    assert cli._main_daemon(["serve", "--socket", str(tmp_path / "t.sock")]) == 0

    bpf = observed["broker"].health_registry.snapshot().by_name("bpf_snapshot_bridge")
    assert bpf is not None and bpf.error is not None
    assert "bridge setup denied" in bpf.error.message


# --- B-16: HTTP gateway handler timeout and concurrency cap ---------------


def _plain_gateway(monkeypatch: pytest.MonkeyPatch, *, timeout_s: float = 5.0):
    from topos.daemon import http_gateway

    monkeypatch.setattr(http_gateway, "HANDLER_TIMEOUT_S", timeout_s)
    gateway = http_gateway.VersionedReadHttpGateway(
        client=object(),  # type: ignore[arg-type]
        config=http_gateway.GatewayConfig(port=0),
    )
    thread = threading.Thread(target=gateway.serve_forever, daemon=True)
    thread.start()
    return gateway, thread


def test_gateway_closes_an_idle_connection_after_the_handler_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gateway, thread = _plain_gateway(monkeypatch, timeout_s=0.3)
    host, port = gateway.server_address
    try:
        with socket.create_connection((host, port), timeout=15.0) as idle:
            # Never send a request: the server must hang up by itself.
            assert idle.recv(1024) == b""
    finally:
        gateway.shutdown()
        gateway.server_close()
        thread.join(timeout=2.0)


def test_gateway_caps_concurrent_handlers_and_answers_busy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gateway, thread = _plain_gateway(monkeypatch)
    gateway._server._handler_slots = threading.BoundedSemaphore(1)  # type: ignore[attr-defined]
    host, port = gateway.server_address
    holder = socket.create_connection((host, port), timeout=15.0)
    try:
        _wait_for(lambda: gateway._server._handler_slots._value == 0)  # type: ignore[attr-defined]
        with socket.create_connection((host, port), timeout=15.0) as second:
            reply = b""
            while chunk := second.recv(4096):
                reply += chunk
        assert reply.startswith(b"HTTP/1.1 503")
        assert b'"gateway_busy"' in reply
    finally:
        holder.close()
        gateway.shutdown()
        gateway.server_close()
        thread.join(timeout=2.0)


def test_operator_interrupt_clears_producer_failure_but_signals_do_not() -> None:
    class _Server:
        def shutdown(self) -> None:
            pass

    failed = ShutdownCoordinator(_Server())
    failed.on_producer_terminal("exhausted")
    assert failed.producer_failed is True

    interrupted = ShutdownCoordinator(_Server())
    interrupted.on_producer_terminal("failed")
    interrupted.operator_interrupt()
    assert interrupted.producer_failed is False

    fresh = ShutdownCoordinator(_Server())
    fresh.operator_interrupt()
    assert fresh.producer_failed is False
    assert fresh.reason == "KeyboardInterrupt"
