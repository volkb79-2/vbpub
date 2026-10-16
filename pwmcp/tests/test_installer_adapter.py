from __future__ import annotations

import importlib.util
import tomllib
from pathlib import Path

import pytest


PWMCP_ROOT = Path(__file__).resolve().parents[1]
ADAPTER_PATH = PWMCP_ROOT / "installer" / "adapter.py"
spec = importlib.util.spec_from_file_location("pwmcp_installer_adapter", ADAPTER_PATH)
assert spec is not None and spec.loader is not None
adapter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(adapter)


def _release_tree(tmp_path: Path) -> tuple[Path, Path]:
    release_root = tmp_path / "releases" / "pwmcp-v1.63.0-r4" / "tree"
    for release_path in [*adapter.ROOT_LINKS.values(), adapter.INSTALL_OVERRIDE]:
        target = release_root / release_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("# release input\n", encoding="utf-8")
    return release_root, release_root.parents[2]


def test_installer_adapter_links_all_standalone_ciu_inputs(tmp_path: Path) -> None:
    release_root, instance_root = _release_tree(tmp_path)

    adapter._install_links(release_root)
    adapter._check_links(release_root)

    for instance_name, release_path in adapter.ROOT_LINKS.items():
        link = instance_root / instance_name
        assert link.is_symlink()
        assert link.readlink() == Path("current") / "tree" / release_path

    override = instance_root / adapter.INSTALL_OVERRIDE_NAME
    assert override.is_file()
    assert not override.is_symlink()
    assert override.read_text(encoding="utf-8") == (
        release_root / adapter.INSTALL_OVERRIDE
    ).read_text(encoding="utf-8")

    # An update reuses stable release links and leaves the site override alone.
    override.write_text("# operator edit\n", encoding="utf-8")
    adapter._install_links(release_root)
    adapter._check_links(release_root)
    assert override.read_text(encoding="utf-8") == "# operator edit\n"


def test_installer_adapter_refuses_an_unowned_root_config(tmp_path: Path) -> None:
    release_root, instance_root = _release_tree(tmp_path)
    config_link = instance_root / "ciu.compose.yml.j2"
    config_link.symlink_to("operator-owned.yml.j2")

    with pytest.raises(FileExistsError, match="refusing to replace unrelated symlink"):
        adapter._install_links(release_root)

    assert config_link.readlink() == Path("operator-owned.yml.j2")


def test_standalone_root_declares_one_default_stack_on_the_shared_network() -> None:
    root_defaults = tomllib.loads(
        (PWMCP_ROOT / "standalone" / "ciu.global.defaults.toml.j2").read_text(
            encoding="utf-8"
        )
    )
    override = tomllib.loads(
        (PWMCP_ROOT / "standalone" / "ciu.install.override.toml.j2").read_text(
            encoding="utf-8"
        )
    )

    services = root_defaults["deploy"]["phases"]["phase_1"]["services"]
    assert services == [{"path": ".", "name": "pwmcp", "enabled": True}]
    assert override["pwmcp"]["network"]["external_default"] is True
