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
import json
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


def _release_token(workspace: ReleaseWorkspace) -> str:
    return workspace.branch.rsplit("/", 1)[-1]


def _scope_dir(repo_root: Path) -> Path:
    return _common_git_dir(repo_root) / "cmru-release-scopes"


def write_release_scope(repo_root: Path, workspace: ReleaseWorkspace, project_names: Sequence[str]) -> None:
    """Record which projects a release attempt targets, in the shared common git
    dir (never inside the worktree — S-REL.4a's undeclared-write guard must never
    see it). A later ``--abandon all-previous`` scopes cleanup by this record."""
    scope_dir = _scope_dir(repo_root)
    scope_dir.mkdir(parents=True, exist_ok=True)
    (scope_dir / f"{_release_token(workspace)}.json").write_text(
        json.dumps(sorted(project_names)), encoding="utf-8",
    )


def read_release_scope(repo_root: Path, workspace: ReleaseWorkspace) -> list[str] | None:
    """The recorded project scope for a retained worktree, or None if it predates
    this feature (an older retained worktree) — callers should treat None
    conservatively (not auto-abandon it)."""
    path = _scope_dir(repo_root) / f"{_release_token(workspace)}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def forget_release_scope(repo_root: Path, workspace: ReleaseWorkspace) -> None:
    """Remove this workspace's scope + progress-checkpoint marker files. Callers:
    abandon_workspace() (a discarded attempt) and a successful release (its scope
    marker is otherwise never cleaned up — see remove_workspace())."""
    (_scope_dir(repo_root) / f"{_release_token(workspace)}.json").unlink(missing_ok=True)
    _forget_release_progress(repo_root, workspace)


def write_release_progress(repo_root: Path, workspace: ReleaseWorkspace, sha: str) -> None:
    """Record the commit SHA as of the last *fully completed* project in a
    per-project release run (build-all-projects-after-another: S-REL — each
    project's prepare/gate/promote/tag/build/publish cycle finishes before the
    next project's starts). On a later failure, the parent uses this instead of
    the transaction's original base commit so it only reverts the in-flight
    project's promoted changes, never an earlier project's already-published
    release."""
    scope_dir = _scope_dir(repo_root)
    scope_dir.mkdir(parents=True, exist_ok=True)
    (scope_dir / f"{_release_token(workspace)}.progress").write_text(sha, encoding="utf-8")


def read_release_progress(repo_root: Path, workspace: ReleaseWorkspace) -> str | None:
    """The last-fully-completed-project checkpoint for a workspace, or None if no
    project has completed yet (or this predates the feature) — callers should
    fall back to ``workspace.base`` (revert everything) in that case."""
    path = _scope_dir(repo_root) / f"{_release_token(workspace)}.progress"
    if not path.exists():
        return None
    try:
        sha = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return sha or None


def _forget_release_progress(repo_root: Path, workspace: ReleaseWorkspace) -> None:
    (_scope_dir(repo_root) / f"{_release_token(workspace)}.progress").unlink(missing_ok=True)


def list_retained_workspaces(repo_root: Path) -> list[ReleaseWorkspace]:
    """Every ``cmru/release/*`` worktree still on disk — i.e. retained after a
    failed (never-resumed) release attempt."""
    raw = _git(repo_root, "worktree", "list", "--porcelain")
    workspaces: list[ReleaseWorkspace] = []
    for block in raw.split("\n\n"):
        fields: dict[str, str] = {}
        for line in block.splitlines():
            key, _, value = line.partition(" ")
            if value:
                fields[key] = value
        wt_path, branch_ref = fields.get("worktree"), fields.get("branch")
        if not wt_path or not branch_ref:
            continue
        branch = branch_ref[len("refs/heads/"):] if branch_ref.startswith("refs/heads/") else branch_ref
        if not branch.startswith("cmru/release/"):
            continue
        path = Path(wt_path)
        base = _git(path, "rev-parse", "HEAD", check=False) or ""
        workspaces.append(ReleaseWorkspace(repo_root, path, branch, base))
    return workspaces


def abandon_workspace(repo_root: Path, workspace: ReleaseWorkspace) -> None:
    """Fully discard a retained, never-promoted release attempt: its origin backup
    branch, local worktree/branch, and scope marker. Unlike remove_workspace() (the
    success path) this never touches origin/main — a failed release's gates ran
    before promote, so there is nothing there to undo."""
    remove_backup_branch(workspace)
    remove_workspace(workspace)
    forget_release_scope(repo_root, workspace)


def abandon_previous(repo_root: Path, current_projects: Sequence[str]) -> list[str]:
    """Abandon every retained release worktree whose recorded scope overlaps
    ``current_projects`` (S-CLI.1: releases always start fresh, never resume by
    default). Worktrees with no recorded scope are left alone — the caller can
    still target them explicitly via ``--abandon <path>``. Returns the branch
    names abandoned."""
    current = set(current_projects)
    abandoned: list[str] = []
    for workspace in list_retained_workspaces(repo_root):
        scope = read_release_scope(repo_root, workspace)
        if scope is None or not (current & set(scope)):
            continue
        abandon_workspace(repo_root, workspace)
        abandoned.append(workspace.branch)
    return abandoned


def promote_workspace(workspace: ReleaseWorkspace) -> None:
    """Fast-forward remote main from the prepared branch, or fail before publication."""
    subprocess.run(
        ["git", "push", "origin", "HEAD:refs/heads/main"], cwd=workspace.path, check=True,
    )


def push_backup_branch(workspace: ReleaseWorkspace) -> None:
    """Push the validated, gated branch to origin under its own name, before promotion.

    Purely additive durability: a crashed machine or lost worktree after this point
    still leaves an inspectable, resumable copy of the prepared release on origin —
    ``main`` itself is untouched by this push.
    """
    subprocess.run(
        ["git", "push", "origin", f"HEAD:refs/heads/{workspace.branch}"],
        cwd=workspace.path, check=True,
    )


def remove_backup_branch(workspace: ReleaseWorkspace) -> None:
    """Delete the durability backup branch from origin after a fully successful release.

    Best-effort: a release that already succeeded should not fail cleanup over a
    missing/already-gone remote branch.
    """
    subprocess.run(
        ["git", "push", "origin", "--delete", workspace.branch],
        cwd=workspace.path, check=False,
    )


def promotion_landed(repo_root: Path, workspace: ReleaseWorkspace) -> bool:
    """True if ``origin/main`` still sits exactly at this workspace's branch tip.

    Used after a failed release to distinguish "promote_workspace() ran, then a
    later step (tag/build/publish) failed" from "the failure happened before
    promotion" or "origin/main has since moved past this release entirely" — the
    latter two are not safe to auto-revert.
    """
    subprocess.run(["git", "fetch", "--prune", "origin", "main"], cwd=repo_root, check=True)
    origin_main = _git(repo_root, "rev-parse", "origin/main")
    branch_tip = _git(workspace.path, "rev-parse", workspace.branch)
    return origin_main == branch_tip


@dataclass(frozen=True)
class RevertResult:
    ok: bool          # False ⇒ needs manual cleanup (didn't apply cleanly / push rejected)
    reverted: bool    # True ⇒ a revert commit was actually pushed; False ⇒ nothing needed it


def revert_promotion(workspace: ReleaseWorkspace, *, from_sha: str | None = None) -> RevertResult:
    """Best-effort: undo this release's commits on ``origin/main`` by pushing a revert.

    ``from_sha`` scopes the revert to ``(from_sha, branch tip]`` instead of the whole
    transaction (``workspace.base``) — pass the last-fully-completed-project checkpoint
    (:func:`read_release_progress`) in a per-project release run so a later project's
    failure only undoes its own promoted changes, never an earlier project's already
    -published release. Defaults to ``workspace.base`` (revert everything promoted this
    transaction) when omitted, preserving the whole-transaction behavior.

    Caller MUST have already confirmed :func:`promotion_landed` — this never rewrites
    history (no force-push), it only adds a new revert commit on top, so it is safe
    to attempt even if the precondition was checked slightly earlier.

    ``RevertResult.ok`` is False (manual cleanup required) when the revert does not
    apply cleanly or the push is rejected (e.g. someone pushed to main meanwhile).
    ``RevertResult.reverted`` distinguishes "there was nothing to revert" (ok=True,
    reverted=False — e.g. the failing project never got as far as its own promote)
    from "a revert commit was actually pushed" (ok=True, reverted=True) — callers
    that log "reverted" should check ``.reverted``, not just ``.ok``, or they'll
    claim a revert happened when nothing was there to undo.
    """
    base = from_sha if from_sha is not None else workspace.base
    branch_tip = _git(workspace.path, "rev-parse", workspace.branch)
    if base == branch_tip:
        return RevertResult(ok=True, reverted=False)
    result = subprocess.run(
        ["git", "revert", "--no-edit", "--no-commit", f"{base}..{branch_tip}"],
        cwd=workspace.path,
    )
    if result.returncode != 0:
        subprocess.run(["git", "revert", "--abort"], cwd=workspace.path, check=False)
        return RevertResult(ok=False, reverted=False)
    subprocess.run(
        ["git", "commit", "-m", f"revert: undo failed release {workspace.branch}"],
        cwd=workspace.path, check=True,
    )
    push = subprocess.run(["git", "push", "origin", "HEAD:refs/heads/main"], cwd=workspace.path)
    return RevertResult(ok=push.returncode == 0, reverted=push.returncode == 0)


def sync_local_main(repo_root: Path) -> bool:
    """Bring the caller's local ``main`` up to date with ``origin/main``.

    Rebase, not merge — consistent with the rest of this pipeline, which is
    fast-forward-only end to end (promote_workspace's push, revert_promotion's
    push, assert_local_main_not_ahead's precondition): no other step here ever
    produces a merge commit, so local main shouldn't be the one exception. A
    release only ever commits declared, mechanical generated paths (S-REL.4a) —
    never hand-edited source — so local commits added while the release built
    (e.g. ongoing work in another terminal) essentially never touch the same
    files, and replay conflict-free. ``git rebase`` degenerates to a plain
    fast-forward when local main hasn't moved at all (the common case). It is
    only aborted (returning False, leaving local main untouched) on a genuine
    content conflict, which is a signal of unusual overlap worth a human's
    attention. Rewrites local main's own commits onto the new base — safe here
    because they are, by construction, commits the release process never saw
    and the developer has not necessarily pushed anywhere yet.
    """
    subprocess.run(["git", "fetch", "--prune", "origin", "main"], cwd=repo_root, check=True)
    current = _git(repo_root, "branch", "--show-current", check=False)
    if current == "main":
        result = subprocess.run(["git", "rebase", "origin/main"], cwd=repo_root)
        if result.returncode != 0:
            subprocess.run(["git", "rebase", "--abort"], cwd=repo_root, check=False)
        return result.returncode == 0
    local_main = _git(repo_root, "rev-parse", "main", check=False)
    if local_main:
        merge_base = _git(repo_root, "merge-base", "main", "origin/main", check=False)
        if merge_base != local_main:
            return False  # local main has commits of its own — do not force-move it
    result = subprocess.run(["git", "branch", "-f", "main", "origin/main"], cwd=repo_root)
    return result.returncode == 0


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
