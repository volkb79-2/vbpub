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


def test_load_builder_config(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("BUILDX_BUILDER", raising=False)
    monkeypatch.delenv("BUILDKIT_HOST", raising=False)
    config_path = tmp_path / "cmru.toml"
    config_path.write_text(
        """[env]
BUILDX_BUILDER = "test-builder"
BUILDKIT_HOST = "unix:///run/test-buildkit.sock"
""",
        encoding="utf-8",
    )

    config = build_push.load_builder_config(config_path, estate=tmp_path / "none.toml")

    assert config.name == "test-builder"
    assert config.endpoint == "unix:///run/test-buildkit.sock"


def test_missing_builder_setting_is_fatal(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("BUILDKIT_HOST", raising=False)
    config_path = tmp_path / "cmru.toml"
    config_path.write_text("[env]\nBUILDX_BUILDER='incomplete'\n", encoding="utf-8")

    with pytest.raises(SystemExit):
        build_push.load_builder_config(config_path, estate=tmp_path / "none.toml")


@pytest.mark.parametrize(
    "contents",
    [
        "[env]\nBUILDX_BUILDER='builder'\nBUILDKIT_HOST='tcp://builder:1234'\n",
    ],
)
def test_invalid_builder_values_are_fatal(tmp_path: Path, contents: str, monkeypatch) -> None:
    monkeypatch.delenv("BUILDX_BUILDER", raising=False)
    monkeypatch.delenv("BUILDKIT_HOST", raising=False)
    config_path = tmp_path / "cmru.toml"
    config_path.write_text(contents, encoding="utf-8")
    with pytest.raises(SystemExit):
        build_push.load_builder_config(config_path, estate=tmp_path / "none.toml")


def test_the_estate_defaults_supply_the_managed_builder(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("BUILDX_BUILDER", raising=False)
    monkeypatch.delenv("BUILDKIT_HOST", raising=False)
    project = tmp_path / "cmru.toml"
    project.write_text("[env]\n", encoding="utf-8")
    config = build_push.load_builder_config(project)
    assert config.name and config.endpoint.startswith("unix:///")


def test_the_environment_overrides_the_estate_defaults_for_a_system_without_the_managed_service(tmp_path: Path, monkeypatch) -> None:
    project = tmp_path / "cmru.toml"
    project.write_text("[env]\n", encoding="utf-8")
    monkeypatch.setenv("BUILDX_BUILDER", "default")
    monkeypatch.setenv("BUILDKIT_HOST", "")  # set-but-empty counts as set, as in cmru's expansion

    config = build_push.load_builder_config(project)

    assert (config.name, config.endpoint) == ("default", "")
    assert build_push._builder_args(config) == ["--builder", "default"]


def test_a_reference_in_the_file_resolves_like_cmru_does(tmp_path: Path, monkeypatch) -> None:
    estate = tmp_path / "estate.toml"
    estate.write_text(
        '[orchestration.defaults.env]\nBUILDX_BUILDER = "${BUILDX_BUILDER:-managed}"\n'
        'BUILDKIT_HOST = "${BUILDKIT_HOST:-unix:///run/x.sock}"\n', encoding="utf-8")
    project = tmp_path / "cmru.toml"
    project.write_text("[env]\n", encoding="utf-8")
    monkeypatch.delenv("BUILDX_BUILDER", raising=False)
    monkeypatch.delenv("BUILDKIT_HOST", raising=False)

    config = build_push.load_builder_config(project, estate=estate)

    assert (config.name, config.endpoint) == ("managed", "unix:///run/x.sock")
