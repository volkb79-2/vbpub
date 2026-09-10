"""The windowing walk: given a fully-parsed, fully-scored event list, decide
what survives into the resumable brief.

Walks backward (newest first) rather than forward-then-trim, per operator
direction: this makes "stop once the budget or the checkpoint target is
satisfied" a natural early-exit instead of a full-scan-then-discard, and it
means the OLDEST material is always what gets dropped first when max_words
is tight -- nothing already accepted is ever un-accepted.

Windowing contract:
  - the last `max_checkpoints` ASSISTANT_TEXT events scoring at or above
    checkpoint_score_threshold are always kept in full;
  - a non-checkpoint ASSISTANT_TEXT/THINKING event survives if it's longer
    than a length bar, OR it reports a concrete finding
    (classifier.has_finding_signal) regardless of length -- real-excerpt
    comparison (an operator's own hand-curated brief vs. this tool's output
    on the same real session span) showed a human keeps "Found it -- a
    pgrep pattern bug..." but drops "Now let's fix the detection" even
    though both are one-liners. The length bar itself is two-tiered: from
    the newest checkpoint back through the 2nd-oldest of the target
    max_checkpoints, it's config.recent_comment_chars (low -- "recent
    history" is lenient, but NOT "keep everything": that unconditional
    version was the original design and the same real-excerpt comparison
    showed it over-keeps low-value procedural narration even in the most
    recent part of a session); past the 2nd-oldest checkpoint it's the
    stricter config.long_comment_chars;
  - OPERATOR_TEXT and QA_PAIR are always kept, anywhere in the walked span;
  - a LIFECYCLE_MARKER is kept (as a note) and then hard-stops the walk --
    content on the far side of a compaction boundary or an explicit
    /compact//clear is a different kind of artifact, never raw-extracted;
  - once max_checkpoints have been found, the walk stops immediately
    (the window never reaches further back than the oldest of the target
    checkpoints);
  - once the cumulative word count exceeds max_words, the walk stops.
"""

from __future__ import annotations

from . import classifier
from .config import ExtractConfig
from .events import EventKind, NormalizedEvent


def select(events: list[NormalizedEvent], config: ExtractConfig) -> list[NormalizedEvent]:
    kept: list[NormalizedEvent] = []
    checkpoints_found = 0
    word_count = 0
    in_recent_window = True
    second_oldest_rank = max(1, config.max_checkpoints - 1)

    for ev in reversed(events):
        if ev.kind is EventKind.LIFECYCLE_MARKER:
            kept.append(ev)
            break

        if ev.kind in (EventKind.OPERATOR_TEXT, EventKind.QA_PAIR):
            kept.append(ev)
            word_count += len(ev.text.split())

        elif ev.kind in (EventKind.ASSISTANT_TEXT, EventKind.THINKING):
            is_checkpoint = (
                ev.kind is EventKind.ASSISTANT_TEXT
                and (ev.checkpoint_score or 0.0) >= config.checkpoint_score_threshold
            )
            if is_checkpoint:
                checkpoints_found += 1
                kept.append(ev)
                word_count += len(ev.text.split())
                if checkpoints_found >= second_oldest_rank:
                    in_recent_window = False
                if checkpoints_found >= config.max_checkpoints:
                    break
            else:
                bar = config.recent_comment_chars if in_recent_window else config.long_comment_chars
                if len(ev.text) > bar or classifier.has_finding_signal(ev.text):
                    kept.append(ev)
                    word_count += len(ev.text.split())

        if word_count > config.max_words:
            break

    kept.reverse()
    return kept
