"""Git parsing and the one subprocess this package runs — fixtures only, never the live estate.

Fixtures are recorded porcelain text, and repositories this test creates under `tmp_path` with
no remote (so the `origin/main` merge read is exercised as the stated unknown it must be).
The failure paths of `_run_git` are exercised by substituting the runner, never by calling a
real git that might be slow or absent.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from agctl import gitstate

PORCELAIN = """\
worktree /repo
HEAD 1111111111111111111111111111111111111111
branch refs/heads/main

worktree /repo/.worktrees/plat-22c
HEAD 2222222222222222222222222222222222222222
branch refs/heads/plat-22c-resumebusy-20261125_031456

worktree /repo/.worktrees/review-tools
HEAD 3333333333333333333333333333333333333333
detached
"""


def _git(cwd: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", *arguments],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
        env={"HOME": str(cwd.parent), "PATH": "/usr/bin:/bin:/usr/local/bin", "GIT_TERMINAL_PROMPT": "0"},
    )
    assert completed.returncode == 0, completed.stderr
    return completed.stdout


# --- porcelain parsing -----------------------------------------------------------------------


def test_porcelain_parsing_keeps_branch_head_and_detached_entries():
    worktrees = gitstate.parse_worktree_porcelain(PORCELAIN)

    assert [entry.path for entry in worktrees] == [
        "/repo",
        "/repo/.worktrees/plat-22c",
        "/repo/.worktrees/review-tools",
    ]
    assert worktrees[0].branch == "main"
    assert worktrees[1].branch == "plat-22c-resumebusy-20261125_031456"
    assert worktrees[1].head == "2222222222222222222222222222222222222222"
    assert worktrees[2].detached is True and worktrees[2].branch is None


def test_a_block_without_a_head_line_reports_no_head_instead_of_inventing_one():
    (entry,) = gitstate.parse_worktree_porcelain("worktree /repo/.worktrees/odd\nbranch refs/heads/odd\n")

    assert entry.path == "/repo/.worktrees/odd"
    assert entry.head is None
    assert entry.branch == "odd"


def test_a_non_commit_head_value_is_not_read_as_a_commit_id():
    (entry,) = gitstate.parse_worktree_porcelain("worktree /repo\nHEAD not-a-commit\nbranch refs/heads/main\n")

    assert entry.head is None


def test_bare_and_locked_blocks_carry_their_flags():
    entries = gitstate.parse_worktree_porcelain("worktree /repo.git\nbare\n\nworktree /repo/wt\nHEAD aaaaaaa\nlocked by hand\n")

    assert entries[0].bare is True and entries[0].locked is None
    assert entries[1].locked == "by hand"


def test_a_damaged_branch_ref_keeps_the_short_name():
    (entry,) = gitstate.parse_worktree_porcelain("worktree /repo/wt\nHEAD aaaaaaa\nbranch odd-name\n")

    assert entry.branch == "odd-name"


def test_a_locked_line_without_reason_still_reads_as_locked():
    (entry,) = gitstate.parse_worktree_porcelain("worktree /repo/wt\nHEAD aaaaaaa\nlocked\n")

    assert entry.locked == "locked"


def test_a_branch_line_that_is_not_a_ref_is_kept_as_the_short_name():
    (entry,) = gitstate.parse_worktree_porcelain("worktree /repo/wt\nHEAD aaaaaaa\nbranch odd-name\n")

    # A `branch` line is normally `refs/heads/<name>`; a damaged one keeps its text instead of
    # being dropped, because the row can still say which branch the block claims.
    assert entry.branch == "odd-name"


def test_a_block_without_a_path_carries_no_entry():
    assert gitstate.parse_worktree_porcelain("HEAD aaaaaaa\nbranch refs/heads/x\n") == ()


def test_merged_parsing_is_one_short_name_per_line():
    assert gitstate.parse_merged("main\nplat-22c\n\n  other \n") == frozenset({"main", "plat-22c", "other"})


# --- the git view -----------------------------------------------------------------------------


def test_merged_here_answers_yes_no_and_unknown_without_a_merge_list():
    present = gitstate.GitView("/repo", (), frozenset({"main"}), "present")
    unknown = gitstate.GitView("/repo", (), frozenset(), "unknown", "no remote")

    assert present.merged_here("main") == "yes"
    assert present.merged_here("other") == "no"
    assert unknown.merged_here("main") == "unknown"
    assert unknown.merged_here(None) == "unknown"


def test_read_view_reports_a_repository_without_a_remote_as_unknown(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    (repo / "file.txt").write_text("one\n", encoding="utf-8")
    _git(repo, "add", "file.txt")
    _git(repo, "commit", "-q", "-m", "one")

    view = gitstate.read_view(str(repo))

    assert view.root == str(repo)
    assert view.merged_state == "unknown"
    assert "origin/main" in view.detail
    assert [entry.branch for entry in view.worktrees] == ["main"]


def test_read_view_reports_a_listing_that_prints_nothing_as_unknown(tmp_path, monkeypatch):
    monkeypatch.setattr(gitstate, "_run_git", lambda arguments, cwd: ("", 0, ""))

    view = gitstate.read_view(str(tmp_path))

    assert view.worktrees == ()
    assert view.merged_state == "unknown"
    assert "printed no entries" in view.detail


def test_read_view_reports_a_failed_listing_with_the_git_reason(tmp_path, monkeypatch):
    monkeypatch.setattr(gitstate, "_run_git", lambda arguments, cwd: ("", 128, "fatal: not a work tree"))

    view = gitstate.read_view(str(tmp_path))

    assert "git worktree list failed" in view.detail
    assert "fatal: not a work tree" in view.detail


def test_read_view_reads_the_merge_set_when_git_answers(tmp_path, monkeypatch):
    def runner(arguments, cwd):
        if "worktree" in arguments:
            return PORCELAIN, 0, ""
        return "main\nplat-22c\n", 0, ""

    monkeypatch.setattr(gitstate, "_run_git", runner)

    view = gitstate.read_view(str(tmp_path))

    assert view.merged_state == "present"
    assert view.merged_branches == frozenset({"main", "plat-22c"})


# --- the one subprocess ------------------------------------------------------------------------


def test_run_git_carries_no_optional_locks_and_a_bounded_timeout(monkeypatch, tmp_path):
    seen: list[dict[str, object]] = []

    class Completed:
        stdout = "ok\n"
        returncode = 0
        stderr = ""

    def fake_run(argv, **kwargs):
        seen.append({"argv": argv, **kwargs})
        return Completed()

    monkeypatch.setattr(gitstate.subprocess, "run", fake_run)

    stdout, code, detail = gitstate._run_git(["--no-optional-locks", "rev-parse"], str(tmp_path))

    assert (stdout, code, detail) == ("ok\n", 0, "")
    assert seen[0]["argv"] == ["git", "--no-optional-locks", "rev-parse"]
    assert seen[0]["timeout"] == gitstate.GIT_TIMEOUT_SECONDS
    # GIT_OPTIONAL_LOCKS=0 is the belt to --no-optional-locks's braces: a read must not write.
    assert seen[0]["env"]["GIT_OPTIONAL_LOCKS"] == "0"
    assert seen[0]["env"]["GIT_TERMINAL_PROMPT"] == "0"


def test_run_git_reports_a_missing_git_as_an_error_result(monkeypatch, tmp_path):
    def fake_run(argv, **kwargs):
        raise FileNotFoundError("git")

    monkeypatch.setattr(gitstate.subprocess, "run", fake_run)

    stdout, code, detail = gitstate._run_git(["rev-parse"], str(tmp_path))

    assert stdout == ""
    assert code == 127
    assert "FileNotFoundError" in detail


def test_run_git_reports_a_timeout_as_an_error_result(monkeypatch, tmp_path):
    def fake_run(argv, **kwargs):
        raise subprocess.TimeoutExpired(cmd=argv, timeout=1)

    monkeypatch.setattr(gitstate.subprocess, "run", fake_run)

    _stdout, code, detail = gitstate._run_git(["rev-parse"], str(tmp_path))

    assert code == 127
    assert "TimeoutExpired" in detail


# --- root discovery -----------------------------------------------------------------------------


def test_discover_root_answers_the_repository_root_from_a_subdirectory(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    nested = repo / "agctl"
    nested.mkdir()

    root, detail = gitstate.discover_root(nested)

    assert root == str(repo)
    assert detail == ""


def test_discover_root_reports_no_repository_rather_than_a_traceback(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()

    root, detail = gitstate.discover_root(plain)

    assert root is None
    assert detail


def test_discover_root_falls_back_to_the_configured_repository(tmp_path, monkeypatch):
    elsewhere = tmp_path / "elsewhere" / "repo"
    elsewhere.mkdir(parents=True)
    start = tmp_path / "start"
    start.mkdir()
    answers = {str(start): ("", 128, "not a work tree"), str(elsewhere): (str(elsewhere) + "\n", 0, "")}

    def runner(arguments, cwd):
        return answers[cwd]

    monkeypatch.setattr(gitstate, "_run_git", runner)
    monkeypatch.setenv("CONTROLLER_REPO", str(elsewhere))

    root, detail = gitstate.discover_root(start)

    assert root == str(elsewhere)
    assert detail == ""


def test_discover_root_names_the_directory_it_asked_when_nothing_answers(tmp_path, monkeypatch):
    start = tmp_path / "start"
    start.mkdir()
    monkeypatch.setattr(gitstate, "_run_git", lambda arguments, cwd: ("", 128, ""))

    root, detail = gitstate.discover_root(start)

    assert root is None
    assert detail == f"git rev-parse exited 128 in {start}"


def test_discover_root_asks_the_configured_repository_that_is_itself_absent(tmp_path, monkeypatch):
    start = tmp_path / "start"
    start.mkdir()
    configured = tmp_path / "absent-repo"
    answers: list[str] = []

    def runner(arguments, cwd):
        answers.append(cwd)
        return "", 128, "nope"

    monkeypatch.setattr(gitstate, "_run_git", runner)
    monkeypatch.setenv("CONTROLLER_REPO", str(configured))

    root, detail = gitstate.discover_root(start)

    # Both the start directory and the configured repository's PARENT were asked, and neither
    # answered; the reason names the last one it tried.
    assert answers == [str(start), str(tmp_path)]
    assert root is None
    assert "nope" in detail


def test_discover_root_answers_an_absent_start_without_calling_git(tmp_path, monkeypatch):
    absent = tmp_path / "absent"
    calls: list[str] = []

    def runner(arguments, cwd):
        calls.append(cwd)
        return "", 128, "nope"

    monkeypatch.setattr(gitstate, "_run_git", runner)

    root, detail = gitstate.discover_root(absent)

    # The parent is asked, not the absent directory itself.
    assert calls == [str(tmp_path)]
    assert root is None
    assert "nope" in detail


def test_discover_root_names_a_configured_path_that_is_not_even_a_parent(tmp_path, monkeypatch):
    start = tmp_path / "start"
    start.mkdir()
    monkeypatch.setenv("CONTROLLER_REPO", str(tmp_path / "absent" / "deeper" / "repo"))
    monkeypatch.setattr(gitstate, "_run_git", lambda arguments, cwd: ("", 128, "nope"))

    root, detail = gitstate.discover_root(start)

    # The configured path and its parent are both absent, so the reason names that rather than
    # reporting a git failure for a directory nobody could enter.
    assert root is None
    assert "is not a directory" in detail
