from __future__ import annotations

import io
import json
import socketserver
import threading
from types import SimpleNamespace

import pytest

from conftest import fixture_frame
from topos.daemon import broker as broker_module
from topos.daemon.broker import (
    BrokerUnixServer,
    FrameBroker,
    FrameUnavailableError,
    MAX_REQUEST_BYTES,
    _BrokerHandler,
)
from topos.model import Frame


def _frame_at(ts: float) -> Frame:
    base = fixture_frame()
    return Frame(base.schema_version, ts, base.interval_s, base.host, base.entities)


def test_failed_thread_start_resets_lifecycle_for_a_real_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    """A transient thread-start failure must not strand the broker as started."""

    class FailingThread:
        def __init__(self, **_kwargs: object) -> None:
            pass

        def start(self) -> None:
            raise RuntimeError("thread resources unavailable")

    broker = FrameBroker([])
    real_thread = broker_module.threading.Thread
    monkeypatch.setattr(broker_module.threading, "Thread", FailingThread)

    with pytest.raises(RuntimeError, match="resources unavailable"):
        broker.start()

    assert broker._thread is None  # type: ignore[attr-defined]
    assert broker._started is False  # type: ignore[attr-defined]
    monkeypatch.setattr(broker_module.threading, "Thread", real_thread)
    broker.start()
    broker.join(timeout=1.0)
    assert broker.terminal_kind == "exhausted"


def test_stop_while_first_frame_is_blocked_returns_stopped_public_error() -> None:
    """Shutdown wakes a current reader with the typed stopped-before-frame result."""
    entered = threading.Event()
    release = threading.Event()

    def source():
        entered.set()
        release.wait()
        yield from ()

    broker = FrameBroker(source())
    broker.start()
    assert entered.wait(timeout=1.0)
    broker.stop()
    try:
        with pytest.raises(FrameUnavailableError, match="stopped before publishing"):
            broker.current()
    finally:
        release.set()
        broker.join(timeout=1.0)


def test_health_and_window_type_errors_stay_safe_and_specific() -> None:
    """Invalid protocol fields return public errors before source work begins."""
    broker = FrameBroker([_frame_at(1.0)])

    assert broker.responses({"op": "health", "extra": True}) == [
        {"type": "error", "error": "invalid health request"}
    ]
    with pytest.raises(TypeError, match="since_ts must be a number"):
        broker.stream_window(since_ts="soon")  # type: ignore[arg-type]


class _Request:
    def __init__(self, *, fail_send: bool = False) -> None:
        self.sent = b""
        self.fail_send = fail_send

    def sendall(self, payload: bytes) -> None:
        if self.fail_send:
            raise OSError("peer closed")
        self.sent += payload


def _bare_server() -> tuple[BrokerUnixServer, list[_Request]]:
    server = object.__new__(BrokerUnixServer)
    server._client_slots = threading.BoundedSemaphore(1)
    closed: list[_Request] = []
    server.shutdown_request = closed.append  # type: ignore[method-assign]
    return server, closed


@pytest.mark.parametrize("fail_send", [False, True])
def test_busy_server_closes_rejected_client_even_when_busy_reply_cannot_send(fail_send: bool) -> None:
    """Backpressure cannot leak a rejected connection when its peer has gone away."""
    server, closed = _bare_server()
    assert server._client_slots.acquire(blocking=False)
    request = _Request(fail_send=fail_send)

    server.process_request(request, None)

    assert closed == [request]
    if fail_send:
        assert request.sent == b""
    else:
        assert json.loads(request.sent) == {"type": "error", "error": "server busy"}


def test_scheduler_failure_releases_the_reserved_client_slot(monkeypatch: pytest.MonkeyPatch) -> None:
    """A dispatch exception must not permanently consume bounded concurrency."""
    server, _closed = _bare_server()

    def fail_dispatch(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("dispatch failed")

    monkeypatch.setattr(socketserver.ThreadingUnixStreamServer, "process_request", fail_dispatch)

    with pytest.raises(RuntimeError, match="dispatch failed"):
        server.process_request(_Request(), None)

    assert server._client_slots.acquire(blocking=False)


class _Connection:
    def __init__(self) -> None:
        self.timeout: float | None = None

    def settimeout(self, timeout: float) -> None:
        self.timeout = timeout


def _handle_line(line: bytes, responses: object = None) -> tuple[float | None, list[dict[str, object]]]:
    handler = object.__new__(_BrokerHandler)
    connection = _Connection()
    handler.connection = connection
    handler.rfile = io.BytesIO(line)
    handler.wfile = io.BytesIO()
    broker = SimpleNamespace(responses=responses)
    handler.server = SimpleNamespace(request_timeout_s=3.0, broker=broker)

    handler.handle()

    written = handler.wfile.getvalue().decode("utf-8").splitlines()
    return connection.timeout, [json.loads(item) for item in written]


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        (b"", []),
        (b"null\n", [{"type": "error", "error": "request must be an object"}]),
        (b"{not-json}\n", [{"type": "error"}]),
        (b"\xff\n", [{"type": "error"}]),
        (b"x" * (MAX_REQUEST_BYTES + 1), [{"type": "error", "error": "request exceeds maximum size"}]),
    ],
)
def test_handler_turns_transport_parse_failures_into_wire_errors(
    line: bytes, expected: list[dict[str, object]]
) -> None:
    """Malformed or oversized input never escapes the handler as an exception."""
    timeout, actual = _handle_line(line)

    assert timeout == 3.0
    if expected and expected[0] == {"type": "error"}:
        assert actual[0]["type"] == "error"
    else:
        assert actual == expected


def test_handler_hides_unexpected_broker_failure() -> None:
    """Transport clients receive one safe error when broker dispatch crashes."""

    def explode(_request: dict[str, object]) -> list[dict[str, object]]:
        raise RuntimeError("internal token must not escape")

    _timeout, actual = _handle_line(b'{"op":"stream"}\n', explode)

    assert actual == [{"type": "error", "error": "request failed"}]
