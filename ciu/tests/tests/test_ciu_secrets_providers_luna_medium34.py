"""Vault HTTP result contracts."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from urllib.error import HTTPError

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu.secrets import providers  # noqa: E402


class _FakeResponse:
    def __init__(self, status: int, body: str) -> None:
        self.status = status
        self._body = body

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return self._body.encode("utf-8")


def test_request_returns_success_status_and_body(monkeypatch: object) -> None:
    """A successful Vault response is returned as its status and text body."""
    response = _FakeResponse(200, '{"data": {"data": {"value": "secret"}}}')
    monkeypatch.setattr(  # type: ignore[attr-defined]
        providers.urllib.request, "urlopen", lambda request, timeout: response
    )
    client = providers.VaultKV2("http://vault.invalid:8200", token="token")

    status, body = client._request("GET", "apps/api-key")

    assert status == 200
    assert body == '{"data": {"data": {"value": "secret"}}}'


def test_request_maps_http_404_to_absent_tuple(monkeypatch: object) -> None:
    """A missing Vault path is represented without making a live request."""
    def raise_not_found(request: object, timeout: float) -> None:
        raise HTTPError("http://vault.invalid:8200/v1/secret/data/missing", 404, "", {}, None)

    monkeypatch.setattr(  # type: ignore[attr-defined]
        providers.urllib.request, "urlopen", raise_not_found
    )
    client = providers.VaultKV2("http://vault.invalid:8200", token="token")

    assert client._request("GET", "missing") == (404, "")


def test_read_returns_none_for_success_without_usable_data_map(
    monkeypatch: object,
) -> None:
    """A successful payload lacking a non-empty data map is absent."""
    client = providers.VaultKV2("http://vault.invalid:8200", token="token")
    monkeypatch.setattr(  # type: ignore[attr-defined]
        client,
        "_request",
        lambda *_args, **_kwargs: (200, json.dumps({"data": {}})),
    )

    assert client.read("apps/api-key") is None


def test_write_http_failure_identifies_attempted_vault_url(
    monkeypatch: object,
) -> None:
    """A failed write raises the typed error naming its Vault URL."""
    client = providers.VaultKV2("http://vault.invalid:8200", token="token")
    monkeypatch.setattr(  # type: ignore[attr-defined]
        client,
        "_request",
        lambda *_args, **_kwargs: (503, ""),
    )

    try:
        client.write("apps/api-key", "secret")
    except providers.VaultError as exc:
        message = str(exc)
    else:
        raise AssertionError("write should reject an HTTP failure")

    assert "[S4.16]" in message
    assert "http://vault.invalid:8200/v1/secret/data/apps/api-key" in message
