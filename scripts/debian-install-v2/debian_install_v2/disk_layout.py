"""Pure defaults and geometry helpers for the installer disk-layout planner."""

from __future__ import annotations

GIB = 1024 ** 3


def automatic_swap_size_gb(*, disk_bytes: int, memory_bytes: int) -> int:
    """Return min(ceil(2x guest RAM in GiB), round-half-up(20% of disk GiB)).

    The RAM-derived amount rounds upward to a whole GiB. The disk-derived
    amount rounds 20% of actual capacity to the nearest GiB, with half-GiB
    ties rounded up. This scales swap with memory while capping it on smaller
    disks.
    """
    if disk_bytes <= 0:
        raise ValueError("disk_bytes must be positive")
    if memory_bytes <= 0:
        raise ValueError("memory_bytes must be positive")
    disk_twenty_percent_rounded = (disk_bytes * 20 + 50 * GIB) // (100 * GIB)
    twice_memory_rounded_up = (memory_bytes * 2 + GIB - 1) // GIB
    return min(disk_twenty_percent_rounded, twice_memory_rounded_up)
