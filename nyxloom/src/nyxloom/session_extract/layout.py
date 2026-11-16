"""`--preset readback` LAYOUT: goal anchor + STATE + recent prose + delta.

The readback preset used to emit a delta alone (whatever came after the
previous read-back's marker). The operator decision of 2026-11-15 makes the
document a recovery artifact for an AUTO-compacted session, so it can carry
four parts, in this order:

1. **the goal anchor** -- OPT-IN: the session's first genuine user prompt
   (`--goal-prompt`), or a pinned text (`--goal-file`). A SPAWNED or targeted
   session anchors on its original goal; a CONTROLLER session does not ask for
   it, because its anchor is `plan.yaml` and its goal shifts over time (the
   operator refinement of 2026-11-15, root `AGENTS.md`). When asked for, it is
   included ONCE per read-back (it is the anchor, so it repeats in every
   read-back by design) and is NEVER truncated. Harness framing is not a goal:
   the Claude Code adapter already drops `isMeta`/`isVisibleInTranscriptOnly`
   records, tool results and `/compact`/`/clear` promotions, tags
   slash-command invocations (`meta["slash_command"]`) which this module
   skips, and any remaining `<system-reminder>` block is stripped here.
2. **the STATE section** -- OPT-IN (`--state-file PATH`), operator decision
   2026-11-16: the LIVING state file an agent writes for its own continuation
   (done / decisions and why / next steps / open questions / exact commands).
   It is the "what is left" half of the primer, so it sits after the anchor
   and BEFORE the recent prose: a resumed session must read its orders before
   it reads its history. The text is rendered VERBATIM (bounded by the budget,
   never summarized) under a header that says where it came from and what it
   is for; it is redacted like every other kept paragraph. Works for every
   harness -- the file belongs to the agent, not to a CLI's format.
3. **the recent prose tail** (`--tail-prose-tokens N`) -- the NEWEST
   user/assistant prose of the session AT THE MOMENT THE READ-BACK IS BUILT
   (the end of the transcript, the PreCompact point), complete records only,
   limited to what comes after the previous read-back's marker so it never
   repeats an earlier read-back. (Operator ruling 2026-11-15: "the most recent
   prose of maybe 10k tokens ... plus the delta".)
4. **the delta** -- the `--since <marker>` selection (the whole session when
   there is no marker) MINUS every record the anchor or the tail already carries,
   so nothing is emitted twice inside one read-back either. When the tail covers
   the whole delta the section says so. On the first read-back (no marker) the
   delta is the part of the session before the tail, bounded by the budget.

Budget and truncation order (`--budget-tokens N`, controller profile 30000):
the goal anchor (when asked for) never shrinks; the STATE section has priority
over the delta (it shrinks only to a paragraph-boundary truncation marker, and
only AFTER the delta's droppable classes are gone -- the state file is what a
successor acts on); the tail prose then shrinks (it also yields to the delta's
protected operator material, which is never dropped); the delta finally fills
whatever is left (its own priorities are unchanged -- select.readback_select
still never drops an operator turn, and discloses a protected overrun instead).

A STATE section cut by the budget ends with an explicit marker naming the
bytes/tokens it dropped and the file it left behind, and the footer's own
`state_truncated=`/`state_chars=` accounting reports the cut a SECOND time, so
a truncated primer is visible even to a reader who only skims the trailers. The
cut falls at a paragraph boundary when one fits, then at a line boundary, then
(with a state file that has no blank lines and one long first block) at a hard
character boundary -- it never collapses to the marker alone, because a primer
that shows a state file's existence but none of its content is the worst of
both.

The document ends with a `[readback marker: ...]` line naming the source
marker the NEXT read-back must use as `--since` (the same marker the cursor
comment carries, so `--since-file` keeps working).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from pathlib import Path

from . import classifier, mangle, render, select
from .config import ExtractConfig
from .events import EventKind, NormalizedEvent
from .select import READBACK_FOOTER_RESERVE_TOKENS, estimate_tokens

GOAL_HEADER = (
    "## Goal of the session (anchor -- included once per read-back, never truncated)"
)
GOAL_MISSING = (
    "[goal: this session has no genuine user prompt to anchor on; "
    "pass --goal-file to pin one]"
)
STATE_HEADER = (
    "## STATE (forward-oriented, written by the agent -- what is left to do, "
    "verbatim)"
)
STATE_SOURCE_NOTE = (
    "The agent wrote this file for its own continuation: done, decisions and why, "
    "next steps, open questions and exact commands. It outranks the history below."
)
DELTA_HEADER = "## Delta since the previous read-back"
TAIL_EMPTY_NOTE = (
    "[no user/assistant prose record after the cut fitted the tail budget or exists]"
)
DELTA_COVERED_NOTE = "[the recent prose above covers the whole delta]"
DELTA_EMPTY_NOTE = "[the delta is empty: no record qualifies after the cut]"

#: The recent-prose tail keeps at most this many tokens (and at most half the
#: content budget) reserved when a `--state-file` is present: the primer's
#: "what is left" must never push out the session's own last words entirely,
#: and those last words must never starve the state file either. The tail's own
#: shrink loop still runs LAST, in its documented order, so protected operator
#: material in the delta keeps its absolute priority.
TAIL_MINIMUM_TOKENS = 1_000

# Harness injected framing that is NOT operator intent. The Claude Code
# adapter strips the other tags it knows (command wrappers, local-command
# output, task notifications) and drops isMeta records; a system-reminder
# block can still ride along inside a genuine prompt, so it is stripped here.
_SYSTEM_REMINDER_RE = re.compile(
    r"<system-reminder\b[^>]*>.*?</system-reminder>", re.IGNORECASE | re.DOTALL,
)


def enabled(config: ExtractConfig) -> bool:
    """Whether this run asks for the full layout (any of its four knobs)."""
    return bool(
        config.goal_prompt or config.goal_file or config.state_file
        or config.tail_prose_tokens
    )


def strip_system_reminders(text: str) -> str:
    return _SYSTEM_REMINDER_RE.sub("", text).strip()


def key_of(event: NormalizedEvent) -> tuple[int, str]:
    """Identity of one source record's event within one parse (used to keep a
    record the layout already carries out of the delta and the tail)."""
    return (event.seq, event.kind.value)


def redact_text(text: str, config: ExtractConfig) -> str:
    """The same paragraph redaction every kept event gets (mangle.py), applied
    to the anchor text so a `--redact-pattern` cannot leak back in through it."""
    if not config.redact_patterns:
        return text
    holder = NormalizedEvent(0, "redact", "", EventKind.ASSISTANT_TEXT, text)
    mangle.redact_paragraphs([holder], list(config.redact_patterns))
    return holder.text


def goal_candidate(events: list[NormalizedEvent]) -> tuple[NormalizedEvent, str] | None:
    """The session's first genuine user prompt: the first OPERATOR_TEXT event
    that is not a slash-command invocation and is not empty once harness
    framing is removed. None when the session has no such prompt."""
    for event in events:
        if event.kind is not EventKind.OPERATOR_TEXT:
            continue
        if event.meta.get("slash_command"):
            continue
        text = strip_system_reminders(event.text)
        if text:
            return event, text
    return None


def prose_events(
    events: list[NormalizedEvent], config: ExtractConfig,
) -> list[NormalizedEvent]:
    """User/assistant prose only -- never tool output, never a lifecycle
    marker, and never API-transport noise (the same hide_api_errors rule the
    delta selection applies)."""
    out: list[NormalizedEvent] = []
    for event in events:
        if event.kind is EventKind.OPERATOR_TEXT or event.kind is EventKind.QA_PAIR:
            out.append(event)
        elif event.kind is EventKind.ASSISTANT_TEXT:
            if config.hide_api_errors and classifier.is_api_error(event.text):
                continue
            out.append(event)
    return out


@dataclass
class TailReport:
    budget_tokens: int = 0
    kept: int = 0
    dropped: int = 0
    kept_tokens: int = 0
    dropped_tokens: int = 0


@dataclass
class LayoutPlan:
    """What extract() needs from the layout: leading sections, the delta
    content limit, the records already carried (so the delta cannot repeat
    them), and the footer accounting lines."""

    sections: list[str] = field(default_factory=list)
    footer_lines: list[str] = field(default_factory=list)
    exclude_keys: set[tuple[int, str]] = field(default_factory=set)
    content_limit_tokens: int = 0
    goal_tokens: int = 0
    tail: TailReport | None = None
    goal_source: str = "none"
    redacted_paragraphs: int = 0
    state_source: str = ""
    state_tokens: int = 0
    state_truncated: bool = False
    #: Chars of the state file the section actually carries, out of the full
    #: (post-redaction) size: the footer reports both, so a truncated STATE is
    #: visible in the ACCOUNTING and not only inside the section's own marker.
    state_kept_chars: int = 0
    state_total_chars: int = 0


def _tail_block(event: NormalizedEvent, config: ExtractConfig) -> str:
    return render.render_event_block(
        event, show_timestamps=config.show_timestamps,
        timestamp_format=config.timestamp_format,
    )


def select_tail(
    events: list[NormalizedEvent], budget_tokens: int, config: ExtractConfig,
) -> tuple[list[NormalizedEvent], TailReport]:
    """Newest-first complete records under the tail budget; nothing already
    accepted is ever un-accepted (the same rule select.py's walk follows)."""
    report = TailReport(budget_tokens=budget_tokens)
    separator = render.separator(config.insert_blank_lines)
    kept: list[NormalizedEvent] = []
    remaining = budget_tokens
    for event in reversed(events):
        tokens = estimate_tokens(_tail_block(event, config) + separator)
        if tokens <= remaining:
            kept.append(event)
            remaining -= tokens
            report.kept += 1
            report.kept_tokens += tokens
        else:
            report.dropped += 1
            report.dropped_tokens += tokens
    kept.reverse()
    return kept, report


def tail_header(config: ExtractConfig) -> str:
    return (
        "## Recent prose (the newest user/assistant prose of the session, "
        f"{config.tail_prose_tokens} tokens requested; the delta below does not repeat it)"
    )


def cut_header(config: ExtractConfig) -> str:
    if config.since_marker is not None:
        return f"{DELTA_HEADER} (strictly after marker {config.since_marker})"
    if config.since_time is not None:
        return f"{DELTA_HEADER} (after {config.since_time})"
    return (
        f"{DELTA_HEADER} (none: no previous read-back marker was given, so this is the "
        "part of the session before the recent prose)"
    )


def state_header(config: ExtractConfig) -> str:
    """The STATE section's header, naming the file it came from."""
    return STATE_HEADER + f"\n[state file: {config.state_file}]\n" + STATE_SOURCE_NOTE


def state_truncation_marker(text: str, kept_chars: int, path: str) -> str:
    """The marker ending a STATE section cut by the budget.

    It names what was dropped and where the rest still lives, so a successor
    reads the file instead of assuming the primer is the whole state, and
    states the boundary the cut fell on -- half an instruction is exactly what
    a resumed agent misreads.
    """
    omitted = len(text) - kept_chars
    boundary = "a paragraph boundary" if "\n\n" in text[:kept_chars] else "a line/character boundary"
    return (
        f"[state truncated: {omitted} of {len(text)} chars omitted to fit "
        f"the budget at {boundary} -- read {path} in full before acting]"
    )


def paragraph_cut(text: str, limit_chars: int) -> int:
    """Length of the longest paragraph-boundary prefix of `text` fitting
    `limit_chars`: the cut falls after a blank line, never mid-paragraph.
    Returns 0 when not even the first paragraph fits, so the caller can choose
    a different boundary instead of emitting half an instruction, and len(text)
    when everything fits -- the loop's own normal exit, so there is no
    impossible-to-reach branch here."""
    kept = 0
    for block in text.split("\n\n"):
        needed = 2 if kept else 0
        if kept + needed + len(block) > limit_chars:
            break
        kept += needed + len(block)
    return kept


def _line_cut(text: str, limit_chars: int) -> int:
    """Longest prefix ending at a NEWLINE boundary within `limit_chars`."""
    if limit_chars <= 0:
        return 0
    window = text[:limit_chars]
    index = window.rfind("\n")
    return index + 1 if index >= 0 else 0


def paragraph_then_line_cut(text: str, limit_chars: int) -> int:
    """Truncation position for a bounded STATE section, best boundary first.

    A paragraph boundary is the IDEAL cut (a successor must not act on half an
    instruction). A state file with no blank lines, or whose FIRST paragraph is
    longer than the whole budget, used to collapse to the marker alone: the
    primer then showed the reader a state file's existence but none of its
    content, which is the worst of both. So the order is paragraph boundary,
    then line boundary, then a hard character cut -- never zero while any room
    remains.
    """
    kept = paragraph_cut(text, limit_chars)
    if kept:
        return kept
    kept = _line_cut(text, limit_chars)
    if kept:
        return kept
    return max(0, min(limit_chars, len(text)))


def _state_render(config: ExtractConfig, text: str) -> str:
    return state_header(config) + "\n" + text


def _state_section(
    config: ExtractConfig, budget_tokens: int,
) -> tuple[str, int, bool, int, int, int]:
    """(rendered section, tokens, truncated, redacted paragraphs, kept chars,
    total chars of the (post-redaction) state text).

    Rendered VERBATIM -- the agent's own words are the point of the section --
    with the same paragraph redaction every kept event gets, and bounded by
    `budget_tokens`; a cut is disclosed with its own marker rather than
    silently shortened.
    """
    separator = render.separator(config.insert_blank_lines)
    path = str(config.state_file)
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"--state-file {path}: {exc}") from exc
    except UnicodeDecodeError as exc:
        # The message names the flag AND the path: "a read failed" and "this
        # file is not UTF-8 text" are different problems with different fixes.
        raise ValueError(
            f"--state-file {path}: not UTF-8 text ({exc.reason} at byte "
            f"{exc.start}) -- save the state file as UTF-8"
        ) from exc
    text = text.strip()
    if not text:
        raise ValueError(f"--state-file {path}: the file is empty")
    redacted = 0
    if config.redact_patterns:
        holder = NormalizedEvent(0, "state", "", EventKind.ASSISTANT_TEXT, text)
        _, redacted = mangle.redact_paragraphs([holder], list(config.redact_patterns))
        text = holder.text
    full_tokens = estimate_tokens(_state_render(config, text) + separator)
    if full_tokens <= budget_tokens:
        return _state_render(config, text), full_tokens, False, redacted, len(text), len(text)
    # Bounded: the header and the marker line are the section's own cost and
    # stay whole; only the state text is cut, best boundary first.
    header_tokens = estimate_tokens(_state_render(config, "") + separator)
    marker_tokens = estimate_tokens(state_truncation_marker(text, 0, path) + separator)
    remaining = max(0, budget_tokens - header_tokens - marker_tokens)
    kept_chars = paragraph_then_line_cut(text, remaining * select.TOKEN_CHARS)
    kept_text = text[:kept_chars].rstrip()
    marker = state_truncation_marker(text, kept_chars, path)
    body = f"{kept_text}\n\n{marker}" if kept_text else marker
    section = _state_render(config, body)
    return section, estimate_tokens(section + separator), True, redacted, kept_chars, len(text)


def _goal_section(
    config: ExtractConfig, full_events: list[NormalizedEvent],
) -> tuple[str, int, str, set[tuple[int, str]]]:
    """(rendered section, tokens, source name, records already carried)."""
    separator = render.separator(config.insert_blank_lines)
    carried: set[tuple[int, str]] = set()
    if config.goal_file is not None:
        text = redact_text(Path(config.goal_file).read_text(encoding="utf-8").strip(), config)
        if not text:
            raise ValueError(f"--goal-file {config.goal_file}: the file is empty")
        section = GOAL_HEADER + "\nOPERATOR: " + text
        return section, estimate_tokens(section + separator), "file", carried
    found = goal_candidate(full_events)
    if found is None:
        section = GOAL_HEADER + "\n" + GOAL_MISSING
        return section, estimate_tokens(section + separator), "none", carried
    event, text = found
    carried.add(key_of(event))
    section = GOAL_HEADER + "\nOPERATOR: " + redact_text(text, config)
    return section, estimate_tokens(section + separator), "session", carried


def build(
    *,
    full_events: list[NormalizedEvent],
    delta_events: list[NormalizedEvent],
    config: ExtractConfig,
    last_marker: str | None,
) -> LayoutPlan:
    """Assemble the parts and the accounting for one read-back.

    `delta_events` is the delta's selected source events (everything after the
    previous marker, the whole session when there is none): the tail is the
    newest prose among them and the delta is the rest; `full_events` is the
    whole-session parse, used only to find the goal anchor."""
    if config.budget_tokens is None:
        raise ValueError(
            "the readback layout needs --preset readback (its --budget-tokens)"
        )
    separator = render.separator(config.insert_blank_lines)
    plan = LayoutPlan()

    goal_section = ""
    if config.goal_prompt or config.goal_file:
        goal_section, plan.goal_tokens, plan.goal_source, carried = _goal_section(
            config, full_events,
        )
        plan.sections.append(goal_section)
        plan.exclude_keys |= carried

    tail_section = ""
    if config.tail_prose_tokens:
        tail_section = tail_header(config)

    # Fixed overhead this layout adds: four section headers plus its own two
    # footer lines. It comes out of the budget BEFORE the STATE, the tail and
    # the delta, so the promised bound covers the whole document, not just its
    # records.
    state_section = ""
    overhead = estimate_tokens(
        separator.join(
            [
                goal_section, state_section, tail_section,
                TAIL_EMPTY_NOTE if tail_section else "",
                cut_header(config), DELTA_EMPTY_NOTE, "",
            ]
        ) + separator
    )
    marker_line = f"[readback marker: {last_marker or 'none'}]"
    # Deliberately terse: these lines are part of the document's own cost, and a
    # tight budget must go to the record it is meant to carry, not to prose
    # about the layout.
    layout_footer = (
        "[readback layout: goal=SOURCE goal_tokens=G "
        "state=K state_tokens=ST state_truncated=T state_chars=KC/TC "
        "tail=K/T tail_tokens=KT delta_tokens=C]"
    )
    fixed_tokens = READBACK_FOOTER_RESERVE_TOKENS + overhead + estimate_tokens(
        marker_line + layout_footer
    )
    content_budget = max(0, config.budget_tokens - fixed_tokens)

    # The floor this document cannot go below: the goal anchor (never
    # truncated) plus the fixed scaffolding. A budget under that floor is
    # honoured by emitting the floor in full and disclosing the overrun
    # (exactly how the existing protected operator/interview class behaves),
    # instead of silently dropping part of the anchor.
    floor_tokens = fixed_tokens + plan.goal_tokens
    if floor_tokens > config.budget_tokens:
        plan.footer_lines.append(
            "[readback warning: anchor plus fixed scaffolding exceed the limit of "
            f"{config.budget_tokens} by {floor_tokens - config.budget_tokens} estimated "
            "tokens; the anchor is never truncated]"
        )

    available = max(0, content_budget - plan.goal_tokens)
    if config.state_file is not None:
        # Operator decision 2026-11-16: the STATE file is the "what is left"
        # half of a successor primer, so it takes its room out of everything
        # except the tail's requested budget -- priority over the DELTA, not
        # over the recent prose that finishes the picture. It is rendered
        # verbatim and truncated only at a paragraph boundary, with a marker
        # naming what was left out.
        reserved_tail = min(
            config.tail_prose_tokens, TAIL_MINIMUM_TOKENS, available // 2,
        )
        state_budget = available - reserved_tail
        # A budget too small for anything still yields the section's header
        # and the truncation marker: the disclosure is the point, and the
        # document reports its own overrun below rather than hiding the file.
        (state_section, state_tokens, truncated, state_redacted,
         state_kept, state_total) = _state_section(config, max(state_budget, 1))
        plan.sections.append(state_section)
        plan.state_tokens = state_tokens
        plan.state_truncated = truncated
        plan.state_source = str(config.state_file)
        plan.state_kept_chars = state_kept
        plan.state_total_chars = state_total
        plan.redacted_paragraphs += state_redacted
        available = max(0, available - state_tokens)

    if config.tail_prose_tokens:
        # The tail is the NEWEST prose of the delta region (the end of the
        # session), so it is taken from the delta's own selected events.
        # Detached copies: it is rendered here, BEFORE extract()'s own
        # post-selection redaction runs on the delta, so --redact-pattern has
        # to be applied to the tail records here or the tail would leak what
        # the delta hides.
        candidates = [
            replace(event, meta=dict(event.meta))
            for event in prose_events(delta_events, config)
            if key_of(event) not in plan.exclude_keys
        ]
        if config.redact_patterns:
            _, plan.redacted_paragraphs = mangle.redact_paragraphs(
                candidates, list(config.redact_patterns),
            )
        # Truncation order: the tail shrinks first. It also yields to the
        # delta's protected operator/interview material that it does NOT carry
        # (never dropped, so otherwise the document would overshoot by a whole
        # tail); an operator turn cut from the tail falls back into the delta,
        # hence the loop. Each pass strictly lowers the budget, so it ends.
        budget = config.tail_prose_tokens
        while True:
            kept_events, plan.tail = select_tail(candidates, budget, config)
            taken = plan.exclude_keys | {key_of(event) for event in kept_events}
            protected = select.protected_readback_tokens(delta_events, config, taken)
            excess = plan.tail.kept_tokens + protected - available
            if excess <= 0 or plan.tail.kept_tokens == 0:
                break
            budget = max(0, plan.tail.kept_tokens - excess)
        plan.exclude_keys = taken
        blocks = [_tail_block(event, config) for event in kept_events]
        plan.sections.append(
            "\n\n".join([tail_section, *blocks]) if blocks
            else tail_section + "\n" + TAIL_EMPTY_NOTE
        )

    delta_limit = max(0, available - (plan.tail.kept_tokens if plan.tail else 0))
    plan.content_limit_tokens = delta_limit
    delta_section = cut_header(config)
    if not select.readback_candidate_count(delta_events, config, plan.exclude_keys):
        delta_section += "\n" + (DELTA_COVERED_NOTE if plan.tail and plan.tail.kept else DELTA_EMPTY_NOTE)
    plan.sections.append(delta_section)
    plan.footer_lines[:0] = [
        (
            "[readback layout: "
            f"goal={plan.goal_source} goal_tokens={plan.goal_tokens} "
            f"state={'file' if plan.state_source else 'none'} state_tokens={plan.state_tokens} "
            f"state_truncated={'1' if plan.state_truncated else '0'} "
            f"state_chars={plan.state_kept_chars}/{plan.state_total_chars} "
            f"tail={plan.tail.kept if plan.tail else 0}/"
            f"{plan.tail.budget_tokens if plan.tail else 0} "
            f"tail_tokens={plan.tail.kept_tokens if plan.tail else 0} "
            f"delta_tokens={delta_limit}]"
        )
    ]
    plan.footer_lines.append(marker_line)
    return plan
