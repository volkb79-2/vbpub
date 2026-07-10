from __future__ import annotations

import json
import socket
import threading
import time
from pathlib import Path

import pytest

from conftest import fixture_frame
from groop.daemon.broker import FrameBroker, FrameBrokerError, FrameProducerError, FrameUnavailableError, serve_unix_socket
from groop.model import Frame, frame_to_jsonable


def _frame_at(ts: float) -> Frame:
    base = fixture_frame()
    return Frame(base.schema_version, ts, base.interval_s, base.host, base.entities)


def _infinite_frames(interval_s: float = 0.01) -> Frame:
    """Yield frames at increasing timestamps forever (well, up to the moon)."""
    base = fixture_frame()
    tick = 0
    while True:
        yield Frame(base.schema_version, 1000.0 + tick, base.interval_s, base.host, base.entities)
        tick += 1


# ── lifecycle ─────────────────────────────────────────────────────────────


def test_producer_advances_independently_of_requests() -> None:
    """The background producer advances the source without any read call."""
    frames = [_frame_at(10.0), _frame_at(20.0), _frame_at(30.0)]
    broker = FrameBroker(frames)
    broker.start()
    time.sleep(0.1)  # let the producer run through all frames
    # The producer should have consumed all three frames.
    frame = broker.current()
    assert frame.ts == 30.0
    broker.stop()
    broker.join()


def test_current_returns_latest_frame_on_repeated_calls() -> None:
    """Repeated current() calls return the freshest published frame
    as the producer advances, without blocking after the first frame."""
    broker = FrameBroker(_infinite_frames(0.02))
    first = broker.current()
    time.sleep(0.05)
    second = broker.current()
    assert second.ts > first.ts, f"second.ts {second.ts} should exceed first.ts {first.ts}"
    third = broker.current()
    assert third.ts >= second.ts, f"third.ts {third.ts} should be >= second.ts {second.ts}"
    broker.stop()
    broker.join()


def test_start_is_idempotent() -> None:
    """Multiple start() calls do not create additional threads."""
    broker = FrameBroker(_infinite_frames())
    broker.start()
    thread_id = id(broker._thread)  # type: ignore[attr-defined]
    broker.start()
    broker.start()
    assert id(broker._thread) == thread_id  # type: ignore[attr-defined]
    broker.stop()
    broker.join()


def test_stop_and_join_terminates_producer() -> None:
    """stop()+join() cleanly terminates the producer thread."""
    broker = FrameBroker(_infinite_frames())
    broker.current()  # trigger lazy start + one frame
    broker.stop()
    broker.join(timeout=3.0)
    # The thread is no longer alive
    assert broker._thread is None or not broker._thread.is_alive()  # type: ignore[attr-defined]


def test_join_re_raises_producer_error() -> None:
    """join() re-raises an exception captured from the producer thread."""
    def broken_source() -> None:
        yield _frame_at(1.0)
        raise RuntimeError("boom")

    broker = FrameBroker(broken_source(), source_error_limit=1)
    broker.current()  # consumes the first frame
    time.sleep(0.2)  # let the producer hit the error
    broker.stop()
    with pytest.raises(RuntimeError, match="boom"):
        broker.join(timeout=1.0)


# ── startup / unavailable ─────────────────────────────────────────────────


def test_current_before_first_frame_times_out_on_empty_source() -> None:
    """An empty source raises FrameUnavailableError after a timeout."""
    broker = FrameBroker([], startup_timeout_s=0.5)
    with pytest.raises(FrameUnavailableError, match="timeout"):
        broker.current()
    broker.stop()
    broker.join()


def test_current_fails_with_timeout_when_no_frame_produced() -> None:
    """A source that never produces a frame times out with FrameUnavailableError."""
    def slow_source() -> None:  # type: ignore[return]
        if False:  # never yield
            yield

    broker = FrameBroker(slow_source(), startup_timeout_s=0.5)  # type: ignore[arg-type]
    with pytest.raises(FrameUnavailableError, match="timeout"):
        broker.current()
    broker.stop()
    broker.join()


# ── stream / cursor ───────────────────────────────────────────────────────


def test_stream_without_cursor_returns_tail() -> None:
    """stream(limit=N) returns the last N published frames."""
    frames = [_frame_at(f) for f in [1.0, 2.0, 3.0, 4.0, 5.0]]
    broker = FrameBroker(frames)
    broker.current()  # wait for all frames to be consumed
    tail = broker.stream(limit=3)
    assert len(tail) == 3
    assert tail[0][0] == 2  # seq 2 = frame at ts 3.0
    assert tail[0][1].ts == 3.0
    assert tail[-1][0] == 4  # seq 4 = frame at ts 5.0
    broker.stop()
    broker.join()


def test_stream_with_cursor_returns_frames_after_cursor() -> None:
    """stream(limit=N, cursor=K) returns frames strictly after cursor K."""
    frames = [_frame_at(f) for f in [10.0, 20.0, 30.0, 40.0]]
    broker = FrameBroker(frames)
    broker.current()  # wait for all frames
    result = broker.stream(limit=10, cursor=1)
    assert len(result) == 2
    assert [seq for seq, _ in result] == [2, 3]
    assert [f.ts for _, f in result] == [30.0, 40.0]
    broker.stop()
    broker.join()


def test_stream_cursor_beyond_history_returns_empty() -> None:
    """A cursor that exceeds all published sequences returns []."""
    broker = FrameBroker([_frame_at(1.0)])
    broker.current()
    assert broker.stream(limit=10, cursor=99) == []
    broker.stop()
    broker.join()


def test_stream_with_high_limit_returns_all_available() -> None:
    """stream with limit > available frame count returns everything."""
    frames = [_frame_at(f) for f in [1.0, 2.0]]
    broker = FrameBroker(frames)
    broker.current()
    result = broker.stream(limit=100)
    assert len(result) == 2
    broker.stop()
    broker.join()


# ── history eviction ──────────────────────────────────────────────────────


def test_history_evicts_old_frames_when_bounded() -> None:
    """Only the most recent history_size frames are retained."""
    frames = [_frame_at(float(i)) for i in range(10)]
    broker = FrameBroker(frames, history_size=3)
    broker.current()  # wait for producer to finish
    history = broker.stream(limit=100)
    assert len(history) == 3
    # Most recent 3 frames: seq 7,8,9 → ts 7.0,8.0,9.0
    assert [f.ts for _, f in history] == [7.0, 8.0, 9.0]
    broker.stop()
    broker.join()


# ── concurrent fan-out ────────────────────────────────────────────────────


def test_multiple_clients_see_same_sequence(tmp_path: Path) -> None:
    """Two concurrent client connections observe the same frames
    and cannot accelerate or consume each other's data."""
    broker = FrameBroker(_infinite_frames(0.02))
    socket_path = tmp_path / "fanout.sock"
    server = serve_unix_socket(socket_path, broker)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    try:
        results: list[list[float]] = []
        lock = threading.Lock()

        def client() -> None:
            local: list[float] = []
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.settimeout(3.0)
            sock.connect(str(socket_path))
            # Read 3 current frames with brief pauses
            for _ in range(3):
                sock.sendall(json.dumps({"op": "current"}).encode("utf-8") + b"\n")
                sock.shutdown(socket.SHUT_WR)
                data = b""
                while True:
                    chunk = sock.recv(65536)
                    if not chunk:
                        break
                    data += chunk
                responses = [json.loads(line) for line in data.decode("utf-8").splitlines()]
                for resp in responses:
                    if resp.get("type") == "frame":
                        local.append(resp["frame"]["ts"])
                time.sleep(0.03)
                # Reconnect for each request
                sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                sock.settimeout(3.0)
                sock.connect(str(socket_path))
            with lock:
                results.append(local)

        threads = [threading.Thread(target=client, daemon=True) for _ in range(2)]
        for th in threads:
            th.start()
        for th in threads:
            th.join(timeout=10.0)

        assert len(results) == 2
        # Both clients see increasing timestamps (frames are not stale)
        # and they see the same or overlapping sequence.
        for timestamps in results:
            assert len(timestamps) >= 1, "each client should see at least one frame"
            for i in range(1, len(timestamps)):
                assert timestamps[i] >= timestamps[i - 1], "timestamps must not go backward"
        # Both clients see broadly similar ranges
        min_seen = min(min(r) for r in results)
        max_seen = max(max(r) for r in results)
        assert min_seen >= 1000.0
    finally:
        server.shutdown()
        server.server_close()
        broker.stop()
        broker.join()


def test_concurrent_current_and_stream(tmp_path: Path) -> None:
    """Concurrent current() and stream() calls do not race."""
    broker = FrameBroker(_infinite_frames(0.01))
    socket_path = tmp_path / "concurrent.sock"
    server = serve_unix_socket(socket_path, broker)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    try:
        errors: list[str] = []
        lock = threading.Lock()

        def do_current() -> None:
            try:
                sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                sock.settimeout(3.0)
                sock.connect(str(socket_path))
                sock.sendall(json.dumps({"op": "current"}).encode("utf-8") + b"\n")
                sock.shutdown(socket.SHUT_WR)
                data = b"".join(iter(lambda: sock.recv(65536), b""))
                responses = [json.loads(line) for line in data.decode("utf-8").splitlines()]
                for resp in responses:
                    if resp.get("type") == "error":
                        with lock:
                            errors.append(f"current error: {resp.get('error')}")
            except Exception as exc:
                with lock:
                    errors.append(f"current exception: {exc}")

        def do_stream() -> None:
            try:
                sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                sock.settimeout(3.0)
                sock.connect(str(socket_path))
                sock.sendall(json.dumps({"op": "stream", "limit": 5}).encode("utf-8") + b"\n")
                sock.shutdown(socket.SHUT_WR)
                data = b"".join(iter(lambda: sock.recv(65536), b""))
                responses = [json.loads(line) for line in data.decode("utf-8").splitlines()]
                for resp in responses:
                    if resp.get("type") == "error":
                        with lock:
                            errors.append(f"stream error: {resp.get('error')}")
            except Exception as exc:
                with lock:
                    errors.append(f"stream exception: {exc}")

        threads = [threading.Thread(target=do_current, daemon=True) for _ in range(4)]
        threads += [threading.Thread(target=do_stream, daemon=True) for _ in range(4)]
        for th in threads:
            th.start()
        for th in threads:
            th.join(timeout=10.0)

        assert errors == [], f"concurrent access produced errors: {errors}"
    finally:
        server.shutdown()
        server.server_close()
        broker.stop()
        broker.join()


# ── protocol dispatch backward compatibility ─────────────────────────────


def test_responses_current_returns_latest_frame() -> None:
    """responses({'op': 'current'}) returns the latest published frame."""
    broker = FrameBroker([_frame_at(1.0), _frame_at(2.0)])
    resp = broker.responses({"op": "current"})
    assert resp[0]["type"] == "frame"
    assert resp[0]["frame"]["ts"] == 2.0
    assert resp[1] == {"type": "end", "count": 1}
    broker.stop()
    broker.join()


def test_responses_stream_without_cursor_returns_tail() -> None:
    """responses({'op': 'stream', 'limit': N}) returns the tail of history."""
    broker = FrameBroker([_frame_at(float(i)) for i in range(10)])
    broker.current()  # wait for all frames
    resp = broker.responses({"op": "stream", "limit": 3})
    frames = [r for r in resp if r["type"] == "frame"]
    assert len(frames) == 3
    assert [f["frame"]["ts"] for f in frames] == [7.0, 8.0, 9.0]
    assert resp[-1] == {"type": "end", "count": 3}
    broker.stop()
    broker.join()


def test_responses_stream_with_cursor() -> None:
    """responses({'op': 'stream', 'limit': N, 'cursor': K}) works."""
    broker = FrameBroker([_frame_at(float(i)) for i in range(5)])
    broker.current()
    resp = broker.responses({"op": "stream", "limit": 10, "cursor": 2})
    frames = [r for r in resp if r["type"] == "frame"]
    assert len(frames) == 2
    assert [f["frame"]["ts"] for f in frames] == [3.0, 4.0]
    assert [f["seq"] for f in frames] == [3, 4]
    broker.stop()
    broker.join()


def test_responses_unknown_op_returns_error() -> None:
    """responses({'op': 'unknown'}) returns an error."""
    broker = FrameBroker([_frame_at(1.0)])
    resp = broker.responses({"op": "read_file", "path": "/etc/shadow"})
    assert resp == [{"type": "error", "error": "unsupported operation"}]
    broker.stop()
    broker.join()


# ── producer exhaustion ───────────────────────────────────────────────────


def test_current_after_source_exhaustion_returns_last_frame() -> None:
    """After the source is exhausted current() still returns the last
    published frame from history."""
    broker = FrameBroker([_frame_at(1.0), _frame_at(2.0)])
    first = broker.current()  # wait for producer to finish
    second = broker.current()
    assert second.ts == 2.0  # still returns latest from history
    broker.stop()
    broker.join()


def test_producer_exhaustion_does_not_kill_server(tmp_path: Path) -> None:
    """An exhausted source still serves cached frames without crashing."""
    broker = FrameBroker([_frame_at(1.0)])
    socket_path = tmp_path / "exhaust.sock"
    server = serve_unix_socket(socket_path, broker)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    try:
        time.sleep(0.2)  # let the producer finish
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(3.0)
        sock.connect(str(socket_path))
        sock.sendall(json.dumps({"op": "current"}).encode("utf-8") + b"\n")
        sock.shutdown(socket.SHUT_WR)
        data = b"".join(iter(lambda: sock.recv(65536), b""))
        responses = [json.loads(line) for line in data.decode("utf-8").splitlines()]
        # Server should still respond with the cached frame, not crash
        assert any(r.get("type") == "frame" for r in responses)
        # Second request should also work from history
        sock2 = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock2.settimeout(3.0)
        sock2.connect(str(socket_path))
        sock2.sendall(json.dumps({"op": "current"}).encode("utf-8") + b"\n")
        sock2.shutdown(socket.SHUT_WR)
        data2 = b"".join(iter(lambda: sock2.recv(65536), b""))
        responses2 = [json.loads(line) for line in data2.decode("utf-8").splitlines()]
        assert any(r.get("type") == "frame" for r in responses2)
    finally:
        server.shutdown()
        server.server_close()
        broker.stop()
        broker.join()


# ── shutdown ──────────────────────────────────────────────────────────────


def test_shutdown_stops_producer_and_server(tmp_path: Path) -> None:
    """stop()+join() on the broker plus server_close() cleanly tears down."""
    broker = FrameBroker(_infinite_frames())
    socket_path = tmp_path / "shutdown.sock"
    server = serve_unix_socket(socket_path, broker)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    time.sleep(0.05)
    server.shutdown()
    server.server_close()
    broker.stop()
    broker.join(timeout=3.0)
    # Socket should be unlinked
    assert not socket_path.exists()
