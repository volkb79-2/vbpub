"""Small, mechanical helpers for reading Claude Code tool_use / tool_result
blocks: is a result a FAILURE, is it the harness's synthetic stop/denial, and
how is a call rendered as one truncated line. Shared by the adapter
(claude_code.py: `--tool-calls`, `--tool-errors`, STOP records), the ledger
(ledger.py: external-effect outcome) and stopstate.py (the Stop state
section) so the three can never disagree about what "failed" or "stopped"
means.

Everything here is a raw JSON field read or a regex over already-present raw
text -- zero LLM calls, the same mechanical-core guarantee as the rest of
this package. No redaction happens here by operator ruling (2026-10-06): the
extract only summarizes what is already in the transcript.
"""

from __future__ import annotations

import json
import re
from typing import Any

# The harness's own wording when a tool call is cut off. The transcript
# renders BOTH a user stop and a controller TaskStop this way (E-020: the
# agent believes the user declined in either case); only the sibling
# `.meta.json` `stoppedByUser` field tells them apart.
DENIAL_PREFIX = "The user doesn't want to proceed with this tool use"
INTERRUPT_PREFIX = "[Request interrupted by user"

_EXIT_RE = re.compile(r"^(?:Error:\s*)?Exit code\s+(\d+)")

_FILE_TOOLS = {"Read", "Edit", "Write", "NotebookEdit"}


def result_text(block: dict[str, Any]) -> str:
    """The plain text of one tool_result block (a string, or a list of
    text blocks, or anything else JSON-dumped)."""
    content = block.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            b.get("text", "") for b in content
            if isinstance(b, dict) and b.get("type") == "text"
        ]
        if parts:
            return "\n".join(parts)
    if content is None:
        return ""
    return json.dumps(content)


def is_denial(text: str, rec: dict[str, Any] | None = None) -> bool:
    """The harness's synthetic "tool use rejected/interrupted" result."""
    if rec is not None and rec.get("toolDenialKind"):
        return True
    return text.lstrip().startswith(DENIAL_PREFIX)


def is_interrupt_text(text: str) -> bool:
    """The harness's synthetic user-text record that follows a rejected call
    (`[Request interrupted by user for tool use]`)."""
    return text.strip().startswith(INTERRUPT_PREFIX)


def is_failed(block: dict[str, Any], rec: dict[str, Any] | None = None) -> bool:
    """A tool_result that reports failure: `is_error`, an interrupted
    result, a `<tool_use_error>` body, or text reading `Exit code N` with
    N != 0."""
    if block.get("is_error") is True:
        return True
    text = result_text(block)
    m = _EXIT_RE.match(text)
    if m and int(m.group(1)) != 0:
        return True
    if "<tool_use_error>" in text:
        return True
    tur = rec.get("toolUseResult") if rec is not None else None
    if isinstance(tur, dict) and tur.get("interrupted") is True:
        return True
    return False


def one_line(text: str, limit: int) -> str:
    """Whitespace-collapsed, truncated to `limit` chars with an ASCII
    ellipsis when cut."""
    flat = " ".join(str(text).split())
    if limit > 0 and len(flat) > limit:
        return flat[: max(limit - 3, 0)] + "..."
    return flat


def tool_intent(tool_input: Any) -> str:
    """The call's OWN description/intent field (Bash `description`, or an
    `intent` key), normalised to one line, <= 240 chars; "" when absent."""
    if not isinstance(tool_input, dict):
        return ""
    raw = tool_input.get("description") or tool_input.get("intent")
    if isinstance(raw, str):
        return " ".join(raw.split())[:240]
    return ""


def summarize_call(name: str, tool_input: Any, limit: int = 160) -> str:
    """The call itself as one truncated line, never the result. Bash ->
    `$ <command>`; file tools -> the path; Grep/Glob -> the pattern; anything
    else -> the first string field, or compact JSON."""
    if not isinstance(tool_input, dict):
        return one_line(tool_input if isinstance(tool_input, str) else "", limit)
    if name == "Bash" or isinstance(tool_input.get("command"), str):
        return one_line("$ " + str(tool_input.get("command", "")), limit)
    if name in _FILE_TOOLS or "file_path" in tool_input:
        return one_line(str(tool_input.get("file_path", "")), limit)
    for key in ("pattern", "query", "url", "prompt", "description"):
        value = tool_input.get(key)
        if isinstance(value, str) and value:
            return one_line(f"{key}={value}", limit)
    try:
        return one_line(json.dumps(tool_input, sort_keys=True), limit)
    except (TypeError, ValueError):
        return ""
