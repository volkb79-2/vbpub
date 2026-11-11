"""OpenAI Codex CLI ``codex exec --json`` event streams.

This is deliberately a separate adapter from ``codex.py``: a rollout file is
Codex's local session record, while ``--json`` is the controller-facing event
stream for one non-interactive run.  The stream has no ``session_meta`` record;
its first event is ``thread.started`` and its event vocabulary uses dotted
top-level names (``item.started``, ``item.completed``, ``turn.completed``) and
lower-case item types.

Verified against the real controller stream preserved as the repository-relative
fixture ``tests/fixtures/stall/review-rg-flake.exec.jsonl``.  The stream's complete
``item`` and ``turn`` records are the stop-state source; the rollout remains the
richer durable conversation source.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..config import ExtractConfig
from ..events import EventKind, NormalizedEvent
from .. import toolresult

name = "codex-exec"

_SNIFF_SCAN_LINES = 10


def _record_type(rec: dict[str, Any]) -> str:
    value = rec.get("type")
    return value if isinstance(value, str) else ""


def sniff(path: Path) -> bool:
    """Recognize the controller stream by its first structural event."""
    try:
        with path.open("r", errors="ignore") as handle:
            for index, line in enumerate(handle):
                if index >= _SNIFF_SCAN_LINES:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(rec, dict):
                    continue
                if _record_type(rec) == "thread.started" and isinstance(rec.get("thread_id"), str):
                    return True
                if _record_type(rec) in {"item.started", "item.completed"}:
                    item = rec.get("item")
                    if isinstance(item, dict) and isinstance(item.get("type"), str):
                        return True
    except OSError:
        return False
    return False


def list_sessions(path: Path) -> list[str]:
    return [str(path)]


def _item_text(item: dict[str, Any]) -> str:
    value = item.get("text")
    if isinstance(value, str):
        return value
    content = item.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            part.get("text", "") for part in content
            if isinstance(part, dict) and isinstance(part.get("text"), str)
        )
    return ""


def _command(item: dict[str, Any]) -> str:
    value = item.get("command")
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return " ".join(str(part) for part in value)
    return ""


def parse(path: Path, session_id: str, config: ExtractConfig) -> list[NormalizedEvent]:
    """Parse the controller stream into the shared prose event model."""
    records: list[tuple[int, dict[str, Any]]] = []
    with path.open("r", errors="ignore") as handle:
        for index, line in enumerate(handle):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(rec, dict) and _record_type(rec) in {
                "thread.started", "item.started", "item.completed", "turn.completed",
                "turn.failed", "turn.started", "error",
            }:
                records.append((index, rec))

    markers = [str(index) for index, _rec in records]
    since_index = None
    if config.since_marker is not None:
        since_index = next(
            (index for index, rec in enumerate(records) if str(index) == config.since_marker),
            None,
        )
        if since_index is None:
            raise ValueError(
                f"--since marker {config.since_marker!r} not found as a source marker in {path}"
            )
    until_index = None
    if config.until_marker is not None:
        until_index = next(
            (index for index, rec in enumerate(records) if str(index) == config.until_marker),
            None,
        )
        if until_index is None:
            raise ValueError(
                f"--until marker {config.until_marker!r} not found as a source marker in {path}"
            )

    events: list[NormalizedEvent] = []
    for seq, (index, rec) in enumerate(records):
        if since_index is not None and seq <= since_index:
            continue
        if until_index is not None and seq > until_index:
            continue
        if _record_type(rec) != "item.completed":
            continue
        item = rec.get("item")
        if not isinstance(item, dict):
            continue
        itype = item.get("type")
        marker = markers[seq]
        if itype == "agent_message":
            text = _item_text(item)
            if text:
                events.append(NormalizedEvent(
                    seq, marker, "", EventKind.ASSISTANT_TEXT, text,
                ))
        elif itype == "reasoning" and config.include_thinking:
            text = _item_text(item)
            if text:
                events.append(NormalizedEvent(
                    seq, marker, "", EventKind.THINKING, text,
                ))
        elif itype == "command_execution" and config.show_tool_calls:
            command = _command(item)
            label = "[tool call: command_execution]"
            if command:
                label += f" $ {toolresult.one_line(command, 160)}"
            events.append(NormalizedEvent(
                seq, marker, "", EventKind.TOOL_CALL, label,
            ))
    return events
