"""Host-temporary-directory discovery failure contract."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import workspace_env  # noqa: E402


def test_missing_docker_client_never_exports_a_host_tmp_bind(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A cockpit without the Docker executable has no trustworthy host ``/tmp``.

    ``HOST_MDT_TMP`` is consumed by sibling containers as a host bind source.
    If the Docker client cannot inspect the cockpit, discovery must safely emit
    the explicit empty value instead of inventing a host path that could expose
    or hide the wrong worktree.
    """
    monkeypatch.delenv("HOST_MDT_TMP", raising=False)
    monkeypatch.setenv("DEVCONTAINER_NAME", "ciu-cockpit")

    def docker_client_missing(*_args: object, **_kwargs: object) -> object:
        raise FileNotFoundError("docker")

    monkeypatch.setattr(workspace_env.subprocess, "run", docker_client_missing)

    assert workspace_env._detect_host_mdt_tmp() == ""
