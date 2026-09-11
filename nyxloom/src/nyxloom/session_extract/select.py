"""The windowing walk: given a fully-parsed, fully-scored event list, decide
what survives into the resumable brief.

Walks backward (newest first) rather than forward-then-trim, per operator
direction: this makes "stop once the budget or the checkpoint target is
satisfied" a natural early-exit instead of a full-scan-then-discard, and it
means the OLDEST material is always what gets dropped first when max_words
is tight -- nothing already accepted is ever un-accepted.

Windowing contract:
  - an ASSISTANT_TEXT event carrying classifier.is_api_error (upstream 429/
    rate-limit/overloaded_error transport noise) is dropped unconditionally
    when config.hide_api_errors is set (the default) -- before checkpoint
    scoring or finding-signal rescue ever apply. `--show-api-errors`
    disables this and restores the pre-2026-09-10 behavior below;
  - the last `max_checkpoints` ASSISTANT_TEXT events scoring at or above
    checkpoint_score_threshold are always kept in full;
  - a non-checkpoint ASSISTANT_TEXT/THINKING event survives if it's longer
    than config.long_comment_chars, OR it reports a concrete finding
    (classifier.has_finding_signal) regardless of length -- real-excerpt
    comparison (an operator's own hand-curated brief vs. this tool's output
    on the same real session span) showed a human keeps "Found it -- a
    pgrep pattern bug..." but drops "Now let's fix the detection" even
    though both are one-liners. This bar is applied UNIFORMLY across the
    whole walked span -- an earlier design gave the newest part of the span
    a much lower, more lenient bar on the theory that recent history
    deserves more slack. A full replay of every ASSISTANT_TEXT event in the
    same real span against the operator's excerpt (not a sample) found
    dozens of short procedural lines the operator dropped even in the very
    last few turns, right up against the newest checkpoint -- no case of
    recency alone rescuing one. See config.py's module docstring
    ("append-only / cache-stable") for why the tool should default toward
    dropping marginal content rather than keeping it;
  - OPERATOR_TEXT and QA_PAIR are always kept, anywhere in the walked span;
  - a LIFECYCLE_MARKER is always kept (as a note); by default (
    config.max_lifecycle_markers == 0) the FIRST one hard-stops the walk --
    content on the far side of a compaction boundary or an explicit
    /compact//clear is a different kind of artifact, not walked past, by
    default. config.max_lifecycle_markers raises how many markers the walk
    is allowed to pass before it finally stops at one -- see config.py;
  - once max_checkpoints have been found, the walk stops immediately
    (the window never reaches further back than the oldest of the target
    checkpoints);
  - once the cumulative word count exceeds max_words, the walk stops.

Two annotations, purely additive via NormalizedEvent.meta (never the return
type or an event's own .text -- so this changes nothing about equality
comparisons in existing tests, which already exclude meta via
compare=False, and nothing about word-budget accounting, which only ever
reads .text):

  - `meta["gap_after"]` -- set on a kept event when one or more raw source
    records (adapter `seq` units -- JSONL lines, rollout ordinals, DB rows;
    see events.py) sit between it and the NEXT-NEWER kept event, with no
    kept event of our own for that stretch. This covers BOTH kinds of gap
    uniformly: an ASSISTANT_TEXT/THINKING event this walk itself considered
    and rejected (too short, no finding signal), and raw records that never
    even became a NormalizedEvent (tool_use/tool_result, mode switches,
    etc -- adapters drop these before select() ever sees them). Either way
    the reader is being told "something real happened here that you are not
    seeing," which is exactly the gap E-011 (docs/design-context-lifecycle-
    experiments.md) flagged: two adjacent kept events currently look
    time-adjacent whether or not they actually were.
  - `meta["walk_stopped_because"]` -- set on the OLDEST kept event, but
    ONLY for the two ways the walk can stop with more session left unread:
    "max_words" or "max_checkpoints". Reaching the true start of the
    session (the for/else below) and hard-stopping at a LIFECYCLE_MARKER
    are both left untagged on purpose -- the former genuinely lost nothing,
    and the marker's own kept text ("[compact boundary]" etc) already says
    why the walk stopped there; a second tag would be redundant. This is
    the "why did selection stop here" signal E-011 asked for: today
    reaching the real start of the log and hitting a budget wall render
    identically, with no way for a reader to tell the difference.

Both are a property of THIS render's own walk over its own span, computed
fresh every call -- nothing already emitted by a prior --since run is ever
retroactively edited (config.py's append-only / cache-stable principle).
"""

from __future__ import annotations

from . import classifier
from .config import ExtractConfig
from .events import EventKind, NormalizedEvent


def select(events: list[NormalizedEvent], config: ExtractConfig) -> list[NormalizedEvent]:
    # An OPERATOR_TEXT/QA_PAIR turn immediately followed (in full,
    # pre-selection chronological order) by a LIFECYCLE_MARKER -- no
    # ASSISTANT_TEXT/THINKING event in between -- got zero response before
    # the boundary. That's not a counting bug (stats.py's per-boundary
    # aggregation is correct: nothing ran in that window), but shown alone
    # it reads as "this instruction was dropped." A real dstdns session
    # (2026-09-10) confirmed the operator's instruction was NOT dropped --
    # the very next assistant turn after the compact summary acted on it
    # directly. render.py surfaces this meta as a note pointing the reader
    # at the next block instead of leaving a bare zero-stat-looking gap.
    # Deliberately does NOT try to tell "compaction ate the turn and NOTHING
    # ever followed" apart from the common case -- that's a real, rarer
    # edge case left for later rather than risking OPERATOR_TEXT/QA_PAIR's
    # existing always-kept guarantee below.
    for i, ev in enumerate(events):
        if (ev.kind in (EventKind.OPERATOR_TEXT, EventKind.QA_PAIR)
                and i + 1 < len(events)
                and events[i + 1].kind is EventKind.LIFECYCLE_MARKER):
            ev.meta["swallowed_by_compaction"] = "1"

    kept: list[NormalizedEvent] = []
    checkpoints_found = 0
    word_count = 0
    markers_passed = 0
    last_kept_seq: int | None = None

    def _keep(ev: NormalizedEvent) -> None:
        nonlocal last_kept_seq
        if last_kept_seq is not None:
            gap = last_kept_seq - ev.seq - 1
            if gap > 0:
                ev.meta["gap_after"] = str(gap)
        kept.append(ev)
        last_kept_seq = ev.seq

    for ev in reversed(events):
        if ev.kind is EventKind.LIFECYCLE_MARKER:
            _keep(ev)
            may_pass = config.max_lifecycle_markers == -1 or markers_passed < config.max_lifecycle_markers
            if not may_pass:
                break
            markers_passed += 1
            continue

        if ev.kind in (EventKind.OPERATOR_TEXT, EventKind.QA_PAIR):
            _keep(ev)
            word_count += len(ev.text.split())

        elif ev.kind in (EventKind.ASSISTANT_TEXT, EventKind.THINKING):
            if (config.hide_api_errors and ev.kind is EventKind.ASSISTANT_TEXT
                    and classifier.is_api_error(ev.text)):
                # Dropped unconditionally -- not even considered for
                # checkpoint/finding-signal rescue -- per config.py's
                # hide_api_errors. Falls into the next-older kept event's
                # gap_after exactly like any other rejected event.
                continue
            is_checkpoint = (
                ev.kind is EventKind.ASSISTANT_TEXT
                and (ev.checkpoint_score or 0.0) >= config.checkpoint_score_threshold
            )
            if is_checkpoint:
                checkpoints_found += 1
                _keep(ev)
                word_count += len(ev.text.split())
                if checkpoints_found >= config.max_checkpoints:
                    if kept:
                        kept[-1].meta["walk_stopped_because"] = "max_checkpoints"
                    break
            elif len(ev.text) > config.long_comment_chars or classifier.has_finding_signal(ev.text):
                _keep(ev)
                word_count += len(ev.text.split())

        if word_count > config.max_words:
            if kept:
                kept[-1].meta["walk_stopped_because"] = "max_words"
            break

    kept.reverse()
    return kept
