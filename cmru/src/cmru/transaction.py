"""Isolated, source-first release transactions.

``cmru release`` is deliberately launched from an ordinary developer checkout but
never publishes from it.  A transaction pins ``origin/main`` to a commit, creates
an isolated worktree on a private ``cmru/release/<id>`` branch, and executes the
release child there.  The caller's uncommitted files therefore cannot leak into a
wheel, image, tag, or release asset.

The parent process owns a repository-local flock for the lifetime of its child.
The child may fast-forward ``origin/main`` from the prepared release branch; a
concurrent remote update fails closed before tags or publication.
"""
from __future__ import annotations

import fcntl
import os
import shutil
import subprocess
import sys
import tempfile
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Sequence


CHILD_ENV = "CMRU_RELEASE_TRANSACTION_CHILD"
BRANCH_ENV = "CMRU_RELEASE_BRANCH"
BASE_ENV = "CMRU_RELEASE_BASE"


def _git(repo_root: Path, *args: str, check: bool = True) -> str:
    result = subprocess.run(
        ["git", *args], cwd=repo_root, text=True, capture_output=True, check=False,
    )
    if check and result.returncode:
        raise RuntimeError(
            f"git {' '.join(args)} failed ({result.returncode}): "
            f"{result.stderr.strip() or result.stdout.strip()}"
        )
    return result.stdout.strip()


def _common_git_dir(repo_root: Path) -> Path:
    raw = _git(repo_root, "rev-parse", "--git-common-dir")
    path = Path(raw)
    return path if path.is_absolute() else (repo_root / path).resolve()


@dataclass(frozen=True)
class ReleaseWorkspace:
    """The immutable source snapshot and private branch for one release."""

    repo_root: Path
    path: Path
    branch: str
    base: str


@contextmanager
def release_lock(repo_root: Path) -> Iterator[None]:
    """Serialize local release transactions without relying on a mutable checkout."""
    lock_path = _common_git_dir(repo_root) / "cmru-release.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("Another cmru release transaction is already running.") from exc
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def fetch_origin_main(repo_root: Path) -> str:
    """Fetch and return the exact remote commit authoritative for a new release."""
    subprocess.run(["git", "fetch", "--prune", "origin", "main"], cwd=repo_root, check=True)
    return _git(repo_root, "rev-parse", "origin/main")


def local_main_divergence(repo_root: Path) -> tuple[int, int]:
    """Return commits ``(ahead, behind)`` for local main relative to origin/main."""
    try:
        counts = _git(repo_root, "rev-list", "--left-right", "--count", "main...origin/main")
        ahead, behind = counts.split()
        return int(ahead), int(behind)
    except (RuntimeError, ValueError) as exc:
        raise RuntimeError(
            "Cannot compare local main with origin/main; fetch/repair the local main ref "
            "before starting a release."
        ) from exc


def assert_local_main_not_ahead(repo_root: Path) -> int:
    """Reject commits local-only commits that an origin/main snapshot would omit.

    A behind local checkout is harmless because ``origin/main`` is deliberately
    authoritative; the caller receives that count so it can be reported.
    """
    ahead, behind = local_main_divergence(repo_root)
    if ahead:
        raise RuntimeError(
            f"Local main is {ahead} commit(s) ahead of origin/main. "
            "Push those commits (or explicitly base the intended change on origin/main) "
            "before release; an isolated release snapshots origin/main and would omit them."
        )
    return behind


def create_workspace(repo_root: Path, *, base: str | None = None) -> ReleaseWorkspace:
    """Create a worktree at one already-fetched authoritative remote commit."""
    if base is None:
        base = fetch_origin_main(repo_root)
    token = uuid.uuid4().hex[:12]
    branch = f"cmru/release/{token}"
    parent = repo_root / ".worktrees"
    parent.mkdir(exist_ok=True)
    path = Path(tempfile.mkdtemp(prefix=f"cmru-release-{token}-", dir=parent))
    path.rmdir()  # git worktree requires a path that does not exist yet.
    subprocess.run(
        ["git", "worktree", "add", "-b", branch, str(path), base], cwd=repo_root, check=True,
    )
    return ReleaseWorkspace(repo_root=repo_root, path=path, branch=branch, base=base)


def resume_workspace(repo_root: Path, path: Path) -> ReleaseWorkspace:
    """Validate and reopen a retained ``cmru/release/*`` worktree."""
    path = path.resolve()
    if not path.is_dir():
        raise RuntimeError(f"release worktree does not exist: {path}")
    if _common_git_dir(path) != _common_git_dir(repo_root):
        raise RuntimeError(f"{path} is not a worktree of {repo_root}")
    branch = _git(path, "branch", "--show-current")
    if not branch.startswith("cmru/release/"):
        raise RuntimeError(f"{path} is not a retained cmru release branch (got {branch!r})")
    subprocess.run(["git", "fetch", "--prune", "origin", "main"], cwd=repo_root, check=True)
    return ReleaseWorkspace(repo_root=repo_root, path=path, branch=branch, base=_git(path, "rev-parse", "HEAD"))


def copy_secret_overlay(repo_root: Path, workspace: ReleaseWorkspace) -> None:
    """Make the optional, gitignored token overlay available only to the child worktree."""
    source = repo_root / "cmru.secret.toml"
    if not source.exists():
        return
    target = workspace.path / "cmru.secret.toml"
    shutil.copyfile(source, target)
    target.chmod(0o600)


def remove_workspace(workspace: ReleaseWorkspace) -> None:
    """Remove a successful ephemeral worktree and its private branch."""
    subprocess.run(
        ["git", "worktree", "remove", "--force", str(workspace.path)],
        cwd=workspace.repo_root, check=True,
    )
    subprocess.run(
        ["git", "branch", "-D", workspace.branch], cwd=workspace.repo_root, check=True,
    )


def promote_workspace(workspace: ReleaseWorkspace) -> None:
    """Fast-forward remote main from the prepared branch, or fail before publication."""
    subprocess.run(
        ["git", "push", "origin", "HEAD:refs/heads/main"], cwd=workspace.path, check=True,
    )


def run_child(workspace: ReleaseWorkspace, release_args: Sequence[str]) -> int:
    """Run the repo-root shim from the snapshot, preserving the caller's terminal output."""
    env = os.environ.copy()
    env[CHILD_ENV] = "1"
    env[BRANCH_ENV] = workspace.branch
    env[BASE_ENV] = workspace.base
    command = [
        sys.executable, str(workspace.path / "cmru.py"), "release",
        "--_transaction-child", *release_args,
    ]
    return subprocess.run(command, cwd=workspace.path, env=env).returncode
