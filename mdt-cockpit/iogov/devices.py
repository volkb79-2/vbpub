"""Find the whole disks behind the protected workload's volumes. Read-only sysfs/dev lookups; no device is opened here."""

from __future__ import annotations

import os
import re
import stat
from dataclasses import dataclass

from . import safeio

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


def _walk(sysfs: str, kname: str, seen: set, unresolved: list[str]) -> set[str]:
    if kname in seen:
        return set()
    seen.add(kname)
    if _is_partition(sysfs, kname):
        return _walk(sysfs, _parent_disk(sysfs, kname), seen, unresolved)
    slaves = os.path.join(_sys_block(sysfs, kname), "slaves")
    if os.path.isdir(slaves) and os.listdir(slaves):
        out: set[str] = set()
        for slave in sorted(os.listdir(slaves)):
            out |= _walk(sysfs, slave, seen, unresolved)
        return out
    if kname.startswith(("dm-", "md")):  # a mapped/raid device without slaves is not a disk: unresolved, never a guess
        unresolved.append(kname)
        return set()
    return {kname}


def whole_disks_status(sysfs: str, kname: str) -> tuple[set[str], list[str]]:
    """(whole disks, unresolved names). Any unresolved name means the disk set is incomplete and must not be used."""

    unresolved: list[str] = []
    disks = _walk(sysfs, kname, set(), unresolved)
    return disks, unresolved


def whole_disks_of(sysfs: str, kname: str) -> set[str]:
    """Resolve a kernel block name through partitions and device-mapper/md slaves to the underlying whole disks.

    Empty when ANY branch of a stacked device cannot be resolved (a partial answer would silently drop a disk)."""

    disks, unresolved = whole_disks_status(sysfs, kname)
    return set() if unresolved else disks


def _sys_has(sysfs: str, kname: str) -> bool:
    return os.path.isdir(_sys_block(sysfs, kname))


_MOUNT_ESC = re.compile(r"\\([0-7]{3})")


def _unescape(field: str) -> str:
    return _MOUNT_ESC.sub(lambda m: chr(int(m.group(1), 8)), field)


def read_mountinfo(procfs: str) -> list[dict]:
    """Parse <procfs>/self/mountinfo: mount point, fs type, mount source, major:minor. Unreadable or malformed lines are skipped."""

    text = safeio.read_text(os.path.join(procfs, "self", "mountinfo"), 4 * 1024 * 1024) or ""
    out = []
    for line in text.splitlines():
        head, sep, tail = line.partition(" - ")
        fields, rest = head.split(), tail.split()
        if not sep or len(fields) < 5 or len(rest) < 2:
            continue
        out.append({"majmin": fields[2], "mnt": _unescape(fields[4]), "fstype": rest[0], "source": _unescape(rest[1])})
    return out


def _covers(m: dict, real: str) -> bool:
    mnt = m["mnt"].rstrip("/") or "/"
    return real == mnt or mnt == "/" or real.startswith(mnt + "/")


def _mount_for(mounts: list[dict], real: str, majmin: str | None = None) -> dict | None:
    """The mount holding `real`. A mount whose major:minor equals the path's st_dev is preferred (the last such entry on a tie);
    otherwise the longest mount point that is a path prefix, the LAST entry winning a tie (a mount stacked on another)."""

    if majmin is not None:
        same = [m for m in mounts if m["majmin"] == majmin and _covers(m, real)]
        if same:
            return same[-1]
    best = None
    for m in mounts:
        mnt = m["mnt"].rstrip("/") or "/"
        if real == mnt or mnt == "/" or real.startswith(mnt + "/"):
            if best is None or len(mnt) >= len(best[0]):
                best = (mnt, m)
    return best[1] if best else None


def _kname_of_source(sysfs: str, dev_root: str, source: str) -> str | None:
    """Kernel name of a mount source such as /dev/mapper/vg-lv, /dev/vda1 or /dev/disk/by-uuid/...; None if it is not a block device."""

    if source.startswith("/dev/"):
        real = os.path.realpath(os.path.join(dev_root, source[len("/dev/"):]))
        if os.path.exists(real) and _sys_has(sysfs, os.path.basename(real)):
            return os.path.basename(real)
        name = os.path.basename(source)
        if source.startswith("/dev/mapper/"):  # no /dev view (a container, a test): the device-mapper name is in sysfs
            blk = os.path.join(sysfs, "class", "block")
            for entry in sorted(os.listdir(blk)) if os.path.isdir(blk) else []:
                if (safeio.read_text(os.path.join(blk, entry, "dm", "name")) or "").strip() == name:
                    return entry
    return None


def _btrfs_members(sysfs: str, kname: str) -> list[str]:
    """Every member device of the btrfs filesystem that `kname` belongs to: /sys/fs/btrfs/<uuid>/devices/<kname> (no btrfs-progs, no root)."""

    base = os.path.join(sysfs, "fs", "btrfs")
    if not os.path.isdir(base):
        return []
    for uuid in sorted(os.listdir(base)):
        devices = os.path.join(base, uuid, "devices")
        if os.path.isdir(devices) and kname in os.listdir(devices):
            return sorted(os.listdir(devices))
    return []


def resolve_path(sysfs: str, path: str, procfs: str = "/proc", dev_root: str = "/dev") -> tuple[set[str], list[str]]:
    """Whole disks behind `path` and the facts seen on the way. An empty set means NOT resolved (never a guess); `seen` explains why.

    1. st_dev -> <sysfs>/dev/block/<maj:min> (ext4, xfs, ...), then partitions / device-mapper / md / dm-crypt slaves recursively.
    2. A filesystem with an anonymous st_dev (btrfs, 0:N): the mount holding the path (<procfs>/self/mountinfo), its source device, and for
       btrfs ALL member devices from <sysfs>/fs/btrfs/<uuid>/devices, each resolved as in 1.
    """

    st = os.stat(path)
    seen = [f"path {path}: st_dev {os.major(st.st_dev)}:{os.minor(st.st_dev)}"]
    kname = _kname_for_devnum(sysfs, os.major(st.st_dev), os.minor(st.st_dev))
    roots: list[str] = []
    if kname is not None:
        roots = [kname]
    else:
        seen.append("st_dev is not a block device (anonymous device number)")
        mounts = read_mountinfo(procfs)
        mount = _mount_for(mounts, os.path.realpath(path), f"{os.major(st.st_dev)}:{os.minor(st.st_dev)}")
        if mount is None:
            seen.append(f"no mount found for it in {procfs}/self/mountinfo ({len(mounts)} mounts read)")
        else:
            seen.append(f"mount {mount['mnt']} fstype={mount['fstype']} source={mount['source']}")
            src = _kname_of_source(sysfs, dev_root, mount["source"])
            if src is None:
                seen.append(f"mount source {mount['source']} is not a block device known to {sysfs}/class/block")
            elif mount["fstype"] == "btrfs":
                members = _btrfs_members(sysfs, src)
                seen.append(f"btrfs source device {src}; members in {sysfs}/fs/btrfs/*/devices: {members or 'none found'}")
                roots = members
            else:
                roots = [src]
    disks: set[str] = set()
    for root in roots:
        if not _sys_has(sysfs, root):
            seen.append(f"{root}: no {sysfs}/class/block entry")
            return set(), seen
        found, unresolved = whole_disks_status(sysfs, root)
        if unresolved:
            seen.append(f"{root}: stacked device not fully resolved (no underlying disk for {sorted(unresolved)}); "
                        f"partial whole disks seen {sorted(found)}; not guessed")
            return set(), seen
        disks |= found
    missing = sorted(d for d in disks if not _sys_has(sysfs, d))
    if missing:
        seen.append(f"underlying device(s) {missing} have no {sysfs}/class/block entry")
        return set(), seen
    if roots:
        seen.append(f"resolved {roots} -> whole disks {sorted(disks)}")
    return disks, seen


def disks_for_path(sysfs: str, path: str, procfs: str = "/proc", dev_root: str = "/dev") -> set[str]:
    return resolve_path(sysfs, path, procfs, dev_root)[0]


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


def _by_name_hint(dev_root: str, explicit: str) -> str:
    """When an explicit device does not exist: the same name under the OTHER /dev/disk directory, else what the by-id/by-path directories hold."""

    name = os.path.basename(explicit)
    for sub in ("by-id", "by-path"):
        alt = os.path.join(dev_root, "disk", sub, name)
        if os.path.lexists(alt) and os.path.join(dev_root, "disk", sub) != os.path.dirname(explicit):
            return f"; a link of that name exists under /dev/disk/{sub}: use {alt}"
    held = []
    for sub in ("by-id", "by-path"):
        directory = os.path.join(dev_root, "disk", sub)
        if os.path.isdir(directory):
            held += [os.path.join(directory, n) for n in sorted(os.listdir(directory)) if "-part" not in n][:8]
    return ("; whole-disk links seen: " + ", ".join(held)) if held else "; no /dev/disk/by-id or by-path directory is visible from this process"


def detect(cfg) -> tuple[list[Disk], list[str]]:
    """Return (disks, notes). Explicit `devices` win; otherwise derive from `data_paths`; nothing is guessed."""

    notes: list[str] = []
    knames: dict[str, str | None] = {}
    for explicit in cfg.devices:
        if not os.path.exists(explicit):  # realpath() of a missing path returns it unchanged: a dangling link must not become a "kname"
            real = os.path.realpath(explicit)
            why = "dangling symlink to " + real if os.path.islink(explicit) else "does not exist"
            notes.append(f"device {explicit}: {why} (as seen by this process){_by_name_hint(cfg.dev_root, explicit)}")
            continue
        real = os.path.realpath(explicit)
        kname = os.path.basename(real)
        if _sys_has(cfg.sysfs_root, kname):
            whole = whole_disks_of(cfg.sysfs_root, kname)
            if whole != {kname}:
                notes.append(f"device {explicit} is not a whole disk ({kname}); using its whole disk(s) {sorted(whole)}")
                for w in sorted(whole):
                    knames.setdefault(w, None)
                continue
        knames[kname] = explicit
    if not cfg.devices:
        if not cfg.data_paths:
            notes.append("no data_paths and no devices configured: nothing to probe")
        for path in cfg.data_paths:
            try:
                found, seen = resolve_path(cfg.sysfs_root, path, cfg.procfs_root, cfg.dev_root)
            except OSError as exc:
                notes.append(f"data path {path}: {exc.strerror or exc}")
                continue
            if not found:
                notes.append(f"data path {path}: no whole disk could be determined (not guessed); saw: " + "; ".join(seen)
                             + "; set `devices = [...]` explicitly")
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
