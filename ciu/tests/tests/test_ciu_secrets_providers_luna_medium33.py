"""Vault token fallback and empty-read semantics."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu.secrets import providers  # noqa: E402


def _rendered_vault_state(repo_root: Path, token: str) -> None:
    state_dir = repo_root / "infra" / "vault"
    state_dir.mkdir(parents=True)
    (state_dir / "ciu.toml").write_text(
        f"[state]\nroot_token = {token!r}\n", encoding="utf-8"
    )


def test_malformed_vault_config_uses_local_rendered_state_fallback(
    tmp_path: Path, monkeypatch: object
) -> None:
    """A malformed optional vault config does not block local state fallback."""
    monkeypatch.delenv("VAULT_TOKEN", raising=False)  # type: ignore[attr-defined]
    _rendered_vault_state(tmp_path, "local-state-token")

    assert providers.resolve_vault_token({"vault": []}, tmp_path) == "local-state-token"


def test_missing_relative_token_file_uses_local_rendered_state_fallback(
    tmp_path: Path, monkeypatch: object
) -> None:
    """An absent configured token file falls through to local rendered state."""
    monkeypatch.delenv("VAULT_TOKEN", raising=False)  # type: ignore[attr-defined]
    _rendered_vault_state(tmp_path, "local-state-token")

    config = {"vault": {"token_file": "missing/vault-token"}}
    assert providers.resolve_vault_token(config, tmp_path) == "local-state-token"


def test_successful_empty_vault_read_returns_none(monkeypatch: object) -> None:
    """An empty successful response represents an absent secret."""
    client = providers.VaultKV2("http://vault.invalid:8200", token="token")
    monkeypatch.setattr(client, "_request", lambda *_args, **_kwargs: (200, b""))  # type: ignore[attr-defined]

    assert client.read("apps/api-key") is None
