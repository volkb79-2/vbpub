"""Standalone provisioning of per-worker trees with automatic, reported degradation levels.

Nothing here reads the environment or knows any host policy: the caller passes plain paths in ``Settings``
(an adapter such as ``cowtree.adapters`` may translate host policy into them). Every level gives each worker a
private, writable directory whose initial content equals the published tree (the same manifest digest), so
verdict-relevant semantics do not depend on the level.

======  ==============================================  ===========================================
Level   Mechanism                                       Needs
======  ==============================================  ===========================================
A       shared read-only tree + per-worker overlay      ``shared_root`` + ``upper_root`` (writable
                                                        dirs) and a docker socket or the ``mount``
                                                        capability
B       per-worker copy inside a tmpfs                  ``tmpfs_dir`` on a tmpfs (e.g. /dev/shm)
C       reflink clone                                   ``clone_dir`` on a reflink-capable fs
D       plain copy                                      ``copy_dir`` (any writable dir)
======  ==============================================  ===========================================
"""
from __future__ import annotations

import fcntl
import os
import shutil
import stat
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from .cache import Cache, Lease, Published, _checked_root, tree_manifest
from .errors import BackendFailed, CowError, PathRefused, RootInvalid
from .overlay import (
    OverlayVolume,
    Runner,
    create_worker,
    remove_token,
    remove_worker,
    run_argv,
    worker_dirs,
    worker_volume,
)
from .paths import mount_fstype

LEVELS = ("A", "B", "C", "D")
_FICLONE = 0x40049409
_CAP_SYS_ADMIN = 21


class LevelUnavailable(CowError):
    """No usable level (or the pinned level is unusable); the report says why for every level."""

    code = "level-unavailable"


@dataclass(frozen=True)
class Settings:
    """Plain configuration. ``level`` is ``auto`` or a pinned ``A``-``D`` (never silently degraded)."""

    level: str = "auto"
    shared_root: str | None = None
    upper_root: str | None = None
    overlay_via: str = "docker"
    docker_socket: str | None = None
    tmpfs_dir: str | None = None
    clone_dir: str | None = None
    copy_dir: str | None = None


def _read_mountinfo() -> str:
    with open("/proc/self/mountinfo", encoding="utf-8") as handle:
        return handle.read()


def _socket_usable(path: str) -> bool:
    try:
        return stat.S_ISSOCK(os.stat(path).st_mode) and os.access(path, os.R_OK | os.W_OK)
    except OSError:
        return False


def _can_mount() -> bool:
    with open("/proc/self/status", encoding="ascii") as handle:
        for line in handle:
            if line.startswith("CapEff:"):
                return bool(int(line.split()[1], 16) >> _CAP_SYS_ADMIN & 1)
    return False


def _writable_dir(path: str) -> bool:
    return os.path.isdir(path) and os.access(path, os.R_OK | os.W_OK | os.X_OK)


def _is_tmpfs(path: str, mountinfo: Callable[[], str]) -> bool:
    return mount_fstype(os.path.realpath(path), mountinfo()) in ("tmpfs", "ramfs")


def _clone_file(source: str, destination: str) -> None:
    try:
        with open(source, "rb") as src, open(destination, "wb") as dst:
            fcntl.ioctl(dst.fileno(), _FICLONE, src.fileno())
    except OSError as exc:
        raise BackendFailed("reflink", exc.errno, destination, str(exc)) from exc
    shutil.copystat(source, destination)


def _reflink_supported(directory: str) -> bool:
    with tempfile.TemporaryDirectory(dir=directory, prefix=".cowtree-probe-") as scratch:
        source = os.path.join(scratch, "a")
        with open(source, "wb") as handle:
            handle.write(b"probe")
        try:
            _clone_file(source, os.path.join(scratch, "b"))
        except BackendFailed:
            return False
    return True


@dataclass(frozen=True)
class Probes:
    """Capability probes; replace any of them in tests or to force a policy."""

    socket_usable: Callable[[str], bool] = _socket_usable
    can_mount: Callable[[], bool] = _can_mount
    writable_dir: Callable[[str], bool] = _writable_dir
    is_tmpfs: Callable[[str], bool] = lambda path: _is_tmpfs(path, _read_mountinfo)
    reflink: Callable[[str], bool] = _reflink_supported


@dataclass(frozen=True)
class Decision:
    level: str
    requested: str
    checks: dict[str, str]

    def report(self) -> dict[str, Any]:
        """The probe result and the chosen level, for logs: ``checks`` maps each level to ``ok`` or its reason."""
        return {"level": self.level, "requested": self.requested, "checks": dict(self.checks)}


def _check_a(settings: Settings, probes: Probes) -> str:
    if not settings.shared_root or not settings.upper_root:
        return "shared_root and upper_root are not configured"
    for label, path in (("shared_root", settings.shared_root), ("upper_root", settings.upper_root)):
        if not probes.writable_dir(path):
            return f"{label} is not a writable directory"
    if settings.overlay_via == "docker":
        if not settings.docker_socket:
            return "docker_socket is not configured"
        return "ok" if probes.socket_usable(settings.docker_socket) else "docker socket is not usable"
    if settings.overlay_via == "mount":
        return "ok" if probes.can_mount() else "mount capability (CAP_SYS_ADMIN) is missing"
    return "overlay_via must be 'docker' or 'mount'"


def _check_b(settings: Settings, probes: Probes) -> str:
    if not settings.tmpfs_dir:
        return "tmpfs_dir is not configured"
    if not probes.writable_dir(settings.tmpfs_dir):
        return "tmpfs_dir is not a writable directory"
    return "ok" if probes.is_tmpfs(settings.tmpfs_dir) else "tmpfs_dir is not on a tmpfs"


def _check_c(settings: Settings, probes: Probes) -> str:
    if not settings.clone_dir:
        return "clone_dir is not configured"
    if not probes.writable_dir(settings.clone_dir):
        return "clone_dir is not a writable directory"
    return "ok" if probes.reflink(settings.clone_dir) else "filesystem does not support reflink"


def _check_d(settings: Settings, probes: Probes) -> str:
    if not settings.copy_dir:
        return "copy_dir is not configured"
    return "ok" if probes.writable_dir(settings.copy_dir) else "copy_dir is not a writable directory"


_CHECKS = {"A": _check_a, "B": _check_b, "C": _check_c, "D": _check_d}


def decide(settings: Settings, probes: Probes = Probes()) -> Decision:
    """Probe the capabilities and choose a level. ``auto`` picks the best usable one (A, B, C, D); a pinned level
    is used or refused, never silently replaced. The decision carries every level's probe result."""
    if settings.level != "auto" and settings.level not in LEVELS:
        raise PathRefused("level must be 'auto' or one of A, B, C, D")
    wanted = LEVELS if settings.level == "auto" else (settings.level,)
    checks = {level: _CHECKS[level](settings, probes) for level in wanted}
    for level in wanted:
        if checks[level] == "ok":
            return Decision(level, settings.level, checks)
    raise LevelUnavailable(f"no usable level ({settings.level}): " + "; ".join(f"{k}: {v}" for k, v in checks.items()))


@dataclass
class WorkerSlot:
    """One worker's private tree. B-D: use ``path``. A: ``mount_argv(dst)`` (docker) or ``path`` (mount)."""

    level: str
    token: str
    index: int
    key: str
    path: str | None
    volume: OverlayVolume | None
    docker: list[str] = field(default_factory=list)

    def mount_argv(self, destination: str) -> list[str]:
        """The ``docker run`` argument that mounts this slot's overlay volume (level A, docker mode only)."""
        if self.volume is None or self.path is not None:
            raise PathRefused("this slot is not a docker overlay volume; use .path")
        return self.volume.mount_argv(destination)


class Provisioner:
    """publish / lease / worker (mount spec) / release / cleanup behind one object, whatever the level."""

    def __init__(
        self,
        settings: Settings,
        *,
        probes: Probes = Probes(),
        runner: Runner = run_argv,
        cleanup_image: str | None = None,
        mountinfo: Callable[[], str] = _read_mountinfo,
        volume_prefix: str = "cowtree-slot",
    ) -> None:
        self.settings = settings
        self.decision = decide(settings, probes)
        self.level = self.decision.level
        self._runner = runner
        self._image = cleanup_image
        self._prefix = volume_prefix
        self._slots: dict[tuple[str, int], WorkerSlot] = {}
        if self.level == "A":
            self._work_root = _checked_root(settings.upper_root, "upper_root")
            cache_root = settings.shared_root
        else:
            base = _checked_root(
                {"B": settings.tmpfs_dir, "C": settings.clone_dir, "D": settings.copy_dir}[self.level],
                "work dir",
            )
            cache_root = os.path.join(base, "cowtree-trees")
            self._work_root = os.path.join(base, "cowtree-workers")
            for directory in (cache_root, self._work_root):
                os.makedirs(directory, mode=0o770, exist_ok=True)
        self.cache = Cache(cache_root, mountinfo=mountinfo)
        self._docker = ["docker", *(["--host", f"unix://{settings.docker_socket}"] if settings.docker_socket else [])]

    # -- publish / lease
    def publish(self, key: str, build: Callable[[str], None], *, owner: str | None = None, seal: bool = False) -> Published:
        return self.cache.ensure(key, build, owner=owner, seal=seal)

    def lease(self, key: str, owner: str, *, pid: int | None = None) -> Lease:
        return self.cache.lease(key, owner, pid=pid)

    # -- workers
    def worker(self, token: str, index: int, key: str, *, target: str | None = None) -> WorkerSlot:
        """A private writable tree for one worker. ``target`` is the mount point for level A in ``mount`` mode."""
        if self.level == "A":
            dirs = create_worker(self._work_root, token, index, self.cache, key)
            volume = worker_volume(dirs, self.cache, key, f"{self._prefix}-{token}-w{index}")
            path = None
            if self.settings.overlay_via == "mount":
                if target is None:
                    raise PathRefused("level A in mount mode needs a target directory")
                self._run(volume.mount_command(target))
                path = target
            slot = WorkerSlot("A", token, index, key, path, volume, self._docker)
        else:
            lower = self.cache.require_complete(key)
            dirs = worker_dirs(self._work_root, token, index)
            os.makedirs(os.path.dirname(dirs.base), mode=0o770, exist_ok=True)
            try:
                os.mkdir(dirs.base, 0o770)
            except FileExistsError as exc:
                raise PathRefused(f"worker slot already exists: {dirs.base!r}") from exc
            path = os.path.join(dirs.base, "tree")
            copy = _clone_file if self.level == "C" else shutil.copy2
            shutil.copytree(lower, path, symlinks=True, copy_function=copy)
            slot = WorkerSlot(self.level, token, index, key, path, None)
        self._slots[(token, index)] = slot
        return slot

    def verify_worker(self, slot: WorkerSlot) -> bool:
        """True if the slot's visible tree still equals the published manifest (identical across levels)."""
        document = self.cache.verify(slot.key, deep=False)
        return slot.path is not None and tree_manifest(slot.path)["digest"] == document["digest"]

    def release_worker(self, slot: WorkerSlot) -> None:
        """Release one worker: unmount/remove the volume (level A) and remove its directories by exact path."""
        self._slots.pop((slot.token, slot.index), None)
        if slot.level == "A":
            if self.settings.overlay_via == "mount":
                self._run(["umount", slot.path])
            else:
                self._run(slot.volume.remove_argv(self._docker, force=True))
        remove_worker(self._work_root, slot.token, slot.index, runner=self._runner, image=self._image, docker=self._docker)

    def cleanup(self, token: str) -> None:
        """Release every slot of a run token, then remove the token directory."""
        for slot in [s for (t, _i), s in self._slots.items() if t == token]:
            self.release_worker(slot)
        remove_token(self._work_root, token, runner=self._runner, image=self._image, docker=self._docker)

    def gc(self, idle_seconds: float) -> list:
        return self.cache.gc(idle_seconds)

    def _run(self, argv: Sequence[str]) -> None:
        status = self._runner(argv)
        if status != 0:
            raise BackendFailed("run", None, None, f"{argv[0]} exited {status}")
