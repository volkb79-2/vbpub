"""Mechanical extraction of communication sent to and received from agents.

Claude Code represents outbound Agent/Task/SendMessage calls as ordinary
tool_use blocks. Inbound subagent completion arrives as a user-role
``<task-notification>`` payload. Controller launchers such as
``codex-impl.sh`` and ``opencode-impl.sh`` additionally write a sibling run
stream whose final assistant message is not part of the parent transcript.

This module owns those three representations so the adapter, follow mode and
tests apply one policy. It performs no model call and reads only fields and
files already referenced by the transcript.
"""

from __future__ import annotations

import hashlib
import html
import json
import os
import re
import shlex
from pathlib import Path
from typing import Any

from .config import ExtractConfig
from .events import EventKind, NormalizedEvent

AGENT_TOOLS = frozenset({"Agent", "Task", "SendMessage"})

_NOTIFICATION_RE = re.compile(
    r"<task-notification\b[^>]*>.*?</task-notification>",
    re.IGNORECASE | re.DOTALL,
)
_TAG_RE = re.compile(
    r"<(?!task-notification\b)([a-z][a-z0-9-]*)\b[^>]*>(.*?)</\1>",
    re.IGNORECASE | re.DOTALL,
)
_MONITOR_EXPIRED_RE = re.compile(r"^\s*monitor\s+expired\b", re.IGNORECASE)


def _bounded(text: str, mode: str, config: ExtractConfig) -> str:
    """Whitespace-collapse summaries and cap the complete rendered item."""
    limit = (
        config.agent_comms_summary_chars
        if mode == "summary"
        else config.agent_comms_item_max_chars
    )
    if mode == "summary":
        text = " ".join(text.split())
    return _cap_text(text, limit)


def _cap_text(text: str, limit: int) -> str:
    """Bound text to ``limit`` characters with an always-visible marker."""
    if len(text) <= limit:
        return text
    suffix = " [truncated]"
    if limit <= len(suffix):
        return text[: limit - 1].rstrip() + "…"
    return text[: limit - len(suffix)].rstrip() + suffix


def _bounded_item(head: str, body: str, config: ExtractConfig) -> str:
    """Cap the complete header and body, never just the message body."""
    limit = (
        config.agent_comms_summary_chars
        if config.agent_comms == "summary"
        else config.agent_comms_item_max_chars
    )
    if config.agent_comms == "summary":
        body = " ".join(body.split())
    text = f"{head}\n{body}" if body else head
    return _cap_text(text, limit)


def _dedupe_meta(event: NormalizedEvent, tag: str, payload: str) -> None:
    digest = hashlib.sha256((tag + "\0" + payload).encode("utf-8")).hexdigest()
    event.meta["agent_dedupe"] = digest


def agent_tool_events(
    name: str,
    tool_input: Any,
    config: ExtractConfig,
    seq: int,
    marker: str,
    timestamp: str,
) -> list[NormalizedEvent]:
    """The AGENT-OUT events for an Agent/Task/SendMessage tool input."""
    if config.agent_comms == "off" or name not in AGENT_TOOLS or not isinstance(tool_input, dict):
        return []
    body_parts: list[str] = []
    identity: list[str] = []
    if name == "Agent" or name == "Task":
        kind = tool_input.get("subagent_type") or tool_input.get("agent_type")
        description = tool_input.get("description")
        if isinstance(kind, str) and kind:
            identity.append(kind)
        if isinstance(description, str) and description:
            identity.append(description)
        for key in ("prompt", "message", "content"):
            value = tool_input.get(key)
            if isinstance(value, str) and value:
                body_parts.append(value)
                break
    else:
        for key in ("to", "recipient", "summary"):
            value = tool_input.get(key)
            if isinstance(value, str) and value:
                identity.append(f"{key}={value}")
        for key in ("message", "content", "text"):
            value = tool_input.get(key)
            if value is not None:
                body_parts.append(value if isinstance(value, str) else json.dumps(value, sort_keys=True))
                break
    if not body_parts:
        body_parts.append(json.dumps(tool_input, sort_keys=True))
    if config.agent_comms == "summary":
        identity = [" ".join(part.split()) for part in identity]
    head = f"AGENT-OUT: {name}"
    if identity:
        head += " [" + "; ".join(identity) + "]"
    raw_body = "\n".join(body_parts)
    text = _bounded_item(head, raw_body, config)
    event = NormalizedEvent(seq, marker, timestamp, EventKind.AGENT_OUT, text)
    event.meta["agent_tool"] = name
    _dedupe_meta(event, "out", f"{name}\0{identity!r}\0{raw_body}")
    return [event]


def _notification_payloads(text: str) -> list[dict[str, str]]:
    payloads: list[dict[str, str]] = []
    for match in _NOTIFICATION_RE.finditer(text):
        fields: dict[str, str] = {}
        for tag in _TAG_RE.finditer(match.group(0)):
            fields[tag.group(1).lower()] = html.unescape(tag.group(2)).strip()
        payloads.append(fields)
    return payloads


def is_monitor_expired_noise(payload: dict[str, str]) -> bool:
    """A completion with no result and only monitor-expiry wording is noise."""
    return not payload.get("result", "").strip() and bool(
        _MONITOR_EXPIRED_RE.match(payload.get("summary", ""))
    )


def notification_event(
    payload: dict[str, str],
    config: ExtractConfig,
    seq: int,
    marker: str,
    timestamp: str,
) -> NormalizedEvent | None:
    """One inbound notification as AGENT-IN, excluding pure expiry noise."""
    if is_monitor_expired_noise(payload):
        return None
    task = payload.get("task-id", "?")
    status = payload.get("status", "unknown")
    summary = payload.get("summary", "")
    result = payload.get("result", "")
    output_file = payload.get("output-file", "")
    head = f"AGENT-IN: task={task} status={status}"
    if output_file:
        head += f" output-file={output_file}"
    body = "\n".join(part for part in (
        f"summary: {summary}" if summary else "",
        f"result:\n{result}" if result else "",
    ) if part)
    text = _bounded_item(head, body, config)
    event = NormalizedEvent(
        seq, marker, timestamp, EventKind.AGENT_IN, text,
    )
    event.meta["agent_result_kind"] = "notification"
    event.meta["agent_task_id"] = task
    _dedupe_meta(event, "in", f"{task}\0{status}\0{summary}\0{result}")
    return event


def notification_events(
    text: str,
    config: ExtractConfig,
    seq: int,
    marker: str,
    timestamp: str,
) -> list[NormalizedEvent]:
    """All AGENT-IN events from a raw user message's notification envelopes."""
    if config.agent_comms == "off":
        return []
    out = [
        event
        for payload in _notification_payloads(text)
        if (event := notification_event(payload, config, seq, marker, timestamp)) is not None
    ]
    return out


def notification_free_text(text: str) -> str:
    """Remove notification envelopes while preserving unrelated operator text."""
    return _NOTIFICATION_RE.sub("", text).strip()


def _safe_cwd(raw: str | None) -> Path | None:
    if not isinstance(raw, str) or not raw:
        return None
    try:
        return Path(raw).resolve()
    except (OSError, RuntimeError):
        return None


def _resolve_path(token: str, cwd: Path | None) -> Path | None:
    if not token or token.startswith("-") or "$" in token:
        return None
    try:
        path = Path(os.path.expanduser(token))
    except (OSError, RuntimeError):
        return None
    if not path.is_absolute() and cwd is not None:
        path = cwd / path
    return path


def _effective_cwd(tokens: list[str], cwd: Path | None) -> Path | None:
    """Apply leading shell ``cd`` segments that scope a relative launcher."""
    effective = cwd
    for index, token in enumerate(tokens):
        if token != "cd" or index + 1 >= len(tokens):
            continue
        raw = tokens[index + 1]
        if raw.startswith("-") or "$" in raw:
            return None
        try:
            target = Path(os.path.expanduser(raw))
        except (OSError, RuntimeError):
            return None
        if not target.is_absolute() and effective is not None:
            target = effective / target
        try:
            effective = target.resolve()
        except (OSError, RuntimeError):
            return None
    return effective


def _launch_argv(
    command: str, patterns: tuple[str, ...],
) -> tuple[int, Path, list[str]] | None:
    try:
        tokens = shlex.split(command, posix=True)
    except ValueError:
        tokens = command.split()
    for index, token in enumerate(tokens):
        if any(pattern in token for pattern in patterns):
            launcher = Path(os.path.expanduser(token))
            return index, launcher, tokens[index + 1:]
    return None


def _final_agent_message(path: Path, config: ExtractConfig) -> str:
    last_path = path.with_suffix(".last.txt")
    try:
        if last_path.is_file():
            text = last_path.read_text(encoding="utf-8", errors="replace").strip()
            if text:
                return _bounded(text, config.agent_comms, config)
    except OSError:
        pass
    candidates = [path]
    if path.is_dir():
        candidates = sorted(path.glob("*.jsonl"))
    for candidate in candidates:
        if not candidate.is_file():
            continue
        last = ""
        try:
            with candidate.open("r", encoding="utf-8", errors="replace") as stream:
                for line in stream:
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    item = record.get("item") if isinstance(record, dict) else None
                    if isinstance(item, dict) and item.get("type") in ("agent_message", "AgentMessage"):
                        value = item.get("text") or item.get("content")
                        if isinstance(value, list):
                            value = "\n".join(
                                part.get("text", "") for part in value
                                if isinstance(part, dict) and isinstance(part.get("text"), str)
                            )
                        if isinstance(value, str) and value.strip():
                            last = value.strip()
                    text = record.get("text") if isinstance(record, dict) else None
                    if isinstance(text, str) and text.strip():
                        last = text.strip()
                    part = record.get("part") if isinstance(record, dict) else None
                    if isinstance(part, dict) and isinstance(part.get("text"), str) and part["text"].strip():
                        last = part["text"].strip()
        except OSError:
            continue
        if last:
            return _bounded(last, config.agent_comms, config)
    return ""


def launch_events(
    command: str,
    cwd_raw: str | None,
    config: ExtractConfig,
    seq: int,
    marker: str,
    timestamp: str,
) -> list[NormalizedEvent]:
    """AGENT-OUT brief/run association and AGENT-IN final run message."""
    if config.agent_comms == "off":
        return []
    found = _launch_argv(command, config.agent_launch_patterns)
    if found is None:
        return []
    launcher_index, launcher, args = found
    try:
        tokens = shlex.split(command, posix=True)
    except ValueError:
        tokens = command.split()
    cwd = _effective_cwd(tokens[:launcher_index], _safe_cwd(cwd_raw))
    brief_index = next(
        (index for index, token in enumerate(args) if token.endswith((".md", ".txt"))),
        None,
    )
    brief = _resolve_path(args[brief_index], cwd) if brief_index is not None else None
    brief_display = str(brief) if brief is not None else (
        args[brief_index] if brief_index is not None else "(brief path not found)"
    )
    run_index = None
    if (
        brief_index is not None
        and brief_index + 1 < len(args)
        and not args[brief_index + 1].startswith("-")
    ):
        run_index = brief_index + 1
    events: list[NormalizedEvent] = []
    out_text = _bounded_item(
        f"AGENT-OUT: Bash launch {launcher.name}",
        f"brief: {brief_display}",
        config,
    )
    out_event = NormalizedEvent(seq, marker, timestamp, EventKind.AGENT_OUT, out_text)
    out_event.meta["agent_launch"] = launcher.name
    out_event.meta["agent_brief"] = brief_display
    _dedupe_meta(out_event, "launch", out_text)
    events.append(out_event)
    if run_index is None:
        return events
    launcher_path = launcher
    if not launcher_path.is_absolute() and cwd is not None:
        launcher_path = cwd / launcher_path
    run_path = launcher_path.parent / "runs" / f"{args[run_index]}.jsonl"
    final = _final_agent_message(run_path, config)
    if final:
        in_text = _bounded_item(
            f"AGENT-IN: run={args[run_index]}",
            f"result:\n{final}",
            config,
        )
        in_event = NormalizedEvent(seq, marker, timestamp, EventKind.AGENT_IN, in_text)
        in_event.meta["agent_result_kind"] = "run_log"
        _dedupe_meta(in_event, "run", f"{args[run_index]}\0{final}")
        events.append(in_event)
    return events


def dedupe_events(events: list[NormalizedEvent]) -> list[NormalizedEvent]:
    """Drop byte-identical repeated communication, preserving source order."""
    seen: set[str] = set()
    out: list[NormalizedEvent] = []
    for event in events:
        key = event.meta.get("agent_dedupe")
        if key is not None:
            if key in seen:
                continue
            seen.add(key)
        out.append(event)
    return out
