"""repo_paths_access: the one place scripts/truenas asks vm-lab.toml for its ISO, image and VM-state folders (RP-03)."""
from __future__ import annotations

from pathlib import Path

import pytest

import repo_paths_access as access

REAL_ROOT = Path(access.__file__).resolve().parent.parent.parent
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
    """A tmp repository root with its own vm-lab.toml and no CoW environment."""
    monkeypatch.delenv("MDT_COW_ROOT", raising=False)
    monkeypatch.delenv("COWTREE_MODE", raising=False)
    (tmp_path / "vm-lab.toml").write_text(CONFIG)
    monkeypatch.setattr(access, "REPO_ROOT", tmp_path)
    return tmp_path


def test_plain_mode_answers_the_repo_folders_of_the_config(plain_repo):
    assert access.iso_dir() == plain_repo / "downloads" / "iso"
    assert access.vm_images_dir() == plain_repo / "downloads" / "vm-images"
    assert access.downloads_dir() == plain_repo / "downloads"
    assert access.vm_state_root() == plain_repo / "scripts" / "truenas" / "vm-lab"
    assert access.golden_source_dir() == plain_repo / "scripts" / "truenas" / "vm-lab" / access.GOLDEN_SOURCE_VM


def test_the_checked_in_config_declares_every_name_this_project_asks_for(monkeypatch):
    monkeypatch.delenv("MDT_COW_ROOT", raising=False)
    monkeypatch.delenv("COWTREE_MODE", raising=False)
    assert access.REPO_ROOT == REAL_ROOT and (REAL_ROOT / "vm-lab.toml").is_file()
    assert access.iso_dir() == REAL_ROOT / "downloads" / "iso"
    assert access.vm_state_root() == REAL_ROOT / "scripts" / "truenas" / "vm-lab"


def test_missing_config_refuses_in_one_line_naming_the_file(tmp_path, monkeypatch):
    monkeypatch.setattr(access, "REPO_ROOT", tmp_path)
    with pytest.raises(access.RepoPathsError) as err:
        access.iso_dir()
    assert str(tmp_path / "vm-lab.toml") in str(err.value) and "\n" not in str(err.value)


def test_missing_resolver_refuses_naming_the_module(plain_repo, monkeypatch):
    monkeypatch.setattr(access, "COWTREE_SOURCE", plain_repo / "no-cowtree")
    with pytest.raises(access.RepoPathsError, match=r"no-cowtree/cowtree/repo_paths\.py not found"):
        access.iso_dir()


def test_a_broken_cow_root_is_a_refusal_not_a_silent_plain_answer(plain_repo, monkeypatch):
    monkeypatch.setenv("MDT_COW_ROOT", str(plain_repo / "does-not-exist"))
    with pytest.raises(access.RepoPathsError, match=r"cannot resolve 'iso'"):
        access.iso_dir()


def test_an_invalid_config_is_a_refusal_naming_the_file(plain_repo):
    (plain_repo / "vm-lab.toml").write_text("schema = 7\n")
    with pytest.raises(access.RepoPathsError, match=r"vm-lab\.toml: cannot resolve"):
        access.vm_state_root()


def test_a_cow_answer_of_the_resolver_is_returned_with_the_project(plain_repo, monkeypatch, tmp_path):
    from cowtree.repo_paths import RepoPaths, Resolved

    seen = []

    def fake(self, name, *, project=None, environ=None, ops=None):
        seen.append((name, project))
        return Resolved(tmp_path / "cow" / name, "cow", name, project)

    monkeypatch.setattr(RepoPaths, "resolve", fake)
    assert access.vm_state_root() == tmp_path / "cow" / "vm-lab"
    assert access.iso_dir() == tmp_path / "cow" / "iso"
    assert seen == [("vm-lab", "scripts/truenas"), ("iso", None)]


def test_no_environment_variable_but_mdt_cow_root_moves_the_folders():
    text = Path(access.__file__).read_text()
    assert "os.environ" not in text and "getenv" not in text
