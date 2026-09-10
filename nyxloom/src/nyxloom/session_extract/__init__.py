"""Mechanical (no LLM roundtrip) session-log extraction: turn a coding-agent
CLI's session transcript into a compact, resumable brief -- real operator
turns, structured Q&A, and content-detected checkpoints, in delimited text
or JSON. Used from `nyxloom extract`; see cli.py's cmd_extract.

Public entry points: extract(path, config, fmt=None, session_id=None), and
read_since_marker(path) for delta-extraction (see its own docstring).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from . import classifier, render, select
from .adapters import DetectionError, detect, get_adapter
from .config import ExtractConfig
from .events import NormalizedEvent

__all__ = ["ExtractConfig", "ExtractResult", "extract", "DetectionError", "read_since_marker"]


@dataclass
class ExtractResult:
    events: list[NormalizedEvent]  # the selected, windowed events, in chronological order
    format: str  # the adapter name that was used
    session_id: str
    last_marker: str | None  # marker of the last event in the FULL parse, for --since chaining

    def render(self) -> str:
        if self._output_format == "json":
            return render.render_json(self.events, self._checkpoint_threshold, self.format, self.last_marker)
        return render.render_text(self.events, self._checkpoint_threshold, self.format, self.last_marker)

    # set by extract() below; not part of the public dataclass contract
    _output_format: str = "text"
    _checkpoint_threshold: float = 3.0


def read_since_marker(path: Path) -> tuple[str, str]:
    """Read back the (format, marker) a prior extract() call embedded in
    its own output file -- the delta-extraction UX: point --since-file at a
    previous run's saved output instead of hunting for or hand-copying a
    raw marker string. Works on either output format (JSON's top-level
    `format`/`last_marker` fields, or text's trailing HTML-comment footer).
    Raises ValueError if the file has no embedded marker (e.g. the prior
    run's own event list was empty, or the file wasn't produced by this
    tool)."""
    text = Path(path).read_text(encoding="utf-8")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        payload = None
    if isinstance(payload, dict) and payload.get("last_marker"):
        return payload["format"], payload["last_marker"]

    m = render.MARKER_FOOTER_RE.search(text)
    if m:
        return m.group(1), m.group(2)

    raise ValueError(f"{path} has no embedded nyxloom-extract marker (empty prior run, or not our output)")


def extract(
    path: Path,
    config: ExtractConfig | None = None,
    fmt: str | None = None,
    session_id: str | None = None,
) -> ExtractResult:
    config = config or ExtractConfig()
    adapter = get_adapter(fmt) if fmt else detect(path)

    sessions = adapter.list_sessions(path)
    if session_id is None:
        if len(sessions) == 1:
            session_id = sessions[0]
        elif not sessions:
            raise DetectionError(f"{path}: no sessions found")
        else:
            raise DetectionError(
                f"{path} holds {len(sessions)} sessions; pass --session (e.g. {sessions[0]!r})"
            )
    elif session_id not in sessions:
        raise DetectionError(f"session {session_id!r} not found at {path}")

    events = adapter.parse(path, session_id, config)
    classifier.score_events(events)
    last_marker = events[-1].marker if events else None
    kept = select.select(events, config)

    result = ExtractResult(events=kept, format=adapter.name, session_id=session_id, last_marker=last_marker)
    result._output_format = config.output_format
    result._checkpoint_threshold = config.checkpoint_score_threshold
    return result
