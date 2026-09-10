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

    # How many LIFECYCLE_MARKER events (a real compaction boundary, or an
    # operator /compact//clear) the backward walk is allowed to walk PAST
    # before finally hard-stopping at one, instead of stopping at the very
    # first one it meets.
    #   0  -- current/default behavior: stop at (and keep) the first marker
    #         encountered. Every marker's own label text ("[compact
    #         boundary]", "[compact summary]", "[/compact] ...") is kept
    #         regardless of this setting -- it's the walk CONTINUING past it
    #         that this knob controls, not whether the marker itself shows up.
    #   N  -- walk past N markers (keeping each one's label), hard-stop at
    #         marker N+1.
    #   -1 -- indefinite: never stop due to markers at all; only max_words /
    #         max_checkpoints / reaching the start of the log bound the walk.
    #
    # Raising this above 0 is an explicit escape hatch, not a general
    # recommendation -- see config.py's own "append-only / cache-stable"
    # principle above. Walking past a real compaction pulls in prose from
    # BEFORE a point where the original live session's own context was
    # reset; that prose is genuine and unaltered, but whatever happened
    # between then and the compaction is only represented by the compact
    # summary's own condensed text, not by this older material. Fine for a
    # one-time manual extraction meant to seed a fresh session (where the
    # only goal is the richest possible brief within a word budget, not
    # long-run chained --since stability); left at 0 for anything chained.
    max_lifecycle_markers: int = 0

    # "text" (delimited prose, ready to paste into a fresh session) or
    # "json" (structured, for a script/second-stage tool to consume).
    output_format: str = "text"

    # Text rendering only (see render.py, select.py's "gap_after" meta,
    # design-context-lifecycle-experiments.md's E-011): the smallest
    # raw-record gap between two kept events worth surfacing as a "[gap: N
    # records omitted]" note. Below this, no note is rendered -- a gap of 1
    # or 2 is usually just the tool_use/tool_result pair for a single
    # ordinary tool call, not informative, and real-data validation (the
    # dstdns 8ebff140 replay) found dozens of them per run, inflating output
    # by ~17% over max_words with mostly-uninformative noise. select.py
    # still records the EXACT count in meta regardless of this threshold --
    # only text rendering filters; JSON rendering (a second-stage tool's
    # input) always reports the true count, filtering being a text-UX
    # concern, not a data-completeness one.
    min_gap_to_annotate: int = 3

    # Text rendering only: opt into naming the adapter's own opaque marker
    # token in each gap/stop-reason note ("...raw log continues after
    # marker <marker>") instead of a bare count. That marker is the exact
    # same token --since/--until already resolve -- a Claude Code uuid, a
    # Codex ordinal, an opencode message-table row id -- so this is a "go
    # look it up yourself" pointer into the raw log, not new data. Off by
    # default (see render.py's module docstring): most readers most of the
    # time only need to know a gap existed, not recover it.
    gap_note_show_marker: bool = False


# Named presets bundling the "how aggressively should selection filter
# content" knobs -- max_checkpoints, checkpoint_score_threshold,
# long_comment_chars, max_lifecycle_markers. Deliberately does NOT bundle
# max_words as a fixed, profile-defining value: target length is its own
# axis (an operator asked for this explicitly, 2026-09-10) -- "how
# permissive is the selection bar" and "how big should the final output be"
# are independent questions, and conflating them into one preset value
# makes "strict criteria, generous budget" or "lenient criteria, tight
# budget" impossible to ask for. Each profile below still carries a
# max_words value, but ONLY as a sensible per-profile DEFAULT that
# `nyxloom extract --profile X --max-words N` (or ExtractConfig's own
# dataclass replace) freely overrides -- see cli.py's cmd_extract for how
# the override is applied. See also
# design-context-lifecycle-experiments.md's E-009 "Named compression
# profiles" open question and its 2026-09-10 follow-up.
PROFILES: dict[str, ExtractConfig] = {
    # Strict bar, no walking past a real compaction -- the default chained-
    # snapshot-pipeline shape (config.py's own append-only/cache-stable
    # principle governs this one).
    "tight": ExtractConfig(max_checkpoints=3, max_words=4_000, long_comment_chars=240),
    # This package's plain, unnamed default -- kept as a named profile too
    # so every extraction, --profile or not, is expressible as "some
    # profile plus overrides."
    "default": ExtractConfig(),
    # Ignores lifecycle markers entirely -- the one-time manual-extraction-
    # for-a-fresh-session use case (validated against a real dstdns session,
    # E-009's follow-up): richest possible brief within a word budget, not
    # chained-run cache stability.
    "manual_fresh": ExtractConfig(max_checkpoints=1_000_000, max_words=8_000, max_lifecycle_markers=-1),
}
