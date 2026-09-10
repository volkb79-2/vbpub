"""OpenAI Codex CLI adapter -- ~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl.

Schema facts verified directly against 400+ real local rollout files
spanning cli_version 0.142.2 through 0.151.0 (this machine has genuine
Codex CLI history going back months -- not guessed from documentation).
**Real, load-bearing finding from that spread: Codex's event_msg schema was
restructured entirely around cli_version 0.147.0 (2026-08-09).** Every file
from 0.147.0 onward uses ONLY the new shape below -- an adapter reading
just the old shape (this adapter's own first version) silently extracted
ZERO events from every real Codex session in the last month of local
history. Both generations are handled here; a real corpus this size having
a breaking schema change mid-window is itself the reason to always verify
an adapter against many real files spanning time, not a handful from one
period.

- One JSON object per line: {timestamp, ordinal, type, payload}. Top-level
  `type` in {"session_meta", "event_msg", "response_item", "turn_context",
  "world_state", "compacted" (new-schema only)}.
- `response_item` carries the raw API-level conversation and is NOT used by
  this adapter (see `event_msg` below, used instead in both generations).
- `event_msg` is a cleaner, already-classified layer, but its payload shape
  changed between generations:
  - OLD (cli_version <= ~0.145.0): `payload.type` directly names the kind --
    "user_message" (payload.message = operator text), "agent_message"
    (payload.message = assistant text, payload also carries "phase" e.g.
    "commentary" -- not yet used, a candidate future checkpoint signal),
    "context_compacted" (Codex's compaction-boundary marker).
  - NEW (cli_version >= 0.147.0): every real event arrives wrapped as
    `payload.type == "item_completed"`, `payload.item.type` naming the real
    kind -- "UserMessage" (item.content[].text where block type=="text"),
    "AgentMessage" (item.content[].text where block type=="Text" -- note
    the differing case between the two message kinds' block type strings,
    verified directly, not assumed consistent), "Reasoning" (see THINKING
    below), "ContextCompaction" (a content-free completion echo of the
    SAME event the top-level "compacted" record already carries with real
    text -- skipped here as redundant, not as unrecognized). Everything
    else under item_completed ("CommandExecution", "CollabAgentToolCall" --
    a sub-agent-spawning tool, verified via its "tool": "spawn_agent"
    field, not a Q&A mechanism -- "FileChange") is machine/tool noise.
  - Every other event_msg type in either generation (task_started,
    token_count, web_search_end, task_complete, patch_apply_end,
    turn_aborted, thread_settings_applied, thread_goal_updated,
    sub_agent_activity) is noise and is dropped.
- The NEW generation's compaction marker is a top-level `type: "compacted"`
  record (not nested under event_msg), whose `payload.message` is Codex's
  own real compaction summary text -- kept as the LIFECYCLE_MARKER's text,
  the direct analog of Claude Code's isCompactSummary content.
- `ordinal` is a per-line, monotonically increasing integer -- present on
  every record in every rollout file from 2026-08 onward (both the old and
  new event_msg shapes appear with it) but ABSENT on older ones (verified
  on a 2026-06/07 file: only {timestamp, type, payload} at the top level).
  Used directly as seq/marker when present; falls back to this adapter's
  own enumerate index otherwise, which is stable within one parse but not
  guaranteed comparable across Codex CLI versions -- --since chaining
  across an old/new version boundary is therefore not guaranteed to line up.

Known gaps, left honest rather than guessed:
- No AskUserQuestion equivalent was found in either generation's sessions
  inspected (including CollabAgentToolCall, checked directly -- it's a
  sub-agent spawn, not a question/answer tool). QA_PAIR is never emitted by
  this adapter; if Codex has a structured-question tool, this adapter
  doesn't yet know its event shape.
- **Superseded finding, corrected by wider real-data sampling**: an earlier
  version of this adapter (reading only `response_item`-layer `reasoning`
  items, which carry `encrypted_content`) concluded Codex's chain-of-
  thought was opaque. The NEW event_msg generation's "Reasoning" item
  carries a `raw_content` list of PLAIN-TEXT reasoning strings -- not
  encrypted at all. THINKING is now emitted from this field (new-generation
  files only) when config.include_thinking is set. Whether `encrypted_content`
  in the old `response_item` layer is genuinely unrecoverable there, or
  just a different (possibly API-configuration-dependent) code path from
  the one that produces plain-text `raw_content` here, is still open --
  see the Codex-investigation research thread for that narrower question.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..config import ExtractConfig
from ..events import EventKind, NormalizedEvent

name = "codex"

_TOP_LEVEL_TYPES = {"event_msg", "compacted"}
_SKIPPED_ITEM_TYPES = {"CommandExecution", "CollabAgentToolCall", "FileChange", "ContextCompaction"}


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


def _item_text(item: dict, block_type: str) -> str:
    blocks = item.get("content") or []
    return "\n".join(
        b.get("text", "") for b in blocks
        if isinstance(b, dict) and b.get("type", "").lower() == block_type
    )


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
            if obj.get("type") in _TOP_LEVEL_TYPES:
                raw.append(obj)

    # Tag every record with its absolute position in the full (unsliced)
    # file BEFORE any --since/--until slicing, and use that absolute
    # position -- never a position re-numbered from 0 after slicing -- as
    # the ordinal fallback everywhere below (mirrors the fallback used when
    # events are actually generated -- see this adapter's docstring on the
    # pre-2026-08 files that lack `ordinal` entirely). Resolution must
    # replicate the exact fallback used when a marker was originally
    # emitted: re-enumerating a sliced list from 0 would make a record's
    # fallback marker depend on how many prior --since hops had already
    # been applied, so the same physical record could mint a different
    # marker on every chained run -- and a later run resolving an old
    # marker against a freshly re-parsed (unsliced) file would then land on
    # the wrong record, silently re-emitting content already flushed to a
    # prior snapshot. Absolute, pre-slice position is stable across any
    # number of chained --since/--until runs since every run re-parses the
    # same on-disk file from scratch.
    indexed = list(enumerate(raw))

    if config.since_marker is not None:
        idx = next((i for i, r in indexed if str(r.get("ordinal", i)) == config.since_marker), None)
        if idx is None:
            raise ValueError(f"--since marker {config.since_marker!r} not found as an ordinal in {path}")
        indexed = [(i, r) for i, r in indexed if i > idx]

    if config.until_marker is not None:
        idx = next((i for i, r in indexed if str(r.get("ordinal", i)) == config.until_marker), None)
        if idx is None:
            raise ValueError(f"--until marker {config.until_marker!r} not found as an ordinal in {path}")
        indexed = [(i, r) for i, r in indexed if i <= idx]

    events: list[NormalizedEvent] = []
    for seq, (abs_i, rec) in enumerate(indexed):
        ordinal = str(rec.get("ordinal", abs_i))
        ts = rec.get("timestamp", "")
        payload = rec.get("payload", {}) or {}
        ptype = payload.get("type")

        if rec.get("type") == "compacted":
            # NEW generation's top-level compaction record -- carries
            # Codex's own real compaction summary text, unlike the OLD
            # generation's content-free "context_compacted" event_msg.
            text = payload.get("message", "") or "[compacted]"
            events.append(NormalizedEvent(seq, ordinal, ts, EventKind.LIFECYCLE_MARKER, text))
            continue

        if ptype == "user_message":  # OLD generation
            text = payload.get("message", "")
            if text:
                events.append(NormalizedEvent(seq, ordinal, ts, EventKind.OPERATOR_TEXT, text))
        elif ptype == "agent_message":  # OLD generation
            text = payload.get("message", "")
            if text:
                events.append(NormalizedEvent(seq, ordinal, ts, EventKind.ASSISTANT_TEXT, text))
        elif ptype == "context_compacted":  # OLD generation
            events.append(NormalizedEvent(seq, ordinal, ts, EventKind.LIFECYCLE_MARKER, "[context_compacted]"))
        elif ptype == "item_completed":  # NEW generation
            item = payload.get("item") or {}
            itype = item.get("type")
            if itype == "UserMessage":
                text = _item_text(item, "text")
                if text:
                    events.append(NormalizedEvent(seq, ordinal, ts, EventKind.OPERATOR_TEXT, text))
            elif itype == "AgentMessage":
                text = _item_text(item, "text")
                if text:
                    events.append(NormalizedEvent(seq, ordinal, ts, EventKind.ASSISTANT_TEXT, text))
            elif itype == "Reasoning" and config.include_thinking:
                text = "\n".join(item.get("raw_content") or [])
                if text:
                    events.append(NormalizedEvent(seq, ordinal, ts, EventKind.THINKING, text))
            # else: itype in _SKIPPED_ITEM_TYPES, or an unrecognized future
            # item type -- either way, machine/tool noise, not emitted.

    return events
