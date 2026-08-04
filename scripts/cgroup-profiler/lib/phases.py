"""Mark IO and mark→phase conversion — collector-tier, standard library only.

Marks are the cheap, universal way to label a run: `cgprofile mark job-a` can
be `docker exec`'d into any container sharing the run directory, including
one this profiler never resolved as a target. That is why `marks.jsonl`
follows the same append rule as every other stream in DESIGN.md §3: a single
`write()` of one line under `O_APPEND` so concurrent writers from unrelated
processes interleave cleanly instead of corrupting each other's lines, and a
reader that tolerates a truncated final line — the writer on the other end of
that race is not this process and cannot be made to finish first.

Changepoint detection (finding phase-like structure nobody marked) is
`analyze.py`'s job, over `ruptures`; nothing here does statistics.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, List, Optional

from .model import Mark, Phase

_MARKS_FILE = "marks.jsonl"


def emit_mark(
    run_path: str,
    name: str,
    kind: str = "phase",
    meta: Optional[Dict[str, Any]] = None,
    when: Optional[float] = None,
    source: str = "mark",
) -> Mark:
    """Append one mark to `<run_path>/marks.jsonl` and return it.

    `source` distinguishes a boundary the workload asked for ("mark", the
    default, and what `cgprofile mark` writes) from one the wrapper inferred
    around a command it was given ("wrapper"). The report treats the two
    differently — an explicit label outranks an inferred one — so the
    provenance has to survive the round trip through the file.

    `mono` is deliberately left unset: a mark written from a foreign
    container has no notion of this run's monotonic start, only wall clock.
    `phases_from_marks` falls back to `t` for exactly that reason; a caller
    that *does* know the run's mono offset (the profiler's own process) can
    pass it via `meta` or resolve it later against the manifest.

    The single `os.write()` call is the whole correctness argument: POSIX
    guarantees a single `write(2)` to an `O_APPEND` fd on a local filesystem
    is atomic with respect to other writers, so two containers marking at
    the same instant still each get one intact line, never an interleaved
    one. Splitting this into an `open()` + multiple writes would lose that.
    """
    mark = Mark(
        t=time.time() if when is None else when,
        name=name,
        kind=kind,
        mono=None,
        meta=dict(meta) if meta else {},
        source=source,
    )
    line = (json.dumps(mark.to_dict(), sort_keys=True) + "\n").encode("utf-8")
    path = os.path.join(run_path, _MARKS_FILE)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    try:
        os.write(fd, line)
    finally:
        os.close(fd)
    return mark


def load_marks(run_path: str) -> List[Mark]:
    """Every complete mark in `<run_path>/marks.jsonl`.

    A missing file reads back as no marks, matching every other reader in
    this package ("absent is not zero"). A line that fails to parse — most
    often the tail of a write this process raced with and lost, but also
    just as safely any other corrupt line — is skipped rather than raising;
    losing one mark to a race is a far smaller problem than a profiler that
    dies reading its own annotation stream.
    """
    path = os.path.join(run_path, _MARKS_FILE)
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except OSError:
        return []
    marks: List[Mark] = []
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        try:
            marks.append(Mark.from_dict(data))
        except (KeyError, TypeError, ValueError):
            continue
    return marks


def _position(mark: Mark) -> float:
    """A mark's place on the run's mono timeline.

    Prefers `mono` when it is known; falls back to wall-clock `t` for a mark
    that only ever had that (see `emit_mark`). Mixing the two units within
    one run is a caller error — the caller resolving marks against a
    manifest should backfill `mono` on every mark before this is called —
    but falling back rather than raising keeps a partially-resolved list
    orderable instead of fatal.
    """
    return mark.mono if mark.mono is not None else mark.t


def phases_from_marks(marks: List[Mark], t_start: float, t_end: float) -> List[Phase]:
    """Turn `kind="phase"` marks into a contiguous, gap-free list of `Phase`.

    A phase mark closes whatever phase was open and opens a new one named
    after it; `kind="event"` marks are point annotations and are ignored
    here entirely — they never move a boundary. Marks are sorted by position
    before folding, so writers racing each other (or a clock a few
    milliseconds behind another container's) cannot produce an out-of-order
    phase list; only the *order* of `phase` marks matters, not the order
    they happened to land in the file.

    With no phase marks at all, the whole run is one unlabelled phase — a
    report must always have at least one phase to draw a per-phase table
    against.
    """
    boundaries = sorted(
        (m for m in marks if m.kind == "phase"),
        key=_position,
    )
    if not boundaries:
        return [Phase(name="run", t0=t_start, t1=t_end, source="mark")]

    phases: List[Phase] = []
    current = boundaries[0]
    current_t0 = t_start
    for nxt in boundaries[1:]:
        nxt_pos = _position(nxt)
        phases.append(
            Phase(name=current.name, t0=current_t0, t1=nxt_pos, source=current.source, meta=current.meta)
        )
        current = nxt
        current_t0 = nxt_pos
    phases.append(
        Phase(name=current.name, t0=current_t0, t1=t_end, source=current.source, meta=current.meta)
    )
    return phases
