"""Normalized event model shared by every session-log adapter.

Every adapter (adapters/claude_code.py, adapters/codex.py, adapters/opencode.py)
parses its own on-disk format into a flat, chronologically-ordered list of
NormalizedEvent. Nothing downstream (classifier.py, select.py, render.py) knows
anything about JSONL, SQLite, or any particular CLI's schema — this is the one
seam adapters must honor.

Adapters drop heavy payloads immediately (tool_result bodies, thinking text
unless requested, raw tool_use inputs) rather than carrying them through —
these files can be tens of MB and the point of this tool is to never need an
LLM (or a human) to read them directly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class EventKind(str, Enum):
    """What a NormalizedEvent represents, post-classification-by-shape.

    Kinds an adapter emits directly (shape-based, no scoring involved):
      OPERATOR_TEXT     - real operator-authored (or controller-injected,
                          e.g. a /loop wakeup) prompt text. Never a tool
                          result, never harness-injected framing.
      QA_PAIR           - a structured question/answer exchange (Claude
                          Code's AskUserQuestion; adapters for other CLIs
                          may never emit this kind if the CLI has no
                          equivalent).
      LIFECYCLE_MARKER  - an explicit session-lifecycle event: a compaction
                          boundary, or an operator-issued /compact or
                          /clear. Selection treats these as hard stops.
      ASSISTANT_TEXT    - assistant prose. select.py's classifier decides
                          which of these count as "checkpoints"; this kind
                          itself makes no such claim.
      THINKING          - assistant reasoning content. Only emitted when
                          the adapter is asked to include it
                          (ExtractConfig.include_thinking); dropped by
                          adapters otherwise, at parse time, not later.

    Adapters never emit a kind for tool_use/tool_result content that isn't
    one of the above (AskUserQuestion aside) — that's the noise this whole
    tool exists to discard.
    """

    OPERATOR_TEXT = "operator_text"
    QA_PAIR = "qa_pair"
    LIFECYCLE_MARKER = "lifecycle_marker"
    ASSISTANT_TEXT = "assistant_text"
    THINKING = "thinking"


@dataclass
class NormalizedEvent:
    """One adapter-agnostic unit of session content, in source order.

    `seq` is the adapter's own within-session ordering key (a line number,
    ordinal, or DB row id — whatever the source format naturally provides);
    it need not be globally meaningful, only stable and monotonic within one
    parse of one session, since it is what --since compares against for
    delta extraction.

    `marker` is the adapter-specific opaque token this event would be
    identified by from the outside (a UUID, an ordinal, a DB message id) —
    this is what extract() reports as the "last event" marker for --since to
    consume on a later run, and adapters must be able to round-trip it via
    their own `since_index` lookup.

    `checkpoint_score` starts unset (None) and is filled in only for
    ASSISTANT_TEXT events by classifier.py during selection; adapters never
    set it.
    """

    seq: int
    marker: str
    timestamp: str  # ISO-8601 UTC string, as adapters already receive it
    kind: EventKind
    text: str
    checkpoint_score: float | None = field(default=None, compare=False)
    meta: dict[str, str] = field(default_factory=dict, compare=False)
