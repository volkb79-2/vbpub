"""Centralized, tunable knobs for extraction. Every threshold that shapes
what survives into the resumable brief lives here — nowhere else in this
package hardcodes a number. cli_extract.py exposes the ones worth changing
per-run as flags; the rest are still overridable by constructing
ExtractConfig directly (a future config-file loader, if one is ever wanted,
has exactly one place to write into).
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

    # Below the 2nd-oldest kept checkpoint (the "older" window), a
    # non-checkpoint ASSISTANT_TEXT/THINKING event survives only if longer
    # than this (chars) or classifier.has_finding_signal(text).
    long_comment_chars: int = 180

    # From the 2nd-oldest checkpoint onward (the "recent" window), the same
    # rule applies but at a much lower bar. NOT unconditional "keep
    # everything": a real-excerpt comparison (an operator's own hand-curated
    # brief of a real session, checked against this tool's output on the
    # same span) showed most of the low-value churn this tool over-keeps is
    # short, purely-procedural narration ("Now let's fix X", "Let me check
    # Y") that a human drops even in the most recent part of a session --
    # while a short line naming a concrete finding survives regardless of
    # window (has_finding_signal always applies). 40 chars is a first-pass
    # value, not yet tuned against a labeled corpus.
    recent_comment_chars: int = 40

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

    # "text" (delimited prose, ready to paste into a fresh session) or
    # "json" (structured, for a script/second-stage tool to consume).
    output_format: str = "text"
