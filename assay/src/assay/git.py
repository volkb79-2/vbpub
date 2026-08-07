"""The thin git subprocess boundary.

Every other module in this package that needs git talks to it through here —
one function that shells out (:func:`run`), and a handful of thin wrappers
over specific invocations. Nothing above this module ever builds a
``["git", ...]`` argv itself.

The two non-obvious behaviours this module exists to get right, both taken
from the union of the three cited sibling gates (``dstdns/scripts/
coverage_gate.py``, ``nyxloom/src/nyxloom/coverage_gate.py``,
``topos/tools/coverage_gate.py``) and verified empirically against a real git
binary while writing this module:

* :func:`resolve_base` — a merge-commit ``HEAD`` (two or more parents)
  resolves to its **first parent** (the merged-into branch, pre-merge); any
  other ``HEAD`` resolves to ``merge-base(base, HEAD)`` (the feature branch's
  fork point). One command, ``git rev-list --parents -n 1 HEAD``, tells you
  which case you are in — its token count is 2 for a normal commit and ≥3 for
  a merge.
* :func:`dirty_paths` — ``git status --porcelain`` **always** reports paths
  relative to the repository's top level, never to the ``-C`` directory or the
  process cwd (unlike ``git diff --relative``). A caller that forgets this and
  treats the output as relative to whatever directory it passed will build the
  wrong absolute path the moment the repo is a monorepo and the git command
  was invoked from a subdirectory — exactly assay's own situation (A-049).
  :func:`repo_top` is what lets a caller convert correctly.

This module raises nothing but :class:`~assay.errors.AssayError`; there is no
locally-defined exception type here (A-091 — ``errors.py`` is outside this
package's ``scope.touch``).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from .errors import AssayError, Outcome, ReasonCode

__all__ = [
    "dirty_paths",
    "head_rev",
    "repo_top",
    "resolve_base",
    "run",
]


def run(repo: Path, *args: str) -> str:
    """Run ``git -C <repo> <args>`` and return its stdout.

    *repo* may be any directory inside the working tree — git itself resolves
    the repository from there, same as every wrapper in this module. Raises
    :class:`AssayError` (``ERROR`` / ``GIT_FAILED``) on a non-zero exit; the
    message carries the argv and the first 200 characters of stderr, matching
    the cited implementations.
    """
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise AssayError(
            f"git {' '.join(args)} failed ({proc.returncode}): "
            f"{proc.stderr.strip()[:200]}",
            outcome=Outcome.ERROR,
            reason_code=ReasonCode.GIT_FAILED,
        )
    return proc.stdout


def repo_top(repo: Path) -> Path:
    """The absolute, resolved top level of the git repository containing
    *repo* — what every ``git status --porcelain`` path is relative to,
    regardless of which subdirectory this process was pointed at."""
    return Path(run(repo, "rev-parse", "--show-toplevel").strip()).resolve()


def head_rev(repo: Path) -> str:
    """The full SHA of ``HEAD``."""
    return run(repo, "rev-parse", "HEAD").strip()


def resolve_base(repo: Path, base: str) -> str:
    """Resolve *base* against the shape of ``HEAD``.

    A merge commit (``HEAD`` has two or more parents) resolves to its first
    parent — the merged-into branch's tip immediately before the merge, so the
    delta measured is the merge's own payload. Any other ``HEAD`` resolves to
    ``merge-base(base, HEAD)`` — the fork point, so a feature branch is judged
    against what it actually diverged from rather than *base*'s current tip
    (which may have moved since).
    """
    tokens = run(repo, "rev-list", "--parents", "-n", "1", "HEAD").split()
    if len(tokens) >= 3:  # HEAD sha + >=2 parent shas
        return tokens[1]
    return run(repo, "merge-base", base, "HEAD").strip()


def dirty_paths(repo: Path) -> tuple[str, ...]:
    """Repo-top-relative paths of every staged, unstaged, or untracked change.

    ``git status --porcelain`` reports the index AND the worktree in one pass
    (unlike ``git diff``, which only ever shows one side), so a staged-but-
    uncommitted change is caught exactly like an unstaged one — both are
    equally invisible to a ``base..HEAD`` diff. Every non-blank porcelain line
    counts, whatever its two status letters say: staged, unstaged, and
    untracked (``??``) are not filtered differently here, because a change
    this function fails to report is a change a caller could measure past
    without knowing it was never actually seen. A rename/copy line
    (``old -> new``) keeps only the new path, since only it still exists in
    the tree.

    Scoping to a set of source roots is deliberately NOT done here — see
    ``measurability.check_dirty_tree``, which does that matching by resolved
    filesystem path rather than by string, and is where the distinction
    actually needs to be correct.
    """
    out = run(repo, "status", "--porcelain")
    paths: set[str] = set()
    for line in out.splitlines():
        # A blank line here would be an empty-string entry; ``splitlines()``
        # never produces one from real ``git status --porcelain`` output (no
        # changes yields zero lines, not one blank one), so no guard against
        # it is kept — an untestable guard is worse than none (AUTHORING.md
        # §3b.D).
        path = line[3:]
        if " -> " in path:  # rename/copy: "old -> new" — new path still exists
            path = path.split(" -> ", 1)[1]
        paths.add(path)
    return tuple(sorted(paths))
