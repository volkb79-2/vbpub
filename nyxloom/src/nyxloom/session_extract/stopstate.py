"""The "Stop state" of a session transcript: WHY it ended, what the agent
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


@dataclass
class CodexRunFacts:
    """Stop-relevant facts read uniformly from a Codex rollout or exec stream."""

    message_count: int
    command_count: int
    last_text: str
    last_text_ts: str
    in_flight: str | None
    terminal: str | None
    terminal_message: str


def _codex_text(item: dict[str, Any]) -> str:
    text = item.get("text")
    if isinstance(text, str):
        return text
    content = item.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            block.get("text", "") for block in content
            if isinstance(block, dict) and isinstance(block.get("text"), str)
        )
    return ""


def _codex_command(item: dict[str, Any]) -> str:
    command = item.get("command")
    if isinstance(command, str):
        return command
    if isinstance(command, list):
        return " ".join(str(part) for part in command)
    return ""


def _call_summary(item: dict[str, Any]) -> str:
    itype = str(item.get("type") or "tool")
    command = _codex_command(item)
    if command:
        return f"{itype}: $ {toolresult.one_line(command, 160)}"
    name = item.get("name")
    if isinstance(name, str) and name:
        return name
    return itype


def _response_call_summary(payload: dict[str, Any]) -> str:
    name = str(payload.get("name") or payload.get("type") or "tool")
    arguments = payload.get("arguments") or payload.get("input")
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except json.JSONDecodeError:
            arguments = None
    if isinstance(arguments, dict):
        command = arguments.get("cmd") or arguments.get("command")
        if isinstance(command, list):
            command = " ".join(str(part) for part in command)
        if isinstance(command, str) and command:
            return f"{name}: $ {toolresult.one_line(command, 160)}"
        description = arguments.get("description") or arguments.get("intent")
        if isinstance(description, str) and description:
            return f"{name}: {toolresult.one_line(description, 160)}"
    return name


def read_codex_facts(path: Path) -> CodexRunFacts:
    """Read either real Codex JSONL schema into one stop-state fact set.

    ``codex exec --json`` records item lifecycle as top-level
    ``item.started``/``item.completed``. Rollouts record ``CommandExecution``
    completions in ``event_msg`` and paired function calls in
    ``response_item``. Tracking that pair makes an unfinished local-session
    call visible instead of treating the last prose message as complete.
    """
    open_calls: dict[str, str] = {}
    calls_in_order: list[str] = []
    completion_records = 0
    response_outputs = 0
    message_count = 0
    last_text = ""
    last_text_ts = ""
    terminal: str | None = None
    terminal_message = ""

    with Path(path).open("r", errors="ignore") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(rec, dict):
                continue
            rtype = rec.get("type")
            payload = rec.get("payload")
            payload = payload if isinstance(payload, dict) else {}
            item = rec.get("item")
            item = item if isinstance(item, dict) else {}

            if rtype == "item.started":
                call_id = item.get("id")
                if isinstance(call_id, str):
                    open_calls[call_id] = _call_summary(item)
                    calls_in_order.append(call_id)
            elif rtype == "item.completed":
                itype = item.get("type")
                call_id = item.get("id")
                if isinstance(call_id, str):
                    open_calls.pop(call_id, None)
                if itype == "agent_message":
                    text = _codex_text(item)
                    if text.strip():
                        message_count += 1
                        last_text = " ".join(text.split())[:_LAST_TEXT_LIMIT]
                        last_text_ts = ""
                elif itype == "command_execution":
                    completion_records += 1
            elif rtype == "turn.completed":
                terminal = "completed"
                terminal_message = ""
            elif rtype in {"turn.failed", "turn.aborted"}:
                terminal = "interrupted"
                error = rec.get("error")
                if isinstance(error, dict) and isinstance(error.get("message"), str):
                    terminal_message = error["message"]
            elif rtype == "error":
                terminal = "interrupted"
                message = rec.get("message")
                terminal_message = message if isinstance(message, str) else ""
            elif rtype == "event_msg":
                ptype = payload.get("type")
                pitem = payload.get("item")
                pitem = pitem if isinstance(pitem, dict) else {}
                if ptype == "task_complete":
                    terminal = "completed"
                    message = payload.get("last_agent_message")
                    if isinstance(message, str) and message.strip():
                        last_text = " ".join(message.split())[:_LAST_TEXT_LIMIT]
                        message_count = max(message_count, 1)
                elif ptype in {"task_aborted", "turn_aborted"}:
                    terminal = "interrupted"
                    reason = payload.get("reason") or payload.get("message")
                    terminal_message = reason if isinstance(reason, str) else ""
                elif ptype == "agent_message":
                    text = payload.get("message")
                    if isinstance(text, str) and text.strip():
                        message_count += 1
                        last_text = " ".join(text.split())[:_LAST_TEXT_LIMIT]
                        last_text_ts = str(rec.get("timestamp") or "")
                elif ptype == "item_completed":
                    itype = pitem.get("type")
                    if itype == "AgentMessage":
                        text = _codex_text(pitem)
                        if text.strip():
                            message_count += 1
                            last_text = " ".join(text.split())[:_LAST_TEXT_LIMIT]
                            last_text_ts = str(rec.get("timestamp") or "")
                    elif itype == "CommandExecution":
                        completion_records += 1
                        call_id = pitem.get("id")
                        if isinstance(call_id, str):
                            open_calls.pop(call_id, None)
            elif rtype == "response_item":
                ptype = payload.get("type")
                call_id = payload.get("call_id") or payload.get("id")
                if ptype in {"function_call", "custom_tool_call"} and isinstance(call_id, str):
                    open_calls[call_id] = _response_call_summary(payload)
                    calls_in_order.append(call_id)
                elif ptype in {"function_call_output", "custom_tool_call_output"} and isinstance(call_id, str):
                    response_outputs += 1
                    open_calls.pop(call_id, None)

    in_flight = None
    for call_id in reversed(calls_in_order):
        if call_id in open_calls:
            in_flight = open_calls[call_id]
            break
    return CodexRunFacts(
        message_count=message_count,
        # A rollout carries both the API-level call/output pair and the
        # higher-level CommandExecution completion for the same tool action.
        # Taking the larger side counts the action once instead of twice.
        command_count=max(completion_records, response_outputs),
        last_text=last_text,
        last_text_ts=last_text_ts,
        in_flight=in_flight,
        terminal=terminal,
        terminal_message=terminal_message,
    )


def _codex_stop_state(facts: CodexRunFacts) -> StopState:
    if facts.terminal == "interrupted":
        cause = "interrupted: the run recorded a terminal error/abort"
        if facts.terminal_message:
            cause += f" -- {toolresult.one_line(facts.terminal_message, 240)}"
    elif facts.in_flight:
        cause = "killed mid-call: the last tool call has no recorded result"
    elif facts.message_count:
        cause = "completed message: the run ends with the assistant's own text"
    elif facts.command_count:
        cause = "ended after completed tool calls without an assistant message"
    else:
        cause = "empty: no assistant message or completed tool call was recorded"

    in_flight_outcome = None
    if facts.in_flight:
        in_flight_outcome = "no completion recorded (the run ended before the call returned)"
    return StopState(
        cause,
        None,
        facts.last_text,
        facts.last_text_ts,
        facts.in_flight,
        in_flight_outcome,
    )


def build_stop_state(path: Path, fmt: str = "claude-code") -> StopState:
    """Build stop state for Claude Code, Codex rollouts, or Codex exec streams."""
    if fmt in {"codex", "codex-exec"}:
        return _codex_stop_state(read_codex_facts(Path(path)))
    if fmt != "claude-code":
        raise ValueError(f"stop state does not support {fmt!r}")

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
                                tail_interrupt = False
                                tail_kind = "user_text"
                elif isinstance(content, str):
                    if toolresult.is_interrupt_text(content):
                        tail_interrupt = True
                        tail_kind = "interrupt"
                    else:
                        tail_interrupt = False
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
        elif kind == "denied" and tail_kind in ("user_result", "interrupt"):
            # Only while nothing (assistant text, operator text) follows it.
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
