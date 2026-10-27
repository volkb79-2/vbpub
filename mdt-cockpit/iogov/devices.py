"""Find the whole disks behind the protected workload's volumes. Read-only sysfs/dev lookups; no device is opened here."""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass

EXCLUDED_PREFIXES = ("loop", "ram", "zram", "sr", "fd", "nbd")


@dataclass(frozen=True)
class Disk:
    kname: str  # kernel name, e.g. a virtio or nvme disk
    node: str  # stable /dev path to open (by-id/by-path preferred), else /dev/<kname>
    sys_path: str  # <sysfs>/class/block/<kname>


def _sys_block(sysfs: str, kname: str) -> str:
    return os.path.join(sysfs, "class", "block", kname)


def _kname_for_devnum(sysfs: str, major: int, minor: int) -> str | None:
    link = os.path.join(sysfs, "dev", "block", f"{major}:{minor}")
    if not os.path.lexists(link):
        return None
    return os.path.basename(os.path.realpath(link))


def _is_partition(sysfs: str, kname: str) -> bool:
    return os.path.exists(os.path.join(_sys_block(sysfs, kname), "partition"))


def _parent_disk(sysfs: str, kname: str) -> str:
    real = os.path.realpath(_sys_block(sysfs, kname))
    return os.path.basename(os.path.dirname(real))


def whole_disks_of(sysfs: str, kname: str, _seen: set | None = None) -> set[str]:
    """Resolve a kernel block name through partitions and device-mapper/md slaves to the underlying whole disks."""

    seen = _seen if _seen is not None else set()
    if kname in seen:
        return set()
    seen.add(kname)
    if _is_partition(sysfs, kname):
        return whole_disks_of(sysfs, _parent_disk(sysfs, kname), seen)
    slaves = os.path.join(_sys_block(sysfs, kname), "slaves")
    if os.path.isdir(slaves) and os.listdir(slaves):
        out: set[str] = set()
        for slave in sorted(os.listdir(slaves)):
            out |= whole_disks_of(sysfs, slave, seen)
        return out
    return {kname}


def disks_for_path(sysfs: str, path: str) -> set[str]:
    st = os.stat(path)
    kname = _kname_for_devnum(sysfs, os.major(st.st_dev), os.minor(st.st_dev))
    if kname is None:
        return set()
    return whole_disks_of(sysfs, kname)


def stable_node(dev_root: str, kname: str) -> str:
    """Prefer /dev/disk/by-id then by-path (stable across renames); fall back to /dev/<kname>."""

    target = os.path.realpath(os.path.join(dev_root, kname))
    for sub in ("by-id", "by-path"):
        directory = os.path.join(dev_root, "disk", sub)
        if not os.path.isdir(directory):
            continue
        for name in sorted(os.listdir(directory)):
            link = os.path.join(directory, name)
            if os.path.realpath(link) == target and "-part" not in name:
                return link
    return os.path.join(dev_root, kname)


def detect(cfg) -> tuple[list[Disk], list[str]]:
    """Return (disks, notes). Explicit `devices` win; otherwise derive from `data_paths`; nothing is guessed."""

    notes: list[str] = []
    knames: dict[str, str | None] = {}
    for explicit in cfg.devices:
        real = os.path.realpath(explicit)
        kname = os.path.basename(real)
        knames[kname] = explicit
    if not cfg.devices:
        if not cfg.data_paths:
            notes.append("no data_paths and no devices configured: nothing to probe")
        for path in cfg.data_paths:
            try:
                found = disks_for_path(cfg.sysfs_root, path)
            except OSError as exc:
                notes.append(f"data path {path}: {exc.strerror or exc}")
                continue
            if not found:
                notes.append(f"data path {path}: no block device behind it (tmpfs/overlay/network?)")
            for kname in found:
                knames.setdefault(kname, None)
    disks = []
    for kname, explicit in sorted(knames.items()):
        if kname.startswith(EXCLUDED_PREFIXES) and not cfg.allow_regular_files_as_devices:
            notes.append(f"{kname}: excluded (virtual device class)")
            continue
        if kname in cfg.exclude_devices:
            notes.append(f"{kname}: excluded by config")
            continue
        node = explicit or stable_node(cfg.dev_root, kname)
        disks.append(Disk(kname=kname, node=node, sys_path=_sys_block(cfg.sysfs_root, kname)))
    return disks, notes


def is_block_node(path: str) -> bool:
    try:
        return stat.S_ISBLK(os.stat(path).st_mode)
    except OSError:
        return False
