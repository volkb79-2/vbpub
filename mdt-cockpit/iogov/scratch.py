"""The adaptive scratch region: the ONLY place this tool ever writes (a file under scratch_dir, never a device).

Sizing: region = min(region_default, disk_fraction * disk, free_fraction * (free - reserve), hard cap), rounded down to
MiB; below `region_min` there is no write region at all (mode "none": write lanes disabled, reported as such).
The file lives for one bench run only: created O_EXCL, fully written with random non-zero data by the bench itself,
removed in `finally` (also on SIGTERM/abort), and stale files from a killed run are removed under an flock.
"""

from __future__ import annotations

import fcntl
import os
import re
from dataclasses import dataclass

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
    try:
        with open(mountinfo, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
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
    except OSError:
        pass
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


def _lock(scratch_dir: str) -> int:
    os.makedirs(scratch_dir, mode=0o700, exist_ok=True)
    fd = os.open(os.path.join(scratch_dir, LOCK_NAME), os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        raise ScratchBusy("another iogov run holds the scratch lock")
    return fd


def remove_stale(scratch_dir: str) -> list[str]:
    """Remove leftovers of a killed run. Only files with our prefix, only while holding the lock."""

    removed = []
    if not os.path.isdir(scratch_dir):
        return removed
    fd = _lock(scratch_dir)
    try:
        for name in sorted(os.listdir(scratch_dir)):
            if re.fullmatch(re.escape(PREFIX) + r"[0-9a-f]+\.dat", name):
                os.unlink(os.path.join(scratch_dir, name))
                removed.append(name)
    finally:
        os.close(fd)
    return removed


class Scratch:
    """Context manager owning the lock and the file. `fd` (O_DIRECT) and `fd_dsync` (O_DIRECT|O_DSYNC) are both read/write."""

    def __init__(self, cfg: Config, env: Env, size: int):
        self.cfg, self.env, self.size = cfg, env, size
        self.path = ""
        self.fd = -1
        self.fd_dsync = -1
        self.lock_fd = -1
        self.direct = False
        self.removed_stale: list[str] = []

    def __enter__(self) -> "Scratch":
        d = self.cfg.scratch_dir
        self.lock_fd = _lock(d)
        try:
            for name in os.listdir(d):
                if name.startswith(PREFIX):
                    os.unlink(os.path.join(d, name))
                    self.removed_stale.append(name)
            self.path = os.path.join(d, PREFIX + self.env.urandom(6).hex() + ".dat")
            base = os.O_RDWR | os.O_CLOEXEC
            direct = getattr(os, "O_DIRECT", 0)
            try:
                self.fd = os.open(self.path, base | os.O_CREAT | os.O_EXCL | direct, 0o600)
                self.direct = bool(direct)
            except OSError as exc:
                if exc.errno != 22:  # EINVAL: filesystem without O_DIRECT; fall back and say so
                    raise
                self.fd = os.open(self.path, base | os.O_CREAT | os.O_EXCL, 0o600)
                self.direct = False
            self.fd_dsync = os.open(self.path, base | os.O_DSYNC | (direct if self.direct else 0))
        except BaseException:
            self._cleanup()
            raise
        return self

    def _cleanup(self) -> None:
        for attr in ("fd", "fd_dsync"):
            fd = getattr(self, attr)
            if fd >= 0:
                try:
                    os.close(fd)
                except OSError:
                    pass
                setattr(self, attr, -1)
        if self.path:
            try:
                os.unlink(self.path)
            except FileNotFoundError:
                pass
            self.path_removed = True
        if self.lock_fd >= 0:
            os.close(self.lock_fd)
            self.lock_fd = -1

    def __exit__(self, *exc) -> None:
        self._cleanup()
