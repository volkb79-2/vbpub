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


def test_merged_parsing_strips_the_ref_prefix():
    # `--format=%(refname)` prints the full ref. `%(refname:short)` is not used: a tag sharing a
    # branch name makes git print `heads/<name>` for the branch, which would silently turn a
    # merged branch into a merge candidate.
    assert gitstate.parse_merged("refs/heads/main\nrefs/heads/plat-22c\n\nrefs/heads/other \n") == frozenset(
        {"main", "plat-22c", "other"}
    )
    # A listing this parser does not recognise keeps its text rather than inventing a name.
    assert gitstate.parse_merged("main\n") == frozenset({"main"})


def test_the_merge_command_asks_for_the_full_ref_and_not_the_short_one(monkeypatch):
    seen: list[list[str]] = []

    def runner(arguments, cwd):
        seen.append(list(arguments))
        return (PORCELAIN if "worktree" in arguments else "refs/heads/main\n"), 0, ""

    monkeypatch.setattr(gitstate, "_run_git", runner)

    gitstate.read_view("/repo")

    assert seen[1][-1] == "--format=%(refname)"


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


def test_run_git_carries_an_allow_listed_environment(monkeypatch, tmp_path):
    seen: list[dict[str, object]] = []

    class Completed:
        stdout = "ok\n"
        returncode = 0
        stderr = ""

    def fake_run(argv, **kwargs):
        seen.append({"argv": argv, **kwargs})
        return Completed()

    monkeypatch.setenv("GIT_DIR", "/elsewhere/.git")
    monkeypatch.setenv("OPENDESIGN_API_KEY_VOLKER", "would-be-a-secret")
    monkeypatch.setattr(gitstate.subprocess, "run", fake_run)

    gitstate._run_git(["worktree", "list"], "/repo")

    environment = seen[0]["env"]
    # A caller-set GIT_DIR would make `worktree list` describe another repository; a credential
    # variable has no business in a read-only local call.
    assert "GIT_DIR" not in environment
    assert "OPENDESIGN_API_KEY_VOLKER" not in environment
    assert environment["GIT_OPTIONAL_LOCKS"] == "0"
    assert environment["GIT_TERMINAL_PROMPT"] == "0"
    assert environment["GIT_ASKPASS"] == ""


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

    root, detail = gitstate.discover_root(None, nested)

    assert root == str(repo)
    assert detail == ""


def test_discover_root_reports_no_repository_rather_than_a_traceback(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()

    root, detail = gitstate.discover_root(None, plain)

    assert root is None
    assert detail
    assert "no git work tree at" in detail


def test_discover_root_takes_the_declared_repository_before_the_cwd(tmp_path, monkeypatch):
    # `CONTROLLER_REPO` is the authoritative source (and the one the peer paths come from); the
    # cwd is only the fallback. Asking the cwd first would answer for a different repository
    # whenever the command is run inside a checkout that is not the declared one.
    elsewhere = tmp_path / "elsewhere" / "repo"
    elsewhere.mkdir(parents=True)
    nested = tmp_path / "cwd" / "agctl"
    nested.mkdir(parents=True)
    answers: list[str] = []

    def runner(arguments, cwd):
        answers.append(cwd)
        return str(elsewhere) + "\n", 0, ""

    monkeypatch.setattr(gitstate, "_run_git", runner)

    root, detail = gitstate.discover_root(str(elsewhere), nested)

    assert answers == [str(elsewhere)]
    assert root == str(elsewhere)
    assert detail == ""


def test_discover_root_never_asks_the_controller_home(tmp_path, monkeypatch):
    # The controller home is tool state, not a checkout, and it is not an input to this call at
    # all: a home that happens to be a git checkout used to make the whole view describe THAT
    # repository. It is a checkout on purpose here, and it must not be asked.
    controller_home = tmp_path / "controller"
    controller_home.mkdir()
    _git(controller_home, "init", "-q", "-b", "main")
    nested = tmp_path / "cwd"
    nested.mkdir()
    asked: list[str] = []

    def runner(arguments, cwd):
        asked.append(cwd)
        return "", 128, "no work tree"

    monkeypatch.setattr(gitstate, "_run_git", runner)

    root, detail = gitstate.discover_root(None, nested)

    assert asked == [str(nested)]
    assert root is None
    assert "no work tree" in detail


def test_a_declared_repository_that_is_not_a_checkout_is_a_stated_error(tmp_path, monkeypatch):
    configured = tmp_path / "absent-repo"
    nested = tmp_path / "start"
    nested.mkdir()
    called: list[str] = []

    def runner(arguments, cwd):
        called.append(cwd)
        return "", 128, "nope"

    monkeypatch.setattr(gitstate, "_run_git", runner)

    root, detail = gitstate.discover_root(str(configured), nested)

    # The caller named a repository: answering for the cwd instead would be a different
    # repository's worktrees under the declared repository's peer paths.
    assert called == []
    assert root is None
    assert "is not a directory" in detail
    assert "CONTROLLER_REPO" in detail


def test_a_declared_repository_without_a_work_tree_says_so(tmp_path, monkeypatch):
    configured = tmp_path / "repo"
    configured.mkdir()
    monkeypatch.setattr(gitstate, "_run_git", lambda arguments, cwd: ("", 128, "fatal: not a git repository"))

    root, detail = gitstate.discover_root(str(configured), tmp_path)

    assert root is None
    assert "names no git work tree" in detail
    assert "fatal: not a git repository" in detail


def test_discover_root_answers_an_absent_cwd_without_calling_git(tmp_path, monkeypatch):
    absent = tmp_path / "absent"
    calls: list[str] = []

    def runner(arguments, cwd):
        calls.append(cwd)
        return "", 128, "nope"

    monkeypatch.setattr(gitstate, "_run_git", runner)

    root, detail = gitstate.discover_root(None, absent)

    # The parent is asked, not the absent directory itself.
    assert calls == [str(tmp_path)]
    assert root is None
    assert "nope" in detail


def test_discover_root_names_the_directory_it_asked_when_nothing_answers(tmp_path, monkeypatch):
    start = tmp_path / "start"
    start.mkdir()
    monkeypatch.setattr(gitstate, "_run_git", lambda arguments, cwd: ("", 128, ""))

    root, detail = gitstate.discover_root(None, start)

    assert root is None
    assert detail == f"no git work tree at {start}: git rev-parse exited 128"


def test_two_lines_after_a_locked_line_still_flush_once_per_block():
    entries = gitstate.parse_worktree_porcelain(
        "worktree /repo/a\nlocked by hand\nHEAD aaaaaaa\nunrecognised line\n\nworktree /repo/b\nbare\n"
    )

    assert [entry.path for entry in entries] == ["/repo/a", "/repo/b"]
    assert entries[0].locked == "by hand"
    assert entries[1].bare is True
