"""The two git facts ``assemble`` binds to: a tag's commit and the project tree of a commit (memo 3.2).

Kept behind a two-method interface so the assembly logic is tested against a fake and the real subprocess calls are
tested once against a throw-away repository.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Protocol

_OID = re.compile(r"^[0-9a-f]{40}$")


class GitError(Exception):
    """A git query failed or returned something that is not an object id."""


class GitFacts(Protocol):
    def tag_commit(self, tag: str) -> str:
        """The commit ``tag`` peels to."""

    def tree(self, commit: str, path: str) -> str:
        """The tree (or blob) object id of ``path`` (project-relative POSIX, ``.`` for the root) at ``commit``."""

    def published(self, commit: str) -> bool:
        """Whether ``commit`` is reachable from ``refs/remotes/origin/main`` or from a tag."""


class RepoGit:
    """:class:`GitFacts` backed by ``git -C <repo>``."""

    def __init__(self, repo: Path) -> None:
        self.repo = Path(repo)

    def _rev_parse(self, spec: str) -> str:
        done = subprocess.run(
            ["git", "-C", str(self.repo), "rev-parse", "--verify", "--quiet", spec],
            capture_output=True, text=True, check=False,
        )
        out = done.stdout.strip()
        if done.returncode != 0 or not _OID.match(out):
            raise GitError(f"git cannot resolve {spec!r}")
        return out

    def tag_commit(self, tag: str) -> str:
        return self._rev_parse(f"refs/tags/{tag}^{{commit}}")

    def tree(self, commit: str, path: str) -> str:
        return self._rev_parse(f"{commit}^{{tree}}" if path == "." else f"{commit}:{path}")

    def _git(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(["git", "-C", str(self.repo), *args], capture_output=True, text=True, check=False)

    def published(self, commit: str) -> bool:
        """Reachable from ``refs/remotes/origin/main`` or from a tag that ORIGIN itself reports (``ls-remote --tags``).

        A local, unpushed tag or a side branch is no evidence. Anything that is not a full object id, an origin that
        cannot be asked, or a tag object this clone does not hold, is "not published" (fail closed).
        """
        if _OID.match(commit) is None:
            return False
        if self._git("merge-base", "--is-ancestor", commit, "refs/remotes/origin/main").returncode == 0:
            return True
        listed = self._git("ls-remote", "--tags", "origin")
        if listed.returncode != 0:
            return False
        peeled: dict[str, str] = {}
        for line in listed.stdout.splitlines():
            oid, _, ref = line.partition("\t")
            peeled[ref.removesuffix("^{}")] = oid  # git lists a tag object, then its peeled commit: the later line wins
        return any(self._git("merge-base", "--is-ancestor", commit, oid).returncode == 0 for oid in peeled.values())
