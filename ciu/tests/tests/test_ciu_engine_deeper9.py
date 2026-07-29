"""Public ``ciu secrets`` failure-boundary contract (S10.3)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import engine  # noqa: E402
from ciu.secrets.providers import VaultError  # noqa: E402


def test_secrets_cli_maps_vault_failure_to_red_runtime_exit(tmp_path, monkeypatch, capsys):
    """S10.3: secret-store operational failures return red without deployment.

    The public command boundary must catch a Vault failure raised while executing
    ``ciu secrets list`` and expose its actionable message as a runtime failure.
    A broken dispatcher would leak the exception, return success, or invoke the
    normal stack pipeline after the failed subcommand.
    """
    def fail_vault(_args):
        raise VaultError("vault is unavailable")

    monkeypatch.setattr(engine, "secrets_command", fail_vault)
    monkeypatch.setattr(
        engine,
        "main_execution",
        lambda **_kwargs: pytest.fail("secrets subcommand must not enter stack deployment"),
    )

    assert engine.main(["secrets", "list", "-d", str(tmp_path)]) == 1
    assert "[ERROR] vault is unavailable" in capsys.readouterr().out
