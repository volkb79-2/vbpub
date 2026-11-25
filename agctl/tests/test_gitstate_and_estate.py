"""Git parsing, row provenance and the `estate` handler — fixtures only, never the live estate.

Fixtures are recorded porcelain text, and repositories this test creates under `tmp_path` with
no remote (so the `origin/main` merge read is exercised as the stated unknown it must be);
the CLI is driven in-process with a fake runtime, never as a subprocess against a checkout.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from agctl import config as ag_config
from agctl import estate as agg
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


class FakeOutput:
    """The one output method the handler uses (`runtime.output.primary`)."""

    def __init__(self) -> None:
        self.values: list[object] = []

    def primary(self, value: object) -> None:
        self.values.append(value)

    @property
    def last(self) -> object:
        return self.values[-1]


class FakeRuntime:
    def __init__(self, json_mode: bool = False) -> None:
        self.json_mode = json_mode
        self.output = FakeOutput()


class Args:
    header = False


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


def test_a_locked_line_without_reason_still_reads_as_locked():
    (entry,) = gitstate.parse_worktree_porcelain("worktree /repo/wt\nHEAD aaaaaaa\nlocked\n")

    assert entry.locked == "locked"


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


def test_read_view_reports_an_unreachable_merge_base_as_unknown(tmp_path):
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


# --- rows -------------------------------------------------------------------------------------


def _view(tmp_path: Path, merged: frozenset[str] = frozenset({"main"})) -> gitstate.GitView:
    worktrees = (
        gitstate.Worktree(str(tmp_path / "repo"), "1" * 40, "main", False, False, None),
        gitstate.Worktree(str(tmp_path / "repo" / ".worktrees" / "plat-99"), "2" * 40, "plat-99-x", False, False, None),
        gitstate.Worktree(str(tmp_path / "wt-detached"), "3" * 40, None, True, False, None),
        gitstate.Worktree(str(tmp_path / "bare.git"), None, None, False, True, None),
    )
    return gitstate.GitView(str(tmp_path / "repo"), worktrees, merged, "present")


def test_a_row_is_created_only_for_an_identified_branch(tmp_path):
    rows = agg.rows_from_git(_view(tmp_path))

    assert [row.package for row in rows] == ["plat-99-x"]


def test_every_cell_states_its_source_and_its_state(tmp_path):
    (row,) = agg.rows_from_git(_view(tmp_path))

    assert row.fields["branch"].value == "plat-99-x"
    assert row.fields["branch"].source == agg.GIT_SOURCE
    assert row.fields["head"].value == "2" * 40
    assert row.fields["merged"].value == "no"
    for name in ("owner", "plan", "runs", "gates", "handover"):
        cell = row.fields[name]
        assert cell.value == agg.UNKNOWN
        assert cell.state == agg.UNKNOWN
        assert "AGC-1" in cell.source


def test_the_row_cell_for_a_worktree_reports_the_source_age(tmp_path):
    worktree = tmp_path / "repo" / ".worktrees" / "plat-99"
    worktree.mkdir(parents=True)
    (row,) = agg.rows_from_git(_view(tmp_path))

    assert row.fields["worktree"].value == str(worktree)
    assert row.fields["worktree"].mtime is not None


def test_an_unreadable_merge_list_makes_the_merged_cell_unknown_with_its_reason(tmp_path):
    view = gitstate.GitView(
        str(tmp_path / "repo"),
        (gitstate.Worktree(str(tmp_path / "repo" / ".worktrees" / "p"), "2" * 40, "p", False, False, None),),
        frozenset(),
        "unknown",
        "origin/main is missing",
    )
    (row,) = agg.rows_from_git(view)

    assert row.fields["merged"].value == "unknown"
    assert row.fields["merged"].detail == "origin/main is missing"


def test_a_head_less_branch_row_names_the_missing_porcelain_line(tmp_path):
    view = gitstate.GitView(
        str(tmp_path / "repo"),
        (gitstate.Worktree(str(tmp_path / "repo" / ".worktrees" / "p"), None, "p", False, False, None),),
        frozenset(),
        "present",
    )
    (row,) = agg.rows_from_git(view)

    assert row.fields["head"].state == agg.UNKNOWN
    assert "no HEAD line" in row.fields["head"].detail


# --- rendering and the handler -----------------------------------------------------------------


def test_text_rendering_states_the_unknown_coverage(tmp_path):
    text = agg.render_text(agg.rows_from_git(_view(tmp_path)), _view(tmp_path))

    assert "PACKAGE plat-99-x" in text
    assert "unverified: every cell whose state is 'unknown'" in text
    # Every cell line carries its source, so a reader never has to guess one.
    assert agg.GIT_SOURCE in text


def test_text_rendering_names_an_unknown_merge_state(tmp_path):
    view = gitstate.GitView(str(tmp_path), (), frozenset(), "unknown", "no remote")

    assert "merge state UNKNOWN (no remote)" in agg.render_text([], view)


@pytest.fixture
def no_repository(tmp_path, monkeypatch):
    """A config whose CONTROLLER_HOME sits outside any repository, with git answering nothing."""

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(gitstate, "_run_git", lambda arguments, cwd: ("", 128, "not a git repository"))
    return ag_config.load({"CONTROLLER_HOME": str(home)}, cwd=tmp_path)


def test_the_handler_reports_an_unresolvable_root_as_unknown(no_repository):
    runtime = FakeRuntime()

    assert agg.run(Args(), runtime, config=no_repository) == 0
    assert str(runtime.output.last).startswith("estate: UNKNOWN")


def test_the_json_handler_reports_an_unresolvable_root_as_structured_unknown(no_repository):
    runtime = FakeRuntime(json_mode=True)

    assert agg.run(Args(), runtime, config=no_repository) == 0
    payload = runtime.output.last
    assert payload["root"] is None
    assert payload["rows"] == []
    assert "detail" in payload["unknown"]["root"]


def test_the_handler_prints_rows_from_a_fixture_worktree(tmp_path, monkeypatch):
    view = _view(tmp_path)
    monkeypatch.setattr(gitstate, "discover_root", lambda start: (str(tmp_path / "repo"), ""))
    monkeypatch.setattr(gitstate, "read_view", lambda root: view)
    config = ag_config.load({"CONTROLLER_HOME": str(tmp_path / "home")}, cwd=tmp_path)

    runtime = FakeRuntime(json_mode=True)
    assert agg.run(Args(), runtime, config=config) == 0
    payload = runtime.output.last
    assert payload["schema"] == "agctl-estate/1"
    assert payload["root"] == str(tmp_path / "repo")
    assert payload["rows"][0]["package"] == "plat-99-x"
    assert payload["rows"][0]["merged"]["value"] == "no"

    runtime_text = FakeRuntime()
    assert agg.run(Args(), runtime_text, config=config) == 0
    assert "PACKAGE plat-99-x" in str(runtime_text.output.last)
