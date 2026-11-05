"""Optional data the frame stream does not carry, supplied by the caller of the UI.

The real app has no such source today (no per-CPU split, no D-state counts, no
process rows), so it prints ``n/a`` for them. A caller that has real data, or a
launcher that wants to judge the layout with invented data, passes an object
implementing :class:`Overlay` to ``ToposApp``. Anything an overlay provides is
drawn with its ``label`` visible; nothing in ``topos`` ever creates an overlay.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from topos.model import Frame


@dataclass(frozen=True, slots=True)
class ProcRow:
    pid: int
    comm: str
    cpu: float
    state: str

    @property
    def hot(self) -> bool:
        return self.cpu >= 3.0 or self.state == "D"


class Overlay(Protocol):
    label: str
    """Short marker shown next to every overlay value, for example ``demo``."""

    def per_cpu(self, frame: Frame) -> list[tuple[float, float, float, float]] | None:
        """One ``(us, sy, io, st)`` tuple per CPU, each a fraction of that CPU."""

    def processes(self, frame: Frame) -> dict[str, list[ProcRow]]:
        """Processes by owning cgroup key."""

    def counts(self, frame: Frame) -> tuple[int, int] | None:
        """Runnable and blocked (D-state) task counts."""
