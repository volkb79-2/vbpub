"""Boundary tests for versioned daemon result validation.

The transport/envelope machinery is covered elsewhere.  These tests pin the
public read methods' promise that malformed successful envelopes are rejected
before callers receive an invalid typed result.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from conftest import fixture_frame
from topos.daemon.client import DaemonClient, DaemonProtocolError
from topos.model import frame_to_jsonable


def _client_with_result(monkeypatch: pytest.MonkeyPatch, result: dict[str, object]) -> DaemonClient:
    client = DaemonClient(Path("/daemon.sock"))
    # DaemonClient is frozen, so replace the transport seam on its class rather
    # than trying to mutate the instance.
    monkeypatch.setattr(DaemonClient, "_request_envelope", lambda *_args, **_kwargs: result)
    return client


def _metrics_meta() -> dict[str, dict[str, object]]:
    return {"cpu": {"sensitivity": "public"}}


def _frame_payload() -> dict[str, object]:
    return frame_to_jsonable(fixture_frame())


@pytest.mark.parametrize("seq", [True, -1, "0"])
def test_request_current_rejects_non_sequence_seq(
    monkeypatch: pytest.MonkeyPatch, seq: object
) -> None:
    client = _client_with_result(
        monkeypatch,
        {"seq": seq, "frame": _frame_payload(), "metrics_meta": _metrics_meta()},
    )

    with pytest.raises(DaemonProtocolError, match="invalid seq"):
        client.request_current()


def test_request_current_wraps_invalid_frame_as_protocol_error(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client_with_result(
        monkeypatch,
        {"seq": 0, "frame": {"not": "a frame"}, "metrics_meta": _metrics_meta()},
    )

    with pytest.raises(DaemonProtocolError, match="invalid frame"):
        client.request_current()


@pytest.mark.parametrize(
    "frames, message",
    [
        ({}, "invalid history frames"),
        (["not-an-entry"], "invalid history entry 0"),
        ([{"seq": True, "frame": _frame_payload()}], "invalid seq at entry 0"),
        ([{"seq": 0, "frame": {}}], "invalid frame at entry 0"),
    ],
)
def test_request_history_rejects_invalid_entries(
    monkeypatch: pytest.MonkeyPatch, frames: object, message: str
) -> None:
    client = _client_with_result(
        monkeypatch,
        {
            "frames": frames,
            "oldest_seq": None,
            "latest_seq": None,
            "next_cursor": None,
            "gap": False,
            "metrics_meta": _metrics_meta(),
        },
    )

    with pytest.raises(DaemonProtocolError, match=message):
        client.request_history()


@pytest.mark.parametrize(
    "result, message",
    [
        ({"seq": True, "entity": {}}, "invalid seq"),
        ({"seq": 0, "entity": {}}, "invalid entity frame"),
    ],
)
def test_request_entity_rejects_invalid_result(
    monkeypatch: pytest.MonkeyPatch, result: dict[str, object], message: str
) -> None:
    result["metrics_meta"] = _metrics_meta()
    client = _client_with_result(monkeypatch, result)

    with pytest.raises(DaemonProtocolError, match=message):
        client.request_entity("entity-key")


@pytest.mark.parametrize(
    "metrics_meta, message",
    [
        ([], "invalid metrics_meta"),
        ({1: {"sensitivity": "public"}}, "non-string metric key"),
        ({"cpu": []}, "non-dict metrics_meta entry"),
        ({"cpu": {"sensitivity": "secret"}}, "invalid sensitivity"),
    ],
)
def test_request_current_rejects_invalid_metrics_metadata(
    monkeypatch: pytest.MonkeyPatch, metrics_meta: object, message: str
) -> None:
    client = _client_with_result(
        monkeypatch,
        {"seq": 0, "frame": _frame_payload(), "metrics_meta": metrics_meta},
    )

    with pytest.raises(DaemonProtocolError, match=message):
        client.request_current()
