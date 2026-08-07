"""O1 (P15) — ``git.dirty_paths`` reads ``git status`` through the NUL-
delimited ``-z`` transport, never git's quoted/arrow-joined display form.

Negative (finding 5, sol's post-series review): the old implementation split
rename records on the literal display string ``" -> "`` and relied on git's
C-style quoting of unusual paths — both misread a path that itself contains
that exact substring or those exact bytes. Every fixture below is a REAL
path on a real filesystem, read back through a real ``git status``
subprocess, never a hand-rolled porcelain string.
"""

from __future__ import annotations

import os

import pytest

from assay.errors import AssayError, Outcome, ReasonCode
from assay.git import dirty_paths


def test_a_rename_reports_only_the_new_path_even_when_the_old_path_contains_the_arrow_text(
    git_repo,
):
    # The exact case a literal " -> " split gets wrong: the OLD path itself
    # contains that substring, so a display-string split could plausibly cut
    # it at the wrong place instead of at git's real (record-boundary) split.
    old_name = "old -> ish.py"
    git_repo.write(old_name, "x = 1\n")
    git_repo.commit_all("seed the arrow-named file")
    git_repo.git("mv", old_name, "new.py")

    paths = dirty_paths(git_repo.path)

    assert "new.py" in paths
    assert old_name not in paths
    assert not any(" -> " in path for path in paths)


def test_a_staged_path_containing_a_space_is_reported_exactly(git_repo):
    spaced = "with space.py"
    git_repo.write(spaced, "x = 1\n")
    git_repo.git("add", spaced)

    assert dirty_paths(git_repo.path) == (spaced,)


def test_an_untracked_path_containing_a_tab_is_reported_exactly(git_repo):
    tabbed = "with\ttab.py"
    git_repo.write(tabbed, "x = 1\n")

    assert dirty_paths(git_repo.path) == (tabbed,)


def test_an_unstaged_path_containing_a_non_ascii_character_is_reported_exactly(
    git_repo,
):
    unicode_name = "café.py"
    git_repo.write(unicode_name, "x = 1\n")
    git_repo.commit_all("seed the unicode file")
    git_repo.write(unicode_name, "x = 2\n")

    assert dirty_paths(git_repo.path) == (unicode_name,)


def test_an_untracked_path_containing_an_embedded_newline_is_reported_exactly(
    git_repo,
):
    weird = "weird\nname.py"
    git_repo.write(weird, "x = 1\n")

    assert dirty_paths(git_repo.path) == (weird,)


def test_multiple_dirty_categories_and_an_unusual_rename_are_all_reported_together(
    git_repo,
):
    git_repo.write("tracked.py", "x = 1\n")
    git_repo.commit_all("seed tracked.py")
    git_repo.write("tracked.py", "x = 2\n")  # unstaged
    git_repo.write("untracked.py", "z = 1\n")  # untracked
    git_repo.write("staged.py", "x = 1\n")
    git_repo.git("add", "staged.py")  # staged, no commit

    assert set(dirty_paths(git_repo.path)) == {
        "staged.py",
        "tracked.py",
        "untracked.py",
    }


def test_a_failing_git_status_invocation_raises_git_failed(tmp_path):
    # tmp_path is a real directory but NOT a git repository -- a genuine
    # non-zero exit from the underlying `git status`, proving `dirty_paths`
    # reports a failing invocation as GIT_FAILED rather than as an empty
    # (clean) tree. (Since A-134 every command shares one `_run_bytes`
    # failure path, so this no longer exercises a branch distinct from
    # `test_git_resolve_base.py`'s -- it still pins THIS function's contract.)
    with pytest.raises(AssayError) as excinfo:
        dirty_paths(tmp_path)

    assert excinfo.value.outcome is Outcome.ERROR
    assert excinfo.value.reason_code is ReasonCode.GIT_FAILED


def test_a_path_that_is_not_valid_utf8_is_rejected_not_silently_mangled(git_repo):
    # A real filename with a byte sequence that cannot be UTF-8 -- legal on
    # a POSIX filesystem (only NUL and '/' are actually forbidden), and the
    # exact case a deliberate decode-or-reject policy exists for.
    bad_name = b"bad_\xff\xfename.py"
    target_bytes = os.fsencode(str(git_repo.path.resolve())) + b"/" + bad_name
    os.close(os.open(target_bytes, os.O_CREAT | os.O_WRONLY, 0o644))

    with pytest.raises(AssayError) as excinfo:
        dirty_paths(git_repo.path)

    assert excinfo.value.outcome is Outcome.ERROR
    assert excinfo.value.reason_code is ReasonCode.GIT_FAILED
    assert "UTF-8" in str(excinfo.value)
