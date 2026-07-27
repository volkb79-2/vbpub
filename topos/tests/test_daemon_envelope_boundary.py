"""Versioned daemon-envelope boundary contracts without a live socket."""

from __future__ import annotations

from io import StringIO
from pathlib import Path

import pytest

from topos.daemon.client import DaemonClient, DaemonProtocolError, DaemonResponseError


@pytest.fixture
def client() -> DaemonClient:
    return DaemonClient(Path("/fixture/daemon.sock"))


@pytest.mark.parametrize(
    ("line", "expected_id", "needle"),
    [
        ("", "request-1", "closed the connection"),
        ("not-json\n", "request-1", "malformed JSON envelope"),
        ("[]\n", "request-1", "non-object envelope"),
        ('{"id":"other","ok":true,"result":{}}\n', "request-1", "returned id 'other'"),
        ('{"id":"request-1","ok":true,"result":[]}\n', "request-1", "non-object result"),
        ('{"id":"request-1","ok":"yes"}\n', "request-1", "ok='yes'"),
    ],
)
def test_envelope_reader_rejects_malformed_or_incompatible_response_shapes(
    client: DaemonClient,
    line: str,
    expected_id: str,
    needle: str,
) -> None:
    with pytest.raises(DaemonProtocolError, match=needle):
        client._read_envelope_response(StringIO(line), expected_id)


def test_envelope_reader_returns_exact_success_result(client: DaemonClient) -> None:
    result = client._read_envelope_response(
        StringIO('{"id":"request-1","ok":true,"result":{"value":"sentinel"}}\n'),
        "request-1",
    )
    assert result == {"value": "sentinel"}


@pytest.mark.parametrize(
    ("error", "expected_code", "expected_message"),
    [
        ({"code": "unavailable", "message": "daemon warming up"}, "unavailable", "daemon warming up"),
        ({"code": 7, "message": 8}, None, "daemon returned an error"),
        (None, None, "daemon returned an error"),
    ],
)
def test_envelope_reader_maps_error_response_to_typed_code_and_message(
    client: DaemonClient,
    error: object,
    expected_code: str | None,
    expected_message: str,
) -> None:
    import json

    line = json.dumps({"id": "request-1", "ok": False, "error": error}) + "\n"
    with pytest.raises(DaemonResponseError, match=expected_message) as caught:
        client._read_envelope_response(StringIO(line), "request-1")
    assert caught.value.code == expected_code
