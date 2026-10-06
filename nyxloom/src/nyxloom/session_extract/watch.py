"""`--prose-only` rendering (the `watch` preset): operator messages and
assistant prose, one timestamped block each, nothing else.

Dropped on purpose (operator decision 2026-10-06): tool calls, tool results,
compaction/lifecycle markers, interrupt STOP markers, gap notes and the cursor
comments. The adapters already keep harness reminders, notifications and
task-notifications out of OPERATOR_TEXT, so what reaches this module is only
what the operator typed and what the assistant said.

Interviews (AskUserQuestion) ARE operator content (controller ruling
2026-10-06) and are kept compactly: each question is ONE line of assistant
text (its header and option list are dropped), and the operator's selected
answer, with any free-text notes, is an OPERATOR line. The question comes from
the prompt event and the answer from the answer event, so a live follow shows
the question the moment it is asked and nothing is printed twice.

Two output forms:

* text: `[HH:MM:SS] OPERATOR: ...` / `[HH:MM:SS] assistant prose`; with colour
  on (the repo's `--color/--no-color`, default on only for a TTY and when
  NO_COLOR is unset) the timestamp is dim, the OPERATOR label and text are
  bold cyan, the assistant text is untouched.
* `--jsonl`: one object per line, `{"v": 1, "ts", "role":
  "operator"|"assistant", "text", "agent"?}` (agent only for a subagent
  transcript), for an editor extension. This is a STABLE, VERSIONED contract:
  `v` is `JSONL_VERSION`; keys are only ever added under a new `v`. `ts` is
  the source ISO-8601 UTC timestamp.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path

from . import render
from .events import EventKind, NormalizedEvent

PROSE_KINDS = (EventKind.OPERATOR_TEXT, EventKind.ASSISTANT_TEXT, EventKind.QA_PAIR)

# Version of the `--jsonl` line format (the `"v"` key of every line).
JSONL_VERSION = 1

_DIM = "\x1b[2m"
_BOLD_CYAN = "\x1b[1;36m"
_RESET = "\x1b[0m"
_AGENT_FILE_RE = re.compile(r"^agent-([0-9A-Za-z]+)\.jsonl$")
_INTERVIEW_PREFIX = "INTERVIEW: "
_ANSWER_RE = re.compile(r"^OPERATOR: (.*?)(?=\n\nINTERVIEW: |\Z)", re.DOTALL | re.MULTILINE)


def items(ev: NormalizedEvent) -> list[tuple[str, str]]:
    """The (role, text) rows one event contributes (empty = not prose)."""
    if ev.kind is EventKind.OPERATOR_TEXT:
        return [("operator", ev.text)]
    if ev.kind is EventKind.ASSISTANT_TEXT:
        return [("assistant", ev.text)]
    if ev.kind is not EventKind.QA_PAIR:
        return []
    answers = _ANSWER_RE.findall(ev.text)
    if answers:
        return [("operator", answer.strip()) for answer in answers]
    if ev.text.startswith(_INTERVIEW_PREFIX):
        return [("assistant", ev.text.splitlines()[0][len(_INTERVIEW_PREFIX):])]
    return [("operator", ev.text)]


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
        rows = items(ev)
        if not rows:
            return None
        if self._jsonl:
            return "".join(self._jsonl_row(ev, role, text) for role, text in rows)
        stamp = render._stamp_mode(ev, self._prev, self._timestamps, self._show, self._gap)
        self._prev = ev.timestamp
        out = []
        for index, (role, text) in enumerate(rows):
            shown = stamp if index == 0 else "none"
            out.append(self._text_row(ev, role, text, shown))
        return "".join(out)

    def _jsonl_row(self, ev: NormalizedEvent, role: str, text: str) -> str:
        row: dict[str, object] = {"v": JSONL_VERSION, "ts": ev.timestamp, "role": role, "text": text}
        if self._agent:
            row["agent"] = self._agent
        return json.dumps(row, ensure_ascii=False) + "\n"

    def _text_row(self, ev: NormalizedEvent, role: str, text: str, stamp: str) -> str:
        if self._block_render is not None:
            text = self._block_render(text)
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
