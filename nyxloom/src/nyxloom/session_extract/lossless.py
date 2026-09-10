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

Claude Code only today -- Codex's event_msg layer and opencode's SQLite
rows would each need their own dumper with the same "keep prose, drop
machine calls" rule; not built since neither has been exercised against a
real hand-curated reference the way Claude Code has.
"""

from __future__ import annotations

import json
from pathlib import Path

_KEEP_BLOCK_TYPES = {"text", "thinking"}
_BOOKKEEPING_TYPES = {
    "mode", "attachment", "bridge-session", "file-history-snapshot",
    "file-history-delta", "atis-latch", "last-prompt", "ai-title",
    "queue-operation", "cost-state",
}


def dump_claude_code(path: Path, since_marker: str | None = None, until_marker: str | None = None) -> str:
    """Render every text/thinking block in `path` as delimited plain text,
    optionally bounded to (since_marker, until_marker] by uuid (same marker
    convention as ExtractConfig.since_marker/until_marker). Raises
    ValueError if a given marker isn't found as a uuid in the file -- same
    contract as the adapters' own since/until handling, so a typo'd or
    wrong-file marker fails loudly rather than silently returning everything
    or nothing.
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

            rtype = rec.get("type")
            ts = rec.get("timestamp", "")

            if rtype == "system" and rec.get("subtype") == "compact_boundary":
                blocks.append(f"===[{i} | {ts} | SYSTEM compact_boundary]===\n{rec.get('content', '')}")
            elif rtype in ("user", "assistant"):
                msg = rec.get("message") or {}
                content = msg.get("content")
                role = (msg.get("role") or rtype).upper()
                tag = f"{role}{' isMeta' if rec.get('isMeta') else ''}"
                if isinstance(content, str):
                    if content.strip():
                        blocks.append(f"===[{i} | {ts} | {tag}]===\n{content}")
                elif isinstance(content, list):
                    for block in content:
                        if not isinstance(block, dict) or block.get("type") not in _KEEP_BLOCK_TYPES:
                            continue
                        text = block.get("text", "")
                        if text.strip():
                            blocks.append(f"===[{i} | {ts} | {tag} {block['type']}]===\n{text}")
            # else: bookkeeping or unrecognized record type -- no prose to lose.

            if until_marker is not None and uuid == until_marker:
                found_until = True
                break

    if since_marker is not None and not in_span:
        raise ValueError(f"--since marker {since_marker!r} not found as a uuid in {path}")
    if until_marker is not None and not found_until:
        raise ValueError(f"--until marker {until_marker!r} not found as a uuid in {path}")

    return "\n\n".join(blocks) + "\n"
