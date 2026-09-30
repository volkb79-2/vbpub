"""Rank sessions by the words in their locally stored transcripts."""

from __future__ import annotations

import json
import math
import re
import sqlite3
import stat
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

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
_WORD = re.compile(r"[^\W_]+", re.UNICODE)
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


def _store_sessions(client: str) -> list[_DiscoveredSession]:
    if client == "claude":
        return _claude_sessions(_claude_projects_root())
    if client == "codex":
        try:
            roots = tuple(_codex_sessions_roots())
        except LocateError as exc:
            raise SearchError(str(exc)) from exc
        return _codex_sessions(roots)

    seen_stores: set[Path] = set()
    found: list[_DiscoveredSession] = []
    for store in _opencode_db_candidates():
        try:
            resolved = store.resolve(strict=True)
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise SearchError(
                f"session search is indeterminate; could not inspect {store}: "
                f"{type(exc).__name__}: {exc}"
            ) from exc
        if resolved in seen_stores:
            continue
        seen_stores.add(resolved)
        try:
            nodes = opencode.list_agents(resolved)
        except (OSError, sqlite3.Error, ValueError) as exc:
            raise SearchError(f"could not search OpenCode store {resolved}: {exc}") from exc
        found.extend(
            _DiscoveredSession("opencode", node.id, node.path, node.last_ts)
            for node in nodes
        )
    return found


def _walk_files(root: Path) -> Iterator[Path]:
    """Walk a session root without hiding unreadable directories or entries."""
    pending = [root]
    while pending:
        directory = pending.pop()
        try:
            entries = _scan_directory(directory)
        except LocateError as exc:
            raise SearchError(str(exc)) from exc
        for path in entries:
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


def _claude_sessions(root: Path) -> list[_DiscoveredSession]:
    if not _root_is_available(root):
        return []
    found = []
    for path in _walk_files(root):
        if path.suffix != ".jsonl" or not _is_claude_transcript(path):
            continue
        if path.parent.name == "subagents" and path.stem.startswith("agent-"):
            session_id = path.stem.removeprefix("agent-")
        else:
            session_id = path.stem
        found.append(_DiscoveredSession("claude", session_id, str(path)))
    return found


def _is_claude_transcript(path: Path) -> bool:
    """Use the adapter's record discriminator while preserving read errors."""
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as source:
            for index, line in enumerate(source):
                if index >= 50:
                    break
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if (
                    isinstance(record, dict)
                    and "sessionId" in record
                    and "parentUuid" in record
                ):
                    return True
    except OSError as exc:
        raise SearchError(f"could not inspect Claude Code session {path}: {exc}") from exc
    return False


def _codex_sessions(roots: tuple[Path, ...]) -> list[_DiscoveredSession]:
    """List Codex rollouts in one filesystem pass.

    ``sessions.list_agents(directory)`` resolves each rollout's whole family
    by rescanning the sessions tree. Search needs every individual session,
    so calling it once per root thread makes discovery quadratic in the
    number of rollouts. Reuse its verified session metadata discriminator
    while walking each root only once; the transcript pass below gathers
    content and source timestamps together.
    """
    found: list[_DiscoveredSession] = []
    visited_roots: set[Path] = set()
    for root in roots:
        if root in visited_roots or not _root_is_available(root):
            continue
        visited_roots.add(root)
        for path in _walk_files(root):
            if not path.name.startswith("rollout-") or not path.name.endswith(".jsonl"):
                continue
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


def _add_text(counts: Counter[str], value: Any) -> None:
    for text in _text_values(value):
        counts.update(token.casefold() for token in _WORD.findall(text))


def _jsonl_content(path: Path) -> tuple[Counter[str], str | None]:
    counts: Counter[str] = Counter()
    last_activity: str | None = None
    try:
        with path.open("r", encoding="utf-8", errors="replace") as source:
            for line in source:
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                _add_text(counts, record)
                if (
                    isinstance(record, dict)
                    and isinstance(record.get("timestamp"), str)
                ):
                    last_activity = record["timestamp"]
    except OSError as exc:
        raise SearchError(f"could not read session transcript {path}: {exc}") from exc
    return counts, last_activity


def _opencode_term_counts(store: Path) -> dict[str, Counter[str]]:
    database = opencode._db_path(store)
    if database is None:
        raise SearchError(f"not an OpenCode session store: {store}")

    counts_by_session: dict[str, Counter[str]] = {}
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True)
        rows = connection.execute(
            "SELECT session_id, data FROM message "
            "UNION ALL "
            "SELECT m.session_id, p.data FROM part AS p "
            "JOIN message AS m ON m.id = p.message_id "
        )
        for session_id, raw_data in rows:
            try:
                record = json.loads(raw_data)
            except (TypeError, json.JSONDecodeError):
                continue
            _add_text(counts_by_session.setdefault(session_id, Counter()), record)
    except sqlite3.Error as exc:
        raise SearchError(f"could not search OpenCode store {store}: {exc}") from exc
    finally:
        if connection is not None:
            connection.close()
    return counts_by_session


def _documents(client: str) -> list[_Document]:
    found = _store_sessions(client)
    docs: list[_Document] = []
    seen: set[tuple[str, str]] = set()
    opencode_counts: dict[str, dict[str, Counter[str]]] = {}
    if client == "opencode":
        for store in sorted({session.source for session in found}):
            opencode_counts[store] = _opencode_term_counts(Path(store))
    for session in found:
        key = (session.session_id, session.source)
        if key in seen:
            continue
        seen.add(key)
        if client == "opencode":
            counts = opencode_counts[session.source].get(session.session_id, Counter())
            last_activity = session.last_activity
        else:
            counts, last_activity = _jsonl_content(Path(session.source))
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
    client: str | None = None,
    sort_by: str = "best",
) -> list[SearchResult]:
    """Find sessions containing any query word and return them in ranked order.

    Word matching is case-insensitive and punctuation-delimited. Best order
    first favors the number of distinct query words found, then a TF-IDF-like
    score that gives more weight to rarer words and modestly rewards repeats.
    Date order sorts by recorded last activity, newest first, with relevance
    and stable identifiers breaking ties.
    """
    terms = _terms(query)
    if client is not None and client not in CLIENTS:
        raise SearchError(f"unknown client {client!r}; expected one of: {', '.join(CLIENTS)}")
    if sort_by not in SORT_ORDERS:
        raise SearchError(f"unknown sort order {sort_by!r}; expected one of: {', '.join(SORT_ORDERS)}")

    selected = (client,) if client is not None else CLIENTS
    documents = [doc for name in selected for doc in _documents(name)]
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
        if not matched:
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
