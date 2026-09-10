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

An earlier revision also added an undocumented length-based floor (any
message over ~800 chars gained score purely from size, capped at +3.0) --
found by adversarial review to directly contradict this file's own opening
claim ("length alone is a poor signal") and to be capable of pushing a
long, shapeless tool-output narration to checkpoint-anchor status on size
alone. Removed rather than re-tuned: nothing in the real-data validation
(the header/closure/direct-address checkpoints actually found) depended on
it.

A SEPARATE, lighter signal -- has_finding_signal() -- exists for a
different question: not "is this a checkpoint" (full-length, scored) but
"is this short non-checkpoint comment worth keeping anyway" (select.py's
length filter, otherwise a bare char-count, applied uniformly across the
whole walked span -- see select.py's module docstring for why "uniformly"
replaced an earlier recency-dependent version). Found by direct comparison
against the operator's own hand-curated excerpt of a real session: they
kept short one-liners that named a concrete finding ("Found it -- a pgrep
pattern bug...", "Found the real root cause: pgrep isn't installed...")
and dropped short purely-procedural ones ("Now let's fix X", "Let me check
Y") of about the same length. The distinguishing feature isn't length,
it's whether the line reports something CONCRETE (a finding, a specific
file/command/identifier) versus announcing an upcoming action.
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

_FINDING_OPENER_RE = re.compile(
    r"^(Found\b|Confirmed\b|Root cause\b|The real (bug|cause|root cause)\b)",
    re.IGNORECASE,
)
# A backtick span containing a slash or dot reads as a file path, command,
# or code identifier ("`/apt-cacher-ng`", "`config.py`") rather than a bare
# word -- that's the difference between naming something concrete and just
# emphasizing a term.
_CODE_REFERENCE_RE = re.compile(r"`[^`\n]*[/.][^`\n]*`")
_FILENAME_RE = re.compile(r"\b\w[\w-]*\.(py|sh|md|toml|json|jsonl|ya?ml|js|ts|cfg|ini)\b")
# adapters/claude_code.py's own "[API ERROR: ...]" tag for an
# isApiErrorMessage record (a real 429/overloaded_error the harness hit) --
# a rate-limit notice is typically SHORT ("You've hit your session limit"),
# exactly the shape the length filter otherwise drops, but the fact a
# session actually stalled on a real API error is never noise.
_API_ERROR_RE = re.compile(r"^\[API ERROR\b")


def has_finding_signal(text: str) -> bool:
    first_line = text.split("\n", 1)[0][:160]
    if _FINDING_OPENER_RE.match(first_line) or _API_ERROR_RE.match(first_line):
        return True
    return bool(_CODE_REFERENCE_RE.search(text) or _FILENAME_RE.search(text))


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
