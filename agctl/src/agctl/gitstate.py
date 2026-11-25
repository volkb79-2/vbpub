"""Read-only git inputs for `agctl estate` (DESIGN section 4a: "Git").

Two calls only, both `--no-optional-locks` so this reader can never take git's own optional
lock files (an index refresh would write into a checkout it only inspects):
`git worktree list --porcelain` and `git branch --merged origin/main`. Git is the only
subprocess this package runs.

Every failure mode degrades to a stated `unknown`: git missing, no repository, a call that
errors, a merge list that cannot be read, porcelain text without a HEAD line. Nothing raises
a traceback into the estate view and nothing guesses a branch or a head it did not read.
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

# A local read operation: seconds, not minutes, so a wedged git cannot hang the command.
GIT_TIMEOUT_SECONDS = 20
HEAD_RE = re.compile(r"^[0-9a-f]{7,64}$")
BRANCH_PREFIX = "refs/heads/"


@dataclass(frozen=True)
class Worktree:
    """One entry of `git worktree list --porcelain`."""

    path: str
    head: str | None
    branch: str | None  # short name, e.g. "main"; None when detached or unparsable
    detached: bool
    bare: bool
    locked: str | None


@dataclass(frozen=True)
class GitView:
    """What git answered, and what it could not."""

    root: str
    worktrees: tuple[Worktree, ...]
    merged_branches: frozenset[str]
    merged_state: str  # "present" | "unknown"
    detail: str = ""

    def merged_here(self, branch: str | None) -> str:
        """`yes`/`no` for a branch; `unknown` when the merge list could not be read."""

        if self.merged_state != "present" or not branch:
            return "unknown"
        return "yes" if branch in self.merged_branches else "no"


def _run_git(arguments: list[str], cwd: str) -> tuple[str, int, str]:
    """Run one read-only git command; return (stdout, returncode, detail)."""

    try:
        completed = subprocess.run(
            ["git", *arguments],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT_SECONDS,
            check=False,
            env={**os.environ, "GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0"},
        )
    except (OSError, subprocess.SubprocessError) as error:
        return "", 127, f"{type(error).__name__}: {error}"
    return completed.stdout, completed.returncode, completed.stderr.strip()


def parse_worktree_porcelain(text: str) -> tuple[Worktree, ...]:
    """Parse the porcelain listing; a damaged block keeps the fields it does carry.

    A block whose HEAD line is missing or is not a commit id keeps `head = None`, so the row
    that uses it reports `unknown` instead of inventing a commit id.
    """

    entries: list[Worktree] = []
    state: dict[str, object] = {}

    def flush() -> None:
        path = state.get("path")
        if isinstance(path, str):
            entries.append(
                Worktree(
                    path=path,
                    head=state.get("head") if isinstance(state.get("head"), str) else None,
                    branch=state.get("branch") if isinstance(state.get("branch"), str) else None,
                    detached=bool(state.get("detached", False)),
                    bare=bool(state.get("bare", False)),
                    locked=state.get("locked") if isinstance(state.get("locked"), str) else None,
                )
            )
        state.clear()

    for line in text.splitlines():
        if not line:
            flush()
        elif line.startswith("worktree "):
            flush()
            state["path"] = line[len("worktree "):]
        elif line.startswith("HEAD "):
            candidate = line[len("HEAD "):].strip()
            if HEAD_RE.match(candidate):
                state["head"] = candidate
        elif line.startswith("branch "):
            ref = line[len("branch "):].strip()
            state["branch"] = ref[len(BRANCH_PREFIX):] if ref.startswith(BRANCH_PREFIX) else ref
        elif line == "detached":
            state["detached"] = True
        elif line == "bare":
            state["bare"] = True
        elif line.startswith("locked"):
            state["locked"] = line[len("locked"):].strip() or "locked"
    flush()
    return tuple(entries)


def parse_merged(text: str) -> frozenset[str]:
    """The short branch names of `git branch --merged <ref>`."""

    return frozenset(line.strip() for line in text.splitlines() if line.strip())


def discover_root(start: Path) -> tuple[str | None, str]:
    """The work tree root containing `start`, read-only, or ``(None, reason)``."""

    directories = [start if start.is_dir() else start.parent]
    configured = os.environ.get("CONTROLLER_REPO")
    if configured:
        candidate = Path(configured).expanduser()
        directories.append(candidate if candidate.is_dir() else candidate.parent)
    reason = "no directory to ask"
    for directory in directories:
        if not directory.is_dir():
            reason = f"{directory} is not a directory"
            continue
        stdout, code, detail = _run_git(
            ["--no-optional-locks", "rev-parse", "--show-toplevel"], str(directory)
        )
        root = stdout.strip()
        if code == 0 and root:
            return os.path.realpath(root), ""
        reason = detail or f"git rev-parse exited {code} in {directory}"
    return None, reason


def read_view(root: str) -> GitView:
    """The worktree list and the `origin/main` merge set for `root`."""

    listing, code, detail = _run_git(["--no-optional-locks", "worktree", "list", "--porcelain"], root)
    if code != 0:
        return GitView(root, (), frozenset(), "unknown", f"git worktree list failed: {detail or code}")
    worktrees = parse_worktree_porcelain(listing)
    if not worktrees:
        return GitView(root, (), frozenset(), "unknown", "git worktree list printed no entries")

    merged, merge_code, merge_detail = _run_git(
        ["--no-optional-locks", "branch", "--merged", "origin/main", "--format=%(refname:short)"], root
    )
    if merge_code != 0:
        # `origin/main` may be absent (a clone with no remote, or a fixture repo). That is a
        # stated unknown: it must never read as "nothing is merged".
        return GitView(
            root, worktrees, frozenset(), "unknown",
            f"git branch --merged origin/main failed: {merge_detail or merge_code}",
        )
    return GitView(root, worktrees, parse_merged(merged), "present")
