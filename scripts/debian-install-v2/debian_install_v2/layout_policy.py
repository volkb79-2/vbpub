"""Disk layout policy: the explicit `partitioning` plan, as pure functions.

Layout contract (README "Disk layout", docs/DESIGN-data-area-v2.md):

    [pre-root partitions] | root | DATA PARTITION(S) or one LVM PV | [reserve] | raw iobench | swap (END)

* Swap is always the LAST thing on the disk. Whatever sits before it (root,
  the data area, a reserve) can then be grown or added to without moving swap.
* Partition numbers after root ascend with DISK POSITION: root, then the data
  partitions in config order, then the swaps. (Pre-root partitions keep the
  numbers they have; cloud images number root 1 with 14/15 before it.)
* The layout depends ONLY on the config (`partitioning` plus `swap_*`). There
  is no rule-based sizing and no hidden reserve: the plan accounts for the whole
  disk (only the 1 MiB end buffer remains), or the installer refuses it.

Everything here is arithmetic on integers (GiB or 512-byte sectors); nothing
touches a device.
"""

from __future__ import annotations

from dataclasses import dataclass
import re

GIB = 1024 ** 3
SECTOR = 512
SECTORS_PER_GIB = GIB // SECTOR
ALIGN = 2048  # 1 MiB in sectors
MIB_SECTORS = 2048

# A swap partition may end at most this far before the disk end: the planner
# reserves a 1 MiB end buffer after rounding to 1 MiB alignment.
SWAP_END_SLACK_SECTORS = 4096
GPT_LAST_USABLE_OFFSET = 34


class LayoutPolicyError(ValueError):
    """A layout violates the policy (swap not last, plan does not fit, bad schema)."""


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


def gpt_safe_end(disk_sectors: int) -> int:
    """Aligned exclusive end for planned partitions, before backup-GPT metadata."""
    if disk_sectors <= GPT_LAST_USABLE_OFFSET:
        raise LayoutPolicyError(f"disk has no usable GPT sectors ({disk_sectors} total sectors)")
    return (disk_sectors - GPT_LAST_USABLE_OFFSET) // ALIGN * ALIGN


def pool_end(disk_sectors: int, swap_extents: list[tuple[int, int]] | tuple[tuple[int, int], ...] = ()) -> int:
    """One pool boundary for the data planner and swap planner.

    A managed swap plan ends at the first swap start. With swap off (or no
    tail swap) the root/data pool ends at the aligned last usable GPT sector.
    """
    return min((start for start, _size in swap_extents), default=gpt_safe_end(disk_sectors))


# --------------------------------------------------------------------------
# Schema
#
#   "partitioning": {
#     "root": "*" | "keep" | 160 | "40%",     default "*"
#     "reserve_gb": 0 | 25 | "*",              default 0
#     "data": {"kind": "partitions"|"lvm", "vg", "pv_size", "vg_free_gib", "volumes": [...]}
#   }
# --------------------------------------------------------------------------

DATA_KINDS = ("partitions", "lvm")
DATA_FILESYSTEMS = ("btrfs", "ext4", "xfs")
PART_FILESYSTEMS = DATA_FILESYSTEMS + ("none",)
LV_FILESYSTEMS = DATA_FILESYSTEMS + ("none",)
LVM_PV_PARTLABEL = "MDT_LVM_PV"
LINUX_FS_TYPE_GUID = "0FC63DAF-8483-4772-8E79-3D69D8477DE4"
LINUX_LVM_TYPE_GUID = "E6D6D379-F507-44C2-A23C-238F2A3DF928"
ZFS_TYPE_GUID = "6A898CC3-1DD2-11B2-99A6-080020736631"
COW_LABEL = "MDT_COW"
IOBENCH_PARTLABEL = "iobench-raw"
DEFAULT_IOBENCH_RAW_GB = 8
LVM_EXTENT_MIB = 4
LABEL_RE = re.compile(r"[A-Za-z0-9_.-]{1,12}")  # xfs limits labels to 12 characters
LV_NAME_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.+-]{0,30}")
PERCENT_RE = re.compile(r"(0|[1-9][0-9]{0,2})(?:\.([0-9]{1,2}))?%")
RESERVED_LABELS = frozenset({"esp", "efi", "boot", "root", "swap", LVM_PV_PARTLABEL, IOBENCH_PARTLABEL})
_RESERVED_MOUNT_TREES = (
    "/boot", "/etc", "/usr", "/var", "/home", "/root", "/tmp", "/var/lib/dpkg",
    "/proc", "/sys", "/dev", "/run", "/bin", "/sbin", "/lib",
    # runner tree-cache paths (mdt-cockpit): a data area must never shadow them
    "/srv/tree-cache", "/srv/tree-upper",
)
# Parents of live system state are refused too. No system-tree filesystem is
# pre-populated by design today; adding an exception requires naming the exact
# mountpoint here and documenting how its contents are seeded.
_RESERVED_MOUNT_PARENTS = ("/var", "/var/lib", "/srv", "/home", "/root", "/tmp")
PREPOPULATED_SYSTEM_MOUNTS: frozenset[str] = frozenset()
MAX_GIB = 10240
GPT_MAX_PARTITIONS = 128
# Minimum sizes (sectors) per entry type; messages name entry, size and minimum.
MIN_SECTORS = {"btrfs": 512 * MIB_SECTORS, "xfs": 512 * MIB_SECTORS, "ext4": 64 * MIB_SECTORS,
               "none": 64 * MIB_SECTORS, "pv": 2 * SECTORS_PER_GIB}
MIN_LV_EXTENTS = 16  # 64 MiB
PARTITIONING_KEYS = frozenset({"root", "reserve_gb", "data", "iobench_raw_gb"})
DATA_KEYS = {
    "partitions": frozenset({"kind", "volumes"}),
    "lvm": frozenset({"kind", "vg", "pv_size", "vg_free_gib", "volumes"}),
}
PARTITION_VOLUME_KEYS = frozenset({"size", "fs", "mount", "label", "type", "subvol"})
LV_VOLUME_KEYS = frozenset({"name", "size", "fs", "mount", "label", "subvol"})
REMOVED_KEYS = (
    "disk_layout", "root_max_size_gb", "data_area", "data_partition", "data_lvm", "root_resize_policy",
)
DOC = "docs/DESIGN-data-area-v2.md"


@dataclass(frozen=True)
class Size:
    """One size request. kind: gib | percent | star | keep.

    value: GiB for gib, hundredths of a percent for percent (4000 == "40%").
    """

    kind: str
    value: int = 0

    @property
    def is_star(self) -> bool:
        return self.kind == "star"

    def describe(self) -> str:
        if self.kind == "gib":
            return f"{self.value} GiB"
        if self.kind == "percent":
            return f"{self.value / 100:g}%"
        return "*" if self.kind == "star" else "keep"


STAR = Size("star")
KEEP = Size("keep")


def parse_size(raw: object, what: str, *, allow_keep: bool = False) -> Size:
    """GiB integer (or digit string), 'N%' (0 < N <= 100, 2 decimals) or '*'; root also 'keep'."""
    if raw == "*":
        return STAR
    if raw == "keep" and allow_keep:
        return KEEP
    expected = (
        f"{what}: size must be an integer GiB value, a percent like \"40%\" (0-100, 2 decimals) or \"*\""
        + (" or \"keep\"" if allow_keep else "")
    )
    if isinstance(raw, bool):
        raise LayoutPolicyError(expected)
    if isinstance(raw, int):
        if not 1 <= raw <= MAX_GIB:
            raise LayoutPolicyError(f"{what}: size must be an integer GiB value from 1 to {MAX_GIB}")
        return Size("gib", raw)
    if isinstance(raw, str):
        if raw.isdigit():
            return parse_size(int(raw), what)
        match = PERCENT_RE.fullmatch(raw)
        if match:
            hundredths = int(match.group(1)) * 100 + int((match.group(2) or "0").ljust(2, "0"))
            if 0 < hundredths <= 10000:
                return Size("percent", hundredths)
    raise LayoutPolicyError(expected)


@dataclass(frozen=True)
class DataVolume:
    """A data partition (partitions kind) or a logical volume (lvm kind)."""

    name: str  # LV name; "" for a partition
    size: Size
    fs: str
    mount: str | None
    label: str | None
    ptype: str = "linux"  # raw partitions only: linux | zfs (GPT type GUID)

    @property
    def type_guid(self) -> str:
        return ZFS_TYPE_GUID if self.ptype == "zfs" else LINUX_FS_TYPE_GUID


@dataclass(frozen=True)
class PoolEntry:
    """One data partition as the pool planner sees it."""

    name: str  # for messages
    partlabel: str
    type_guid: str
    size: Size
    min_sectors: int


@dataclass(frozen=True)
class DataArea:
    kind: str  # none | partitions | lvm
    vg: str = ""
    volumes: tuple[DataVolume, ...] = ()
    pv_size: Size = STAR  # lvm: size of the PV partition
    vg_free_gib: int = 0

    @property
    def mode(self) -> str:  # alias used by the installer's "is there a data area" checks
        return self.kind

    @property
    def partition_count(self) -> int:
        if self.kind == "partitions":
            return len(self.volumes)
        return 1 if self.kind == "lvm" else 0

    def entries(self) -> list[PoolEntry]:
        """The data partitions in disk order."""
        if self.kind == "partitions":
            return [
                PoolEntry(f"volume[{i}] ({v.label})", v.label or "", v.type_guid, v.size, MIN_SECTORS[v.fs])
                for i, v in enumerate(self.volumes)
            ]
        if self.kind == "lvm":
            return [PoolEntry("PV", LVM_PV_PARTLABEL, LINUX_LVM_TYPE_GUID, self.pv_size, MIN_SECTORS["pv"])]
        return []

    def mounts(self) -> list[str]:
        return [v.mount for v in self.volumes if v.mount]

    @property
    def filesystems(self) -> set[str]:
        return {v.fs for v in self.volumes}


@dataclass(frozen=True)
class DiskSpec:
    """The normalized plan: root, reserve and data area."""

    root: Size
    reserve: Size  # gib (0 = none) or star
    data: DataArea
    iobench_raw_gb: int | None = DEFAULT_IOBENCH_RAW_GB

    @property
    def partition_count(self) -> int:
        return self.data.partition_count + (1 if self.iobench_raw_gb is not None else 0)

    def entries(self) -> list[PoolEntry]:
        """All planned GPT partitions between root and swap, in disk order."""
        entries = self.data.entries()
        if self.iobench_raw_gb is not None:
            entries.append(
                PoolEntry(
                    "iobench raw", IOBENCH_PARTLABEL, LINUX_FS_TYPE_GUID,
                    Size("gib", self.iobench_raw_gb), MIN_SECTORS["none"],
                )
            )
        return entries

    @property
    def root_policy(self) -> str:
        """The resize behaviour derived from `root`: keep => untouched, anything else => resize."""
        return "untouched" if self.root.kind == "keep" else "resize"

    @property
    def root_fixed_or_percent(self) -> bool:
        return self.root.kind in {"gib", "percent"}


def _check_mount(mount: object, what: str) -> str:
    if not isinstance(mount, str) or not mount.startswith("/") or (mount != "/" and mount.endswith("/")):
        raise LayoutPolicyError(f"{what} mount must be an absolute path without a trailing slash")
    if not re.fullmatch(r"/[A-Za-z0-9_.@+/-]*", mount) or "//" in mount or "/." in mount + "/":
        raise LayoutPolicyError(f"{what} mount {mount!r} contains unsupported characters or dot segments")
    if mount == "/":
        raise LayoutPolicyError(f"{what} mount {mount!r} would hide installer or system state")
    if mount in PREPOPULATED_SYSTEM_MOUNTS:
        return mount
    if any(mount == p or mount.startswith(p + "/") for p in _RESERVED_MOUNT_TREES):
        raise LayoutPolicyError(f"{what} mount {mount!r} would hide installer or system state")
    if mount in _RESERVED_MOUNT_PARENTS or any(p.startswith(mount + "/") for p in (*_RESERVED_MOUNT_TREES, *_RESERVED_MOUNT_PARENTS)):
        raise LayoutPolicyError(f"{what} mount {mount!r} would hide installer or system state")
    return mount


def _check_label(label: object, what: str) -> str:
    if not isinstance(label, str) or not LABEL_RE.fullmatch(label):
        raise LayoutPolicyError(f"{what} label must be 1-12 characters of [A-Za-z0-9_.-]")
    if label.lower() in RESERVED_LABELS or label.startswith("vbpub-swap"):
        raise LayoutPolicyError(f"{what} label {label!r} collides with a system partition/swap label")
    return label


def _check_mounts_distinct(mounts: list[str]) -> None:
    for index, first in enumerate(mounts):
        for second in mounts[index + 1:]:
            if first == second or first.startswith(second + "/") or second.startswith(first + "/"):
                raise LayoutPolicyError(
                    f"mount point collision between {first} and {second} (equal or nested mounts are refused)"
                )


def _check_fs(fs: object, what: str, allowed: tuple[str, ...]) -> str:
    if fs == "zfs":
        raise LayoutPolicyError(
            f"{what}: fs zfs is not supported yet (the installer has no zfs package/pool path; btrfs is the "
            f"supported COW filesystem). Use fs \"none\" for an unformatted volume, one of {', '.join(allowed)}"
        )
    if not isinstance(fs, str) or fs not in allowed:
        raise LayoutPolicyError(f"{what}: fs must be one of {', '.join(allowed)}")
    return fs


def _parse_volume(item: object, kind: str, index: int) -> DataVolume:
    what = f"partitioning.data.volumes[{index}]"
    if not isinstance(item, dict):
        raise LayoutPolicyError(f"{what} must be an object")
    unknown = sorted(set(item) - (PARTITION_VOLUME_KEYS if kind == "partitions" else LV_VOLUME_KEYS))
    if unknown:
        raise LayoutPolicyError(f"{what} has unknown key(s): {', '.join(unknown)}")
    name = ""
    if kind == "lvm":
        name = item.get("name")  # type: ignore[assignment]
        if not isinstance(name, str) or not LV_NAME_RE.fullmatch(name) or name in {".", "..", "snapshot", "pvmove"}:
            raise LayoutPolicyError(f"{what} lv name {name!r} is invalid")
        what = f"{what} ({name})"
    elif "name" in item:
        raise LayoutPolicyError(f"{what}: name is only allowed for lvm volumes")
    if "subvol" in item:
        raise LayoutPolicyError(f"{what}: subvol is not supported yet (the mount is the filesystem top level)")
    size = parse_size(item.get("size", "*"), what)
    fs = _check_fs(item.get("fs", "none" if kind == "lvm" else None), what, PART_FILESYSTEMS if kind == "partitions" else LV_FILESYSTEMS)
    mount, label = item.get("mount"), item.get("label")
    ptype = item.get("type", "linux")
    if ptype not in {"linux", "zfs"}:
        raise LayoutPolicyError(f"{what}: type must be linux or zfs")
    if ptype == "zfs" and fs != "none":
        raise LayoutPolicyError(f"{what}: type zfs is only for a raw partition (fs \"none\")")
    if fs == "none":
        if mount is not None:
            raise LayoutPolicyError(f"{what}: an unformatted volume (fs none) takes no mount")
        if kind == "lvm" and label is not None:
            raise LayoutPolicyError(f"{what}: an unformatted LV (fs none) takes no label")
    else:
        if mount is None:
            raise LayoutPolicyError(f"{what}: a formatted volume needs a mount point")
        _check_mount(mount, what)
    if label is None and kind == "partitions":
        raise LayoutPolicyError(
            f"{what}: a data partition needs a label (it is the GPT partition name used to recognise the "
            "partition on a rerun; use MDT_COW for the runner's cow area)"
        )
    if label is not None:
        _check_label(label, what)
    return DataVolume(name, size, fs, mount, label, ptype)


def _count_stars(named: list[tuple[str, Size]]) -> None:
    if sum(size.value for _name, size in named if size.kind == "percent") > 10000:
        raise LayoutPolicyError(
            "the percentages add up to more than 100% (" + ", ".join(
                f"{name} {size.describe()}" for name, size in named if size.kind == "percent") + ")"
        )
    stars = [name for name, size in named if size.is_star]
    if len(stars) > 1:
        raise LayoutPolicyError(
            f"exactly one \"*\" is allowed per plan, but {' and '.join(stars)} are all \"*\" "
            "(an omitted size is \"*\"): give all but one an explicit size"
        )


def _validate_static_pool_shape(named: list[tuple[str, Size]], *, allow_empty: bool = False) -> None:
    """Refuse pool shapes that cannot account for a disk at any disk size."""
    if not named and allow_empty:
        return
    _count_stars(named)
    kinds = {size.kind for _name, size in named}
    describe = ", ".join(f"{name} {size.describe()}" for name, size in named)
    if "gib" in kinds and "percent" in kinds:
        raise LayoutPolicyError(
            f"mixing percent and fixed sizes is ambiguous ({describe}); percentages are a share "
            "of the whole pool: use percentages only or fixed sizes with one growable entry"
        )
    if any(size.is_star for _name, size in named):
        return
    if kinds == {"percent"}:
        total = sum(size.value for _name, size in named)
        if total != 10000:
            raise LayoutPolicyError(
                f"without a \"*\" the percentages must add up to 100% ({describe} = {total / 100:g}%)"
            )
    elif "gib" in kinds:
        raise LayoutPolicyError(
            f"fixed sizes without a growable entry cannot account for the whole disk ({describe}); "
            "set one size to \"*\" or use percentages that add up to 100%"
        )


def parse_partitioning(raw: object) -> DiskSpec:
    """Validate the `partitioning` object (static checks; the fit against a disk is plan_pool/plan_lvs)."""
    if not isinstance(raw, dict):
        raise LayoutPolicyError("partitioning must be an object")
    unknown = sorted(set(raw) - PARTITIONING_KEYS)
    if unknown:
        raise LayoutPolicyError(f"partitioning has unknown key(s): {', '.join(unknown)}")
    root = parse_size(raw.get("root", "*"), "partitioning.root", allow_keep=True)
    raw_iobench = raw.get("iobench_raw_gb", DEFAULT_IOBENCH_RAW_GB)
    if raw_iobench is False:
        iobench_raw_gb: int | None = None
    elif isinstance(raw_iobench, bool) or not isinstance(raw_iobench, int) or not 2 <= raw_iobench <= MAX_GIB:
        raise LayoutPolicyError(
            f"partitioning.iobench_raw_gb must be an integer GiB value from 2 to {MAX_GIB} or false to disable"
        )
    else:
        iobench_raw_gb = raw_iobench
    reserve_raw = raw.get("reserve_gb", 0)
    if reserve_raw == "*":
        reserve = STAR
    elif isinstance(reserve_raw, bool) or not isinstance(reserve_raw, int) or not 0 <= reserve_raw <= MAX_GIB:
        raise LayoutPolicyError(f"partitioning.reserve_gb must be an integer GiB value from 0 to {MAX_GIB} or \"*\"")
    else:
        reserve = Size("gib", reserve_raw)
    fixed_reserve = reserve.kind == "gib" and reserve.value > 0
    data_raw = raw.get("data")
    if data_raw is None:
        if root.kind == "keep" and not reserve.is_star:
            raise LayoutPolicyError("root keep leaves space unallocated: add a data volume or set reserve_gb to \"*\"")
        entries = [("root", root)]
        if fixed_reserve or reserve.is_star:
            entries.append(("reserve_gb", reserve))
        if root.kind == "keep":
            entries = [("reserve_gb", reserve)] if fixed_reserve or reserve.is_star else []
        if reserve.is_star and any(size.kind == "percent" for _name, size in entries):
            raise LayoutPolicyError(
                "percent sizes need a known pool: they cannot be combined with reserve_gb \"*\" "
                "(the reserve is the remainder)"
            )
        _validate_static_pool_shape(entries, allow_empty=reserve.is_star)
        return DiskSpec(root, reserve, DataArea("none"), iobench_raw_gb)
    if not isinstance(data_raw, dict):
        raise LayoutPolicyError("partitioning.data must be an object")
    kind = data_raw.get("kind")
    if kind not in DATA_KINDS:
        raise LayoutPolicyError("partitioning.data.kind must be partitions or lvm")
    unknown = sorted(set(data_raw) - DATA_KEYS[kind])
    if unknown:
        raise LayoutPolicyError(f"partitioning.data has unknown key(s): {', '.join(unknown)}")
    volumes_raw = data_raw.get("volumes")
    if not isinstance(volumes_raw, list) or not volumes_raw:
        raise LayoutPolicyError("partitioning.data.volumes must be a non-empty list")
    volumes = [_parse_volume(item, kind, i) for i, item in enumerate(volumes_raw)]
    names = [v.name for v in volumes if v.name]
    if len(names) != len(set(names)):
        raise LayoutPolicyError("partitioning.data: lv names must be unique")
    for label in sorted({v.label for v in volumes if v.label}):
        if sum(1 for v in volumes if v.label == label) > 1:
            raise LayoutPolicyError(f"label {label} is used twice (labels must be unique across the plan)")
    _check_mounts_distinct([v.mount for v in volumes if v.mount])  # type: ignore[misc]
    if kind == "partitions":
        pool = ([] if root.kind == "keep" else [("root", root)]) + [
            (f"volume[{i}]", v.size) for i, v in enumerate(volumes)
        ]
        if root.kind == "keep" and not volumes and not reserve.is_star:
            raise LayoutPolicyError("root keep leaves space unallocated: add a data volume or set reserve_gb to \"*\"")
        if fixed_reserve or reserve.is_star:
            pool.append(("reserve_gb", reserve))
        if reserve.is_star and any(size.kind == "percent" for _name, size in pool):
            raise LayoutPolicyError(
                "percent sizes need a known pool: they cannot be combined with reserve_gb \"*\" "
                "(the reserve is the remainder)"
            )
        _validate_static_pool_shape(pool, allow_empty=reserve.is_star)
        area = DataArea("partitions", volumes=tuple(volumes))
    else:
        vg = data_raw.get("vg")
        if not isinstance(vg, str) or not LV_NAME_RE.fullmatch(vg) or vg in {".", ".."}:
            raise LayoutPolicyError("partitioning.data.vg must be a valid volume group name")
        pv = parse_size(data_raw.get("pv_size", "*"), "partitioning.data.pv_size")
        free = data_raw.get("vg_free_gib", 0)
        if isinstance(free, bool) or not isinstance(free, int) or not 0 <= free <= MAX_GIB:
            raise LayoutPolicyError(f"partitioning.data.vg_free_gib must be an integer GiB value from 0 to {MAX_GIB}")
        pool = ([] if root.kind == "keep" else [("root", root)]) + [("pv_size", pv)]
        if fixed_reserve or reserve.is_star:
            pool.append(("reserve_gb", reserve))
        if reserve.is_star and any(size.kind == "percent" for _name, size in pool):
            raise LayoutPolicyError(
                "percent sizes need a known pool: they cannot be combined with reserve_gb \"*\" "
                "(the reserve is the remainder)"
            )
        _validate_static_pool_shape(pool)
        _count_stars([(f"volume[{i}]", v.size) for i, v in enumerate(volumes)])
        area = DataArea("lvm", vg=vg, volumes=tuple(volumes), pv_size=pv, vg_free_gib=free)
        if sum(v.size.value for v in volumes if v.size.kind == "percent") > 10000:
            raise LayoutPolicyError("partitioning.data.volumes: the percentages add up to more than 100%")
    return DiskSpec(root, reserve, area, iobench_raw_gb)


def disk_spec(partitioning: object) -> DiskSpec:
    """The single disk plan; omitted settings resolve to root `*` plus the raw benchmark partition."""
    return parse_partitioning(partitioning or {})


def removed_keys_message(present: list[str]) -> str:
    return (
        f"configuration key(s) {', '.join(present)} were removed; the disk layout is now the single "
        f"`partitioning` object (see {DOC}): root \"*\"|\"keep\"|GiB|\"N%\", reserve_gb, data {{kind, volumes}}"
    )


def validate_prereqs(*, swap_policy: str) -> None:
    """A plan lays out the whole tail of the disk; it needs swap managed (on) or absent (off)."""
    if swap_policy not in {"on", "off"}:
        raise LayoutPolicyError(
            "partitioning needs swap_policy=on or off: with swap_policy=untouched the installer cannot know where "
            "the pool ends"
        )


# ---- the pool planner -----------------------------------------------------------------------

@dataclass(frozen=True)
class PoolPlan:
    root_size: int  # sectors
    extents: tuple[tuple[int, int], ...]  # (start, size) of each data partition, in disk order
    reserve_start: int
    reserve_sectors: int


def _gib(sectors: int) -> str:
    return f"{sectors / SECTORS_PER_GIB:.2f} GiB"


def _align_up(value: int) -> int:
    return -(-value // ALIGN) * ALIGN


def _align_down(value: int) -> int:
    return (value // ALIGN) * ALIGN


def plan_pool(
    spec: DiskSpec,
    *,
    root_start: int,
    pool_end: int,
    root_current_size: int,
    min_root_sectors: int = 0,
) -> PoolPlan:
    """Lay out root + data partitions contiguously from `root_start` to `pool_end` (first swap start, aligned).

    Percent is a share of the POOL = pool_end - root_start - reserve (with root `keep`: the pool behind the
    kept root). Every boundary is 1 MiB aligned; the `*` entry (root, a data partition, the PV, or the reserve)
    absorbs every remainder, so nothing is left over. Raises LayoutPolicyError with the numbers on any problem.
    """
    keep = spec.root.kind == "keep"
    reserve_fixed = spec.reserve.value * SECTORS_PER_GIB if spec.reserve.kind == "gib" else 0
    reserve_star = spec.reserve.is_star
    data = spec.data.entries()
    raw_sectors = (spec.iobench_raw_gb or 0) * SECTORS_PER_GIB
    allocation_end = pool_end - raw_sectors
    # entries in disk order: root first (unless keep, which is a fixed, exact size)
    names = ["root"] + [e.name for e in data]
    sizes = [spec.root] + [e.size for e in data]
    mins = [min_root_sectors] + [e.min_sectors for e in data]
    cursor0 = root_start
    if keep:
        cursor0 = _align_up(root_start + root_current_size)
    first_pool = 1 if keep else 0
    end_total = allocation_end - reserve_fixed
    total = end_total - (cursor0 if keep else root_start)
    if total <= 0:
        raise LayoutPolicyError(
            f"no space is left for the plan: swap and reserve_gb take the disk down to {_gib(max(end_total, 0))} "
            f"while root starts at {_gib(root_start)}"
        )
    items = [(n, s, m) for n, s, m in zip(names, sizes, mins)][first_pool:]
    n_star = sum(1 for _n, s, _m in items if s.is_star) + (1 if reserve_star else 0)
    kinds = {s.kind for _n, s, _m in items}
    if "gib" in kinds and "percent" in kinds:
        raise LayoutPolicyError(
            "mixing percent and fixed sizes is ambiguous: use percentages only or fixed sizes with one growable entry"
        )

    def fixed(size: Size) -> int | None:
        if size.kind == "gib":
            return size.value * SECTORS_PER_GIB
        if size.kind == "percent":
            return _align_down(total * size.value // 10000)
        return None

    requested = [fixed(s) for _n, s, _m in items]
    describe = ", ".join(f"{n} {s.describe()}" for n, s, _m in items)
    if not items and not reserve_star:
        raise LayoutPolicyError(
            f"root keep leaves {_gib(max(total, 0))} unallocated: add a data volume or set reserve_gb to \"*\""
        )
    if n_star == 0:
        if "gib" in kinds and "percent" in kinds:
            raise LayoutPolicyError(
                f"mixing percent and fixed sizes without a \"*\" is ambiguous ({describe}); percentages are a share "
                f"of the whole pool ({_gib(total)}): give one entry \"*\" or use percentages only"
            )
        if kinds == {"percent"}:
            if sum(s.value for _n, s, _m in items) != 10000:
                raise LayoutPolicyError(
                    f"without a \"*\" the percentages must add up to 100% ({describe} = "
                    f"{sum(s.value for _n, s, _m in items) / 100:g}%)"
                )
        else:
            used = sum(r or 0 for r in requested)
            if used > total:
                raise LayoutPolicyError(
                    f"the plan over-allocates the pool by {_gib(used - total)}: {describe} do not fit into "
                    f"{_gib(total)} (swap and reserve already taken)"
                )
            last = items[-1][0]
            raise LayoutPolicyError(
                f"{describe} leave {_gib(total - used)} of the disk unallocated: set {last} to \"*\" (it becomes "
                f"{_gib(total - used + (requested[-1] or 0))}), or set reserve_gb to \"*\" to keep it free on purpose"
            )
    # lay out sequentially
    later_fixed = [sum(r or 0 for r in requested[i + 1:]) for i in range(len(items))]
    layout: list[tuple[int, int]] = []
    cursor = cursor0 if keep else root_start
    for i, (name, size, _m) in enumerate(items):
        if size.is_star:
            got = end_total - cursor - later_fixed[i]
            length = got
        else:
            length = requested[i] or 0
            if i == 0 and not keep:  # an unaligned root start: align the END, not the length
                length = _align_down(cursor + length) - cursor
        layout.append((cursor, length))
        cursor += length
    leftover = end_total - cursor
    if kinds == {"percent"} and not reserve_star and leftover > 0:
        start, length = layout[-1]
        layout[-1] = (start, length + leftover)  # rounding remainder goes to the last percent entry
        leftover = 0
    reserve_sectors = reserve_fixed
    if reserve_star:
        if leftover < 0:
            raise LayoutPolicyError(
                f"the plan over-allocates the pool by {_gib(-leftover)}: {describe} do not fit into "
                f"{_gib(total)} (swap and the raw iobench partition already take their space)"
            )
        reserve_sectors = leftover + reserve_fixed
        leftover = 0
    if leftover < 0 or any(length <= 0 for _s, length in layout):
        raise LayoutPolicyError(
            f"the plan over-allocates the pool by {_gib(max(-leftover, sum(r or 0 for r in requested) - total))}: "
            f"{describe} do not fit into {_gib(total)} (swap and reserve already taken)"
        )
    if leftover:
        raise LayoutPolicyError(f"internal error: {_gib(leftover)} unallocated after planning")  # pragma: no cover
    for (name, _s, minimum), (_start, length) in zip(items, layout):
        if length < minimum:
            raise LayoutPolicyError(
                f"{name} is {length // MIB_SECTORS} MiB, below the minimum of "
                f"{minimum // MIB_SECTORS} MiB (preserve_root_size_gb)"
            )
    reserve_start = allocation_end - reserve_sectors
    raw_extent = (pool_end - raw_sectors, raw_sectors) if raw_sectors else None
    if keep:
        extents = [*layout]
        if raw_extent:
            extents.append(raw_extent)
        return PoolPlan(root_current_size, tuple(extents), reserve_start, reserve_sectors)
    extents = [*layout[1:]]
    if raw_extent:
        extents.append(raw_extent)
    return PoolPlan(layout[0][1], tuple(extents), reserve_start, reserve_sectors)


# ---- the LV planner -------------------------------------------------------------------------

@dataclass(frozen=True)
class LvPlan:
    name: str
    args: tuple[str, ...]  # lvcreate size arguments: ("-L", "120G") | ("-l", "123") | ("-l", "100%FREE")
    star: bool
    fs: str
    mount: str | None
    label: str | None


def estimate_vg_extents(pv_sectors: int) -> int:
    """Plan-time estimate of the extents of a one-PV VG (1 MiB metadata area, 4 MiB extents)."""
    return max(0, (pv_sectors // MIB_SECTORS - 1) // LVM_EXTENT_MIB)


def plan_lvs(area: DataArea, vg_extents: int) -> list[LvPlan]:
    """Concrete lvcreate arguments for a VG of `vg_extents` extents; the `*` LV is created LAST.

    Percent is a share of the whole VG (`-l N` with N floor(extents * p), what `-l P%VG` means, but exact to
    two decimals). `vg_free_gib` is kept free (taken from the `*` LV, or required to remain when there is none).
    """
    free_keep = area.vg_free_gib * 1024 // LVM_EXTENT_MIB
    fixed: dict[str, int] = {}
    for lv in area.volumes:
        if lv.size.kind == "gib":
            fixed[lv.name] = lv.size.value * 1024 // LVM_EXTENT_MIB
        elif lv.size.kind == "percent":
            fixed[lv.name] = vg_extents * lv.size.value // 10000
    used = sum(fixed.values())
    has_star = any(lv.size.is_star for lv in area.volumes)
    gib = lambda extents: f"{extents * LVM_EXTENT_MIB / 1024:.2f} GiB"  # noqa: E731
    if used > vg_extents - free_keep:
        raise LayoutPolicyError(
            f"LV sizes ({gib(used)}) exceed the VG ({gib(vg_extents)}"
            + (f" less vg_free_gib {area.vg_free_gib} GiB" if free_keep else "") + ")"
        )
    for lv in area.volumes:
        if lv.name in fixed and fixed[lv.name] < MIN_LV_EXTENTS:
            raise LayoutPolicyError(f"LV {lv.name} is {fixed[lv.name] * LVM_EXTENT_MIB} MiB, below the minimum of 64 MiB")
    star_extents = vg_extents - used - free_keep
    if has_star and star_extents < MIN_LV_EXTENTS:
        raise LayoutPolicyError(
            f"the \"*\" LV would get {max(star_extents, 0) * LVM_EXTENT_MIB} MiB, below the minimum of 64 MiB: "
            "the other LVs take the whole VG"
        )
    plans: list[LvPlan] = []
    for lv in area.volumes:
        if lv.size.is_star:
            continue
        args = ("-L", f"{lv.size.value}G") if lv.size.kind == "gib" else ("-l", str(fixed[lv.name]))
        plans.append(LvPlan(lv.name, args, False, lv.fs, lv.mount, lv.label))
    for lv in area.volumes:
        if lv.size.is_star:
            args = ("-l", "100%FREE") if not free_keep else ("-l", str(star_extents))
            plans.append(LvPlan(lv.name, args, True, lv.fs, lv.mount, lv.label))
    return plans


def data_area_packages(area: DataArea) -> list[str]:
    """Packages the data area needs on the host (installed in stage two, before any mkfs/pvcreate)."""
    if area.kind == "none":
        return []
    filesystems = area.filesystems
    packages: list[str] = []
    if area.kind == "lvm":
        packages.append("lvm2")
    if "btrfs" in filesystems:
        packages.append("btrfs-progs")
    if "xfs" in filesystems:
        packages.append("xfsprogs")
    return packages or ["util-linux"]
