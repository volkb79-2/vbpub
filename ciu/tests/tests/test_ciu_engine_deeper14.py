"""Reset pre-dispatch failure contract."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import engine  # noqa: E402
from ciu.config_constants import CIU_COMPOSE_OUTPUT, MACHINE_DIR, STACK_CONFIG_RENDERED  # noqa: E402


def test_reset_stops_before_deleting_artifacts_when_docker_is_unavailable(tmp_path, monkeypatch):
    """S6.4: reset must not partially destroy state when its first daemon action fails.

    ``reset`` begins by tearing down the scoped Compose project.  If Docker is
    absent, continuing to local volume/config deletion would leave a live
    deployment with its recovery artifacts removed.  The engine therefore
    reports the operational failure and preserves every local artifact.
    """
    config = {
        "deploy": {
            "project_name": "demo",
            "labels": {"prefix": "example"},
        }
    }
    volume = tmp_path / "vol-data"
    volume.mkdir()
    compose = tmp_path / CIU_COMPOSE_OUTPUT
    rendered = tmp_path / STACK_CONFIG_RENDERED
    compose.write_text("services: {}\n", encoding="utf-8")
    rendered.write_text("[state]\n", encoding="utf-8")
    (tmp_path / MACHINE_DIR).mkdir()

    monkeypatch.setattr(
        engine.procutil,
        "run_cmd",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(FileNotFoundError("docker")),
    )

    (tmp_path / "ciu.env").write_text(
        'export REPO_NAME="dstdns"\nexport INSTANCE_ID="abc123"\n',
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="docker not available for reset"):
        engine.reset_service(config, tmp_path, repo_root=tmp_path)

    assert volume.is_dir()
    assert compose.is_file()
    assert rendered.is_file()
