from __future__ import annotations


def ratio(z_eq: int | None, z_pool: int | None) -> float | None:
    if z_eq is None or z_pool in (None, 0):
        return None
    return z_eq / z_pool


def swap_disk_bytes(memory_swap_current: int | None, zswapped: int | None, swapcached: int | None) -> int | None:
    if memory_swap_current is None or zswapped is None or swapcached is None:
        return None
    return max(0, memory_swap_current - zswapped - swapcached)


def split_refault_rates(workingset_refault_anon_delta: int | None, zswpin_delta: int | None, interval_s: float) -> tuple[float | None, float | None]:
    if workingset_refault_anon_delta is None or zswpin_delta is None or interval_s <= 0:
        return None, None
    return zswpin_delta / interval_s, max(0, workingset_refault_anon_delta - zswpin_delta) / interval_s
