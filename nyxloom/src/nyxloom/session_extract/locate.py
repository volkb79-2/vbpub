"""Bare session-ref resolution: turn a pasted session id into the file (or
SQLite store + session id) the adapters already know how to read.

The problem this closes: every harness picks its own on-disk path for a
session, and none of them is something a human remembers -- Claude Code
buries it under an escaped-cwd project directory, Codex under a
YYYY/MM/DD tree with a timestamp in the filename, opencode inside one
machine-wide SQLite store. What DOES survive a terminal restart, a
scrollback copy, or a chat message is the session's own id. This module
maps that id back to the path, so `nyxloom extract <id>` works without
hand-constructing anything.

Three recognized ref shapes, each verified against this machine's own real
session history rather than assumed:

  - **Claude Code / Codex session UUID** -- the canonical 8-4-4-4-12 form.
    A Claude Code top-level session file is `<uuid>.jsonl` inside a project
    directory; a Codex rollout is `rollout-<timestamp>-<uuid>.jsonl` (real
    example: `rollout-2026-06-27T10-11-40-019f0890-43a2-75c2-9143-
    3f8d10ad4484.jsonl`), so the uuid is matched as a filename SUFFIX
    there, which also covers Codex sub-agent rollouts for free (sibling
    files in the same tree -- see adapters/codex.py's "Sub-agent targeting"
    note).
  - **Claude Code sub-agent agentId** -- 17 lowercase hex characters, NOT a
    uuid. This is a real-data correction to this feature's own design,
    which assumed a sub-agent id would fit the uuid shape: all 1117
    `subagents/agent-<id>.jsonl` files present on this machine have a
    17-hex-char id (e.g. `a36c6ff1d3cc69767`), zero exceptions, so a
    uuid-only trigger would have rejected every pasted sub-agent id
    outright. Searched only under Claude Code project dirs -- no other
    harness mints ids of this shape.
  - **opencode session id** -- `ses_` followed by alphanumerics. A second
    real-data correction: adapters/opencode.py's own docstring shows this id
    only TRUNCATED (`ses_0a6bb813bffe...`), which reads as hex, and a
    hex-only pattern was this feature's first design. Against the real
    935MB local store every one of the 72 session ids is `ses_` + 26 MIXED-
    CASE alphanumerics (real example: `ses_04bd4e9b4ffeBJm48T6v130DS6` --
    a hex-looking prefix, then base62), and NONE is pure hex, so a hex-only
    trigger would have rejected every real opencode id there is. opencode
    keeps one machine-wide store, not a per-project tree, so this is a
    short fixed candidate list of DB paths, not a walk.

Resolution rules, deliberately strict:

  - a ref that already EXISTS as a path is returned unchanged (so every
    call site can route its positional through here unconditionally);
  - exactly one match resolves transparently;
  - zero matches raise LocateError -- never a guess, never a "closest"
    match;
  - more than one match raises LocateError listing every candidate, so the
    caller can re-run with the full path they actually meant. Silently
    picking (newest, first found, ...) is exactly the failure mode that
    makes a resume land in the wrong session.

Claude Code's project directories are keyed by an escaped form of the cwd
the session ran in (`/workspaces/vbpub` -> `-workspaces-vbpub`), so the
directory matching the CURRENT cwd is searched first and short-circuits on
a unique hit. That ordering is a speed optimization only: correctness comes
from the full scan it falls back to, which is also what covers any cwd
whose escaping this module gets wrong (the rule below -- `/` and `.` both
to `-` -- is a best-effort mirror of Claude Code's own, checked against all
26 real project dirs on this machine, but it is the harness's rule, not
ours, and it is free to change).
"""

from __future__ import annotations

import os
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path


class LocateError(Exception):
    """A bare ref resolved to zero, or more than one, session."""


@dataclass
class SessionRef:
    """A located session: the path an adapter reads, plus -- for opencode
    only -- which session inside that store was meant. `session_id` is None
    for the file-per-session harnesses (Claude Code, Codex), where the path
    alone identifies the session; callers pass it straight through to
    extract()'s own `session_id` parameter (cli.py's --opencode-session).
    """

    path: Path
    session_id: str | None = None


_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE
)
# See the module docstring: real Claude Code sub-agent agentIds are 17 hex
# chars, not uuids (1117/1117 files on this machine).
_SUBAGENT_ID_RE = re.compile(r"^[0-9a-f]{17}$", re.IGNORECASE)
# Case-SENSITIVE on purpose: the suffix is base62 in real data, so an id's
# own case is meaningful and must be passed through to the DB lookup
# verbatim (see the module docstring's real-data note).
_OPENCODE_SESSION_RE = re.compile(r"^ses_[0-9A-Za-z]+$")


def _claude_projects_root() -> Path:
    return Path.home() / ".claude" / "projects"


def _codex_sessions_root() -> Path:
    return Path.home() / ".codex" / "sessions"


def _opencode_db_candidates() -> list[Path]:
    """The canonical default store named in adapters/opencode.py's own
    module docstring, plus the XDG override when that env var is actually
    set. Fixed, short list: opencode's storage is one machine-wide DB, so
    there is no tree to walk here."""
    candidates: list[Path] = []
    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        candidates.append(Path(xdg) / "opencode" / "opencode.db")
    default = Path.home() / ".local" / "share" / "opencode" / "opencode.db"
    if default not in candidates:
        candidates.append(default)
    return candidates


def escape_cwd(cwd: Path) -> str:
    """Claude Code's own project-directory naming for a cwd -- see the
    module docstring on why a wrong answer here costs speed, not
    correctness."""
    return str(cwd).replace("/", "-").replace(".", "-")


def _claude_matches_in(project_dir: Path, ref: str) -> list[Path]:
    """Both file layouts one Claude Code project directory can hold a
    session under: `<uuid>.jsonl` for the interactive session itself, and
    `<uuid>/subagents/agent-<agentId>.jsonl` for each dispatched sub-agent
    (the verified layout adapters/claude_code.py's own docstring documents).
    """
    wanted = ref.lower()
    matches = [
        f for f in sorted(project_dir.glob("*.jsonl"))
        if f.is_file() and f.stem.lower() == wanted
    ]
    matches += [
        f for f in sorted(project_dir.glob("*/subagents/agent-*.jsonl"))
        if f.is_file() and f.stem.lower() == f"agent-{wanted}"
    ]
    return matches


def _codex_matches(ref: str) -> list[Path]:
    root = _codex_sessions_root()
    if not root.is_dir():
        return []
    suffix = f"-{ref.lower()}"
    return sorted(
        f for f in root.glob("**/rollout-*.jsonl")
        if f.is_file() and f.stem.lower().endswith(suffix)
    )


def _opencode_matches(ref: str) -> list[Path]:
    from .adapters import opencode as opencode_adapter

    found: list[Path] = []
    for db in _opencode_db_candidates():
        if not db.is_file() or not opencode_adapter.sniff(db):
            continue
        try:
            conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        except sqlite3.Error:
            continue
        try:
            row = conn.execute("SELECT 1 FROM session WHERE id = ?", (ref,)).fetchone()
        except sqlite3.Error:
            row = None
        finally:
            conn.close()
        if row is not None:
            found.append(db)
    return found


def _ambiguous(ref: str, candidates: list[str]) -> LocateError:
    listing = "\n".join(f"  {c}" for c in candidates)
    return LocateError(
        f"session ref {ref!r} matches {len(candidates)} sessions -- pass the full "
        f"path (or --opencode-session) for the one you mean:\n{listing}"
    )


def resolve_session_ref(ref: str, cwd: Path | None = None) -> SessionRef:
    """Resolve a SESSION_LOG positional that may be a path OR a bare
    session id. See the module docstring for the recognized shapes and the
    0/1/>1 rules. Raises LocateError for anything that neither exists as a
    path nor resolves to exactly one session."""
    path = Path(ref).expanduser()
    if path.exists():
        return SessionRef(path=path)

    if _OPENCODE_SESSION_RE.match(ref):
        dbs = _opencode_matches(ref)
        if len(dbs) == 1:
            return SessionRef(path=dbs[0], session_id=ref)
        if not dbs:
            searched = ", ".join(str(p) for p in _opencode_db_candidates())
            raise LocateError(
                f"opencode session {ref!r} not found in any known store (searched: "
                f"{searched}) -- pass the store's path plus --opencode-session if it "
                f"lives somewhere else"
            )
        raise _ambiguous(ref, [f"{db} --opencode-session {ref}" for db in dbs])

    if _UUID_RE.match(ref) or _SUBAGENT_ID_RE.match(ref):
        projects_root = _claude_projects_root()
        if projects_root.is_dir():
            preferred = projects_root / escape_cwd(cwd or Path.cwd())
            if preferred.is_dir():
                hits = _claude_matches_in(preferred, ref)
                if len(hits) == 1:
                    return SessionRef(path=hits[0])
                if len(hits) > 1:
                    raise _ambiguous(ref, [str(h) for h in hits])

        candidates: list[Path] = []
        if projects_root.is_dir():
            for project_dir in sorted(projects_root.iterdir()):
                if project_dir.is_dir():
                    candidates += _claude_matches_in(project_dir, ref)
        if _UUID_RE.match(ref):
            candidates += _codex_matches(ref)

        unique: list[Path] = []
        for c in candidates:
            if c not in unique:
                unique.append(c)
        if len(unique) == 1:
            return SessionRef(path=unique[0])
        if not unique:
            raise LocateError(
                f"session {ref!r} not found under {projects_root} or "
                f"{_codex_sessions_root()} -- pass the session log's full path, or see "
                f"`nyxloom extract-sessions <directory>` to list what exists"
            )
        raise _ambiguous(ref, [str(c) for c in unique])

    raise LocateError(
        f"{ref!r} is neither an existing path nor a recognized session id "
        f"(a Claude Code/Codex uuid, a 17-hex-char Claude Code sub-agent agentId, "
        f"or an opencode `ses_...` id)"
    )
