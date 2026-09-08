"""B068 -- a LINKED git worktree whose main checkout's ``.git`` is absent.

**The measurement that shaped this module.** B068 was filed with a
"discriminator": an R2 ``sql-mutation`` lane was observed running fine in a
Mode-B container while an R0/R1 ``mock`` lane hard-failed
``ERROR``/``GIT_FAILED``, and the entry asked which caller of
:func:`assay.git._resolve_repo` R0/R1 reaches that R2 does not. Traced to
source and then reproduced end to end: **there is no such caller.**
``cli._cmd_run`` -> ``cli._run_reserved`` calls ``git.head_rev`` once,
unconditionally, before any tier dispatch exists to diverge -- so every
rigor level fails identically, and the field observation was a difference in
the two containers' MOUNTS, not in assay's code.

That is why the backlog's fix (a) ("make the resolution tolerate the failure
the way R2 apparently already does") was not available: nothing tolerates it.
Fix (b) shipped instead -- and it is the honest one on its own merits, because
git resolution is load-bearing for R0/R1's own semantics: the commit label on
every verdict, the dirty set behind ``clean_tree``, and the comparison base
all come from git, and a worktree whose object store is not present cannot
supply any of them. There is nothing to relax; there was only a useless
message (git's own ``fatal: not a git repository: (null)``) to replace.

The tests below pin BOTH halves so the split behavior cannot come back
unexplained: the tier-independence of the failure (:func:`
test_every_rigor_level_fails_identically...`), and the message that now names
the gap.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from assay import git as git_module
from assay.cli import main
from assay.errors import AssayError, Outcome, ReasonCode

from conftest import GitRepo


_LANES = """
schema_version = 2

[lanes.r0only]
scope = "S1"
rigor = ["R0"]
enforcement = "gate"
argv = ["python3", "-c", "print('ok')"]
env = {}
env_passthrough = ["PATH"]
allow_argv_append = false
budget = "2m"

[lanes.r2lane]
scope = "S1"
rigor = ["R0", "R1", "R2"]
enforcement = "gate"
argv = ["python3", "-c", "print('ok')"]
env = {}
env_passthrough = ["PATH"]
allow_argv_append = false
budget = "2m"

[lanes.r2lane.isolation]
snapshot_selection = "repository"

[lanes.r2lane.judge]
language = "python"
source_roots = ["pkg"]
fail_under = 0.0
allow_excluded = false
require_branch = false
mode = "whole_target"
targets = ["pkg/flags.py"]

[lanes.r2lane.judge.coverage]
format = "cobertura"
artifact = "cov.xml"

[lanes.r2lane.judge.mutation]
jobs = 1
max_mutants = 2
operators = ["python:bool-const-flip"]
"""


def _severed_linked_worktree(tmp_path: Path) -> Path:
    """Build a REAL linked worktree and then sever it, exactly the way a
    Mode-B container does: the worktree subtree is present and complete, the
    main checkout's ``.git`` is not reachable from it.

    Severing by MOVING the main repository's git directory (rather than by
    hand-writing a gitfile that points nowhere) keeps the fixture honest --
    ``git worktree add`` wrote the gitfile, and its content is git's, not
    the test's.
    """
    main = GitRepo(path=tmp_path / "main")
    main.path.mkdir()
    main.git("init", "-q", "-b", "main")
    main.git("config", "user.email", "assay-tests@example.com")
    main.git("config", "user.name", "assay tests")
    main.write("pkg/flags.py", "def enabled():\n    return True\n")
    main.write("assay.toml", _LANES)
    main.commit_all("seed")
    main.git("worktree", "add", "-q", str(tmp_path / "wt"), "-b", "wt")

    worktree = tmp_path / "wt"
    assert (worktree / ".git").is_file(), "git worktree add should write a gitfile"
    (main.path / ".git").rename(main.path / ".git-unmounted")
    return worktree


# --- the discriminator B068 asked about ----------------------------------------


@pytest.mark.parametrize("lane", ["r0only", "r2lane"])
def test_every_rigor_level_fails_identically_in_a_severed_linked_worktree(
    tmp_path: Path, lane: str, capsys: pytest.CaptureFixture[str]
):
    """R0-only and R0+R1+R2 reach the SAME refusal from the SAME call site.

    This is B068's "discriminator" pinned as the fact it actually is: the
    repository bootstrap runs upstream of every tier, so a future change that
    let one rigor level quietly skip or soften it -- the shape B068 assumed
    already existed -- turns this red instead of shipping as an unexplained
    split.
    """
    worktree = _severed_linked_worktree(tmp_path)

    exit_code = main(["run", lane, "--file", str(worktree / "assay.toml")])

    assert exit_code != 0
    err = capsys.readouterr().err
    assert "ERROR/GIT_FAILED" in err
    assert "is a LINKED git worktree" in err


def test_the_severed_worktree_is_the_only_thing_that_makes_them_differ(
    tmp_path: Path,
):
    """Control: with the main repository present, the identical fixture
    resolves -- proving the refusal above is the severing and not the lane
    file, the worktree layout, or the tier set.
    """
    worktree = _severed_linked_worktree(tmp_path)
    (tmp_path / "main" / ".git-unmounted").rename(tmp_path / "main" / ".git")

    assert git_module._linked_worktree_gap(worktree) is None
    assert len(git_module.head_rev(worktree)) == 40


# --- the message ---------------------------------------------------------------


def test_the_refusal_names_the_missing_git_directory_and_a_remedy(tmp_path: Path):
    worktree = _severed_linked_worktree(tmp_path)
    missing = tmp_path / "main" / ".git" / "worktrees" / "wt"

    with pytest.raises(AssayError) as excinfo:
        git_module.head_rev(worktree)

    message = str(excinfo.value)
    assert excinfo.value.outcome is Outcome.ERROR
    assert excinfo.value.reason_code is ReasonCode.GIT_FAILED
    assert "LINKED git worktree" in message
    assert str(missing) in message, "the path that is absent must be named"
    assert "Remedies:" in message
    # The raw git evidence is NAMED-then-kept, never replaced.
    assert "git rev-parse --absolute-git-dir failed resolving" in message


def test_no_lane_setting_routes_around_it_is_stated(tmp_path: Path):
    """``clean_tree = false`` was the operator's first guess in the field
    (B068's own text); the message has to close that door explicitly."""
    worktree = _severed_linked_worktree(tmp_path)
    with pytest.raises(AssayError) as excinfo:
        git_module.head_rev(worktree)
    assert "clean_tree" in str(excinfo.value)


# --- _linked_worktree_gap's own branches ---------------------------------------


def test_an_ordinary_repository_gets_no_linked_worktree_sentence(git_repo: GitRepo):
    """A plain ``.git`` DIRECTORY is not a linked worktree; a bootstrap
    failure there must keep the unchanged message rather than blame a
    redirect that does not exist."""
    assert git_module._linked_worktree_gap(git_repo.path) is None


def test_a_healthy_linked_worktree_gets_no_sentence(tmp_path: Path):
    worktree = _severed_linked_worktree(tmp_path)
    (tmp_path / "main" / ".git-unmounted").rename(tmp_path / "main" / ".git")
    assert git_module._linked_worktree_gap(worktree) is None


def test_a_relative_gitdir_redirect_is_resolved_against_the_worktree(tmp_path: Path):
    """``gitdir:`` may be relative (git writes one for a worktree created
    with a relative path). Resolving it against the wrong anchor would report
    a healthy worktree as severed."""
    worktree = tmp_path / "wt"
    (worktree / "real-gitdir").mkdir(parents=True)
    (worktree / ".git").write_text("gitdir: real-gitdir\n", encoding="utf-8")
    assert git_module._linked_worktree_gap(worktree) is None

    (worktree / ".git").write_text("gitdir: gone\n", encoding="utf-8")
    gap = git_module._linked_worktree_gap(worktree)
    assert gap is not None and str(worktree / "gone") in gap


def test_a_marker_file_that_is_not_a_gitfile_is_named_as_such(tmp_path: Path):
    worktree = tmp_path / "wt"
    worktree.mkdir()
    (worktree / ".git").write_text("this is not a gitfile\n", encoding="utf-8")
    gap = git_module._linked_worktree_gap(worktree)
    assert gap is not None and "gitdir:" in gap


def test_an_empty_gitdir_path_is_named(tmp_path: Path):
    worktree = tmp_path / "wt"
    worktree.mkdir()
    (worktree / ".git").write_text("gitdir:   \n", encoding="utf-8")
    gap = git_module._linked_worktree_gap(worktree)
    assert gap is not None and "empty" in gap


def test_an_oversized_marker_file_is_refused_without_reading_it_all(tmp_path: Path):
    worktree = tmp_path / "wt"
    worktree.mkdir()
    (worktree / ".git").write_bytes(b"gitdir: /x\n" + b"A" * git_module._MAX_GITFILE_BYTES)
    gap = git_module._linked_worktree_gap(worktree)
    assert gap is not None and "larger than" in gap


def test_a_marker_file_that_is_not_utf8_is_named(tmp_path: Path):
    worktree = tmp_path / "wt"
    worktree.mkdir()
    (worktree / ".git").write_bytes(b"gitdir: \xff\xfe\n")
    gap = git_module._linked_worktree_gap(worktree)
    assert gap is not None and "UTF-8" in gap


def test_an_unreadable_marker_file_says_nothing_rather_than_guessing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """The diagnostic path must never raise ON TOP of the failure it is
    explaining -- an ``OSError`` here yields ``None`` and the plain message."""
    worktree = tmp_path / "wt"
    worktree.mkdir()
    (worktree / ".git").write_text("gitdir: /nowhere\n", encoding="utf-8")

    def boom(*args, **kwargs):
        raise OSError("injected")

    monkeypatch.setattr(Path, "open", boom)
    assert git_module._linked_worktree_gap(worktree) is None


def test_git_itself_writes_the_gitfile_shape_this_helper_parses(tmp_path: Path):
    """Guards the parse against a future git that stops writing
    ``gitdir: <path>``: the fixture asserts the real binary's output rather
    than trusting this module's memory of the format."""
    main = GitRepo(path=tmp_path / "main")
    main.path.mkdir()
    main.git("init", "-q", "-b", "main")
    main.git("config", "user.email", "assay-tests@example.com")
    main.git("config", "user.name", "assay tests")
    main.write("README.md", "seed\n")
    main.commit_all("seed")
    main.git("worktree", "add", "-q", str(tmp_path / "wt"), "-b", "wt")

    text = (tmp_path / "wt" / ".git").read_text(encoding="utf-8")
    assert text.startswith(git_module._GITFILE_PREFIX)
    target = Path(text[len(git_module._GITFILE_PREFIX) :].strip())
    if not target.is_absolute():
        target = tmp_path / "wt" / target
    assert target.is_dir()
    assert subprocess.run(["git", "-C", str(tmp_path / "wt"), "rev-parse", "HEAD"]).returncode == 0
