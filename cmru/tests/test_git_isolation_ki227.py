"""KI-227: a push into a local bare repository must not start maintenance under isolation."""
from __future__ import annotations

import subprocess

import pytest

from cmru import git_auth
from tests._git_isolation import git_isolation  # noqa: F401

pytestmark = pytest.mark.usefixtures("git_isolation")


def _git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], capture_output=True, text=True, check=True,
    )


def test_local_bare_receiver_runs_without_starting_maintenance(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    _git("init", "-q", "-b", "main", str(source))
    (source / "seed.txt").write_text("seed\n", encoding="utf-8")
    _git("-C", str(source), "add", "seed.txt")
    _git("-C", str(source), "commit", "-q", "-m", "seed")

    remote = tmp_path / "remote.git"
    _git("init", "--bare", "-q", str(remote))
    # The setting must come from the isolated global config, not from the bare repository.
    unset = subprocess.run(
        ["git", "--git-dir", str(remote), "config", "--local", "--get", "receive.autogc"],
        capture_output=True, text=True, check=False,
    )
    assert unset.returncode == 1, unset.stderr
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
    # Git versions differ in the command a receiver starts for automatic housekeeping.
    assert "maintenance run" not in trace_text
    assert "gc --auto" not in trace_text
    remaining = subprocess.run(
        ["git", "--git-dir", str(remote), "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"],
        capture_output=True, text=True, check=False,
    )
    assert remaining.returncode == 1, remaining.stderr
