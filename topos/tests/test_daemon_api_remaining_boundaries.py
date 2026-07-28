"""Remaining defensive DaemonApi branches exposed as direct API contracts.

These cases avoid a socket server deliberately: they exercise the small
defensive seams directly with fakes, so a transport failure cannot be mistaken
for a daemon/API semantic failure.
"""

from __future__ import annotations

import socket
from types import SimpleNamespace

import pytest

from conftest import fixture_frame
from topos.daemon.api import (
    DaemonApi,
    ErrorCode,
    PeerCredentials,
    _ApiError,
    _EnvelopeHandler,
    _validate_cursor_envelope,
    _validate_envelope_finite,
)
from topos.daemon.broker import FrameBroker


def _api() -> DaemonApi:
    return DaemonApi(FrameBroker([fixture_frame()]))


@pytest.mark.parametrize(
    ("peer", "expected"),
    [
        (PeerCredentials(pid=9, uid=None, gid=None), {"pid": 9}),
        (PeerCredentials(pid=None, uid=None, gid=12), {"gid": 12}),
    ],
)
def test_peer_audit_shape_preserves_each_available_identity_field(
    peer: PeerCredentials, expected: dict[str, int]
) -> None:
    """Partial SO_PEERCRED data stays truthful instead of being fabricated."""
    assert peer.to_audit() == expected


def test_internal_dispatch_and_per_op_param_guards_remain_typed() -> None:
    """Defensive internal entry points must retain the public error taxonomy."""
    api = _api()

    with pytest.raises(_ApiError) as unknown:
        api._dispatch("removed-op", {}, None)
    assert unknown.value.code is ErrorCode.UNKNOWN_OP

    for operation, request in [
        (api._op_history, {"surplus": True}),
        (api._op_entity, {"surplus": True}),
    ]:
        with pytest.raises(_ApiError) as extra:
            operation(request)
        assert extra.value.code is ErrorCode.UNKNOWN_FIELD


def test_optional_cursor_and_timestamp_helpers_keep_none_and_reject_booleans() -> None:
    """`None` is the sentinel; bool must never silently become 0 or 1."""
    assert _validate_cursor_envelope(None) is None
    with pytest.raises(_ApiError) as cursor:
        _validate_cursor_envelope(True)
    assert cursor.value.code is ErrorCode.MALFORMED_CURSOR

    with pytest.raises(_ApiError) as timestamp:
        _validate_envelope_finite(False, "since_ts")
    assert timestamp.value.code is ErrorCode.INVALID_TYPE


class _Connection:
    def __init__(self) -> None:
        self.timeout: float | None = None

    def settimeout(self, timeout: float) -> None:
        self.timeout = timeout


class _ReadFailure:
    def __init__(self, error: BaseException) -> None:
        self._error = error

    def readline(self, _limit: int) -> bytes:
        raise self._error


class _WriteFailure:
    def write(self, _payload: bytes) -> None:
        raise OSError("client closed")


def test_envelope_handler_timeout_answers_once_but_read_oserror_is_silent() -> None:
    """Timeout receives a typed reply; a disconnected reader must not escape."""
    api = _api()

    for error, expected in [
        (socket.timeout("late"), [{"type": "error", "error": "request timed out"}]),
        (OSError("closed"), []),
    ]:
        handler = object.__new__(_EnvelopeHandler)
        handler.connection = _Connection()
        handler.rfile = _ReadFailure(error)
        handler.server = SimpleNamespace(api=api)
        writes: list[dict[str, object]] = []
        handler._write = writes.append

        handler.handle()

        assert handler.connection.timeout == api.limits.request_timeout_s
        assert writes == expected


def test_envelope_writer_swallows_disconnected_client() -> None:
    """A peer close during response serialization is a transport event, not a crash."""
    handler = object.__new__(_EnvelopeHandler)
    handler.wfile = _WriteFailure()

    handler._write({"ok": True, "result": {"bounded": "response"}})
