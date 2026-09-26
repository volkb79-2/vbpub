"""Turn a selected event list into output: delimited text (paste-ready for
a fresh session) or JSON (for a second-stage script).

Both formats embed the true end-of-session marker (from the FULL parse,
not just what survived selection) so a later run can be pointed at THIS
output file as --since-file and pick up exactly where it left off --
finding the delta border by reading the marker back out, not by re-typing
or diffing text. See __init__.py's read_since_marker().

Also surfaces the two annotations select.py attaches via NormalizedEvent.meta
(see select.py's own module docstring for the full design rationale,
design-context-lifecycle-experiments.md's E-011): a "gap_after" note between
two kept events that weren't actually time-adjacent, and a leading
"walk_stopped_because" note when the OLDEST kept event isn't the true start
of the session and isn't a LIFECYCLE_MARKER either (a marker's own kept text
already explains itself -- see select.py). Both are purely cosmetic in text
mode (bracketed notes, not real content) and typed fields in JSON mode.

Both notes default to a bare count ("[gap: 20 records omitted]") -- an
operator's own read against real output (2026-09-10) found the first draft's
per-occurrence explanation ("...short/procedural content or tool activity
not kept") was pure repeated bloat once a real session produced dozens of
these; the explanation belongs once, here, not N times in the rendered
text. `show_gap_marker=True` opts into a longer form that names the
adapter's own opaque marker token bounding the gap ("...raw log continues
after marker <marker>") -- the SAME token --since/--until already resolve
against, so it generalizes across all four adapters for free: a Claude
Code marker is a uuid (or a `lineN` fallback) grep-able in the JSONL, a
Codex marker is an ordinal (or a legacy numeric / `response_item-<position>`
fallback) likewise grep-able, a
Reasonix marker is a `lineN` position in its JSONL, and an opencode marker
is a message-table row id queryable against its SQLite store. Nothing new
to build per-adapter -- this reuses the exact identifier
each adapter already mints for delta extraction, just surfaced to the
reader as a "go look here" pointer instead of consumed internally by
--since. Off by default because most readers most of the time don't need
to actually go recover the omitted content, only to know it existed.

Text-mode block format (2026-09-10, operator feedback against a real render):
a `## [timestamp] LABEL` header per block was measured as pure bloat once
gap/stop-reason notes made the output long enough to matter -- every kind's
own text is unambiguous without it (assistant prose reads as assistant
prose; a LIFECYCLE_MARKER's own text is already self-labeling, e.g. "[compact
boundary]"; the bracketed nyxloom-authored notes above read as nyxloom's own
commentary, not transcript). The one thing genuinely worth keeping inline is
which lines are the OPERATOR's own words versus everything else -- that's
the actual decision-relevant distinction (README's own "structured-Q&A-
preserving" framing: an operator's real input is the highest-signal content
in a session). So OPERATOR_TEXT gets a bare `OPERATOR: ` prefix directly on
the text; every other kind renders as plain, unprefixed text. Checkpoint-vs-not
Checkpoint scores remain JSON/debug metadata. Text mode optionally places
source timestamps inline before, after, both, or neither; the default CLI
policy is pre-event timestamps, while the lower-level renderer can omit them.
The placement does not change which content selection keeps.

**QA_PAIR is deliberately EXCLUDED from that blanket prefix** (corrected
2026-09-10, later the same day, against a real rendered session): when
QA_PAIR's `.text` was still the harness's raw flattened blob, a single
leading `OPERATOR: ` was the whole story. Once `claude_code.py`'s
`_format_qa_pairs` started reconstructing the real per-question shape
(question text, its option bullets, then `OPERATOR: <answer>` -- repeated
per question in a multi-question batch, blank line between blocks), that
`OPERATOR: ` label already lives INSIDE the formatted text, correctly
placed before each answer and absent before each question. Also blanket-
prefixing the kind duplicated it onto the QUESTION line instead ("OPERATOR:
<question>\n- opt\n...\n\nOPERATOR: <answer>") and couldn't express more
than one label for a multi-question batch either way.

Optional `block_render` param (2026-09-12): a per-block text->text hook
applied to each kept event's OWN PROSE only, before blocks are joined. This
is where `extract --render-markdown` (render_markdown.py, via `rich`) and
`extract --highlight` (highlight.py, via `pygments`) plug in, and it is
    deliberately narrow: the `---` separators, the bracketed gap/stop-reason
    notes this module authors itself, the ledger line, and positioned
    `<!-- nyxloom-extract: ... -->` cursor comments are NOT passed through it. Running
the whole rendered output through a markdown renderer instead would mangle
exactly that scaffolding -- a `---` line is a horizontal rule, an HTML
    comment vanishes -- and the cursor is machine-read by read_since_marker(),
    so it must survive byte-exact. Neither dependency is imported here; the
caller passes a callable.

Optional `ledger` param (E-012, `ledger.py`): a dict keyed by boundary
marker -- when given, `render_text` inserts that boundary's rendered
`[files read: ...] [files edited: ...] [commits created: ...] [branches
involved: ...] [tests: ...]` line right after the boundary's own text, only
for the categories that actually have entries. Off by default (`ledger=None`
skips this entirely -- opt-in, per E-012's own "config-gated" framing), and
text-mode only; JSON mode has no equivalent yet.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from datetime import datetime, timezone
from urllib.parse import quote

from .events import EventKind, NormalizedEvent
from .ledger import Ledger

MARKER_FOOTER_RE = re.compile(
    r"^<!-- nyxloom-extract: format=(\S+) marker=(\S+)(?P<attrs>(?:\s+[^>]*?)?) -->$",
    re.MULTILINE,
)

# select.py's meta["walk_stopped_because"] values -> the reader-facing
# explanation of why selection stopped before reaching the real start of
# the session. Keep in sync with select.py's own set of stop reasons.
_STOP_REASON_TEXT = {
    "max_words": "word budget reached (--max-words)",
    "max_checkpoints": "checkpoint target reached (--max-checkpoints)",
    "max_compactions": "compaction limit reached (--max-compactions)",
    "max_time_minutes": "time window reached (--max-time-minutes)",
}

# Kinds whose text IS the operator's own words -- see the module docstring
# above for why this is the one distinction worth keeping inline in text
# mode, and why QA_PAIR is deliberately NOT here (its INTERVIEW: and, when
# present, OPERATOR: labels are already embedded in its text).
_USER_AUTHORED = (EventKind.OPERATOR_TEXT,)

# Kinds a `ledger` dict (E-012, ledger.py) is keyed by -- the same boundary
# concept stats.py's Block groups by, extended to LIFECYCLE_MARKER too (a
# real compaction's own "since the last boundary" tool activity is just as
# aggregation-worthy as an operator turn's).
_LEDGER_BOUNDARY_KINDS = (EventKind.OPERATOR_TEXT, EventKind.QA_PAIR, EventKind.LIFECYCLE_MARKER)


def _gap_note(ev: NormalizedEvent, min_gap_to_annotate: int, show_marker: bool) -> str | None:
    """"full" gap_marker_mode's own STANDALONE block -- see render_text."""
    raw = ev.meta.get("gap_after")
    if not raw or int(raw) < min_gap_to_annotate:
        return None
    unit = "record" if raw == "1" else "records"
    if show_marker:
        return f"[gap: {raw} {unit} omitted -- raw log continues after marker {ev.marker}]"
    return f"[gap: {raw} {unit} omitted]"


# The three gap_marker_mode values embedded IN the block separator's dashes
# (config.py's own gap_marker_mode comment has the full picture) -- "full"
# and "none" don't use this table: "full" builds its own standalone block
# via _gap_note above, "none" suppresses the gap entirely.
_GAP_INLINE_TEXT = {
    "inline": lambda raw, unit: f"[gap: {raw} {unit} omitted]",
    "inline2": lambda raw, unit: f"... {raw}x ...",
    "inline-short": lambda raw, unit: "...",
}


def _gap_inline_text(ev: NormalizedEvent, min_gap_to_annotate: int, gap_marker_mode: str, show_marker: bool) -> str | None:
    if gap_marker_mode not in _GAP_INLINE_TEXT:
        return None
    raw = ev.meta.get("gap_after")
    if not raw or int(raw) < min_gap_to_annotate:
        return None
    unit = "record" if raw == "1" else "records"
    text = _GAP_INLINE_TEXT[gap_marker_mode](raw, unit)
    if show_marker:
        text = f"{text} -- raw log continues after marker {ev.marker}"
    return text


def separator(insert_blank_lines: int, inline_text: str | None = None) -> str:
    """The block-join separator -- see config.py's insert_blank_lines
    comment for the -1/0/N>=1 semantics. Public (2026-09-12) because
    follow.py separates live-streamed blocks with the identical marker. inline_text, when given, is
    embedded between the marker's two dash groups ("--- <text> ---")
    instead of a bare "---" -- gap_marker_mode's inline/inline2/inline-short
    modes (see _gap_inline_text above)."""
    marker = f"--- {inline_text} ---" if inline_text else "---"
    if insert_blank_lines == -1:
        return f" {marker}\n"
    pad = "\n" * (insert_blank_lines + 1)
    return f"{pad}{marker}{pad}"


def _format_timestamp(raw: str, timestamp_format: str) -> str | None:
    if not raw:
        return None
    try:
        value = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).strftime(timestamp_format)


def render_event_block(
    ev: NormalizedEvent,
    block_render: Callable[[str], str] | None = None,
    show_timestamps: str = "none",
    timestamp_format: str = "[%H:%M:%S]",
) -> str:
    """One kept event's own rendered text block -- the `OPERATOR: ` prefix
    rule (see the module docstring for why that one label, and why QA_PAIR is
    excluded from it) plus the optional per-block render hook, applied to the
    event's prose only, never to the prefix. Shared with follow.py so a live
    stream and a one-shot render can't drift on either decision."""
    text = block_render(ev.text) if block_render is not None else ev.text
    prefix = "OPERATOR: " if ev.kind in _USER_AUTHORED else ""
    timestamp = _format_timestamp(ev.timestamp, timestamp_format)
    if timestamp is None or show_timestamps == "none":
        return f"{prefix}{text}"
    if show_timestamps in ("pre", "both"):
        return f"{prefix}{timestamp} {text}" if show_timestamps == "pre" else f"{prefix}{timestamp} {text} {timestamp}"
    return f"{prefix}{text} {timestamp}"


def _metadata_comment(
    fmt: str, marker: str, metadata: dict[str, str] | None = None,
    metadata_position: str = "both",
) -> str:
    attrs = ""
    if metadata:
        pairs = [
            ("source", metadata.get("path", "")),
            ("name", metadata.get("name", "")),
            ("bytes", metadata.get("bytes", "")),
            ("created", metadata.get("created", "unavailable")),
        ]
        attrs = " " + " ".join(f"{key}={quote(value, safe='/._-:TZ') or '-'}" for key, value in pairs)
    attrs += f" placement={metadata_position}"
    return f"<!-- nyxloom-extract: format={fmt} marker={marker}{attrs} -->"


def render_text(
    events: list[NormalizedEvent],
    fmt: str,
    last_marker: str | None,
    min_gap_to_annotate: int = 3,
    show_gap_marker: bool = False,
    ledger: dict[str, Ledger] | None = None,
    insert_blank_lines: int = 1,
    gap_marker_mode: str = "full",
    block_render: Callable[[str], str] | None = None,
    show_timestamps: str = "none",
    timestamp_format: str = "[%H:%M:%S]",
    metadata_position: str = "both",
    source_metadata: dict[str, str] | None = None,
) -> str:
    plain_sep = separator(insert_blank_lines)
    parts: list[str] = []
    seps: list[str] = []
    pending_sep: str | None = None  # a gap-embedding separator queued by the
    # PREVIOUS block, consumed by the next _add() call (or left unused if
    # there is no next block -- harmless, nothing left to separate).

    def _add(text: str) -> None:
        nonlocal pending_sep
        if parts:
            seps.append(pending_sep if pending_sep is not None else plain_sep)
        pending_sep = None
        parts.append(text)

    if events:
        stop_reason = events[0].meta.get("walk_stopped_because")
        if stop_reason:
            explanation = _STOP_REASON_TEXT.get(stop_reason, stop_reason)
            note = f"[older session content exists but was not included -- {explanation}]"
            if show_gap_marker:
                note = note[:-1] + f"; raw log continues before marker {events[0].marker}]"
            _add(note)
    last_epoch: str | None = None
    for ev in events:
        epoch = ev.meta.get("epoch")
        if epoch is not None and epoch != last_epoch and int(ev.meta.get("epoch_count", "1")) > 1:
            _add(f"[epoch {epoch}/{ev.meta.get('epoch_count', '1')}]")
            last_epoch = epoch
        _add(render_event_block(ev, block_render, show_timestamps, timestamp_format))
        if ledger is not None and ev.kind in _LEDGER_BOUNDARY_KINDS:
            entry = ledger.get(ev.marker)
            if entry and not entry.is_empty():
                _add(entry.render())
        if gap_marker_mode == "full":
            gap = _gap_note(ev, min_gap_to_annotate, show_gap_marker)
            if gap:
                _add(gap)
        elif gap_marker_mode != "none":
            inline_text = _gap_inline_text(ev, min_gap_to_annotate, gap_marker_mode, show_gap_marker)
            if inline_text:
                pending_sep = separator(insert_blank_lines, inline_text)

    body = parts[0] if parts else ""
    for text, sep in zip(parts[1:], seps):
        body += sep + text
    body += "\n"
    if last_marker is None:
        return body
    marker = _metadata_comment(fmt, last_marker, source_metadata, metadata_position)
    if metadata_position in ("pre", "both"):
        body = marker + "\n" + body
    if metadata_position in ("post", "both"):
        body += f"\n{marker}\n"
    return body


def render_json(
    events: list[NormalizedEvent], checkpoint_threshold: float, fmt: str,
    last_marker: str | None, source_metadata: dict[str, str] | None = None,
) -> str:
    payload = {
        "format": fmt,
        "stop_reason": events[0].meta.get("walk_stopped_because") if events else None,
        "events": [
            {
                "timestamp": ev.timestamp,
                "kind": ev.kind.value,
                "checkpoint": (ev.checkpoint_score or 0.0) >= checkpoint_threshold
                if ev.kind is EventKind.ASSISTANT_TEXT
                else False,
                "marker": ev.marker,
                "text": ev.text,
                "gap_after": int(ev.meta["gap_after"]) if ev.meta.get("gap_after") else 0,
                "epoch": int(ev.meta["epoch"]) if ev.meta.get("epoch") else None,
            }
            for ev in events
        ],
        "last_marker": last_marker,
        "source": source_metadata,
    }
    return json.dumps(payload, indent=2)
