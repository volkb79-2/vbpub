"""Hostile-Git-boundary regressions that the REGISTERED GATE actually runs.

Why this file exists (reviewer, P20 O1/work item 2). The two-repository
attacks proving O1 lived only in ``nyxloom-trove/carve-assets/P20/`` -- the
carver-owned acceptance packet and its tracer. That material is run
separately by the controller and is NOT collected by the project's real ship
signal: ``assay.toml``'s lane argv is ``python -m pytest tests -q ...``, and
these files are not under ``tests/``. Measured, not assumed: deleting
``GIT_NO_REPLACE_OBJECTS`` from ``git._REPLACEMENT_ENV`` in a disposable copy
of the tree left the whole gated suite green at 1878 passed / 0 failed --
a named A-173 control whose removal the gate could not see. That is A-141's
rule one layer down ("a capability is not closed until the audit that
measures it moves too"), so each control now has an attack under ``tests/``.

Every test here proves an EXACT repository fact -- the resolved top, the full
HEAD OID, the diff's bytes, the dirty set -- never merely that git exited
zero, and every hostile process variable is set through ``monkeypatch`` so it
is restored for the sibling that shares the xdist worker (AUTHORING.md
§3b.B).

**Named residual, deliberately not asserted here.** A repository's own
``.git/info/exclude`` can still hide untracked dirt from
:func:`assay.git.dirty_paths`. It is repository CONTENT, not configuration,
so no command-line option reaches it, and reporting ignored paths instead
would collide with the requirement that the declared coverage artifact BE
git-ignored (work item 6 / A-140). Closing it needs a product decision about
which ignored paths are legitimately exempt; it is routed to the carver
rather than guessed at, and no test here pretends the current behaviour is
correct.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from conftest import GitRepo

from assay import git as git_module
from assay.errors import AssayError, ReasonCode


def _seed(root: Path, marker: str) -> tuple[str, str]:
    """A real two-commit repository whose content names *marker*, so a diff
    or HEAD taken from the WRONG repository is detectable by content, not
    merely by inequality."""
    subprocess.run(["git", "init", "-q", "-b", "main", str(root)], check=True)
    repo = GitRepo(path=root)
    repo.git("config", "user.name", "P20 hostile")
    repo.git("config", "user.email", "p20@example.invalid")
    repo.write("src/mod.zzz", f"{marker}-BASE\n")
    base = repo.commit_all("base")
    repo.write("src/mod.zzz", f"{marker}-BASE\n{marker}-HEAD\n")
    return base, repo.commit_all("head")


@pytest.fixture
def two_repos(tmp_path: Path) -> tuple[Path, str, str, Path, str]:
    repo_a = tmp_path / "alpha"
    repo_b = tmp_path / "beta"
    repo_a.mkdir()
    repo_b.mkdir()
    base_a, head_a = _seed(repo_a, "alpha")
    _, head_b = _seed(repo_b, "beta")
    assert head_a != head_b
    return repo_a, base_a, head_a, repo_b, head_b


def test_ambient_git_dir_and_work_tree_cannot_redirect_identity(
    two_repos, monkeypatch: pytest.MonkeyPatch
):
    """O1's own negative: pointing ``GIT_DIR``/``GIT_WORK_TREE`` at a second
    seeded repository must not change the recorded commit, the resolved top,
    or the path set."""
    repo_a, _, head_a, repo_b, head_b = two_repos
    monkeypatch.setenv("GIT_DIR", str(repo_b / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(repo_b))
    monkeypatch.setenv("GIT_OBJECT_DIRECTORY", str(repo_b / ".git" / "objects"))

    assert git_module.head_rev(repo_a) == head_a
    assert git_module.head_rev(repo_a) != head_b
    assert git_module.repo_top(repo_a) == repo_a.resolve()
    assert git_module.dirty_paths(repo_a) == ()

    (repo_a / "only-in-alpha.txt").write_text("x", encoding="utf-8")
    assert git_module.dirty_paths(repo_a) == ("only-in-alpha.txt",)


def test_ambient_git_config_count_cannot_inject_configuration(
    git_repo: GitRepo, monkeypatch: pytest.MonkeyPatch
):
    """``GIT_CONFIG_COUNT``/``KEY``/``VALUE`` is a config channel that needs
    no file on disk. Turning ``core.quotePath`` back ON would octal-escape a
    non-ASCII path inside a quoted string, which is exactly the spelling
    P15/A-134 removed -- the raw bytes must survive."""
    git_repo.write("keep.txt", "x\n")
    git_repo.commit_all("base")
    (git_repo.path / "café.txt").write_text("x", encoding="utf-8")

    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "core.quotePath")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "true")

    assert git_module.dirty_paths(git_repo.path) == ("café.txt",)


def test_a_repository_local_core_worktree_cannot_redirect_identity(two_repos):
    """The attack ``-C`` alone does not stop, and the reason A-173 requires
    an explicit ``--git-dir``/``--work-tree`` pair: a repository-LOCAL
    ``core.worktree`` redirects ``rev-parse --show-toplevel``/``status``."""
    repo_a, _, head_a, repo_b, _ = two_repos
    GitRepo(path=repo_a).git("config", "core.worktree", str(repo_b))

    assert git_module.repo_top(repo_a) == repo_a.resolve()
    assert git_module.head_rev(repo_a) == head_a
    assert git_module.dirty_paths(repo_a) == ()


def test_a_replace_ref_cannot_forge_an_empty_diff(two_repos):
    """``git replace <head> <base>`` makes a real base..HEAD diff read as
    empty while every commit spelling stays valid -- the witnessed attack
    behind ``GIT_NO_REPLACE_OBJECTS``. Deleting that key from the
    replacement environment left the gated suite green before this test."""
    repo_a, base_a, head_a, _, _ = two_repos
    GitRepo(path=repo_a).git("replace", head_a, base_a)

    diff = git_module.run(repo_a, "diff", "--unified=0", base_a, head_a)
    assert "alpha-HEAD" in diff, "the replace ref forged an empty diff"


def test_local_and_ambient_external_diff_programs_never_run(
    two_repos, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """A repository-local ``diff.external`` and an ambient
    ``GIT_EXTERNAL_DIFF`` both name a user program git would otherwise
    execute. Neither may run, and the real patch text must still be
    produced."""
    repo_a, base_a, head_a, _, _ = two_repos
    sentinel = tmp_path / "external-diff-ran"
    external = tmp_path / "external-diff"
    external.write_text(f"#!/bin/sh\ntouch '{sentinel}'\nexit 99\n", encoding="utf-8")
    external.chmod(0o755)
    GitRepo(path=repo_a).git("config", "diff.external", str(external))
    monkeypatch.setenv("GIT_EXTERNAL_DIFF", str(external))

    diff = git_module.run(repo_a, "diff", "--unified=0", base_a, head_a)

    assert "alpha-HEAD" in diff
    assert not sentinel.exists(), "an external diff program was executed"


def test_a_repository_local_core_excludes_file_cannot_hide_untracked_dirt(
    git_repo: GitRepo, tmp_path: Path
):
    """Reviewer repair (F3, config half). A SYSTEM or GLOBAL excludes file was
    already neutralised, but a repository-LOCAL ``core.excludesFile`` survived
    -- so whether a given untracked file was reported depended only on WHICH
    config file named the excludes list. O1 requires that hostile config
    cannot change the dirty set.

    The declared-artifact exemption must survive the repair: a path ignored by
    the repository's own TRACKED ``.gitignore`` (the mechanism work item 6
    relies on) stays correctly hidden.
    """
    git_repo.write(".gitignore", "cov.json\n")
    git_repo.write("keep.txt", "x\n")
    git_repo.commit_all("base")

    hide_everything = tmp_path / "hide-everything"
    hide_everything.write_text("*\n", encoding="utf-8")
    git_repo.git("config", "core.excludesFile", str(hide_everything))

    (git_repo.path / "leftover.bin").write_text("junk", encoding="utf-8")
    (git_repo.path / "cov.json").write_text("{}", encoding="utf-8")

    assert git_module.dirty_paths(git_repo.path) == ("leftover.bin",)


def test_git_info_exclude_cannot_hide_untracked_dirt(
    git_repo: GitRepo,
):
    """A clean committed .gitignore is policy; .git/info/exclude is not."""
    git_repo.write(".gitignore", "cov.json\n")
    git_repo.write("keep.txt", "x\n")
    git_repo.commit_all("base")
    (git_repo.path / ".git" / "info" / "exclude").write_text(
        "*\n", encoding="utf-8"
    )
    (git_repo.path / "cov.json").write_text("{}", encoding="utf-8")
    (git_repo.path / "leftover.bin").write_text("junk", encoding="utf-8")

    assert git_module.dirty_paths(git_repo.path) == ("leftover.bin",)


def test_git_output_is_bounded_by_a_fixed_byte_ceiling(
    two_repos, monkeypatch: pytest.MonkeyPatch
):
    """O4: every repository read has a FIXED byte bound. Before the reviewer
    repair, ``subprocess.run(capture_output=True)`` let the repository decide
    how much this process allocated. Only the ceiling's magnitude is injected;
    the git child, its output and the refusal are all real."""
    repo_a, base_a, head_a, _, _ = two_repos
    monkeypatch.setattr(git_module, "MAX_GIT_OUTPUT_BYTES", 4)

    with pytest.raises(AssayError) as caught:
        git_module.run(repo_a, "diff", "--unified=0", base_a, head_a)

    assert caught.value.reason_code is ReasonCode.GIT_FAILED
    assert "unbounded amount of repository data" in str(caught.value)


def test_a_bounded_git_read_still_returns_output_that_fits(two_repos):
    """The control for the bound above: with the real ceiling in force an
    ordinary diff is returned intact, so the test above is an oracle for the
    bound rather than for refusing everything."""
    repo_a, base_a, head_a, _, _ = two_repos
    assert "alpha-HEAD" in git_module.run(
        repo_a, "diff", "--unified=0", base_a, head_a
    )


def test_git_stderr_overflow_kills_the_child_instead_of_only_capping_memory(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(git_module, "_MAX_GIT_STDERR_BYTES", 4)

    with pytest.raises(AssayError) as caught:
        git_module._run_bounded(
            [sys.executable, "-c", "import os; os.write(2, b'x' * 5)"]
        )

    assert caught.value.reason_code is ReasonCode.GIT_FAILED
    assert "standard error" in str(caught.value)
