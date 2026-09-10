"""Turn a selected event list into output: delimited text (paste-ready for
a fresh session) or JSON (for a second-stage script).

Both formats embed the true end-of-session marker (from the FULL parse,
not just what survived selection) so a later run can be pointed at THIS
output file as --since-file and pick up exactly where it left off --
finding the delta border by reading the marker back out, not by re-typing
or diffing text. See __init__.py's read_since_marker().
"""

from __future__ import annotations

import json
import re

from .events import EventKind, NormalizedEvent

MARKER_FOOTER_RE = re.compile(r"^<!-- nyxloom-extract: format=(\S+) marker=(\S+) -->$", re.MULTILINE)

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


def render_text(events: list[NormalizedEvent], checkpoint_threshold: float, fmt: str, last_marker: str | None) -> str:
    blocks = []
    for ev in events:
        blocks.append(f"## [{ev.timestamp}] {_label(ev, checkpoint_threshold)}\n\n{ev.text}")
    body = "\n\n---\n\n".join(blocks) + "\n"
    if last_marker is None:
        return body
    return body + f"\n<!-- nyxloom-extract: format={fmt} marker={last_marker} -->\n"


def render_json(events: list[NormalizedEvent], checkpoint_threshold: float, fmt: str, last_marker: str | None) -> str:
    payload = {
        "format": fmt,
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
