"""Scored "is this ASSISTANT_TEXT a checkpoint" classifier.

Length alone is a poor signal (checked against 651 real long-form assistant
messages mined from this estate's own vbpub session history: median length
of ALL long text blocks is dominated by mid-work asides, not status
updates). What actually recurs across genuine checkpoint/summary-style
messages is a small set of content families:

  - a markdown-header opening line ("## Where things actually stand",
    "## Compaction prompt", "## 1. Orientation manifest")
  - closure/verdict declaratives ("Done -- ...", "Landed.", "Verified
    everything against real code.", "All four parts landed", "<N> commits,
    main clean", "<N> passed / <M> failed")
  - explicit meta-compaction markers (the model naming its own checkpoint:
    "Paste this as the /compact argument:", "compaction prompt")
  - direct-address openers answering a question head-on ("Short answer:",
    "You're right...", "Good catch", "I followed it well. Here's my read:")

A fifth, structural signal was added on operator feedback: a checkpoint is
usually followed by a pause -- the very next real event in the session is
an operator prompt or a Q&A exchange, not more of the same assistant turn's
tool calls continuing. That requires a lookahead across the full event
list, which is why classification runs once, after a full parse, rather
than adapter-side.

None of this is validated against a labeled corpus yet -- these weights are
a first-pass, real-data-informed starting point, in the same spirit as
jsonl-metrics.py's own boundary-detection heuristics (E-008), which needed
three rounds of repair once run against real transcripts. Expect to retune
WEIGHTS from evidence, not replace the shape of this approach.
"""

from __future__ import annotations

import re

from .events import EventKind, NormalizedEvent

_HEADER_RE = re.compile(r"^#{1,6}\s+\S")

_CLOSURE_RE = re.compile(
    r"^(Done\b|Landed\.|Shipped\b|Verified\b|Investigation complete\.|"
    r"Committed\b|Cleanly committed\b|All (three|four|five|six|\d+)\b|"
    r"Both (landed|packages)\b)",
    re.IGNORECASE,
)
_CLOSURE_MID_RE = re.compile(
    r"\bmain clean\b|\btree clean\b|\b\d+ passed\b|\b\d+ commits?\b|"
    r"\bVerified everything\b|\bVerdict saved\b",
    re.IGNORECASE,
)

_META_COMPACT_RE = re.compile(
    r"/compact\b|compaction prompt|paste this as the\b",
    re.IGNORECASE,
)

_DIRECT_ADDRESS_RE = re.compile(
    r"^(You're right\b|Short answer:|Good catch\b|Good news:?\b|"
    r"Good (scoping|catch) instinct\b|Here('|’)s (my|the|where) read)",
    re.IGNORECASE,
)

_LENGTH_FLOOR_CHARS = 800
_LENGTH_FLOOR_STEP = 1000
_LENGTH_FLOOR_WEIGHT = 1.0
_LENGTH_FLOOR_CAP = 3.0

WEIGHTS = {
    "header": 3.0,
    "closure": 2.0,
    "meta_compact": 2.0,
    "direct_address": 1.0,
    "followed_by_pause": 2.0,
}


def _shape_score(text: str) -> float:
    first_line = text.split("\n", 1)[0][:160]
    score = 0.0
    if _HEADER_RE.match(first_line):
        score += WEIGHTS["header"]
    if _CLOSURE_RE.match(first_line) or _CLOSURE_MID_RE.search(text[:400]):
        score += WEIGHTS["closure"]
    if _META_COMPACT_RE.search(text[:2000]):
        score += WEIGHTS["meta_compact"]
    if _DIRECT_ADDRESS_RE.match(first_line):
        score += WEIGHTS["direct_address"]
    if len(text) > _LENGTH_FLOOR_CHARS:
        over = len(text) - _LENGTH_FLOOR_CHARS
        score += min(_LENGTH_FLOOR_CAP, _LENGTH_FLOOR_WEIGHT * (over / _LENGTH_FLOOR_STEP))
    return score


def score_events(events: list[NormalizedEvent]) -> None:
    """Fill in .checkpoint_score for every ASSISTANT_TEXT event, in place.

    Requires the full event list (not a streaming/partial one) because the
    "followed by a pause" signal looks at whatever real event comes next.
    """
    n = len(events)
    for i, ev in enumerate(events):
        if ev.kind is not EventKind.ASSISTANT_TEXT:
            continue
        score = _shape_score(ev.text)
        for j in range(i + 1, n):
            nxt = events[j]
            if nxt.kind is EventKind.ASSISTANT_TEXT:
                break
            if nxt.kind in (EventKind.OPERATOR_TEXT, EventKind.QA_PAIR):
                score += WEIGHTS["followed_by_pause"]
                break
            if nxt.kind is EventKind.LIFECYCLE_MARKER:
                break
        ev.checkpoint_score = score
