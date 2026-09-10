"""OpenAI Codex CLI adapter -- ~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl.

Schema facts verified directly against real local rollout files (this
machine has genuine Codex CLI history going back months -- not guessed from
documentation):

- One JSON object per line: {timestamp, ordinal, type, payload}. `type` in
  {"session_meta", "event_msg", "response_item", "turn_context",
  "world_state"}.
- `response_item` carries the raw API-level conversation (payload.type
  "message" with role user/assistant/developer, plus "function_call",
  "function_call_output", "reasoning", "web_search_call",
  "custom_tool_call(_output)"). "developer"-role messages are Codex's own
  harness-injected framing (permissions, collaboration-mode instructions)
  -- the same role Claude Code's isMeta flag marks.
- `event_msg` is a cleaner, already-classified layer sitting above that raw
  stream, and this adapter uses IT instead of touching response_item at
  all:
    - payload.type == "user_message" -> payload.message is the real,
      already-clean operator prompt text (no wrapper tags to strip, unlike
      Claude Code).
    - payload.type == "agent_message" -> payload.message is the real
      assistant text (payload also carries a "phase" field, e.g.
      "commentary" -- not yet used here, a candidate future checkpoint
      signal once its full value set is understood).
    - payload.type == "context_compacted" -> Codex's own compaction-
      boundary marker, the direct analog of Claude Code's compact_boundary.
  Every other event_msg type (task_started, token_count, web_search_end,
  task_complete, patch_apply_end, turn_aborted, thread_settings_applied,
  item_completed) is noise for this tool's purpose and is dropped.
- `ordinal` is a per-line, monotonically increasing integer -- present on
  every record in newer rollout files (verified on files from 2026-08
  onward) but ABSENT on older ones (verified on a 2026-07 file: only
  {timestamp, type, payload} at the top level). Used directly as seq/marker
  when present; falls back to this adapter's own enumerate index otherwise,
  which is stable within one parse but not guaranteed comparable across
  Codex CLI versions -- --since chaining across an old/new version boundary
  is therefore not guaranteed to line up.

Known gaps, left honest rather than guessed:
- No AskUserQuestion equivalent was found in the sessions inspected. QA_PAIR
  is never emitted by this adapter; if Codex has a structured-question tool
  this adapter doesn't yet know its event shape.
- `reasoning` response_items carry only `encrypted_content` in the local
  data seen -- Codex's chain-of-thought is opaque in the rollout file, so
  THINKING is never emitted here regardless of config.include_thinking.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..config import ExtractConfig
from ..events import EventKind, NormalizedEvent

name = "codex"

_LIFECYCLE_EVENT_TYPES = {"context_compacted"}


_SNIFF_SCAN_LINES = 10


def sniff(path: Path) -> bool:
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
                if (
                    isinstance(obj, dict)
                    and obj.get("type") == "session_meta"
                    and isinstance(obj.get("payload"), dict)
                    and "cli_version" in obj["payload"]
                ):
                    return True
    except OSError:
        return False
    return False


def list_sessions(path: Path) -> list[str]:
    return [str(path)]


def parse(path: Path, session_id: str, config: ExtractConfig) -> list[NormalizedEvent]:
    raw: list[dict] = []
    with path.open("r", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if obj.get("type") == "event_msg":
                raw.append(obj)

    if config.since_marker is not None:
        idx = next((i for i, r in enumerate(raw) if str(r.get("ordinal")) == config.since_marker), None)
        if idx is None:
            raise ValueError(f"--since marker {config.since_marker!r} not found as an ordinal in {path}")
        raw = raw[idx + 1 :]

    if config.until_marker is not None:
        idx = next((i for i, r in enumerate(raw) if str(r.get("ordinal")) == config.until_marker), None)
        if idx is None:
            raise ValueError(f"--until marker {config.until_marker!r} not found as an ordinal in {path}")
        raw = raw[: idx + 1]

    events: list[NormalizedEvent] = []
    for seq, rec in enumerate(raw):
        ordinal = str(rec.get("ordinal", seq))
        ts = rec.get("timestamp", "")
        payload = rec.get("payload", {}) or {}
        ptype = payload.get("type")

        if ptype == "user_message":
            text = payload.get("message", "")
            if text:
                events.append(NormalizedEvent(seq, ordinal, ts, EventKind.OPERATOR_TEXT, text))
        elif ptype == "agent_message":
            text = payload.get("message", "")
            if text:
                events.append(NormalizedEvent(seq, ordinal, ts, EventKind.ASSISTANT_TEXT, text))
        elif ptype in _LIFECYCLE_EVENT_TYPES:
            events.append(NormalizedEvent(seq, ordinal, ts, EventKind.LIFECYCLE_MARKER, f"[{ptype}]"))

    return events
