"""Remaining fail-closed protocol boundaries for :mod:`topos.daemon.client`.

These tests use the client's parser seams directly.  A socket is transport
plumbing; the contract under test is that malformed daemon bytes never become
typed client results.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from conftest import fixture_frame
from topos.daemon.client import (
    DaemonClient,
    DaemonHistoryResult,
    DaemonProtocolError,
    DaemonResponseError,
)
from topos.model import frame_to_jsonable


def _client() -> DaemonClient:
    return DaemonClient(Path("/fixture/daemon.sock"))


def _line(payload: object) -> str:
    return json.dumps(payload) + "\n"


class _InvalidUtf8Reader:
    def readline(self, _size: int) -> str:
        raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")


def test_envelope_reader_maps_decode_failure_to_typed_protocol_error() -> None:
    """A text-decoder failure is a daemon protocol failure, never a raw UnicodeError."""
    with pytest.raises(DaemonProtocolError, match="invalid UTF-8 envelope data"):
        _client()._read_envelope_response(_InvalidUtf8Reader(), "request-1")


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({"schema_version": 1, "capability": "health-v1", "components": []}, "invalid components array"),
        (
            {
                "schema_version": 1,
                "capability": "health-v1",
                "components": [
                    {
                        "name": "collector",
                        "state": "healthy",
                        "detail": "ready",
                        "last_attempt_ts": None,
                        "last_success_ts": None,
                        "consecutive_failures": 0,
                        "state_change_count": 0,
                        "error": [],
                    },
                    {
                        "name": "bpf_snapshot_bridge",
                        "state": "disabled",
                        "detail": "disabled",
                        "last_attempt_ts": None,
                        "last_success_ts": None,
                        "consecutive_failures": 0,
                        "state_change_count": 0,
                        "error": None,
                    },
                    {
                        "name": "paddr_lifecycle",
                        "state": "disabled",
                        "detail": "disabled",
                        "last_attempt_ts": None,
                        "last_success_ts": None,
                        "consecutive_failures": 0,
                        "state_change_count": 0,
                        "error": None,
                    },
                ],
            },
            "invalid component error",
        ),
    ],
)
def test_health_parser_rejects_invalid_component_structure(
    payload: dict[str, object], message: str
) -> None:
    with pytest.raises(DaemonProtocolError, match=message):
        _client()._read_health(io.StringIO(_line({"type": "health", **payload})))


def test_health_string_validator_rejects_non_string_values() -> None:
    with pytest.raises(DaemonProtocolError, match="invalid or oversized detail"):
        _client()._bounded_health_string(None, "detail", 64)


@pytest.mark.parametrize(
    ("lines", "error_type", "message"),
    [
        ("\nnot-json\n", DaemonProtocolError, "malformed JSON on line 2"),
        (_line({"type": "error", "error": "maintenance"}), DaemonResponseError, "maintenance"),
        (_line({"type": "end", "count": True, "gap": False}), DaemonProtocolError, "invalid stream end count"),
        (
            _line({"type": "frame", "seq": 7, "frame": frame_to_jsonable(fixture_frame())})
            + _line(
                {
                    "type": "end",
                    "count": 1,
                    "gap": False,
                    "oldest_seq": 7,
                    "latest_seq": 7,
                    "next_cursor": 6,
                }
            ),
            DaemonProtocolError,
            "invalid next cursor",
        ),
        ("", DaemonProtocolError, "closed the connection without an end response"),
    ],
)
def test_stream_reader_fails_closed_on_untrusted_terminal_data(
    lines: str, error_type: type[Exception], message: str
) -> None:
    with pytest.raises(error_type, match=message):
        _client()._read_stream_batch(io.StringIO(lines))


@pytest.mark.parametrize("value", [True, -1])
def test_optional_sequence_rejects_boolean_and_negative_metadata(value: object) -> None:
    with pytest.raises(DaemonProtocolError, match="invalid oldest_seq"):
        _client()._optional_sequence(value, "oldest_seq")


def test_history_result_frames_preserves_entry_order() -> None:
    frame = fixture_frame()
    result = DaemonHistoryResult(
        entries=((4, frame), (9, frame)),
        oldest_seq=4,
        latest_seq=9,
        next_cursor=9,
        gap=False,
        metrics_meta={},
    )

    assert result.frames == (frame, frame)
