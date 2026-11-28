"""Regression witnesses for transaction R2 survivor batch 3b."""
from __future__ import annotations

import subprocess

import pytest

from cmru import git_auth
from cmru import transaction
from tests._git_isolation import git_isolation  # noqa: F401

pytestmark = pytest.mark.usefixtures("git_isolation")


def _git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], capture_output=True, text=True, check=True,
    )


def _candidate(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    _git("init", "-q", "-b", "main", str(root))
    (root / "seed.txt").write_text("seed\n", encoding="utf-8")
    _git("-C", str(root), "add", "seed.txt")
    _git("-C", str(root), "commit", "-q", "-m", "seed")
    remote = tmp_path / "remote.git"
    _git("init", "--bare", "-q", str(remote))
    _git("-C", str(root), "remote", "add", "origin", str(remote))
    branch = "cmru-release-witness"
    candidate = tmp_path / "candidate"
    _git("-C", str(root), "worktree", "add", "-q", "-b", branch, str(candidate))
    _git("-C", str(candidate), "push", "-q", "origin", branch)
    oid = _git("-C", str(candidate), "rev-parse", "HEAD").stdout.strip()
    workspace = transaction.ReleaseWorkspace(root, candidate, branch, oid)
    return root, remote, workspace, oid


def test_local_bare_receiver_runs_without_starting_maintenance(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    _git("init", "-q", "-b", "main", str(source))
    (source / "seed.txt").write_text("seed\n", encoding="utf-8")
    _git("-C", str(source), "add", "seed.txt")
    _git("-C", str(source), "commit", "-q", "-m", "seed")

    remote = tmp_path / "remote.git"
    _git("init", "--bare", "-q", str(remote))
    unset = subprocess.run(
        ["git", "--git-dir", str(remote), "config", "--local", "--get", "receive.autogc"],
        capture_output=True, text=True, check=False,
    )
    assert unset.returncode != 0
    _git("-C", str(source), "remote", "add", "origin", str(remote))
    _git("-C", str(source), "push", "-q", "origin", "main")

    trace = tmp_path / "git-trace.log"
    monkeypatch.setenv("GIT_TRACE", str(trace))
    branch = "cmru-release-witness"
    _git("-C", str(source), "branch", branch)
    pushed = git_auth.run_remote_git(source, "push", "origin", branch)
    assert pushed.returncode == 0
    oid = _git("-C", str(source), "rev-parse", branch).stdout.strip()
    deleted = git_auth.run_remote_git(
        source, "push", f"--force-with-lease=refs/heads/{branch}:{oid}",
        "origin", f":refs/heads/{branch}",
    )
    assert deleted.returncode == 0

    trace_text = trace.read_text(encoding="utf-8")
    assert "git-receive-pack" in trace_text
    assert "git maintenance run --auto --quiet --detach" not in trace_text
    remaining = subprocess.run(
        ["git", "--git-dir", str(remote), "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"],
        capture_output=True, text=True, check=False,
    )
    assert remaining.returncode != 0


def test_abandon_refuses_remote_candidate_without_its_push_record(tmp_path):
    root, remote, workspace, oid = _candidate(tmp_path)

    with pytest.raises(RuntimeError, match="origin branch exists without matching active transaction state"):
        transaction.abandon_workspace(root, workspace, expected_remote_candidate_oid=oid)

    assert workspace.path.is_dir()
    assert _git("--git-dir", str(remote), "rev-parse", "refs/heads/" + workspace.branch).stdout.strip() == oid


def test_abandon_refuses_a_missing_remote_candidate_until_removal_is_recorded(tmp_path):
    root, remote, workspace, oid = _candidate(tmp_path)
    transaction.mark_backup_pushed(root, workspace)
    _git("--git-dir", str(remote), "update-ref", "-d", "refs/heads/" + workspace.branch, oid)

    with pytest.raises(RuntimeError, match="refusing stale transaction metadata"):
        transaction.abandon_workspace(root, workspace, expected_remote_candidate_oid=oid)

    assert workspace.path.is_dir()
    assert subprocess.run(
        ["git", "--git-dir", str(remote), "show-ref", "--verify", "--quiet", "refs/heads/" + workspace.branch],
        capture_output=True, text=True, check=False,
    ).returncode != 0
