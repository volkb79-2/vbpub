"""Mechanical (no LLM roundtrip) session-log extraction: turn a coding-agent
CLI's session transcript into a compact, resumable brief -- real operator
turns, structured Q&A, and content-detected checkpoints, in delimited text
or JSON. Used from `nyxloom extract`; see cli.py's cmd_extract.

Public entry point: extract(path, config, format=None, session_id=None).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from . import classifier, render, select
from .adapters import DetectionError, detect, get_adapter
from .config import ExtractConfig
from .events import NormalizedEvent

__all__ = ["ExtractConfig", "ExtractResult", "extract", "DetectionError"]


@dataclass
class ExtractResult:
    events: list[NormalizedEvent]  # the selected, windowed events, in chronological order
    format: str  # the adapter name that was used
    session_id: str
    last_marker: str | None  # marker of the last event in the FULL parse, for --since chaining

    def render(self) -> str:
        if self._output_format == "json":
            return render.render_json(self.events, self._checkpoint_threshold, self.last_marker)
        return render.render_text(self.events, self._checkpoint_threshold)

    # set by extract() below; not part of the public dataclass contract
    _output_format: str = "text"
    _checkpoint_threshold: float = 3.0


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
