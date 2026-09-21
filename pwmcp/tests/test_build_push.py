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
    config_path = tmp_path / "cmru.toml"
    config_path.write_text(
        """[env]
BUILDX_BUILDER = "test-builder"
BUILDKIT_HOST = "unix:///run/test-buildkit.sock"
""",
        encoding="utf-8",
    )

    config = build_push.load_builder_config(config_path)

    assert config.name == "test-builder"
    assert config.endpoint == "unix:///run/test-buildkit.sock"


def test_missing_builder_setting_is_fatal(tmp_path: Path) -> None:
    config_path = tmp_path / "cmru.toml"
    config_path.write_text("[env]\nBUILDX_BUILDER='incomplete'\n", encoding="utf-8")

    with pytest.raises(SystemExit):
        build_push.load_builder_config(config_path)


@pytest.mark.parametrize(
    "contents",
    [
        "[env]\nBUILDX_BUILDER=''\nBUILDKIT_HOST='unix:///run/test.sock'\n",
        "[env]\nBUILDX_BUILDER='builder'\nBUILDKIT_HOST='tcp://builder:1234'\n",
    ],
)
def test_invalid_builder_values_are_fatal(tmp_path: Path, contents: str) -> None:
    config_path = tmp_path / "cmru.toml"
    config_path.write_text(contents, encoding="utf-8")
    with pytest.raises(SystemExit):
        build_push.load_builder_config(config_path)
