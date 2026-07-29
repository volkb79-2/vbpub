"""Focused cleanup coverage for the CIU SSH temporary-credential envelope."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from ciu import transport_ssh as transport


def _host() -> dict[str, object]:
    return {
        "ssh_host": "deploy.example",
        "ssh_user": "deploy",
        "ssh_port": 2222,
        "ssh_key": "ASK_VAULT:secret/data/deploy#private_key",
        "known_host": "ssh-ed25519 AAAACIU",
    }


@pytest.mark.parametrize("operation", ["exec", "sync", "scp"])
def test_public_transports_remove_temporary_credentials_despite_cleanup_races(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
) -> None:
    """Cleanup races are benign, while neither temporary credential survives."""
    key = tmp_path / "vault-key.pem"
    key.write_text("private key")
    created: list[Path] = [key]
    real_unlink = transport.os.unlink

    def resolve_key(*_: object, **__: object) -> str:
        return str(key)

    monkeypatch.setattr(transport, "resolve_key", resolve_key)

    real_known_hosts_file = transport._known_hosts_file

    def make_known_hosts(host_cfg: dict[str, object]) -> str:
        path = Path(real_known_hosts_file(host_cfg) or "")
        created.append(path)
        return str(path)

    monkeypatch.setattr(transport, "_known_hosts_file", make_known_hosts)
    monkeypatch.setattr(
        transport.subprocess,
        "run",
        lambda _argv: SimpleNamespace(returncode=41),
    )

    def race_unlink(path: str) -> None:
        real_unlink(path)
        raise OSError("cleanup race: already gone")

    monkeypatch.setattr(transport.os, "unlink", race_unlink)
    host = _host()

    if operation == "exec":
        result = transport.ssh_exec(
            host, ["ciu", "health"], config={}, repo_root=tmp_path,
        )
    elif operation == "sync":
        result = transport.ssh_sync(
            host, "/src", "/dst", config={}, repo_root=tmp_path,
        )
    else:
        result = transport.scp_file(
            host, "/src/file", "/dst/file", config={}, repo_root=tmp_path,
        )

    assert result == 41
    assert all(not path.exists() for path in created)
