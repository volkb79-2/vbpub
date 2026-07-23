"""Capability-band assignment via per-axis operator thresholds.

Bands are 1-based integers: 1 = simplest capability, higher = more capable.
Operator thresholds are ASCENDING cutoffs per capability axis.
Cutoffs [c1, c2] define 3 bands:
  band 1 if score < c1
  band 2 if c1 <= score < c2
  band 3 if score >= c2
N cutoffs produce N+1 bands (the function generalizes to any number of cutoffs).
Axes are capability dimensions like "intelligence", "coding", "agentic".
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


def assign_band(score: float | None, cutoffs: list[float]) -> int:
    """Assign a capability band for a single axis score.

    Bands are 1-based (1 = lowest capability). Band 0 means "unknown/unrated"
    and is distinct from band 1 (lowest rated band).

    Args:
        score: Capability score for one axis, or None if unrated.
        cutoffs: Ascending cutoffs defining band boundaries. Unsorted input is
            sorted defensively. N cutoffs produce N+1 bands (1 .. N+1).
            A score exactly equal to a cutoff falls into the HIGHER band
            (the >= boundary is inclusive on the upper side).

    Returns:
        Band number: 0 = unknown/unrated (score is None), 1..N+1 for rated scores.
        Empty cutoffs -> band 1 (everything rated falls in the single band).
        Score above all cutoffs -> top band (len(cutoffs) + 1).
        Score exactly on a cutoff -> higher band (inclusive upper bound).
        Negative/zero scores handled normally by comparison.
    """
    if score is None:
        return 0

    sorted_cutoffs = sorted(cutoffs)

    if not sorted_cutoffs:
        return 1

    for i, cutoff in enumerate(sorted_cutoffs):
        if score < cutoff:
            return i + 1

    return len(sorted_cutoffs) + 1


@dataclass(frozen=True)
class AxisThresholds:
    """Per-axis operator-tunable cutoffs for capability-band assignment.

    Attributes:
        cutoffs: Mapping from axis name (e.g., "intelligence", "coding", "agentic")
            to a list of ascending float cutoffs. N cutoffs define N+1 bands.
            Empty list means a single band (band 1) for all rated scores.
            Unknown axis (not in mapping) -> band 0 ("unknown/unrated").
    """

    cutoffs: dict[str, list[float]]

    def band_for(self, axis: str, score: float | None) -> int:
        """Return the band for a single axis score using this axis's cutoffs.

        Args:
            axis: Capability axis name (e.g., "intelligence", "coding", "agentic").
            score: Capability score for the axis, or None if unrated.

        Returns:
            Band number: 0 if axis unknown or score is None; otherwise 1..N+1
            where N = len(cutoffs[axis]).
        """
        axis_cutoffs = self.cutoffs.get(axis)
        if axis_cutoffs is None:
            return 0
        return assign_band(score, axis_cutoffs)


DEFAULT_CUTOFFS: dict[str, list[float]] = {
    "intelligence": [0.33, 0.66],
    "coding": [0.33, 0.66],
    "agentic": [0.33, 0.66],
}
"""Operator-tunable starting defaults for capability axes.

These are PLACEHOLDER values (0.33, 0.66 creating 3 equal bands on [0,1] scale).
Operators MUST tune these to their benchmark scales and routing policies.
They are NOT authoritative benchmarks.
"""


def assign_bands(
    scores: dict[str, float | None],
    thresholds: AxisThresholds,
) -> dict[str, int]:
    """Map each axis score to its capability band using the given thresholds.

    Args:
        scores: Mapping from axis name to score (or None if unrated).
        thresholds: AxisThresholds holding per-axis cutoffs.

    Returns:
        Mapping from axis name to band number (0 = unknown axis or unrated score,
        1..N+1 for rated scores on known axes).
        Axes present in scores but absent from thresholds -> band 0.
    """
    result: dict[str, int] = {}
    for axis, score in scores.items():
        result[axis] = thresholds.band_for(axis, score)
    return result