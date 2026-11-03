"""Regression tests for the cmru-1 bug-fix package (tee flush, KI-67, KI-102)."""
from __future__ import annotations

import os
import subprocess
import sys
import textwrap
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


@pytest.mark.parametrize("dry_run, resume", [(True, None), (False, "/some/candidate"), (False, None)])
def test_ki102_other_modes_keep_their_commits(candidate, dry_run, resume):
    before = _git(candidate, "rev-parse", "HEAD")
    vargs = SimpleNamespace(dry_run=dry_run, resume=resume)

    with cli._resumed_dry_run_leaves_candidate_tip_unchanged(vargs, candidate):
        _commit_prepared(candidate)

    assert _git(candidate, "rev-parse", "HEAD") != before


# --- tee flush: a broken pipe on flush must not skip the descriptor restore --------------

_BROKEN_PIPE_SCRIPT = textwrap.dedent(
    """
    import os, sys
    from cmru import cli

    original = os.fstat(1)
    log = sys.argv[1]
    os.environ["CMRU_RELEASE_LOG"] = log
    os.environ.pop("CMRU_NATIVE_RELEASE_LOGGING", None)

    class Broken:
        def flush(self):
            raise BrokenPipeError("closed pipe")

    outcome = "none"
    try:
        with cli._native_release_logging_scope():
            cli._configure_native_release_logging(__import__("pathlib").Path(log).parent, append=False)
            real_stdout, real_stderr = sys.stdout, sys.stderr
            sys.stdout = Broken()
            sys.stderr = real_stderr
            try:
                raise RuntimeError("in-flight")
            finally:
                pass
    except BaseException as exc:
        outcome = type(exc).__name__ + ":" + ";".join(getattr(exc, "__notes__", []))
    finally:
        sys.stdout = real_stdout
    restored = os.fstat(1).st_ino == original.st_ino
    print("OUTCOME", outcome, "RESTORED", restored, "ACTIVE", cli._ACTIVE_NATIVE_RELEASE_LOGGING)
    """
)


def test_tee_flush_broken_pipe_still_restores_descriptors_and_keeps_the_inflight_exception(tmp_path):
    result = subprocess.run(
        [sys.executable, "-c", _BROKEN_PIPE_SCRIPT, str(tmp_path / "release.log")],
        capture_output=True, text=True, check=False, timeout=60,
        env={**os.environ, "PYTHONPATH": os.pathsep.join(
            [str(Path(cli.__file__).resolve().parent.parent), os.environ.get("PYTHONPATH", "")])},
    )

    line = next(row for row in result.stdout.splitlines() if row.startswith("OUTCOME"))
    assert "OUTCOME RuntimeError:" in line  # the in-flight exception is not masked
    assert "BrokenPipeError" in line  # the flush failure is recorded as a note
    assert "RESTORED True" in line
    assert "ACTIVE None" in line


def test_tee_flush_broken_pipe_without_an_inflight_exception_is_raised_after_the_restore(tmp_path):
    script = _BROKEN_PIPE_SCRIPT.replace('raise RuntimeError("in-flight")', "pass")

    result = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path / "release.log")],
        capture_output=True, text=True, check=False, timeout=60,
        env={**os.environ, "PYTHONPATH": os.pathsep.join(
            [str(Path(cli.__file__).resolve().parent.parent), os.environ.get("PYTHONPATH", "")])},
    )

    line = next(row for row in result.stdout.splitlines() if row.startswith("OUTCOME"))
    assert "OUTCOME BrokenPipeError:" in line
    assert "RESTORED True" in line
