"""Boundary contracts for daemon-client history and legacy-current reads.

The envelope transport is deliberately replaced at its public seam so these
tests exercise only the client-side invariants that prevent malformed daemon
results from reaching callers.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from conftest import fixture_frame
from topos.daemon.client import DaemonClient, DaemonProtocolError
from topos.model import Frame, frame_to_jsonable


def _client_with_history_result(
    monkeypatch: pytest.MonkeyPatch, result: dict[str, object]
) -> DaemonClient:
    client = DaemonClient(Path("/daemon.sock"))
    monkeypatch.setattr(DaemonClient, "_request_envelope", lambda *_args, **_kwargs: result)
    return client


def _valid_history_result() -> dict[str, object]:
    return {
        "frames": [{"seq": 4, "frame": frame_to_jsonable(fixture_frame())}],
        "oldest_seq": 1,
        "latest_seq": 4,
        "next_cursor": 4,
        "gap": False,
        "metrics_meta": {"cpu": {"sensitivity": "public"}},
    }


@pytest.mark.parametrize(
    "mutate, message",
    [
        (lambda result: result.__setitem__("gap", "false"), "invalid gap marker"),
        (lambda result: result.__setitem__("latest_seq", None), "invalid history bounds"),
        (lambda result: result.update(oldest_seq=5, latest_seq=4), "invalid history bounds"),
        (lambda result: result.__setitem__("next_cursor", 3), "invalid next cursor"),
    ],
)
def test_request_history_rejects_inconsistent_success_envelope(
    monkeypatch: pytest.MonkeyPatch,
    mutate: Callable[[dict[str, object]], None],
    message: str,
) -> None:
    result = _valid_history_result()
    mutate(result)
    client = _client_with_history_result(monkeypatch, result)

    with pytest.raises(DaemonProtocolError, match=message):
        client.request_history()


@pytest.mark.parametrize("frames", [[], [fixture_frame(), fixture_frame()]])
def test_current_frame_rejects_any_count_other_than_one(
    monkeypatch: pytest.MonkeyPatch, frames: list[Frame]
) -> None:
    client = DaemonClient(Path("/daemon.sock"))
    monkeypatch.setattr(DaemonClient, "request_frames", lambda *_args, **_kwargs: frames)

    with pytest.raises(DaemonProtocolError, match="expected exactly 1"):
        client.current_frame()
