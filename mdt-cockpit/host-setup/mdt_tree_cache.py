#!/usr/bin/env python3
"""HOST_ROLE=dedicated-dev shared tree cache (AD2-2): plan, render, activate, check, garbage-collect.

The host owns WHERE and under which POLICY; run-gate owns placement, assay owns content, cowtree owns the
generic publish/lease mechanism (PARALLELISATION-DESIGN-MEMO-ADDENDUM-2, section 2). This module is the host
half:

* ``dev-cache.slice`` (persistent, under dev.slice, MemoryLow from ``DEV_CACHE_MEMORY_LOW``);
* two tmpfs mount units, the tree cache (``MDT_TREE_CACHE_ROOT``) and the overlay upper area
  (``MDT_TREE_UPPER_ROOT``), sized from RAM, ``mode=2770 root:<gate-runner group>``;
* the ``tree_cache`` block of ``/etc/mdt/host-capacity.json`` (merged, never replaced);
* ``mdt-tree-cache-gc.timer`` which runs ``gc`` below.

Everything is derived from /etc/mdt/host-setup.env, so a reinstalled host is rebuilt from the repo plus the
env keys. HOST_ROLE other than dedicated-dev: nothing is rendered, and everything this module owns is removed
again (owned = a marker line in the unit file), so shared-prod stays exactly as it was.

This is the vbpub implementation of the neutral host contract in libraries/cowtree/HOST-CONTRACT.md (optional
for every consumer; no other tool names this module's keys).

Directory and lease format (the contract cowtree's ``cache`` module publishes and this GC consumes)
------------------------------------------------------------------------------------------------
Under ``MDT_TREE_CACHE_ROOT`` (a tmpfs mount point; GC refuses anything else)::

    <key>/                 a COMPLETE key: atomically renamed from <key>.tmp-<id>
    <key>/COMPLETE         written last inside <key>.tmp-<id>; lanes accept only keys that have it
    <key>/tree/            the content (never inspected here)
    <key>/.lease           lease file: every lane holds LOCK_SH (flock) on it for its whole life
    <key>/.last-used       mtime = the last lease/use (touched by the lessee); fallback COMPLETE, then <key>
    <key>.tmp-<id>/        a build in progress; the builder holds LOCK_EX on .locks/<key> while it builds
    .locks/<key>           build locks (never removed here)
    .trash-*               a key being removed (GC renames, then deletes; leftovers are removed next run)

A key is removed only when ALL hold: it is idle longer than MDT_TREE_CACHE_GC_IDLE; LOCK_EX|LOCK_NB on
``<key>/.lease`` succeeds (no live lessee); no process has it mounted (``<root>/<key>`` as the bind root or
mount source, or inside an overlay ``lowerdir=``, in ANY /proc/*/mountinfo). A ``<key>.tmp-<id>`` is removed
when LOCK_EX|LOCK_NB on ``.locks/<key>`` succeeds and nothing mounts it. Under ``MDT_TREE_UPPER_ROOT``,
``<token>/`` (per-lane overlay uppers/workdirs, created and removed by run-gate) is an orphan when it is
older than MDT_TREE_UPPER_GC_IDLE and no mountinfo names it (``upperdir=``/``workdir=``). Any doubt
(unreadable mountinfo, symlink entry, root not a tmpfs mount point) means: keep.

Standard library only; installed beside mdt_capacity.py.
"""
from __future__ import annotations

import argparse
import errno
import fcntl
import grp
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mdt_capacity as cap  # noqa: E402

MIB = 1024 * 1024
OVERLAY_OPTS = "redirect_dir=on,xino=auto"
SLICE = "dev-cache.slice"
DEFAULT_CACHE_ROOT = "/srv/tree-cache"
DEFAULT_UPPER_ROOT = "/srv/tree-upper"
DEFAULT_GROUP = "runner"
DEFAULT_CACHE_PCT = 25
DEFAULT_UPPER_PCT = 10
MIN_AREA = 64 * MIB
PAGE = os.sysconf("SC_PAGE_SIZE")
STALE_BUILD_MIN_AGE = 3600  # a <key>.tmp-* build younger than this is never removed (its builder may not hold .locks/<key> yet)
BYTES_PER_INODE = 16 * 1024
MODE = "2770"
OWN_MARK_SLICE = "# MDT-TREE-CACHE owned"
OWN_MARK_MOUNT = "# MDT-TREE-AREA "
SYSTEMD_DIR = Path("/etc/systemd/system")
CAPACITY_JSON = Path("/etc/mdt/host-capacity.json")
_TIME_RE = re.compile(r"^[1-9][0-9]*(s|min|h|d)$")
_PATH_RE = re.compile(r"^/[A-Za-z0-9._+/-]+$")
_DENY_ROOTS = ("/", "/bin", "/boot", "/dev", "/etc", "/home", "/lib", "/opt", "/proc", "/root", "/run", "/sbin",
               "/sys", "/tmp", "/usr", "/var", "/srv", "/mnt", "/media", "/lib64")
# Nothing below these may become a tmpfs mount point (a tmpfs over /var/lib/docker or /home/x hides real data).
# /srv/<name>, /mnt/<name> and /tmp/<name> stay usable; activate additionally refuses a non-empty directory.
_DENY_TREES = ("/bin", "/boot", "/dev", "/etc", "/home", "/lib", "/lib64", "/opt", "/proc", "/root", "/run", "/sbin",
               "/sys", "/usr", "/var")


class TreeCacheError(ValueError):
    """A key or value that cannot be used; the installer exits 2 before changing anything."""


@dataclass(frozen=True)
class Plan:
    enabled: bool
    cache_root: str = DEFAULT_CACHE_ROOT
    cache_size: int = 0
    cache_inodes: int = 0
    upper_root: str | None = None
    upper_size: int = 0
    upper_inodes: int = 0
    group: str = DEFAULT_GROUP
    gid: int = 0
    cache_low: str = ""
    zswap_writeback: str = "yes"
    gc_idle: int = 6 * 3600
    upper_gc_idle: int = 24 * 3600
    gc_interval: str = "15min"


def mount_unit_name(path: str) -> str:
    """systemd-escape -p --suffix=mount, without the binary (hermetic): '/' -> '-', other bytes outside
    [A-Za-z0-9:_.] -> \\xHH, a leading '.' escaped."""
    trimmed = path.strip("/")
    if not trimmed:
        return "-.mount"
    out = []
    for i, ch in enumerate(trimmed):
        if ch == "/":
            out.append("-")
        elif re.fullmatch(r"[A-Za-z0-9:_]", ch) or (ch == "." and i != 0):
            out.append(ch)
        else:
            out.append("".join(f"\\x{b:02x}" for b in ch.encode()))
    return "".join(out) + ".mount"


def validate_tree_path(value: str) -> str | None:
    if not _PATH_RE.match(value) or "//" in value or value.endswith("/") or any(p in ("", ".", "..") for p in value.split("/")[1:]):
        return "must be an absolute path of letters, digits and . _ + - / (no '..', no trailing slash)"
    if value in _DENY_ROOTS:
        return f"{value} is a system directory; use a dedicated directory such as {DEFAULT_CACHE_ROOT}"
    for prefix in _DENY_TREES:
        if value.startswith(prefix + "/"):
            return f"{value} is inside the system directory {prefix}; use a dedicated directory such as {DEFAULT_CACHE_ROOT}"
    for system in _DENY_ROOTS[1:]:
        if system != "/srv" and system.startswith(value + "/"):
            return f"{value} contains the system directory {system}"
    return None


def validate_area_size(value: str) -> str | None:
    """empty = default % of RAM, `-` = disabled, or a size of at least 64M."""
    if value.strip() in ("", "-"):
        return None
    parsed = cap.parse_size_bytes(value)
    if parsed is None:
        return "must be a size such as 2G, empty (default share of RAM) or `-` (disabled)"
    if parsed < MIN_AREA:
        return "must be at least 64M"
    return None


def validate_timespan(value: str) -> str | None:
    if value.strip() and not _TIME_RE.match(value.strip()):
        return "must be a whole number followed by s, min, h or d (such as 15min, 6h, 1d)"
    return None


def validate_inodes(value: str) -> str | None:
    if value.strip() and not re.fullmatch(r"[1-9][0-9]{3,8}", value.strip()):
        return "must be a whole number of inodes (1000 to 999999999) or empty (derived from the size)"
    return None


def validate_group(value: str) -> str | None:
    if value.strip() and not re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}", value.strip()):
        return "must be a group name"
    return None


def _seconds(value: str) -> int:
    m = re.fullmatch(r"([0-9]+)(s|min|h|d)", value)
    assert m, value
    return int(m.group(1)) * {"s": 1, "min": 60, "h": 3600, "d": 86400}[m.group(2)]


def _get(values: dict[str, str], key: str, default: str = "") -> str:
    return (values.get(key, "") or "").strip() or default


def _area(values: dict[str, str], size_key: str, inodes_key: str, pct: int, mem_total: int) -> tuple[int, int] | None:
    raw = _get(values, size_key)
    if raw == "-":
        return None
    bad = validate_area_size(raw)
    if bad:
        raise TreeCacheError(f"{size_key}={raw!r}: {bad}")
    size = cap.parse_size_bytes(raw) if raw else max(MIN_AREA, mem_total * pct // 100)
    size -= size % PAGE  # tmpfs rounds size= to whole pages: store the rounded value so the mount, the check and the JSON agree
    raw_i = _get(values, inodes_key)
    bad = validate_inodes(raw_i)
    if bad:
        raise TreeCacheError(f"{inodes_key}={raw_i!r}: {bad}")
    inodes = int(raw_i) if raw_i else max(65536, min(4 * 1024 * 1024, size // BYTES_PER_INODE))
    return size, inodes


def make_plan(values: dict[str, str], *, mem_total: int, dedicated: bool,
              gid_lookup=lambda name: grp.getgrnam(name).gr_gid) -> Plan:
    """Everything derived from the env. Not dedicated-dev, or MDT_TREE_CACHE_SIZE=`-`: disabled."""
    if not dedicated or _get(values, "MDT_TREE_CACHE_SIZE") == "-":
        return Plan(enabled=False)
    cache_root = _get(values, "MDT_TREE_CACHE_ROOT", DEFAULT_CACHE_ROOT)
    upper_root = _get(values, "MDT_TREE_UPPER_ROOT", DEFAULT_UPPER_ROOT)
    for key, path in (("MDT_TREE_CACHE_ROOT", cache_root), ("MDT_TREE_UPPER_ROOT", upper_root)):
        bad = validate_tree_path(path)
        if bad:
            raise TreeCacheError(f"{key}={path!r}: {bad}")
    cache = _area(values, "MDT_TREE_CACHE_SIZE", "MDT_TREE_CACHE_INODES", DEFAULT_CACHE_PCT, mem_total)
    assert cache is not None
    upper = _area(values, "MDT_TREE_UPPER_SIZE", "MDT_TREE_UPPER_INODES", DEFAULT_UPPER_PCT, mem_total)
    if upper is not None and (cache_root == upper_root or cache_root.startswith(upper_root + "/") or upper_root.startswith(cache_root + "/")):
        raise TreeCacheError(f"MDT_TREE_CACHE_ROOT={cache_root} and MDT_TREE_UPPER_ROOT={upper_root} must be two separate directories (neither inside the other)")
    total = cache[0] + (upper[0] if upper else 0)
    if total > mem_total // 2:
        raise TreeCacheError(f"the tree cache and upper areas ({cap.format_size(total)}) may not exceed half of RAM ({cap.format_size(mem_total // 2)}); "
                             "lower MDT_TREE_CACHE_SIZE / MDT_TREE_UPPER_SIZE")
    group = _get(values, "MDT_TREE_CACHE_GROUP", DEFAULT_GROUP)
    bad = validate_group(group)
    if bad:
        raise TreeCacheError(f"MDT_TREE_CACHE_GROUP={group!r}: {bad}")
    try:
        gid = gid_lookup(group)
    except KeyError:
        raise TreeCacheError(f"MDT_TREE_CACHE_GROUP={group!r}: no such group on this host (the gate runner's group; the areas are root:<group> mode {MODE})") from None
    for key in ("MDT_TREE_CACHE_GC_IDLE", "MDT_TREE_UPPER_GC_IDLE", "MDT_TREE_CACHE_GC_INTERVAL"):
        bad = validate_timespan(_get(values, key))
        if bad:
            raise TreeCacheError(f"{key}={_get(values, key)!r}: {bad}")
    low = _get(values, "DEV_CACHE_MEMORY_LOW")
    if low == "-":
        low = ""
    if low:
        parsed = cap.parse_size_bytes(low)
        if parsed is None or parsed <= 0:
            raise TreeCacheError(f"DEV_CACHE_MEMORY_LOW={low!r} is not a size (such as 512M), `-` or empty for none")
    zswap = _get(values, "DEV_CACHE_ZSWAP_WRITEBACK", "yes")
    if zswap not in ("yes", "no"):
        raise TreeCacheError(f"DEV_CACHE_ZSWAP_WRITEBACK={zswap!r}: must be yes or no")
    return Plan(
        enabled=True, cache_root=cache_root, cache_size=cache[0], cache_inodes=cache[1],
        upper_root=upper_root if upper else None, upper_size=upper[0] if upper else 0, upper_inodes=upper[1] if upper else 0,
        group=group, gid=gid, cache_low=low, zswap_writeback=zswap,
        gc_idle=_seconds(_get(values, "MDT_TREE_CACHE_GC_IDLE", "6h")),
        upper_gc_idle=_seconds(_get(values, "MDT_TREE_UPPER_GC_IDLE", "1d")),
        gc_interval=_get(values, "MDT_TREE_CACHE_GC_INTERVAL", "15min"),
    )


def capacity_block(plan: Plan) -> dict:
    return {
        "root": plan.cache_root, "slice": SLICE, "size_bytes": plan.cache_size,
        "upper_root": plan.upper_root, "upper_size_bytes": plan.upper_size if plan.upper_root else None,
        "overlay_opts": OVERLAY_OPTS, "group": plan.group, "mode": MODE,
        "gc_idle_seconds": plan.gc_idle,
    }


def merge_capacity_json(path: Path, plan: Plan) -> str:
    """Set/remove only the `tree_cache` key of host-capacity.json (another writer owns the rest); atomic."""
    data: dict = {}
    existed = path.exists()
    if existed:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise TreeCacheError(f"{path} exists but is not readable JSON ({exc}); not touching it") from exc
        if not isinstance(data, dict):
            raise TreeCacheError(f"{path} is not a JSON object; not touching it")
    if plan.enabled:
        data["tree_cache"] = capacity_block(plan)
    elif "tree_cache" in data:
        del data["tree_cache"]
    else:
        return "unchanged"
    if not data and existed:
        path.unlink()
        return "removed (it held only tree_cache)"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)
    return "written"


# ---------------------------------------------------------------------------------------------------- render
def _render(template: Path, subs: dict[str, str]) -> str:
    text = template.read_text(encoding="utf-8")
    for k, v in subs.items():
        text = text.replace(f"@{k}@", v)
    # same semantics as install.sh render(): a directive whose value is empty is dropped
    return "\n".join(line for line in text.split("\n") if not re.fullmatch(r"[A-Za-z][A-Za-z0-9]*=", line))


def planned_units(plan: Plan, units_dir: Path, values: dict[str, str]) -> dict[str, str]:
    """unit file name -> rendered content (empty when disabled)."""
    if not plan.enabled:
        return {}
    out = {
        SLICE: _render(units_dir / "dev-cache.slice.in", {"DEV_CACHE_MEMORY_LOW": plan.cache_low,
                                                           "DEV_CACHE_ZSWAP_WRITEBACK": plan.zswap_writeback}),
        "mdt-tree-cache-gc.service": _render(units_dir / "mdt-tree-cache-gc.service.in", {"TREE_CACHE_ROOT": plan.cache_root}),
        "mdt-tree-cache-gc.timer": _render(units_dir / "mdt-tree-cache-gc.timer.in", {"MDT_TREE_CACHE_GC_INTERVAL": plan.gc_interval}),
    }
    areas = [("cache", plan.cache_root, plan.cache_size, plan.cache_inodes)]
    if plan.upper_root:
        areas.append(("upper", plan.upper_root, plan.upper_size, plan.upper_inodes))
    for kind, root, size, inodes in areas:
        out[mount_unit_name(root)] = _render(units_dir / "mdt-tree-area.mount.in", {
            "TREE_KIND": kind, "TREE_WHERE": root, "TREE_SIZE": str(size), "TREE_INODES": str(inodes), "TREE_GID": str(plan.gid)})
    return out


def _owned(path: Path) -> bool:
    try:
        head = path.read_text(encoding="utf-8")[:600]
    except OSError:
        return False
    return OWN_MARK_SLICE in head or OWN_MARK_MOUNT in head


def owned_unit_files(systemd_dir: Path) -> list[Path]:
    names = [systemd_dir / n for n in (SLICE, "mdt-tree-cache-gc.service", "mdt-tree-cache-gc.timer")]
    names += sorted(systemd_dir.glob("*.mount"))
    return [p for p in names if p.is_file() and _owned(p)]


def _systemctl(*args: str) -> int:
    return subprocess.run(["systemctl", *args], check=False).returncode


def cmd_render(plan: Plan, values: dict[str, str], units_dir: Path, systemd_dir: Path, capacity_json: Path) -> int:
    want = planned_units(plan, units_dir, values)
    # Refuse BEFORE writing anything: a same-named unit that this module does not own is the operator's.
    for name in want:
        target = systemd_dir / name
        if (target.is_symlink() or target.exists()) and (target.is_symlink() or not _owned(target)):
            print(f"ERROR: {target} exists and is not owned by the tree cache (no ownership marker, or a symlink); "
                  "refusing to overwrite it. Rename or remove it, or change MDT_TREE_*_ROOT.", file=sys.stderr)
            return 2
    for path in owned_unit_files(systemd_dir):
        if path.name not in want:
            if path.name.endswith(".timer"):
                _systemctl("disable", "--now", path.name)
            path.unlink()
            print(f"removed owned unit {path} (not planned: role left, area disabled or root key changed); a tmpfs that is still mounted stays until reboot or an operator umount")
    for name, text in want.items():
        (systemd_dir / name).write_text(text if text.endswith("\n") else text + "\n", encoding="utf-8")
        print(f"rendered {systemd_dir / name}")
    print(f"host-capacity.json tree_cache block: {merge_capacity_json(capacity_json, plan)}")
    return 0


def _holds_data(root: str, mountinfo: Path = Path("/proc/self/mountinfo")) -> bool:
    """True when `root` exists, is not a mount point and has content (a tmpfs mounted over it would hide it)."""
    path = Path(root)
    try:
        if not path.is_dir() or any(mp == root for mp, _fs, _dev in mount_table(mountinfo)):
            return False
        return any(path.iterdir())
    except OSError:
        return True


def cmd_activate(plan: Plan, systemd_dir: Path) -> int:
    """After daemon-reload: start the slice, mount the areas (remount when the options changed), enable the timer."""
    if not plan.enabled:
        return 0
    rc = 0
    if _systemctl("start", SLICE) != 0:
        print(f"ERROR: could not start {SLICE}", file=sys.stderr)
        rc = 2
    roots = [plan.cache_root] + ([plan.upper_root] if plan.upper_root else [])
    for root in roots:
        unit = mount_unit_name(root)
        if _systemctl("is-active", "--quiet", unit) != 0 and _holds_data(root):
            print(f"ERROR: {root} is a non-empty directory that is not a mount point; a tmpfs over it would hide its content. Not mounting.", file=sys.stderr)
            rc = 2
            continue
        Path(root).mkdir(parents=True, exist_ok=True)
        if _systemctl("is-active", "--quiet", unit) == 0:
            # an active mount keeps its old options until it is told to remount with the unit's Options=
            if _systemctl("reload", unit) != 0:
                print(f"WARN: {unit} is active but could not be remounted with the new options (size/mode); it keeps the old ones until it is unmounted", file=sys.stderr)
        elif _systemctl("enable", "--now", unit) != 0:
            print(f"ERROR: could not mount {root} ({unit})", file=sys.stderr)
            rc = 2
        else:
            _systemctl("enable", unit)
    if _systemctl("enable", "--now", "mdt-tree-cache-gc.timer") != 0:
        print("ERROR: could not enable mdt-tree-cache-gc.timer", file=sys.stderr)
        rc = 2
    return rc


# ------------------------------------------------------------------------------------------------- mountinfo
def mount_table(mountinfo: Path = Path("/proc/self/mountinfo")) -> list[tuple[str, str, str]]:
    """[(mount point, fstype, major:minor)] from one mountinfo file."""
    out = []
    for line in mountinfo.read_text(encoding="utf-8", errors="replace").splitlines():
        left, _, right = line.partition(" - ")
        f, r = left.split(), right.split()
        if len(f) >= 5 and r:
            out.append((_unescape(f[4]), r[0], f[2]))
    return out


def tmpfs_device(root: str, mountinfo: Path) -> str | None:
    """major:minor of the tmpfs mounted exactly at `root`, else None (the last mount at that point wins)."""
    found = None
    for mp, fs, dev in mount_table(mountinfo):
        if mp == root and fs == "tmpfs":
            found = dev
    return found


def _unescape(s: str) -> str:
    return re.sub(r"\\([0-7]{3})", lambda m: chr(int(m.group(1), 8)), s)




def all_mountinfo_text(proc: Path = Path("/proc")) -> str | None:
    """Concatenated mountinfo of every process, or None when one cannot be read for a reason other than
    the process having exited (a half-read holder table could cause a false 'unused')."""
    chunks: list[str] = []
    try:
        pids = [p for p in os.listdir(proc) if p.isdigit()]
    except OSError:
        return None
    for pid in pids:
        try:
            chunks.append(_read_mountinfo(proc / pid / "mountinfo"))
        except (FileNotFoundError, ProcessLookupError):
            continue
        except OSError as exc:
            # A process that exits while we scan fails in several ways (ESRCH, ENOENT, EINVAL for a zombie, ...).
            # It cannot hold a mount when it is gone (its /proc entry is gone, or it is a zombie/dead). Any other
            # state may hide a holder: retry once, then name the pid and keep everything.
            if _proc_gone(proc, pid, exc):
                continue
            time.sleep(0.05)
            try:
                chunks.append(_read_mountinfo(proc / pid / "mountinfo"))
                continue
            except OSError as exc2:
                if _proc_gone(proc, pid, exc2):
                    continue
                print(f"cannot read {proc}/{pid}/mountinfo: {exc2} (process state {_proc_state(proc, pid) or '?'})", file=sys.stderr)
                return None
    return "\n".join(chunks)


def _read_mountinfo(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _proc_state(proc: Path, pid: str) -> str | None:
    """The one-letter state from /proc/<pid>/stat (Z zombie, X dead, ...); None when the process is gone or unreadable."""
    try:
        text = (proc / pid / "stat").read_text(errors="replace")
    except OSError:
        return None
    tail = text.rpartition(")")[2].split()
    return tail[0] if tail else None


def _proc_gone(proc: Path, pid: str, exc: OSError) -> bool:
    """True only when the process has vanished or is a zombie/dead entry (it holds no mount namespace)."""
    if exc.errno in (errno.ESRCH, errno.ENOENT) or not (proc / pid).exists():
        return True
    return _proc_state(proc, pid) in ("Z", "X", "x")


def path_is_held(root: str, rel: str, dev: str | None, mountinfo_text: str) -> bool:
    """True when `<root>/<rel>` (or something below it) is held by a mount in the given mountinfo text:

    * a BIND of it: same device as the tmpfs at `root` and a mount root field `/<rel>` or below (a bind of a
      sub-directory of a tmpfs shows the path RELATIVE to the filesystem, not `<root>/<rel>`);
    * an overlay (or any option text) naming the absolute path, e.g. `lowerdir=<root>/<rel>/tree`, `upperdir=`,
      `workdir=` (boundary-aware: `k1` does not hold `k10`).
    """
    absolute = f"{root}/{rel}"
    pat = re.compile(r"(?<![A-Za-z0-9._+-])" + re.escape(absolute) + r"(?![A-Za-z0-9._+-])")
    for line in mountinfo_text.splitlines():
        text = _unescape(line)
        f = text.split()
        if dev is not None and len(f) >= 5 and f[2] == dev and (f[3] == "/" + rel or f[3].startswith("/" + rel + "/")):
            return True
        if absolute in text and pat.search(text):
            return True
    return False


# --------------------------------------------------------------------------------------------------- gc
_INVALID_LEASE = -2


def _try_exclusive(path: Path) -> int | None:
    """An fd holding LOCK_EX|LOCK_NB on path (a missing path is unleased: returns -1; a path that is not a
    regular file is an invalid lease: returns -2, never opened), or None when held."""
    try:
        if not stat.S_ISREG(os.lstat(path).st_mode):
            return _INVALID_LEASE  # symlink, FIFO, directory, device: never opened
        fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return -1
    except OSError:  # ELOOP (replaced by a symlink meanwhile), EACCES, ...: an invalid lease, never an abort
        return _INVALID_LEASE
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            os.close(fd)
            return _INVALID_LEASE
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        return None
    return fd


def _release(fd: int | None) -> None:
    if fd is not None and fd >= 0:
        os.close(fd)


def _last_used(key_dir: Path) -> float:
    for name in (".last-used", "COMPLETE"):
        try:
            return (key_dir / name).stat().st_mtime
        except OSError:
            continue
    return key_dir.stat().st_mtime


def _rmtree(path: Path, dry: bool) -> None:
    if not dry:
        shutil.rmtree(path)


def _entries(root: Path, log: list[str]) -> list[os.DirEntry]:
    try:
        return sorted(os.scandir(root), key=lambda e: e.name)
    except OSError as exc:
        log.append(f"ERROR cannot list {root}: {exc}; keeping everything here")
        return []


def gc_cache(root: Path, idle: int, mountinfo_text: str, now: float, dry: bool = False, dev: str | None = None) -> list[str]:
    """One GC pass over the tree cache; returns one log line per decision. One odd or vanishing entry never
    aborts the pass: it is logged and kept."""
    log: list[str] = []
    for entry in _entries(root, log):
        try:
            _gc_cache_entry(root, entry, idle, mountinfo_text, now, dry, dev, log)
        except Exception as exc:  # noqa: BLE001 - per-entry isolation is the point
            log.append(f"ERROR {entry.name}: {type(exc).__name__}: {exc}; kept")
    return log


def _gc_cache_entry(root: Path, entry: os.DirEntry, idle: int, mountinfo_text: str, now: float, dry: bool,
                    dev: str | None, log: list[str]) -> None:
    name, path = entry.name, Path(entry.path)
    if name == ".locks":
        return
    if entry.is_symlink() or not entry.is_dir(follow_symlinks=False):
        log.append(f"skip {name}: not a plain directory")
        return
    if name.startswith(".trash-"):
        _rmtree(path, dry)
        log.append(f"removed leftover {name}")
        return
    if name.startswith("."):
        return
    if ".tmp-" in name:
        key = name.split(".tmp-", 1)[0]
        build_age = now - entry.stat(follow_symlinks=False).st_mtime
        if build_age < STALE_BUILD_MIN_AGE:
            log.append(f"keep {name}: build touched {int(build_age)}s ago (< {STALE_BUILD_MIN_AGE}s)")
            return
        fd = _try_exclusive(root / ".locks" / key)
        if fd is None:
            log.append(f"keep {name}: its builder holds .locks/{key}")
            return
        if fd == _INVALID_LEASE:
            log.append(f"keep {name}: .locks/{key} is not a regular file")
            return
        try:
            if path_is_held(str(root), name, dev, mountinfo_text):
                log.append(f"keep {name}: mounted")
                return
            _rmtree(path, dry)
            log.append(f"removed stale build {name}")
        finally:
            _release(fd)
        return
    if not (path / "COMPLETE").exists():
        log.append(f"keep {name}: no COMPLETE (not a published key; not mine to judge)")
        return
    age = now - _last_used(path)
    if age < idle:
        log.append(f"keep {name}: used {int(age)}s ago (< {idle}s)")
        return
    fd = _try_exclusive(path / ".lease")
    if fd is None:
        log.append(f"keep {name}: leased")
        return
    if fd == _INVALID_LEASE:
        log.append(f"keep {name}: .lease is not a regular file (invalid lease)")
        return
    try:
        if path_is_held(str(root), name, dev, mountinfo_text):
            log.append(f"keep {name}: mounted (bind root or overlay lowerdir)")
            return
        if dry:
            log.append(f"would remove {name}: idle {int(age)}s, unleased, unmounted")
            return
        trash = root / f".trash-{name}.{os.getpid()}"
        os.rename(path, trash)
        shutil.rmtree(trash)
        log.append(f"removed {name}: idle {int(age)}s, unleased, unmounted")
    finally:
        _release(fd)


def gc_upper(root: Path, idle: int, mountinfo_text: str, now: float, dry: bool = False, dev: str | None = None) -> list[str]:
    log: list[str] = []
    for entry in _entries(root, log):
        try:
            path = Path(entry.path)
            if entry.is_symlink() or not entry.is_dir(follow_symlinks=False):
                log.append(f"skip {entry.name}: not a plain directory")
                continue
            age = now - entry.stat(follow_symlinks=False).st_mtime
            if age < idle:
                log.append(f"keep {entry.name}: modified {int(age)}s ago (< {idle}s)")
                continue
            if path_is_held(str(root), entry.name, dev, mountinfo_text):
                log.append(f"keep {entry.name}: named by a live mount (upperdir/workdir)")
                continue
            _rmtree(path, dry)
            log.append(f"{'would remove' if dry else 'removed'} orphan upper {entry.name}: idle {int(age)}s, not mounted")
        except Exception as exc:  # noqa: BLE001
            log.append(f"ERROR {entry.name}: {type(exc).__name__}: {exc}; kept")
    return log


def cmd_gc(plan: Plan, dry: bool, mountinfo: Path = Path("/proc/self/mountinfo"), proc: Path = Path("/proc"), now: float | None = None) -> int:
    if not plan.enabled:
        print("tree cache disabled for this host role/config: nothing to do")
        return 0
    now = time.time() if now is None else now
    text = all_mountinfo_text(proc)
    if text is None:
        print("ERROR: cannot read every process's mountinfo; keeping everything this run", file=sys.stderr)
        return 1
    rc = 0
    areas = [(plan.cache_root, plan.gc_idle, gc_cache)] + ([(plan.upper_root, plan.upper_gc_idle, gc_upper)] if plan.upper_root else [])
    for root, idle, fn in areas:
        dev = tmpfs_device(root, mountinfo)
        if dev is None:
            print(f"ERROR: {root} is not a tmpfs mount point; refusing to delete anything below it", file=sys.stderr)
            rc = 1
            continue
        try:
            lines = fn(Path(root), idle, text, now, dry, dev)
        except Exception as exc:  # noqa: BLE001 - the other area still gets its pass
            print(f"ERROR: GC of {root} failed: {type(exc).__name__}: {exc}", file=sys.stderr)
            rc = 1
            continue
        for line in lines:
            print(f"{root}: {line}")
    return rc


# ------------------------------------------------------------------------------------------------- check
def cmd_check(plan: Plan, cg_root: Path, mountinfo: Path, systemd_dir: Path, capacity_json: Path, systemctl_active) -> int:
    """OK/WARN/FAIL lines for mdt-host-check.sh. Role not dedicated-dev: one INFO line."""
    if not plan.enabled:
        print("INFO tree cache not configured for this host (HOST_ROLE other than dedicated-dev, or MDT_TREE_CACHE_SIZE=-)")
        return 0
    cg = cg_root / "dev.slice" / SLICE
    if cg.is_dir():
        print(f"OK {SLICE} exists (cgroup {cg})")
        low = (cg / "memory.low")
        want = str(cap.parse_size_bytes(plan.cache_low)) if plan.cache_low else "0"
        got = low.read_text().strip() if low.exists() else "?"
        print(f"OK {SLICE} memory.low={got}" if got == want else f"FAIL {SLICE} memory.low={got} but DEV_CACHE_MEMORY_LOW says {want}. Re-run install.sh.")
    else:
        print(f"FAIL {SLICE} has no cgroup under dev.slice (not started). Re-run install.sh; a run-gate helper placed with --cgroup-parent {SLICE} would otherwise fail open into an unlimited transient slice")
    table = {mp: fs for mp, fs, _dev in mount_table(mountinfo)}
    options = _mount_options(mountinfo)
    for kind, root, size, inodes in [("cache", plan.cache_root, plan.cache_size, plan.cache_inodes)] + (
            [("upper", plan.upper_root, plan.upper_size, plan.upper_inodes)] if plan.upper_root else []):
        if table.get(root) != "tmpfs":
            print(f"FAIL tree {kind} area {root} is not a tmpfs mount point ({mount_unit_name(root)}). Re-run install.sh")
            continue
        opt = options.get(root, "")
        want_opts = (f"size={size // 1024}k", f"nr_inodes={inodes}", f"gid={plan.gid}", "mode=2770")
        missing = [w for w in want_opts if w not in opt.split(",")]
        if missing:
            print(f"FAIL tree {kind} area {root} mount options lack {','.join(missing)} (have {opt}). Re-run install.sh (it remounts)")
        else:
            print(f"OK tree {kind} area {root}: tmpfs {opt}")
    if systemctl_active("mdt-tree-cache-gc.timer"):
        print("OK mdt-tree-cache-gc.timer is active")
    else:
        print("FAIL mdt-tree-cache-gc.timer is not active. Re-run install.sh")
    try:
        block = json.loads(capacity_json.read_text())["tree_cache"]
        print(f"OK {capacity_json} tree_cache block present (root {block.get('root')})" if block.get("root") == plan.cache_root
              else f"FAIL {capacity_json} tree_cache.root={block.get('root')} but the env says {plan.cache_root}. Re-run install.sh")
    except (OSError, ValueError, KeyError):
        print(f"FAIL {capacity_json} has no readable tree_cache block. Re-run install.sh")
    return 0


def _mount_options(mountinfo: Path) -> dict[str, str]:
    out = {}
    for line in mountinfo.read_text(encoding="utf-8", errors="replace").splitlines():
        left, _, right = line.partition(" - ")
        f, r = left.split(), right.split()
        if len(f) >= 5 and len(r) >= 3:
            out[_unescape(f[4])] = r[2]
    return out


# --------------------------------------------------------------------------------------------------- cli
def _load(args: argparse.Namespace) -> tuple[dict[str, str], Plan]:
    values = cap._parse_env(args.config)
    mem_total, _swap = cap._meminfo_bytes(args.meminfo)
    role = args.role or _get(values, "HOST_ROLE")
    try:
        plan = make_plan(values, mem_total=mem_total, dedicated=role == "dedicated-dev",
                         gid_lookup=(lambda _n: args.gid) if args.gid is not None else (lambda n: grp.getgrnam(n).gr_gid))
    except TreeCacheError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(2)
    return values, plan


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("cmd", choices=["plan", "render", "activate", "gc", "check"])
    p.add_argument("--config", type=Path, default=Path("/etc/mdt/host-setup.env"))
    p.add_argument("--meminfo", type=Path, default=Path("/proc/meminfo"))
    p.add_argument("--role", default=None, help="override HOST_ROLE (the installer passes its own detection)")
    p.add_argument("--gid", type=int, default=None, help="override the group lookup (tests)")
    p.add_argument("--units-dir", type=Path, default=Path(__file__).resolve().parent / "units")
    p.add_argument("--systemd-dir", type=Path, default=SYSTEMD_DIR)
    p.add_argument("--capacity-json", type=Path, default=CAPACITY_JSON)
    p.add_argument("--cgroup-root", type=Path, default=Path("/sys/fs/cgroup"))
    p.add_argument("--mountinfo", type=Path, default=Path("/proc/self/mountinfo"))
    p.add_argument("--proc", type=Path, default=Path("/proc"))
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args(argv)
    values, plan = _load(args)
    if args.cmd == "plan":
        print(f"TREE_CACHE_ENABLED={1 if plan.enabled else 0}")
        if plan.enabled:
            for k, v in (("CACHE_ROOT", plan.cache_root), ("CACHE_SIZE", plan.cache_size), ("CACHE_INODES", plan.cache_inodes),
                         ("UPPER_ROOT", plan.upper_root or ""), ("UPPER_SIZE", plan.upper_size), ("UPPER_INODES", plan.upper_inodes),
                         ("GROUP", plan.group), ("GID", plan.gid), ("CACHE_LOW", plan.cache_low), ("GC_IDLE", plan.gc_idle)):
                print(f"TREE_{k}={v}")
        return 0
    if args.cmd == "render":
        return cmd_render(plan, values, args.units_dir, args.systemd_dir, args.capacity_json)
    if args.cmd == "activate":
        return cmd_activate(plan, args.systemd_dir)
    if args.cmd == "gc":
        return cmd_gc(plan, args.dry_run, args.mountinfo, args.proc)
    return cmd_check(plan, args.cgroup_root, args.mountinfo, args.systemd_dir, args.capacity_json,
                     lambda unit: subprocess.run(["systemctl", "is-active", "--quiet", unit], check=False).returncode == 0)


if __name__ == "__main__":
    raise SystemExit(main())
