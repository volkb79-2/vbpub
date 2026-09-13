"""A "dumb", independent lossless-prose dump -- deliberately NOT the smart
adapter's classification path.

For every user/assistant record: keep every text/thinking content block
verbatim, drop tool_use/tool_result blocks (machine calls) and every
non-conversational bookkeeping record type (mode, attachment,
bridge-session, file-history-*, atis-latch, last-prompt, ai-title,
queue-operation). No isMeta/task-notification/AskUserQuestion-pairing
logic at all -- that's exactly the point.

Two use cases this exists for:

1. **A ground-truth superset for judging/tuning the real classifier.**
   `select.select()`'s job is to pick a small, curated subset of a
   session's prose. Judging whether it picked well requires seeing
   everything it *could* have picked from, read in full -- comparing the
   real output against this dump (not against the raw JSONL, which is
   mostly machine noise) is what let a full, line-by-line replay of a real
   session span against an operator's own hand-curated excerpt find the
   two real bugs documented in config.py/select.py's module docstrings
   (task-notification noise, the recency-leniency over-keep). Use `--until`
   alongside `--since` to pin a run to a fixed historical span for a
   reproducible before/after comparison.
2. **Raw material for a future hybrid: agent-authored condensation of the
   segment mechanical extraction structurally cannot recover.** Claude
   Code's own built-in auto-compaction preserves a short verbatim tail of
   recent messages (including raw tool_result content -- more than this
   tool ever keeps) and LLM-summarizes everything older into prose,
   because that older segment may describe tool activity the assistant
   never restated in its own words -- something no static heuristic can
   reconstruct. A future design point (not yet implemented): on a genuine
   cold restart with no prefix-cache reuse to preserve, hand an agent this
   dump for the segment being retired and ask it to describe, in its own
   words, "what should be remembered from here" -- especially anything that
   exists ONLY in tool output -- then prepend that agent-authored
   condensation before this tool's own mechanically-extracted,
   unsummarized prose for the segment that IS still cache-worth keeping
   verbatim. See the session_extract README's "Future" section.

Claude Code, Codex, and opencode are all supported today (`dump_claude_code`,
`dump_codex`, `dump_opencode`) -- each independently reads its own format's
raw records rather than sharing code with that format's adapters/*.py
parse(), per this module's own "unbiased ground-truth" design point above.
None of the Codex/opencode dumpers has been exercised against a real
hand-curated reference the way `dump_claude_code` has (see that function's
own history, docstring point 1 above) -- they are validated against real
local session files (shape/field-level correctness), not yet against a
human's own hand-picked "what should have survived" judgment the way
Claude Code's dump was.

`dump_codex` reuses two small, already-tested pieces of plumbing from
adapters/codex.py rather than re-deriving them: `is_top_level_record` (the
event_msg/compacted top-level-type filter that defines codex.py's OWN
ordinal-fallback marker space -- diverging from it here would silently
break --since/--until chaining between `nyxloom extract --format codex` and
`nyxloom extract-lossless --format codex`, exactly the reproducible-
comparison use case point 1 above names) and `_item_text` (a pure
content-block-text extractor, not a classification decision). Everything
about WHAT counts as prose to
keep is independently re-derived here, same as `dump_claude_code`.

The per-record block functions (`claude_code_blocks`, `codex_blocks`,
`opencode_blocks` -- see `LosslessBlock`) are the whole-file dumpers' own
loop bodies, factored out 2026-09-12 so `extract-lossless --follow` can
apply them to a newly-appended record. That sharing is WITHIN this module,
so it does not weaken the independence-from-adapters point above.

`dump_codex` also found the same real-data surprise `stats.py`'s own
`_build_call_rows_codex` documents in full: the NEW generation's
`compacted.payload.message` is empty for ~91% of real compaction records
(the real replacement content is genuinely encrypted, not recoverable as
text) -- this dumper falls back to the same `"[compacted]"` placeholder
adapters/codex.py's own parse() already uses, so behavior is honest by
construction; see stats.py's module docstring for the full writeup.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

_KEEP_BLOCK_TYPES = {"text", "thinking"}
_BOOKKEEPING_TYPES = {
    "mode", "attachment", "bridge-session", "file-history-snapshot",
    "file-history-delta", "atis-latch", "last-prompt", "ai-title",
    "queue-operation", "cost-state",
}


@dataclass(frozen=True)
class LosslessBlock:
    """One dumped block: its `===[marker | ts | TAG]===` header and the
    verbatim prose under it, kept apart so a caller can use either half.

    The per-record functions below (`claude_code_blocks`, `codex_blocks`,
    `opencode_blocks`) were factored out of the whole-file dumpers'
    respective loops (2026-09-12) so `extract-lossless --follow` streams
    newly-appended records through exactly the same rules a one-shot dump
    applies, rather than a second implementation of "what counts as prose"
    that could drift from it. The dumpers now consume them.

    `text` separately (not just `render()`) because follow.py's attention
    detection scores the PROSE, and the header -- nyxloom's own framing --
    would otherwise pollute that.
    """

    header: str
    text: str

    def render(self) -> str:
        return f"{self.header}\n{self.text}"


def claude_code_blocks(rec: dict, fallback_marker: str) -> list[LosslessBlock]:
    """Every text/thinking block one raw Claude Code record contributes,
    with the flag tags the header carries -- see dump_claude_code's docstring
    for what each tag means and why all four are surfaced."""
    rtype = rec.get("type")
    ts = rec.get("timestamp", "")
    marker = rec.get("uuid") or fallback_marker

    if rtype == "system" and rec.get("subtype") == "compact_boundary":
        return [LosslessBlock(
            f"===[{marker} | {ts} | SYSTEM compact_boundary]===", str(rec.get("content", ""))
        )]

    if rtype not in ("user", "assistant"):
        # bookkeeping or unrecognized record type -- no prose to lose.
        return []

    msg = rec.get("message") or {}
    content = msg.get("content")
    role = (msg.get("role") or rtype).upper()
    flags = "".join(
        f" {name}" for name, present in (
            ("isMeta", rec.get("isMeta")),
            ("isVisibleInTranscriptOnly", rec.get("isVisibleInTranscriptOnly")),
            ("interruptedMessageId", rec.get("interruptedMessageId")),
            ("isSidechain", rec.get("isSidechain")),
        ) if present
    )
    tag = f"{role}{flags}"

    if isinstance(content, str):
        if content.strip():
            return [LosslessBlock(f"===[{marker} | {ts} | {tag}]===", content)]
        return []

    blocks: list[LosslessBlock] = []
    if isinstance(content, list):
        for block in content:
            if not isinstance(block, dict) or block.get("type") not in _KEEP_BLOCK_TYPES:
                continue
            # A thinking block's prose is under `thinking`, NOT `text` -- a
            # real bug found 2026-09-12: this dumper read `text` for both
            # kept block types, so every thinking block dumped as empty and
            # was silently discarded, contradicting this module's own
            # "lossless means lossless, include_thinking plays no part"
            # claim (see dump_codex's docstring, which states it outright).
            # Verified against 5469 real thinking blocks across 40 real
            # sessions: EVERY one has keys {type, thinking, signature} and
            # no `text` key at all. Practical impact on this corpus was
            # small only by luck -- Claude Code stores an empty `thinking`
            # string for all but 2 of those 5469 -- which is exactly why
            # nothing surfaced it before.
            text = block.get("text") or block.get("thinking") or ""
            if text.strip():
                blocks.append(LosslessBlock(f"===[{marker} | {ts} | {tag} {block['type']}]===", text))
    return blocks


def codex_blocks(rec: dict, marker: str) -> list[LosslessBlock]:
    """Every prose block one raw Codex rollout record contributes, both
    schema generations -- see dump_codex's docstring."""
    from .adapters.codex import _item_text

    ts = rec.get("timestamp", "")
    payload = rec.get("payload", {}) or {}
    ptype = payload.get("type")

    if rec.get("type") == "compacted":
        # See this module's own docstring / stats.py's fuller writeup:
        # payload.message is empty for the large majority of real
        # compactions (the real content is encrypted) -- same fallback
        # adapters/codex.py's own parse() already uses.
        text = payload.get("message", "") or "[compacted]"
        return [LosslessBlock(f"===[{marker} | {ts} | LIFECYCLE compacted]===", text)]
    if ptype == "user_message":  # OLD generation
        text = payload.get("message", "")
        return [LosslessBlock(f"===[{marker} | {ts} | USER]===", text)] if text else []
    if ptype == "agent_message":  # OLD generation
        text = payload.get("message", "")
        return [LosslessBlock(f"===[{marker} | {ts} | ASSISTANT]===", text)] if text else []
    if ptype == "context_compacted":  # OLD generation, content-free
        return [LosslessBlock(
            f"===[{marker} | {ts} | LIFECYCLE context_compacted]===", "[context_compacted]"
        )]
    if ptype == "item_completed":  # NEW generation
        item = payload.get("item") or {}
        itype = item.get("type")
        if itype == "UserMessage":
            text = _item_text(item, "text")
            return [LosslessBlock(f"===[{marker} | {ts} | USER]===", text)] if text else []
        if itype == "AgentMessage":
            text = _item_text(item, "text")
            return [LosslessBlock(f"===[{marker} | {ts} | ASSISTANT]===", text)] if text else []
        if itype == "Reasoning":
            text = "\n".join(item.get("raw_content") or [])
            return [LosslessBlock(f"===[{marker} | {ts} | THINKING]===", text)] if text else []
        # else: CommandExecution/CollabAgentToolCall/FileChange/
        # ContextCompaction -- machine/tool noise, no prose to lose.
    # else: task_started/token_count/web_search_end/task_complete/
    # patch_apply_end/turn_aborted/... -- noise, no prose to lose.
    return []


def opencode_block_for_texts(
    msg_id: str, time_created: int, data_json: str, texts: list[str]
) -> LosslessBlock | None:
    """Build a lossless block from selected text contributions.

    Follow mode calls this for a message whose part list changed, so it can
    preserve the message boundary while emitting only text not printed by
    phase one or an earlier poll.
    """
    try:
        data = json.loads(data_json)
    except json.JSONDecodeError:
        return None
    role = data.get("role")
    if role not in ("user", "assistant") or not texts:
        return None
    ts = datetime.fromtimestamp(time_created / 1000, tz=timezone.utc).isoformat()
    return LosslessBlock(f"===[{msg_id} | {ts} | {role.upper()}]===", "\n".join(texts))


def opencode_blocks(
    conn: sqlite3.Connection, msg_id: str, time_created: int, data_json: str
) -> list[LosslessBlock]:
    """The one block a `message` row contributes, its `part` rows' text
    fragments joined -- see dump_opencode's docstring. Takes the open
    connection because the parts are a second query, not part of the row."""
    part_rows = conn.execute(
        "SELECT data FROM part WHERE message_id = ? ORDER BY id ASC", (msg_id,)
    ).fetchall()
    texts = []
    for (part_json,) in part_rows:
        try:
            part = json.loads(part_json)
        except json.JSONDecodeError:
            continue
        if part.get("type") == "text" and part.get("text"):
            texts.append(part["text"])
    block = opencode_block_for_texts(msg_id, time_created, data_json, texts)
    return [block] if block is not None else []


def dump_claude_code(path: Path, since_marker: str | None = None, until_marker: str | None = None) -> str:
    """Render every text/thinking block in `path` as delimited plain text,
    optionally bounded to (since_marker, until_marker] by uuid (same marker
    convention as ExtractConfig.since_marker/until_marker). Raises
    ValueError if a given marker isn't found as a uuid in the file -- same
    contract as the adapters' own since/until handling, so a typo'd or
    wrong-file marker fails loudly rather than silently returning everything
    or nothing.

    Each block's own header names the record's real `uuid` (falling back to
    `L<line-number>` only for the rare record with none, e.g. a synthetic
    "system" record) -- the SAME marker adapters/claude_code.py's parse()
    assigns as a NormalizedEvent's own `.marker` for a user/assistant
    record, deliberately, so debug_diff.py can look up "does a real,
    scored event for this exact record exist, and what happened to it" by
    EXACT identity rather than approximating from isolated text (2026-09-11,
    operator direction: "we should always know why we excluded something").
    Also promotes `isVisibleInTranscriptOnly`, `interruptedMessageId`, and
    `isSidechain` into the same tag suffix as `isMeta` -- adapters/
    claude_code.py's parse() drops a "user" record unconditionally for
    isMeta/isVisibleInTranscriptOnly (before any cleaning), separately drops
    one carrying interruptedMessageId (Claude Code's own synthetic
    "[Request interrupted by user]" Ctrl-C marker, never real operator
    intent) unconditionally too, and drops ANY record (user or assistant)
    with isSidechain=true whenever the file also has a primary (non-
    sidechain) thread. All four are real, header-visible ground truth once
    tagged here, so debug_diff.py's marker lookup can name the exact
    adapter-level reason instead of falling back to "no matching event
    found."
    """
    blocks: list[str] = []
    in_span = since_marker is None
    found_until = False

    with Path(path).open("r", errors="ignore") as f:
        for i, line in enumerate(f):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue

            uuid = rec.get("uuid")
            if not in_span:
                if uuid == since_marker:
                    in_span = True
                continue

            blocks += [b.render() for b in claude_code_blocks(rec, f"L{i}")]

            if until_marker is not None and uuid == until_marker:
                found_until = True
                break

    if since_marker is not None and not in_span:
        raise ValueError(f"--since marker {since_marker!r} not found as a uuid in {path}")
    if until_marker is not None and not found_until:
        raise ValueError(f"--until marker {until_marker!r} not found as a uuid in {path}")

    return "\n\n".join(blocks) + "\n"


def dump_codex(path: Path, since_marker: str | None = None, until_marker: str | None = None) -> str:
    """Render every UserMessage/AgentMessage/Reasoning text block plus every
    compaction-boundary marker in a Codex rollout `path` as delimited plain
    text, optionally bounded to (since_marker, until_marker] -- same
    since/until semantics and same "raises ValueError for an unresolved
    marker" contract as `dump_claude_code`. Handles BOTH Codex schema
    generations (see adapters/codex.py's own docstring for the full
    pre/post cli_version 0.147.0 shape).

    Marker convention is IDENTICAL to adapters/codex.py's own: an integer
    `ordinal` when present on the record, else its absolute position within
    the filtered {event_msg, compacted} universe -- `_TOP_LEVEL_TYPES` is
    reused directly from adapters/codex.py (see this module's own docstring
    for why that specific piece of plumbing, and not the rest of parse(),
    is shared).

    Unlike `dump_claude_code`, THINKING content (Codex's "Reasoning" item,
    new generation only -- the old generation's `response_item`-layer
    reasoning is not read by this adapter family at all, see
    adapters/codex.py) is kept unconditionally here, same as
    `dump_claude_code` keeps `thinking` blocks unconditionally: lossless
    means lossless, `ExtractConfig.include_thinking` plays no part in
    either dumper.
    """
    from .adapters.codex import is_top_level_record

    filtered: list[dict] = []
    with Path(path).open("r", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if is_top_level_record(rec):
                filtered.append(rec)

    blocks: list[str] = []
    in_span = since_marker is None
    found_until = False

    for i, rec in enumerate(filtered):
        marker = str(rec.get("ordinal", i))
        if not in_span:
            if marker == since_marker:
                in_span = True
            continue

        blocks += [b.render() for b in codex_blocks(rec, marker)]

        if until_marker is not None and marker == until_marker:
            found_until = True
            break

    if since_marker is not None and not in_span:
        raise ValueError(f"--since marker {since_marker!r} not found as an ordinal in {path}")
    if until_marker is not None and not found_until:
        raise ValueError(f"--until marker {until_marker!r} not found as an ordinal in {path}")

    return "\n\n".join(blocks) + "\n"


def dump_opencode(
    path: Path, session_id: str, since_marker: str | None = None, until_marker: str | None = None
) -> str:
    """Render every `part.data.type == "text"` fragment of every
    user/assistant message in one opencode session (identified by
    `session_id`, since one opencode.db holds many sessions) as delimited
    plain text -- same since/until semantics and same "raises ValueError
    for an unresolved marker" contract as `dump_claude_code`.

    Reads the `message`/`part` tables directly via sqlite3, independent of
    adapters/opencode.py's own `parse()` for the classification logic (this
    module's usual rule) -- but mirrors that adapter's own query shape
    (table/column names, `ORDER BY time_created ASC, id ASC`) exactly,
    since getting THAT wrong would silently reorder or drop rows rather
    than just duplicate a classification decision.

    opencode's marker is always the message's own real DB id, never a
    positional fallback (adapters/opencode.py's own parse() uses the same
    id directly as NormalizedEvent.marker) -- no reindexing hazard to guard
    against here the way Codex's/Claude Code's own marker resolution does.
    """
    from .adapters.opencode import _db_path

    db = _db_path(Path(path))
    if db is None:
        raise ValueError(f"{path} is not an opencode SQLite store")

    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "SELECT id, time_created, data FROM message WHERE session_id = ? "
            "ORDER BY time_created ASC, id ASC",
            (session_id,),
        ).fetchall()

        blocks: list[str] = []
        in_span = since_marker is None
        found_until = False

        for msg_id, time_created, data_json in rows:
            if not in_span:
                if msg_id == since_marker:
                    in_span = True
                continue

            blocks += [b.render() for b in opencode_blocks(conn, msg_id, time_created, data_json)]

            if until_marker is not None and msg_id == until_marker:
                found_until = True
                break

        if since_marker is not None and not in_span:
            raise ValueError(f"--since marker {since_marker!r} not found in session {session_id}")
        if until_marker is not None and not found_until:
            raise ValueError(f"--until marker {until_marker!r} not found in session {session_id}")

        return "\n\n".join(blocks) + "\n"
    finally:
        conn.close()
