"""O1 -- ordinary regression coverage for :mod:`assay.git`'s P20 repository-
identity boundary, beyond the locked acceptance suite
(``nyxloom-trove/carve-assets/P20/test_acceptance.py``) and the existing
``test_git_*.py`` modules. Those prove the headline hostile-namespace and
UTF-8 behaviors end to end, mostly through real subprocesses (including one
that runs in a CHILD Python process specifically to prove PATH-absence
in-process coverage tooling cannot see). This module proves the remaining
executable-resolution and repository-discovery branches directly, in-process,
with injected boundaries (``monkeypatch`` on ``os.environ``/``shutil.which``/
``subprocess.run``) rather than real subprocess isolation -- each is a
programmer-facing internal function, not part of the module's public surface,
but AUTHORING's own "test the private function directly" precedent
(``measurability._check_ancestor_or_equal``) applies here identically.
"""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest

from assay import git as git_module
from assay.errors import AssayError, Outcome, ReasonCode


# --- _resolve_git_executable ---------------------------------------------------


def test_no_path_declared_at_all_is_git_failed(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("PATH", raising=False)
    with pytest.raises(AssayError) as excinfo:
        git_module._resolve_git_executable()
    assert excinfo.value.outcome is Outcome.ERROR
    assert excinfo.value.reason_code is ReasonCode.GIT_FAILED


def test_an_empty_path_is_git_failed(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PATH", "")
    with pytest.raises(AssayError) as excinfo:
        git_module._resolve_git_executable()
    assert excinfo.value.reason_code is ReasonCode.GIT_FAILED


def test_git_absent_from_a_real_but_unrelated_path_is_git_failed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    empty_bin = tmp_path / "empty-bin"
    empty_bin.mkdir()
    monkeypatch.setenv("PATH", str(empty_bin))
    with pytest.raises(AssayError) as excinfo:
        git_module._resolve_git_executable()
    assert excinfo.value.reason_code is ReasonCode.GIT_FAILED


def test_a_resolved_target_that_cannot_be_stat_raises_git_failed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """``shutil.which`` found something, but it vanishes before this
    function can resolve/stat it -- a genuine TOCTOU race, reproduced here
    by injecting the boundary rather than waiting on a real one
    (AUTHORING.md §3b.A/E)."""
    ghost = tmp_path / "git"
    monkeypatch.setattr(git_module.shutil, "which", lambda name, path: str(ghost))
    with pytest.raises(AssayError) as excinfo:
        git_module._resolve_git_executable()
    assert excinfo.value.reason_code is ReasonCode.GIT_FAILED


def test_a_resolved_target_that_is_a_directory_is_refused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    fake_git_dir = tmp_path / "git"
    fake_git_dir.mkdir()
    monkeypatch.setattr(git_module.shutil, "which", lambda name, path: str(fake_git_dir))
    with pytest.raises(AssayError) as excinfo:
        git_module._resolve_git_executable()
    assert excinfo.value.reason_code is ReasonCode.GIT_FAILED
    assert "not an absolute regular executable" in str(excinfo.value)


def test_a_resolved_target_that_is_not_executable_is_refused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    fake_git = tmp_path / "git"
    fake_git.write_bytes(b"#!/bin/sh\n")
    fake_git.chmod(stat.S_IRUSR | stat.S_IWUSR)  # no execute bit
    monkeypatch.setattr(git_module.shutil, "which", lambda name, path: str(fake_git))
    with pytest.raises(AssayError) as excinfo:
        git_module._resolve_git_executable()
    assert excinfo.value.reason_code is ReasonCode.GIT_FAILED


# --- _nearest_git_marker --------------------------------------------------------


def test_a_symlinked_git_marker_is_refused(tmp_path: Path):
    real_git_dir = tmp_path / "real-dot-git"
    real_git_dir.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    (project / ".git").symlink_to(real_git_dir)
    with pytest.raises(AssayError) as excinfo:
        git_module._nearest_git_marker(project)
    assert excinfo.value.reason_code is ReasonCode.GIT_FAILED
    assert "symlink" in str(excinfo.value)


def test_a_git_marker_that_is_neither_a_directory_nor_a_regular_file_is_refused(
    tmp_path: Path,
):
    project = tmp_path / "project"
    project.mkdir()
    os.mkfifo(project / ".git")
    with pytest.raises(AssayError) as excinfo:
        git_module._nearest_git_marker(project)
    assert excinfo.value.reason_code is ReasonCode.GIT_FAILED


def test_no_git_marker_anywhere_in_the_ancestor_chain_is_refused(tmp_path: Path):
    lonely = tmp_path / "a" / "b" / "c"
    lonely.mkdir(parents=True)
    with pytest.raises(AssayError) as excinfo:
        git_module._nearest_git_marker(lonely)
    assert excinfo.value.reason_code is ReasonCode.GIT_FAILED
    assert "no .git marker" in str(excinfo.value)


# --- _resolve_repo: the bootstrap rev-parse --absolute-git-dir call -----------


def test_an_invalid_gitfile_is_refused_as_git_failed(tmp_path: Path):
    """A ``.git`` REGULAR FILE (the linked-worktree/submodule shape) whose
    content is not a valid ``gitdir: ...`` pointer -- git's own bootstrap
    resolution fails outright (reproduced directly against a real git
    binary), never silently treated as a usable repository."""
    project = tmp_path / "project"
    project.mkdir()
    (project / ".git").write_text("not a real gitfile\n", encoding="utf-8")
    git_executable = git_module._resolve_git_executable()
    with pytest.raises(AssayError) as excinfo:
        git_module._resolve_repo(project, git_executable)
    assert excinfo.value.reason_code is ReasonCode.GIT_FAILED


def test_a_resolved_git_dir_that_is_not_an_existing_absolute_directory_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Belt-and-suspenders: even if the bootstrap call exits 0, a reported
    git-dir that is not an absolute, existing directory is refused rather
    than trusted blindly. A real git binary was not found to produce this
    shape for any constructible input (both a garbled and a dangling
    gitfile make git itself exit non-zero instead, covered above), so the
    boundary is injected directly here rather than left unreachable."""
    project = tmp_path / "project"
    project.mkdir()
    (project / ".git").mkdir()

    real_bounded = git_module._run_bounded

    def fake_bounded(argv):
        if "--absolute-git-dir" in argv:
            return 0, b"relative/not/absolute\n", b""
        return real_bounded(argv)

    monkeypatch.setattr(git_module, "_run_bounded", fake_bounded)
    git_executable = git_module._resolve_git_executable()
    with pytest.raises(AssayError) as excinfo:
        git_module._resolve_repo(project, git_executable)
    assert excinfo.value.reason_code is ReasonCode.GIT_FAILED
    assert "not an absolute, existing directory" in str(excinfo.value)
