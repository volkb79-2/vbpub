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
against, so it generalizes across all three adapters for free: a Claude
Code marker is a uuid (or a `lineN` fallback) grep-able in the JSONL, a
Codex marker is an ordinal (or an index fallback) likewise grep-able, an
opencode marker is a message-table row id queryable against its SQLite
store. Nothing new to build per-adapter -- this reuses the exact identifier
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
and per-event timestamps are dropped from text mode entirely (they were never
load-bearing for a human/LLM reading the rendered brief) but remain full-
fidelity fields in JSON mode, which is for a second-stage tool, not a paste
target -- terseness there would cost correctness for no reader benefit.

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

An OPERATOR_TEXT/QA_PAIR block immediately followed by a LIFECYCLE_MARKER in
select.py's own pre-selection pass (meta["swallowed_by_compaction"], set
whenever no ASSISTANT_TEXT/THINKING event ran before the boundary) gets a
`[response follows after compaction]` note right after its own text --
without it, a reader sees an operator instruction followed immediately by a
compaction marker and reasonably reads that as "this got dropped." A real
dstdns session (2026-09-10) showed the opposite: the very next assistant
turn after the compact summary acted on the instruction directly. The note
only ever points forward to the next block; it does not (yet) try to tell
apart the rarer case where nothing ever follows at all.

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

from .events import EventKind, NormalizedEvent
from .ledger import Ledger

MARKER_FOOTER_RE = re.compile(r"^<!-- nyxloom-extract: format=(\S+) marker=(\S+) -->$", re.MULTILINE)

# select.py's meta["walk_stopped_because"] values -> the reader-facing
# explanation of why selection stopped before reaching the real start of
# the session. Keep in sync with select.py's own set of stop reasons.
_STOP_REASON_TEXT = {
    "max_words": "word budget reached (--max-words / a profile's max_words)",
    "max_checkpoints": "checkpoint target reached (--checkpoints / max_checkpoints)",
}

# Kinds whose text IS the operator's own words -- see the module docstring
# above for why this is the one distinction worth keeping inline in text
# mode, and why QA_PAIR is deliberately NOT here (its own OPERATOR: label(s)
# are already embedded in the text by claude_code.py's _format_qa_pairs).
_USER_AUTHORED = (EventKind.OPERATOR_TEXT,)

# Kinds a `ledger` dict (E-012, ledger.py) is keyed by -- the same boundary
# concept stats.py's Block groups by, extended to LIFECYCLE_MARKER too (a
# real compaction's own "since the last boundary" tool activity is just as
# aggregation-worthy as an operator turn's).
_LEDGER_BOUNDARY_KINDS = (EventKind.OPERATOR_TEXT, EventKind.QA_PAIR, EventKind.LIFECYCLE_MARKER)


def _gap_note(ev: NormalizedEvent, min_gap_to_annotate: int, show_marker: bool) -> str | None:
    raw = ev.meta.get("gap_after")
    if not raw or int(raw) < min_gap_to_annotate:
        return None
    unit = "record" if raw == "1" else "records"
    if show_marker:
        return f"[gap: {raw} {unit} omitted -- raw log continues after marker {ev.marker}]"
    return f"[gap: {raw} {unit} omitted]"


def render_text(
    events: list[NormalizedEvent],
    fmt: str,
    last_marker: str | None,
    min_gap_to_annotate: int = 3,
    show_gap_marker: bool = False,
    ledger: dict[str, Ledger] | None = None,
) -> str:
    blocks = []
    if events:
        stop_reason = events[0].meta.get("walk_stopped_because")
        if stop_reason:
            explanation = _STOP_REASON_TEXT.get(stop_reason, stop_reason)
            note = f"[older session content exists but was not included -- {explanation}]"
            if show_gap_marker:
                note = note[:-1] + f"; raw log continues before marker {events[0].marker}]"
            blocks.append(note)
    for ev in events:
        prefix = "OPERATOR: " if ev.kind in _USER_AUTHORED else ""
        blocks.append(f"{prefix}{ev.text}")
        if ev.meta.get("swallowed_by_compaction"):
            blocks.append("[response follows after compaction]")
        if ledger is not None and ev.kind in _LEDGER_BOUNDARY_KINDS:
            entry = ledger.get(ev.marker)
            if entry and not entry.is_empty():
                blocks.append(entry.render())
        gap = _gap_note(ev, min_gap_to_annotate, show_gap_marker)
        if gap:
            blocks.append(gap)
    body = "\n\n---\n\n".join(blocks) + "\n"
    if last_marker is None:
        return body
    return body + f"\n<!-- nyxloom-extract: format={fmt} marker={last_marker} -->\n"


def render_json(events: list[NormalizedEvent], checkpoint_threshold: float, fmt: str, last_marker: str | None) -> str:
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
            }
            for ev in events
        ],
        "last_marker": last_marker,
    }
    return json.dumps(payload, indent=2)
