"""Session cost/timeline analysis -- `nyxloom session-stats`. V9 in
`nyxloom/docs/design-context-lifecycle-experiments.md` (E-009): every source
format already carries a full per-call token/cost ledger the extractor
itself never reads (it only needs event text, not usage). This module reads
that ledger and turns it into two views:

  - a DETAILED view, one row per real API call (`build_call_rows`), and
  - a CONDENSED view, one row per block of calls between prompt boundaries
    (`build_blocks`), meant to fit a whole session on one page.

Both are annotated with what `select.select()` would have kept, under each
of a small set of named `PROFILES`, if an extraction had been triggered at
that exact point -- so the timeline doubles as a "what would our extractor
have produced here" simulator (`_simulate_profile`), not just a usage log.

Claude Code only today. Codex's `event_msg.token_count` and opencode's
`message.data.tokens`/`cost` carry the equivalent data (confirmed real,
E-009) but aren't wired up here yet -- left honest rather than guessed,
same convention as every other adapter's "Known gaps" section.

No --since/--until support yet: this always reads the FULL, unsliced
session. Marker fallback (`rec.get("uuid") or f"line{i}"`) therefore only
needs to match `adapters/claude_code.py`'s own fallback for an UNSLICED
parse (config.since_marker=None) -- the absolute-position fix in that
adapter (commit `9d2f06ae`) doesn't need duplicating here for that reason;
if --since support is ever added here, it does.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import classifier
from .adapters import claude_code, detect
from .config import ExtractConfig
from .events import EventKind, NormalizedEvent
from .select import select

# Named compression-profile presets (open design question in
# session_extract/README.md, "Named compression profiles" -- these are a
# first-pass definition, not a settled taxonomy). "manual_fresh" matches
# the real --max-lifecycle-markers -1 --max-words 8000 run validated
# against the real dstdns session (E-009 follow-up).
PROFILES: dict[str, ExtractConfig] = {
    "tight": ExtractConfig(max_checkpoints=3, max_words=4_000, long_comment_chars=240),
    "default": ExtractConfig(),
    "manual_fresh": ExtractConfig(max_checkpoints=1_000_000, max_words=8_000, max_lifecycle_markers=-1),
}


@dataclass
class CallRow:
    """One real API call (an `assistant`-type record with a `usage` block)
    plus the OPERATOR_TEXT/QA_PAIR/LIFECYCLE_MARKER record immediately
    preceding it, if any -- Claude Code turns are (prompt, response) pairs
    on disk, and a cost row is meaningless without knowing what triggered it.
    """

    marker: str
    timestamp: str
    elapsed_since_prev_s: float | None
    kind: str  # "operator" | "qa" | "lifecycle" | "checkpoint" | "assistant_minor" | "thinking_only"
    text_preview: str  # first 80 chars of whatever text this row carries, for a human-readable view
    words: int
    input_tokens: int
    cache_creation_tokens: int
    cache_creation_1h_tokens: int
    cache_creation_5m_tokens: int
    cache_read_tokens: int
    output_tokens: int
    thinking_tokens: int
    model: str | None
    effort: str | None
    tools_called: list[str] = field(default_factory=list)
    checkpoint_score: float | None = None
    is_real_lifecycle: bool = False
    compact_trigger: str | None = None
    compact_pre_tokens: int | None = None
    compact_post_tokens: int | None = None
    compact_duration_ms: int | None = None
    # profile name -> cumulative kept-word-count of select()'s walk if it
    # had been triggered starting from the newest row down to (and
    # including) this one; None if this row would not have survived under
    # that profile at all.
    profile_running_words: dict[str, int | None] = field(default_factory=dict)

    @property
    def context_size(self) -> int:
        return self.input_tokens + self.cache_creation_tokens + self.cache_read_tokens


@dataclass
class Block:
    """One span between prompt boundaries -- a human "turn" of the session:
    an OPERATOR_TEXT/QA_PAIR row (or the very start of the file) through the
    row right before the NEXT OPERATOR_TEXT/QA_PAIR/LIFECYCLE_MARKER row.
    """

    trigger_text_preview: str  # verbatim preview of whatever opened this block -- see module docstring
    trigger_kind: str
    start_ts: str
    end_ts: str
    n_calls: int
    n_checkpoints: int
    n_minor_updates: int  # non-checkpoint ASSISTANT_TEXT rows -- "mini prose status updates"
    tool_call_counts: dict[str, int]
    sum_input_tokens: int
    sum_cache_creation_tokens: int
    sum_cache_read_tokens: int
    sum_output_tokens: int
    peak_context_size: int
    contains_real_lifecycle_marker: bool


def _parse_ts(ts: str) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


def _claude_code_raw_scan(path: Path) -> list[dict]:
    """One pass over the raw JSONL, unfiltered by record type -- mirrors
    `adapters/claude_code.py._load_records` but keeps every record (incl.
    `mode`/bookkeeping types this package's extractor ignores) since a
    usage/cost view cares about call cadence, not just kept prose.
    """
    records: list[dict] = []
    with path.open("r", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return records


def _event_kind_str(ev: NormalizedEvent, checkpoint_threshold: float) -> str:
    if ev.kind is EventKind.OPERATOR_TEXT:
        return "operator"
    if ev.kind is EventKind.QA_PAIR:
        return "qa"
    if ev.kind is EventKind.LIFECYCLE_MARKER:
        return "lifecycle"
    if ev.kind is EventKind.THINKING:
        return "thinking_only"
    if (ev.checkpoint_score or 0.0) >= checkpoint_threshold:
        return "checkpoint"
    return "assistant_minor"


def _simulate_profile(events: list[NormalizedEvent], config: ExtractConfig) -> dict[str, int]:
    """Duplicates select()'s own walk (newest -> oldest), instrumented to
    record the CUMULATIVE kept-word-count at every marker instead of only
    the final kept list -- select.py itself stays untouched/uncoupled from
    this analysis feature. Kept logic must track select.select() exactly;
    if that function's windowing rules change, this needs the same change.
    """
    running: dict[str, int] = {}
    checkpoints_found = 0
    word_count = 0
    markers_passed = 0

    for ev in reversed(events):
        if ev.kind is EventKind.LIFECYCLE_MARKER:
            running[ev.marker] = word_count
            may_pass = config.max_lifecycle_markers == -1 or markers_passed < config.max_lifecycle_markers
            if not may_pass:
                break
            markers_passed += 1
            continue

        if ev.kind in (EventKind.OPERATOR_TEXT, EventKind.QA_PAIR):
            word_count += len(ev.text.split())
            running[ev.marker] = word_count

        elif ev.kind in (EventKind.ASSISTANT_TEXT, EventKind.THINKING):
            is_checkpoint = (
                ev.kind is EventKind.ASSISTANT_TEXT
                and (ev.checkpoint_score or 0.0) >= config.checkpoint_score_threshold
            )
            if is_checkpoint:
                checkpoints_found += 1
                word_count += len(ev.text.split())
                running[ev.marker] = word_count
                if checkpoints_found >= config.max_checkpoints:
                    break
            elif len(ev.text) > config.long_comment_chars or classifier.has_finding_signal(ev.text):
                word_count += len(ev.text.split())
                running[ev.marker] = word_count

        if word_count > config.max_words:
            break

    return running


def build_call_rows(path: Path, fmt: str | None = None, session_id: str | None = None) -> list[CallRow]:
    """Claude Code only -- see module docstring. Raises NotImplementedError
    for any other detected format rather than silently returning nothing.
    """
    path = Path(path)
    resolved_fmt = fmt or detect(path).name
    if resolved_fmt != "claude-code":
        raise NotImplementedError(
            f"session-stats only supports claude-code today, not {resolved_fmt!r} "
            "-- see stats.py's module docstring"
        )

    sessions = claude_code.list_sessions(path)
    if session_id is None:
        session_id = sessions[0] if len(sessions) == 1 else str(path)

    config = ExtractConfig()
    events = claude_code.parse(path, session_id, config)
    classifier.score_events(events)
    checkpoint_threshold = config.checkpoint_score_threshold

    profile_running = {name: _simulate_profile(events, cfg) for name, cfg in PROFILES.items()}

    raw_by_marker: dict[str, dict] = {}
    for i, rec in enumerate(_claude_code_raw_scan(path)):
        marker = rec.get("uuid") or f"line{i}"
        raw_by_marker[marker] = rec

    rows: list[CallRow] = []
    prev_ts: datetime | None = None

    for ev in events:
        rec = raw_by_marker.get(ev.marker, {})
        msg = rec.get("message") or {}
        usage = msg.get("usage") or {}
        cache_creation = usage.get("cache_creation") or {}
        content = msg.get("content") or []
        tools_called = [
            b.get("name", "") for b in content
            if isinstance(b, dict) and b.get("type") == "tool_use"
        ]

        ts = _parse_ts(ev.timestamp)
        elapsed = (ts - prev_ts).total_seconds() if ts and prev_ts else None
        if ts:
            prev_ts = ts

        compact_meta = rec.get("compactMetadata") or {}

        row = CallRow(
            marker=ev.marker,
            timestamp=ev.timestamp,
            elapsed_since_prev_s=elapsed,
            kind=_event_kind_str(ev, checkpoint_threshold),
            text_preview=ev.text[:80].replace("\n", " "),
            words=len(ev.text.split()),
            input_tokens=usage.get("input_tokens", 0) or 0,
            cache_creation_tokens=usage.get("cache_creation_input_tokens", 0) or 0,
            cache_creation_1h_tokens=cache_creation.get("ephemeral_1h_input_tokens", 0) or 0,
            cache_creation_5m_tokens=cache_creation.get("ephemeral_5m_input_tokens", 0) or 0,
            cache_read_tokens=usage.get("cache_read_input_tokens", 0) or 0,
            output_tokens=usage.get("output_tokens", 0) or 0,
            thinking_tokens=(usage.get("output_tokens_details") or {}).get("thinking_tokens", 0) or 0,
            model=msg.get("model"),
            effort=rec.get("effort"),
            tools_called=tools_called,
            checkpoint_score=ev.checkpoint_score,
            is_real_lifecycle=ev.kind is EventKind.LIFECYCLE_MARKER and bool(compact_meta),
            compact_trigger=compact_meta.get("trigger"),
            compact_pre_tokens=compact_meta.get("preTokens"),
            compact_post_tokens=compact_meta.get("postTokens"),
            compact_duration_ms=compact_meta.get("durationMs"),
            profile_running_words={name: profile_running[name].get(ev.marker) for name in PROFILES},
        )
        rows.append(row)

    return rows


_BOUNDARY_KINDS = {"operator", "qa", "lifecycle"}


def build_blocks(rows: list[CallRow]) -> list[Block]:
    """Groups rows into spans between prompt boundaries. A block always
    STARTS at a boundary row (operator/qa/lifecycle) and runs through every
    row up to (not including) the next boundary row -- so a block reads as
    "what happened in response to this one trigger."
    """
    blocks: list[Block] = []
    current: list[CallRow] = []

    def flush() -> None:
        if not current:
            return
        trigger = current[0]
        tool_counts: dict[str, int] = {}
        for r in current:
            for t in r.tools_called:
                tool_counts[t] = tool_counts.get(t, 0) + 1
        blocks.append(Block(
            trigger_text_preview=trigger.text_preview,
            trigger_kind=trigger.kind,
            start_ts=current[0].timestamp,
            end_ts=current[-1].timestamp,
            n_calls=len(current),
            n_checkpoints=sum(1 for r in current if r.kind == "checkpoint"),
            n_minor_updates=sum(1 for r in current if r.kind == "assistant_minor"),
            tool_call_counts=tool_counts,
            sum_input_tokens=sum(r.input_tokens for r in current),
            sum_cache_creation_tokens=sum(r.cache_creation_tokens for r in current),
            sum_cache_read_tokens=sum(r.cache_read_tokens for r in current),
            sum_output_tokens=sum(r.output_tokens for r in current),
            peak_context_size=max((r.context_size for r in current), default=0),
            contains_real_lifecycle_marker=any(r.is_real_lifecycle for r in current),
        ))

    for row in rows:
        if row.kind in _BOUNDARY_KINDS and current:
            flush()
            current = [row]
        else:
            current.append(row)
    flush()

    return blocks


def render_detailed_csv(rows: list[CallRow]) -> str:
    """One line per real API call. Profile columns show the cumulative
    kept-word-count select() would report if extraction had been triggered
    starting from the newest row down to this one, under that profile --
    blank if this row wouldn't have survived that profile's bar at all.
    """
    import csv
    import io

    profile_names = sorted(PROFILES)
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow([
        "marker", "timestamp", "elapsed_since_prev_s", "kind", "text_preview", "words",
        "input_tokens", "cache_creation_tokens", "cache_creation_1h_tokens",
        "cache_creation_5m_tokens", "cache_read_tokens", "output_tokens", "thinking_tokens",
        "context_size", "model", "effort", "tools_called", "checkpoint_score",
        "is_real_lifecycle", "compact_trigger", "compact_pre_tokens", "compact_post_tokens",
        "compact_duration_ms",
    ] + [f"profile_{p}_words" for p in profile_names])
    for r in rows:
        w.writerow([
            r.marker, r.timestamp,
            f"{r.elapsed_since_prev_s:.1f}" if r.elapsed_since_prev_s is not None else "",
            r.kind, r.text_preview, r.words,
            r.input_tokens, r.cache_creation_tokens, r.cache_creation_1h_tokens,
            r.cache_creation_5m_tokens, r.cache_read_tokens, r.output_tokens, r.thinking_tokens,
            r.context_size, r.model or "", r.effort or "", ";".join(r.tools_called),
            f"{r.checkpoint_score:.2f}" if r.checkpoint_score is not None else "",
            r.is_real_lifecycle, r.compact_trigger or "", r.compact_pre_tokens or "",
            r.compact_post_tokens or "", r.compact_duration_ms or "",
        ] + [
            r.profile_running_words.get(p) if r.profile_running_words.get(p) is not None else ""
            for p in profile_names
        ])
    return out.getvalue()


def render_condensed(blocks: list[Block]) -> str:
    """One line per block, meant to fit a whole session's shape on one page.
    A LIFECYCLE_MARKER-triggered block gets its own visible divider, since
    that's the one boundary this package treats as structurally different
    from an ordinary prompt.
    """
    lines = [
        f"{'#':>3}  {'trigger':<9} {'calls':>5} {'cp':>3} {'minor':>5} "
        f"{'in':>8} {'cache_w':>8} {'cache_r':>8} {'out':>7} {'peak_ctx':>9}  trigger text",
        "-" * 110,
    ]
    for i, b in enumerate(blocks, 1):
        if b.contains_real_lifecycle_marker:
            lines.append(f"{'':>3}  {'=' * 104}  REAL COMPACTION")
        marker = "**" if b.trigger_kind == "lifecycle" else ""
        lines.append(
            f"{i:>3}  {b.trigger_kind:<9} {b.n_calls:>5} {b.n_checkpoints:>3} {b.n_minor_updates:>5} "
            f"{b.sum_input_tokens:>8} {b.sum_cache_creation_tokens:>8} {b.sum_cache_read_tokens:>8} "
            f"{b.sum_output_tokens:>7} {b.peak_context_size:>9}  {marker}{b.trigger_text_preview}"
        )
    totals = Block(
        trigger_text_preview="TOTAL", trigger_kind="", start_ts="", end_ts="",
        n_calls=sum(b.n_calls for b in blocks),
        n_checkpoints=sum(b.n_checkpoints for b in blocks),
        n_minor_updates=sum(b.n_minor_updates for b in blocks),
        tool_call_counts={},
        sum_input_tokens=sum(b.sum_input_tokens for b in blocks),
        sum_cache_creation_tokens=sum(b.sum_cache_creation_tokens for b in blocks),
        sum_cache_read_tokens=sum(b.sum_cache_read_tokens for b in blocks),
        sum_output_tokens=sum(b.sum_output_tokens for b in blocks),
        peak_context_size=max((b.peak_context_size for b in blocks), default=0),
        contains_real_lifecycle_marker=False,
    )
    lines.append("-" * 110)
    lines.append(
        f"{'':>3}  {'':<9} {totals.n_calls:>5} {totals.n_checkpoints:>3} {totals.n_minor_updates:>5} "
        f"{totals.sum_input_tokens:>8} {totals.sum_cache_creation_tokens:>8} "
        f"{totals.sum_cache_read_tokens:>8} {totals.sum_output_tokens:>7} {totals.peak_context_size:>9}  "
        f"({len(blocks)} blocks)"
    )
    return "\n".join(lines) + "\n"

    return blocks
