"""Fail-closed boundary tests for the Vault provider (CIU S4.16)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu.secrets.providers import VaultError, vault_addr_from_config  # noqa: E402


@pytest.mark.parametrize(
    "config",
    [
        {"topology": []},
        {"topology": {"services": []}},
        {"topology": {"services": {"vault": []}}},
    ],
)
def test_malformed_vault_topology_fails_as_s4_16_error(config: dict) -> None:
    """S4.16: malformed merged topology aborts cleanly, before any Vault I/O.

    A broken configuration must not leak an implementation ``AttributeError``
    or lead a caller to attempt a provider request with an invented address.
    """
    with pytest.raises(VaultError, match=r"\[S4\.16\].*Vault address"):
        vault_addr_from_config(config)
