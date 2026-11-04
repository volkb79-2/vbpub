"""Disk layout policy: sizing rules and refusals, as pure functions.

Layout contract (README "Disk layout policy"):

    ESP | /boot | root (ext4) | [unallocated: cow area + reserve] | swap (END)

* Swap is always the LAST thing on the disk. Whatever sits before it (root,
  later a Btrfs cow partition, spare space) can then be grown or added to
  without moving or deleting swap. The r1002/v1001 layout (root 40G, swap in
  the middle, cow after it) made growth of root and cow impossible.
* Root is sized generously. A root below `min_root_gib(disk)` is refused.
* Space between root and swap stays unallocated; the operator tooling
  (`scripts/netcup/runner/provision-runner.sh`) carves the cow partition from
  it, sized by `recommended_layout`, and leaves the rest as reserve.

Everything here is arithmetic on integers (GiB or 512-byte sectors); nothing
touches a device.
"""

from __future__ import annotations

from dataclasses import dataclass

GIB = 1024 ** 3
SECTOR = 512
SECTORS_PER_GIB = GIB // SECTOR

# Root floor: 20% of the disk, at least 8 GiB, never demanding more than 100 GiB.
MIN_ROOT_FLOOR_GIB = 8
MIN_ROOT_CEIL_GIB = 100
MIN_ROOT_FRACTION_PERCENT = 20

# dev-host recommendation: root = 5/16 of the disk (31.25%), cow = 1/3.
ROOT_RECOMMENDED_NUM = 5
ROOT_RECOMMENDED_DEN = 16
ROOT_RECOMMENDED_MIN_GIB = 24
ROOT_RECOMMENDED_MAX_GIB = 200
COW_FRACTION_DEN = 3
ROUND_GIB = 8

# A swap partition may end at most this far before the disk end: the planner
# reserves a 1 MiB end buffer after rounding to 1 MiB alignment.
SWAP_END_SLACK_SECTORS = 4096

DISK_LAYOUTS = ("full-root", "dev-host")


class LayoutPolicyError(ValueError):
    """A layout violates the policy (swap not last, root too small)."""


def _round_down(value: int, step: int) -> int:
    return (value // step) * step


def min_root_gib(disk_gib: float) -> int:
    """Smallest root the installer accepts on a disk of `disk_gib` GiB."""
    wanted = -(-int(disk_gib * MIN_ROOT_FRACTION_PERCENT) // 100)
    return max(MIN_ROOT_FLOOR_GIB, min(MIN_ROOT_CEIL_GIB, wanted))


def recommended_root_gib(disk_gib: float) -> int:
    """Root size of the `dev-host` layout: 5/16 of the disk, 8 GiB steps, 24..200."""
    raw = int(disk_gib * ROOT_RECOMMENDED_NUM // ROOT_RECOMMENDED_DEN)
    return max(ROOT_RECOMMENDED_MIN_GIB, min(ROOT_RECOMMENDED_MAX_GIB, _round_down(raw, ROUND_GIB)))


@dataclass(frozen=True)
class LayoutRecommendation:
    disk_gib: float
    root_gib: int
    cow_gib: int
    swap_gib: int
    boot_gib: float
    reserve_gib: float


def recommended_layout(disk_gib: float, swap_gib: int, boot_gib: float = 1.25) -> LayoutRecommendation:
    """Rule-based sizes for a disposable dev/test host.

    root: 5/16 of the disk (160 GiB on 512): docker data (images, layers, build
    cache), runner checkouts and logs fit with at least 3x headroom over the
    measured 27 GB docker footprint. cow: 1/3 of the disk (168 GiB on 512,
    8 GiB steps). The remainder (~166 GiB on 512) is unallocated reserve that
    stays directly behind root (cow sits at the end of the gap, before swap):
    root can be extended into it; cow is re-created or extended by a device.
    Raises LayoutPolicyError when the pieces do not fit.
    """
    root = recommended_root_gib(disk_gib)
    cow = _round_down(int(disk_gib // COW_FRACTION_DEN), ROUND_GIB)
    reserve = disk_gib - boot_gib - root - cow - swap_gib
    if root < min_root_gib(disk_gib):
        raise LayoutPolicyError(f"recommended root {root} GiB is below the minimum {min_root_gib(disk_gib)} GiB")
    if reserve < 0:
        raise LayoutPolicyError(
            f"disk of {disk_gib:g} GiB cannot hold root {root} + cow {cow} + swap {swap_gib} GiB"
        )
    return LayoutRecommendation(disk_gib, root, cow, swap_gib, boot_gib, reserve)


def validate_root_size(root_sectors: int, disk_sectors: int) -> None:
    """Refuse a planned root below the minimum for this disk."""
    root_gib = root_sectors / SECTORS_PER_GIB
    minimum = min_root_gib(disk_sectors / SECTORS_PER_GIB)
    if root_gib + 1e-9 < minimum:
        raise LayoutPolicyError(
            f"planned root is {root_gib:.2f} GiB, below the minimum of {minimum} GiB for a "
            f"{disk_sectors / SECTORS_PER_GIB:.0f} GiB disk; docker data, checkouts and growth "
            "do not fit (raise root_max_size_gb or use disk_layout=dev-host)"
        )


def validate_swap_last(
    swap_extents: list[tuple[int, int]],
    other_extents: list[tuple[int, int]],
    disk_sectors: int,
) -> None:
    """Refuse swap that is not the last allocation on the disk.

    `swap_extents` and `other_extents` are (start_sector, size_sectors). Every
    non-swap partition must end at or before the first swap starts, and the
    last swap must end within SWAP_END_SLACK_SECTORS of the disk end.
    """
    if not swap_extents:
        return
    first_swap_start = min(start for start, _size in swap_extents)
    last_swap_end = max(start + size for start, size in swap_extents)
    for start, size in other_extents:
        if start + size > first_swap_start:
            raise LayoutPolicyError(
                "swap must be the last allocation on the disk: a non-swap partition "
                f"(sectors {start}..{start + size}) extends past the first swap start {first_swap_start}"
            )
    if disk_sectors - last_swap_end > SWAP_END_SLACK_SECTORS:
        raise LayoutPolicyError(
            f"swap must sit at the END of the disk: {(disk_sectors - last_swap_end) / SECTORS_PER_GIB:.2f} GiB "
            "of the disk follows the last swap partition, which blocks growth of the areas before it"
        )
