"""`agctl estate`: one row per package, every field naming its source (DESIGN section 4a).

This build implements the row shape, per-field provenance and the git half of the inputs.
Git (`gitstate`) supplies the worktree, branch, head and merged-into-`origin/main` fields;
every other field of the row is a STATED `unknown` naming the reader that is still missing
(KNOWN_ISSUES AGC-1), so a reader of the output can always tell "nothing to report" from
"not read yet".

Contract the remaining readers must keep:

* A field carries `value`, `source`, `state` and `mtime`. A value that could not be
  established is `unknown` (or `missing` when the source is absent), with the reason in
  `detail` — never `""`, `0` or `-`, which read as facts.
* Nothing here writes, locks, or contacts a host or the network; git is the only subprocess.
"""

from __future__ import annotations

import argparse
import os
import time
from dataclasses import dataclass, field
from typing import Any

from cli_extended import CliRuntime

from . import config as ag_config
from . import gitstate

PRESENT = "present"
UNKNOWN = "unknown"
MISSING = "missing"

GIT_SOURCE = "git worktree list --porcelain"
MERGED_SOURCE = "git branch --merged origin/main"
PENDING = "reader not implemented yet: AGC-1"
FIELDS = ("owner", "plan", "worktree", "branch", "head", "merged", "runs", "gates", "handover")


@dataclass(frozen=True)
class Field:
    """One cell of a row: what it says, who said it, and how old that source is."""

    value: str
    source: str
    state: str = PRESENT
    mtime: float | None = None
    detail: str = ""

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"value": self.value, "source": self.source, "state": self.state}
        if self.mtime is not None:
            payload["mtime"] = self.mtime
        if self.detail:
            payload["detail"] = self.detail
        return payload

    def text(self) -> str:
        parts = [f"{self.value:14s}", f"{self.state:7s}", self.source]
        if self.mtime is not None:
            parts.append(time.strftime("mtime %Y-%m-%dT%H:%M:%SZ", time.gmtime(self.mtime)))
        if self.detail:
            parts.append(f"({self.detail})")
        return " ".join(parts)


def pending(name: str) -> Field:
    """An unimplemented reader's cell: a stated unknown naming the backlog item."""

    return Field(value=UNKNOWN, source=f"{name}: {PENDING}", state=UNKNOWN)


def _mtime_of(path: str) -> float | None:
    try:
        return os.stat(path).st_mtime
    except OSError:
        return None


def _git_field(value: str, path: str, state: str = PRESENT, detail: str = "") -> Field:
    """A git-derived cell; its age is the worktree's own `.git` entry (or the path itself)."""

    return Field(value=value, source=GIT_SOURCE, state=state, mtime=_mtime_of(os.path.join(path, ".git")) or _mtime_of(path), detail=detail)


def rows_from_git(view: gitstate.GitView) -> list[Row]:
    """One row per non-`main` branch: the package id is the branch name (the 4a join key)."""

    rows: list[Row] = []
    for entry in view.worktrees:
        # A detached or bare entry carries no package identity; it is reported in the header
        # count, never folded into a row that would invent one.
        if entry.bare or entry.detached or not entry.branch or entry.branch == "main":
            continue
        merged = view.merged_here(entry.branch)
        rows.append(
            Row(
                package=entry.branch,
                fields={
                    "owner": pending("owner"),
                    "plan": pending("plan"),
                    "worktree": _git_field(entry.path, entry.path),
                    "branch": _git_field(entry.branch, entry.path),
                    "head": _git_field(
                        entry.head or UNKNOWN,
                        entry.path,
                        state=PRESENT if entry.head else UNKNOWN,
                        detail="" if entry.head else "the porcelain block carries no HEAD line",
                    ),
                    "merged": Field(
                        value=merged,
                        source=MERGED_SOURCE,
                        state=PRESENT if merged in {"yes", "no"} else UNKNOWN,
                        detail="" if merged in {"yes", "no"} else view.detail,
                    ),
                    "runs": pending("runs"),
                    "gates": pending("gates"),
                    "handover": pending("handover"),
                },
            )
        )
    return rows


@dataclass
class Row:
    """One package."""

    package: str
    fields: dict[str, Field] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"package": self.package}
        payload.update({name: self.fields[name].as_dict() for name in FIELDS})
        return payload


def render_text(rows: list[Row], view: gitstate.GitView) -> str:
    lines = [
        f"estate: {len(rows)} package row(s) of {len(view.worktrees)} worktree entries (source: {GIT_SOURCE})"
    ]
    if view.merged_state != PRESENT:
        lines.append(f"estate: merge state UNKNOWN ({view.detail})")
    for row in rows:
        lines.extend(("", f"PACKAGE {row.package}"))
        lines.extend(f"  {name:9s} {row.fields[name].text()}" for name in FIELDS)
    lines.extend(("", "unverified: every cell whose state is 'unknown' (this build reads git only; AGC-1)"))
    return "\n".join(lines) + "\n"


def run(args: argparse.Namespace, runtime: CliRuntime, *, config: ag_config.Config | None = None) -> int:
    """The ``estate`` handler: resolve the inputs, read git, print rows. Read-only."""

    resolved = config if config is not None else ag_config.load()
    start = resolved.home
    root, detail = gitstate.discover_root(start)
    if root is None:
        # No repository is neither a crash nor an empty estate: it is a stated unknown.
        if runtime.json_mode:
            runtime.output.primary(
                {
                    "schema": "agctl-estate/1",
                    "root": None,
                    "rows": [],
                    "unknown": {"root": {"source": "git rev-parse --show-toplevel", "detail": detail}},
                }
            )
        else:
            runtime.output.primary(f"estate: UNKNOWN - no git work tree could be resolved ({detail})\n")
        return 0
    view = gitstate.read_view(root)
    rows = rows_from_git(view)
    if runtime.json_mode:
        runtime.output.primary(
            {
                "schema": "agctl-estate/1",
                "root": root,
                "git": {"worktrees": len(view.worktrees), "merged": view.merged_state, "detail": view.detail},
                "rows": [row.as_dict() for row in rows],
            }
        )
    else:
        runtime.output.primary(render_text(rows, view))
    return 0
