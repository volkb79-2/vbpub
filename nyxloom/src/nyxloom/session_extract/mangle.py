"""Post-selection, pre-render heuristic transforms -- opt-in, best-effort,
built for exactly one use case: `nyxloom extract $SESSION ... | claude`
handing the rendered brief straight to a FRESH agent as its entire prompt.
An LLM given that with no explicit new instruction resolves "what should I
do" by pattern-matching whatever's most salient in what it's handed --
either the recency-biased TAIL (the last thing discussed reads as "the
current situation" even when it's long since closed), or a strong ROLE-level
directive found anywhere in the body (a standing controller/autonomous-loop
framing reads as "you are the one who should keep doing this," regardless of
how far back it sits). Both transforms below target one of those two
failure modes; neither is validated against a labeled corpus (same
epistemic status as classifier.py's own WEIGHTS) -- each is off by default
and only ever removes/redacts from what already survived selection.

Both were verified against one real session, not guessed:
/home/vscode/.claude/projects/-workspaces-dstdns/ccfca53c-2cd9-4444-b8bb-24c595a48309.jsonl
(2026-09-11). Its rendered `extract` output ended in three near-duplicate
"stale wakeup, already resolved" assistant checkpoints -- a ScheduleWakeup/
Stop-hook fallback re-firing on its own schedule after the task that armed
it already considered itself done. The actual re-injected wakeup PROMPT
text was already absent from the render (adapters/claude_code.py drops
`isMeta: true` records, and Claude Code marks these fallback re-injections
that way) -- what survived was the assistant's own genuine reactive prose,
individually reasonable ("Confirmed consistent -- nothing stale or
missing...") but adding nothing new the second and third time it repeated
right at the very end of the walked span, exactly where a fresh reader's
attention lands. Separately, that same session's FIRST kept event (a
`/compact` boundary's own KEEP text -- select.py always keeps a
LIFECYCLE_MARKER's text unconditionally, in full) was a verbatim restatement
of the standing controller directive: "KEEP: standing /goal is active (set
verbatim by the user via /goal command...) -- resume/finish P102/P93/P175 to
merge, then continue autonomous per-component ... repair...". Handed to a
fresh or forked agent, framing like that is exactly the kind of role-level
statement that leads it to assume the controller's own identity instead of
whatever narrower task it was actually given.

That SPECIFIC case -- a `/compact` boundary's own KEEP text -- is now handled
at the source by default instead (2026-09-11, separate operator direction:
"the extract prose should never include the content of the compaction
itself"): adapters/claude_code.py hints an operator-issued /compact dispatch
as "[compaction: steered dispatched]" rather than keeping its argument text
verbatim (config.hide_compaction_content, default True; --show-compaction-
content opts back into the old verbatim behavior). `redact_paragraphs`
below remains for the general case this doesn't cover -- a `/goal`-style
directive restated in ordinary ASSISTANT_TEXT prose (not a lifecycle
marker), which is real model-authored content, not something a compaction
hint can summarize away.
"""

from __future__ import annotations

import re

from .events import EventKind, NormalizedEvent

# Real phrasing mined from the dstdns session above -- an assistant's own
# reaction to a mechanically re-injected wakeup/hook prompt (the prompt
# ITSELF is already excluded at the adapter level; this matches the
# response TO it). Deliberately narrow: only self-describing "this trigger
# was stale/a repeat, nothing new to do" language, never a bare "confirmed"
# or "stale" alone -- both words show up constantly in legitimate
# progress-monitoring prose ("Confirmed alive, continuing to wait") that
# must survive untouched since it reports a real poll result, not a no-op.
_STALE_WAKEUP_RE = re.compile(
    r"\bstale\b[^.\n]{0,60}\b(wakeup|fallback|notification|message|prompt)\b|"
    r"\b(wakeup|fallback|notification)\b[^.\n]{0,60}\bstale\b|"
    r"\b(same|repeat(?:ing|ed)?)\b[^.\n]{0,40}\b(stale|fallback)\b|"
    r"\balready (confirmed|resolved|completed|done|handled)\b[^.\n]{0,80}\bno (new|further)\b|"
    r"\bno (new|further) action (is )?needed\b|"
    r"\bnothing (more|further|new) to (do|add|report)\b",
    re.IGNORECASE,
)


def strip_stale_wakeup_tail(events: list[NormalizedEvent]) -> tuple[list[NormalizedEvent], int]:
    """Drop a trailing run of 2+ ASSISTANT_TEXT checkpoints that each match
    _STALE_WAKEUP_RE, keeping only the EARLIEST one in that run (a single
    "yes, confirmed, nothing to do" is still real signal -- it says the
    session really did end idle, not mid-task) and dropping the repeats
    after it. Only ever removes from the true END of `events` -- a match
    anywhere earlier (mid-session, while genuinely still monitoring
    something) is never touched, and neither is a lone trailing match (run
    length 1): this targets REPETITION specifically, not the phrasing alone.
    Consistent with config.py's append-only/cache-stable principle -- this
    only decides whether to render the newest few events, never edits
    anything an earlier --since-file run already emitted.
    """
    run_start = len(events)
    for i in range(len(events) - 1, -1, -1):
        ev = events[i]
        if ev.kind is not EventKind.ASSISTANT_TEXT or not _STALE_WAKEUP_RE.search(ev.text):
            break
        run_start = i
    run_len = len(events) - run_start
    if run_len < 2:
        return events, 0
    return events[: run_start + 1], run_len - 1


def redact_paragraphs(
    events: list[NormalizedEvent], patterns: list[str],
) -> tuple[list[NormalizedEvent], int]:
    """For every event's text, replace each blank-line-delimited paragraph
    matching ANY of `patterns` (case-insensitive) with a one-line
    placeholder naming which pattern matched. Never drops the whole event --
    only the matching paragraph(s) -- so unrelated state packed into the
    same block (a `/compact` boundary's per-package KEEP notes, alongside
    the one paragraph restating a standing goal) survives untouched.
    Mutates events in place (NormalizedEvent is a plain mutable dataclass)
    and returns the same list, for chaining after strip_stale_wakeup_tail.
    """
    compiled = [re.compile(p, re.IGNORECASE) for p in patterns]
    redacted = 0
    for ev in events:
        paragraphs = ev.text.split("\n\n")
        changed = False
        for i, para in enumerate(paragraphs):
            for pattern in compiled:
                if pattern.search(para):
                    paragraphs[i] = f"[redacted paragraph -- matched --redact-pattern {pattern.pattern!r}]"
                    redacted += 1
                    changed = True
                    break
        if changed:
            ev.text = "\n\n".join(paragraphs)
    return events, redacted
