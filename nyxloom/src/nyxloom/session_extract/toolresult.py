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

from . import shellcmd

# The harness's own wording when a tool call is cut off. The transcript
# renders BOTH a user stop and a controller TaskStop this way (E-020: the
# agent believes the user declined in either case); only the sibling
# `.meta.json` `stoppedByUser` field tells them apart.
DENIAL_PREFIX = "The user doesn't want to proceed with this tool use"
_INTERRUPT_RE = re.compile(r"\[Request interrupted by user(?: for tool use)?\]")

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
    (`[Request interrupted by user]` / `[Request interrupted by user for tool
    use]`). An EXACT match of the whole text: a real operator message that
    merely starts with (or quotes) that wording and goes on is operator
    intent and must stay OPERATOR."""
    return _INTERRUPT_RE.fullmatch(text.strip()) is not None


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
    if text.lstrip().startswith("<tool_use_error>"):
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


Aliases = tuple[tuple[str, str], ...]

EDIT_TOOLS = frozenset({"Edit", "Write", "MultiEdit", "NotebookEdit"})
READ_TOOLS = frozenset({"Read", "Grep", "Glob", "LS", "NotebookRead", "WebFetch", "WebSearch"})

_CD_RE = re.compile(r"""^\s*cd\s+(?:"[^"]*"|'[^']*'|\S+)\s*(?:&&|;)\s*""")
_CD_PASSES = 4
_INTENT_LINE_RE = re.compile(r"^\s*Intent:\s*(\S.*?)\s*$")


def strip_cd(command: str) -> str:
    """Drop a leading `cd X &&` / `cd X;` (repeated, bounded)."""
    for _ in range(_CD_PASSES):
        new = _CD_RE.sub("", command, count=1)
        if new == command:
            break
        command = new
    return command


def apply_aliases(text: str, aliases: Aliases) -> str:
    """Replace each known root path in `text` by its `$NAME` alias, longest
    path first, only at a path boundary."""
    for name, path in sorted(aliases, key=lambda a: len(a[1]), reverse=True):
        if not path:
            continue
        rx = re.compile(r"(?<![\w.\-])" + re.escape(path.rstrip("/")) + r"(?![\w.\-])")
        text = rx.sub("$" + name, text)
    return text


def paired_intent(text: str) -> tuple[str, str]:
    """If the LAST non-empty line of an assistant text block is
    `Intent: <what and why>`, return (that intent, the text before it); else
    ("", text). The Edit/Write intent convention (nyxloom-dispatch skill)."""
    lines = text.rstrip().split("\n")
    m = _INTENT_LINE_RE.match(lines[-1])
    if m is None:
        return "", text
    return " ".join(m.group(1).split()), "\n".join(lines[:-1]).strip()


def tool_intent(tool_input: Any, aliases: Aliases = ()) -> str:
    """The call's OWN description/intent field (Bash `description`, or an
    `intent` key), normalised to one line, aliased, <= 240 chars; "" when
    absent."""
    if not isinstance(tool_input, dict):
        return ""
    raw = tool_input.get("description") or tool_input.get("intent")
    if isinstance(raw, str):
        return apply_aliases(" ".join(raw.split()), aliases)[:240]
    return ""


def summarize_call(
    name: str, tool_input: Any, limit: int = 160,
    strip_cd_prefix: bool = False, aliases: Aliases = (),
) -> str:
    """The call itself as one truncated line, never the result. Bash ->
    `$ <command>`; file tools -> the path; Grep/Glob -> the pattern; anything
    else -> the first string field, or compact JSON. The cleanups
    (`strip_cd_prefix`, `aliases`) run BEFORE the truncation."""
    if not isinstance(tool_input, dict):
        return one_line(tool_input if isinstance(tool_input, str) else "", limit)
    if name == "Bash" or isinstance(tool_input.get("command"), str):
        command = str(tool_input.get("command", ""))
        if strip_cd_prefix:
            command = strip_cd(command)
        return one_line("$ " + apply_aliases(command, aliases), limit)
    if name in _FILE_TOOLS or "file_path" in tool_input:
        return one_line(apply_aliases(str(tool_input.get("file_path", "")), aliases), limit)
    for key in ("pattern", "query", "url", "prompt", "description"):
        value = tool_input.get(key)
        if isinstance(value, str) and value:
            return one_line(apply_aliases(f"{key}={value}", aliases), limit)
    try:
        return one_line(apply_aliases(json.dumps(tool_input, sort_keys=True), aliases), limit)
    except (TypeError, ValueError):
        return ""


def tool_kind(
    name: str, tool_input: Any, effect_patterns: tuple[str, ...], scp_uploads: bool = True,
) -> str:
    """"edit" | "effect" | "read" | "other" -- the class `--edit-calls`,
    `--read-calls` and `--effect-calls` act on."""
    if name in EDIT_TOOLS:
        return "edit"
    command = tool_input.get("command") if isinstance(tool_input, dict) else None
    if name == "Bash" and isinstance(command, str):
        if shellcmd.effect_segments(command, effect_patterns, scp_uploads):
            return "effect"
        return "read" if shellcmd.is_read_only(command) else "other"
    return "read" if name in READ_TOOLS else "other"
