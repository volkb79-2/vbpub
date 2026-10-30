"""Rendering of query results for the CLI (the TUI renders the same Disk values in a DataTable)."""
from __future__ import annotations

from .domain import Disk, human_bytes, human_free

HEADERS = ("NAME", "SIZE", "TYPE", "MODEL", "SERIAL", "POOL", "PARTS", "FREE (UPPER BOUND)")


def disk_row(d: Disk) -> tuple[str, ...]:
    parts = "?" if d.partitions is None else str(len(d.partitions))
    return (d.name, human_bytes(d.size), d.kind or "-", d.model or "-", d.serial or "-", d.pool or "-", parts, human_free(d.free_upper_bound_bytes))


def disk_table(disks: list[Disk]) -> str:
    rows = [HEADERS] + [disk_row(d) for d in disks]
    widths = [max(len(r[i]) for r in rows) for i in range(len(HEADERS))]
    return "\n".join("  ".join(c.ljust(w) for c, w in zip(r, widths)).rstrip() for r in rows)
