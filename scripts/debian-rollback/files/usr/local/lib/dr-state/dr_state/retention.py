"""Count-based retention selection, without backend capacity decisions."""

from __future__ import annotations

from collections.abc import Iterable

from .ids import StateId


def select_prune(
    states: Iterable[StateId],
    keep: int,
    *,
    protected: Iterable[str] = (),
    pinned: Iterable[str] = (),
) -> tuple[StateId, ...]:
    """Select the oldest unprotected, unpinned excess states."""

    if not isinstance(keep, int) or isinstance(keep, bool) or keep < 0:
        raise ValueError("retention count must be a non-negative integer")
    protected_names = frozenset(protected)
    pinned_names = frozenset(pinned)
    eligible = sorted(
        (state for state in states if state.name not in pinned_names),
        key=lambda state: (state.sequence, state.name),
    )
    excess = max(0, len(eligible) - keep)
    return tuple(
        state
        for state in eligible[:excess]
        if state.name not in protected_names
    )
