"""Coverage of the merge_digest git-subprocess degradation branches.

`_diff_name_status`/`_diff_numstat` first resolve the repo root and first-parent
via `git rev-parse` (`_repo_root`, `_git_rev_parse`), THEN run `git diff-tree`.
A naive GLOBAL subprocess monkeypatch short-circuits at rev-parse (first_parent
becomes None → early return before the diff-tree branch), so these tests use a
SELECTIVE fake: rev-parse/show-toplevel succeed, only diff-tree fails or raises.
This reaches the nonzero-return and OSError/TimeoutExpired fallbacks — the exact
graceful-degradation the module exists to provide.
"""
import subprocess
from pathlib import Path

from nyxloom import merge_digest


class _FakeProc:
    def __init__(self, returncode: int, stdout: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = ""


def _selective(diff_tree_behavior):
    """git rev-parse (incl. --show-toplevel) succeeds; diff-tree -> behavior()."""
    def _run(argv, **kw):
        if "diff-tree" in argv:
            return diff_tree_behavior()
        if "rev-parse" in argv:
            return _FakeProc(0, "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef\n")
        return _FakeProc(0, "")
    return _run


_EMPTY_DIFFSTAT = {"files_changed": 0, "insertions": 0, "deletions": 0}


def test_diff_name_status_nonzero_returns_empty(monkeypatch):
    monkeypatch.setattr(merge_digest.subprocess, "run", _selective(lambda: _FakeProc(1)))
    assert merge_digest._diff_name_status(Path("/x"), "c0ffee") == []


def test_diff_numstat_nonzero_returns_empty(monkeypatch):
    monkeypatch.setattr(merge_digest.subprocess, "run", _selective(lambda: _FakeProc(1)))
    assert merge_digest._diff_numstat(Path("/x"), "c0ffee") == _EMPTY_DIFFSTAT


def _raise_oserror():
    raise OSError("git could not be executed")


def _raise_timeout():
    raise subprocess.TimeoutExpired(cmd="git diff-tree", timeout=30)


def test_diff_name_status_oserror_returns_empty(monkeypatch):
    monkeypatch.setattr(merge_digest.subprocess, "run", _selective(_raise_oserror))
    assert merge_digest._diff_name_status(Path("/x"), "c0ffee") == []


def test_diff_numstat_timeout_returns_empty(monkeypatch):
    monkeypatch.setattr(merge_digest.subprocess, "run", _selective(_raise_timeout))
    assert merge_digest._diff_numstat(Path("/x"), "c0ffee") == _EMPTY_DIFFSTAT
