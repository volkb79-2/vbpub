"""Regression tests for the cmru-1 bug-fix package (tee flush, KI-67, KI-102)."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from cmru import cli


# --- KI-67: a stale egg-info next to the source must not win the metadata lookup ---------

def _write_metadata(directory: Path, name: str, version: str) -> None:
    directory.mkdir(parents=True)
    (directory / "METADATA").write_text(
        f"Metadata-Version: 2.1\nName: cmru\nVersion: {version}\n", encoding="utf-8",
    )


def test_ki67_launcher_program_appends_a_root_that_carries_an_egg_info(tmp_path):
    stale_root = tmp_path / "src"
    (stale_root / "cmru.egg-info").mkdir(parents=True)
    clean_root = tmp_path / "lib"
    clean_root.mkdir()

    program = cli._bound_launcher_program([stale_root, clean_root])

    assert f"sys.path[:0] = {[str(clean_root)]!r}" in program
    assert f"sys.path.extend({[str(stale_root)]!r})" in program


def test_ki67_installed_metadata_beats_a_stale_egg_info_under_the_source_root(tmp_path):
    site = tmp_path / "site"
    _write_metadata(site / "cmru-9.9.9.dist-info", "cmru", "9.9.9")
    source_root = tmp_path / "src"
    _write_metadata(source_root / "cmru.egg-info", "cmru", "5.3.2.dev36")
    setup = cli._bound_launcher_program([source_root]).split("from cmru.cli", 1)[0]

    found = subprocess.run(
        [sys.executable, "-I", "-c",
         f"import sys; sys.path.insert(0, {str(site)!r}); {setup}"
         "import importlib.metadata as m; print(m.version('cmru'))"],
        capture_output=True, text=True, check=True,
    )

    assert found.stdout.strip() == "9.9.9"


def test_ki67_a_prepended_stale_egg_info_would_win(tmp_path):
    """Control: the old prepend ordering resolves the stale metadata."""
    site = tmp_path / "site"
    _write_metadata(site / "cmru-9.9.9.dist-info", "cmru", "9.9.9")
    source_root = tmp_path / "src"
    _write_metadata(source_root / "cmru.egg-info", "cmru", "5.3.2.dev36")

    found = subprocess.run(
        [sys.executable, "-I", "-c",
         f"import sys; sys.path.insert(0, {str(site)!r}); sys.path[:0] = {[str(source_root)]!r}; "
         "import importlib.metadata as m; print(m.version('cmru'))"],
        capture_output=True, text=True, check=True,
    )

    assert found.stdout.strip() == "5.3.2.dev36"


# --- KI-102: a resumed dry run leaves the retained candidate tip unchanged --------------

def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
        cwd=repo, capture_output=True, text=True, check=True,
    ).stdout.strip()


@pytest.fixture
def candidate(tmp_path):
    repo = tmp_path / "candidate"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / "a.txt").write_text("a\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    return repo


def _commit_prepared(repo: Path) -> None:
    (repo / "generated.txt").write_text("prepared\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "chore(demo): prepare release inputs")


def test_ki102_resumed_dry_run_restores_the_tip_after_a_prepared_inputs_commit(candidate):
    before = _git(candidate, "rev-parse", "HEAD")
    vargs = SimpleNamespace(dry_run=True, resume="/some/candidate")

    with cli._resumed_dry_run_leaves_candidate_tip_unchanged(vargs, candidate):
        _commit_prepared(candidate)
        assert _git(candidate, "rev-parse", "HEAD") != before

    assert _git(candidate, "rev-parse", "HEAD") == before
    assert not (candidate / "generated.txt").exists()


def test_ki102_the_tip_is_restored_when_the_dry_run_exits_early(candidate):
    before = _git(candidate, "rev-parse", "HEAD")
    vargs = SimpleNamespace(dry_run=True, resume="/some/candidate")

    with pytest.raises(SystemExit):
        with cli._resumed_dry_run_leaves_candidate_tip_unchanged(vargs, candidate):
            _commit_prepared(candidate)
            raise SystemExit(4)

    assert _git(candidate, "rev-parse", "HEAD") == before


def test_ki102_a_resumed_dry_run_that_commits_nothing_changes_nothing(candidate):
    before = _git(candidate, "rev-parse", "HEAD")
    vargs = SimpleNamespace(dry_run=True, resume="/some/candidate")

    with cli._resumed_dry_run_leaves_candidate_tip_unchanged(vargs, candidate):
        pass

    assert _git(candidate, "rev-parse", "HEAD") == before


@pytest.mark.parametrize("dry_run, resume", [(True, None), (False, "/some/candidate"), (False, None)])
def test_ki102_other_modes_keep_their_commits(candidate, dry_run, resume):
    before = _git(candidate, "rev-parse", "HEAD")
    vargs = SimpleNamespace(dry_run=dry_run, resume=resume)

    with cli._resumed_dry_run_leaves_candidate_tip_unchanged(vargs, candidate):
        _commit_prepared(candidate)

    assert _git(candidate, "rev-parse", "HEAD") != before


# --- tee flush: a broken pipe on flush must not skip the descriptor restore --------------

class _BrokenStream:
    def flush(self):
        raise BrokenPipeError("closed pipe")


def _open_tee_scope(monkeypatch, tmp_path):
    monkeypatch.setenv("CMRU_RELEASE_LOG", str(tmp_path / "release.log"))
    monkeypatch.delenv("CMRU_NATIVE_RELEASE_LOGGING", raising=False)
    return os.fstat(1)


def test_tee_flush_broken_pipe_still_restores_descriptors_and_keeps_the_inflight_exception(
    monkeypatch, tmp_path,
):
    original = _open_tee_scope(monkeypatch, tmp_path)

    with pytest.raises(RuntimeError, match="in-flight") as excinfo:
        with cli._native_release_logging_scope():
            cli._configure_native_release_logging(tmp_path, append=False)
            tee = cli._ACTIVE_NATIVE_RELEASE_LOGGING.process
            monkeypatch.setattr(sys, "stdout", _BrokenStream())
            raise RuntimeError("in-flight")

    assert any("BrokenPipeError" in note for note in excinfo.value.__notes__)
    assert os.fstat(1).st_ino == original.st_ino
    assert cli._ACTIVE_NATIVE_RELEASE_LOGGING is None
    assert tee.wait(timeout=5) == 0


def test_tee_flush_with_both_streams_broken_reports_the_first_failure_only(
    monkeypatch, tmp_path,
):
    original = _open_tee_scope(monkeypatch, tmp_path)

    class _Closed:
        def flush(self):
            raise ValueError("stderr closed")

    with pytest.raises(BrokenPipeError, match="closed pipe"):
        with cli._native_release_logging_scope():
            cli._configure_native_release_logging(tmp_path, append=False)
            tee = cli._ACTIVE_NATIVE_RELEASE_LOGGING.process
            monkeypatch.setattr(sys, "stdout", _BrokenStream())
            monkeypatch.setattr(sys, "stderr", _Closed())

    assert os.fstat(1).st_ino == original.st_ino
    assert tee.wait(timeout=5) == 0


def test_tee_flush_broken_pipe_without_an_inflight_exception_is_raised_after_the_restore(
    monkeypatch, tmp_path,
):
    original = _open_tee_scope(monkeypatch, tmp_path)

    with pytest.raises(BrokenPipeError):
        with cli._native_release_logging_scope():
            cli._configure_native_release_logging(tmp_path, append=False)
            tee = cli._ACTIVE_NATIVE_RELEASE_LOGGING.process
            monkeypatch.setattr(sys, "stdout", _BrokenStream())

    assert os.fstat(1).st_ino == original.st_ino
    assert cli._ACTIVE_NATIVE_RELEASE_LOGGING is None
    assert tee.wait(timeout=5) == 0
