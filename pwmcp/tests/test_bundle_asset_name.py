"""PWMCP-07: the bundle pwmcp builds and publishes is named as its own get.py requests.

The documented contract (cmru/docs/CONSUMERS.md, SPEC S6.3) is ``<tag><asset_suffix>``,
i.e. ``pwmcp-v<version>.tar.xz``. Before the fix ``bundle.toml`` and ``publish-bundle.py``
spelled ``pwmcp-<version>.tar.xz`` (no ``v``) and every release's own installer answered
HTTP 404. The expected name is spelled out here from the contract, not imported from the
code under test, and does not depend on the installed cmru version.
"""
from __future__ import annotations

import importlib.util
import tomllib
from pathlib import Path

import pytest

PWMCP = Path(__file__).resolve().parent.parent
VERSIONS = ["1.64.0-r2", "1.65.0-r1", "2.0.0"]


def _suffix() -> str:
    cfg = tomllib.loads((PWMCP / "cmru.toml").read_text(encoding="utf-8"))
    return cfg["project"]["installer"]["asset_suffix"]


def _contract_name(version: str) -> str:
    return f"pwmcp-v{version}{_suffix()}"


@pytest.mark.parametrize("version", VERSIONS)
def test_bundle_toml_archive_name_is_the_contract_name(version: str) -> None:
    template = tomllib.loads((PWMCP / "bundle.toml").read_text(encoding="utf-8"))["archive"][
        "name_template"
    ]
    assert template.format(version=version) == _contract_name(version)


@pytest.mark.parametrize("version", VERSIONS)
def test_publish_bundle_looks_for_the_contract_name(version: str, tmp_path: Path) -> None:
    spec = importlib.util.spec_from_file_location(
        "pwmcp_publish_bundle_name", PWMCP / "scripts" / "publish-bundle.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    legacy = tmp_path / f"pwmcp-{version}.tar.xz"
    legacy.write_bytes(b"x")
    with pytest.raises(SystemExit):  # the pre-fix name must no longer be accepted
        module.find_bundle(tmp_path, version)
    wanted = tmp_path / _contract_name(version)
    wanted.write_bytes(b"x")
    assert module.find_bundle(tmp_path, version) == wanted
