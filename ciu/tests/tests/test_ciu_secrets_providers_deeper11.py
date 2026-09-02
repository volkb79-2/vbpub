"""Vault provider local-state failure contract."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu.secrets.providers import VaultError, resolve_vault_token  # noqa: E402


def test_unreadable_bootstrap_store_fails_typed_before_a_token_is_accepted(
    tmp_path, monkeypatch
):
    """S4.16: an unreadable bootstrap store file is red, never a silent skip.

    A local Vault stack can be present after an interrupted deploy or a damaged
    store. Source #3 distinguishes ABSENT (falls through — the stack may simply
    not be bootstrapped here) from PRESENT-BUT-UNREADABLE, which is
    indeterminacy: CIU must report a typed provider failure rather than
    continue as if no bootstrap had ever happened. The message names the PATH
    and never any value (S4.23).
    """
    monkeypatch.delenv("VAULT_TOKEN", raising=False)
    store_dir = tmp_path / "infra" / "vault" / ".ciu" / "secrets"
    store_dir.mkdir(parents=True)
    store_file = store_dir / "root_token"
    store_file.write_text("s.partial", encoding="utf-8")
    store_file.chmod(0o000)

    if os.getuid() == 0:
        pytest.skip("root bypasses file permission bits")

    with pytest.raises(VaultError, match=r"\[S4\.16\].*could not be read"):
        resolve_vault_token({}, tmp_path)
