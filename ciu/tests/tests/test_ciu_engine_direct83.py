"""S6.1 hostdir value-shape regression contract."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import engine


def test_hostdir_rejects_non_string_non_table_value_before_provisioning(tmp_path: Path):
    """A malformed hostdir declaration must not silently omit a required mount."""

    config = {
        "deploy": {"env": {"shared": {"CONTAINER_UID": "1000", "DOCKER_GID": "999"}}},
        "service": {"name": "api", "hostdir": {"data": 42}},
    }

    with pytest.raises(ValueError, match=r"\[S6\.1\].*data.*int"):
        engine.create_hostdirs(
            config,
            tmp_path,
            repo_root=tmp_path,
            physical_root=tmp_path,
            chown_fn=lambda *_args: None,
        )
