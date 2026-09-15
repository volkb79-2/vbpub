"""Reasonix session adapter.

Reasonix stores its primary chat transcript as JSONL under
``~/.reasonix/projects/<escaped-cwd>/sessions/``.  The primary records are
chat objects with a ``role`` and (usually) a string ``content``.  Companion
``*.events.jsonl`` files contain replace-snapshot event records, not chat
records, and are deliberately rejected here.

This adapter only exposes content whose meaning is established by the
Reasonix record shape:

* user text -> ``OPERATOR_TEXT``;
* assistant text -> ``ASSISTANT_TEXT``;
* assistant ``reasoning_content`` -> ``THINKING`` when requested.

System messages, tool output, structured tool calls, empty records, and
malformed lines are not session prose.  Reasonix chat records do not carry a
timestamp, so the normalized timestamp is the documented empty value rather
than an invented filesystem or wall-clock timestamp.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..config import ExtractConfig
from ..events import EventKind, NormalizedEvent

name = "reasonix"

_CHAT_ROLES = {"system", "user", "assistant", "tool"}
_EVENT_SNAPSHOT_TYPES = {"replace", "append"}
_SNIFF_SCAN_LINES = 50


def is_chat_record(rec: dict) -> bool:
    """Whether ``rec`` belongs to the primary chat-record universe.

    The marker space includes system and tool records even though neither
    contributes normalized prose.  That keeps a marker tied to a stable
    source-record position and prevents ignored records from shifting a
    later ``line<N>`` marker.
    """
    return rec.get("role") in _CHAT_ROLES


def _is_event_snapshot(rec: object) -> bool:
    return isinstance(rec, dict) and rec.get("type") in _EVENT_SNAPSHOT_TYPES


def is_events_path(path: Path) -> bool:
    """Whether the path names Reasonix's non-primary event companion."""
    return Path(path).name.endswith(".events.jsonl")


def sniff(path: Path) -> bool:
    """Recognize a real primary Reasonix JSONL file by record content.

    This is intentionally not a suffix-only check.  In particular, event
    snapshots are rejected even though they also use a ``.jsonl`` suffix,
    and Claude/Codex records do not claim this adapter because they have no
    Reasonix chat ``role`` record.
    """
    if is_events_path(path):
        return False
    found_chat_record = False
    try:
        with Path(path).open("r", errors="ignore") as handle:
            for i, line in enumerate(handle):
                if i >= _SNIFF_SCAN_LINES:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if _is_event_snapshot(rec):
                    return False
                if isinstance(rec, dict) and is_chat_record(rec):
                    found_chat_record = True
    except OSError:
        return False
    return found_chat_record


def list_sessions(path: Path) -> list[str]:
    """Reasonix's one-file adapter contract: one primary file, one session."""
    return [str(path)]


def _load_records(path: Path) -> list[dict]:
    records: list[dict] = []
    if is_events_path(path):
        raise ValueError(
            f"{path} is a Reasonix events snapshot, not a primary session file"
        )
    try:
        with Path(path).open("r", errors="ignore") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if _is_event_snapshot(rec):
                    raise ValueError(
                        f"{path} is a Reasonix events snapshot, not a primary session file"
                    )
                if isinstance(rec, dict) and is_chat_record(rec):
                    records.append(rec)
    except OSError as exc:
        raise ValueError(f"could not read Reasonix session {path}: {exc}") from exc
    return records


def parse_record(
    rec: dict, seq: int, marker: str, config: ExtractConfig
) -> list[NormalizedEvent]:
    """Convert one primary chat record into zero, one, or two events."""
    role = rec.get("role")
    content = rec.get("content")
    events: list[NormalizedEvent] = []

    if role == "user" and isinstance(content, str) and content:
        events.append(
            NormalizedEvent(seq, marker, "", EventKind.OPERATOR_TEXT, content)
        )
    elif role == "assistant":
        if isinstance(content, str) and content:
            events.append(
                NormalizedEvent(seq, marker, "", EventKind.ASSISTANT_TEXT, content)
            )
        reasoning = rec.get("reasoning_content")
        if config.include_thinking and isinstance(reasoning, str) and reasoning:
            events.append(
                NormalizedEvent(seq, marker, "", EventKind.THINKING, reasoning)
            )

    return events


def parse(path: Path, session_id: str, config: ExtractConfig) -> list[NormalizedEvent]:
    """Parse a Reasonix primary file with stable pre-slice line markers."""
    records = _load_records(path)
    indexed = list(enumerate(records))

    if config.since_marker is not None:
        idx = next(
            (i for i, rec in indexed if f"line{i}" == config.since_marker),
            None,
        )
        if idx is None:
            raise ValueError(
                f"--since marker {config.since_marker!r} not found as a Reasonix line marker in {path}"
            )
        indexed = [(i, rec) for i, rec in indexed if i > idx]

    if config.until_marker is not None:
        idx = next(
            (i for i, _rec in indexed if f"line{i}" == config.until_marker),
            None,
        )
        if idx is None:
            raise ValueError(
                f"--until marker {config.until_marker!r} not found as a Reasonix line marker in {path}"
            )
        indexed = [(i, rec) for i, rec in indexed if i <= idx]

    events: list[NormalizedEvent] = []
    for seq, (absolute_index, rec) in enumerate(indexed):
        events.extend(parse_record(rec, seq, f"line{absolute_index}", config))
    return events
