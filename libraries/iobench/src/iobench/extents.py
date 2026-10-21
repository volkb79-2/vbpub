"""Half-open byte-extent sets: the written-extent map and confinement checks."""
from __future__ import annotations

from typing import Iterable, Sequence

Extent = tuple[int, int]


class Extents:
    """A set of disjoint half-open ``[start, end)`` byte ranges."""

    def __init__(self, ranges: Iterable[Sequence[int]] = ()) -> None:
        self._r: list[Extent] = []
        for start, end in ranges:
            self.add(int(start), int(end))

    def add(self, start: int, end: int) -> None:
        if end <= start:
            return
        kept: list[Extent] = []
        for s, e in self._r:
            if e < start or s > end:
                kept.append((s, e))
            else:
                start, end = min(start, s), max(end, e)
        kept.append((start, end))
        self._r = sorted(kept)

    def remove(self, start: int, end: int) -> None:
        out: list[Extent] = []
        for s, e in self._r:
            if e <= start or s >= end:
                out.append((s, e))
                continue
            if s < start:
                out.append((s, start))
            if e > end:
                out.append((end, e))
        self._r = out

    def covers(self, start: int, end: int) -> bool:
        """True when ``[start, end)`` lies entirely inside one stored range."""
        return any(s <= start and end <= e for s, e in self._r)

    def overlap(self, start: int, end: int) -> int:
        return sum(max(0, min(e, end) - max(s, start)) for s, e in self._r)

    def total(self) -> int:
        return sum(e - s for s, e in self._r)

    def as_list(self) -> list[list[int]]:
        return [[s, e] for s, e in self._r]

    def copy(self) -> "Extents":
        return Extents(self._r)

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Extents) and self._r == other._r

    def __repr__(self) -> str:
        return f"Extents({self._r!r})"
