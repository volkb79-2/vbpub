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

Four recognized ref shapes, each verified against this machine's own real
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
  - **Reasonix session id** -- a timestamp/model filename stem such as
    `20260724-001821.579557040-deepseek-v4-flash`, or a `sa_...` subagent
    filename stem. Reasonix keeps primary files directly under
    `~/.reasonix/projects/<escaped-cwd>/sessions/` and subagents under its
    `sessions/subagents/` directory; the exact filename is matched across
    all escaped-cwd project directories.

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

Codex homes are namespaces. The default is ``~/.codex``; ``CODEX_HOME`` can
point Codex at another home, and this resolver also checks sibling
``~/.codex*`` homes so a UUID copied between local profiles cannot silently
select whichever profile happens to be the default. Every matching rollout
is a candidate, and more than one candidate is an error. A full path remains
the disambiguation mechanism.

Claude Code's project directories are keyed by an escaped form of the cwd
the session ran in (`/workspaces/vbpub` -> `-workspaces-vbpub`), so the
directory matching the CURRENT cwd is searched first. That ordering is a
speed optimization only: correctness comes from the full scan, which is
always performed so a preferred hit cannot hide another Claude Code or Codex
match. The full scan also covers any cwd whose escaping this module gets
wrong (the rule below -- `/` and `.` both to `-` -- is a best-effort mirror of
Claude Code's own, checked against all 26 real project dirs on this machine,
but it is the harness's rule, not ours, and it is free to change).
"""

from __future__ import annotations

import os
import re
import sqlite3
import stat
from dataclasses import dataclass
from pathlib import Path


class LocateError(Exception):
    """A bare ref was absent, ambiguous, or could not be resolved."""


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
_REASONIX_SESSION_RE = re.compile(
    r"^(?:\d{8}-\d{6}(?:\.\d+)?-[A-Za-z0-9][A-Za-z0-9._-]*|sa_[A-Za-z0-9_]+)$"
)


def _claude_projects_root() -> Path:
    return Path.home() / ".claude" / "projects"


def _codex_sessions_root() -> Path:
    configured = os.environ.get("CODEX_HOME")
    home = Path(configured).expanduser() if configured else Path.home() / ".codex"
    return home / "sessions"


def _codex_sessions_roots() -> list[Path]:
    """Return every locally discoverable Codex session namespace.

    ``CODEX_HOME`` is the active Codex home, while ``~/.codex`` is the
    default home. Both remain relevant to a bare UUID lookup: the same UUID
    can exist in two profiles, and choosing one would violate the resolver's
    strict exactly-one-match contract. Sibling hidden directories named
    ``.codex*`` cover locally named profiles such as ``.codex2`` when the
    lookup itself is launched without the one-shot ``CODEX_HOME=...``
    assignment that created the session.

    The public test seam is ``_codex_sessions_root``; retain it as the first
    root so callers and older tests that replace that function continue to
    exercise the same scan.
    """
    roots: list[Path] = []

    def add(root: Path) -> None:
        if root not in roots:
            roots.append(root)

    add(_codex_sessions_root())
    add(Path.home() / ".codex" / "sessions")

    # Do not use Path.glob here: on Python versions where directory
    # enumeration errors are softened by glob, an inaccessible profile could
    # disappear from the ambiguity check and make a wrong answer look valid.
    for candidate in _scan_directory(Path.home(), missing_ok=True):
        if candidate.name.startswith(".codex") and stat.S_ISDIR(_entry_mode(candidate)):
            add(candidate / "sessions")
    return roots


def _reasonix_projects_root() -> Path:
    return Path.home() / ".reasonix" / "projects"


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


def _scan_indeterminate(path: Path, operation: str, exc: OSError) -> LocateError:
    return LocateError(
        f"session lookup is indeterminate; could not {operation} {path}: "
        f"{type(exc).__name__}: {exc}"
    )


def _scan_directory(path: Path, *, missing_ok: bool = False) -> list[Path]:
    """List one directory without hiding traversal or metadata failures.

    ``Path.glob`` can turn an unreadable directory into an empty iterator on
    newer Python versions. An empty result is a valid negative only when the
    directory is absent (or genuinely empty); a failed scan must refuse to
    resolve a session from a partial view.
    """
    try:
        path_stat = path.stat()
    except FileNotFoundError as exc:
        if missing_ok:
            return []
        raise _scan_indeterminate(path, "inspect", exc) from exc
    except OSError as exc:
        raise _scan_indeterminate(path, "inspect", exc) from exc
    if not stat.S_ISDIR(path_stat.st_mode):
        return []

    try:
        return sorted(path.iterdir())
    except FileNotFoundError as exc:
        if missing_ok:
            return []
        raise _scan_indeterminate(path, "scan", exc) from exc
    except OSError as exc:
        raise _scan_indeterminate(path, "scan", exc) from exc


def _entry_mode(path: Path) -> int:
    try:
        return path.stat().st_mode
    except OSError as exc:
        raise _scan_indeterminate(path, "inspect", exc) from exc


def _claude_matches_in(project_dir: Path, ref: str) -> list[Path]:
    """Both file layouts one Claude Code project directory can hold a
    session under: `<uuid>.jsonl` for the interactive session itself, and
    `<uuid>/subagents/agent-<agentId>.jsonl` for each dispatched sub-agent
    (the verified layout adapters/claude_code.py's own docstring documents).
    """
    wanted = ref.lower()
    matches: list[Path] = []
    for path in _scan_directory(project_dir):
        mode = _entry_mode(path)
        if stat.S_ISREG(mode) and path.name.endswith(".jsonl"):
            if path.stem.lower() == wanted:
                matches.append(path)
            continue
        if not stat.S_ISDIR(mode):
            continue
        subagents = path / "subagents"
        for nested_path in _scan_directory(subagents, missing_ok=True):
            nested_mode = _entry_mode(nested_path)
            if (
                stat.S_ISREG(nested_mode)
                and nested_path.name.startswith("agent-")
                and nested_path.name.endswith(".jsonl")
                and nested_path.stem.lower() == f"agent-{wanted}"
            ):
                matches.append(nested_path)
    return matches


def _codex_matches(ref: str) -> list[Path]:
    suffix = f"-{ref.lower()}"
    matches: list[Path] = []

    def visit(directory: Path, *, missing_ok: bool = False) -> None:
        for path in _scan_directory(directory, missing_ok=missing_ok):
            mode = _entry_mode(path)
            if stat.S_ISDIR(mode):
                visit(path)
            elif (
                stat.S_ISREG(mode)
                and path.name.startswith("rollout-")
                and path.name.endswith(".jsonl")
                and path.stem.lower().endswith(suffix)
            ):
                matches.append(path)

    for root in _codex_sessions_roots():
        visit(root, missing_ok=True)
    return sorted(matches)


def _reasonix_matches(ref: str) -> list[Path]:
    """Find an exact primary or subagent Reasonix filename.

    The events companion suffix is intentionally not a candidate: matching
    only ``<id>.jsonl`` keeps replace-snapshot logs out of extraction even
    when a companion exists beside the primary file.
    """
    root = _reasonix_projects_root()
    matches: list[Path] = []
    wanted_name = f"{ref}.jsonl"

    for project_dir in _scan_directory(root, missing_ok=True):
        if not stat.S_ISDIR(_entry_mode(project_dir)):
            continue
        sessions = project_dir / "sessions"
        for session_path in _scan_directory(sessions, missing_ok=True):
            mode = _entry_mode(session_path)
            if stat.S_ISREG(mode) and session_path.name == wanted_name:
                matches.append(session_path)
                continue
            if not stat.S_ISDIR(mode) or session_path.name != "subagents":
                continue
            for subagent_path in _scan_directory(session_path, missing_ok=True):
                if (
                    stat.S_ISREG(_entry_mode(subagent_path))
                    and subagent_path.name == wanted_name
                ):
                    matches.append(subagent_path)
    return sorted(matches)


def _opencode_matches(ref: str) -> list[Path]:
    from .adapters import opencode as opencode_adapter

    found: list[Path] = []
    failures: list[str] = []
    for db in _opencode_db_candidates():
        try:
            db_stat = db.stat()
        except FileNotFoundError:
            continue
        except OSError as exc:
            failures.append(f"{db}: path lookup failed: {type(exc).__name__}: {exc}")
            continue
        if not stat.S_ISREG(db_stat.st_mode):
            continue

        try:
            sniffed = opencode_adapter.sniff(db)
        except Exception as exc:  # census: process-boundary translation (nyxloom-P112)
            failures.append(f"{db}: sniff failed: {type(exc).__name__}: {exc}")
            continue

        if not sniffed:
            # opencode.sniff() deliberately exposes a boolean API and handles
            # its own SQLite errors as False. Re-open the candidate just
            # enough to tell a readable, non-opencode SQLite database (a
            # genuine negative) from a failed sniff (indeterminate).
            probe_error = _sqlite_read_probe_error(db)
            if probe_error is not None:
                failures.append(f"{db}: sniff failed: {probe_error}")
            continue

        try:
            conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        except Exception as exc:  # census: process-boundary translation (nyxloom-P112)
            failures.append(f"{db}: query failed: {type(exc).__name__}: {exc}")
            continue

        try:
            row = conn.execute("SELECT 1 FROM session WHERE id = ?", (ref,)).fetchone()
        except Exception as exc:  # census: process-boundary translation (nyxloom-P112)
            failures.append(f"{db}: query failed: {type(exc).__name__}: {exc}")
            continue
        finally:
            try:
                conn.close()
            except Exception as exc:  # census: process-boundary translation (nyxloom-P112)
                failures.append(f"{db}: query cleanup failed: {type(exc).__name__}: {exc}")
        if row is not None:
            found.append(db)

    if failures:
        details = "\n".join(f"  {failure}" for failure in failures)
        raise LocateError(
            f"opencode lookup for session {ref!r} is indeterminate; a known store "
            f"could not be inspected:\n{details}"
        )
    return found


def _sqlite_read_probe_error(db: Path) -> str | None:
    """Return a sniff failure, or None when ``db`` is a genuine negative.

    ``opencode.sniff`` returns False for both a readable database with the
    wrong schema and SQLite/open failures. The former is a genuine negative;
    the latter is indeterminate and must not become a "session not found"
    answer. This small probe separates those two outcomes without changing
    the adapter's public sniff contract.
    """
    conn: sqlite3.Connection | None = None
    failure: str | None = None
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        if {"session", "message", "part"}.issubset(tables):
            failure = "sniff returned False for a database with the expected opencode tables"
    except Exception as exc:  # census: process-boundary translation (nyxloom-P112)
        failure = f"{type(exc).__name__}: {exc}"
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception as exc:  # census: process-boundary translation (nyxloom-P112)
                failure = f"{type(exc).__name__}: {exc}"
    return failure


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

    if _REASONIX_SESSION_RE.match(ref):
        candidates = _reasonix_matches(ref)
        if len(candidates) == 1:
            return SessionRef(path=candidates[0])
        if not candidates:
            raise LocateError(
                f"Reasonix session {ref!r} not found under {_reasonix_projects_root()} "
                f"-- pass the primary session file's full path, or see "
                f"`nyxloom extract-sessions <directory>` to list what exists"
            )
        raise _ambiguous(ref, [str(c) for c in candidates])

    if _UUID_RE.match(ref) or _SUBAGENT_ID_RE.match(ref):
        projects_root = _claude_projects_root()
        candidates: list[Path] = []
        project_dirs: list[Path] = []
        for project_dir in _scan_directory(projects_root, missing_ok=True):
            if stat.S_ISDIR(_entry_mode(project_dir)):
                project_dirs.append(project_dir)
        preferred = projects_root / escape_cwd(cwd or Path.cwd())
        ordered_project_dirs = []
        if preferred in project_dirs:
            ordered_project_dirs.append(preferred)
        ordered_project_dirs.extend(
            project_dir for project_dir in project_dirs if project_dir != preferred
        )
        for project_dir in ordered_project_dirs:
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
            codex_roots = ", ".join(str(root) for root in _codex_sessions_roots())
            raise LocateError(
                f"session {ref!r} not found under {projects_root} or "
                f"{codex_roots} -- pass the session log's full path, or see "
                f"`nyxloom extract-sessions <directory>` to list what exists"
            )
        raise _ambiguous(ref, [str(c) for c in unique])

    raise LocateError(
        f"{ref!r} is neither an existing path nor a recognized session id "
        f"(a Claude Code/Codex uuid, a 17-hex-char Claude Code sub-agent agentId, "
        f"a Reasonix timestamp/model or `sa_...` sub-agent id, or an opencode "
        f"`ses_...` id)"
    )
