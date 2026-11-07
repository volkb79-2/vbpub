"""Regression coverage for the extraction review's second fix round."""

from __future__ import annotations

import importlib
from importlib import metadata
import os
from pathlib import Path
import subprocess
import sys
import types

import iogovmon
from iogovmon.config import Config


PROJECT = Path(__file__).resolve().parents[1]
VERSION_HELPER = PROJECT / "tools/write_installed_version.py"


def _reload_version() -> str:
    return importlib.reload(iogovmon).__version__


def _metadata_missing(name: str) -> str:
    raise metadata.PackageNotFoundError(name)


def test_governed_cgroups_defaults_to_empty():
    assert Config().governed_cgroups == []


def test_version_prefers_installed_wheel_metadata(monkeypatch):
    def version(name: str) -> str:
        assert name == "iogovmon"
        return "4.5.6+wheel"

    with monkeypatch.context() as patcher:
        patcher.setattr(metadata, "version", version)
        assert _reload_version() == "4.5.6+wheel"
    _reload_version()


def test_version_uses_generated_installed_file_without_wheel_metadata(monkeypatch):
    generated = types.ModuleType("iogovmon._version_installed")
    generated.__version__ = "0.4.2+g1a2b3c4"
    with monkeypatch.context() as patcher:
        patcher.setattr(metadata, "version", _metadata_missing)
        patcher.setitem(sys.modules, "iogovmon._version_installed", generated)
        assert _reload_version() == "0.4.2+g1a2b3c4"
    _reload_version()


def test_version_falls_back_to_source_without_metadata_or_generated_file(monkeypatch):
    with monkeypatch.context() as patcher:
        patcher.setattr(metadata, "version", _metadata_missing)
        patcher.delitem(sys.modules, "iogovmon._version_installed", raising=False)
        patcher.delattr(iogovmon, "_version_installed", raising=False)
        assert _reload_version() == "0+source"
    _reload_version()


def _run_helper(project_root: Path, output: Path, search_path: str | Path) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ, PATH=str(search_path))
    return subprocess.run(
        [sys.executable, str(VERSION_HELPER), "--project-root", str(project_root), "--output", str(output)],
        capture_output=True,
        text=True,
        env=env,
        timeout=20,
    )


def _helper_project(tmp_path: Path) -> Path:
    project_root = tmp_path / "project"
    project_root.mkdir()
    (project_root / "pyproject.toml").write_text(
        '[tool.iogovmon.install]\nversion = "0.4.2"\n', encoding="utf-8"
    )
    return project_root


def test_version_helper_writes_pyproject_version_with_git_sha(tmp_path):
    project_root = _helper_project(tmp_path)
    (project_root / ".git").touch()
    bindir = tmp_path / "bin"
    bindir.mkdir()
    fake_git = bindir / "git"
    fake_git.write_text(
        '#!/bin/sh\n[ "$3" = "rev-parse" ] || exit 2\nprintf "1a2b3c4\\n"\n',
        encoding="utf-8",
    )
    fake_git.chmod(0o755)
    output = tmp_path / "pkg/iogovmon/_version_installed.py"

    result = _run_helper(project_root, output, bindir)

    assert result.returncode == 0, result.stdout + result.stderr
    assert output.read_text(encoding="utf-8") == '__version__ = "0.4.2+g1a2b3c4"\n'


def test_version_helper_uses_pyproject_version_outside_git(tmp_path):
    project_root = _helper_project(tmp_path)
    output = tmp_path / "pkg/iogovmon/_version_installed.py"

    result = _run_helper(project_root, output, os.environ["PATH"])

    assert result.returncode == 0, result.stdout + result.stderr
    assert output.read_text(encoding="utf-8") == '__version__ = "0.4.2"\n'
