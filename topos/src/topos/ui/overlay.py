"""Optional data the frame stream does not carry, supplied by the caller of the UI.

The local collector has no per-CPU split, no D-state counts and no process rows, so
the UI prints ``n/a`` for them. A caller that has real data (the sampler source,
:mod:`topos.sampler_adapter`) or a launcher that wants to judge the layout with
invented data passes an object implementing :class:`Overlay` to ``ToposApp``.

An overlay marked ``demo = True`` (the default) is drawn magenta with its ``label``
next to every value; ``demo = False`` means real data drawn like any other value.
Nothing in ``topos`` creates a demo overlay.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from topos.model import Frame

HOT_CPU_PCT = 3.0
HOT_IO_BPS = 1_048_576.0


@dataclass(frozen=True, slots=True)
class ProcRow:
    pid: int
    comm: str
    cpu: float | None
    """Percent of one CPU; ``None`` when the source has no value (shown ``n/a``)."""
    state: str
    io_bps: float | None = None
    """Read plus write bytes per second; ``None`` when unknown."""
    majflt: float | None = None
    blkio_ms: float | None = None
    """Block-IO delay in ms per second; ``None`` when ``task_delayacct`` is off."""
    rss: int | None = None
    cmdline: str = ""

    @property
    def hot(self) -> bool:
        return (
            (self.cpu is not None and self.cpu >= HOT_CPU_PCT)
            or self.state == "D"
            or (self.io_bps is not None and self.io_bps >= HOT_IO_BPS)
        )

    @property
    def cpu_rank(self) -> float:
        return -1.0 if self.cpu is None else self.cpu


class Overlay(Protocol):
    label: str
    """Short marker shown next to every overlay value, for example ``demo``."""

    def per_cpu(self, frame: Frame) -> list[tuple[float, float, float, float] | None] | None:
        """One ``(us, sy, io, st)`` tuple per CPU, each a fraction of that CPU.

        An entry may be ``None`` for a CPU without a value; the whole result may
        be ``None`` when there is no per-CPU source.
        """

    def processes(self, frame: Frame) -> dict[str, list[ProcRow]]:
        """Processes by owning cgroup key."""

    def counts(self, frame: Frame) -> tuple[int, int] | None:
        """Runnable and blocked (D-state) task counts."""

    # Optional, looked up with getattr by the UI:
    #   demo: bool                                  default True
    #   blocked(frame) -> dict[str, int] | None     D-state tasks per cgroup (subtree sums
    #                                               are the overlay's business); when absent
    #                                               they are counted from ``processes``
    #   why(frame) -> str | None                    a hard event worth the WHY line
    #   notes(frame) -> list[str]                   plain statements for the info line
