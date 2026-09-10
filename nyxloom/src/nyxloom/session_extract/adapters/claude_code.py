"""Claude Code adapter -- ~/.claude/projects/<project>/<session>.jsonl.

Schema facts this adapter relies on (verified directly against real session
files during this tool's design, not from documentation):

- One JSON object per line. Relevant top-level `type` values: "user",
  "assistant", "system" (plus a long tail of harness bookkeeping types --
  mode, bridge-session, attachment, last-prompt, atis-latch, ai-title,
  queue-operation, cost-state, file-history-* -- all ignored here).
- `assistant` messages carry message.content as a list of blocks: "text",
  "thinking", "tool_use". A message with BOTH tool_use and text blocks is
  common (the model narrates, then calls a tool) -- text blocks are always
  emitted as ASSISTANT_TEXT regardless; classifier.py, not this adapter,
  decides whether a given block reads as a checkpoint.
- `user` messages are mostly synthetic tool_result feedback, not real
  operator input -- in one 10k-line real session, 1753 of 1812 "user"-type
  records were tool_result, only 58 were a real typed prompt. Real operator
  text has message.content as a plain string (or a list containing exactly
  one "text" block); harness-injected framing (a context-usage report, a
  local-command-caveat notice, "session continued from...") is marked
  `isMeta: true` or `isVisibleInTranscriptOnly: true` and is excluded here
  rather than treated as operator intent.
- Claude Code wraps slash-command invocations in the operator's own text as
  `<command-name>NAME</command-name>` (+ optional `<command-message>` /
  `<command-args>`); `/compact` and `/clear` specifically are promoted to
  LIFECYCLE_MARKER since they bound what a resume ever needs to look past.
- AskUserQuestion: the tool_use lives on an assistant turn; its answer
  arrives later as a `user`-type tool_result whose `tool_use_id` matches.
  Verified on a real 4-question batch: the harness ALREADY renders every
  question+answer pair into one string ('"Q1"="A1", "Q2"="A2", ...') --
  this adapter uses that string verbatim rather than re-deriving pairing
  from tool_use.input.questions.
- `system` records with `subtype: "compact_boundary"`, and `user` records
  with top-level `isCompactSummary: true`, mark an auto-compaction; both
  become LIFECYCLE_MARKER (selection never looks earlier than the nearest
  one -- that content is already a different kind of artifact).
- Branch points (a parentUuid with >1 child) were investigated directly:
  every one found was a parallel-tool-call fan-out artifact (one assistant
  turn issuing several tool calls forces a tree structure onto what is
  really a fan-out), never message-edit/retry branching, and file order was
  confirmed strictly timestamp-monotonic. Since this adapter discards all
  tool_use/tool_result content except AskUserQuestion answers anyway, that
  branching is invisible here -- this is a straight top-to-bottom scan,
  deliberately NOT parentUuid-chain-aware. Genuine message-edit/resubmit
  branching was not observed and is not specifically handled; if it turns
  out to matter, this is the place to add it.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from ..config import ExtractConfig
from ..events import EventKind, NormalizedEvent

name = "claude-code"

_COMMAND_NAME_RE = re.compile(r"<command-name>([^<]*)</command-name>", re.IGNORECASE)
_HARNESS_TAG_RE = re.compile(
    r"<(local-command-caveat|local-command-stdout|command-name|command-message|command-args)>.*?"
    r"</\1>|<(local-command-caveat|local-command-stdout|command-name|command-message|command-args)\b[^>]*/?>",
    re.IGNORECASE | re.DOTALL,
)
_LIFECYCLE_COMMANDS = {"compact", "clear"}


_SNIFF_SCAN_LINES = 50


def sniff(path: Path) -> bool:
    """The first line is often a housekeeping record (type "mode",
    "bridge-session", ...) that carries sessionId but no parentUuid --
    real user/assistant records normally appear within the first handful of
    lines, so this scans forward rather than trusting line 1 alone."""
    if path.suffix != ".jsonl":
        return False
    try:
        with path.open("r", errors="ignore") as f:
            for i, line in enumerate(f):
                if i >= _SNIFF_SCAN_LINES:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(obj, dict) and "sessionId" in obj and "parentUuid" in obj:
                    return True
    except OSError:
        return False
    return False


def list_sessions(path: Path) -> list[str]:
    return [str(path)]


def _strip_harness_tags(text: str) -> str:
    return _HARNESS_TAG_RE.sub("", text).strip()


def _command_name(text: str) -> str | None:
    m = _COMMAND_NAME_RE.search(text)
    if not m:
        return None
    return m.group(1).strip().lstrip("/").lower()


def _load_records(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with path.open("r", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if obj.get("type") in ("user", "assistant", "system"):
                records.append(obj)
    return records


def parse(path: Path, session_id: str, config: ExtractConfig) -> list[NormalizedEvent]:
    records = _load_records(path)

    if config.since_marker is not None:
        idx = next((i for i, r in enumerate(records) if r.get("uuid") == config.since_marker), None)
        if idx is None:
            raise ValueError(
                f"--since marker {config.since_marker!r} not found as a uuid in {path}"
            )
        records = records[idx + 1 :]

    if config.until_marker is not None:
        idx = next((i for i, r in enumerate(records) if r.get("uuid") == config.until_marker), None)
        if idx is None:
            raise ValueError(
                f"--until marker {config.until_marker!r} not found as a uuid in {path}"
            )
        records = records[: idx + 1]

    askuserquestion_ids: set[str] = set()
    for rec in records:
        if rec.get("type") != "assistant":
            continue
        for block in rec.get("message", {}).get("content", []) or []:
            if isinstance(block, dict) and block.get("type") == "tool_use" and block.get("name") == "AskUserQuestion":
                tid = block.get("id")
                if tid:
                    askuserquestion_ids.add(tid)

    events: list[NormalizedEvent] = []
    for seq, rec in enumerate(records):
        if rec.get("isSidechain"):
            continue

        uuid = rec.get("uuid") or f"line{seq}"
        ts = rec.get("timestamp", "")
        rtype = rec.get("type")

        if rtype == "system":
            if rec.get("subtype") == "compact_boundary":
                events.append(NormalizedEvent(seq, uuid, ts, EventKind.LIFECYCLE_MARKER, "[compact boundary]"))
            continue

        if rtype == "assistant":
            content = rec.get("message", {}).get("content", []) or []
            for block in content:
                if not isinstance(block, dict):
                    continue
                btype = block.get("type")
                if btype == "text":
                    text = block.get("text", "")
                    if text:
                        events.append(NormalizedEvent(seq, uuid, ts, EventKind.ASSISTANT_TEXT, text))
                elif btype == "thinking" and config.include_thinking:
                    text = block.get("thinking", "")
                    if text:
                        events.append(NormalizedEvent(seq, uuid, ts, EventKind.THINKING, text))
            continue

        if rtype == "user":
            if rec.get("isCompactSummary"):
                events.append(NormalizedEvent(seq, uuid, ts, EventKind.LIFECYCLE_MARKER, "[compact summary]"))
                continue

            content = rec.get("message", {}).get("content")

            if isinstance(content, list):
                qa_text = None
                has_other_tool_result = False
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    if block.get("type") == "tool_result":
                        if block.get("tool_use_id") in askuserquestion_ids:
                            c = block.get("content")
                            qa_text = c if isinstance(c, str) else json.dumps(c)
                        else:
                            has_other_tool_result = True
                if qa_text is not None:
                    events.append(NormalizedEvent(seq, uuid, ts, EventKind.QA_PAIR, qa_text))
                    continue
                if has_other_tool_result:
                    continue
                text_blocks = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
                if not text_blocks:
                    continue
                raw_text = "\n".join(text_blocks)
            elif isinstance(content, str):
                raw_text = content
            else:
                continue

            if rec.get("isMeta") or rec.get("isVisibleInTranscriptOnly"):
                continue

            if raw_text.lstrip().startswith("<task-notification>"):
                # A background-agent (Task tool) completion push. Same shape
                # as a real operator turn (type "user", plain-string
                # content, no isMeta flag) but it's controller-injected tool
                # output, not operator intent -- and confirmed, on a real
                # session, to be pure noise for a resume brief: the assistant
                # always re-narrates whatever mattered from it in its own
                # next reply, which IS captured normally. Verified against a
                # real ~950-word raw notification block that added nothing
                # a human curating the same session chose to keep.
                continue

            cmd = _command_name(raw_text)
            cleaned = _strip_harness_tags(raw_text)
            if cmd in _LIFECYCLE_COMMANDS:
                label = f"[/{cmd}]" + (f" {cleaned}" if cleaned else "")
                events.append(NormalizedEvent(seq, uuid, ts, EventKind.LIFECYCLE_MARKER, label))
                continue
            if not cleaned:
                continue
            events.append(NormalizedEvent(seq, uuid, ts, EventKind.OPERATOR_TEXT, cleaned))

    return events
