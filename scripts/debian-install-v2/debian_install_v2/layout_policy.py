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
import re

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


# --------------------------------------------------------------------------
# Data area: the flexible middle partition (README "Data area").
#
#     ESP | /boot | root | [reserve] | DATA AREA | swap (END)
#
# `none`      nothing is created; the space behind root stays unallocated and
#             `scripts/netcup/runner/cow-carve.sh` may carve a cow partition
#             after install (legacy behaviour).
# `partition` one GPT partition with one filesystem (btrfs/ext4/xfs).
# `lvm`       one GPT partition used as the only PV of one VG, with LVs
#             {name, size_gb, fs, mount, label}; VG free space is left for
#             later lvcreate/lvextend (zfs as a managed filesystem is NOT in
#             scope: an LV meant for zfs is left unformatted with fs "none").
# The data area is always placed directly in front of swap, the reserve sits
# between root and the data area, so root can still be extended into it.
# --------------------------------------------------------------------------

DATA_AREA_MODES = ("none", "partition", "lvm")
DATA_FILESYSTEMS = ("btrfs", "ext4", "xfs")
LV_FILESYSTEMS = DATA_FILESYSTEMS + ("none",)
DATA_PARTITION_DEFAULTS = {
    "fs": "btrfs",
    "size_gb": None,
    "size_fraction": "1/3",
    "mount": "/srv/cow",
    "label": "MDT_COW",
}
DATA_PARTITION_KEYS = frozenset(DATA_PARTITION_DEFAULTS)
DATA_LVM_KEYS = frozenset({"vg", "pv_size_gb", "pv_size_fraction", "pv_label", "lvs"})
DATA_LV_KEYS = frozenset({"name", "size_gb", "fs", "mount", "label"})
LVM_PV_PARTLABEL = "MDT_LVM_PV"
LINUX_FS_TYPE_GUID = "0FC63DAF-8483-4772-8E79-3D69D8477DE4"
LINUX_LVM_TYPE_GUID = "E6D6D379-F507-44C2-A23C-238F2A3DF928"
COW_LABEL = "MDT_COW"
# Capacity LVM keeps for its own metadata and extent alignment.
LVM_METADATA_GIB = 1
FRACTION_RE = re.compile(r"([1-9][0-9]?)/([1-9][0-9]?)")
LABEL_RE = re.compile(r"[A-Za-z0-9_.-]{1,12}")  # xfs limits labels to 12 characters
LV_NAME_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.+-]{0,30}")
_RESERVED_MOUNT_PREFIXES = (
    "/boot", "/etc", "/usr", "/var/lib/dpkg", "/proc", "/sys", "/dev", "/run", "/bin", "/sbin", "/lib",
    # runner tree-cache paths (mdt-cockpit): a data area must never shadow them
    "/srv/tree-cache", "/srv/tree-upper",
)
DATA_ALIGN_SECTORS = 2048


@dataclass(frozen=True)
class DataLV:
    name: str
    size_gb: int
    fs: str
    mount: str | None
    label: str | None


@dataclass(frozen=True)
class DataArea:
    mode: str
    # partition mode
    fs: str = ""
    mount: str = ""
    label: str = ""
    # lvm mode
    vg: str = ""
    lvs: tuple[DataLV, ...] = ()
    # both: size of the partition (the PV in lvm mode)
    size_gb: int | None = None
    size_fraction: tuple[int, int] | None = None

    @property
    def partlabel(self) -> str:
        return LVM_PV_PARTLABEL if self.mode == "lvm" else self.label

    @property
    def type_guid(self) -> str:
        return LINUX_LVM_TYPE_GUID if self.mode == "lvm" else LINUX_FS_TYPE_GUID

    def mounts(self) -> list[str]:
        if self.mode == "partition":
            return [self.mount]
        return [lv.mount for lv in self.lvs if lv.mount]


def _int_gib(value: object, what: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 10240:
        raise LayoutPolicyError(f"{what} must be an integer GiB value from 1 to 10240")
    return value


def _check_mount(mount: object, what: str) -> str:
    if not isinstance(mount, str) or not mount.startswith("/") or (mount != "/" and mount.endswith("/")):
        raise LayoutPolicyError(f"{what} mount must be an absolute path without a trailing slash")
    if not re.fullmatch(r"/[A-Za-z0-9_.@+/-]*", mount) or "//" in mount or "/." in mount + "/":
        raise LayoutPolicyError(f"{what} mount {mount!r} contains unsupported characters or dot segments")
    if mount == "/" or any(mount == p or mount.startswith(p + "/") for p in _RESERVED_MOUNT_PREFIXES):
        raise LayoutPolicyError(f"{what} mount {mount!r} collides with the system tree (root, /boot, /etc, /usr, ...)")
    return mount


def _check_label(label: object, what: str) -> str:
    if not isinstance(label, str) or not LABEL_RE.fullmatch(label):
        raise LayoutPolicyError(f"{what} label must be 1-12 characters of [A-Za-z0-9_.-]")
    return label


def _size_fields(raw: dict, what: str) -> tuple[int | None, tuple[int, int] | None]:
    size = raw.get("size_gb" if what == "data_partition" else "pv_size_gb")
    fraction = raw.get("size_fraction" if what == "data_partition" else "pv_size_fraction")
    if size is not None and fraction is not None:
        raise LayoutPolicyError(f"{what}: give either a size in GiB or a disk fraction, not both")
    if size is not None:
        return _int_gib(size, f"{what} size"), None
    if fraction is None:
        raise LayoutPolicyError(f"{what}: a size in GiB or a disk fraction such as \"1/3\" is required")
    match = FRACTION_RE.fullmatch(fraction) if isinstance(fraction, str) else None
    if not match or int(match.group(1)) >= int(match.group(2)):
        raise LayoutPolicyError(f"{what}: fraction must look like \"1/3\" (numerator < denominator, below 1)")
    return None, (int(match.group(1)), int(match.group(2)))


def parse_data_area(mode: object, partition: object, lvm: object) -> DataArea:
    """Validate the three config fields and return the normalized data area.

    Pure and static: everything that needs the disk size (does it fit?) is in
    `plan_data_area`. Raises LayoutPolicyError on the first problem.
    """
    if not isinstance(mode, str) or mode not in DATA_AREA_MODES:
        raise LayoutPolicyError("data_area must be none, partition or lvm")
    if partition is not None and not isinstance(partition, dict):
        raise LayoutPolicyError("data_partition must be an object")
    if lvm is not None and not isinstance(lvm, dict):
        raise LayoutPolicyError("data_lvm must be an object")
    if mode == "none":
        return DataArea("none")
    if mode == "partition":
        raw = {**DATA_PARTITION_DEFAULTS, **(partition or {})}
        unknown = sorted(set(partition or {}) - DATA_PARTITION_KEYS)
        if unknown:
            raise LayoutPolicyError(f"data_partition has unknown key(s): {', '.join(unknown)}")
        if partition and "size_gb" in partition and "size_fraction" not in partition:
            raw["size_fraction"] = None  # an explicit size replaces the default fraction
        if raw["fs"] not in DATA_FILESYSTEMS:
            raise LayoutPolicyError(f"data_partition fs must be one of {', '.join(DATA_FILESYSTEMS)}")
        size, fraction = _size_fields(raw, "data_partition")
        if raw["label"] != COW_LABEL:
            raise LayoutPolicyError(
                f"data_partition label must be {COW_LABEL}: the runner's cow detection (cow-carve/provision-runner) "
                "finds the cow area by that label only"
            )
        return DataArea(
            "partition", fs=raw["fs"], mount=_check_mount(raw["mount"], "data_partition"),
            label=_check_label(raw["label"], "data_partition"), size_gb=size, size_fraction=fraction,
        )
    # lvm
    if not lvm:
        raise LayoutPolicyError("data_area=lvm requires data_lvm with vg and lvs")
    unknown = sorted(set(lvm) - DATA_LVM_KEYS)
    if unknown:
        raise LayoutPolicyError(f"data_lvm has unknown key(s): {', '.join(unknown)}")
    raw = {"pv_size_fraction": "1/3", **lvm}
    if "pv_size_gb" in lvm and "pv_size_fraction" not in lvm:
        raw["pv_size_fraction"] = None
    vg = raw.get("vg")
    if not isinstance(vg, str) or not LV_NAME_RE.fullmatch(vg) or vg in {".", ".."}:
        raise LayoutPolicyError("data_lvm vg must be a valid volume group name")
    size, fraction = _size_fields(raw, "data_lvm")
    lvs_raw = raw.get("lvs")
    if not isinstance(lvs_raw, list) or not lvs_raw:
        raise LayoutPolicyError("data_lvm lvs must be a non-empty list of {name, size_gb, fs, mount, label}")
    lvs: list[DataLV] = []
    names: set[str] = set()
    labels: set[str] = set()
    mounts: list[str] = []
    for item in lvs_raw:
        if not isinstance(item, dict):
            raise LayoutPolicyError("data_lvm lvs entries must be objects")
        unknown = sorted(set(item) - DATA_LV_KEYS)
        if unknown:
            raise LayoutPolicyError(f"data_lvm lv has unknown key(s): {', '.join(unknown)}")
        name = item.get("name")
        if not isinstance(name, str) or not LV_NAME_RE.fullmatch(name) or name in {".", "..", "snapshot", "pvmove"}:
            raise LayoutPolicyError(f"data_lvm lv name {name!r} is invalid")
        if name in names:
            raise LayoutPolicyError(f"data_lvm lv name {name!r} is used twice")
        names.add(name)
        fs = item.get("fs", "none")
        if fs not in LV_FILESYSTEMS:
            raise LayoutPolicyError(
                f"data_lvm lv {name}: fs must be one of {', '.join(LV_FILESYSTEMS)} "
                "(zfs is not managed: leave the LV unformatted with fs \"none\")"
            )
        mount = item.get("mount")
        label = item.get("label")
        if fs == "none":
            if mount is not None or label is not None:
                raise LayoutPolicyError(f"data_lvm lv {name}: an unformatted LV (fs none) takes no mount or label")
        else:
            if mount is None:
                raise LayoutPolicyError(f"data_lvm lv {name}: a formatted LV needs a mount point")
            _check_mount(mount, f"data_lvm lv {name}")
            mounts.append(mount)
            if label is not None:
                _check_label(label, f"data_lvm lv {name}")
                if label in labels:
                    raise LayoutPolicyError(f"data_lvm label {label!r} is used twice")
                labels.add(label)
        lvs.append(DataLV(name, _int_gib(item.get("size_gb"), f"data_lvm lv {name} size"), fs, mount, label))
    _check_mounts_distinct(mounts)
    return DataArea("lvm", vg=vg, lvs=tuple(lvs), size_gb=size, size_fraction=fraction)


def _check_mounts_distinct(mounts: list[str]) -> None:
    for index, first in enumerate(mounts):
        for second in mounts[index + 1:]:
            if first == second or first.startswith(second + "/") or second.startswith(first + "/"):
                raise LayoutPolicyError(
                    f"mount point collision between {first} and {second} (equal or nested mounts are refused)"
                )


def data_area_size_gib(area: DataArea, disk_gib: float) -> int:
    """Partition size in GiB: explicit, or the fraction of the disk in 8 GiB steps (1/3 of 512 -> 168)."""
    if area.size_gb is not None:
        return area.size_gb
    assert area.size_fraction is not None
    num, den = area.size_fraction
    return _round_down(int(disk_gib * num // den), ROUND_GIB)


@dataclass(frozen=True)
class DataAreaPlan:
    start: int  # sectors
    size: int   # sectors
    reserve_sectors: int  # unallocated gap between root end and data start
    lv_free_gib: float | None


def plan_data_area(
    area: DataArea,
    *,
    disk_sectors: int,
    root_end: int,
    first_swap_start: int,
) -> DataAreaPlan:
    """Place the data area directly in front of swap, behind root; refuse what does not fit.

    All numbers are 512-byte sectors. `root_end` is the first sector after the
    planned root, `first_swap_start` the first swap sector (swap is last).
    """
    if area.mode == "none":
        raise LayoutPolicyError("no data area to plan")
    want_gib = data_area_size_gib(area, disk_sectors / SECTORS_PER_GIB)
    size = want_gib * SECTORS_PER_GIB
    end = (first_swap_start // DATA_ALIGN_SECTORS) * DATA_ALIGN_SECTORS
    start = end - size
    root_end_aligned = -(-root_end // DATA_ALIGN_SECTORS) * DATA_ALIGN_SECTORS
    if start < root_end_aligned:
        raise LayoutPolicyError(
            f"the data area of {want_gib} GiB does not fit between root and swap: only "
            f"{max(0, end - root_end_aligned) / SECTORS_PER_GIB:.2f} GiB are free there "
            "(reduce the data area, the root cap or swap)"
        )
    lv_free = None
    if area.mode == "lvm":
        total = sum(lv.size_gb for lv in area.lvs)
        usable = want_gib - LVM_METADATA_GIB
        if total > usable:
            raise LayoutPolicyError(
                f"LV sizes ({total} GiB) exceed the physical volume ({want_gib} GiB less "
                f"{LVM_METADATA_GIB} GiB LVM metadata); the VG must keep free space"
            )
        if total == usable:
            raise LayoutPolicyError(
                f"LV sizes ({total} GiB) leave no free VG space in a {want_gib} GiB physical volume; "
                "keep some free space for later lvextend/snapshots"
            )
        lv_free = float(usable - total)
    return DataAreaPlan(start, size, start - root_end_aligned, lv_free)


def validate_data_area_prereqs(*, swap_policy: str, root_resize_policy: str, root_capped: bool) -> None:
    """Refuse data-area configurations whose geometry cannot be guaranteed."""
    if swap_policy != "on":
        raise LayoutPolicyError(
            "data_area needs swap_policy=on: the data area sits directly in front of the "
            "swap partition(s), which must be the last allocation on the disk"
        )
    if root_resize_policy != "resize":
        raise LayoutPolicyError("data_area needs root_resize_policy=resize (the installer lays out the whole tail)")
    if not root_capped:
        raise LayoutPolicyError(
            "data_area needs a capped root: set disk_layout=dev-host or root_max_size_gb, "
            "otherwise root takes all space and no room remains for the data area"
        )


def data_area_packages(area: DataArea) -> list[str]:
    """Packages the data area needs on the host (installed in stage two, before any mkfs/pvcreate)."""
    if area.mode == "none":
        return []
    filesystems = {area.fs} if area.mode == "partition" else {lv.fs for lv in area.lvs}
    packages: list[str] = []
    if area.mode == "lvm":
        packages.append("lvm2")
    if "btrfs" in filesystems:
        packages.append("btrfs-progs")
    if "xfs" in filesystems:
        packages.append("xfsprogs")
    return packages or ["util-linux"]
