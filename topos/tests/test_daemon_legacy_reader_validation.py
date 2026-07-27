"""Protocol-boundary tests for legacy line-oriented daemon readers."""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from conftest import fixture_frame
from topos.daemon.client import DaemonClient, DaemonProtocolError, DaemonResponseError
from topos.model import frame_to_jsonable


def _client() -> DaemonClient:
    return DaemonClient(Path("/daemon.sock"))


def _line(payload: object) -> str:
    return json.dumps(payload) + "\n"


def test_read_frames_accepts_frame_then_matching_end() -> None:
    reader = io.StringIO(
        _line({"type": "frame", "frame": frame_to_jsonable(fixture_frame())})
        + _line({"type": "end", "count": 1})
    )

    frames = _client()._read_frames(reader)

    assert len(frames) == 1
    assert frames[0].schema_version == fixture_frame().schema_version


@pytest.mark.parametrize(
    "lines, message",
    [
        ("\n", "closed the connection without an end response"),
        (_line([]), "non-object response"),
        (_line({"type": "frame", "frame": {}}), "invalid frame"),
        (_line({"type": "end", "count": "1"}), "invalid end count"),
        (_line({"type": "end", "count": 2}), "ended with count 2"),
        (_line({"type": "unexpected"}), "unsupported response type"),
    ],
)
def test_read_frames_rejects_malformed_protocol(lines: str, message: str) -> None:
    with pytest.raises(DaemonProtocolError, match=message):
        _client()._read_frames(io.StringIO(lines))


def test_read_frames_surfaces_daemon_error() -> None:
    with pytest.raises(DaemonResponseError, match="service unavailable"):
        _client()._read_frames(io.StringIO(_line({"type": "error", "message": "service unavailable"})))


def test_read_stream_batch_accepts_ordered_entries_and_metadata() -> None:
    frame = frame_to_jsonable(fixture_frame())
    reader = io.StringIO(
        _line({"type": "frame", "seq": 4, "frame": frame})
        + _line({"type": "frame", "seq": 5, "frame": frame})
        + _line(
            {
                "type": "end",
                "count": 2,
                "gap": False,
                "oldest_seq": 1,
                "latest_seq": 5,
                "next_cursor": 5,
            }
        )
    )

    batch = _client()._read_stream_batch(reader)

    assert [seq for seq, _ in batch.entries] == [4, 5]
    assert (batch.oldest_seq, batch.latest_seq, batch.next_cursor, batch.gap) == (1, 5, 5, False)


@pytest.mark.parametrize(
    "lines, message",
    [
        (_line([]), "non-object response"),
        (_line({"type": "frame", "seq": True, "frame": {}}), "invalid frame sequence"),
        (
            _line({"type": "frame", "seq": 1, "frame": frame_to_jsonable(fixture_frame())})
            + _line({"type": "frame", "seq": 1, "frame": frame_to_jsonable(fixture_frame())}),
            "non-increasing frame sequences",
        ),
        (_line({"type": "frame", "seq": 1, "frame": {}}), "invalid frame"),
        (_line({"type": "unknown"}), "unsupported response type"),
        (_line({"type": "end", "count": 0, "gap": "false"}), "invalid stream gap marker"),
        (_line({"type": "end", "count": 0, "gap": False, "oldest_seq": 4, "latest_seq": None, "next_cursor": None}), "invalid stream history bounds"),
    ],
)
def test_read_stream_batch_rejects_malformed_protocol(lines: str, message: str) -> None:
    with pytest.raises(DaemonProtocolError, match=message):
        _client()._read_stream_batch(io.StringIO(lines))
