"""Vault token source fallback branch coverage."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu.secrets.providers import resolve_vault_token  # noqa: E402


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


def test_blank_token_file_falls_back_to_rendered_local_stack(
    tmp_path: Path, monkeypatch: object
) -> None:
    monkeypatch.delenv("VAULT_TOKEN", raising=False)  # type: ignore[attr-defined]
    token_file = tmp_path / "vault-token"
    token_file.write_text("  \n", encoding="utf-8")
    stack_dir = tmp_path / "infra" / "vault"
    stack_dir.mkdir(parents=True)
    (stack_dir / "ciu.toml").write_text(
        '[state]\nroot_token = "fixture-local-token"\n', encoding="utf-8"
    )

    config = {"vault": {"token_file": str(token_file)}}

    assert resolve_vault_token(config, tmp_path) == "fixture-local-token"


def test_non_mapping_state_has_no_local_token(
    tmp_path: Path, monkeypatch: object
) -> None:
    monkeypatch.delenv("VAULT_TOKEN", raising=False)  # type: ignore[attr-defined]
    stack_dir = tmp_path / "infra" / "vault"
    stack_dir.mkdir(parents=True)
    (stack_dir / "ciu.toml").write_text(
        'state = "not-a-mapping"\n', encoding="utf-8"
    )

    assert resolve_vault_token({}, tmp_path) is None


def test_blank_root_token_has_no_local_token(
    tmp_path: Path, monkeypatch: object
) -> None:
    monkeypatch.delenv("VAULT_TOKEN", raising=False)  # type: ignore[attr-defined]
    stack_dir = tmp_path / "infra" / "vault"
    stack_dir.mkdir(parents=True)
    (stack_dir / "ciu.toml").write_text(
        '[state]\nroot_token = ""\n', encoding="utf-8"
    )

    assert resolve_vault_token({}, tmp_path) is None
