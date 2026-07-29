"""Focused tests for workspace environment autodetection fallbacks."""
from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import workspace_env  # noqa: E402


def test_detect_env_type_defaults_to_native_without_markers(monkeypatch) -> None:
    """A host without CI/container markers gets the complete native contract."""
    for name in ("ENV_TYPE", "GITHUB_ACTIONS", "REMOTE_CONTAINERS", "WORKSPACE_DIR"):
        monkeypatch.delenv(name, raising=False)

    real_exists = Path.exists

    def marker_absent(path: Path) -> bool:
        if path == Path("/.dockerenv"):
            return False
        return real_exists(path)

    with patch.object(Path, "exists", marker_absent):
        assert workspace_env._detect_env_type() == {
            "ENV_TYPE": "native",
            "IS_DEVCONTAINER": "0",
            "IS_GITHUB_ACTIONS": "0",
            "IS_NATIVE": "1",
        }


def test_detect_docker_gid_uses_existing_first_socket(monkeypatch) -> None:
    """Without an explicit GID, the first existing Docker socket supplies it."""
    monkeypatch.delenv("DOCKER_GID", raising=False)
    first_socket = Path("/var/run/docker-host.sock")
    second_socket = Path("/var/run/docker.sock")

    real_exists = Path.exists

    def first_socket_exists(path: Path) -> bool:
        if path == first_socket:
            return True
        if path == second_socket:
            return False
        return real_exists(path)

    def controlled_stat(path: Path, *args, **kwargs):
        assert path == first_socket
        return SimpleNamespace(st_gid=24680)

    with (
        patch.object(Path, "exists", first_socket_exists),
        patch.object(Path, "stat", controlled_stat),
    ):
        assert workspace_env._detect_docker_gid() == "24680"
