"""`--prose-only` rendering (the `watch` preset): operator messages and
assistant prose, one timestamped block each, nothing else.

Dropped on purpose (operator decision 2026-10-06): tool calls, tool results,
Q&A interview blocks, compaction/lifecycle markers, interrupt STOP markers,
gap notes and the cursor comments. The adapters already keep harness
reminders, notifications and task-notifications out of OPERATOR_TEXT, so what
reaches this module is only what the operator typed and what the assistant
said.

Two output forms:

* text: `[HH:MM:SS] OPERATOR: ...` / `[HH:MM:SS] assistant prose`; with colour
  on (the repo's `--color/--no-color`, default on only for a TTY and when
  NO_COLOR is unset) the timestamp is dim, the OPERATOR label and text are
  bold cyan, the assistant text is untouched.
* `--jsonl`: one object per line, `{"ts", "role": "operator"|"assistant",
  "text", "agent"?}` (agent only for a subagent transcript), for an editor
  extension. `ts` is the source ISO-8601 UTC timestamp.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path

from . import render
from .events import EventKind, NormalizedEvent

PROSE_KINDS = (EventKind.OPERATOR_TEXT, EventKind.ASSISTANT_TEXT)

_DIM = "\x1b[2m"
_BOLD_CYAN = "\x1b[1;36m"
_RESET = "\x1b[0m"
_AGENT_FILE_RE = re.compile(r"^agent-([0-9A-Za-z]+)\.jsonl$")


def role_of(ev: NormalizedEvent) -> str | None:
    if ev.kind is EventKind.OPERATOR_TEXT:
        return "operator"
    if ev.kind is EventKind.ASSISTANT_TEXT:
        return "assistant"
    return None


def agent_id(path: Path | str) -> str | None:
    """The agent id of a subagent transcript (`agent-<id>.jsonl`), else None."""
    match = _AGENT_FILE_RE.match(Path(path).name)
    return match.group(1) if match else None


class WatchFormatter:
    """Stateful because `--timestamps gaps` needs the previous event's time."""

    def __init__(
        self,
        *,
        jsonl: bool = False,
        color: bool = False,
        agent: str | None = None,
        timestamps: str = "all",
        show_timestamps: str = "pre",
        timestamp_format: str = "[%H:%M:%S]",
        gap_minutes: int = 5,
        block_render: Callable[[str], str] | None = None,
    ):
        self._jsonl = jsonl
        self._color = color
        self._agent = agent
        self._timestamps = timestamps
        self._show = show_timestamps
        self._format = timestamp_format
        self._gap = gap_minutes
        self._block_render = block_render
        self._prev: str | None = None

    def format(self, ev: NormalizedEvent) -> str | None:
        """The finished output for one event (newline-terminated), or None
        when the event is not operator/assistant prose."""
        role = role_of(ev)
        if role is None:
            return None
        if self._jsonl:
            row = {"ts": ev.timestamp, "role": role, "text": ev.text}
            if self._agent:
                row["agent"] = self._agent
            return json.dumps(row, ensure_ascii=False) + "\n"
        stamp = render._stamp_mode(ev, self._prev, self._timestamps, self._show, self._gap)
        self._prev = ev.timestamp
        text = self._block_render(ev.text) if self._block_render is not None else ev.text
        label = "OPERATOR: " if role == "operator" else ""
        when = None if stamp == "none" else render._format_timestamp(ev.timestamp, self._format)
        if self._color:
            if role == "operator":
                text = f"{_BOLD_CYAN}{label}{text}{_RESET}"
                label = ""
            if when:
                when = f"{_DIM}{when}{_RESET}"
        head = f"{when} " if when else ""
        return f"{head}{label}{text}\n\n"

    def format_all(self, events: list[NormalizedEvent]) -> str:
        return "".join(chunk for ev in events if (chunk := self.format(ev)) is not None)
