from __future__ import annotations

import threading

import pytest

from conftest import fixture_frame
from topos.daemon.broker import (
    FrameBroker,
    FrameBrokerError,
    FrameProducerError,
)
from topos.model import Frame


def _frame_at(ts: float) -> Frame:
    base = fixture_frame()
    return Frame(base.schema_version, ts, base.interval_s, base.host, base.entities)


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"history_size": True}, "history_size must be an integer"),
        ({"history_size": 0}, "history_size must be at least 1"),
        ({"startup_timeout_s": True}, "startup_timeout_s must be a number"),
        ({"startup_timeout_s": 0.0}, "startup_timeout_s must be greater than 0"),
        ({"startup_timeout_s": 61.0}, "at most 60 seconds"),
    ],
)
def test_constructor_rejects_invalid_retention_and_startup_bounds(kwargs: dict[str, object], message: str) -> None:
    """Bad lifecycle settings fail before a producer can be started."""
    with pytest.raises((TypeError, ValueError), match=message):
        FrameBroker([], **kwargs)  # type: ignore[arg-type]


def test_stop_before_start_prevents_restart() -> None:
    """A stopped broker cannot silently create a new producer later."""
    broker = FrameBroker([_frame_at(1.0)])

    broker.stop()

    with pytest.raises(FrameBrokerError, match="cannot restart after stop"):
        broker.start()
    assert broker.terminal_kind is None


def test_stop_callback_failure_preserves_stopped_state_and_wakes_waiters() -> None:
    """Cleanup aids cannot turn shutdown into an untyped callback failure."""
    release = threading.Event()

    def blocked_source():
        release.wait()
        return
        yield _frame_at(1.0)  # pragma: no cover - establishes generator shape

    def failing_callback() -> None:
        release.set()
        raise RuntimeError("cleanup failed")

    broker = FrameBroker(blocked_source(), stop_callback=failing_callback)
    broker.start()
    broker.stop()
    broker.join(timeout=1.0)

    # The callback releases the iterator, which can finish naturally; its
    # exception must not prevent a clean terminal state from being recorded.
    assert broker.terminal_kind == "exhausted"
    assert broker.producer_alive is False


def test_non_frame_producer_value_becomes_public_typed_failure() -> None:
    """Malformed producer output is never exposed as an implementation error."""
    broker = FrameBroker([object()])  # type: ignore[list-item]

    with pytest.raises(FrameProducerError, match="failed before publishing"):
        broker.current()
    assert broker.terminal_kind == "failed"
    with pytest.raises(FrameProducerError, match="frame producer failed"):
        broker.join(timeout=1.0)


def test_empty_stream_and_window_preserve_explicit_cursor_contract() -> None:
    """No-history reads remain safe and report their distinct cursor semantics."""
    broker = FrameBroker([])

    stream = broker.stream(limit=2, cursor=-1)
    window = broker.stream_window(since_ts=1.0, until_ts=1.0, limit=2)

    assert stream.entries == ()
    assert (stream.oldest_seq, stream.latest_seq, stream.next_cursor, stream.gap) == (None, None, -1, False)
    assert window.entries == ()
    assert (window.oldest_seq, window.latest_seq, window.next_cursor, window.gap) == (None, None, None, False)
    broker.join(timeout=1.0)


def test_window_validation_rejects_non_finite_and_reversed_bounds() -> None:
    """Window validation fails before producer lifecycle side effects."""
    broker = FrameBroker([_frame_at(1.0)])

    with pytest.raises(ValueError, match="since_ts must be finite"):
        broker.stream_window(since_ts=float("nan"))
    with pytest.raises(ValueError, match="since_ts must not exceed until_ts"):
        broker.stream_window(since_ts=2.0, until_ts=1.0)


def test_current_response_preserves_typed_broker_error_as_safe_wire_error() -> None:
    """Protocol callers get the public lifecycle message, not an exception."""
    broker = FrameBroker([])

    response = broker.responses({"op": "current"})

    assert response == [{"type": "error", "error": "frame source exhausted before publishing a frame"}]
    broker.join(timeout=1.0)
