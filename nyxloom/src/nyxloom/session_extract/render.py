"""Turn a selected event list into output: delimited text (paste-ready for
a fresh session) or JSON (for a second-stage script).
"""

from __future__ import annotations

import json

from .events import EventKind, NormalizedEvent

_LABELS = {
    EventKind.OPERATOR_TEXT: "OPERATOR",
    EventKind.QA_PAIR: "Q&A",
    EventKind.LIFECYCLE_MARKER: "LIFECYCLE",
    EventKind.THINKING: "THINKING",
}


def _label(ev: NormalizedEvent, checkpoint_threshold: float) -> str:
    if ev.kind is EventKind.ASSISTANT_TEXT:
        is_checkpoint = (ev.checkpoint_score or 0.0) >= checkpoint_threshold
        return "ASSISTANT (checkpoint)" if is_checkpoint else "ASSISTANT"
    return _LABELS.get(ev.kind, ev.kind.value.upper())


def render_text(events: list[NormalizedEvent], checkpoint_threshold: float) -> str:
    blocks = []
    for ev in events:
        blocks.append(f"## [{ev.timestamp}] {_label(ev, checkpoint_threshold)}\n\n{ev.text}")
    return "\n\n---\n\n".join(blocks) + "\n"


def render_json(events: list[NormalizedEvent], checkpoint_threshold: float, last_marker: str | None) -> str:
    payload = {
        "events": [
            {
                "timestamp": ev.timestamp,
                "kind": ev.kind.value,
                "checkpoint": (ev.checkpoint_score or 0.0) >= checkpoint_threshold
                if ev.kind is EventKind.ASSISTANT_TEXT
                else False,
                "marker": ev.marker,
                "text": ev.text,
            }
            for ev in events
        ],
        "last_marker": last_marker,
    }
    return json.dumps(payload, indent=2)
