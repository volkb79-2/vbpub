from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


MODULE_PATH = Path(__file__).resolve().parents[1] / "build-push.py"
SPEC = importlib.util.spec_from_file_location("pwmcp_build_push", MODULE_PATH)
assert SPEC and SPEC.loader
build_push = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = build_push
SPEC.loader.exec_module(build_push)


def test_load_builder_config(tmp_path: Path) -> None:
    config_path = tmp_path / "build-push.toml"
    config_path.write_text(
        """[builder]
name = "test-builder"
memory = "4g"
memory_swap = "12g"
cpu_shares = 128
cpu_quota = 400000
cpu_period = 100000
""",
        encoding="utf-8",
    )

    config = build_push.load_builder_config(config_path)

    assert config.name == "test-builder"
    assert config.memory_swap == "12g"
    assert config.cpu_quota / config.cpu_period == 4


@pytest.mark.parametrize(
    ("value", "expected"),
    [("4g", 4 * 1024**3), ("12GiB", 12 * 1024**3), ("512m", 512 * 1024**2)],
)
def test_docker_size_bytes(value: str, expected: int) -> None:
    assert build_push.docker_size_bytes(value) == expected


def test_missing_builder_setting_is_fatal(tmp_path: Path) -> None:
    config_path = tmp_path / "build-push.toml"
    config_path.write_text("[builder]\nname='incomplete'\n", encoding="utf-8")

    with pytest.raises(SystemExit):
        build_push.load_builder_config(config_path)
