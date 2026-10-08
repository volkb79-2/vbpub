"""Rank sessions by the words in their locally stored transcripts."""

from __future__ import annotations

import json
import math
import os
import re
import select
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time
from collections import Counter
from collections.abc import Callable, Iterator, Sequence
from concurrent.futures import FIRST_COMPLETED, Future, ProcessPoolExecutor, wait
from contextlib import closing
from dataclasses import dataclass
from functools import lru_cache
from itertools import product
from pathlib import Path
from typing import Any

from .session_extract.adapters import codex, opencode
from .session_extract.locate import (
    LocateError,
    _claude_projects_root,
    _codex_sessions_roots,
    _opencode_db_candidates,
    _scan_directory,
)

CLIENTS = ("codex", "claude", "opencode")
SORT_ORDERS = ("best", "date")
WORD_MATCHES = ("any", "all")
TERM_MATCHES = ("exact", "prefix")
_WORD = re.compile(r"[^\W_]+", re.UNICODE)
_NONASCII_BYTES = re.compile(rb"[\x80-\xff]")
_JSONL_CHUNK_SIZE = 8 * 1024 * 1024
_ACTIVITY_CHUNK_SIZE = 1024 * 1024
_PROGRESS_FILE_THRESHOLD = 64 * 1024 * 1024
_PROGRESS_BYTE_STEP = 16 * 1024 * 1024
_RG_ARGV_PATH_BUDGET = 128 * 1024
_TERM_FREQUENCY_CAP = 64
_RIPGREP_PARALLEL_AFTER_CANDIDATES = 512
_RIPGREP_MAX_WORKERS = 4
_RIPGREP_MAX_PENDING_PER_WORKER = 2
_RIPGREP_BATCH_RECORD_LIMIT = 128
_RIPGREP_BATCH_BYTE_LIMIT = 1024 * 1024
_RIPGREP_WORKER_RECORD_BYTE_LIMIT = 4 * 1024 * 1024
_METADATA_KEYS = {
    "agent",
    "cli_version",
    "cwd",
    "created_at",
    "createdat",
    "event_id",
    "eventid",
    "git_branch",
    "gitbranch",
    "id",
    "message_id",
    "messageid",
    "model",
    "model_name",
    "model_provider",
    "modelname",
    "ordinal",
    "originator",
    "parent_id",
    "parentid",
    "parentuuid",
    "provider_id",
    "providerid",
    "request_id",
    "requestid",
    "role",
    "sessionid",
    "session_id",
    "subtype",
    "source",
    "thread_id",
    "threadid",
    "thread_source",
    "time",
    "time_created",
    "timecreated",
    "time_updated",
    "timeupdated",
    "timestamp",
    "turn_id",
    "turnid",
    "type",
    "updated_at",
    "updatedat",
    "user_hash",
    "uuid",
    "version",
}


class SearchError(ValueError):
    """Search could not inspect a declared local session store completely."""


@dataclass(frozen=True)
class SearchResult:
    client: str
    session_id: str
    source: str
    last_activity: str | None
    matched_terms: tuple[str, ...]
    score: float


@dataclass(frozen=True)
class _Document:
    client: str
    session_id: str
    source: str
    last_activity: str | None
    term_counts: Counter[str]


@dataclass(frozen=True)
class _DiscoveredSession:
    client: str
    session_id: str
    source: str
    last_activity: str | None = None


def _terms(text: str) -> tuple[str, ...]:
    terms = tuple(dict.fromkeys(token.casefold() for token in _WORD.findall(text)))
    if not terms:
        raise SearchError("search query must contain at least one word")
    return terms


Progress = Callable[[str, int | None, int | None], None]


def _report(progress: Progress | None, message: str, current=None, total=None) -> None:
    if progress is not None:
        progress(message, current, total)


def _path_identity(path: Path) -> tuple[int, int] | None:
    """Return the physical file identity, preserving indeterminate errors."""
    try:
        result = path.stat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise SearchError(
            f"session search is indeterminate; could not inspect {path}: "
            f"{type(exc).__name__}: {exc}"
        ) from exc
    return result.st_dev, result.st_ino


def _dedupe_paths(paths: Sequence[Path]) -> tuple[Path, ...]:
    seen: set[tuple[int, int]] = set()
    lexical: set[Path] = set()
    result: list[Path] = []
    for path in paths:
        if path in lexical:
            continue
        lexical.add(path)
        identity = _path_identity(path)
        if identity is not None:
            if identity in seen:
                continue
            seen.add(identity)
        result.append(path)
    return tuple(result)


def _source_roots(paths: Sequence[str | Path] | None) -> tuple[Path, ...] | None:
    if paths is None:
        return None
    roots: list[Path] = []
    for value in paths:
        path = Path(value).expanduser()
        try:
            result = path.stat()
        except OSError as exc:
            raise SearchError(
                f"could not inspect explicit source root {path}: "
                f"{type(exc).__name__}: {exc}"
            ) from exc
        if not (stat.S_ISDIR(result.st_mode) or stat.S_ISREG(result.st_mode)):
            raise SearchError(f"source root is not a regular file or directory: {path}")
        roots.append(path)
    if not roots:
        raise SearchError("at least one --source-root is required when source roots are supplied")
    # Keep physical aliases until each client has interpreted the path. An
    # OpenCode database is selected partly by its spelling (the `.db` suffix),
    # so inode-deduping `store.sqlite` before an alias `store.db` is filtered
    # can discard the only compatible root.
    return tuple(dict.fromkeys(roots))


def _directory_roots(
    source_roots: tuple[Path, ...], client: str, *, required: bool,
) -> tuple[Path, ...]:
    directories = []
    for root in source_roots:
        try:
            if stat.S_ISDIR(root.stat().st_mode):
                directories.append(root)
        except OSError as exc:
            raise SearchError(f"could not inspect explicit source root {root}: {exc}") from exc
    if not directories and required:
        raise SearchError(
            f"{client} requires at least one directory --source-root"
        )
    return _dedupe_paths(directories)


def _root_is_available(root: Path) -> bool:
    try:
        mode = root.stat().st_mode
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise SearchError(
            f"session search is indeterminate; could not inspect {root}: "
            f"{type(exc).__name__}: {exc}"
        ) from exc
    if not stat.S_ISDIR(mode):
        raise SearchError(f"session store root is not a directory: {root}")
    try:
        _scan_directory(root)
    except LocateError as exc:
        raise SearchError(str(exc)) from exc
    return True


def _opencode_stores(source_roots: tuple[Path, ...] | None) -> tuple[Path, ...]:
    if source_roots is None:
        candidates = _opencode_db_candidates()
    else:
        candidates = []
        for root in source_roots:
            try:
                mode = root.stat().st_mode
            except OSError as exc:
                raise SearchError(f"could not inspect explicit source root {root}: {exc}") from exc
            if stat.S_ISREG(mode):
                if root.suffix == ".db":
                    candidates.append(root)
            elif stat.S_ISDIR(mode):
                candidates.append(root / "opencode.db")
    return _dedupe_paths(candidates)


def _store_sessions(
    client: str,
    *,
    source_roots: tuple[Path, ...] | None = None,
    progress: Progress | None = None,
    require_compatible_roots: bool = False,
) -> list[_DiscoveredSession]:
    if client == "claude":
        roots = (
            (_claude_projects_root(),)
            if source_roots is None
            else _directory_roots(
                source_roots, client, required=require_compatible_roots,
            )
        )
        found = []
        indexes: dict[tuple[int, int] | Path, int] = {}
        priorities: dict[tuple[int, int] | Path, int] = {}
        for index, root in enumerate(roots, 1):
            try:
                if not stat.S_ISDIR(root.stat().st_mode):
                    continue
            except FileNotFoundError:
                continue
            except OSError as exc:
                raise SearchError(f"could not inspect Claude Code session root {root}: {exc}") from exc
            _report(progress, f"Discovering Claude Code sessions under {root}", index, len(roots))
            for session in _claude_sessions(root, progress=progress):
                _merge_claude_session(found, indexes, priorities, session)
        return found
    if client == "codex":
        if source_roots is None:
            try:
                roots = tuple(_codex_sessions_roots())
            except LocateError as exc:
                raise SearchError(str(exc)) from exc
        else:
            roots = _directory_roots(
                source_roots, client, required=require_compatible_roots,
            )
        if not roots:
            return []
        return _codex_sessions(roots, progress=progress)

    seen_stores: set[tuple[int, int] | Path] = set()
    found: list[_DiscoveredSession] = []
    stores = _opencode_stores(source_roots)
    if source_roots is not None and not any(
        _path_identity(store) is not None for store in stores
    ):
        if require_compatible_roots:
            raise SearchError(
                "no OpenCode database found at an explicit --source-root; pass an "
                "opencode.db file or a directory containing opencode.db"
            )
        return []
    for index, store in enumerate(stores, 1):
        _report(progress, f"Discovering OpenCode sessions in {store}", index, len(stores))
        try:
            resolved = store.resolve(strict=True)
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise SearchError(
                f"session search is indeterminate; could not inspect {store}: "
                f"{type(exc).__name__}: {exc}"
            ) from exc
        identity = _path_identity(resolved)
        key: tuple[int, int] | Path = identity if identity is not None else resolved
        if key in seen_stores:
            continue
        seen_stores.add(key)
        try:
            nodes = opencode.list_agents(resolved)
        except (OSError, sqlite3.Error, ValueError) as exc:
            raise SearchError(f"could not search OpenCode store {resolved}: {exc}") from exc
        found.extend(
            _DiscoveredSession("opencode", node.id, node.path, node.last_ts)
            for node in nodes
        )
    return found


def _walk_files(root: Path, *, progress: Progress | None = None) -> Iterator[Path]:
    """Walk a session root without hiding unreadable directories or entries."""
    pending = [root]
    visited_directories: set[tuple[int, int]] = set()
    entries_seen = 0
    while pending:
        directory = pending.pop()
        identity = _path_identity(directory)
        if identity is not None:
            if identity in visited_directories:
                continue
            visited_directories.add(identity)
        try:
            entries = _scan_directory(directory)
        except LocateError as exc:
            raise SearchError(str(exc)) from exc
        for path in entries:
            entries_seen += 1
            if entries_seen == 1 or entries_seen % 500 == 0:
                _report(progress, f"Walking {root}: {entries_seen:,} entries")
            try:
                mode = path.lstat().st_mode
            except OSError as exc:
                raise SearchError(
                    f"session search is indeterminate; could not inspect {path}: "
                    f"{type(exc).__name__}: {exc}"
                ) from exc
            if stat.S_ISDIR(mode):
                pending.append(path)
            elif stat.S_ISLNK(mode):
                try:
                    target_mode = path.stat().st_mode
                except FileNotFoundError:
                    continue
                except OSError as exc:
                    raise SearchError(
                        f"session search is indeterminate; could not inspect {path}: "
                        f"{type(exc).__name__}: {exc}"
                    ) from exc
                if stat.S_ISREG(target_mode):
                    yield path
            elif stat.S_ISREG(mode):
                yield path


def _claude_session_priority(session: _DiscoveredSession) -> int:
    source = Path(session.source)
    if source.parent.name == "subagents" and source.stem.startswith("agent-"):
        return 3
    if source.stem.casefold() == session.session_id.casefold():
        return 2
    return 1


def _merge_claude_session(
    found: list[_DiscoveredSession],
    indexes: dict[tuple[int, int] | Path, int],
    priorities: dict[tuple[int, int] | Path, int],
    candidate: _DiscoveredSession,
) -> None:
    source = Path(candidate.source)
    identity = _path_identity(source)
    key: tuple[int, int] | Path = identity if identity is not None else source
    priority = _claude_session_priority(candidate)
    if key not in indexes:
        indexes[key] = len(found)
        priorities[key] = priority
        found.append(candidate)
        return

    current_index = indexes[key]
    current = found[current_index]
    current_priority = priorities[key]
    if (
        priority == current_priority
        and candidate.session_id.casefold() != current.session_id.casefold()
    ):
        raise SearchError(
            "session search is indeterminate; hard-linked Claude Code "
            f"transcript {source} has conflicting session IDs "
            f"{current.session_id!r} and {candidate.session_id!r}"
        )
    if priority > current_priority:
        found[current_index] = candidate
        priorities[key] = priority


def _claude_sessions(
    root: Path, *, progress: Progress | None = None,
) -> list[_DiscoveredSession]:
    if not _root_is_available(root):
        return []
    found: list[_DiscoveredSession] = []
    indexes: dict[tuple[int, int] | Path, int] = {}
    priorities: dict[tuple[int, int] | Path, int] = {}
    for path in _walk_files(root, progress=progress):
        if path.suffix != ".jsonl":
            continue
        is_transcript, transcript_session_id = _claude_transcript_metadata(path)
        if not is_transcript:
            continue
        try:
            # Report the target's real filename and ID when discovery first
            # encounters a symlink alias (including a symlinked parent root).
            source = path.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise SearchError(
                f"session search is indeterminate; could not resolve Claude Code "
                f"session {path}: {type(exc).__name__}: {exc}"
            ) from exc
        if source.parent.name == "subagents" and source.stem.startswith("agent-"):
            session_id = source.stem.removeprefix("agent-")
        elif transcript_session_id:
            session_id = transcript_session_id
        else:
            session_id = source.stem
        candidate = _DiscoveredSession("claude", session_id, str(source))
        _merge_claude_session(found, indexes, priorities, candidate)
    return found


def _claude_transcript_metadata(path: Path) -> tuple[bool, str | None]:
    """Return the Claude discriminator and source-declared session ID."""
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as source:
            for index, line in enumerate(source):
                if index >= 50:
                    break
                if r"\u" not in line and not (
                    '"sessionId"' in line and '"parentUuid"' in line
                ):
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if (
                    isinstance(record, dict)
                    and "sessionId" in record
                    and "parentUuid" in record
                ):
                    session_id = record["sessionId"]
                    return True, session_id if isinstance(session_id, str) else None
    except OSError as exc:
        raise SearchError(f"could not inspect Claude Code session {path}: {exc}") from exc
    return False, None


def _is_claude_transcript(path: Path) -> bool:
    """Use the adapter's record discriminator while preserving read errors."""
    return _claude_transcript_metadata(path)[0]


def _codex_sessions(
    roots: tuple[Path, ...], *, progress: Progress | None = None,
) -> list[_DiscoveredSession]:
    """List Codex rollouts in one filesystem pass.

    ``sessions.list_agents(directory)`` resolves each rollout's whole family
    by rescanning the sessions tree. Search needs every individual session,
    so calling it once per root thread makes discovery quadratic in the
    number of rollouts. Reuse its verified session metadata discriminator
    while walking each root only once; the transcript pass below gathers
    content and source timestamps together.
    """
    found: list[_DiscoveredSession] = []
    visited_roots: set[tuple[int, int] | Path] = set()
    seen_files: set[tuple[int, int]] = set()
    for index, root in enumerate(_dedupe_paths(roots), 1):
        _report(progress, f"Discovering Codex sessions under {root}", index, len(roots))
        try:
            if not stat.S_ISDIR(root.stat().st_mode):
                continue
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise SearchError(f"could not inspect Codex session root {root}: {exc}") from exc
        identity = _path_identity(root)
        root_key: tuple[int, int] | Path = identity if identity is not None else root
        if root_key in visited_roots or not _root_is_available(root):
            continue
        visited_roots.add(root_key)
        for path in _walk_files(root, progress=progress):
            if not path.name.startswith("rollout-") or not path.name.endswith(".jsonl"):
                continue
            file_identity = _path_identity(path)
            if file_identity is not None and file_identity in seen_files:
                continue
            if file_identity is not None:
                seen_files.add(file_identity)
            metadata = _codex_metadata(path)
            if metadata is None or not isinstance(metadata.get("session_id"), str):
                continue
            session_id = metadata.get("id") or str(path)
            found.append(_DiscoveredSession("codex", str(session_id), str(path)))
    return found


def _codex_metadata(path: Path) -> dict[str, Any] | None:
    """Read Codex's first session metadata without hiding I/O failures."""
    first_payload: dict[str, Any] | None = None
    seen_session_meta = False
    supported = False
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as source:
            for index, line in enumerate(source):
                if index >= codex._SNIFF_SCAN_LINES:
                    break
                if r"\u" not in line and '"session_meta"' not in line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(record, dict) or record.get("type") != "session_meta":
                    continue
                payload = record.get("payload")
                if not seen_session_meta:
                    seen_session_meta = True
                    first_payload = payload if isinstance(payload, dict) else None
                if isinstance(payload, dict) and "cli_version" in payload:
                    supported = True
                if seen_session_meta and supported:
                    break
    except OSError as exc:
        raise SearchError(f"could not inspect Codex session {path}: {exc}") from exc
    return first_payload if supported else None


def _text_values(value: Any) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, child in value.items():
            if str(key).casefold() in _METADATA_KEYS:
                continue
            yield from _text_values(child)
    elif isinstance(value, list):
        for child in value:
            yield from _text_values(child)


@lru_cache(maxsize=64)
def _query_pattern(terms: tuple[str, ...], term_match: str) -> re.Pattern[str]:
    alternatives = "|".join(re.escape(term) for term in terms)
    suffix = "" if term_match == "prefix" else r"(?![A-Za-z0-9])"
    return re.compile(
        r"(?<![A-Za-z0-9])(?:" + alternatives + ")" + suffix,
        re.IGNORECASE | re.ASCII,
    )


@lru_cache(maxsize=64)
def _query_text_pattern(
    terms: tuple[str, ...], term_match: str,
) -> re.Pattern[str] | None:
    if not all(term.isascii() for term in terms):
        return None
    suffix = "" if term_match == "prefix" else r"(?![A-Za-z0-9])"
    alternatives = "|".join(
        re.escape(term) for term in sorted(terms, key=len, reverse=True)
    )
    return re.compile(
        r"(?<![A-Za-z0-9])(?:" + alternatives + ")" + suffix,
        re.IGNORECASE | re.ASCII,
    )


@lru_cache(maxsize=64)
def _query_bytes_pattern(
    terms: tuple[str, ...], term_match: str,
) -> re.Pattern[bytes] | None:
    if not all(term.isascii() for term in terms):
        return None
    alternatives = b"|".join(re.escape(term.encode("ascii")) for term in terms)
    suffix = b"" if term_match == "prefix" else rb"(?![A-Za-z0-9])"
    return re.compile(
        rb"(?<![A-Za-z0-9])(?:" + alternatives + b")" + suffix,
        re.IGNORECASE | re.ASCII,
    )


@lru_cache(maxsize=64)
def _candidate_bytes_pattern(
    terms: tuple[str, ...], term_match: str,
) -> re.Pattern[bytes] | None:
    query_pattern = _query_bytes_pattern(terms, term_match)
    if query_pattern is None:
        return None
    return re.compile(
        b"(?:" + query_pattern.pattern + rb")|[\x80-\xff]|\\u",
        re.IGNORECASE | re.ASCII,
    )


def _raw_may_match(text: str, terms: tuple[str, ...], term_match: str) -> bool:
    """Cheap candidate filter; return True whenever parsing is required.

    JSON escaping and Unicode case-fold expansions make a raw miss unsafe, so
    those lines deliberately bypass the filter. A raw hit is only a candidate:
    JSON parsing and text-field filtering still decide whether it matches.
    """
    if not text.isascii() or r"\u" in text:
        return True
    return _query_pattern(terms, term_match).search(text) is not None


def _raw_bytes_may_match(raw_line: bytes, terms: tuple[str, ...], term_match: str) -> bool:
    pattern = _query_bytes_pattern(terms, term_match)
    if pattern is None:
        return True
    if pattern.search(raw_line) is not None:
        return True
    casefold_pattern = _query_casefold_bytes_pattern(terms)
    if casefold_pattern is not None and casefold_pattern.search(raw_line) is not None:
        return True
    escape_pattern = _query_escape_bytes_pattern(terms)
    return escape_pattern is not None and escape_pattern.search(raw_line) is not None


def _filtered_jsonl_counts(
    path: Path,
    terms: tuple[str, ...],
    term_match: str,
    *,
    progress: Progress | None = None,
    client: str | None = None,
    index: int | None = None,
    total: int | None = None,
) -> Counter[str] | None:
    """Parse only matching or Unicode/escaped records from a JSONL file.

    A native regex finds query candidates and records that need JSON decoding
    to resolve Unicode case-folding or JSON escapes. All other lines can be
    skipped safely. Fixed-size reads bound memory to a chunk plus the largest
    record that crosses a chunk boundary.
    """
    pattern = _candidate_bytes_pattern(terms, term_match)
    if pattern is None:
        return None
    counts: Counter[str] = Counter()

    def report_candidate_progress() -> None:
        if progress is not None and matched_lines % 4096 == 0:
            _report(
                progress,
                f"Checking {client or 'session'} transcript {path.name}: "
                f"{matched_lines:,} candidate records",
                matched_lines,
                None,
            )

    def count_record(
        buffer: bytes | bytearray, start: int, end: int,
    ) -> None:
        nonlocal matched_lines
        if pattern.search(buffer, start, end) is None:
            return
        matched_lines += 1
        active_terms = _active_query_terms(
            counts, terms, _TERM_FREQUENCY_CAP,
        )
        if not active_terms:
            report_candidate_progress()
            return
        if len(active_terms) != len(terms):
            active_pattern = _candidate_bytes_pattern(active_terms, term_match)
            if active_pattern is None or active_pattern.search(buffer, start, end) is None:
                report_candidate_progress()
                return
        raw_line = bytes(buffer[start:end])
        try:
            record = json.loads(raw_line.decode("utf-8", errors="replace"))
        except json.JSONDecodeError:
            report_candidate_progress()
            return
        _add_text(counts, record, terms, term_match)
        report_candidate_progress()

    try:
        with path.open("rb") as source:
            file_size = source.seek(0, 2)
            source.seek(0)
            if file_size == 0:
                return counts
            if progress is not None and file_size >= _PROGRESS_FILE_THRESHOLD:
                _report(
                    progress,
                    f"Searching {client or 'session'} transcript {path.name}",
                    0,
                    file_size,
                )
            pending = bytearray()
            bytes_read = 0
            last_progress = 0
            matched_lines = 0
            while True:
                block = source.read(_JSONL_CHUNK_SIZE)
                eof = not block
                cursor = 0
                while (line_end := block.find(b"\n", cursor)) >= 0:
                    if pending:
                        pending.extend(block[cursor:line_end])
                        count_record(pending, 0, len(pending))
                        pending.clear()
                    else:
                        count_record(block, cursor, line_end)
                    cursor = line_end + 1
                if cursor < len(block):
                    pending.extend(block[cursor:])
                bytes_read += len(block)
                if (
                    progress is not None
                    and file_size >= _PROGRESS_FILE_THRESHOLD
                    and bytes_read - last_progress >= _PROGRESS_BYTE_STEP
                ):
                    _report(
                        progress,
                        f"Searching {client or 'session'} transcript {path.name}",
                        bytes_read,
                        file_size,
                    )
                    last_progress = bytes_read
                if eof:
                    if pending:
                        count_record(pending, 0, len(pending))
                    break
    except OSError as exc:
        raise SearchError(f"could not scan session transcript {path}: {exc}") from exc
    return counts


@lru_cache(maxsize=64)
def _ripgrep_casefold_chars(terms: tuple[str, ...]) -> tuple[str, ...]:
    """Return non-ASCII characters whose casefold can contribute to a term."""
    query_characters = set("".join(terms))
    candidates = []
    for codepoint in range(128, 0x110000):
        character = chr(codepoint)
        folded = character.casefold()
        if folded != character and query_characters.intersection(folded):
            candidates.append(character)
    return tuple(candidates)


@lru_cache(maxsize=64)
def _ripgrep_escape_literals(terms: tuple[str, ...]) -> tuple[str, ...]:
    query_characters = set("".join(terms))
    characters = query_characters | set(_ripgrep_casefold_chars(terms))
    candidates = set()
    for character in characters:
        for variant in {character, *character.upper()}:
            if not query_characters.intersection(variant.casefold()):
                continue
            encoded = variant.encode("utf-16-be")
            code_units = tuple(
                (encoded[index] << 8) | encoded[index + 1]
                for index in range(0, len(encoded), 2)
            )
            for escape_case in product((str.lower, str.upper), repeat=len(code_units)):
                candidates.add("".join(
                    r"\u" + escape_case[index](f"{codepoint:04x}")
                    for index, codepoint in enumerate(code_units)
                ))
    return tuple(sorted(candidates))


def _ripgrep_pattern(terms: tuple[str, ...], term_match: str) -> str | None:
    """Build a safe regex filter without making Unicode candidates case-blind."""
    if not all(term.isascii() for term in terms):
        return None
    left = r"(?:^|[^A-Za-z0-9])"
    right = r"(?:$|[^A-Za-z0-9])" if term_match == "exact" else ""
    query = f"(?i:{'|'.join(re.escape(term) for term in terms)})"
    candidates = [left + query + right]
    candidates.extend(re.escape(character) for character in _ripgrep_casefold_chars(terms))
    candidates.extend(re.escape(literal) for literal in _ripgrep_escape_literals(terms))
    return "(?:" + "|".join(candidates) + ")"


@lru_cache(maxsize=64)
def _query_casefold_bytes_pattern(terms: tuple[str, ...]) -> re.Pattern[bytes] | None:
    chars = _ripgrep_casefold_chars(terms)
    if not chars:
        return None
    alternatives = b"|".join(re.escape(char.encode("utf-8")) for char in chars)
    return re.compile(b"(?:" + alternatives + b")")


@lru_cache(maxsize=64)
def _query_escape_bytes_pattern(terms: tuple[str, ...]) -> re.Pattern[bytes] | None:
    literals = _ripgrep_escape_literals(terms)
    if not literals:
        return None
    alternatives = b"|".join(re.escape(literal.encode("ascii")) for literal in literals)
    return re.compile(b"(?:" + alternatives + b")", re.IGNORECASE | re.ASCII)


def _ripgrep_path_batches(paths: Sequence[Path]) -> Iterator[tuple[Path, ...]]:
    batch: list[Path] = []
    size = 0
    for path in paths:
        path_size = len(os.fsencode(path)) + 1
        if batch and size + path_size > _RG_ARGV_PATH_BUDGET:
            yield tuple(batch)
            batch = []
            size = 0
        batch.append(path)
        size += path_size
    if batch:
        yield tuple(batch)


def _ripgrep_stdout_ready(stream, timeout: float) -> bool:
    """Wait briefly for scanner output, with a compatibility path for test streams."""
    try:
        descriptor = stream.fileno()
    except (AttributeError, OSError, ValueError):
        return True
    readable, _, _ = select.select((descriptor,), (), (), timeout)
    return bool(readable)


def _ripgrep_records(stream, *, progress: Progress | None, client: str):
    """Yield (path, JSONL record) frames without confusing newlines in paths."""
    buffer = bytearray()
    path_bytes: bytes | None = None
    started = time.monotonic()
    while True:
        if progress is not None and not _ripgrep_stdout_ready(stream, 1.0):
            elapsed = int(time.monotonic() - started)
            _report(
                progress,
                f"Ripgrep is still searching {client} transcripts ({elapsed}s elapsed)",
            )
            continue
        read = getattr(stream, "read1", stream.read)
        chunk = read(64 * 1024)
        if not chunk:
            if path_bytes is not None and len(buffer):
                yield path_bytes, bytes(buffer)
            elif path_bytes is not None or buffer:
                raise SearchError(
                    f"ripgrep returned a malformed candidate record for {client}"
                )
            return
        buffer.extend(chunk)
        cursor = 0
        while True:
            if path_bytes is None:
                separator = buffer.find(b"\0", cursor)
                if separator < 0:
                    break
                path_bytes = bytes(buffer[cursor:separator])
                cursor = separator + 1
            line_end = buffer.find(b"\n", cursor)
            if line_end < 0:
                break
            raw_record = bytes(buffer[cursor:line_end])
            cursor = line_end + 1
            yield path_bytes, raw_record
            path_bytes = None
        if cursor:
            del buffer[:cursor]


def _active_query_terms(
    counts: Counter[str], terms: tuple[str, ...], cap: int,
) -> tuple[str, ...]:
    return tuple(term for term in terms if counts.get(term, 0) < cap)


def _search_process_worker_count() -> int:
    main_module = sys.modules.get("__main__")
    main_file = getattr(main_module, "__file__", None)
    if not main_file or not Path(main_file).is_file():
        return 1
    main_path = Path(main_file).resolve()
    module_entrypoint = Path(__file__).with_name("cli_harness.py").resolve()
    console_entrypoint = (
        Path(sys.argv[0]).name == "nyxloom-harness"
        and main_path.is_file()
    )
    if main_path != module_entrypoint and not console_entrypoint:
        return 1
    process_cpu_count = getattr(os, "process_cpu_count", None)
    available = process_cpu_count() if callable(process_cpu_count) else os.cpu_count()
    return min(_RIPGREP_MAX_WORKERS, max(1, available or 1))


def _count_ripgrep_batch(
    records: Sequence[tuple[str, bytes]],
    initial_counts: dict[str, Counter[str]],
    terms: tuple[str, ...],
    term_match: str,
    cap: int,
) -> dict[str, Counter[str]]:
    """Count a bounded batch, returning capped increments by source file."""
    counts = {
        path: Counter(initial_counts.get(path, Counter()))
        for path, _raw_record in records
    }
    for path, raw_record in records:
        path_counts = counts[path]
        active_terms = _active_query_terms(path_counts, terms, cap)
        if not active_terms:
            continue
        if (
            len(active_terms) != len(terms)
            and not _raw_bytes_may_match(raw_record, active_terms, term_match)
        ):
            continue
        try:
            record = json.loads(raw_record.decode("utf-8", errors="replace"))
        except json.JSONDecodeError:
            continue
        _add_text(
            path_counts,
            record,
            active_terms,
            term_match,
            term_frequency_cap=cap,
        )

    increments: dict[str, Counter[str]] = {}
    for path, final_counts in counts.items():
        baseline = initial_counts.get(path, Counter())
        delta = Counter({
            term: final_counts.get(term, 0) - baseline.get(term, 0)
            for term in terms
            if final_counts.get(term, 0) > baseline.get(term, 0)
        })
        if delta:
            increments[path] = delta
    return increments


def _merge_capped_count_increments(
    counts: dict[str, Counter[str]],
    increments: dict[str, Counter[str]],
    cap: int,
) -> None:
    for source, delta in increments.items():
        target = counts.setdefault(source, Counter())
        for term, amount in delta.items():
            target[term] = min(cap, target.get(term, 0) + amount)


def _collect_ripgrep_batch(
    pending: set[Future],
    counts: dict[str, Counter[str]],
    cap: int,
) -> None:
    done, _ = wait(pending, return_when=FIRST_COMPLETED)
    for future in done:
        pending.remove(future)
        _merge_capped_count_increments(counts, future.result(), cap)


def _submit_ripgrep_batch(
    executor: ProcessPoolExecutor,
    pending: set[Future],
    records: list[tuple[str, bytes]],
    counts: dict[str, Counter[str]],
    terms: tuple[str, ...],
    term_match: str,
    cap: int,
    worker_count: int,
) -> None:
    if not records:
        return
    initial = {
        source: Counter(counts.get(source, Counter()))
        for source, _raw_record in records
    }
    pending.add(executor.submit(
        _count_ripgrep_batch,
        tuple(records),
        initial,
        terms,
        term_match,
        cap,
    ))
    records.clear()
    while len(pending) >= worker_count * _RIPGREP_MAX_PENDING_PER_WORKER:
        _collect_ripgrep_batch(pending, counts, cap)


def _ripgrep_jsonl_counts(
    paths: Sequence[Path],
    terms: tuple[str, ...],
    term_match: str,
    *,
    client: str,
    progress: Progress | None = None,
) -> dict[str, Counter[str]] | None:
    """Stream query candidate records from ripgrep when it is available.

    Ripgrep's native scanner searches all transcripts in parallel. It emits
    only candidate JSONL records; Python still parses JSON and applies the
    authoritative word/field semantics. Return None when the optional binary
    is unavailable or query terms require Unicode-aware Python filtering.
    """
    binary = shutil.which("rg")
    pattern = _ripgrep_pattern(terms, term_match)
    if binary is None or pattern is None:
        return None
    counts: dict[str, Counter[str]] = {}
    candidates_seen = 0
    frequency_cap = _TERM_FREQUENCY_CAP
    parallel_workers = _search_process_worker_count()
    executor: ProcessPoolExecutor | None = None
    pending: set[Future] = set()
    queued_records: list[tuple[str, bytes]] = []
    queued_bytes = 0
    completed = False
    try:
        for batch in _ripgrep_path_batches(paths):
            _report(
                progress,
                f"Searching {client} transcripts with ripgrep ({len(batch):,} files)",
            )
            with tempfile.TemporaryFile() as stderr_file:
                try:
                    process = subprocess.Popen(
                        [
                            binary,
                            "--no-config",
                            "--no-ignore",
                            "--hidden",
                            "--text",
                            "--null",
                            "--with-filename",
                            "--no-heading",
                            "--no-line-number",
                            "--regexp",
                            pattern,
                            "--",
                            *(str(path) for path in batch),
                        ],
                        stdout=subprocess.PIPE,
                        stderr=stderr_file,
                    )
                except OSError as exc:
                    raise SearchError(
                        f"could not start ripgrep for {client} session search: {exc}"
                    ) from exc
                assert process.stdout is not None
                process_completed = False
                try:
                    for raw_path, raw_record in _ripgrep_records(
                        process.stdout, progress=progress, client=client,
                    ):
                        path_text = os.fsdecode(raw_path)
                        candidates_seen += 1
                        if progress is not None and candidates_seen % 4096 == 0:
                            _report(
                                progress,
                                f"Checking {client} transcripts: {candidates_seen:,} candidate records",
                                candidates_seen,
                                None,
                            )
                        session_counts = counts.get(path_text, Counter())
                        active_terms = _active_query_terms(
                            session_counts, terms, frequency_cap,
                        )
                        if not active_terms:
                            continue
                        if (
                            len(active_terms) != len(terms)
                            and not _raw_bytes_may_match(
                                raw_record, active_terms, term_match,
                            )
                        ):
                            continue
                        if (
                            executor is None
                            and (
                                parallel_workers <= 1
                                or candidates_seen < _RIPGREP_PARALLEL_AFTER_CANDIDATES
                            )
                        ):
                            _merge_capped_count_increments(
                                counts,
                                _count_ripgrep_batch(
                                    ((path_text, raw_record),),
                                    {path_text: Counter(counts.get(path_text, Counter()))},
                                    terms,
                                    term_match,
                                    frequency_cap,
                                ),
                                frequency_cap,
                            )
                            continue
                        if executor is None:
                            executor = ProcessPoolExecutor(max_workers=parallel_workers)
                            _report(
                                progress,
                                f"Counting {client} candidates with {parallel_workers} workers",
                            )
                        record_size = len(raw_path) + len(raw_record)
                        if record_size > _RIPGREP_WORKER_RECORD_BYTE_LIMIT:
                            if queued_records:
                                _submit_ripgrep_batch(
                                    executor,
                                    pending,
                                    queued_records,
                                    counts,
                                    terms,
                                    term_match,
                                    frequency_cap,
                                    parallel_workers,
                                )
                                queued_bytes = 0
                            _merge_capped_count_increments(
                                counts,
                                _count_ripgrep_batch(
                                    ((path_text, raw_record),),
                                    {path_text: Counter(counts.get(path_text, Counter()))},
                                    terms,
                                    term_match,
                                    frequency_cap,
                                ),
                                frequency_cap,
                            )
                            continue
                        if queued_records and (
                            len(queued_records) >= _RIPGREP_BATCH_RECORD_LIMIT
                            or queued_bytes + record_size > _RIPGREP_BATCH_BYTE_LIMIT
                        ):
                            _submit_ripgrep_batch(
                                executor,
                                pending,
                                queued_records,
                                counts,
                                terms,
                                term_match,
                                frequency_cap,
                                parallel_workers,
                            )
                            queued_bytes = 0
                        queued_records.append((path_text, raw_record))
                        queued_bytes += record_size
                        if (
                            len(queued_records) >= _RIPGREP_BATCH_RECORD_LIMIT
                            or queued_bytes >= _RIPGREP_BATCH_BYTE_LIMIT
                        ):
                            _submit_ripgrep_batch(
                                executor,
                                pending,
                                queued_records,
                                counts,
                                terms,
                                term_match,
                                frequency_cap,
                                parallel_workers,
                            )
                            queued_bytes = 0
                    process_completed = True
                finally:
                    process.stdout.close()
                    if not process_completed and process.poll() is None:
                        process.terminate()
                    return_code = process.wait()
                if return_code not in (0, 1):
                    stderr_file.seek(0)
                    details = stderr_file.read().decode("utf-8", errors="replace").strip()
                    detail_text = f": {details}" if details else ""
                    raise SearchError(
                        f"ripgrep could not search {client} session transcripts "
                        f"(exit {return_code}){detail_text}"
                    )
        if executor is not None:
            if queued_records:
                _submit_ripgrep_batch(
                    executor,
                    pending,
                    queued_records,
                    counts,
                    terms,
                    term_match,
                    frequency_cap,
                    parallel_workers,
                )
            while pending:
                _collect_ripgrep_batch(pending, counts, frequency_cap)
        completed = True
    finally:
        if executor is not None:
            executor.shutdown(wait=True, cancel_futures=not completed)
    return counts


def _add_text(
    counts: Counter[str],
    value: Any,
    terms: tuple[str, ...] | None = None,
    term_match: str = "exact",
    *,
    term_frequency_cap: int | None = None,
) -> None:
    if terms is not None and term_frequency_cap is None:
        term_frequency_cap = _TERM_FREQUENCY_CAP
    query_pattern = (
        _query_text_pattern(terms, term_match)
        if terms is not None
        else None
    )
    for text in _text_values(value):
        if terms is None:
            counts.update(token.casefold() for token in _WORD.findall(text))
            continue
        if query_pattern is not None and text.isascii():
            if term_frequency_cap is not None:
                active_terms = tuple(
                    term for term in terms
                    if counts.get(term, 0) < term_frequency_cap
                )
                cursor = 0
                while active_terms:
                    active_pattern = _query_text_pattern(active_terms, term_match)
                    if active_pattern is None:
                        break
                    restart = False
                    for match in active_pattern.finditer(text, cursor):
                        token = match.group().casefold()
                        if term_match == "prefix":
                            for term in active_terms:
                                if token.startswith(term):
                                    counts[term] += 1
                        else:
                            counts[token] += 1
                        remaining_terms = tuple(
                            term for term in active_terms
                            if counts.get(term, 0) < term_frequency_cap
                        )
                        if remaining_terms != active_terms:
                            active_terms = remaining_terms
                            cursor = match.end()
                            restart = True
                            break
                    if not restart:
                        break
                continue
            for match in query_pattern.finditer(text):
                token = match.group().casefold()
                if term_match == "prefix":
                    for term in terms:
                        if token.startswith(term):
                            counts[term] += 1
                else:
                    counts[token] += 1
            continue
        active_terms = (
            tuple(
                term for term in terms
                if counts.get(term, 0) < term_frequency_cap
            )
            if term_frequency_cap is not None
            else terms
        )
        if not active_terms:
            continue
        for match in _WORD.finditer(text):
            token = match.group().casefold()
            for term in active_terms:
                if token == term or (term_match == "prefix" and token.startswith(term)):
                    counts[term] += 1
            if term_frequency_cap is not None:
                active_terms = tuple(
                    term for term in active_terms
                    if counts.get(term, 0) < term_frequency_cap
                )
                if not active_terms:
                    break


def _jsonl_content(
    path: Path,
    terms: tuple[str, ...] | None = None,
    *,
    term_match: str = "exact",
    progress: Progress | None = None,
    client: str | None = None,
    index: int | None = None,
    total: int | None = None,
) -> tuple[Counter[str], str | None]:
    counts: Counter[str] = Counter()
    last_activity: str | None = None
    if terms is not None:
        filtered_counts = _filtered_jsonl_counts(
            path,
            terms,
            term_match,
            progress=progress,
            client=client,
            index=index,
            total=total,
        )
        if filtered_counts is not None:
            last_activity = (
                _jsonl_last_activity(
                    path,
                    progress=progress,
                    client=client,
                    index=index,
                    total=total,
                )
                if filtered_counts
                else None
            )
            return filtered_counts, last_activity
    try:
        with path.open("rb") as source:
            for line_number, raw_line in enumerate(source, 1):
                if (
                    terms is None
                    or (
                        (active_terms := _active_query_terms(
                            counts, terms, _TERM_FREQUENCY_CAP,
                        ))
                        and _raw_bytes_may_match(raw_line, active_terms, term_match)
                    )
                ):
                    line = raw_line.decode("utf-8", errors="replace")
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        record = None
                    if record is not None:
                        _add_text(counts, record, terms, term_match)
                        if (
                            isinstance(record, dict)
                            and isinstance(record.get("timestamp"), str)
                        ):
                            last_activity = record["timestamp"]
                if progress is not None and line_number % 4096 == 0:
                    _report(
                        progress,
                        f"Scanning {client or 'session'} transcript {path.name}: "
                        f"{line_number:,} records",
                        index,
                        total,
                    )
    except OSError as exc:
        raise SearchError(f"could not read session transcript {path}: {exc}") from exc
    if terms is not None and counts:
        last_activity = _jsonl_last_activity(
            path,
            progress=progress,
            client=client,
            index=index,
            total=total,
        )
    return counts, last_activity


def _jsonl_last_activity(
    path: Path,
    *,
    progress: Progress | None = None,
    client: str | None = None,
    index: int | None = None,
    total: int | None = None,
) -> str | None:
    """Read the newest source-order root timestamp after a query match."""
    chunk_size = _ACTIVITY_CHUNK_SIZE
    progress_step = _PROGRESS_BYTE_STEP
    try:
        with path.open("rb") as source:
            source.seek(0, 2)
            file_size = source.tell()
            position = file_size
            # Chunks are retained as pieces until a line boundary is found.
            # Appending a growing carry buffer to every earlier chunk makes a
            # single huge final JSONL record quadratic in copied bytes.
            carry_parts: list[bytes] = []
            last_progress = 0
            while position > 0:
                read_size = min(chunk_size, position)
                position -= read_size
                source.seek(position)
                block = source.read(read_size)
                lines = block.split(b"\n")
                if len(lines) == 1:
                    carry_parts.append(block)
                else:
                    # The final segment in this chunk joins any pieces read
                    # from later chunks. Join those pieces once, at the line
                    # boundary, then continue toward older complete records.
                    raw_line = lines[-1] + b"".join(reversed(carry_parts))
                    carry_parts = [lines[0]] if lines[0] else []
                    candidates = [raw_line, *reversed(lines[1:-1])]
                    for raw_line in candidates:
                        if not raw_line:
                            continue
                        if b'"timestamp"' not in raw_line and b"\\u" not in raw_line:
                            continue
                        line = raw_line.decode("utf-8", errors="replace")
                        try:
                            record = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if isinstance(record, dict) and isinstance(record.get("timestamp"), str):
                            return record["timestamp"]
                scanned = file_size - position
                if progress is not None and scanned - last_progress >= progress_step:
                    _report(
                        progress,
                        f"Reading {client or 'session'} activity from {path.name}",
                        scanned,
                        file_size,
                    )
                    last_progress = scanned
            if carry_parts:
                raw_line = b"".join(reversed(carry_parts))
                if b'"timestamp"' in raw_line or b"\\u" in raw_line:
                    line = raw_line.decode("utf-8", errors="replace")
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        return None
                    if isinstance(record, dict) and isinstance(record.get("timestamp"), str):
                        return record["timestamp"]
    except OSError as exc:
        raise SearchError(f"could not read session activity from {path}: {exc}") from exc
    return None


def _opencode_term_counts(
    store: Path,
    terms: tuple[str, ...] | None = None,
    *,
    term_match: str = "exact",
    progress: Progress | None = None,
) -> dict[str, Counter[str]]:
    database = opencode._db_path(store)
    if database is None:
        raise SearchError(f"not an OpenCode session store: {store}")

    counts_by_session: dict[str, Counter[str]] = {}
    try:
        connection = sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True)
        with closing(connection):
            rows = connection.execute(
                "SELECT session_id, data FROM message "
                "UNION ALL "
                "SELECT m.session_id, p.data FROM part AS p "
                "JOIN message AS m ON m.id = p.message_id "
            )
            for row_index, (session_id, raw_data) in enumerate(rows, 1):
                if progress is not None and row_index == 1:
                    _report(progress, f"Searching OpenCode messages and parts in {store}")
                if progress is not None and row_index % 5000 == 0:
                    _report(progress, f"Searching OpenCode messages and parts: {row_index:,} records")
                raw_text = raw_data if isinstance(raw_data, str) else ""
                session_counts = counts_by_session.get(session_id, Counter())
                active_terms = (
                    _active_query_terms(
                        session_counts, terms, _TERM_FREQUENCY_CAP,
                    )
                    if terms is not None
                    else None
                )
                if terms is not None and not active_terms:
                    continue
                if (
                    terms is not None
                    and active_terms is not None
                    and raw_text
                    and not _raw_may_match(raw_text, active_terms, term_match)
                ):
                    continue
                try:
                    record = json.loads(raw_data)
                except (TypeError, json.JSONDecodeError):
                    continue
                _add_text(
                    session_counts,
                    record,
                    active_terms,
                    term_match,
                )
                if session_counts:
                    counts_by_session[session_id] = session_counts
    except sqlite3.Error as exc:
        raise SearchError(f"could not search OpenCode store {store}: {exc}") from exc
    return counts_by_session


def _documents(
    client: str,
    terms: tuple[str, ...] | None = None,
    *,
    term_match: str = "exact",
    source_roots: tuple[Path, ...] | None = None,
    progress: Progress | None = None,
    require_compatible_roots: bool = False,
) -> list[_Document]:
    found = _store_sessions(
        client,
        source_roots=source_roots,
        progress=progress,
        require_compatible_roots=require_compatible_roots,
    )
    docs: list[_Document] = []
    seen: set[tuple[str, tuple[int, int] | str]] = set()
    unique_sessions: list[tuple[_DiscoveredSession, tuple[int, int] | str]] = []
    files_by_identity: dict[tuple[int, int] | str, Path] = {}
    path_identities: dict[str, tuple[int, int] | str] = {}
    for session in found:
        source = Path(session.source)
        identity = _path_identity(source) if client != "opencode" else None
        file_identity: tuple[int, int] | str = (
            identity if identity is not None else session.source
        )
        key = (session.session_id, file_identity)
        if key in seen:
            continue
        seen.add(key)
        unique_sessions.append((session, file_identity))
        if client != "opencode":
            files_by_identity.setdefault(file_identity, source)
            path_identities[str(source)] = file_identity

    opencode_counts: dict[str, dict[str, Counter[str]]] = {}
    if client == "opencode":
        for store in sorted({session.source for session in found}):
            opencode_counts[store] = _opencode_term_counts(
                Path(store), terms, term_match=term_match, progress=progress,
            )
    rg_counts: dict[str, Counter[str]] | None = None
    counts_by_identity: dict[tuple[int, int] | str, Counter[str]] = {}
    if client != "opencode" and terms is not None:
        rg_counts = _ripgrep_jsonl_counts(
            tuple(files_by_identity.values()),
            terms,
            term_match,
            client=client,
            progress=progress,
        )
        if rg_counts is not None:
            for path_text, counts in rg_counts.items():
                file_identity = path_identities.get(path_text)
                if file_identity is None:
                    identity = _path_identity(Path(path_text))
                    if identity is None:
                        raise SearchError(
                            f"ripgrep returned a transcript that disappeared during search: {path_text}"
                        )
                    file_identity = identity
                if file_identity not in files_by_identity:
                    raise SearchError(
                        f"ripgrep returned an unexpected {client} transcript: {path_text}"
                    )
                counts_by_identity[file_identity] = counts

    activity_by_identity: dict[tuple[int, int] | str, str | None] = {}
    for index, (session, file_identity) in enumerate(unique_sessions, 1):
        source = Path(session.source)
        if progress is not None and (index == 1 or index % 25 == 0):
            _report(
                progress,
                f"Reading {client} transcript {index:,}/{len(unique_sessions):,}: {source.name}",
                index,
                len(unique_sessions),
            )
        if client == "opencode":
            counts = opencode_counts[session.source].get(session.session_id, Counter())
            last_activity = session.last_activity
        elif rg_counts is not None:
            counts = counts_by_identity.get(file_identity, Counter())
            if file_identity not in activity_by_identity:
                activity_by_identity[file_identity] = (
                    _jsonl_last_activity(
                        source,
                        progress=progress,
                        client=client,
                        index=index,
                        total=len(unique_sessions),
                    )
                    if counts
                    else None
                )
            last_activity = activity_by_identity[file_identity]
        else:
            counts, last_activity = _jsonl_content(
                source,
                terms,
                term_match=term_match,
                progress=progress,
                client=client,
                index=index,
                total=len(found),
            )
        docs.append(_Document(
            client=client,
            session_id=session.session_id,
            source=session.source,
            last_activity=last_activity,
            term_counts=counts,
        ))
    return docs


def search_sessions(
    query: str,
    *,
    client: str | Sequence[str] | None = None,
    sort_by: str = "best",
    source_roots: Sequence[str | Path] | None = None,
    word_match: str = "any",
    term_match: str = "exact",
    progress: Progress | None = None,
) -> list[SearchResult]:
    """Find sessions containing the requested query words and rank matches.

    Word matching is case-insensitive and punctuation-delimited. Best order
    first favors the number of distinct query words found, then a TF-IDF-like
    score that gives more weight to rarer words and modestly rewards repeats.
    Date order sorts by recorded last activity, newest first, with relevance
    and stable identifiers breaking ties. ``word_match`` selects any/all query
    words, and ``term_match`` selects exact word matches or token prefixes.
    """
    terms = _terms(query)
    if client is None:
        selected = CLIENTS
    elif isinstance(client, str):
        selected = (client,)
    else:
        selected = tuple(dict.fromkeys(client))
    unknown_clients = [name for name in selected if name not in CLIENTS]
    if unknown_clients:
        raise SearchError(
            f"unknown client {unknown_clients[0]!r}; expected one of: {', '.join(CLIENTS)}"
        )
    if sort_by not in SORT_ORDERS:
        raise SearchError(f"unknown sort order {sort_by!r}; expected one of: {', '.join(SORT_ORDERS)}")
    if word_match not in WORD_MATCHES:
        raise SearchError(
            f"unknown word match mode {word_match!r}; expected one of: {', '.join(WORD_MATCHES)}"
        )
    if term_match not in TERM_MATCHES:
        raise SearchError(
            f"unknown term match mode {term_match!r}; expected one of: {', '.join(TERM_MATCHES)}"
        )
    roots = _source_roots(source_roots)

    documents = [
        doc
        for name in selected
        for doc in _documents(
            name,
            terms,
            term_match=term_match,
            source_roots=roots,
            progress=progress,
            require_compatible_roots=client is not None,
        )
    ]
    document_frequency = Counter(
        term
        for doc in documents
        for term in terms
        if doc.term_counts.get(term, 0) > 0
    )
    total_documents = len(documents)
    results: list[SearchResult] = []
    for doc in documents:
        matched = tuple(term for term in terms if doc.term_counts.get(term, 0) > 0)
        if not matched or (word_match == "all" and len(matched) != len(terms)):
            continue
        score = 0.0
        for term in matched:
            doc_freq = document_frequency[term]
            inverse_frequency = math.log(
                1.0 + (total_documents - doc_freq + 0.5) / (doc_freq + 0.5)
            )
            score += inverse_frequency * (1.0 + math.log(doc.term_counts[term]))
        results.append(SearchResult(
            client=doc.client,
            session_id=doc.session_id,
            source=doc.source,
            last_activity=doc.last_activity,
            matched_terms=matched,
            score=score,
        ))

    results.sort(key=lambda result: (result.client, result.session_id, result.source))
    if sort_by == "date":
        results.sort(key=lambda result: result.score, reverse=True)
        results.sort(key=lambda result: result.last_activity or "", reverse=True)
    else:
        results.sort(key=lambda result: result.last_activity or "", reverse=True)
        results.sort(key=lambda result: result.score, reverse=True)
        results.sort(key=lambda result: len(result.matched_terms), reverse=True)
    return results
