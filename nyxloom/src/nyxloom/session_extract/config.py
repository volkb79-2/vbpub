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
    # --- Walk-stop conditions (max_checkpoints, max_words, max_compactions,
    # max_time_minutes below): select.py's backward walk halts the instant
    # ANY ONE enabled condition trips, whichever comes first. Each accepts
    # -1 to mean never trips due to this condition. Epoch selection is a
    # separate source-span filter applied before this walk.

    # How many of the most-recent detected checkpoints to anchor on.
    # A checkpoint is a classifier-scored assistant prose message, not a
    # semantic session boundary or a known-safe compaction point. -1 disables
    # this stop condition.
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
    # -1 = never stop due to word count (see the group note above).
    max_words: int = 10_000

    # THINKING events are dropped at parse time unless this is set.
    include_thinking: bool = False

    # NOTE: there is deliberately no `include_sidechain` knob here. An
    # earlier version of this field existed (2026-09-11) after a real bug
    # (a dispatched Agent-tool subagent's own transcript file has
    # isSidechain=true on every record and was silently extracting to zero
    # events) -- but exposing a Claude-Code-only vocabulary word
    # ("sidechain") on this shared, adapter-agnostic config was itself a
    # design mistake (operator critique, 2026-09-11: "adapters solve the
    # CLI specifics"). Fixed instead entirely inside
    # adapters/claude_code.py's parse(): whether a file has a "primary
    # thread" at all (any non-sidechain record present) is auto-detected
    # from the file's own content -- see that function's comment for the
    # exhaustive real-data verification behind it. No flag needed; targeting
    # a subagent's own conversation is just `nyxloom extract <that agent's
    # own file path>`, same as targeting any other adapter's session.

    # Opaque marker (an event.marker from a prior extract() call) to resume
    # from -- only events strictly after it are considered. None = no lower
    # cursor bound; epoch selection and the walk's configured stop conditions
    # still apply.
    since_marker: str | None = None

    # Opaque marker to stop at (inclusive) -- only events at/before it are
    # considered. None = walk to the end of the log. Bounding both ends is a
    # dev/comparison need first (pin an extraction to a fixed historical
    # span so a run is reproducible against a fixed hand-curated reference),
    # but is a plain, symmetric extension of since_marker with no reason to
    # withhold from normal use.
    until_marker: str | None = None

    # How many actual compaction boundaries the backward walk may cross
    # before it stops at the next one. `/clear`, `/compact` command records,
    # and summary echoes are not counted as actual compactions. -1 disables
    # this stop condition. The default is -1; `/clear` is handled separately
    # by epoch selection.
    #
    max_compactions: int = -1

    # Compatibility for callers constructing ExtractConfig with the old
    # field. CLI help and documentation use max_compactions.
    max_lifecycle_markers: int | None = None

    # Stop once walking backward exceeds this many minutes from the newest
    # event timestamp in the selected span. -1 disables the stop condition.
    max_time_minutes: int = -1

    # Epoch selection is parsed as a 1-based epoch number, inclusive A:B
    # range, or "all". None means the newest epoch only.
    epochs: str | None = None

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
    # Codex ordinal or `response_item-<position>` fallback, an opencode
    # message-table row id -- so this is a "go
    # look it up yourself" pointer into the raw log, not new data. Off by
    # default (see render.py's module docstring): most readers most of the
    # time only need to know a gap existed, not recover it.
    gap_note_show_marker: bool = False

    # Text rendering only: how many blank lines render.py pads around each
    # "---" block separator.
    #    1  -- one blank
    #          line on each side ("block\n\n---\n\nblock").
    #    0  -- tight: "---" gets its own line, no blank line either side
    #          ("block\n---\nblock").
    #   -1  -- fused: "---" (and any embedded gap text -- see
    #          gap_marker_mode below) shares the END of the preceding
    #          block's last line, separated by one space, no blank line
    #          ("block ---\nblock").
    #    N>1 -- N blank lines on each side, a plain generalization of 1.
    insert_blank_lines: int = 0

    # Text rendering only (2026-09-11, operator direction): how a kept
    # event's gap_after annotation (select.py's own meta field -- a raw-
    # record gap to the next-newer kept event, at/above min_gap_to_annotate
    # above) is surfaced.
    #   "full"        -- a
    #                    STANDALONE block of its own ("[gap: N records
    #                    omitted]"), separated from its neighbors by the
    #                    same insert_blank_lines-controlled separator as
    #                    everything else.
    #   "inline"      -- embedded directly in the separator's dashes
    #                    instead of its own block: "--- [gap: N records
    #                    omitted] ---".
    #   "inline2"     -- same placement, terser count notation: "--- ... Nx
    #                    ... ---".
    #   "inline-short" -- same placement, no count at all, just "--- ... ---"
    #                    (a gap happened, magnitude not stated).
    #   "none"        -- suppressed entirely -- a reader cannot tell a gap
    #                    happened at all from the rendered text (JSON
    #                    rendering is unaffected either way -- it always
    #                    reports the true gap_after count, per
    #                    min_gap_to_annotate's own comment above).
    # gap_note_show_marker above still applies to "full"/the three inline
    # variants (appending the adapter's own marker token) but is a no-op
    # under "none".
    gap_marker_mode: str = "inline"

    # Event visibility is a content-selection option. Tool inputs/results
    # remain omitted; when requested, adapters emit a short tool-call label.
    show_tool_calls: bool = False
    show_tool_call_intent: bool = False

    # Text-only timestamps. The source event timestamp is used; empty source
    # timestamps stay absent rather than being invented.
    show_timestamps: str = "pre"
    timestamp_format: str = "[%H:%M:%S]"
    extract_metadata: str = "both"

    # Upstream API-transport noise (429/rate-limit/overloaded_error --
    # adapters/claude_code.py's own "[API ERROR: ...]" tag,
    # classifier.is_api_error) is irrelevant to the SESSION CONTENT this
    # package summarizes -- it's a fact about the harness's connection to
    # the API, not about what happened in the session. Suppressed from
    # selection entirely by default (2026-09-10 operator direction,
    # reversing this package's earlier "never noise" stance): dropped even
    # though classifier.has_finding_signal would otherwise rescue it past
    # the length filter. `nyxloom extract --show-api-errors` restores the
    # old behavior for a run where seeing API-transport stalls matters.
    hide_api_errors: bool = True

    # An operator-issued `/compact <prompt>` dispatch's own argument text,
    # which can otherwise carry a large verbatim body (2026-09-11, operator
    # direction: real example -- a session had one directing "KEEP:
    # standing /goal is active..." verbatim, the same text mangle.py's
    # redact_paragraphs was separately built to strip back out downstream).
    # By default this collapses to a terse "[compaction: steered
    # dispatched]" instead -- the operator's instruction almost always just
    # re-quotes a "compaction prompt" the model already produced as
    # ordinary ASSISTANT_TEXT moments earlier (kept in full there,
    # unaffected by this flag -- classifier.py's meta_compact scoring bonus
    # is what keeps a model-authored "paste this as the /compact argument"
    # checkpoint), and extract's own concatenated prose already IS the
    # context that would otherwise be repeated verbatim a second time.
    # `--show-compaction-content` (hide_compaction_content=False) restores
    # the pre-2026-09-11 behavior verbatim for this one case, for a run
    # where recovering the exact dispatched prompt text matters more than
    # compactness. Does NOT affect the resulting compact_boundary record's
    # own marker text ("[compaction: steered|automatic happened, <pre>-><
    # post> tok, <dur>s]", mirroring extract-report's own stats.py
    # _compaction_label bracket format, "just like our extract-report" --
    # operator's own words) -- that text is always the enriched form,
    # unconditionally, since the boundary record never carried real
    # verbatim content for this flag to hide in the first place (its own
    # `content` field is always just the fixed literal string "Conversation
    # compacted"; the real retained-context summary lives on a SEPARATE
    # isCompactSummary record, which has always been hint-only --
    # "[compact summary]" -- with no code change needed there at all).
    hide_compaction_content: bool = True

    # Post-selection, pre-render heuristic mangling (mangle.py) -- both OFF
    # by default; see that module's docstring for the real-session evidence
    # behind each. Built for exactly one use case: piping `extract`'s own
    # output straight into a fresh agent's prompt (`nyxloom extract ... |
    # claude`), where recency bias (a stale trailing confirmation reads as
    # "the current situation") and role-level framing (a standing
    # controller directive surviving in the render) can hijack what the new
    # agent does first.

    # Collapse a trailing run of 2+ near-duplicate "stale wakeup, nothing
    # new" assistant checkpoints down to just the first of the run. See
    # mangle.strip_stale_wakeup_tail for the exact detection rule.
    strip_stale_wakeups: bool = False

    # Paragraph-level redaction: any blank-line-delimited paragraph in ANY
    # kept event's text matching one of these regexes (case-insensitive) is
    # replaced with a one-line placeholder naming the match. Empty tuple =
    # no-op. The canonical use case this was built for: `--redact-pattern
    # '/goal'` to strip a standing controller directive's own text back out
    # of what a fresh (or forked) agent would otherwise inherit verbatim --
    # see mangle.py's docstring for the real `/compact` KEEP-block example
    # that motivated this.
    redact_patterns: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.max_lifecycle_markers is not None:
            self.max_compactions = self.max_lifecycle_markers
        for name in ("max_checkpoints", "max_words", "max_compactions", "max_time_minutes"):
            value = getattr(self, name)
            if value < -1:
                raise ValueError(f"{name} must be -1 (unlimited) or non-negative")
        if self.max_checkpoints == 0:
            raise ValueError("max_checkpoints must be -1 (unlimited) or positive")
        if self.insert_blank_lines < -1:
            raise ValueError("insert_blank_lines must be -1 or non-negative")
        if self.min_gap_to_annotate < 0:
            raise ValueError("min_gap_to_annotate must be non-negative")
        if self.show_timestamps not in {"pre", "post", "both", "none"}:
            raise ValueError("show_timestamps must be pre, post, both, or none")
        if self.extract_metadata not in {"pre", "post", "both"}:
            raise ValueError("extract_metadata must be pre, post, or both")
        if self.epochs is not None and self.epochs != "all":
            parts = self.epochs.split(":")
            if len(parts) == 1:
                parts *= 2
            if len(parts) != 2 or not all(part.isdigit() and int(part) > 0 for part in parts):
                raise ValueError("epochs must be N, A:B, or all using positive 1-based numbers")
            if int(parts[0]) > int(parts[1]):
                raise ValueError("epochs range must satisfy 1 <= A <= B")


# Profiles name operator use cases. Explicit CLI values override the
# corresponding profile value. Gap surfacing follows the profile; timestamp
# and metadata placement, Markdown rendering, ANSI color, and tool visibility
# remain independent options.
DEFAULT_PROFILE = "operator-review"
PROFILES: dict[str, ExtractConfig] = {
    "operator-review": ExtractConfig(
        max_checkpoints=5, long_comment_chars=180, max_words=10_000,
        max_compactions=-1, max_time_minutes=-1, epochs=None,
    ),
    "all": ExtractConfig(
        max_checkpoints=-1, long_comment_chars=-1, max_words=-1,
        max_compactions=-1, max_time_minutes=-1, epochs="all",
        min_gap_to_annotate=1, gap_marker_mode="inline", insert_blank_lines=0,
    ),
}
