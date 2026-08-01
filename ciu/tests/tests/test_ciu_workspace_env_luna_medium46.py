"""Focused FQDN discovery contracts for workspace environment setup."""
from __future__ import annotations

import socket
import sys
import urllib.error
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import workspace_env  # noqa: E402


def test_malformed_global_config_and_unreachable_ip_lookup_fall_back_to_localhost(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Optional discovery must survive malformed config and an offline lookup."""
    (tmp_path / workspace_env.GLOBAL_CONFIG_RENDERED).write_text(
        "[infrastructure\npublic_fqdn = \"broken\"\n", encoding="utf-8"
    )
    monkeypatch.delenv("PUBLIC_FQDN", raising=False)
    monkeypatch.delenv("PUBLIC_IP", raising=False)
    monkeypatch.delenv("PUBLIC_TLS_CRT_PEM", raising=False)
    monkeypatch.delenv("PUBLIC_TLS_KEY_PEM", raising=False)
    monkeypatch.setattr(
        workspace_env.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            urllib.error.URLError("offline")
        ),
    )

    result = workspace_env._detect_public_fqdn(tmp_path, require_fqdn=False)

    assert result == {
        "PUBLIC_FQDN": "localhost",
        "PUBLIC_IP": "",
        "PUBLIC_TLS_CRT_PEM": "",
        "PUBLIC_TLS_KEY_PEM": "",
    }


class _IpifyResponse:
    def __enter__(self) -> _IpifyResponse:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return b"198.51.100.46\n"


@pytest.mark.parametrize("require_fqdn", [False, True])
def test_reverse_dns_failure_is_safe_optional_fallback_and_required_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    require_fqdn: bool,
) -> None:
    """A missing reverse-DNS name falls back only when FQDN is optional."""
    monkeypatch.delenv("PUBLIC_FQDN", raising=False)
    monkeypatch.delenv("PUBLIC_IP", raising=False)
    monkeypatch.delenv("PUBLIC_TLS_CRT_PEM", raising=False)
    monkeypatch.delenv("PUBLIC_TLS_KEY_PEM", raising=False)
    monkeypatch.setattr(
        workspace_env.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: _IpifyResponse(),
    )
    monkeypatch.setattr(
        workspace_env.socket,
        "gethostbyaddr",
        lambda _public_ip: (_ for _ in ()).throw(socket.herror("no PTR")),
    )

    if require_fqdn:
        with pytest.raises(
            workspace_env.WorkspaceEnvError, match="Failed to detect PUBLIC_FQDN"
        ):
            workspace_env._detect_public_fqdn(tmp_path, require_fqdn=True)
    else:
        result = workspace_env._detect_public_fqdn(tmp_path, require_fqdn=False)
        assert result == {
            "PUBLIC_FQDN": "198.51.100.46",
            "PUBLIC_IP": "198.51.100.46",
            "PUBLIC_TLS_CRT_PEM": "",
            "PUBLIC_TLS_KEY_PEM": "",
        }
