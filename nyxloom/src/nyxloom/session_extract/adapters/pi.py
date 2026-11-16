"""pi (``@earendil-works/pi-coding-agent``) session records.

Two on-disk shapes carry the same conversation model, and this one adapter
reads both:

``stored session`` -- ``~/.pi/agent/sessions/<encoded-cwd>/<time>_<uuid>.jsonl``
    pi's own durable session file. Record types verified against the real
    files in that tree: ``session`` (first; ``version``, ``id``, ``cwd``),
    ``model_change``, ``thinking_level_change``, ``message`` (the message
    object nested under ``message``), ``compaction`` (a ``summary`` string),
    ``context_edit``. A ``resume`` appends further records to the SAME file,
    so one file can hold several agent cycles but exactly one ``session``
    header.

``run stream`` -- what ``pi --mode json`` writes for one run (the controller
    keeps these under its own ``codex/runs/*.jsonl``). Record types:
    ``session``, ``agent_start``, ``turn_start``, ``message_start``,
    ``message_update`` (streaming deltas), ``message_end`` (the authoritative
    message), ``tool_execution_start``/``..._update``/``..._end``,
    ``turn_end``, ``compaction_start``/``compaction_end``, ``agent_end``,
    ``agent_settled``.

What is read, and why:

* ``user`` text content -> ``OPERATOR_TEXT``. pi's ``system`` message carries
  the harness preamble/rules/skills, which is harness framing, never operator
  intent (a real stored session records it as ``role: "system"`` with
  ``sections``).
* ``assistant`` content blocks -> one event per block in content order:
  ``text`` -> ``ASSISTANT_TEXT``, ``thinking`` -> ``THINKING`` (only under
  ``--include-thinking``, like every other adapter), ``toolCall`` ->
  ``TOOL_CALL`` when the configured tool-call mode renders it.
* ``toolResult`` messages -> a FAILED result (``isError``) renders as
  ``[tool error: <tool>] <call> => <result>`` under ``--tool-errors show``.
  A successful result is never rendered: results are the noise this whole
  tool exists to discard, and the failure path is the one kept because it
  explains why the session changed course.
* ``compaction`` / ``compaction_end`` -> ``LIFECYCLE_MARKER`` with
  ``boundary_type="compaction"``; the summary text is included only under
  ``--show-compaction-content`` (the rule Claude Code's markers follow).
* A ``stopReason: "error"`` assistant message also yields an
  ``[API ERROR: ...]`` assistant-channel event, so the shared
  ``--show-api-errors`` rule hides a failed model call by default, as it
  does for Claude Code.
* A Bash ``toolCall`` naming a ``--agent-launch-pattern`` launcher contributes
  ``AGENT-OUT``/``AGENT-IN`` through ``agentcomms.launch_events`` when
  ``--agent-comms`` is not ``off``. pi has no Agent/Task/SendMessage tool, so
  a controller launcher run inside pi is the only agent-communication shape
  that exists here.

Deliberately NOT read:

* ``message_start`` / ``message_update`` of a run stream. ``message_end``
  carries the finished message and the earlier records are mostly cumulative
  partial snapshots (one real 292k-line run stream was 99.2% ``message_update``
  records), so reading them would emit the same message hundreds of times.
* ``tool_execution_*``. The call is in the assistant message and its outcome
  is in the ``toolResult`` message; these records repeat that pair. stopstate
  reads them instead, where the pair IS the in-flight evidence.
* ``turn_end`` / ``agent_end`` (they repeat the turn's own message) and
  ``agent_settled`` (a scheduling tick, not conversation content).

Markers are absolute, pre-slice ``line<N>`` positions of the record that
produced the event -- the same stable fallback ``reasonix`` uses, so a marker
from an earlier ``--since`` hop still resolves to the same physical record on
a later run.

Stop-state (``stopstate.py``) is where pi's own ``stopReason`` vocabulary
becomes a cause: ``length`` is CONTEXT-FULL, ``error`` is ERROR, an
``assistant`` message with ``stopReason`` ``stop`` and no prose is
EMPTY-COMPLETION, and a session ending on ``toolUse`` is TOOL-USE.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .. import agentcomms, toolresult
from ..config import ExtractConfig
from ..events import EventKind, NormalizedEvent

name = "pi"

_SNIFF_SCAN_LINES = 10
COMPACTION_LABEL = "[compact boundary]"
COMPACTION_SUMMARY_LABEL = "[compact summary]"
#: Record types that carry a conversation message (``message`` in a stored
#: session, ``message_end`` in a run stream).
MESSAGE_RECORD_TYPES = ("message", "message_end")
#: Record types that mark a compaction boundary in either shape.
COMPACTION_RECORD_TYPES = ("compaction", "compaction_start", "compaction_end")


def record_type(rec: dict[str, Any]) -> str:
    value = rec.get("type")
    return value if isinstance(value, str) else ""


def message_of(rec: dict[str, Any]) -> dict[str, Any] | None:
    """The message object a conversation record carries, if any."""
    if record_type(rec) not in MESSAGE_RECORD_TYPES:
        return None
    message = rec.get("message")
    return message if isinstance(message, dict) else None


def record_timestamp(rec: dict[str, Any], message: dict[str, Any]) -> str:
    """ISO-8601 UTC for one record.

    A run stream's message carries an integer epoch-ms ``timestamp``; a stored
    session's message carries none and its wrapper carries an ISO string; pi
    writes neither on some synthetic records, for which the normalized empty
    timestamp is the honest representation rather than an invented one.
    """
    value = message.get("timestamp")
    if isinstance(value, bool):
        return ""
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value / 1000, tz=UTC).isoformat()
    if isinstance(value, str) and value:
        return value
    wrapper_value = rec.get("timestamp")
    return wrapper_value if isinstance(wrapper_value, str) else ""


#: pi's tool names are lowercase (``bash``/``read``/``edit``/``write``/``grep``
#: /``find``/``ls``), while the shared toolresult vocabulary is Claude Code's
#: capitalized set. Mapping them here keeps the compression stage
#: (``--edit-calls``/``--read-calls``) and the ledger's own typology working
#: across both harnesses instead of silently classifying every pi call as
#: "other".
_TOOL_NAME_ALIASES: dict[str, str] = {
    "edit": "Edit", "write": "Write", "read": "Read", "grep": "Grep",
    "find": "Glob", "ls": "LS",
}


def shared_tool_name(tool_name: str) -> str:
    """pi's tool name as the shared capitalized vocabulary spells it."""
    return _TOOL_NAME_ALIASES.get(tool_name, tool_name)


def blocks(message: dict[str, Any]) -> list[dict[str, Any]]:
    content = message.get("content")
    if not isinstance(content, list):
        return []
    return [block for block in content if isinstance(block, dict)]


def block_text(blocks_: list[dict[str, Any]], kind: str, key: str) -> str:
    return "\n".join(
        block[key] for block in blocks_
        if block.get("type") == kind and isinstance(block.get(key), str) and block[key]
    )


def sniff(path: Path) -> bool:
    """Recognize pi's ``session`` header by its own content.

    Both shapes start with ``{"type": "session", "version": <int>, "id":
    <str>, "cwd": <str>}``. No other supported harness writes a record of
    that shape (Codex's nearest is ``session_meta`` with a ``payload``), so
    the content check -- not a file suffix -- is the discriminator.
    """
    try:
        with Path(path).open("r", errors="ignore") as handle:
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
                if not isinstance(rec, dict) or record_type(rec) != "session":
                    continue
                version = rec.get("version")
                if isinstance(version, bool) or not isinstance(version, int):
                    continue
                if isinstance(rec.get("id"), str) and isinstance(rec.get("cwd"), str):
                    return True
    except OSError:
        return False
    return False


def list_sessions(path: Path) -> list[str]:
    """pi stores one session per file, so the path is the whole session."""
    return [str(path)]


def compaction_summary(rec: dict[str, Any]) -> str:
    """The summary a compaction record carries, or "".

    A stored session's ``compaction`` record has ``summary``; a run stream's
    ``compaction_end`` carries it under ``result``.
    """
    summary = rec.get("summary")
    if isinstance(summary, str) and summary:
        return summary
    result = rec.get("result")
    if isinstance(result, dict):
        nested = result.get("summary")
        if isinstance(nested, str):
            return nested
    return ""


def compaction_events(
    rec: dict[str, Any], seq: int, marker: str, ts: str, config: ExtractConfig,
) -> list[NormalizedEvent]:
    """One compaction boundary as a kept LIFECYCLE_MARKER.

    ``compaction_start`` opens the boundary ``compaction_end`` closes and
    carries no summary, so the marker is emitted once, from the record that
    can describe it: one marker per compaction in either shape.
    """
    if record_type(rec) == "compaction_start":
        return []
    summary = compaction_summary(rec)
    if config.hide_compaction_content or not summary:
        text = COMPACTION_LABEL
    else:
        text = f"{COMPACTION_SUMMARY_LABEL} {summary}"
    event = NormalizedEvent(seq, marker, ts, EventKind.LIFECYCLE_MARKER, text)
    event.meta["boundary_type"] = "compaction"
    return [event]


def tool_label(tool_name: str, tool_input: Any, config: ExtractConfig) -> str | None:
    """One ``toolCall`` block -> its rendered text under ``--tool-calls``,
    or None when the mode renders nothing for it. Never includes the result;
    the mode vocabulary is shared with Claude Code because the option is."""
    mode = config.tool_call_mode
    if mode == "none":
        return None
    head = f"[tool call: {tool_name}]"
    intent = toolresult.tool_intent(tool_input, config.path_aliases)
    if mode == "label":
        return head
    if mode == "label-intent":
        return head + (f" {intent}" if intent else "")
    call = toolresult.summarize_call(
        tool_name, tool_input, strip_cd_prefix=config.strip_cd_prefix,
        aliases=config.path_aliases,
    )
    if mode == "intent":
        return f"{head} {intent}" if intent else None
    text = intent or call if mode == "intent-or-call" else call
    return f"{head} {text}" if text else head


def tool_call_event(
    seq: int, marker: str, ts: str, block: dict[str, Any], config: ExtractConfig,
) -> NormalizedEvent | None:
    tool_name = block.get("name")
    if not isinstance(tool_name, str) or not tool_name:
        return None
    arguments = block.get("arguments")
    label = tool_label(tool_name, arguments, config)
    if label is None:
        return None
    event = NormalizedEvent(seq, marker, ts, EventKind.TOOL_CALL, label)
    event.meta["tool"] = tool_name
    event.meta["tool_intent"] = toolresult.tool_intent(arguments, config.path_aliases)
    event.meta["tool_kind"] = toolresult.tool_kind(
        shared_tool_name(tool_name), arguments, config.effect_patterns,
        config.effect_scp_uploads,
    )
    if isinstance(arguments, dict):
        file_path = arguments.get("path") or arguments.get("file_path")
        if isinstance(file_path, str) and file_path:
            event.meta["tool_file"] = toolresult.apply_aliases(
                file_path, config.path_aliases,
            )
    return event


def tool_result_event(
    seq: int, marker: str, ts: str, message: dict[str, Any], config: ExtractConfig,
    calls: dict[str, str],
) -> NormalizedEvent | None:
    """A FAILED tool result, truncated, independent of the tool-call mode.

    The failed call comes first: an error line must say which call failed,
    and that call is the one the assistant message already carried.
    """
    if config.tool_errors != "show" or message.get("isError") is not True:
        return None
    tool_name = message.get("toolName")
    tool_name = tool_name if isinstance(tool_name, str) and tool_name else "tool"
    call_id = message.get("toolCallId")
    call = calls.get(call_id, "") if isinstance(call_id, str) else ""
    text = toolresult.result_text({"content": message.get("content")})
    error = toolresult.one_line(toolresult.apply_aliases(text, config.path_aliases), 240)
    label = f"[tool error: {tool_name}] {call} => {error}" if call else f"[tool error: {tool_name}] {error}"
    event = NormalizedEvent(seq, marker, ts, EventKind.TOOL_CALL, label)
    event.meta["tool_error"] = "true"
    return event


def launch_events(
    blocks_: list[dict[str, Any]], cwd: Any, config: ExtractConfig,
    seq: int, marker: str, ts: str,
) -> list[NormalizedEvent]:
    """AGENT-OUT/AGENT-IN for a Bash call that invokes a known launcher."""
    if config.agent_comms == "off":
        return []
    cwd_raw = cwd if isinstance(cwd, str) else None
    out: list[NormalizedEvent] = []
    for block in blocks_:
        if block.get("type") != "toolCall" or block.get("name") != "bash":
            continue
        arguments = block.get("arguments")
        command = arguments.get("command") if isinstance(arguments, dict) else None
        if isinstance(command, str):
            out.extend(
                agentcomms.launch_events(command, cwd_raw, config, seq, marker, ts),
            )
    return out


def message_events(
    rec: dict[str, Any], message: dict[str, Any], marker: str, config: ExtractConfig,
    calls: dict[str, str], seq: int,
) -> list[NormalizedEvent]:
    """The events one conversation message yields, in content order."""
    role = message.get("role")
    ts = record_timestamp(rec, message)
    blocks_ = blocks(message)
    if role == "user":
        text = block_text(blocks_, "text", "text")
        if not text:
            return []
        return [NormalizedEvent(seq, marker, ts, EventKind.OPERATOR_TEXT, text)]
    if role == "toolResult":
        event = tool_result_event(seq, marker, ts, message, config, calls)
        return [event] if event is not None else []
    if role != "assistant":
        return []
    events: list[NormalizedEvent] = []
    for block in blocks_:
        kind = block.get("type")
        if kind == "text":
            text = block.get("text")
            if isinstance(text, str) and text:
                events.append(
                    NormalizedEvent(seq, marker, ts, EventKind.ASSISTANT_TEXT, text)
                )
        elif kind == "thinking":
            thinking = block.get("thinking")
            if config.include_thinking and isinstance(thinking, str) and thinking:
                events.append(
                    NormalizedEvent(seq, marker, ts, EventKind.THINKING, thinking)
                )
        elif kind == "toolCall":
            call_id = block.get("id")
            if isinstance(call_id, str) and call_id:
                calls[call_id] = toolresult.summarize_call(
                    str(block.get("name") or ""), block.get("arguments"),
                    strip_cd_prefix=config.strip_cd_prefix, aliases=config.path_aliases,
                )
            event = tool_call_event(seq, marker, ts, block, config)
            if event is not None:
                events.append(event)
    events.extend(launch_events(blocks_, rec.get("cwd"), config, seq, marker, ts))
    if message.get("stopReason") == "error":
        # The assistant channel's own error record, so the shared selector's
        # --show-api-errors rule hides a failed model call by default here
        # exactly as it does for Claude Code.
        detail = message.get("errorMessage") or message.get("error") or ""
        events.append(NormalizedEvent(
            seq, marker, ts, EventKind.ASSISTANT_TEXT,
            f"[API ERROR: pi stopReason=error] {toolresult.one_line(str(detail), 240)}",
        ))
    return events


def records(path: Path) -> list[tuple[int, dict[str, Any]]]:
    """(absolute line index, record) for every parseable record.

    A malformed or truncated line (a run killed mid-write) is skipped rather
    than fatal: the intact records around it are still the session's content,
    and the file is appended to, never rewritten.
    """
    out: list[tuple[int, dict[str, Any]]] = []
    with Path(path).open("r", errors="replace") as handle:
        for index, line in enumerate(handle):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(rec, dict):
                out.append((index, rec))
    return out


def _resolve_bound(records_: list[tuple[int, dict[str, Any]]], marker: str | None, flag: str,
                   path: Path) -> int | None:
    if marker is None:
        return None
    found = next((index for index, _rec in records_ if f"line{index}" == marker), None)
    if found is None:
        raise ValueError(f"{flag} marker {marker!r} not found as a pi line marker in {path}")
    return found


def parse(path: Path, session_id: str, config: ExtractConfig) -> list[NormalizedEvent]:
    records_ = records(path)
    since_line = _resolve_bound(records_, config.since_marker, "--since", path)
    until_line = _resolve_bound(records_, config.until_marker, "--until", path)

    calls: dict[str, str] = {}
    events: list[NormalizedEvent] = []
    for index, rec in records_:
        if since_line is not None and index <= since_line:
            continue
        if until_line is not None and index > until_line:
            continue
        # `seq` is the record's ABSOLUTE, pre-slice line position and `marker`
        # spells that same position as a resolvable `line<N>` token, so a
        # marker minted by one --since hop resolves to the same physical
        # record on the next one.
        marker = f"line{index}"
        message = message_of(rec)
        if message is not None:
            events.extend(
                message_events(rec, message, marker, config, calls, index)
            )
        elif record_type(rec) in COMPACTION_RECORD_TYPES:
            events.extend(
                compaction_events(rec, index, marker, record_timestamp(rec, {}), config)
            )
    return agentcomms.dedupe_events(events)
