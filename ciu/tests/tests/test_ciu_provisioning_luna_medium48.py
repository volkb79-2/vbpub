"""Focused coverage for Vault client construction through probe_ref."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import provisioning  # noqa: E402


def test_vault_probe_constructs_client_from_resolved_address_and_token(
    monkeypatch, tmp_path
):
    from ciu.secrets import providers

    constructed = []

    class FakeVaultKV2:
        def __init__(self, addr, token):
            constructed.append((addr, token))

        def read(self, path):
            assert path == "app/password"
            return {"value": "present"}

    monkeypatch.setattr(providers, "vault_addr_from_config", lambda config: "http://vault:8200")
    monkeypatch.setattr(
        providers, "resolve_vault_token", lambda config, repo_root: "resolved-token"
    )
    monkeypatch.setattr(providers, "VaultKV2", FakeVaultKV2)

    result = provisioning.probe_ref("vault:secret/app/password", {}, tmp_path)

    assert constructed == [("http://vault:8200", "resolved-token")]
    assert result.satisfied is True
    assert result.reason == "Vault secret exists at 'app/password'"
