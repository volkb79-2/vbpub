"""Focused workspace-env parsing and discovery fallback contracts."""
from __future__ import annotations

import grp
import sys
import urllib.error
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import workspace_env  # noqa: E402


def test_malformed_env_entry_is_a_workspace_env_error(tmp_path: Path) -> None:
    """Malformed shell syntax must not leak the parser's raw ValueError."""
    env_file = tmp_path / "ciu.env"
    env_file.write_text('BROKEN="unterminated\n', encoding="utf-8")

    with pytest.raises(workspace_env.WorkspaceEnvError, match="Invalid ciu.env"):
        workspace_env.parse_workspace_env(env_file)


def test_malformed_global_toml_falls_back_without_lookup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Optional public-FQDN discovery degrades to localhost when config is bad."""
    (tmp_path / workspace_env.GLOBAL_CONFIG_RENDERED).write_text(
        "[infrastructure\npublic_fqdn = \"broken\"\n", encoding="utf-8"
    )
    monkeypatch.delenv("PUBLIC_FQDN", raising=False)
    monkeypatch.delenv("PUBLIC_IP", raising=False)

    with patch.object(
        workspace_env.urllib.request,
        "urlopen",
        side_effect=urllib.error.URLError("offline"),
    ) as urlopen:
        result = workspace_env._detect_public_fqdn(tmp_path, require_fqdn=False)

    urlopen.assert_called_once()
    assert result["PUBLIC_IP"] == ""
    assert result["PUBLIC_FQDN"] == "localhost"


def test_missing_docker_socket_and_group_raise_typed_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Docker GID discovery must fail visibly instead of fabricating a GID."""
    monkeypatch.delenv("DOCKER_GID", raising=False)

    class MissingSocket:
        def __init__(self, path: str) -> None:
            self.path = path

        def exists(self) -> bool:
            return False

    monkeypatch.setattr(workspace_env, "Path", MissingSocket)
    monkeypatch.setattr(
        grp,
        "getgrnam",
        lambda _name: (_ for _ in ()).throw(KeyError("docker")),
    )

    with pytest.raises(workspace_env.WorkspaceEnvError, match="DOCKER_GID not found"):
        workspace_env._detect_docker_gid()
