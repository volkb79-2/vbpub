"""tnt._paths: the one place tnt asks the repo path config for its ISO, image and VM-state folders (RP-03)."""
from __future__ import annotations

from pathlib import Path

import pytest

from tnt import _paths
from tnt.errors import Refused

REAL_ROOT = Path(_paths.__file__).resolve().parents[4]
CONFIG = """schema = 1
repo = "demo"

[paths.iso]
repo = "downloads/iso"
cow = "downloads/iso"

[paths.vm-images]
repo = "downloads/vm-images"

[paths.vm-lab]
repo = "{project}/vm-lab"
cow = "{project}/vm-lab"

[paths.downloads]
repo = "downloads"
"""


@pytest.fixture
def plain_repo(tmp_path, monkeypatch):
    """A tmp repository root with its own path config and no CoW environment."""
    monkeypatch.delenv("MDT_COW_ROOT", raising=False)
    monkeypatch.delenv("COWTREE_MODE", raising=False)
    (tmp_path / _paths.config_name()).write_text(CONFIG)
    monkeypatch.setattr(_paths, "REPO_ROOT", tmp_path)
    return tmp_path


def test_plain_mode_answers_the_repo_folders_of_the_config(plain_repo):
    assert _paths.iso_dir() == plain_repo / "downloads" / "iso"
    assert _paths.vm_images_dir() == plain_repo / "downloads" / "vm-images"
    assert _paths.downloads_dir() == plain_repo / "downloads"
    assert _paths.vm_state_root() == plain_repo / "truenas" / "tnt" / "vm-lab"


def test_the_checked_in_config_declares_every_name_tnt_asks_for(monkeypatch):
    monkeypatch.delenv("MDT_COW_ROOT", raising=False)
    monkeypatch.delenv("COWTREE_MODE", raising=False)
    assert _paths.REPO_ROOT == REAL_ROOT and (REAL_ROOT / _paths.config_name()).is_file()
    assert _paths.iso_dir() == REAL_ROOT / "downloads" / "iso"
    assert _paths.vm_images_dir() == REAL_ROOT / "downloads" / "vm-images"
    assert _paths.downloads_dir() == REAL_ROOT / "downloads"
    assert _paths.vm_state_root() == REAL_ROOT / "truenas" / "tnt" / "vm-lab"


def test_missing_config_refuses_in_one_line_naming_the_file(tmp_path, monkeypatch):
    monkeypatch.setattr(_paths, "REPO_ROOT", tmp_path)
    with pytest.raises(Refused) as err:
        _paths.iso_dir()
    assert str(tmp_path / _paths.config_name()) in str(err.value) and "\n" not in str(err.value)


def test_missing_resolver_refuses_naming_the_module(plain_repo, monkeypatch):
    monkeypatch.setattr(_paths, "COWTREE_SOURCE", plain_repo / "no-cowtree")
    with pytest.raises(Refused, match=r"no-cowtree/cowtree/repo_paths\.py not found"):
        _paths.iso_dir()


def test_the_config_name_is_cowtrees_and_the_fallback_only_names_a_missing_resolver(plain_repo, monkeypatch):
    from cowtree import repo_paths

    assert _paths.config_name() == repo_paths.CONFIG_NAME == "cowtree.toml"
    monkeypatch.setattr(_paths, "COWTREE_SOURCE", plain_repo / "no-cowtree")
    assert _paths.config_name() == "cowtree.toml"
    with pytest.raises(Refused, match=r"\(cowtree\.toml\) cannot be read"):
        _paths.iso_dir()


def test_a_repo_with_only_the_old_config_name_is_refused_by_tnt_with_cowtrees_migration_hint(tmp_path, monkeypatch):
    """The consumer's own refusal (through tnt._paths, as every tnt caller asks) carries cowtree's one-line hint."""
    from cowtree import repo_paths

    (tmp_path / repo_paths.LEGACY_CONFIG_NAME).write_text(CONFIG)
    monkeypatch.setattr(_paths, "REPO_ROOT", tmp_path)
    for ask in (_paths.iso_dir, _paths.vm_images_dir, _paths.downloads_dir, _paths.vm_state_root):
        with pytest.raises(Refused) as err:
            ask()
        message = str(err.value)
        assert err.value.rule == "repo-paths"
        assert f"{tmp_path / repo_paths.LEGACY_CONFIG_NAME}" in message
        assert f"was renamed to {_paths.config_name()}: git mv {repo_paths.LEGACY_CONFIG_NAME} {_paths.config_name()}" in message
        assert "\n" not in message and "not found" not in message


def test_a_broken_cow_root_is_a_refusal_not_a_silent_plain_answer(plain_repo, monkeypatch):
    monkeypatch.setenv("MDT_COW_ROOT", str(plain_repo / "does-not-exist"))
    with pytest.raises(Refused, match=r"cannot resolve 'iso'"):
        _paths.iso_dir()


def test_a_cow_answer_of_the_resolver_is_returned_with_the_project(plain_repo, monkeypatch, tmp_path):
    from cowtree.repo_paths import RepoPaths, Resolved

    seen = []

    def fake(self, name, *, project=None, environ=None, ops=None):
        seen.append((name, project))
        return Resolved(tmp_path / "cow" / name, "cow", name, project)

    monkeypatch.setattr(RepoPaths, "resolve", fake)
    assert _paths.vm_state_root() == tmp_path / "cow" / "vm-lab"
    assert _paths.vm_images_dir() == tmp_path / "cow" / "vm-images"
    assert seen == [("vm-lab", "truenas/tnt"), ("vm-images", None)]
