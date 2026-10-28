"""Plain domain values shared by the CLI and the TUI. No I/O here."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Partition:
    name: str
    size: int
    start: int | None = None
    kind: str | None = None


@dataclass(frozen=True)
class Disk:
    name: str
    size: int
    model: str = ""
    serial: str = ""
    kind: str = ""  # SSD / HDD / NVME as the API says
    pool: str | None = None
    # None means the API gave no partition data for this disk (unknown), () means it has none.
    partitions: tuple[Partition, ...] | None = None
    extra: dict[str, object] = field(default_factory=dict, compare=False)

    @property
    def free_bytes(self) -> int | None:
        """Upper bound of the unpartitioned space (alignment gaps and the backup GPT are not subtracted).

        None when partition data is unknown, so a caller never mistakes "unknown" for "full" or "empty".
        """
        if self.partitions is None:
            return None
        return max(self.size - sum(p.size for p in self.partitions), 0)

    def as_dict(self) -> dict[str, object]:
        return {"name": self.name, "size": self.size, "model": self.model, "serial": self.serial, "type": self.kind, "pool": self.pool,
                "partitions": None if self.partitions is None else [
                    {"name": p.name, "size": p.size, "start": p.start, "type": p.kind} for p in self.partitions],
                "free_bytes": self.free_bytes}


def human_bytes(n: int | None) -> str:
    if n is None:
        return "?"
    value = float(n)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB", "PiB"):
        if value < 1024 or unit == "PiB":
            return f"{int(value)} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    raise AssertionError("unreachable")  # pragma: no cover
