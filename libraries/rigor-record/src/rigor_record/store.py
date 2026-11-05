"""The git procedure for every write to ``main`` (memo 5.4): lock, fetch, temporary worktree, plan, regenerate, commit, push.

Steps of one attempt: fetch ``<remote>/<branch>``; create a detached linked worktree there; load the project's records;
let the caller's ``plan_fn`` compute the change; write the changed records and regenerate ``rigor/index.json`` and
``RIGOR.md`` from all records; ``git commit --only`` those paths; push ``HEAD`` to ``<branch>``. A push that is
rejected because the remote moved discards the attempt and starts again from a fresh fetch (at most
:data:`MAX_PUSH_ATTEMPTS`, with a growing jittered pause). The procedure never merges, rebases or force-pushes, so the
views are always recomputed from the records on the new tip and a conflict cannot arise.
"""

from __future__ import annotations

import fcntl
import os
import random
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from .assemble import Refusal
from .check import load_records
from .derive import targets_at_factory
from .gitfacts import GitFacts, RepoGit
from .render import RenderError, render_views
from .update import Plan
from .validate import canonical_json

MAX_PUSH_ATTEMPTS = 5
COMMIT_PREFIX = "chore(rigor): "
TRAILER = re.compile(r"^Rigor-Record: \S+@\S+ rev=[0-9]+ fragments=[0-9a-f]{12}$", re.MULTILINE)


class StoreError(Exception):
    """A git step failed, the push kept being rejected, or the lock could not be taken."""


@dataclass(frozen=True)
class Outcome:
    """``status``: ``pushed``, ``dry-run`` (committed in the throw-away worktree only) or ``noop``."""

    status: str
    commit: str | None
    attempts: int
    subject: str | None


PlanFn = Callable[[dict[str, dict], Path], "Plan | None"]


def default_lock(repo: Path) -> Path:
    base = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state")
    return base / "rigor-record" / f"{Path(repo).resolve().name}.lock"


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    done = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=False,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
    )
    if check and done.returncode != 0:
        first = (done.stderr.strip().splitlines() or ["no message"])[0][:300]
        raise StoreError(f"git {args[0]} failed: {first}")
    return done


def _relative(project_dir: str, name: str) -> str:
    return name if project_dir == "." else f"{project_dir}/{name}"


def _already_applied(worktree: Path, project_dir: str, key: str) -> bool:
    paths = [_relative(project_dir, "rigor/commits"), _relative(project_dir, "rigor/index.json")]
    log = _git(worktree, "log", "--format=%B", "HEAD", "--", *paths).stdout
    return re.search(rf"^Rigor-Record: \S+@[0-9a-f]{{40}} rev=[0-9]+ fragments={key}$", log, re.MULTILINE) is not None


def _write_files(project: Path, changed: dict[str, dict], by_commit: dict[str, dict], git: GitFacts) -> None:
    for commit, record in changed.items():
        folder = project / "rigor" / "commits" / commit[:2]
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{commit}.json").write_text(canonical_json(record), encoding="utf-8")
    merged = list({**by_commit, **changed}.values())
    try:
        views = render_views(merged, merged[0]["project"], targets_at_factory(merged, git))
    except RenderError as exc:
        raise Refusal(exc.problems) from exc
    for relative, text in views.items():
        (project / relative).write_text(text, encoding="utf-8")


def _attempt(
    *, repo: Path, worktree: Path, project_dir: str, plan_fn: PlanFn, idem: str | None,
    remote: str, branch: str, base: str, push: bool, attempt: int, before_push: Callable[[int], None] | None,
    hosts: Iterable[str], users: Iterable[str],
) -> Outcome | None:
    """One pass in the worktree. ``None`` means the push was rejected because the remote moved."""
    project = worktree if project_dir == "." else worktree / project_dir
    records, problems = load_records(project, tuple(hosts), tuple(users))
    if problems:
        raise Refusal([f"the committed rigor/ tree is invalid: {p}" for p in problems])
    by_commit = {r["commit"]: r for r in records}
    if idem is not None and _already_applied(worktree, project_dir, idem):
        return Outcome("noop", None, attempt, None)
    plan = plan_fn(by_commit, project)
    if plan is None:
        return Outcome("noop", None, attempt, None)
    _write_files(project, plan.changed, by_commit, RepoGit(worktree))
    paths = [_relative(project_dir, "rigor"), _relative(project_dir, "RIGOR.md")]
    _git(worktree, "add", "--", *paths)
    message = plan.subject + "\n\n" + "\n".join(plan.trailers) + "\n"
    _git(worktree, "commit", "--quiet", "--only", "-m", message, "--", *paths)
    commit = _git(worktree, "rev-parse", "HEAD").stdout.strip()
    if not push:
        return Outcome("dry-run", commit, attempt, plan.subject)
    if before_push is not None:
        before_push(attempt)
    pushed = _git(worktree, "push", "--quiet", remote, f"HEAD:refs/heads/{branch}", check=False)
    if pushed.returncode == 0:
        return Outcome("pushed", commit, attempt, plan.subject)
    _git(repo, "fetch", "--quiet", remote, f"+refs/heads/{branch}:refs/remotes/{remote}/{branch}")
    if _git(repo, "rev-parse", f"refs/remotes/{remote}/{branch}").stdout.strip() == base:
        first = (pushed.stderr.strip().splitlines() or ["no message"])[0][:300]
        raise StoreError(f"push failed and {remote}/{branch} did not move: {first}")
    return None


def write_through(
    *,
    repo: Path,
    project_dir: str,
    plan_fn: PlanFn,
    push: bool,
    idem: str | None = None,
    remote: str = "origin",
    branch: str = "main",
    lock: Path | None = None,
    max_attempts: int = MAX_PUSH_ATTEMPTS,
    sleep: Callable[[float], None] = time.sleep,
    jitter: Callable[[int], float] = lambda attempt: random.uniform(0.1, 0.5) * attempt,
    before_push: Callable[[int], None] | None = None,
    hosts: Iterable[str] = (),
    users: Iterable[str] = (),
) -> Outcome:
    """Run ``plan_fn`` against the tip of ``<remote>/<branch>`` and commit (and with ``push`` publish) its plan.

    ``idem`` is a fragments key: when a commit already carries it in a Rigor-Record trailer, nothing is done (memo 3.2).
    ``before_push(attempt)`` is a test seam called between the commit and the push.
    """
    repo = Path(repo)
    lock = default_lock(repo) if lock is None else Path(lock)
    lock.parent.mkdir(parents=True, exist_ok=True)
    with open(lock, "a", encoding="utf-8") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        for attempt in range(1, max_attempts + 1):
            _git(repo, "fetch", "--quiet", remote, f"+refs/heads/{branch}:refs/remotes/{remote}/{branch}")
            base = _git(repo, "rev-parse", f"refs/remotes/{remote}/{branch}").stdout.strip()
            scratch = Path(tempfile.mkdtemp(prefix="rigor-record-"))
            worktree = scratch / "wt"
            try:
                _git(repo, "worktree", "add", "--quiet", "--detach", str(worktree), base)
                outcome = _attempt(
                    repo=repo, worktree=worktree, project_dir=project_dir, plan_fn=plan_fn, idem=idem, remote=remote,
                    branch=branch, base=base, push=push, attempt=attempt, before_push=before_push, hosts=hosts, users=users,
                )
            finally:
                _git(repo, "worktree", "remove", "--force", str(worktree), check=False)
                _git(repo, "worktree", "prune", check=False)
                shutil.rmtree(scratch, ignore_errors=True)
            if outcome is not None:
                return outcome
            sleep(jitter(attempt))
    raise StoreError(f"push rejected {max_attempts} times: nothing was published, rerun the command")


def check_commits(repo: Path, project_dir: str, rev_range: str) -> list[str]:
    """Commits in ``rev_range`` that touch generated rigor files without the prefix and the trailer (memo 5.4)."""
    paths = [_relative(project_dir, "rigor/commits"), _relative(project_dir, "rigor/index.json"), _relative(project_dir, "RIGOR.md")]
    problems = []
    for commit in _git(repo, "log", "--format=%H", rev_range, "--", *paths).stdout.split():
        message = _git(repo, "log", "-1", "--format=%B", commit).stdout
        if not message.startswith(COMMIT_PREFIX):
            problems.append(f"{commit[:12]}: touches generated rigor files but the subject lacks the '{COMMIT_PREFIX}' prefix")
        if TRAILER.search(message) is None:
            problems.append(f"{commit[:12]}: touches generated rigor files but has no valid Rigor-Record trailer")
    return problems
