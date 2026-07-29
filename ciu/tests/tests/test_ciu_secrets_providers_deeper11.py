"""Vault provider local-state failure contract."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu.secrets.providers import VaultError, resolve_vault_token  # noqa: E402


def test_malformed_local_vault_state_fails_typed_before_a_token_is_accepted(tmp_path, monkeypatch):
    """S4.16: corrupt rendered Vault state is red, never an implicit token source.

    A local Vault stack can be present after an interrupted or manually damaged
    render.  Without an environment or token-file override, CIU must report a
    typed provider configuration failure rather than leak TOML parser details
    or continue with a fabricated/partial token.
    """
    monkeypatch.delenv("VAULT_TOKEN", raising=False)
    vault_stack = tmp_path / "infra" / "vault"
    vault_stack.mkdir(parents=True)
    state_file = vault_stack / "ciu.toml"
    state_file.write_text("[state\\nroot_token = 'partial'\\n", encoding="utf-8")

    with pytest.raises(VaultError, match=r"\[S4\.16\].*could not read Vault stack state"):
        resolve_vault_token({}, tmp_path)
