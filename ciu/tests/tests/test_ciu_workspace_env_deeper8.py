"""Docker-GID fallback contracts for the workspace environment generator."""
from __future__ import annotations

import grp
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import workspace_env  # noqa: E402


def test_docker_gid_uses_group_fallback_or_reports_missing_group(monkeypatch) -> None:
    """A host without either Docker socket must use its docker group or fail clearly.

    This is the final identity fallback for ``ciu env generate``: emitting a
    fabricated GID would make generated bind mounts inaccessible, while a host
    with a valid docker group remains usable even when the socket is absent.
    """
    monkeypatch.delenv("DOCKER_GID", raising=False)
    monkeypatch.setattr(workspace_env.Path, "exists", lambda _path: False)
    monkeypatch.setattr(grp, "getgrnam", lambda name: SimpleNamespace(gr_gid=4242))

    assert workspace_env._detect_docker_gid() == "4242"

    def no_docker_group(_name: str):
        raise KeyError("docker")

    monkeypatch.setattr(grp, "getgrnam", no_docker_group)
    with pytest.raises(workspace_env.WorkspaceEnvError, match="DOCKER_GID not found"):
        workspace_env._detect_docker_gid()
