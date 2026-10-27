"""The adaptive scratch region: the ONLY place this tool ever writes (a file under scratch_dir, never a device).

Sizing: region = min(region_default, disk_fraction * disk, free_fraction * (free - reserve), hard cap), rounded down to
MiB; below `region_min` there is no write region at all (mode "none": write lanes disabled, reported as such).
The file lives for one bench run only: created O_EXCL, fully written with random non-zero data by the bench itself,
removed in `finally` (also on SIGTERM/abort), and stale files from a killed run are removed under an flock.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

from . import safeio
from .config import MiB, Config
from .env import Env

PREFIX = "iogov-scratch-"
LOCK_NAME = "iogov-scratch.lock"


class ScratchBusy(Exception):
    pass


@dataclass
class RegionPlan:
    mode: str  # "file" | "none"
    region_bytes: int = 0
    probe_bytes: int = 0
    escalation_bytes: int = 0
    reason: str = ""
    free_bytes: int = 0
    fs_bytes: int = 0
    fstype: str = "?"

    def as_dict(self) -> dict:
        return dict(self.__dict__)


def _floor(n: int, unit: int) -> int:
    return (n // unit) * unit


def fstype_of(path: str, mountinfo: str = "/proc/self/mountinfo") -> str:
    """Filesystem type of the longest mount point that is a prefix of `path`."""

    best, best_len = "?", -1
    real = os.path.realpath(path)
    for line in (safeio.read_text(mountinfo) or "").splitlines():
        left, sep, right = line.partition(" - ")
        if not sep:
            continue
        fields = left.split()
        if len(fields) < 5:
            continue
        mp = fields[4].replace("\\040", " ")
        if real == mp or real.startswith(mp.rstrip("/") + "/") or mp == "/":
            if len(mp) > best_len:
                best, best_len = right.split()[0], len(mp)
    return best


def plan_region(cfg: Config, disk_bytes: int | None, statvfs=os.statvfs, fstype: str | None = None) -> RegionPlan:
    if cfg.region_default == 0:
        return RegionPlan("none", reason="write region disabled by config (region_default = 0)")
    scratch = cfg.scratch_dir
    probe_path = scratch
    while probe_path != "/" and not os.path.isdir(probe_path):  # the dir is created on first run: size against its ancestor
        probe_path = os.path.dirname(probe_path.rstrip("/")) or "/"
    try:
        sv = statvfs(probe_path)
    except OSError as exc:
        return RegionPlan("none", reason=f"scratch filesystem unreadable: {exc.strerror or exc}")
    fs_bytes = sv.f_blocks * sv.f_frsize
    free = sv.f_bavail * sv.f_frsize
    ft = fstype if fstype is not None else fstype_of(probe_path)
    base = dict(free_bytes=free, fs_bytes=fs_bytes, fstype=ft)
    if cfg.scratch_fs_allowed and ft not in cfg.scratch_fs_allowed:
        return RegionPlan("none", reason=f"filesystem type {ft!r} not in scratch_fs_allowed", **base)
    reserve = max(cfg.free_reserve_bytes, int(cfg.free_reserve_fraction * fs_bytes))
    usable = free - reserve
    if usable <= 0:
        return RegionPlan("none", reason=f"free space {free} is below the reserve {reserve}", **base)
    limits = [cfg.region_default, cfg.region_hard_cap, int(cfg.region_free_fraction * usable)]
    if disk_bytes:
        limits.append(int(cfg.region_disk_fraction * disk_bytes))
    region = _floor(min(limits), MiB)
    if region < cfg.region_min:
        return RegionPlan("none", reason=f"adaptive region {region} is below region_min {cfg.region_min}", **base)
    unit = cfg.block_bytes * 16
    probe = _floor(min(cfg.probe_bytes_cap, region // 8), unit)
    if probe < unit:
        return RegionPlan("none", reason="region too small for the probe ladder", **base)
    esc = max(probe, _floor(min(cfg.escalation_bytes_cap, region * 3 // 8), unit))
    return RegionPlan("file", region, probe, esc, "ok", **base)


STALE_RE = re.compile(re.escape(PREFIX) + r"[0-9a-f]{12}\.dat")


def _lock(scratch_dir: str) -> safeio.FileLock:
    safeio.ensure_dir(scratch_dir, strict=True)
    lock = safeio.FileLock(os.path.join(scratch_dir, LOCK_NAME))
    if not lock.acquire():
        raise ScratchBusy("another iogov run holds the scratch lock")
    return lock


def _remove_stale_files(scratch_dir: str) -> list[str]:
    """Remove only regular files whose name matches the strict pattern; anything else with the prefix (a directory, a link) is left."""

    removed = []
    with os.scandir(scratch_dir) as it:
        entries = sorted(it, key=lambda e: e.name)
    for e in entries:
        if STALE_RE.fullmatch(e.name) and e.is_file(follow_symlinks=False):
            os.unlink(e.path)
            removed.append(e.name)
    return removed


def remove_stale(scratch_dir: str) -> list[str]:
    """Remove leftovers of a killed run, while holding the scratch lock."""

    if not os.path.isdir(scratch_dir):
        return []
    lock = _lock(scratch_dir)
    try:
        return _remove_stale_files(scratch_dir)
    finally:
        lock.release()


class Scratch:
    """Context manager owning the lock and the file, used through ONE descriptor (never re-opened by name).

    The file is O_EXCL|O_NOFOLLOW created and verified (regular, ours, 0600, the name still names it); durability of the 4 KiB lane
    comes from an explicit fdatasync after each pwrite."""

    def __init__(self, cfg: Config, env: Env, size: int):
        self.cfg, self.env, self.size = cfg, env, size
        self.path = ""
        self.fd = -1
        self.lock: safeio.FileLock | None = None
        self.direct = False
        self.removed_stale: list[str] = []

    def __enter__(self) -> "Scratch":
        d = self.cfg.scratch_dir
        self.lock = _lock(d)
        try:
            self.removed_stale = _remove_stale_files(d)
            self.path = os.path.join(d, PREFIX + self.env.urandom(6).hex() + ".dat")
            direct = getattr(os, "O_DIRECT", 0)
            base = os.O_RDWR | os.O_CREAT | os.O_EXCL
            try:
                self.fd = safeio.open_file(self.path, base | direct, 0o600)
                self.direct = bool(direct)
            except OSError as exc:
                if exc.errno != 22 or not direct:
                    raise
                # EINVAL: the filesystem has no O_DIRECT (tmpfs). Some of them create the file before refusing: remove OUR random
                # name and create it again, buffered, and say so in the journal (`direct` false)
                try:
                    os.unlink(self.path)
                except FileNotFoundError:
                    pass
                self.fd = safeio.open_file(self.path, base, 0o600)
                self.direct = False
        except BaseException:
            self._cleanup()
            raise
        return self

    def _cleanup(self) -> None:
        if self.fd >= 0:
            if self.path:
                try:
                    safeio.unlink_if_same(self.path, self.fd)
                except OSError:
                    pass
            try:
                os.close(self.fd)
            except OSError:
                pass
            self.fd = -1
        elif self.path:
            try:
                os.unlink(self.path)
            except FileNotFoundError:
                pass
            except OSError:
                pass
        if self.lock is not None:
            self.lock.release()
            self.lock = None

    def __exit__(self, *exc) -> None:
        self._cleanup()
