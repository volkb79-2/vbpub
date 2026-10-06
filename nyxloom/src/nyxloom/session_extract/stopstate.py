"""The "Stop state" of a Claude Code transcript: WHY it ended, what the agent
last said, and which call was in flight. Built for the successor workflow
(`nyxloom extract --stop-state` / `--successor-brief`, skill nyxloom-successor)
where a stopped Agent-tool subagent's transcript must be turned into a
faithful hand-over.

Facts, all read mechanically (E-020, design-context-lifecycle-experiments.md):
  - The sibling `.meta.json` of a subagent transcript carries
    `"stoppedByUser": true` when the OPERATOR stopped it (the controller can
    then not resume it: SendMessage is refused by policy).
  - The transcript itself renders a user stop AND a controller TaskStop the
    same way: an assistant tool_use, a tool_result "The user doesn't want to
    proceed with this tool use ..." (is_error, toolDenialKind), then a user
    text "[Request interrupted by user for tool use]". Neither is an operator
    message. So the transcript alone can say "interrupted", never "by whom".
  - An agent killed mid-call by a crash/restart may end on a tool_use with no
    result record at all.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import toolresult

_LAST_TEXT_LIMIT = 800


def meta_path(path: Path) -> Path:
    """`agent-X.jsonl` -> `agent-X.meta.json` (sibling)."""
    return path.with_suffix(".meta.json")


def read_meta(path: Path) -> dict[str, Any] | None:
    """The sibling `.meta.json` as a dict, or None when absent/unreadable."""
    mp = meta_path(Path(path))
    try:
        data = json.loads(mp.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


@dataclass
class StopState:
    cause: str
    stopped_by_user: bool | None  # None = no .meta.json / field absent
    last_text: str
    last_text_ts: str
    in_flight: str | None  # "Bash: $ cmd" of the last call that has no real result
    in_flight_outcome: str | None

    def render(self) -> str:
        lines = ["[stop state]", f"cause: {self.cause}"]
        if self.last_text:
            ts = f" {self.last_text_ts[11:19]}" if len(self.last_text_ts) >= 19 else ""
            lines.append(f"last assistant text{ts}: {self.last_text}")
        else:
            lines.append("last assistant text: (none)")
        if self.in_flight:
            lines.append(f"in-flight call: {self.in_flight} -- {self.in_flight_outcome}")
        else:
            lines.append("in-flight call: none (the last call, if any, has a normal result)")
        return "\n".join(lines)


def build_stop_state(path: Path) -> StopState:
    path = Path(path)
    meta = read_meta(path)
    stopped = meta.get("stoppedByUser") if meta is not None else None
    stopped_by_user = bool(stopped) if meta is not None and "stoppedByUser" in meta else None

    calls: dict[str, tuple[str, str]] = {}  # id -> (name, summary), in order
    order: list[str] = []
    result_kind: dict[str, str] = {}  # id -> "ok" | "failed" | "denied"
    last_text, last_text_ts = "", ""
    tail_interrupt = False  # a synthetic interrupt text after the last assistant record
    tail_kind = "none"  # kind of the last conversation record: assistant_text/assistant_call/user_result/interrupt

    with path.open("r", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            rtype = rec.get("type")
            content = (rec.get("message") or {}).get("content")
            if rtype == "assistant":
                tail_interrupt = False
                has_call = False
                for block in content if isinstance(content, list) else []:
                    if not isinstance(block, dict):
                        continue
                    if block.get("type") == "text" and (block.get("text") or "").strip():
                        last_text = " ".join(block["text"].split())[:_LAST_TEXT_LIMIT]
                        last_text_ts = rec.get("timestamp", "")
                        tail_kind = "assistant_text"
                    elif block.get("type") == "tool_use":
                        has_call = True
                        tid = block.get("id") or f"anon{len(order)}"
                        calls[tid] = (
                            str(block.get("name") or "tool"),
                            toolresult.summarize_call(str(block.get("name") or ""), block.get("input")),
                        )
                        order.append(tid)
                if has_call:
                    tail_kind = "assistant_call"
            elif rtype == "user":
                if isinstance(content, list):
                    for block in content:
                        if not isinstance(block, dict):
                            continue
                        if block.get("type") == "tool_result":
                            text = toolresult.result_text(block)
                            tid = block.get("tool_use_id")
                            if toolresult.is_denial(text, rec):
                                result_kind[tid] = "denied"
                            elif toolresult.is_failed(block, rec):
                                result_kind[tid] = "failed"
                            else:
                                result_kind[tid] = "ok"
                            tail_kind = "user_result"
                        elif block.get("type") == "text":
                            if toolresult.is_interrupt_text(block.get("text", "")):
                                tail_interrupt = True
                                tail_kind = "interrupt"
                            else:
                                tail_kind = "user_text"
                elif isinstance(content, str):
                    if toolresult.is_interrupt_text(content):
                        tail_interrupt = True
                        tail_kind = "interrupt"
                    else:
                        tail_kind = "user_text"

    in_flight = None
    in_flight_outcome = None
    if order:
        last_id = order[-1]
        name, summary = calls[last_id]
        kind = result_kind.get(last_id)
        if kind is None:
            in_flight = f"{name}: {summary}" if summary else name
            in_flight_outcome = "no result recorded (the transcript ends before the call returned)"
        elif kind == "denied":
            in_flight = f"{name}: {summary}" if summary else name
            in_flight_outcome = "rejected by the harness (stop/interrupt): it did NOT complete"

    denied_tail = tail_interrupt or (in_flight is not None and result_kind.get(order[-1]) == "denied")
    if stopped_by_user is True:
        cause = (
            "stopped by the USER (.meta.json stoppedByUser=true) -- the harness refuses "
            "SendMessage to this agent; recover with a fresh successor"
        )
    elif denied_tail:
        cause = (
            "interrupted: the transcript ends with the harness's synthetic stop/rejection "
            "records, but "
            + ("`.meta.json` does not set stoppedByUser" if meta is not None else "no `.meta.json` is available")
            + " (a controller TaskStop renders identically; try SendMessage before building a successor)"
        )
    elif in_flight is not None:
        cause = (
            "ended with a tool call in flight and no recorded result (crash, kill, restart or "
            "lost session)"
        )
    elif tail_kind == "assistant_text":
        cause = "ended normally: the last record is the assistant's own text"
    else:
        cause = f"ended without a clear stop signal (last record kind: {tail_kind})"
    return StopState(cause, stopped_by_user, last_text, last_text_ts, in_flight, in_flight_outcome)
