"""NL-24: a leftover src/nyxloom.egg-info must not shadow the installed dist-info
when exec-nyxloom.py puts the checkout's src/ first on sys.path. Real metadata
directories on disk, no mocking of the unit under test."""

from __future__ import annotations

from importlib.metadata import distributions
from pathlib import Path

import nyxloom


def _meta(path: Path, version: str) -> None:
    path.mkdir(parents=True)
    fname = "PKG-INFO" if path.name.endswith(".egg-info") else "METADATA"
    (path / fname).write_text(f"Metadata-Version: 2.1\nName: nyxloom\nVersion: {version}\n", encoding="utf-8")


def _layout(tmp_path: Path, with_installed: bool) -> tuple[Path, Path]:
    src = tmp_path / "src"
    site = tmp_path / "site"
    (src / "nyxloom").mkdir(parents=True)
    site.mkdir()
    _meta(src / "nyxloom.egg-info", "0.5.1.dev4+stale")
    if with_installed:
        _meta(site / "nyxloom-0.12.0.dist-info", "0.12.0")
    return src.resolve(), site.resolve()


def test_fixture_really_shadows_without_the_fix(tmp_path):
    src, site = _layout(tmp_path, with_installed=True)
    first = next(d for d in distributions(path=[str(src), str(site)]) if d.metadata["Name"] == "nyxloom")
    assert first.version == "0.5.1.dev4+stale"  # plain lookup order == the reported bug


def test_stale_egg_info_does_not_shadow_the_installed_version(tmp_path):
    src, site = _layout(tmp_path, with_installed=True)
    assert nyxloom._resolve_version([str(src), str(site)], src) == "0.12.0"


def test_egg_info_only_dev_install_keeps_working(tmp_path):
    src, _ = _layout(tmp_path, with_installed=False)
    # nothing installed elsewhere: the egg-info is the only metadata, so the plain lookup runs
    # (and finds whatever the real environment has); it must not raise or return the stale text
    assert nyxloom._resolve_version([str(src)], src) == nyxloom._resolve_version()


def test_no_egg_info_beside_the_package_is_the_plain_lookup(tmp_path):
    root = tmp_path / "src"
    (root / "nyxloom").mkdir(parents=True)
    assert nyxloom._unshadowed_version(None, root.resolve()) is None
