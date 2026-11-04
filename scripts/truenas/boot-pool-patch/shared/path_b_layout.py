"""Fail-closed layout planning for the reserved-tail Path B data pool.

The caller supplies facts read from the TrueNAS guest. This module makes no
device changes and imports no middleware modules, so its layout decisions can
be tested before a disk operation is attempted.
"""

from __future__ import annotations

from dataclasses import dataclass
import posixpath
import re


DATA_POOL_NAME = "boot-disk-data"
ZFS_GPT_TYPE = "BF01"
EXPECTED_PARTITIONS = (1, 2, 3)
NEW_PARTITION_NUMBER = 4
DEFAULT_MINIMUM_FREE_BYTES = 1024**3


class LayoutError(ValueError):
    """The observed disk layout is not the supported safe Path B layout."""


@dataclass(frozen=True)
class GPTPartition:
    number: int
    start: int
    end: int
    type_code: str
    name: str


@dataclass(frozen=True)
class GPTLayout:
    logical_sector_bytes: int
    last_usable_sector: int
    partitions: tuple[GPTPartition, ...]


@dataclass(frozen=True)
class PathBPlan:
    disk: str
    boot_pool_partition: str
    new_partition: str
    start_sector: int
    end_sector: int
    sector_bytes: int
    preexisting_partitions: tuple[GPTPartition, ...]

    @property
    def partition_bytes(self) -> int:
        return (self.end_sector - self.start_sector + 1) * self.sector_bytes


_SECTOR_SIZE_RE = re.compile(
    r"^Sector size \(logical(?:/physical)?\):\s*(\d+)(?:/\d+)? bytes\s*$",
    re.M,
)
_LAST_USABLE_RE = re.compile(r"^First usable sector is\s+\d+, last usable sector is\s+(\d+)\s*$", re.M)
_PARTITION_RE = re.compile(
    r"^[ \t]*(\d+)[ \t]+(\d+)[ \t]+(\d+)[ \t]+.*?[ \t]{2,}"
    r"([0-9A-Fa-f]{4})(?:[ \t]{2,}(.*?))?[ \t]*$",
    re.M,
)


def parse_sgdisk_print(output: str) -> GPTLayout:
    """Parse the stable table fields from ``sgdisk --print`` output."""
    sector_match = _SECTOR_SIZE_RE.search(output)
    usable_match = _LAST_USABLE_RE.search(output)
    if sector_match is None or usable_match is None:
        raise LayoutError("sgdisk --print output lacks logical sector or usable-LBA facts")
    sector_bytes = int(sector_match.group(1))
    last_usable = int(usable_match.group(1))
    if sector_bytes not in (512, 4096):
        raise LayoutError(f"unsupported logical sector size: {sector_bytes}")

    partitions = tuple(
        GPTPartition(
            number=int(match.group(1)),
            start=int(match.group(2)),
            end=int(match.group(3)),
            type_code=match.group(4).upper(),
            name=(match.group(5) or "").strip(),
        )
        for match in _PARTITION_RE.finditer(output)
    )
    if not partitions:
        raise LayoutError("sgdisk --print output contains no GPT partition rows")
    numbers = tuple(part.number for part in partitions)
    if numbers != tuple(sorted(set(numbers))):
        raise LayoutError(f"partition numbers are duplicated or unordered: {numbers!r}")
    for part in partitions:
        if part.start < 1 or part.end < part.start or part.end > last_usable:
            raise LayoutError(f"invalid GPT extent for partition {part.number}")
    by_extent = sorted(partitions, key=lambda part: part.start)
    for previous, current in zip(by_extent, by_extent[1:]):
        if current.start <= previous.end:
            raise LayoutError(
                f"GPT partitions {previous.number} and {current.number} overlap"
            )
    return GPTLayout(sector_bytes, last_usable, partitions)


def parse_sgdisk_sector(output: str, *, label: str) -> int:
    """Parse an exact one-line sector number from ``sgdisk -F/-E``."""
    value = output.strip()
    if not re.fullmatch(r"\d+", value):
        raise LayoutError(f"sgdisk {label} returned an invalid sector: {value!r}")
    return int(value)


def validate_disk_path(path: str) -> str:
    """Accept only an absolute Linux block-device path, normalized lexically."""
    if not isinstance(path, str) or not path.startswith("/dev/") or "\x00" in path:
        raise LayoutError(f"disk path must be an absolute /dev path: {path!r}")
    if any(component in {".", ".."} for component in path.split("/")):
        raise LayoutError(f"invalid disk path: {path!r}")
    normalized = posixpath.normpath(path)
    if not normalized.startswith("/dev/"):
        raise LayoutError(f"invalid disk path: {path!r}")
    return normalized


def make_path_b_plan(
    *,
    disk: str,
    boot_pool_partition: str,
    layout: GPTLayout,
    aligned_free_start: int,
    largest_free_end: int,
    boot_pool_leaf_paths: tuple[str, ...],
    minimum_free_bytes: int = DEFAULT_MINIMUM_FREE_BYTES,
    new_partition_number: int = NEW_PARTITION_NUMBER,
) -> PathBPlan:
    """Require the exact single-disk TrueNAS GPT plus an unambiguous free tail."""
    disk = validate_disk_path(disk)
    boot_pool_partition = validate_disk_path(boot_pool_partition)
    expected_boot_partition = f"{disk}p3" if disk[-1:].isdigit() else f"{disk}3"
    if boot_pool_partition != expected_boot_partition:
        raise LayoutError(
            f"expected boot-pool partition 3 on {disk}, got {boot_pool_partition}"
        )
    if tuple(part.number for part in layout.partitions) != EXPECTED_PARTITIONS:
        raise LayoutError(
            "Path B creation requires exactly GPT partitions 1, 2, and 3; "
            f"found {[part.number for part in layout.partitions]}"
        )
    boot_partition = layout.partitions[-1]
    if boot_partition.type_code != ZFS_GPT_TYPE:
        raise LayoutError(
            f"partition 3 has GPT type {boot_partition.type_code}, expected {ZFS_GPT_TYPE} (ZFS)"
        )
    leaf_paths = tuple(validate_disk_path(path) for path in boot_pool_leaf_paths)
    if leaf_paths != (boot_pool_partition,):
        raise LayoutError(
            "boot-pool must be a single leaf on the selected disk's partition 3; "
            f"found {leaf_paths!r}"
        )
    if new_partition_number != NEW_PARTITION_NUMBER:
        raise LayoutError("Path B currently supports only the unused partition 4 slot")
    if not isinstance(minimum_free_bytes, int) or isinstance(minimum_free_bytes, bool) or minimum_free_bytes <= 0:
        raise LayoutError("minimum_free_bytes must be a positive integer")

    last_existing_end = max(part.end for part in layout.partitions)
    if aligned_free_start <= last_existing_end:
        raise LayoutError(
            "the largest free GPT region is not a tail after partition 3; refusing to use a gap"
        )
    if largest_free_end > layout.last_usable_sector:
        raise LayoutError("sgdisk reported a free extent beyond the last usable GPT sector")
    if largest_free_end < aligned_free_start:
        raise LayoutError("the free GPT tail is empty or reversed")
    free_bytes = (largest_free_end - aligned_free_start + 1) * layout.logical_sector_bytes
    if free_bytes < minimum_free_bytes:
        raise LayoutError(
            f"free GPT tail is {free_bytes} bytes, below the required {minimum_free_bytes} bytes"
        )

    new_path = f"{disk}p{NEW_PARTITION_NUMBER}" if disk[-1:].isdigit() else f"{disk}{NEW_PARTITION_NUMBER}"
    return PathBPlan(
        disk=disk,
        boot_pool_partition=boot_pool_partition,
        new_partition=new_path,
        start_sector=aligned_free_start,
        end_sector=largest_free_end,
        sector_bytes=layout.logical_sector_bytes,
        preexisting_partitions=layout.partitions,
    )


def verify_existing_path_b(
    *,
    disk: str,
    boot_pool_partition: str,
    layout: GPTLayout,
    boot_pool_leaf_paths: tuple[str, ...],
    data_pool_leaf_paths: tuple[str, ...],
    pool_status: str,
) -> str:
    """Return the existing data partition when a completed Path B is verified."""
    disk = validate_disk_path(disk)
    boot_pool_partition = validate_disk_path(boot_pool_partition)
    expected_boot = f"{disk}p3" if disk[-1:].isdigit() else f"{disk}3"
    expected_data = f"{disk}p4" if disk[-1:].isdigit() else f"{disk}4"
    if boot_pool_partition != expected_boot:
        raise LayoutError(f"boot-pool is not on the selected disk's partition 3: {boot_pool_partition}")
    if tuple(part.number for part in layout.partitions) != (1, 2, 3, 4):
        raise LayoutError("an existing partition 4 is not the complete Path B GPT layout")
    if layout.partitions[2].type_code != ZFS_GPT_TYPE:
        raise LayoutError("partition 3 is not a ZFS partition")
    data = layout.partitions[3]
    if data.type_code != ZFS_GPT_TYPE or data.name != DATA_POOL_NAME:
        raise LayoutError(
            f"partition 4 must be named {DATA_POOL_NAME!r} and have ZFS type {ZFS_GPT_TYPE}"
        )
    if data.start <= layout.partitions[2].end:
        raise LayoutError("partition 4 overlaps partition 3 or is not placed after it")
    if tuple(validate_disk_path(path) for path in boot_pool_leaf_paths) != (expected_boot,):
        raise LayoutError("boot-pool is not a single vdev on the selected partition 3")
    if tuple(validate_disk_path(path) for path in data_pool_leaf_paths) != (expected_data,):
        raise LayoutError(f"{DATA_POOL_NAME} is not a single vdev on the selected partition 4")
    if pool_status != "ONLINE":
        raise LayoutError(f"{DATA_POOL_NAME} is registered but status is {pool_status!r}")
    return expected_data
