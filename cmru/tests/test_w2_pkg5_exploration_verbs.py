"""T5 (W2-PKG5): every exploration (read-only) verb leaves the repository exactly as it found it.

For each verb the test snapshots, BEFORE and AFTER one real ``cmru.cli.main`` invocation inside a
committed fixture repository: every file under the tree (``.git`` excluded; path, mode and bytes),
all refs, ``HEAD``, ``git status --porcelain``, the worktree list and the stash list. Any difference
fails. This replaces the circular "mutating label" test: it checks the behaviour a label promises,
not the label. The conditionally mutating verbs (``dependencies``, ``standards``, ``tool-deps``) are
exercised WITHOUT their write flag; ``skills`` runs against a throw-away HOME.

Repo-walking is confined to the fixture tree, so the test is valid in a sparse canary snapshot.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import urllib.error
from pathlib import Path

import pytest

from tests.test_cli_dispatch import MINIMAL_S2, PROJECT_CENTRAL

_GIT_ENV = {
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid",
}


def _git(repo: Path, *args: str) -> str:
    env = {**os.environ, **_GIT_ENV}
    return subprocess.run(
        ["git", *args], cwd=repo, env=env, check=True, capture_output=True, text=True,
    ).stdout


def snapshot(repo: Path) -> dict:
    """Everything an exploration verb could (wrongly) change in ``repo``."""
    files = {}
    for path in sorted(repo.rglob("*")):
        rel = path.relative_to(repo)
        if rel.parts[0] == ".git":
            continue
        if path.is_symlink():
            files[str(rel)] = ("symlink", os.readlink(path))
        elif path.is_file():
            files[str(rel)] = (
                "file", path.stat().st_mode, hashlib.sha256(path.read_bytes()).hexdigest(),
            )
        else:
            files[str(rel)] = ("dir",)
    return {
        "files": files,
        "refs": _git(repo, "for-each-ref"),
        "head": _git(repo, "rev-parse", "HEAD"),
        "status": _git(repo, "status", "--porcelain", "--ignored"),
        "worktrees": _git(repo, "worktree", "list", "--porcelain"),
        "stash": _git(repo, "stash", "list"),
    }


@pytest.fixture
def fixture_repo(tmp_path, monkeypatch):
    """A committed repo with a valid orchestration config; HOME/PATH/network sandboxed."""
    repo = tmp_path / "repo"
    (repo / "alpha").mkdir(parents=True)
    (repo / "cmru.orchestration.toml").write_text(MINIMAL_S2, encoding="utf-8")
    (repo / "alpha" / "cmru.toml").write_text(PROJECT_CENTRAL, encoding="utf-8")
    (repo / "alpha" / "README.md").write_text("alpha\n", encoding="utf-8")
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")

    home = tmp_path / "home"
    bindir = tmp_path / "bin"
    home.mkdir()
    bindir.mkdir()
    os.symlink(shutil.which("git"), bindir / "git")
    for key in list(os.environ):
        if key.startswith(("CMRU_", "GITHUB_", "GIT_", "XDG_")) or key in {"SOURCE_DATE_EPOCH", "GH_TOKEN"}:
            monkeypatch.delenv(key)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("PATH", str(bindir))
    for key, value in _GIT_ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.chdir(repo)

    def offline(*_a, **_k):
        raise urllib.error.URLError("offline (T5 fixture)")

    monkeypatch.setattr("cmru.release.urlopen", offline)
    monkeypatch.setattr("cmru.resolve.resolve", lambda *_a, **_k: {
        "version": "1.0.0", "tag": "alpha-v1.0.0", "asset": "a", "sha256": None, "url": "https://u",
    })
    yield repo
    from cmru import output
    os.environ.pop(output._TIME_ENV, None)
    output.configure(False)


# (id, argv, may-be-nonzero). Statuses are not the point here (a verb that refuses a fixture it
# cannot satisfy must still leave the tree alone), but the "runs" set below must really run.
EXPLORATION_VERBS = [
    ("worktrees", ["worktrees"]),
    ("status", ["status"]),
    ("resolve", ["resolve"]),
    ("versions-check", ["versions", "check"]),
    ("doctor", ["doctor"]),
    ("skills-list", ["skills", "list"]),
    ("skills-check", ["skills", "check"]),
    ("handler-wheel-validate", ["handler", "wheel-validate"]),
    ("handler-tarball-validate", ["handler", "tarball-validate"]),
    ("dependencies-no-write", ["dependencies"]),
    ("standards-no-update", ["standards"]),
    ("tool-deps-no-refresh", ["tool-deps"]),
]
# Verbs that must complete (exit 0) on the fixture, so the snapshot compares a real run.
MUST_SUCCEED = {"worktrees", "resolve", "skills-list", "dependencies-no-write"}
# `standards` reaches its policy verdict on the fixture (template revision 2 is stale: exit 4).
MUST_REACH_VERDICT = {"standards-no-update": {0, 4}}


def _run(argv: list[str]) -> int:
    from cmru.cli import main

    try:
        status = main(list(argv))
    except SystemExit as exc:
        status = exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 1)
    return int(status or 0)


@pytest.mark.parametrize(("verb_id", "argv"), EXPLORATION_VERBS, ids=[v[0] for v in EXPLORATION_VERBS])
def test_exploration_verb_leaves_the_tree_unchanged(verb_id, argv, fixture_repo, capsys):
    before = snapshot(fixture_repo)
    status = _run(argv)
    captured = capsys.readouterr()
    after = snapshot(fixture_repo)
    assert "Traceback" not in captured.err, captured.err
    if verb_id in MUST_SUCCEED:
        assert status == 0, (argv, status, captured.out, captured.err)
    if verb_id in MUST_REACH_VERDICT:
        assert status in MUST_REACH_VERDICT[verb_id], (argv, status, captured.out, captured.err)
    assert after == before, f"{argv!r} (exit {status}) changed the repository"


def test_the_snapshot_detects_every_kind_of_change(fixture_repo):
    """The comparison itself must see file, ref, head, status, worktree and stash changes."""
    base = snapshot(fixture_repo)
    (fixture_repo / "stray.txt").write_text("x", encoding="utf-8")
    assert snapshot(fixture_repo) != base
    (fixture_repo / "stray.txt").unlink()
    assert snapshot(fixture_repo) == base
    (fixture_repo / "alpha" / "README.md").write_text("changed\n", encoding="utf-8")
    assert snapshot(fixture_repo)["files"] != base["files"]
    _git(fixture_repo, "checkout", "-q", "--", "alpha/README.md")
    _git(fixture_repo, "branch", "extra")
    assert snapshot(fixture_repo)["refs"] != base["refs"]
    _git(fixture_repo, "branch", "-q", "-D", "extra")
    _git(fixture_repo, "worktree", "add", "-q", str(fixture_repo.parent / "wt"), "-b", "wtb")
    assert snapshot(fixture_repo)["worktrees"] != base["worktrees"]


def test_a_read_only_verb_that_writes_a_file_is_caught(fixture_repo, monkeypatch):
    """Plant (T5): a read-only verb that drops a file must fail the unchanged-tree comparison."""
    import cmru.cli as cli_module

    original = cli_module.main

    def writes_a_file(argv=None):
        (Path.cwd() / "planted.txt").write_text("leak", encoding="utf-8")
        return original(argv)

    monkeypatch.setattr(cli_module, "main", writes_a_file)
    before = snapshot(fixture_repo)
    _run(["worktrees"])
    assert snapshot(fixture_repo) != before
