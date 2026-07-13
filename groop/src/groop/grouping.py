from __future__ import annotations

import sys
from dataclasses import dataclass, field

from groop.model import EntityFrame, Frame


# Sort keys for phase ordering.
# Valid numeric phases sort by their value (group 0).
# Unparseable phase (phase_raw is set, phase is None) goes after valid phases (group 1).
# Absent phase (both None) sorts last (group 2).
_PHASE_ORDER_VALID = 0
_PHASE_ORDER_UNPARSEABLE = 1
_PHASE_ORDER_ABSENT = 2


def _phase_sort_key(phase: int | None, phase_raw: str | None) -> tuple[int, int]:
    """Return a sort key for a ciu phase.

    Valid numeric phases sort first in ascending order (phase_2 before
    phase_10).  Unparseable phases (phase_raw set, phase=None) sort next.
    Absent phases (both None) sort last.  This ensures an unknown phase
    never silently sorts as ``0``.
    """
    if phase is not None:
        return (_PHASE_ORDER_VALID, phase)
    if phase_raw is not None:
        return (_PHASE_ORDER_UNPARSEABLE, 0)
    return (_PHASE_ORDER_ABSENT, 0)


def _group_sort_key(group: CiuGroup) -> tuple[str, tuple[int, int]]:
    """Return a sort key for a ``CiuGroup``: by stack name, then phase order."""
    return (group.stack or "", _phase_sort_key(group.phase, group.phase_raw))


@dataclass
class CiuGroup:
    """One CIU stack/phase group.

    Every entity in this group shares the same stack and phase.  The
    ``source`` field reflects the **best** (most reliable) source among
    the group's entities — if any entity is label-confirmed the group
    is labelled ``"label"``; otherwise it is ``"inferred"``.
    """

    stack: str | None
    """Stack directory name (e.g. ``infra/redis-core``)."""

    phase: int | None
    """Numeric phase number.  ``None`` when unparseable or absent."""

    phase_raw: str | None
    """Raw phase label value (e.g. ``phase_2``).  ``None`` when absent."""

    source: str
    """Detection tier for this group: ``"label"`` or ``"inferred"``."""

    entity_frames: list[EntityFrame] = field(default_factory=list)
    """Entities belonging to this group."""


@dataclass
class GroupedEntities:
    """Result of grouping a frame's entities by CIU stack and phase.

    ``groups`` are ordered by (stack, phase) per the numeric phase rule.
    ``ungrouped`` are entities whose ``ciu`` is ``None`` — they pass
    through with no synthetic bucket.
    """

    groups: list[CiuGroup] = field(default_factory=list)
    ungrouped: list[EntityFrame] = field(default_factory=list)


def group_entities(frame: Frame) -> GroupedEntities:
    """Group the frame's container entities by CIU stack and phase.

    Pure function — no side effects, no Textual imports.  Entities with
    ``ciu is None`` are returned in ``ungrouped``; entities with CIU
    metadata are placed into ``CiuGroup`` buckets keyed by ``(stack,
    phase)``.

    Within a stack, groups are ordered by numeric phase (phase_2 before
    phase_10).  Unparseable phases sort after valid phases; absent
    phases sort last.
    """
    by_key: dict[tuple[str | None, int | None, str | None], CiuGroup] = {}
    ungrouped: list[EntityFrame] = []

    for entity_frame in frame.entities.values():
        ciu = entity_frame.entity.ciu
        if ciu is None:
            ungrouped.append(entity_frame)
            continue

        key = (ciu.stack, ciu.phase, ciu.phase_raw)
        if key not in by_key:
            by_key[key] = CiuGroup(
                stack=ciu.stack,
                phase=ciu.phase,
                phase_raw=ciu.phase_raw,
                source=ciu.source,
            )
        group = by_key[key]
        group.entity_frames.append(entity_frame)
        # Promote source to the most reliable tier in the group.
        if ciu.source == "label":
            group.source = "label"

    groups = sorted(by_key.values(), key=_group_sort_key)
    return GroupedEntities(groups=groups, ungrouped=ungrouped)
