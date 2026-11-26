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
# Only what git needs to find itself and to answer in the caller's locale. Everything else in
# `os.environ` stays out of the child (see `_run_git`).
ALLOWED_ENV = ("PATH", "HOME", "LANG", "LC_ALL", "LC_CTYPE", "TMPDIR")


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
    """Run one read-only git command; return (stdout, returncode, detail).

    The child gets an ALLOW-LISTED environment, never the caller's whole one: an exported
    `GIT_DIR`/`GIT_WORK_TREE` (or a stray `GIT_CONFIG` from a shell) would make `worktree
    list` describe a repository this reader never asked about, and a caller's credential
    variable has no business in a read-only local call. The two `GIT_*` keys set here are
    the read-only guarantees this module documents.
    """

    environment = {key: os.environ[key] for key in ALLOWED_ENV if key in os.environ}
    environment.update({"GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": ""})
    try:
        completed = subprocess.run(
            ["git", *arguments],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT_SECONDS,
            check=False,
            env=environment,
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
    """The branch names of a `git branch --merged <ref> --format=%(refname)` listing.

    The full ref (`refs/heads/<name>`) is what git prints for that format, and the prefix is
    stripped here. `%(refname:short)` is not used: when a TAG shares a branch name git prints
    `heads/<name>` for the branch, which would silently turn a merged branch into a merge
    candidate.
    """

    names: set[str] = set()
    for line in text.splitlines():
        name = line.strip()
        if not name:
            continue
        names.add(name[len(BRANCH_PREFIX):] if name.startswith(BRANCH_PREFIX) else name)
    return frozenset(names)


def discover_root(repo: str | None, cwd: Path) -> tuple[str | None, str]:
    """The work tree root to read: the declared repository, else the caller's cwd.

    The order is the source order of DESIGN sections 3/4a and NOT a matter of taste: the
    controller home is a tool-state directory, so asking git there either reports nothing or
    (worse) reports a *different* repository's worktrees while every peer path still comes
    from `CONTROLLER_REPO`. `CONTROLLER_REPO` set but not a checkout is a configuration
    error, not a reason to silently answer for the cwd: the caller asked for a specific
    repository.
    """

    if repo:
        configured = Path(repo).expanduser()
        if not configured.is_dir():
            return None, f"CONTROLLER_REPO={configured} is not a directory"
        root, reason = _toplevel(str(configured))
        if root:
            return root, ""
        return None, f"CONTROLLER_REPO={configured} names no git work tree: {reason}"
    directory = cwd if cwd.is_dir() else cwd.parent
    root, reason = _toplevel(str(directory))
    if root:
        return root, ""
    return None, f"no git work tree at {directory}: {reason}"


def _toplevel(directory: str) -> tuple[str, str]:
    """`(root, "")` when git answers, `("", reason)` when it does not; never a guess."""

    stdout, code, detail = _run_git(["--no-optional-locks", "rev-parse", "--show-toplevel"], directory)
    root = stdout.strip()
    if code != 0 or not root:
        return "", detail or f"git rev-parse exited {code}"
    return os.path.realpath(root), ""


def read_view(root: str) -> GitView:
    """The worktree list and the `origin/main` merge set for `root`."""

    listing, code, detail = _run_git(["--no-optional-locks", "worktree", "list", "--porcelain"], root)
    if code != 0:
        return GitView(root, (), frozenset(), "unknown", f"git worktree list failed: {detail or code}")
    worktrees = parse_worktree_porcelain(listing)
    if not worktrees:
        return GitView(root, (), frozenset(), "unknown", "git worktree list printed no entries")

    merged, merge_code, merge_detail = _run_git(
        ["--no-optional-locks", "branch", "--merged", "origin/main", "--format=%(refname)"], root
    )
    if merge_code != 0:
        # `origin/main` may be absent (a clone with no remote, or a fixture repo). That is a
        # stated unknown: it must never read as "nothing is merged".
        return GitView(
            root, worktrees, frozenset(), "unknown",
            f"git branch --merged origin/main failed: {merge_detail or merge_code}",
        )
    return GitView(root, worktrees, parse_merged(merged), "present")
