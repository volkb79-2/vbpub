"""Explicit-root workspace environment identity contract."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu.workspace_env import bootstrap_workspace_env  # noqa: E402


def test_define_root_replaces_stale_workspace_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``--define-root`` loads its chosen repo, not inherited shell state.

    A shell can retain ``REPO_ROOT`` and related identity values from a
    previously sourced workspace.  An explicit root is the user's unambiguous
    selection, so its committed ``ciu.env`` must replace those values; merely
    preserving the inherited values would render/deploy against the wrong
    repository.
    """
    selected = tmp_path / "selected"
    selected.mkdir()
    (selected / "ciu.global.defaults.toml.j2").write_text("# marker\n", encoding="utf-8")
    (selected / "ciu.env").write_text(
        "\n".join(
            [
                "REPO_ROOT=/chosen/repo",
                "PHYSICAL_REPO_ROOT=/host/chosen/repo",
                "DOCKER_NETWORK_INTERNAL=chosen-network",
                "CONTAINER_UID=1001",
                "DOCKER_GID=1002",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("REPO_ROOT", "/stale/repo")
    monkeypatch.setenv("PHYSICAL_REPO_ROOT", "/host/stale/repo")
    monkeypatch.setenv("DOCKER_NETWORK_INTERNAL", "stale-network")

    resolved = bootstrap_workspace_env(
        start_dir=tmp_path,
        define_root=selected,
        defaults_filename="ciu.global.defaults.toml.j2",
        generate_env=False,
        update_cert_permission=False,
        required_keys=(
            "REPO_ROOT",
            "PHYSICAL_REPO_ROOT",
            "DOCKER_NETWORK_INTERNAL",
            "CONTAINER_UID",
            "DOCKER_GID",
        ),
    )

    assert resolved == selected.resolve()
    assert {key: os.environ[key] for key in (
        "REPO_ROOT", "PHYSICAL_REPO_ROOT", "DOCKER_NETWORK_INTERNAL", "CONTAINER_UID", "DOCKER_GID"
    )} == {
        "REPO_ROOT": "/chosen/repo",
        "PHYSICAL_REPO_ROOT": "/host/chosen/repo",
        "DOCKER_NETWORK_INTERNAL": "chosen-network",
        "CONTAINER_UID": "1001",
        "DOCKER_GID": "1002",
    }
