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
from topos.daemon.broker import (
    FrameBroker,
    FrameProducerError,
    FrameProducerTimeoutError,
    FrameUnavailableError,
    serve_unix_socket,
)
from topos.daemon.client import DaemonClient, DaemonHistoryGapError, stream_frames
from topos.daemon.component_health import ComponentHealthRegistry, ComponentState
from topos.model import Frame
from topos.record.live import live_frame_stream


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


def test_producer_advances_independently_and_current_is_fresh() -> None:
    source = ControlledSource()
    broker = FrameBroker(source)
    broker.start()
    try:
        source.frame(10.0)
        _wait_for(lambda: broker.current().ts == 10.0)
        source.frame(20.0)
        _wait_for(lambda: broker.current().ts == 20.0)
    finally:
        _stop(broker, source)


def test_start_is_atomic_under_concurrent_callers() -> None:
    source = ControlledSource()
    broker = FrameBroker(source)
    callers = [threading.Thread(target=broker.start) for _ in range(16)]
    for caller in callers:
        caller.start()
    for caller in callers:
        caller.join()
    producers = [thread for thread in threading.enumerate() if thread.name == "topos-broker-producer"]
    try:
        assert producers == [broker._thread]  # type: ignore[attr-defined]
    finally:
        _stop(broker, source)


def test_join_timeout_is_typed_for_uninterruptible_iterator() -> None:
    release = threading.Event()

    def blocked() -> Iterator[Frame]:
        release.wait()
        yield _frame_at(1.0)

    broker = FrameBroker(blocked())
    broker.start()
    broker.stop()
    with pytest.raises(FrameProducerTimeoutError, match="timed out"):
        broker.join(timeout=0.01)
    release.set()
    broker.join(timeout=2.0)


def test_production_live_stream_sleep_is_stop_interruptible() -> None:
    class Collector:
        class Config:
            interval = 60.0

        config = Config()

        def collect_once(self) -> Frame:
            return _frame_at(1.0)

    stop_event = threading.Event()
    broker = FrameBroker(
        live_frame_stream(Collector(), stop_event=stop_event),  # type: ignore[arg-type]
        stop_callback=stop_event.set,
    )
    assert broker.current().ts == 1.0
    started = time.monotonic()
    broker.stop()
    broker.join(timeout=1.0)
    assert time.monotonic() - started < 0.5


def test_empty_source_reports_exhaustion_without_waiting_full_timeout() -> None:
    registry = ComponentHealthRegistry()
    broker = FrameBroker([], startup_timeout_s=5.0, health_registry=registry)
    started = time.monotonic()
    with pytest.raises(FrameUnavailableError, match="exhausted"):
        broker.current()
    assert time.monotonic() - started < 0.5
    assert broker.terminal_kind == "exhausted"
    assert registry.snapshot().by_name("collector").state is ComponentState.FAILED
    broker.join(timeout=1.0)


def test_blocked_startup_times_out_but_can_be_released_for_shutdown() -> None:
    release = threading.Event()

    def blocked() -> Iterator[Frame]:
        release.wait()
        yield _frame_at(1.0)

    broker = FrameBroker(blocked(), startup_timeout_s=0.05)
    with pytest.raises(FrameUnavailableError, match="startup timeout"):
        broker.current()
    broker.stop()
    release.set()
    broker.join(timeout=1.0)


def test_failure_before_first_frame_is_typed_sanitized_and_persistent() -> None:
    source = ControlledSource()
    registry = ComponentHealthRegistry()
    broker = FrameBroker(source, health_registry=registry)
    broker.start()
    source.fail("TOKEN=secret /private/path")
    with pytest.raises(FrameProducerError, match="failed before") as first:
        broker.current()
    assert "secret" not in str(first.value)
    for _ in range(2):
        with pytest.raises(FrameProducerError, match="frame producer failed"):
            broker.join(timeout=1.0)
    health = registry.snapshot().by_name("collector")
    assert health.state is ComponentState.FAILED
    assert health.error is not None
    assert "secret" not in health.error.message


def test_failure_after_valid_frame_preserves_last_valid_and_health_failure() -> None:
    source = ControlledSource()
    registry = ComponentHealthRegistry()
    broker = FrameBroker(source, health_registry=registry)
    broker.start()
    source.frame(1.0)
    _wait_for(lambda: broker.current().ts == 1.0)
    source.fail("boom")
    _wait_for(lambda: broker.terminal_kind == "failed")
    assert broker.current().ts == 1.0
    assert registry.snapshot().by_name("collector").state is ComponentState.FAILED
    with pytest.raises(FrameProducerError):
        broker.join(timeout=1.0)


def test_history_cursor_and_eviction_gap_are_explicit() -> None:
    broker = FrameBroker([_frame_at(float(i)) for i in range(6)], history_size=3)
    broker.start()
    _wait_for(lambda: broker.terminal_kind == "exhausted")
    batch = broker.stream(limit=3, cursor=0)
    assert [seq for seq, _ in batch.entries] == [3, 4, 5]
    assert batch.gap is True
    assert (batch.oldest_seq, batch.latest_seq, batch.next_cursor) == (3, 5, 5)
    tail = broker.stream(limit=2)
    assert [seq for seq, _ in tail.entries] == [4, 5]
    assert tail.gap is False
    broker.join(timeout=1.0)


@pytest.mark.parametrize(
    "payload,message",
    [
        ({"op": "stream", "limit": True}, "integer"),
        ({"op": "stream", "limit": 0}, "between"),
        ({"op": "stream", "limit": 1001}, "between"),
        ({"op": "stream", "cursor": 1.5}, "integer"),
        ({"op": "stream", "cursor": -2}, "at least"),
        ({"op": "stream", "extra": 1}, "invalid stream"),
        ({"op": "current", "extra": 1}, "invalid current"),
    ],
)
def test_request_validation_is_strict_and_bounded(payload: dict, message: str) -> None:
    broker = FrameBroker([_frame_at(1.0)])
    response = broker.responses(payload)
    assert response[0]["type"] == "error"
    assert message in response[0]["error"]
    _stop(broker)


def test_two_readers_observe_same_published_sequence() -> None:
    source = ControlledSource()
    broker = FrameBroker(source)
    broker.start()
    source.frame(12.0)
    _wait_for(lambda: broker.current().ts == 12.0)
    barrier = threading.Barrier(3)
    results: list[tuple[int, float]] = []

    def read() -> None:
        barrier.wait()
        seq, frame = broker.current_entry()
        results.append((seq, frame.ts))

    readers = [threading.Thread(target=read) for _ in range(2)]
    for reader in readers:
        reader.start()
    barrier.wait()
    for reader in readers:
        reader.join()
    try:
        assert results == [(0, 12.0), (0, 12.0)]
    finally:
        _stop(broker, source)


def test_client_cursor_batch_does_not_replay_tail(tmp_path: Path) -> None:
    source = ControlledSource()
    broker = FrameBroker(source, history_size=3)
    path = tmp_path / "topos.sock"
    server = serve_unix_socket(path, broker)
    thread = threading.Thread(target=server.serve_forever)
    broker.start()
    thread.start()
    try:
        source.frame(1.0)
        _wait_for(lambda: broker.current().ts == 1.0)
        client = DaemonClient(path)
        first = client.stream_batch(limit=3)
        assert [frame.ts for frame in first.frames] == [1.0]
        again = client.stream_batch(limit=3, cursor=first.next_cursor)
        assert again.frames == ()
        source.frame(2.0)
        _wait_for(lambda: broker.current().ts == 2.0)
        advanced = client.stream_batch(limit=3, cursor=first.next_cursor)
        assert [frame.ts for frame in advanced.frames] == [2.0]
        assert advanced.gap is False
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)
        _stop(broker, source)


def test_polling_helper_raises_on_evicted_cursor(tmp_path: Path) -> None:
    source = ControlledSource()
    broker = FrameBroker(source, history_size=1)
    path = tmp_path / "topos.sock"
    server = serve_unix_socket(path, broker)
    thread = threading.Thread(target=server.serve_forever)
    broker.start()
    thread.start()
    try:
        source.frame(1.0)
        _wait_for(lambda: broker.current().ts == 1.0)
        frames = stream_frames(path, limit=1, poll_interval_s=0.01)
        assert next(frames).ts == 1.0
        source.frame(2.0)
        source.frame(3.0)
        _wait_for(lambda: broker.current().ts == 3.0)
        with pytest.raises(DaemonHistoryGapError, match="evicted"):
            next(frames)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)
        _stop(broker, source)


def test_server_rejects_excess_slow_clients(tmp_path: Path) -> None:
    broker = FrameBroker([_frame_at(1.0)])
    path = tmp_path / "topos.sock"
    server = serve_unix_socket(path, broker, request_timeout_s=1.0, max_clients=1)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    first = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    second = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        first.connect(str(path))
        _wait_for(lambda: server._client_slots._value == 0)  # type: ignore[attr-defined]
        second.connect(str(path))
        response = second.recv(4096)
        assert json.loads(response)["error"] == "server busy"
    finally:
        first.close()
        second.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)
        _stop(broker)
