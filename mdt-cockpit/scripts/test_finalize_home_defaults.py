"""Tests for post-mount home default seeding and compatibility paths."""
from __future__ import annotations

import os
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
import finalize_container_environment as finalize  # noqa: E402


def test_shell_and_config_defaults_seed_only_missing_entries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    defaults = tmp_path / "defaults"
    (defaults / ".config/nvim/lua/custom").mkdir(parents=True)
    (defaults / ".bashrc").write_text("# image bash defaults\n", encoding="utf-8")
    (defaults / ".profile").write_text("# image profile\n", encoding="utf-8")
    (defaults / ".zshrc").write_text("# image zsh defaults\n", encoding="utf-8")
    (defaults / ".config/nvim/init.lua").write_text("return {}\n", encoding="utf-8")
    (defaults / ".config/nvim/lua/custom/plugin.lua").write_text("-- image\n", encoding="utf-8")
    (home / ".config/nvim/lua/custom").mkdir(parents=True)
    (home / ".zshrc").write_text("# user zshrc\n", encoding="utf-8")
    (home / ".config/nvim/init.lua").write_text("-- user config\n", encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(finalize, "SHARED_HOME_DEFAULTS", defaults)

    finalize.setup_shell_bootstrap()
    finalize.setup_shell_bootstrap()

    assert (home / ".bashrc").read_text(encoding="utf-8") == "# image bash defaults\n"
    assert (home / ".profile").read_text(encoding="utf-8") == "# image profile\n"
    assert (home / ".zshrc").read_text(encoding="utf-8") == "# user zshrc\n"
    assert (home / ".config/nvim/init.lua").read_text(encoding="utf-8") == "-- user config\n"
    assert (home / ".config/nvim/lua/custom/plugin.lua").read_text(encoding="utf-8") == "-- image\n"


def test_image_home_compatibility_links_do_not_replace_user_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    image_venv = tmp_path / "opt/mdt/venv"
    image_venvs = tmp_path / "opt/mdt/venvs"
    image_oh_my_zsh = tmp_path / "opt/mdt/oh-my-zsh"
    manifest = tmp_path / "share/manifest.md"
    image_venv.mkdir(parents=True)
    (image_venvs / "py311").mkdir(parents=True)
    image_oh_my_zsh.mkdir(parents=True)
    manifest.parent.mkdir(parents=True)
    manifest.write_text("manifest\n", encoding="utf-8")
    (home / ".venv").mkdir(parents=True)
    (home / ".venv/user-file").write_text("keep", encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(finalize, "IMAGE_VENV", image_venv)
    monkeypatch.setattr(finalize, "IMAGE_VENVS", image_venvs)
    monkeypatch.setattr(finalize, "IMAGE_OH_MY_ZSH", image_oh_my_zsh)
    monkeypatch.setattr(finalize, "IMAGE_MANIFEST", manifest)

    finalize.setup_image_home_links()

    assert (home / ".venv/user-file").read_text(encoding="utf-8") == "keep"
    assert not (home / ".venv").is_symlink()
    assert (home / ".venv-py311").is_symlink()
    assert (home / ".venv-py311").resolve() == (image_venvs / "py311").resolve()
    assert (home / ".oh-my-zsh").resolve() == image_oh_my_zsh.resolve()
    assert (home / "mdt-manifest.md").resolve() == manifest.resolve()
    assert (home / ".oh-my-zsh-custom").is_dir()

