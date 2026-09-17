"""OpenAI Codex CLI adapter -- $CODEX_HOME/sessions/YYYY/MM/DD/rollout-*.jsonl
(default ``~/.codex/sessions``).

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
- **Sub-agent targeting (2026-09-11, corrected same day by a live test --
  the note this replaces was WRONG).** The original note here claimed
  Codex sub-agent activity is inline-only with no separate file, based on
  400+ pre-existing local rollout files that apparently never contained a
  real `spawn_agent` dispatch. A live test (`codex exec -m gpt-5.6-luna`,
  prompted to delegate one of two tasks via its collab-agent tool) proved
  the opposite: Codex creates a genuine SEPARATE rollout file per spawned
  sub-agent, one directory listing away from its dispatcher --
  `~/.codex/sessions/YYYY/MM/DD/rollout-<ts>-<thread_id>.jsonl`, where
  `<thread_id>` is exactly the id returned in the parent's own
  `CollabAgentToolCall` event (`item.receiver_thread_ids`, alongside
  `item.receiver_agents[].agent_nickname` -- sub-agents get human-readable
  codenames, e.g. "Feynman" in the test run). The child file is a FULLY
  NORMAL, self-contained rollout (session_meta/event_msg/response_item/
  world_state/turn_context/token_usage_record, same as any top-level
  session) -- its `session_meta.payload` carries `thread_source:
  "subagent"` (vs `"user"` for a real top-level session -- a single,
  direct discriminator, cleaner than Claude Code's isSidechain, which
  needs a same-file-has-a-primary-thread heuristic -- see
  claude_code.py's parse()) plus `forked_from_id` / `source.subagent.
  thread_spawn.{parent_thread_id, depth, agent_nickname}` -- `depth`
  is the direct analog of Claude Code's `.meta.json` `spawnDepth`.
  Verified end to end: this adapter's existing `parse()`, UNCHANGED,
  already extracts a sub-agent's own rollout file correctly (its
  `UserMessage`/`AgentMessage`/`FileChange` records are schema-identical
  to a normal session) -- `nyxloom extract <that child rollout file>`
  works today, no adapter code change needed. What's still missing, same
  as Claude Code and opencode (see their own adapters' notes): a
  DISCOVERY step that lists a session's sub-agent files and their
  lineage without already knowing the thread id -- `list_sessions()`
  below still returns only `[str(path)]` (one file = one session, still
  correct per-file) and does not yet walk sibling rollout files for a
  shared root `session_id` / matching `forked_from_id` chain.
- **Superseded finding, corrected by wider real-data sampling**: an earlier
  version of this adapter (reading only `response_item`-layer `reasoning`
  items, which carry `encrypted_content`) concluded Codex's chain-of-
  thought was opaque. The NEW event_msg generation's "Reasoning" item
  carries a `raw_content` list of PLAIN-TEXT reasoning strings -- not
  encrypted at all. THINKING is now emitted from this field (new-generation
  files only) when config.include_thinking is set.
- **RESOLVED 2026-09-11** (was the open question directly above):
  `encrypted_content` in the OLD `response_item.reasoning` layer is
  genuinely, permanently unrecoverable -- not a config-dependent alternate
  code path to the same plaintext `raw_content` above. It's OpenAI's
  Responses API "encrypted reasoning items" feature (stateless mode:
  `store: false` or org-level Zero Data Retention). Mechanism: the
  reasoning payload is sealed by OpenAI's backend at generation time;
  Codex round-trips the opaque blob back on the next turn so the model can
  recall its own prior reasoning; the backend decrypts it server-side, in
  memory only, reconstructs reasoning context, then discards the plaintext
  -- the client never holds a key and cannot decrypt it, ever, by design
  (both a CoT-exposure policy and a ZDR-compliance mechanism at once).
  Confirmed by real-world failure, not just docs: openai/codex#25290 shows
  Codex's OWN later runs failing to replay locally-persisted
  `encrypted_content` it wrote itself ("the encrypted content ... could
  not be decrypted or parsed") after a backend-side key/format change --
  proof the opacity is real and outlives even the writing client's own
  later sessions, not an artifact of this adapter's own limited access.
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
    """Not gated on path.suffix == ".jsonl" (removed 2026-09-11, see
    claude_code.py's sniff() docstring for the motivating case) -- the
    session_meta + payload.cli_version content check below was already the
    real discriminator for every .jsonl-suffixed file too."""
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


def _first_session_meta(path: Path) -> dict | None:
    """The FIRST session_meta record's own payload -- a rollout file that
    is itself a sub-agent's own file carries its OWN session_meta first,
    THEN (in a real file this session inspected) a second session_meta
    copying the root/parent's -- confirmed by real ordinal order (0, then
    1), not assumed. Only a few lines are scanned, not the whole file."""
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
                if isinstance(obj, dict) and obj.get("type") == "session_meta":
                    payload = obj.get("payload")
                    return payload if isinstance(payload, dict) else None
    except OSError:
        return None
    return None


def _scan_event_counts(path: Path) -> tuple[int, str | None, str | None]:
    count = 0
    first_ts: str | None = None
    last_ts: str | None = None
    try:
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
                    count += 1
                    ts = obj.get("timestamp")
                    if ts:
                        first_ts = first_ts or ts
                        last_ts = ts
    except OSError:
        pass
    return count, first_ts, last_ts


def list_agents(path: Path) -> list["SessionNode"]:
    """Discovery for `nyxloom extract-sessions` -- every rollout file that
    shares path's own root `session_id`, walked from real, verified schema
    (2026-09-11 live test, corrected this module's earlier wrong "inline
    only" claim -- see the "Sub-agent targeting" note above). path may be
    the top-level session's own file or a specific sub-agent's file --
    either way the search is by shared session_id, so the whole family is
    found regardless of which member path names.

    A depth>1 chain (a sub-agent that itself spawned a sub-agent) was not
    produced by the one live test this was verified against -- `depth`/
    `parent_thread_id` come directly from `source.subagent.thread_spawn`
    when present, so a real deeper chain should resolve correctly if one
    is ever seen, but that specific case is UNVERIFIED, not guessed away.
    """
    from ..sessions import SessionNode

    own_meta = _first_session_meta(path)
    if own_meta is None:
        return []
    root_id = own_meta.get("session_id")
    if root_id is None:
        return []

    sessions_root = next((anc for anc in path.parents if anc.name == "sessions"), path.parent)

    nodes = []
    for candidate in sessions_root.glob("**/rollout-*.jsonl"):
        meta = _first_session_meta(candidate)
        if meta is None or meta.get("session_id") != root_id:
            continue
        cid = meta.get("id") or str(candidate)
        thread_source = meta.get("thread_source")
        # `source` is a plain string ("exec") for a real top-level session
        # and only an object ({"subagent": {"thread_spawn": {...}}}) for a
        # real sub-agent's own file -- verified directly, not assumed
        # consistent (see codex.py's module docstring's session_meta dump).
        raw_source = meta.get("source")
        subagent = raw_source.get("subagent") if isinstance(raw_source, dict) else None
        spawn = (subagent or {}).get("thread_spawn") or {}
        is_subagent = thread_source == "subagent"
        depth = spawn.get("depth", 1) if is_subagent else 0
        parent_id = meta.get("forked_from_id") or spawn.get("parent_thread_id")
        label = spawn.get("agent_nickname") or ("(sub-agent)" if is_subagent else "(interactive session)")
        if is_subagent:
            label = f"{label} (depth {depth})"
        count, first_ts, last_ts = _scan_event_counts(candidate)
        nodes.append(SessionNode(
            id=cid, parent_id=parent_id, depth=depth, label=label, path=str(candidate),
            record_count=count, first_ts=first_ts, last_ts=last_ts,
        ))
    return nodes


def _item_text(item: dict, block_type: str) -> str:
    blocks = item.get("content") or []
    return "\n".join(
        b.get("text", "") for b in blocks
        if isinstance(b, dict) and b.get("type", "").lower() == block_type
    )


def is_top_level_record(rec: dict) -> bool:
    """Whether this raw record is one parse() would even look at -- the
    {event_msg, compacted} universe that also defines this adapter's own
    ordinal-fallback marker space. Public because follow.py's tailer decides
    the same thing about each newly-arrived line, and because a disagreement
    there would silently shift every fallback marker."""
    return rec.get("type") in _TOP_LEVEL_TYPES


def parse_record(
    rec: dict, seq: int, fallback_marker: str, config: ExtractConfig
) -> list[NormalizedEvent]:
    """One raw record -> the NormalizedEvents it yields (0 or 1 today).

    Factored out of parse()'s own loop (2026-09-12) so follow.py's
    incremental tailer applies literally the same per-record rules to a
    newly-appended line as a full parse does. `fallback_marker` is the
    marker for a record with no `ordinal` of its own (real pre-2026-08
    rollout files have none -- see the module docstring): parse() passes its
    absolute pre-slice position, follow.py a stream-local token. Needs no
    cross-record state at all, unlike claude_code.parse_record.
    """
    ordinal = str(rec.get("ordinal", fallback_marker))
    ts = rec.get("timestamp", "")
    payload = rec.get("payload", {}) or {}
    ptype = payload.get("type")

    if rec.get("type") == "compacted":
        # NEW generation's top-level compaction record -- carries Codex's own
        # real compaction summary text, unlike the OLD generation's
        # content-free "context_compacted" event_msg.
        text = payload.get("message", "") or "[compacted]"
        return [NormalizedEvent(seq, ordinal, ts, EventKind.LIFECYCLE_MARKER, text)]

    if ptype == "user_message":  # OLD generation
        text = payload.get("message", "")
        if text:
            return [NormalizedEvent(seq, ordinal, ts, EventKind.OPERATOR_TEXT, text)]
    elif ptype == "agent_message":  # OLD generation
        text = payload.get("message", "")
        if text:
            return [NormalizedEvent(seq, ordinal, ts, EventKind.ASSISTANT_TEXT, text)]
    elif ptype == "context_compacted":  # OLD generation
        return [NormalizedEvent(seq, ordinal, ts, EventKind.LIFECYCLE_MARKER, "[context_compacted]")]
    elif ptype == "item_completed":  # NEW generation
        item = payload.get("item") or {}
        itype = item.get("type")
        if itype == "UserMessage":
            text = _item_text(item, "text")
            if text:
                return [NormalizedEvent(seq, ordinal, ts, EventKind.OPERATOR_TEXT, text)]
        elif itype == "AgentMessage":
            text = _item_text(item, "text")
            if text:
                return [NormalizedEvent(seq, ordinal, ts, EventKind.ASSISTANT_TEXT, text)]
        elif itype == "Reasoning" and config.include_thinking:
            text = "\n".join(item.get("raw_content") or [])
            if text:
                return [NormalizedEvent(seq, ordinal, ts, EventKind.THINKING, text)]
        # else: itype in _SKIPPED_ITEM_TYPES, or an unrecognized future item
        # type -- either way, machine/tool noise, not emitted.
        # CollabAgentToolCall (sub-agent dispatch) included: the spawned
        # sub-agent's OWN conversation is NOT here -- it lives in its own
        # separate rollout file (item.receiver_thread_ids names it) -- see the
        # module docstring's "Sub-agent targeting" note.
    return []


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
            if is_top_level_record(obj):
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
        events += parse_record(rec, seq, str(abs_i), config)

    return events
