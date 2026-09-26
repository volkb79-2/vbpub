"""`nyxloom extract-debug` -- a colored diff between the lossless base for
the requested source span (`lossless.py`'s own "dumb, independent" dump, the
ground truth of everything in that span it can recover) and what a given `extract()` call
--profile/--max-words/etc. actually kept, so a reader can see EXACTLY what a
profile/budget threw away, in place, instead of trusting a bare word count
or re-deriving it by eye. Operator ask, verbatim: "it compares our dumb
lossless extraction output with a given profile/parameter -- identical how
we would call `extract`."

The VISIBLE grey/white diff itself stays a TEXT-level comparison
(`difflib.SequenceMatcher` over each side's own rendered blocks, normalized
only enough to strip each side's own formatting) -- `lossless.py` is an
independent raw-record reader that deliberately does NOT share code with the
adapters (see its own module docstring, point 1 -- "an unbiased ground
truth" for judging the smart classifier), and a text diff shows what's in
the final output vs not regardless of which stage did the cutting.

WHY a grey block was dropped, however, is no longer re-derived from text
alone (see the "yellow" bullet below) -- as of 2026-09-11 it's resolved
against the real, fully-scored pre-selection event list
(`ExtractResult.all_events`) by exact marker/uuid identity, per operator
direction: "we should always know why we excluded something ... should
never happen [that the reason is unclear]." `lossless.py`'s block headers
now carry the SAME marker (`uuid`, or a `line<n>`/positional fallback) the
real adapter assigns as a NormalizedEvent's own `.marker` -- see each
`dump_*` function's own docstring -- so a lossless block and the event the
real adapter derived from the identical source record can be looked up by
exact identity instead of approximated from isolated re-rendered text.

Color scheme (operator's own spec, extended 2026-09-11):
- **white** (no color code): identical in both -- verbatim lossless content
  that also survived into the extract output.
- **grey**: lossless-only -- content the extract run dropped. A CONTIGUOUS
  run of dropped lossless blocks gets ONE bracketing `>>> ... <<<` note (how
  MANY were dropped), not one per block -- but each individual block still
  gets its own **yellow** reason line right after it, since two adjacent
  dropped blocks can be dropped for entirely different reasons (see
  `_drop_reason` below).
- **yellow**: WHY a grey block wasn't kept. Checked in strictly decreasing
  order of certainty -- an adapter-level, header-visible flag first (claude-
  code only: isMeta/isVisibleInTranscriptOnly/interruptedMessageId/
  isSidechain, or a THINKING block dropped because --include-thinking
  wasn't passed), then the real production harness-tag-only check
  (adapters.claude_code.is_harness_tag_only), then a marker-resolved lookup
  against the real NormalizedEvent this exact record produced (its REAL
  checkpoint_score -- already including score_events()'s lookahead "followed
  by a pause" bonus, not a text-only re-derivation of it -- and REAL
  is_api_error/has_finding_signal/length against ITS OWN text, not the
  lossless block's independently-rendered text). Every branch names a
  concrete cause; see `_drop_reason`'s own docstring for the one residual,
  honestly-labeled case (no matching event found at all -- an adapter-level
  drop this module can't further pin down) and the one known imprecision
  (a message with multiple content blocks sharing one marker).
- **blue**: NEW 2026-09-11 -- "detected checkpoint" tag, on ANY block (grey
  OR white) whose real NormalizedEvent scored at/above
  config.checkpoint_score_threshold -- operator direction: "detected
  checkpoints should be labelled in the debug ... showing that they were
  detected as such." A white one was walked and kept for exactly that
  reason; a grey one was detected but the walk never reached it (see its
  yellow line) or a same-marker sibling block was the one actually kept.
- **cyan**: nyxloom's own commentary -- the `>>> ... <<<` gap-count wrapper
  this module adds around a dropped run, AND any extract-only insertion
  that's a bracketed nyxloom-authored note (`[gap: ...]`, `[older session
  content...]`) rather than real transcript content.
- **green**: real added value -- an E-012 ledger line (`[files read: ...]`
  etc., see `ledger.py`) that has no lossless counterpart because it's
  synthesized, not verbatim transcript.

This module's own gap count ("N lossless blocks dropped") is intentionally
a SEPARATE number from render.py's own `[gap: N records omitted]` note
(which may also appear here, colored cyan, as an extract-only insertion) --
they count different units (lossless.py's own block granularity vs the
adapter's raw seq-unit granularity) and conflating them into one number
would be a wrong, not just imprecise, label. Showing both, distinctly
labeled, is more honest than merging them.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

from . import classifier, mangle, select
from .events import EventKind, NormalizedEvent

_LOSSLESS_HEADER_RE = re.compile(r"^===\[[^\]]*\]===\n", re.MULTILINE)
_HEADER_PARSE_RE = re.compile(r"^===\[([^|\]]*)\|([^|\]]*)\|([^\]]*)\]===")
_OPERATOR_PREFIX = "OPERATOR: "
_MARKER_FOOTER_RE = re.compile(r"\n<!-- nyxloom-extract: format=\S+ marker=\S+(?:\s+[^>]*?)? -->\n$")
_METADATA_COMMENT_RE = re.compile(r"^<!-- nyxloom-extract: format=\S+ marker=\S+(?:\s+[^>]*?)? -->\n", re.MULTILINE)

_RESET = "\x1b[0m"
_GREY = "\x1b[90m"
_CYAN = "\x1b[36m"
_GREEN = "\x1b[32m"
_YELLOW = "\x1b[33m"
_BLUE = "\x1b[34m"

# Prefixes render.py/ledger.py themselves use for nyxloom-authored bracketed
# notes -- kept in sync by hand (small, stable set); see each module's own
# note-formatting code (render.py's _gap_note/_STOP_REASON_TEXT,
# ledger.py's Ledger.render()).
_CYAN_NOTE_PREFIXES = ("[gap:", "[older session content", "[epoch ")
_GREEN_NOTE_PREFIXES = ("[files read:", "[files edited:", "[commits created:", "[branches involved:", "[tests:")
_ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


def _lossless_blocks(text: str) -> list[str]:
    text = _MARKER_FOOTER_RE.sub("", text.strip())
    # lossless.py's own block-join separator is exactly "\n\n" between two
    # "===[...]===" headers -- split right before each header, matching it.
    # (`={3}` spelled as a repetition, not 4 stacked literal "=" chars,
    # since "(?===\[)" is only 3 "=" total -- one too few, and silently
    # matches nothing rather than raising, which is what "\n\n===[" needs.)
    parts = re.split(r"\n\n(?=={3}\[)", text)
    return [p for p in parts if p.strip()]


def _extract_blocks(text: str) -> list[str]:
    text = _MARKER_FOOTER_RE.sub("", text.strip())
    text = _METADATA_COMMENT_RE.sub("", text)
    # render.separator() permits any configured blank-line count, and -1
    # fuses the separator onto the preceding block. Parse that complete
    # grammar here so extract-debug stays aligned with extract for every
    # rendering choice, not just the default one or two newline forms.
    parts = re.split(
        r"(?:\n+|[ \t])---(?:[ \t]+[^\n]*?)?[ \t]*---(?:\n+|$)|"
        r"(?:\n+|[ \t])---(?:\n+|$)",
        text,
    )
    return [p for p in parts if p.strip()]


def _normalize_lossless(block: str) -> str:
    return _ANSI_RE.sub("", _LOSSLESS_HEADER_RE.sub("", block, count=1)).strip()


def _normalize_extract(block: str, timestamp_values: set[str] | None = None) -> str:
    b = _ANSI_RE.sub("", block).strip()
    if b.startswith(_OPERATOR_PREFIX):
        b = b[len(_OPERATOR_PREFIX):]
    if timestamp_values:
        for value in timestamp_values:
            if b.startswith(value + " "):
                b = b[len(value) + 1:]
                break
        for value in timestamp_values:
            if b.endswith(" " + value):
                b = b[:-(len(value) + 1)]
                break
        return b.strip()
    return re.sub(r"^\[[^\]]+\]\s+", "", b, count=1)


def _render_lossless_block(block: str, block_render) -> str:
    if block_render is None:
        return block
    header, sep, body = block.partition("\n")
    return header + sep + block_render(body) if sep else block_render(header)


def _note_color(block: str) -> str | None:
    b = block.strip()
    if any(b.startswith(p) for p in _CYAN_NOTE_PREFIXES):
        return _CYAN
    if any(b.startswith(p) for p in _GREEN_NOTE_PREFIXES):
        return _GREEN
    return None


def _parse_header(raw_block: str) -> tuple[str, str] | None:
    """Pull (marker, tag) out of a lossless block's own
    `===[marker | ts | tag]===` header -- every `dump_*` function in
    lossless.py emits this exact shape. Returns None for a malformed/missing
    header (defensive only; every real lossless block has one)."""
    m = _HEADER_PARSE_RE.match(raw_block)
    if not m:
        return None
    marker, _ts, tag = (g.strip() for g in m.groups())
    return marker, tag


def _expected_kinds(tag: str) -> tuple[EventKind, ...]:
    """Which NormalizedEvent kind(s) a real adapter would produce for a
    record carrying this lossless tag -- used to filter a marker's
    candidate events down to the ones actually comparable to THIS block
    (a single marker can cover more than one event -- see
    `_marker_lookup`'s own docstring)."""
    role = tag.split()[0] if tag else ""
    if tag.endswith(" thinking") or tag == "thinking":
        return (EventKind.THINKING,)
    if role == "ASSISTANT":
        return (EventKind.ASSISTANT_TEXT,)
    if role == "USER":
        return (EventKind.OPERATOR_TEXT, EventKind.QA_PAIR)
    if role in {"INTERVIEW", "QA_PAIR"}:
        return (EventKind.QA_PAIR,)
    if role == "TOOL_CALL":
        return (EventKind.TOOL_CALL,)
    if role == "SYSTEM" or role == "LIFECYCLE":
        return (EventKind.LIFECYCLE_MARKER,)
    return ()


def _is_checkpoint_event(ev: NormalizedEvent, config) -> bool:
    return ev.kind is EventKind.ASSISTANT_TEXT and (ev.checkpoint_score or 0.0) >= config.checkpoint_score_threshold


def _index_events(all_events: list[NormalizedEvent]) -> dict[str, list[NormalizedEvent]]:
    by_marker: dict[str, list[NormalizedEvent]] = {}
    for ev in all_events:
        by_marker.setdefault(ev.marker, []).append(ev)
    return by_marker


def _marker_lookup(
    marker: str | None, tag: str, events_by_marker: dict[str, list[NormalizedEvent]],
) -> NormalizedEvent | None:
    """The one real NormalizedEvent this lossless block corresponds to, or
    None if the adapter never produced one for this exact record. Filters
    candidates sharing `marker` down to the kind this block's own tag
    implies (see `_expected_kinds`) -- this resolves the one common
    ambiguity (a THINKING block and a real ASSISTANT_TEXT block sharing one
    record's uuid) but NOT the rarer one: two content blocks of the SAME
    kind under the same marker (a message with two sequential plain "text"
    blocks) are indistinguishable here and this takes the first -- see
    `_drop_reason`'s own docstring for how that residual imprecision is
    labeled rather than silently assumed away.
    """
    if marker is None:
        return None
    candidates = events_by_marker.get(marker)
    if not candidates:
        return None
    kinds = _expected_kinds(tag)
    for ev in candidates:
        if not kinds or ev.kind in kinds:
            return ev
    return None


def _drop_reason(
    marker: str | None,
    tag: str,
    block_norm: str,
    config,
    *,
    fmt: str | None,
    is_leading_gap: bool,
    events_by_marker: dict[str, list[NormalizedEvent]],
    kept_markers: set[str],
    post_selection_removed_markers: set[str] | None = None,
) -> str:
    """WHY select.py's walk didn't keep this lossless block. `block_norm`
    must already be header-stripped (the caller passes lossless_norm, not
    lossless_raw). `marker`/`tag` come from `_parse_header` on the SAME
    block's raw form -- the header itself, not the body, carries the
    adapter-level flags (isMeta etc) and the block-type suffix.

    Checked in order of DECREASING certainty; every branch below names a
    concrete cause -- there is no "reason: unclear" branch left (operator
    direction, 2026-09-11: "we should always know why we excluded
    something ... should never happen").

    1. (claude-code only) a header flag adapters/claude_code.py's parse()
       itself checks BEFORE any cleaning or scoring: isMeta/
       isVisibleInTranscriptOnly (drops a "user" record unconditionally),
       interruptedMessageId (Claude Code's own synthetic Ctrl-C marker), or
       isSidechain (dropped whenever the file also has a primary thread).
       lossless.py's dump_claude_code tags all four in the block header
       precisely so this check is exact, not inferred.
    2. (claude-code only) adapters.claude_code.is_harness_tag_only -- the
       REAL production check parse() itself uses (`if not cleaned:
       continue`) for a record that isn't excluded by #1 but whose entire
       body is harness-tag framing (<task-notification>/<command-name>/etc).
    3. (claude-code only) a THINKING-tagged block when config.include_thinking
       is False -- adapters only ever emit a THINKING event when that flag
       is set; lossless.py keeps thinking blocks unconditionally ("lossless
       means lossless"), so this is certain, not inferred.
    4. config.hide_api_errors and classifier.is_api_error(block_norm) --
       matches adapters/claude_code.py's own [API ERROR: ...] tag.
    5. Marker-resolved: `_marker_lookup` finds the real NormalizedEvent this
       exact record produced.
         - if its marker is among the run's actually-kept markers, the
           apparent "drop" is a TEXT-FORMATTING MISMATCH, not a selection
           decision -- lossless.py's raw block and extract's rendered/
           cleaned text differ enough that SequenceMatcher didn't align
           them, even though the underlying record survived selection (see
           the matching white block elsewhere in this diff for the same
           marker).
         - otherwise the event was genuinely walked and rejected: this
           function re-checks is_api_error/is_checkpoint/has_finding_signal/
           length against the REAL event's OWN text and REAL
           checkpoint_score (already including score_events()'s "followed
           by a pause" lookahead bonus -- not a text-only shape guess), so
           the verdict is exact, not probabilistic. A checkpoint or
           finding-signal event found here (not the leading gap, so
           genuinely walked) contradicts select()'s own deterministic
           "always keep once walked" rule UNLESS a different content block
           sharing this same marker (see `_marker_lookup`'s docstring) was
           the one actually kept -- named explicitly as that specific,
           narrow case rather than papered over.
    6. No matching event at all: the adapter dropped or never emitted this
       exact record for a reason not covered above (this module can name
       WHERE the loss happened -- at/before parse-time, in the adapter, not
       in select()'s windowing -- without pinning the exact clause). A
       shape-based re-derivation is still shown alongside, explicitly
       labeled as an approximate cross-check, not the primary reason.

    `is_leading_gap` (this block sits before the OLDEST event the walk ever
    reached) short-circuits all of the above: the content was never
    evaluated for any content-level reason at all -- the walk simply
    stopped before getting here.
    """
    ev = _marker_lookup(marker, tag, events_by_marker)
    if ev is not None and ev.meta.get("excluded_epoch") == "true":
        return (
            f"reason: source epoch {ev.meta.get('epoch')} is outside the selected --epochs span; "
            "the record was excluded before content filtering"
        )
    if ev is not None and marker in (post_selection_removed_markers or set()):
        return (
            "reason: this event survived the configured selection walk but was removed by "
            "the post-selection --strip-stale-wakeups transform, which keeps the earliest "
            "event in a repeated stale-wakeup tail and removes later repeats"
        )
    if is_leading_gap:
        return (
            "reason: never reached by the walk -- a --max-checkpoints/--max-words/"
            "--max-compactions/--max-time-minutes stop (or the true start of the selected epoch) "
            "was hit before the walk "
            "got this far back; see any '[older session content...]' note above"
        )

    role = tag.split()[0] if tag else ""
    if fmt == "claude-code" and role == "USER":
        flag_hits = [
            name for name in ("isMeta", "isVisibleInTranscriptOnly", "interruptedMessageId", "isSidechain")
            if name in tag
        ]
        if flag_hits:
            return (
                f"reason: adapters/claude_code.py's parse() drops this record unconditionally, before "
                f"any cleaning or scoring, because it carries {', '.join(flag_hits)}=true -- harness-"
                "injected framing or a synthetic marker, never operator intent. lossless.py tags this "
                "flag in the block header precisely so this is certain, not inferred"
            )
    if fmt == "claude-code":
        from .adapters.claude_code import is_harness_tag_only

        if is_harness_tag_only(block_norm):
            return (
                "reason: pure harness-tag framing (<task-notification>/<command-name>/"
                "<local-command-caveat>/etc.) -- adapters/claude_code.py's parse() strips these "
                "entirely and never emits an event when nothing real is left, regardless of "
                "length/checkpoint scoring. lossless.py deliberately does NOT apply this same "
                "stripping (its own module docstring: 'no task-notification-aware logic at all'), "
                "which is why this shows up here but nowhere in the real adapter's output"
            )
    if fmt == "claude-code" and (tag.endswith(" thinking") or tag == "thinking") and not config.include_thinking:
        return (
            "reason: THINKING content -- adapters/claude_code.py only ever emits a THINKING event "
            "when --include-thinking is passed; lossless.py keeps thinking blocks unconditionally "
            "('lossless means lossless'), which is why this shows up here but never in the real "
            "adapter's output without that flag"
        )
    if config.hide_api_errors and classifier.is_api_error(block_norm):
        return (
            "reason: matched adapters/claude_code.py's own [API ERROR: ...] tag -- suppressed by "
            "default (config.hide_api_errors / --show-api-errors to include)"
        )

    if ev is not None:
        if marker in kept_markers:
            return (
                f"reason: NOT actually a selection drop -- a real event with this exact marker "
                f"({ev.kind.value}) survived selection and is represented in the extract output; "
                "this block only shows grey here because lossless.py's raw rendering and extract's "
                "cleaned/rendered text of the SAME record differ: a formatting mismatch, not a "
                "content decision, or a matching paragraph replaced by --redact-pattern, prevented "
                "the text-level diff from aligning "
                "them -- look for the matching marker in a white block elsewhere in this diff"
            )
        if ev.kind is EventKind.ASSISTANT_TEXT and config.hide_api_errors and classifier.is_api_error(ev.text):
            return (
                "reason: the real event for this exact record matched adapters/claude_code.py's own "
                "[API ERROR: ...] tag -- suppressed by default (config.hide_api_errors / "
                "--show-api-errors to include)"
            )
        if _is_checkpoint_event(ev, config):
            return (
                f"reason: the real event for this exact record scored {ev.checkpoint_score:.1f} as a "
                f"checkpoint (>= threshold {config.checkpoint_score_threshold}, INCLUDING score_events()'s "
                "real lookahead bonus, not a shape guess) -- select() keeps every walked checkpoint "
                "unconditionally, and this isn't the leading (never-reached) gap, so the only honest "
                "explanation is that a DIFFERENT content block sharing this same marker (a message with "
                "more than one block) was the one actually kept, not this exact block"
            )
        if classifier.has_finding_signal(ev.text):
            return (
                "reason: the real event for this exact record reports a concrete finding "
                "(classifier.has_finding_signal on its OWN text) -- select() keeps this unconditionally "
                "once walked, and this isn't the leading gap, so the only honest explanation is that a "
                "DIFFERENT content block sharing this same marker was the one actually kept, not this "
                "exact block"
            )
        return (
            f"reason: the real event for this exact record is {len(ev.text)} chars (its own text, not "
            f"lossless.py's rendering) -- at/below --answer-length ({config.long_comment_chars}) and no "
            f"finding signal, and scored {ev.checkpoint_score or 0.0:.1f} < the checkpoint threshold "
            f"{config.checkpoint_score_threshold} (already including score_events()'s lookahead bonus) -- "
            "select() correctly dropped it for length"
        )

    score = classifier.shape_score(block_norm)
    return (
        f"reason: no scored event exists for this exact record (marker={marker!r}) in the real parse -- "
        "the adapter dropped or never emitted it before scoring ever ran, for a reason not covered by the "
        "checks above (see adapters/claude_code.py's parse() for its full exclusion list -- e.g. an "
        "AskUserQuestion tool_result reformatted under a QA_PAIR event instead, or a fallback line-based "
        "marker that doesn't align with the adapter's own indexing). Shape re-derivation as a secondary, "
        f"APPROXIMATE cross-check only: scores {score:.1f} by shape (header/closure/meta-compact/"
        f"direct-address) against threshold {config.checkpoint_score_threshold}, "
        f"finding_signal={classifier.has_finding_signal(block_norm)}, {len(block_norm)} chars"
    )


def _checkpoint_tag(marker: str | None, tag: str, config, events_by_marker: dict[str, list[NormalizedEvent]]) -> str | None:
    """Operator direction, 2026-09-11: "detected checkpoints should be
    labelled in the debug ... showing that they were detected as such."
    Returns a short blue annotation when the real event this block
    corresponds to scored as a checkpoint -- for a white (kept) block this
    is why it survived; for a grey (dropped) block it's additional context
    alongside its own yellow reason line (typically "never reached by the
    walk" -- a real checkpoint that just sits too far back for the current
    --max-checkpoints/--max-words budget)."""
    ev = _marker_lookup(marker, tag, events_by_marker)
    if ev is not None and _is_checkpoint_event(ev, config):
        return f"[checkpoint detected: score {ev.checkpoint_score:.1f} >= threshold {config.checkpoint_score_threshold}]"
    return None


def render_debug(
    lossless_text: str, extract_text: str, use_color: bool, config=None, fmt: str | None = None,
    all_events: list[NormalizedEvent] | None = None, block_render=None,
) -> str:
    """Pure function: both text inputs are already-rendered strings (the
    caller -- cmd_extract_debug -- owns calling `lossless.dump_*`/
    `extract()` with matching path/fmt/session args, "identical how we
    would call extract"). `config` (the ExtractConfig the extract() call
    actually used) is optional -- None (the default) skips per-block yellow
    reason lines and blue checkpoint tags entirely, preserving this module's
    pre-2026-09-11 output byte-for-byte; cmd_extract_debug always passes the
    real one. `fmt` (the resolved adapter name) enables the format-specific,
    exact-not-approximate reason checks (claude-code's isMeta/
    is_harness_tag_only/THINKING-without-include_thinking) -- harmless to
    omit for another format, or when config is None. `all_events`
    (ExtractResult.all_events -- the FULL, pre-selection, post-
    classifier.score_events() event list) enables the marker-resolved,
    certain reason/checkpoint-tag lookups; without it, this module falls
    straight to the "no scored event exists" approximate branch for every
    grey block, same as before this feature existed.

    Re-runs extract's epoch filter and selection over the already-parsed
    events, then mirrors its post-selection stale-wakeup trimming. Redaction
    changes event text but does not remove event markers; extract() keeps the
    full parse unmodified so these explanations use source text, not redacted
    output text.
    """
    import difflib

    lossless_raw = [_render_lossless_block(b, block_render) for b in _lossless_blocks(lossless_text)]
    extract_raw = _extract_blocks(extract_text)
    lossless_norm = [_normalize_lossless(b) for b in lossless_raw]
    timestamp_values: set[str] = set()
    if config is not None and config.show_timestamps != "none" and all_events:
        for ev in all_events:
            if not ev.timestamp:
                continue
            try:
                value = datetime.fromisoformat(ev.timestamp.replace("Z", "+00:00"))
            except ValueError:
                continue
            if value.tzinfo is None:
                value = value.replace(tzinfo=timezone.utc)
            timestamp_values.add(value.astimezone(timezone.utc).strftime(config.timestamp_format))
    extract_norm = [_normalize_extract(b, timestamp_values) for b in extract_raw]

    events_by_marker: dict[str, list[NormalizedEvent]] = {}
    kept_markers: set[str] = set()
    post_selection_removed_markers: set[str] = set()
    if config is not None and all_events is not None:
        events_by_marker = _index_events(all_events)
        # _select_epochs annotates excluded records for exact reason labels.
        # Copy metadata so a diagnostic render never mutates the caller's
        # full parse, even when render_debug is used independently of extract.
        from dataclasses import replace
        from . import _select_epochs

        diagnostic_events = [replace(ev, meta=dict(ev.meta)) for ev in all_events]
        for ev in diagnostic_events:
            ev.meta.pop("excluded_epoch", None)
            ev.meta.pop("epoch_count", None)
        epoch_events = _select_epochs(diagnostic_events, config.epochs)
        selected_events = select.select(epoch_events, config)
        after_transforms = selected_events
        if config.strip_stale_wakeups:
            after_transforms, _ = mangle.strip_stale_wakeup_tail(selected_events)
        kept_markers = {ev.marker for ev in after_transforms}
        post_selection_removed_markers = (
            {ev.marker for ev in selected_events} - kept_markers
        )

    def paint(code: str, text: str) -> str:
        return f"{code}{text}{_RESET}" if use_color and code else text

    out: list[str] = []
    # The leading `None` is isjunk (no junk filtering). A mutation-testing
    # None->[] swap on this arg is a PROVEN-equivalent mutant, not an
    # untested gap: difflib only ever calls `self.isjunk(...)` behind its
    # own `if isjunk:` guard, and `[]` is exactly as falsy as `None` there --
    # so the two are behaviorally identical through every real code path,
    # not merely untested by this package's own suite (verified against
    # cpython's difflib source, not assumed).
    sm = difflib.SequenceMatcher(None, lossless_norm, extract_norm, autojunk=False)
    for opcode_index, (tag, i1, i2, j1, j2) in enumerate(sm.get_opcodes()):
        if tag == "equal":
            for k in range(i1, i2):
                out.append(paint("", lossless_raw[k]))
                if config is not None and all_events is not None:
                    header = _parse_header(lossless_raw[k])
                    if header is not None:
                        marker, block_tag = header
                        note = _checkpoint_tag(marker, block_tag, config, events_by_marker)
                        if note is not None:
                            out.append(paint(_BLUE, note))
            continue

        if tag in ("delete", "replace"):
            dropped = lossless_raw[i1:i2]
            dropped_norm = lossless_norm[i1:i2]  # header-stripped, for _drop_reason's own analysis
            n = len(dropped)
            unit = "block" if n == 1 else "blocks"
            # Only the very FIRST opcode in the WHOLE diff, if it starts at
            # i1==0, can be "never reached by the walk" -- select() walks
            # newest-to-oldest and never skips a middle span uninspected, so
            # any OTHER delete/replace run is necessarily a real per-content
            # rejection. See _drop_reason's own docstring. The leading-gap
            # reason is IDENTICAL for every block in the run by definition
            # (none of it was walked), so it gets ONE line for the whole
            # run rather than one per block, unlike genuine per-content
            # rejections below, which can legitimately differ block to
            # block (one an API error, the next just too short).
            is_leading_gap = opcode_index == 0 and i1 == 0
            out.append(paint(_CYAN, f">>> [gap: {n} lossless {unit} dropped]"))
            if config is not None and is_leading_gap:
                out.append(paint(_YELLOW, _drop_reason(
                    None, "", "", config, fmt=fmt, is_leading_gap=True,
                    events_by_marker=events_by_marker, kept_markers=kept_markers,
                    post_selection_removed_markers=post_selection_removed_markers,
                )))
            grey_lines: list[str] = []
            for b, b_norm in zip(dropped, dropped_norm):
                grey_lines.append(paint(_GREY, b))
                header = _parse_header(b)
                marker, block_tag = header if header is not None else (None, "")
                # The leading-gap reason is IDENTICAL for every block in this
                # run (none of it was walked) and already printed once above
                # -- repeating it per block here would reintroduce the exact
                # 239-line repetition this feature's first version fixed.
                # The blue checkpoint tag is NOT identical per block (most
                # never-reached blocks aren't checkpoints at all), so it's
                # still worth attaching individually even inside this run.
                if config is not None and not is_leading_gap:
                    grey_lines.append(paint(_YELLOW, _drop_reason(
                        marker, block_tag, b_norm, config, fmt=fmt, is_leading_gap=False,
                        events_by_marker=events_by_marker, kept_markers=kept_markers,
                        post_selection_removed_markers=post_selection_removed_markers,
                    )))
                if config is not None and all_events is not None:
                    note = _checkpoint_tag(marker, block_tag, config, events_by_marker)
                    if note is not None:
                        grey_lines.append(paint(_BLUE, note))
            grey_body = "\n\n".join(grey_lines)
            out.append(f"---\n{grey_body}\n---")
            out.append(paint(_CYAN, "<<<"))

        if tag in ("insert", "replace"):
            for k in range(j1, j2):
                block = extract_raw[k]
                color = _note_color(block)
                out.append(paint(color, block) if color else block)

    return "\n\n".join(out) + "\n"
