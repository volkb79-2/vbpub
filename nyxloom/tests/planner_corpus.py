"""Load and replay the CR-06 planner differential corpus.

The fixture is a pruned event LOG (see `tools/extract_planner_corpus.py` for
what is real, what is pruned, and the check that pruning is plan-faithful).
This module turns it back into the projections the planner plans against.

Kept separate from `corpus_profiles.py` on purpose: this half is the REAL
history, that half is the DECLARED environment, and the differential is their
cross product. Confusing the two is how a corpus starts looking bigger than
the ground truth behind it.
"""

from __future__ import annotations

import json
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Iterator

from nyxloom.projection import apply_event
from nyxloom.types import Actor, ActorKind, Event, EventType, TaskStateFile

FIXTURE = Path(__file__).with_name("fixtures") / "planner_corpus_v1.json"

#: One actor for the whole corpus. The projector records it; the planner never
#: reads it, and the operator identities in the source logs are not the
#: differential's subject.
CORPUS_ACTOR = Actor(kind=ActorKind.TICK, id="corpus")


def rebuild_event(project: str, row: dict) -> Event:
    return Event(
        schema_version=1,
        sequence=row["sequence"],
        timestamp=datetime.fromisoformat(row["timestamp"]),
        project=project,
        actor=CORPUS_ACTOR,
        type=EventType(row["type"]),
        payload=row["payload"],
        task_id=row.get("task_id"),
        attempt_id=row.get("attempt_id"),
        wave_id=row.get("wave_id"),
        decision_id=row.get("decision_id"),
    )


@lru_cache(maxsize=1)
def _fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def manifest() -> list[dict]:
    return _fixture()["manifest"]


def _deep_copy(states: dict[str, TaskStateFile]) -> dict[str, TaskStateFile]:
    """A projection independent of the replay that produced it.

    `apply_event` mutates TaskStateFile objects IN PLACE, so `dict(states)`
    copies the mapping and shares every value -- every yielded "projection"
    would silently become the final one, and a corpus of 877 identical
    end-states would pass a differential while proving nothing. Round-tripping
    through the serde is the copy the projector cannot reach through.
    """
    return {tid: TaskStateFile.from_dict(tsf.to_dict()) for tid, tsf in states.items()}


def replay(project: str, rows: list[dict]) -> Iterator[tuple[int, dict[str, TaskStateFile]]]:
    """Yield `(sequence, projection)` after each event that moved it."""
    states: dict[str, TaskStateFile] = {}
    for row in rows:
        affected = apply_event(states, rebuild_event(project, row))
        if affected and states:
            yield row["sequence"], _deep_copy(states)


@lru_cache(maxsize=1)
def projections() -> tuple[tuple[str, dict[str, TaskStateFile]], ...]:
    """Every DISTINCT projection the corpus replays through, newest last.

    Distinct by content: consecutive events that leave the projection
    byte-identical (an audit-only event, a no-op re-assertion) contribute one
    case, not several.
    """
    out: list[tuple[str, dict[str, TaskStateFile]]] = []
    seen: set[str] = set()
    for project, rows in sorted(_fixture()["logs"].items()):
        for sequence, states in replay(project, rows):
            key = json.dumps(
                {t: v.to_dict() for t, v in sorted(states.items())},
                sort_keys=True, default=str)
            if key in seen:
                continue
            seen.add(key)
            out.append((f"{project}-{sequence}", states))
    return tuple(out)
