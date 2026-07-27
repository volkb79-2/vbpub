"""Daemon hello response shape and element-type contracts."""

from __future__ import annotations

from pathlib import Path

import pytest

from topos.daemon.client import DaemonClient, DaemonProtocolError


def _payload() -> dict[str, object]:
    return {
        "protocol_versions": [1, 2],
        "capabilities": ["hello", "current"],
        "identity": {"name": "topos-daemon"},
        "limits": {"max_response_bytes": 1024},
    }


@pytest.mark.parametrize(
    ("field", "value", "needle"),
    [
        ("protocol_versions", "not-a-list", "invalid protocol_versions"),
        ("protocol_versions", [True], "invalid protocol_versions"),
        ("capabilities", ["hello", 3], "invalid capabilities"),
        ("identity", {"name": 3}, "invalid identity"),
        ("limits", {3: 1024}, "invalid limits"),
    ],
)
def test_hello_rejects_malformed_container_and_element_types(
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    value: object,
    needle: str,
) -> None:
    payload = _payload()
    payload[field] = value
    monkeypatch.setattr(DaemonClient, "_request_envelope", lambda *_args, **_kwargs: payload)

    with pytest.raises(DaemonProtocolError, match=needle):
        DaemonClient(Path("/fixture/daemon.sock")).request_hello()


def test_hello_preserves_valid_protocol_types_without_coercion(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = _payload()
    monkeypatch.setattr(DaemonClient, "_request_envelope", lambda *_args, **_kwargs: payload)

    result = DaemonClient(Path("/fixture/daemon.sock")).request_hello()

    assert result.protocol_versions == (1, 2)
    assert result.capabilities == ("hello", "current")
    assert result.identity == {"name": "topos-daemon"}
    assert result.limits == {"max_response_bytes": 1024}
