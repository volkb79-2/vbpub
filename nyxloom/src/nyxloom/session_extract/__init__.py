"""Mechanical (no LLM roundtrip) session-log extraction: turn a coding-agent
CLI's session transcript into a compact, resumable brief -- real operator
turns, structured Q&A, and content-detected checkpoints, in delimited text
or JSON. Used from `nyxloom extract`; see cli.py's cmd_extract.

Public entry points: extract(path, config, fmt=None, session_id=None), and
read_since_marker(path) for delta-extraction (see its own docstring).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path

from . import classifier, mangle, render, select
from .adapters import DetectionError, detect, get_adapter
from .config import ExtractConfig
from .events import NormalizedEvent
from .ledger import Ledger

__all__ = ["ExtractConfig", "ExtractResult", "extract", "DetectionError", "read_since_marker"]


@dataclass
class ExtractResult:
    events: list[NormalizedEvent]  # the selected, windowed events, in chronological order
    format: str  # the adapter name that was used
    session_id: str
    last_marker: str | None  # marker of the last event in the FULL parse, for --since chaining
    # mangle.py's own report -- how many trailing stale-wakeup checkpoints
    # --strip-stale-wakeups collapsed away, and how many paragraphs
    # --redact-pattern replaced. Zero when the corresponding config knob
    # wasn't set (mangle.py never ran). cli.py surfaces these as a one-line
    # stderr note, not part of the rendered brief itself.
    stale_wakeups_stripped: int = 0
    redacted_paragraphs: int = 0
    # The FULL, pre-selection, post-classifier-scoring event list -- every
    # NormalizedEvent the adapter emitted for this session, not just the
    # ones select() kept. `events` above is a WINDOWED SUBSET of this (or
    # equal to it, if nothing was walked past). extract() always populates
    # this; Optional only so the dataclass field can default. Exposed for
    # extract-debug's own reason-labeling (debug_diff.py): given an exact
    # marker, "does a real, scored event exist for this record, and is it
    # in `events`" is a certain lookup, not a text-based approximation.
    all_events: list[NormalizedEvent] | None = None

    def render(self) -> str:
        if self._output_format == "json":
            return render.render_json(
                self.events, self._checkpoint_threshold, self.format, self.last_marker,
                source_metadata=self._source_metadata,
            )
        return render.render_text(
            self.events, self.format, self.last_marker, self._min_gap_to_annotate, self._gap_note_show_marker,
            ledger=self._ledger,
            insert_blank_lines=self._insert_blank_lines,
            gap_marker_mode=self._gap_marker_mode,
            block_render=self._block_render,
            show_timestamps=self._show_timestamps,
            timestamp_format=self._timestamp_format,
            metadata_position=self._metadata_position,
            source_metadata=self._source_metadata,
        )

    # set by extract() below; not part of the public dataclass contract
    _output_format: str = "text"
    _checkpoint_threshold: float = 3.0
    _min_gap_to_annotate: int = 3
    _gap_note_show_marker: bool = False
    _insert_blank_lines: int = 1
    _gap_marker_mode: str = "full"
    _show_timestamps: str = "pre"
    _timestamp_format: str = "[%H:%M:%S]"
    _metadata_position: str = "both"
    _source_metadata: dict[str, str] | None = None
    # E-012 (ledger.py) -- opt-in, built and attached by cli.py's cmd_extract
    # when --ledger is passed; None means "not requested," skipped entirely.
    _ledger: dict[str, Ledger] | None = None
    # Per-block prose render hook (render.py's own block_render param) --
    # attached by cli.py for --render-markdown/--highlight. A rendering
    # concern, not a selection one, so it lives here rather than in
    # ExtractConfig, same as _output_format above.
    _block_render: Callable[[str], str] | None = None


def read_since_marker(path: Path) -> tuple[str, str]:
    """Read back the (format, marker) a prior extract() call embedded in
    its own output file -- the delta-extraction UX: point --since-file at a
    previous run's saved output instead of hunting for or hand-copying a
    raw marker string. Works on either output format (JSON's top-level
    `format`/`last_marker` fields, or text's positioned HTML-comment cursor).
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

    # The LAST match, not the first: a kept event's own text can quote an
    # old cursor comment verbatim (realistic given the README's own "paste a prior
    # run's output back as context" snapshot-chain pattern) -- taking the
    # first match would silently resolve to that stale, embedded marker
    # instead of this run's own cursor. Prefer a trailing `post` comment when
    # present, otherwise use a leading `pre` comment or the last legacy footer.
    matches = list(render.MARKER_FOOTER_RE.finditer(text))
    if matches:
        positioned = []
        for match in matches:
            attrs = match.group("attrs") or ""
            placement = next((value for value in ("pre", "post", "both")
                              if f"placement={value}" in attrs), None)
            if placement is not None:
                positioned.append((match, placement))
        if positioned:
            trailing_post = [
                match for match, placement in positioned
                if placement == "post" and not text[match.end():].strip()
            ]
            pre = [match for match, placement in positioned if placement == "pre"]
            if trailing_post:
                m = trailing_post[-1]
            elif pre and text[:pre[0].start()].strip() == "":
                m = pre[0]
            else:
                m = positioned[-1][0]
        else:
            # Legacy outputs had no explicit placement attribute. Their real
            # marker was trailing, so prefer the final match over a quoted one.
            m = matches[-1]
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
                f"{path} holds {len(sessions)} sessions; pass session_id "
                f"(e.g. {sessions[0]!r}) -- cli.py's --opencode-session flag selects it"
            )
    elif session_id not in sessions:
        raise DetectionError(f"session {session_id!r} not found at {path}")

    events = adapter.parse(path, session_id, config)
    _assign_default_epochs(events)
    classifier.score_events(events)
    last_marker = events[-1].marker if events else None
    selected_events = _select_epochs(events, config.epochs)
    # Selection returns references into `events`. The post-selection redactor
    # edits event.text in place, so detach the selected view first: all_events
    # is the promised full, pre-selection parse and must remain suitable for
    # extract-debug's independent explanation of what selection kept/dropped.
    kept = [replace(ev, meta=dict(ev.meta)) for ev in select.select(selected_events, config)]

    stale_wakeups_stripped = 0
    if config.strip_stale_wakeups:
        kept, stale_wakeups_stripped = mangle.strip_stale_wakeup_tail(kept)

    redacted_paragraphs = 0
    if config.redact_patterns:
        kept, redacted_paragraphs = mangle.redact_paragraphs(kept, list(config.redact_patterns))

    result = ExtractResult(
        events=kept, format=adapter.name, session_id=session_id, last_marker=last_marker,
        stale_wakeups_stripped=stale_wakeups_stripped, redacted_paragraphs=redacted_paragraphs,
        all_events=events,
    )
    result._output_format = config.output_format
    result._checkpoint_threshold = config.checkpoint_score_threshold
    result._min_gap_to_annotate = config.min_gap_to_annotate
    result._gap_note_show_marker = config.gap_note_show_marker
    result._insert_blank_lines = config.insert_blank_lines
    result._gap_marker_mode = config.gap_marker_mode
    result._show_timestamps = config.show_timestamps
    result._timestamp_format = config.timestamp_format
    result._metadata_position = config.extract_metadata
    return result


def _assign_default_epochs(events: list[NormalizedEvent]) -> None:
    """Fill in epoch metadata for adapters whose source has no clear marker.

    The Claude adapter assigns source-stable epoch numbers while parsing,
    before --since/--until slicing. Other adapters currently expose one
    session epoch. Checkpoint scores are independent from this metadata.
    """
    for ev in events:
        ev.meta.setdefault("epoch", "1")


def _select_epochs(events: list[NormalizedEvent], selection: str | None) -> list[NormalizedEvent]:
    available = sorted({int(ev.meta.get("epoch", "1")) for ev in events})
    if not available:
        return []
    for ev in events:
        ev.meta["epoch_count"] = str(max(available))

    if selection is None:
        chosen = {available[-1]}
    elif selection == "all":
        chosen = set(available)
    elif ":" in selection:
        try:
            start_s, end_s = selection.split(":", 1)
            start, end = int(start_s), int(end_s)
        except ValueError as exc:
            raise ValueError("--epochs range must be A:B with positive 1-based integers") from exc
        if start < 1 or end < start:
            raise ValueError("--epochs range must satisfy 1 <= A <= B")
        chosen = set(range(start, end + 1))
    else:
        try:
            epoch = int(selection)
        except ValueError as exc:
            raise ValueError("--epochs must be N, A:B, or all") from exc
        if epoch < 1:
            raise ValueError("--epochs uses positive 1-based epoch numbers")
        chosen = {epoch}

    missing = sorted(chosen.difference(available))
    if missing:
        raise ValueError(
            f"requested epoch(s) {', '.join(map(str, missing))} are not present; "
            f"this source has epochs 1:{max(available)}"
        )
    for ev in events:
        if int(ev.meta.get("epoch", "1")) not in chosen:
            ev.meta["excluded_epoch"] = "true"
    return [ev for ev in events if int(ev.meta.get("epoch", "1")) in chosen]
