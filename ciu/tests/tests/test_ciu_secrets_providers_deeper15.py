"""Vault transport-failure contract."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu.secrets import providers  # noqa: E402


def test_unreachable_vault_read_is_typed_and_never_returns_the_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A provider outage is a red S4.16 error, not an implicit secret value.

    Secret materialization must distinguish an absent secret from an
    unreachable Vault.  The controlled transport failure proves that no live
    network is attempted and that the configured token is neither accepted as
    a read result nor exposed in diagnostics.
    """
    client = providers.VaultKV2("http://vault.internal:8200", token="top-secret")

    def unreachable(*_args: object, **_kwargs: object) -> object:
        raise providers.urllib.error.URLError("connection refused")

    monkeypatch.setattr(providers.urllib.request, "urlopen", unreachable)

    with pytest.raises(providers.VaultError, match=r"\[S4\.16\].*could not reach Vault") as exc:
        client.read("apps/api-key")

    assert "top-secret" not in str(exc.value)
