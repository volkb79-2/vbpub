"""Vault token source fallback branch coverage."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu.secrets.providers import VaultError, resolve_vault_token  # noqa: E402


def test_absolute_token_file_is_used_independent_of_repo_root(
    tmp_path: Path, monkeypatch: object
) -> None:
    monkeypatch.delenv("VAULT_TOKEN", raising=False)  # type: ignore[attr-defined]
    token_file = tmp_path / "outside-repo" / "vault-token"
    token_file.parent.mkdir()
    token_file.write_text("fixture-absolute-token\n", encoding="utf-8")

    config = {"vault": {"token_file": str(token_file)}}

    assert resolve_vault_token(config, tmp_path / "different-repo") == (
        "fixture-absolute-token"
    )


def test_blank_token_file_falls_back_to_bootstrap_store(
    tmp_path: Path, monkeypatch: object
) -> None:
    monkeypatch.delenv("VAULT_TOKEN", raising=False)  # type: ignore[attr-defined]
    token_file = tmp_path / "vault-token"
    token_file.write_text("  \n", encoding="utf-8")
    store_dir = tmp_path / "infra" / "vault" / ".ciu" / "secrets"
    store_dir.mkdir(parents=True)
    (store_dir / "root_token").write_text("fixture-local-token", encoding="utf-8")

    config = {"vault": {"token_file": str(token_file)}}

    assert resolve_vault_token(config, tmp_path) == "fixture-local-token"


def test_bootstrap_store_directory_has_no_local_token(
    tmp_path: Path, monkeypatch: object
) -> None:
    """A DIRECTORY where the store file belongs is indeterminacy, not a token."""
    monkeypatch.delenv("VAULT_TOKEN", raising=False)  # type: ignore[attr-defined]
    (tmp_path / "infra" / "vault" / ".ciu" / "secrets" / "root_token").mkdir(
        parents=True
    )

    with pytest.raises(VaultError, match=r"\[S4\.16\].*could not be read"):
        resolve_vault_token({}, tmp_path)


def test_blank_bootstrap_store_has_no_local_token(
    tmp_path: Path, monkeypatch: object
) -> None:
    monkeypatch.delenv("VAULT_TOKEN", raising=False)  # type: ignore[attr-defined]
    store_dir = tmp_path / "infra" / "vault" / ".ciu" / "secrets"
    store_dir.mkdir(parents=True)
    (store_dir / "root_token").write_text("  \n", encoding="utf-8")

    assert resolve_vault_token({}, tmp_path) is None
