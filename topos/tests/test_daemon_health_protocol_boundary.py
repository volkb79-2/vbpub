"""Controlled health-protocol rejection contracts for the daemon client."""

from __future__ import annotations

from io import StringIO
from pathlib import Path

import pytest

from topos.daemon.client import DaemonClient, DaemonProtocolError, DaemonResponseError


@pytest.fixture
def client() -> DaemonClient:
    return DaemonClient(Path("/fixture/daemon.sock"))


@pytest.mark.parametrize(
    ("line", "error_type", "needle"),
    [
        ("", DaemonProtocolError, "closed the connection"),
        ("not-json\n", DaemonProtocolError, "malformed JSON"),
        ("[]\n", DaemonProtocolError, "non-object response"),
        ('{"type":"unexpected"}\n', DaemonProtocolError, "unsupported response type"),
        ('{"type":"error","message":"health unavailable"}\n', DaemonResponseError, "health unavailable"),
    ],
)
def test_legacy_health_reader_maps_malformed_and_error_envelopes_to_typed_contracts(
    client: DaemonClient,
    line: str,
    error_type: type[Exception],
    needle: str,
) -> None:
    with pytest.raises(error_type, match=needle):
        client._read_health(StringIO(line))


@pytest.mark.parametrize(
    ("value", "field", "needle"),
    [
        ("has\x00control", "detail", "control character in detail"),
        (True, "last_attempt_ts", "invalid last_attempt_ts"),
        (float("nan"), "last_success_ts", "invalid last_success_ts"),
        (-0.1, "last_success_ts", "invalid last_success_ts"),
        (-1, "consecutive_failures", "invalid consecutive_failures"),
    ],
)
def test_health_field_validators_reject_control_nonfinite_and_negative_values(
    client: DaemonClient,
    value: object,
    field: str,
    needle: str,
) -> None:
    with pytest.raises(DaemonProtocolError, match=needle):
        if field == "detail":
            client._bounded_health_string(value, field, 1024)
        elif field == "consecutive_failures":
            client._health_counter(value, field)
        else:
            client._optional_health_timestamp(value, field)
