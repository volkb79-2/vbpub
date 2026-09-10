"""Centralized, tunable knobs for extraction. Every threshold that shapes
what survives into the resumable brief lives here — nowhere else in this
package hardcodes a number. cli_extract.py exposes the ones worth changing
per-run as flags; the rest are still overridable by constructing
ExtractConfig directly (a future config-file loader, if one is ever wanted,
has exactly one place to write into).

Design principle -- append-only / cache-stable extraction: a rendered
extraction's whole reason to exist is to become the start of a fresh
session's prompt, which the inference provider then prefix-caches. Once a
later --since/--since-file run has extended THAT session further and the
cache has built up on top of it, retroactively deciding "actually, drop
that one-liner from three checkpoints back" would edit the middle of an
already-cached prefix and tear the cache for everything built on top of it
-- an expensive, one-way mistake, not a free do-over. This has two
consequences for every threshold below: (1) selection for a span must never
depend on anything that happens in a LATER span (no cross-checkpoint
redundancy pruning, no "drop this because a later checkpoint restates it" --
tempting, and rejected for exactly this reason), and (2) when a threshold is
ambiguous, default toward DROPPING marginal content rather than keeping it:
dropping loses nothing permanently (the raw session log is still there to
re-read), but keeping something bakes it into a prefix that becomes
expensive to ever revise.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ExtractConfig:
    # How many of the most-recent detected checkpoints to anchor on.
    max_checkpoints: int = 5

    # classifier.py score (see CheckpointClassifier.WEIGHTS) an ASSISTANT_TEXT
    # event must reach to count as one of the max_checkpoints anchors.
    checkpoint_score_threshold: float = 3.0

    # A non-checkpoint ASSISTANT_TEXT/THINKING event survives only if longer
    # than this (chars) or classifier.has_finding_signal(text) -- applied
    # UNIFORMLY across the whole walked span, not just "past the 2nd-oldest
    # checkpoint". An earlier design gave the newest part of the span (from
    # the 2nd-oldest checkpoint onward) a much lower bar on the theory that
    # "recent history" deserves more leniency. A full, line-by-line replay of
    # this tool's output against an operator's own hand-curated excerpt --
    # every ASSISTANT_TEXT event in the span checked, not a sample -- found
    # roughly 40 short (40-150 char), no-finding-signal procedural lines
    # ("Now the `log-opts` cleanup gap...", "Real bug confirmed and it's a
    # quick fix. Let me apply it...") that the operator dropped even in the
    # LAST few turns of the span, right up against the newest checkpoint.
    # There was no example anywhere of recency alone rescuing a short
    # procedural line. Recency-based leniency was therefore removed rather
    # than re-tuned. See the "append-only / cache-stable" note below for why
    # the tool should default toward dropping marginal content, not keeping it.
    long_comment_chars: int = 180

    # Hard output budget. Selection walks backward from the end and stops
    # accepting older material once this is exceeded -- the trim always
    # happens at the OLD end, never by truncating something already kept.
    max_words: int = 10_000

    # THINKING events are dropped at parse time unless this is set.
    include_thinking: bool = False

    # Opaque marker (an event.marker from a prior extract() call) to resume
    # from -- only events strictly after it are considered. None = walk the
    # whole session (bounded by the nearest LIFECYCLE_MARKER regardless).
    since_marker: str | None = None

    # Opaque marker to stop at (inclusive) -- only events at/before it are
    # considered. None = walk to the end of the log. Bounding both ends is a
    # dev/comparison need first (pin an extraction to a fixed historical
    # span so a run is reproducible against a fixed hand-curated reference),
    # but is a plain, symmetric extension of since_marker with no reason to
    # withhold from normal use.
    until_marker: str | None = None

    # "text" (delimited prose, ready to paste into a fresh session) or
    # "json" (structured, for a script/second-stage tool to consume).
    output_format: str = "text"
