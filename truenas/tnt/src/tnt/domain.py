"""Plain domain values shared by the CLI and the TUI. No I/O here."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Partition:
    name: str
    size: int | None  # None: the API gave no usable size, so the disk's free space is unknown
    start: int | None = None
    kind: str | None = None


@dataclass(frozen=True)
class Disk:
    name: str
    size: int | None  # None: no usable size reported
    model: str = ""
    serial: str = ""
    kind: str = ""  # SSD / HDD / NVME as the API says
    pool: str | None = None
    # None means the API gave no partition data for this disk (unknown), () means it has none.
    partitions: tuple[Partition, ...] | None = None
    extra: dict[str, object] = field(default_factory=dict, compare=False)

    @property
    def free_upper_bound_bytes(self) -> int | None:
        """An UPPER BOUND of the unpartitioned space (alignment gaps and the backup GPT are not subtracted).

        None whenever it cannot be known: no partition data, no disk size, or any partition without a usable
        size. Unknown is never reported as 0 (full) or as the whole disk (empty).
        """
        if self.partitions is None or self.size is None or any(p.size is None for p in self.partitions):
            return None
        return max(self.size - sum(p.size for p in self.partitions), 0)

    def as_dict(self) -> dict[str, object]:
        return {"name": self.name, "size": self.size, "model": self.model, "serial": self.serial, "type": self.kind, "pool": self.pool,
                "partitions": None if self.partitions is None else [
                    {"name": p.name, "size": p.size, "start": p.start, "type": p.kind} for p in self.partitions],
                "free_upper_bound_bytes": self.free_upper_bound_bytes}


def human_free(n: int | None) -> str:
    """Free space is an upper bound; the marker travels with the number."""
    return "?" if n is None else f"<= {human_bytes(n)}"


def human_bytes(n: int | None) -> str:
    if n is None:
        return "?"
    value = float(n)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB", "PiB"):
        if value < 1024 or unit == "PiB":
            return f"{int(value)} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    raise AssertionError("unreachable")  # pragma: no cover
