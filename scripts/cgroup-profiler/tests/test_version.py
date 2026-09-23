"""CMRU release tags are the authority for OCI image version coordinates."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from lib import version


def _project_root(tmp_path: Path, prefix: str = "cgprofile-v") -> Path:
    (tmp_path / "cmru.toml").write_text(
        f'[project]\nprefix = "{prefix}"\n', encoding="utf-8",
    )
    return tmp_path


def _git_tags(monkeypatch, *, stdout: str = "", returncode: int = 0, stderr: str = ""):
    def fake_run(argv, **kwargs):
        assert argv == ["git", "tag", "--points-at", "HEAD"]
        return subprocess.CompletedProcess(argv, returncode, stdout, stderr)

    monkeypatch.setattr(version.subprocess, "run", fake_run)


def test_runtime_version_uses_source_fallback_when_environment_is_absent(monkeypatch):
    monkeypatch.delenv(version.VERSION_ENV, raising=False)
    assert version.runtime_version("1.0.0") == "1.0.0"


def test_runtime_version_refuses_empty_embedded_identity(monkeypatch):
    monkeypatch.setenv(version.VERSION_ENV, "  ")
    with pytest.raises(RuntimeError, match="CGPROFILE_VERSION must be a semantic version"):
        version.runtime_version("1.0.0")


def test_runtime_version_uses_embedded_release_identity(monkeypatch):
    monkeypatch.setenv(version.VERSION_ENV, " 1.1.0 ")
    assert version.runtime_version("1.0.0") == "1.1.0"


@pytest.mark.parametrize("bad", ["latest", "1.1.0-01"])
def test_runtime_version_refuses_invalid_embedded_identity(monkeypatch, bad):
    monkeypatch.setenv(version.VERSION_ENV, bad)
    with pytest.raises(RuntimeError, match="CGPROFILE_VERSION must be a semantic version"):
        version.runtime_version("1.0.0")


def test_exact_release_tag_is_authoritative_over_stale_environment(tmp_path, monkeypatch):
    root = _project_root(tmp_path)
    _git_tags(monkeypatch, stdout="assay-v7.0.0\n\ncgprofile-v1.1.0\n")
    assert version.resolve_build_version(
        root, require_release_tag=True, environ={version.VERSION_ENV: "9.9.9"},
    ) == "1.1.0"


def test_multiple_project_tags_at_head_are_refused(tmp_path, monkeypatch):
    root = _project_root(tmp_path)
    _git_tags(monkeypatch, stdout="cgprofile-v1.0.0\ncgprofile-v1.1.0\n")
    with pytest.raises(RuntimeError, match="multiple .* release tags"):
        version.resolve_build_version(root, require_release_tag=True, environ={})


def test_malformed_project_tag_at_head_is_refused(tmp_path, monkeypatch):
    root = _project_root(tmp_path)
    _git_tags(monkeypatch, stdout="cgprofile-vlatest\n")
    with pytest.raises(RuntimeError, match="release tag .* semantic version"):
        version.resolve_build_version(root, require_release_tag=True, environ={})


def test_valid_manual_version_is_used_when_no_project_tag_exists(tmp_path, monkeypatch):
    root = _project_root(tmp_path)
    _git_tags(monkeypatch)
    assert version.resolve_build_version(
        root, require_release_tag=True, environ={version.VERSION_ENV: "2.3.4"},
    ) == "2.3.4"


def test_invalid_manual_version_is_refused(tmp_path, monkeypatch):
    root = _project_root(tmp_path)
    _git_tags(monkeypatch)
    with pytest.raises(RuntimeError, match="CGPROFILE_VERSION must be a semantic version"):
        version.resolve_build_version(
            root, require_release_tag=False, environ={version.VERSION_ENV: "latest"},
        )


def test_untagged_local_build_uses_development_identity(tmp_path, monkeypatch):
    root = _project_root(tmp_path)
    _git_tags(monkeypatch)
    assert version.resolve_build_version(root, require_release_tag=False, environ={}) == "0.0.0-dev"


def test_publish_without_tag_or_override_is_refused(tmp_path, monkeypatch):
    root = _project_root(tmp_path)
    _git_tags(monkeypatch)
    with pytest.raises(RuntimeError, match="no cgprofile-v<version> tag points at HEAD"):
        version.resolve_build_version(root, require_release_tag=True, environ={})


def test_git_tag_probe_failure_is_not_masked_by_local_build_fallback(tmp_path, monkeypatch):
    root = _project_root(tmp_path)
    _git_tags(monkeypatch, returncode=128, stderr="not a git repository")
    with pytest.raises(RuntimeError, match="cannot resolve release tag at HEAD"):
        version.resolve_build_version(root, require_release_tag=False, environ={})


def test_project_prefix_is_read_from_cmru_contract(tmp_path, monkeypatch):
    root = _project_root(tmp_path, prefix="profile-v")
    _git_tags(monkeypatch, stdout="profile-v3.2.1\n")
    assert version.resolve_build_version(root, require_release_tag=True, environ={}) == "3.2.1"


def test_missing_project_prefix_is_refused(tmp_path):
    (tmp_path / "cmru.toml").write_text("[project]\nid = 'cgroup-profiler'\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="must declare a non-empty project.prefix"):
        version.resolve_build_version(tmp_path, require_release_tag=False, environ={})


def test_non_table_project_is_refused(tmp_path):
    (tmp_path / "cmru.toml").write_text("project = 'not a table'\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="must declare a non-empty project.prefix"):
        version.resolve_build_version(tmp_path, require_release_tag=False, environ={})


def test_build_version_reads_ambient_override_when_mapping_omitted(monkeypatch, tmp_path):
    root = _project_root(tmp_path)
    _git_tags(monkeypatch)
    monkeypatch.setenv(version.VERSION_ENV, "3.4.5")
    assert version.resolve_build_version(root, require_release_tag=True) == "3.4.5"


def test_daemon_version_uses_embedded_release_identity_in_fresh_process():
    import os
    import sys

    env = dict(os.environ, CGPROFILE_VERSION="1.1.0")
    result = subprocess.run(
        [sys.executable, "-c", "from lib.serve import CGPROFILE_VERSION; print(CGPROFILE_VERSION)"],
        cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True,
        check=False, env=env,
    )
    assert result.returncode == 0
    assert result.stdout == "1.1.0\n"
    assert result.stderr == ""
