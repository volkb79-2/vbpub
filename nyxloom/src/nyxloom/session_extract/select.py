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
  - OPERATOR_TEXT, QA_PAIR, and explicitly-enabled TOOL_CALL events are
    always kept, anywhere in the walked span;
  - a LIFECYCLE_MARKER is always kept (as a note) REGARDLESS of max_words --
    never rejected for its own length -- but its own word count DOES count
    toward the running total (2026-09-11 bug fix; previously it counted for
    zero, so a large marker body -- e.g. an operator's /compact <prompt>
    dispatch under --show-compaction-content, see config.py -- could
    silently blow straight through the budget with no trim ever
    triggering). Only actual compaction boundaries count toward
    config.max_compactions. `/clear` is handled as an epoch boundary before
    this walk; `/compact` command records and compact-summary echoes are not
    counted as actual compactions;
  - once max_checkpoints have been found, the walk stops immediately
    (the window never reaches further back than the oldest of the target
    checkpoints) -- max_checkpoints=-1 disables this condition entirely;
  - once the cumulative word count (every kept event's own text, markers
    included) exceeds max_words, the walk stops -- max_words=-1 disables
    this condition entirely.

Checkpoint count, word count, compaction count, and elapsed session time
are independent stop conditions. Any one can end the backward walk; -1
disables that condition. With every condition disabled, the walk reaches the
start of the selected epoch span.

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
  - `meta["walk_stopped_because"]` -- set on the OLDEST kept event when a
    checkpoint, word, compaction, or time stop leaves older source content
    unread. Reaching the true start of the selected epoch is left untagged;
    the walk genuinely lost nothing there. This is the "why did selection
    stop here" signal E-011 asked for.

Both are a property of THIS render's own walk over its own span, computed
fresh every call -- nothing already emitted by a prior --since run is ever
retroactively edited (config.py's append-only / cache-stable principle).

`decide()` below is the PER-EVENT half of that contract, factored out of
the walk (2026-09-12) so `--follow`'s forward stream (follow.py) can apply
the identical rule to a newly-arrived event instead of reimplementing it.
Only the per-event half generalizes: aggregate stop conditions
(max_checkpoints / max_words / max_compactions / max_time_minutes) are
statements about a fixed, already-known span and mean nothing against an
unbounded stream, so they stay here in the walk. The split is
behavior-preserving -- select() now consumes decide() rather than repeating
it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from . import classifier, render
from .config import ExtractConfig
from .events import EventKind, NormalizedEvent


@dataclass(frozen=True)
class EventDecision:
    """What the per-event rule says about one event, with the two facts the
    walk's aggregate bookkeeping additionally needs: whether the kept event
    counts toward max_checkpoints, and whether it is the LIFECYCLE_MARKER
    kind that is kept unconditionally and can hard-stop the walk."""

    keep: bool
    is_checkpoint: bool = False
    is_lifecycle_marker: bool = False


@dataclass
class BudgetCategory:
    kept: int = 0
    dropped: int = 0
    dropped_chars: int = 0
    dropped_tokens: int = 0
    dropped_first: str = ""
    dropped_last: str = ""


@dataclass
class ReadbackReport:
    budget_tokens: int
    reserved_footer_tokens: int
    estimated_content_tokens: int = 0
    protected_content_tokens: int = 0
    budget_overrun_tokens: int = 0
    categories: dict[str, BudgetCategory] = field(default_factory=dict)
    source_first: str = ""
    source_last: str = ""
    agent_comms: str = "off"


READBACK_CATEGORIES = (
    "operator-interview",
    "assistant-prose",
    "agent-communication",
    "intent-lines",
    "run-results",
)
READBACK_PROTECTED_CATEGORY = "operator-interview"

# Char/4 is intentionally simple and reproducible. The readback footer has five
# fixed accounting rows plus source/effective-window rows, so reserving a
# conservative floor leaves room for it and for the fixed source metadata.
TOKEN_CHARS = 4
READBACK_FOOTER_RESERVE_TOKENS = 512


def estimate_tokens(text: str) -> int:
    """Deterministic estimate: rendered characters divided by four."""
    return max(1, len(text) // TOKEN_CHARS)


def _parse_timestamp(raw: str) -> datetime | None:
    if not raw:
        return None
    try:
        value = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def filter_since_time(events: list[NormalizedEvent], config: ExtractConfig) -> list[NormalizedEvent]:
    """Apply a resolved timestamp cutoff uniformly to normalized events."""
    if config.since_time is None:
        return events
    cutoff = _parse_timestamp(config.since_time)
    if cutoff is None:
        raise ValueError("--since time must be a valid ISO-8601 timestamp")
    out: list[NormalizedEvent] = []
    for event in events:
        timestamp = _parse_timestamp(event.timestamp)
        if timestamp is None:
            continue
        if timestamp > cutoff or (config.since_time_inclusive and timestamp == cutoff):
            out.append(event)
    return out


def _readback_category(event: NormalizedEvent, config: ExtractConfig) -> str | None:
    if event.kind in (EventKind.OPERATOR_TEXT, EventKind.QA_PAIR):
        return "operator-interview"
    if event.kind is EventKind.ASSISTANT_TEXT:
        if config.hide_api_errors and classifier.is_api_error(event.text):
            return None
        return "assistant-prose"
    if event.kind in (EventKind.AGENT_OUT, EventKind.AGENT_IN) and config.agent_comms != "off":
        if event.kind is EventKind.AGENT_IN and event.meta.get("agent_result_kind") == "run_log":
            return "run-results"
        return "agent-communication"
    if event.kind is EventKind.TOOL_CALL:
        return "intent-lines"
    return None


def _event_tokens(event: NormalizedEvent, config: ExtractConfig) -> int:
    block = render.render_event_block(
        event, show_timestamps=config.show_timestamps,
        timestamp_format=config.timestamp_format,
    )
    # The fixed separator is part of every rendered item envelope.
    return estimate_tokens(block + (render.separator(config.insert_blank_lines)))


def readback_select(
    events: list[NormalizedEvent], config: ExtractConfig,
) -> tuple[list[NormalizedEvent], ReadbackReport]:
    """Priority-fill complete records under the configured token estimate."""
    if config.budget_tokens is None or config.budget_tokens < 1:
        raise ValueError("readback needs a positive --budget-tokens value")
    report = ReadbackReport(
        budget_tokens=config.budget_tokens,
        reserved_footer_tokens=READBACK_FOOTER_RESERVE_TOKENS,
        categories={name: BudgetCategory() for name in READBACK_CATEGORIES},
        agent_comms=config.agent_comms,
    )
    candidates: dict[str, list[NormalizedEvent]] = {
        name: [] for name in READBACK_CATEGORIES
    }
    by_seq = sorted(events, key=lambda event: event.seq)
    for event in by_seq:
        category = _readback_category(event, config)
        if category is not None:
            candidates[category].append(event)
    timestamps = [event.timestamp for event in by_seq if event.timestamp]
    report.source_first = timestamps[0] if timestamps else ""
    report.source_last = timestamps[-1] if timestamps else ""

    available_content_tokens = max(
        0, config.budget_tokens - READBACK_FOOTER_RESERVE_TOKENS,
    )
    remaining = available_content_tokens
    kept: list[NormalizedEvent] = []
    for category in READBACK_CATEGORIES:
        bucket = report.categories[category]
        for event in reversed(candidates[category]):
            tokens = _event_tokens(event, config)
            # Operator turns and answered interviews are the material a
            # readback exists to preserve. They are never budget-dropped:
            # lower-priority classes yield instead, and the footer reports
            # an explicit overrun when the protected class alone exceeds the
            # requested content budget.
            if category == READBACK_PROTECTED_CATEGORY or tokens <= remaining:
                kept.append(event)
                remaining -= tokens
                report.estimated_content_tokens += tokens
                if category == READBACK_PROTECTED_CATEGORY:
                    report.protected_content_tokens += tokens
                bucket.kept += 1
            else:
                bucket.dropped += 1
                bucket.dropped_chars += len(event.text)
                bucket.dropped_tokens += tokens
                if event.timestamp:
                    bucket.dropped_first = (
                        event.timestamp if not bucket.dropped_first
                        else min(bucket.dropped_first, event.timestamp)
                    )
                    bucket.dropped_last = (
                        event.timestamp if not bucket.dropped_last
                        else max(bucket.dropped_last, event.timestamp)
                    )
    report.budget_overrun_tokens = max(
        0, report.protected_content_tokens - available_content_tokens,
    )
    kept.sort(key=lambda event: event.seq)
    return kept, report


def decide(ev: NormalizedEvent, config: ExtractConfig) -> EventDecision:
    """The windowing contract's per-event half, with no reference to how
    much has already been kept -- see this module's docstring for the rules
    and the real-data evidence behind each, and follow.py for the forward-
    stream caller this was factored out for.

    An ASSISTANT_TEXT's `checkpoint_score` must already be set (extract()
    runs classifier.score_events over the full parse first; follow.py
    computes it per event, which is why its own one-event-delay buffer
    exists -- see that module).
    """
    if ev.kind is EventKind.LIFECYCLE_MARKER:
        return EventDecision(keep=True, is_lifecycle_marker=True)

    if ev.kind in (EventKind.OPERATOR_TEXT, EventKind.QA_PAIR, EventKind.TOOL_CALL):
        return EventDecision(keep=True)
    if ev.kind in (EventKind.AGENT_OUT, EventKind.AGENT_IN) and config.agent_comms != "off":
        return EventDecision(keep=True)

    if ev.kind in (EventKind.ASSISTANT_TEXT, EventKind.THINKING):
        if (config.hide_api_errors and ev.kind is EventKind.ASSISTANT_TEXT
                and classifier.is_api_error(ev.text)):
            # Dropped unconditionally -- not even considered for
            # checkpoint/finding-signal rescue -- per config.py's
            # hide_api_errors.
            return EventDecision(keep=False)
        if (ev.kind is EventKind.ASSISTANT_TEXT
                and (ev.checkpoint_score or 0.0) >= config.checkpoint_score_threshold):
            return EventDecision(keep=True, is_checkpoint=True)
        if len(ev.text) > config.long_comment_chars or classifier.has_finding_signal(ev.text):
            return EventDecision(keep=True)

    return EventDecision(keep=False)


def select(events: list[NormalizedEvent], config: ExtractConfig) -> list[NormalizedEvent]:
    kept: list[NormalizedEvent] = []
    checkpoints_found = 0
    word_count = 0
    compactions_passed = 0
    last_kept_seq: int | None = None

    timestamps = [_parse_timestamp(ev.timestamp) for ev in events]
    end_timestamp = max((value for value in timestamps if value is not None), default=None)
    if config.max_time_minutes != -1 and events and any(value is None for value in timestamps):
        raise ValueError(
            "--max-time-minutes requires a valid timestamp on every selected event"
        )

    def _keep(ev: NormalizedEvent) -> None:
        nonlocal last_kept_seq
        if last_kept_seq is not None:
            gap = last_kept_seq - ev.seq - 1
            if gap > 0:
                ev.meta["gap_after"] = str(gap)
        kept.append(ev)
        last_kept_seq = ev.seq

    def _record_stop(
        reason: str, older_events_remain: bool, *, current_omitted: bool = False,
    ) -> None:
        if kept and (older_events_remain or current_omitted):
            kept[-1].meta["walk_stopped_because"] = reason

    for reverse_index, ev in enumerate(reversed(events)):
        older_events_remain = reverse_index + 1 < len(events)

        event_timestamp = _parse_timestamp(ev.timestamp)
        if (config.max_time_minutes != -1 and event_timestamp is not None
                and end_timestamp is not None
                and (end_timestamp - event_timestamp).total_seconds() > config.max_time_minutes * 60):
            _record_stop(
                "max_time_minutes", older_events_remain, current_omitted=True,
            )
            break

        # A rejected event is not skipped silently -- it falls into the
        # next-older kept event's own gap_after count, exactly like a raw
        # record that never became a NormalizedEvent at all.
        verdict = decide(ev, config)

        if verdict.is_lifecycle_marker:
            _keep(ev)
            # A marker's own text counts toward the budget too (2026-09-11
            # bug fix -- previously never added at all, so a large marker
            # body, e.g. an operator's uncounted /compact <prompt> dispatch
            # under --show-compaction-content, silently blew straight
            # through --max-words without ever tripping the stop-check
            # below). A marker is still NEVER rejected for its own length --
            # LIFECYCLE_MARKER is unconditionally kept regardless of budget
            # -- this only makes the RUNNING TOTAL honest, so OLDER content
            # past this point is correctly trimmed once the budget the
            # marker itself helped exhaust is actually exceeded. Falls
            # through (no `continue`) to the shared max_words check at the
            # bottom of the loop, exactly like every other kept event.
            word_count += len(ev.text.split())
            boundary_type = ev.meta.get("boundary_type")
            if boundary_type == "compaction" or (
                boundary_type is None and ev.text.startswith((
                    "[compacted]", "[context_compacted]", "[compaction:", "[compact boundary]",
                ))
            ):
                may_pass = config.max_compactions == -1 or compactions_passed < config.max_compactions
                if not may_pass:
                    _record_stop("max_compactions", older_events_remain)
                    break
                compactions_passed += 1

        elif verdict.keep:
            _keep(ev)
            word_count += len(ev.text.split())
            if verdict.is_checkpoint:
                checkpoints_found += 1
                if config.max_checkpoints != -1 and checkpoints_found >= config.max_checkpoints:
                    _record_stop("max_checkpoints", older_events_remain)
                    break

        if config.max_words != -1 and word_count > config.max_words:
            _record_stop("max_words", older_events_remain)
            break

    kept.reverse()
    return kept
