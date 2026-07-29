"""Luna-medium43 provisioning probes require a Vault token."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import provisioning  # noqa: E402


def test_probe_ref_vault_without_token_is_a_red_prerequisite(monkeypatch, tmp_path):
    from ciu.secrets import providers

    monkeypatch.setattr(providers, "vault_addr_from_config", lambda config: "http://vault")
    monkeypatch.setattr(providers, "resolve_vault_token", lambda config, root: None)

    result = provisioning.probe_ref("vault:secret/app/password", {}, tmp_path)

    assert result.satisfied is False
    assert result.reason == "No Vault token available"
