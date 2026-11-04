"""Per-worker overlay slots over a published tree: mount specs, upper/work directories, repair and cleanup.

cowtree never mounts anything. It builds the argv/options a dockerd ``local`` overlay volume needs (the measured
M-2 recipe) and manages the host-side upper and work directories. Hazards handled here:

* ``redirect_dir=on`` needs a root mount (under ``userxattr`` it is forced to ``nofollow`` and a directory
  rename returns EXDEV), so ``userxattr`` is never offered. ``xino=auto`` keeps ``st_dev`` uniform.
* The upper directory's own mode and owner become the merged root's, so the upper is created with the lower
  root's mode (and owner, when they differ and the caller may chown).
* An upper holds redirects and origin references bound to one lower. Reusing it under a different lower is
  undefined, so a marker records the key and ``reuse_worker`` refuses a mismatch.
* The lower must not change while mounted: only a published (COMPLETE) key tree is accepted as a lower.
* The overlay work directory ends up root-owned; removal falls back to a helper container (the verified
  recipe) when the unprivileged ``rmtree`` is refused.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from .cache import Cache, _force_rmtree
from .errors import BackendFailed, PathRefused, Unrepairable
from .paths import normalized_absolute, validate_name

MARKER_FORMAT = "cowtree-worker/1"
_SAFE_PATH = re.compile(r"/[A-Za-z0-9_./@+=-]*\Z")
_VOLUME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
_REDIRECT = ("on", "off", "follow", "nofollow")
_XINO = ("on", "off", "auto")

Runner = Callable[[Sequence[str]], int]


def run_argv(argv: Sequence[str]) -> int:
    """The default runner: execute *argv* and return its exit status."""
    return subprocess.run(list(argv), check=False).returncode


def _prefix(docker: str | Sequence[str]) -> list[str]:
    return [docker] if isinstance(docker, str) else list(docker)


def _safe_path(path: str, label: str) -> str:
    if not normalized_absolute(path) or _SAFE_PATH.fullmatch(path) is None:
        raise PathRefused(f"{label} must be an absolute normalized path of [A-Za-z0-9_./@+=-]: {path!r}")
    return path


@dataclass(frozen=True)
class OverlayVolume:
    name: str
    lowerdir: str
    upperdir: str
    workdir: str
    redirect_dir: str = "on"
    xino: str = "auto"

    @property
    def options(self) -> str:
        return (
            f"lowerdir={self.lowerdir},upperdir={self.upperdir},workdir={self.workdir},"
            f"redirect_dir={self.redirect_dir},xino={self.xino}"
        )

    def create_argv(self, docker: str | Sequence[str] = "docker") -> list[str]:
        """``docker volume create`` form (create once, mount by name). *docker* may be a prefix such as
        ``["docker", "--host", "unix:///run/docker.sock"]``."""
        return [
            *_prefix(docker), "volume", "create", "--driver", "local", "--opt", "type=overlay", "--opt", "device=overlay",
            "--opt", f"o={self.options}", self.name,
        ]

    def remove_argv(self, docker: str | Sequence[str] = "docker", *, force: bool = False) -> list[str]:
        return [*_prefix(docker), "volume", "rm", *(["-f"] if force else []), self.name]

    def mount_command(self, target: str, mount: str = "mount") -> list[str]:
        """The plain-``mount`` form of the same overlay (needs CAP_SYS_ADMIN)."""
        return [mount, "-t", "overlay", "overlay", "-o", self.options, _safe_path(target, "target")]

    def mount_arg(self, destination: str) -> str:
        """The ``--mount`` CSV value; the comma-bearing ``o=`` field is quoted per CSV rules."""
        _safe_path(destination, "destination")
        return (
            f"type=volume,src={self.name},dst={destination},volume-driver=local,"
            f"volume-opt=type=overlay,volume-opt=device=overlay,\"volume-opt=o={self.options}\""
        )

    def mount_argv(self, destination: str) -> list[str]:
        return ["--mount", self.mount_arg(destination)]


def overlay_volume(
    name: str,
    lowerdir: str,
    upperdir: str,
    workdir: str,
    *,
    redirect_dir: str = "on",
    xino: str = "auto",
) -> OverlayVolume:
    """Validate every field; refuse anything that could smuggle an extra mount option or CSV field."""
    if _VOLUME.fullmatch(name) is None:
        raise PathRefused("volume name must match [A-Za-z0-9][A-Za-z0-9_.-]{0,127}")
    if redirect_dir not in _REDIRECT:
        raise PathRefused(f"redirect_dir must be one of {_REDIRECT}")
    if xino not in _XINO:
        raise PathRefused(f"xino must be one of {_XINO}")
    return OverlayVolume(
        name, _safe_path(lowerdir, "lowerdir"), _safe_path(upperdir, "upperdir"), _safe_path(workdir, "workdir"),
        redirect_dir, xino,
    )


@dataclass(frozen=True)
class WorkerDirs:
    base: str
    upper: str
    work: str
    marker: str


def worker_dirs(upper_root: str, token: str, index: int) -> WorkerDirs:
    if not isinstance(index, int) or isinstance(index, bool) or index < 0:
        raise PathRefused("worker index must be a non-negative integer")
    base = os.path.join(upper_root, validate_name(token), f"w{index}")
    return WorkerDirs(base, os.path.join(base, "u"), os.path.join(base, "w"), os.path.join(base, "lower.json"))


def _match_owner(path: str, reference: os.stat_result, chown: Callable[[str, int, int], None]) -> None:
    current = os.stat(path)
    if (current.st_uid, current.st_gid) != (reference.st_uid, reference.st_gid):
        try:
            chown(path, reference.st_uid, reference.st_gid)
        except OSError as exc:
            raise BackendFailed("chown", exc.errno, path, str(exc)) from exc


def create_worker(
    upper_root: str,
    token: str,
    index: int,
    cache: Cache,
    key: str,
    *,
    chown: Callable[[str, int, int], None] = os.chown,
) -> WorkerDirs:
    """Create ``<upper_root>/<token>/w<index>/{u,w}`` for a published key. The base must not exist (a fresh slot
    per run; use ``reuse_worker`` or ``reset_worker`` for an existing one)."""
    lower = cache.require_complete(key)
    dirs = worker_dirs(upper_root, token, index)
    os.makedirs(os.path.dirname(dirs.base), mode=0o770, exist_ok=True)
    try:
        os.mkdir(dirs.base, 0o770)
    except FileExistsError as exc:
        raise PathRefused(f"worker slot already exists: {dirs.base!r}") from exc
    reference = os.stat(lower)
    os.mkdir(dirs.upper, 0o700)
    os.chmod(dirs.upper, stat.S_IMODE(reference.st_mode))
    _match_owner(dirs.upper, reference, chown)
    os.mkdir(dirs.work, 0o700)
    with open(dirs.marker, "w", encoding="utf-8") as handle:
        json.dump({"format": MARKER_FORMAT, "key": key, "lowerdir": lower}, handle, sort_keys=True)
    return dirs


def reuse_worker(upper_root: str, token: str, index: int, key: str) -> WorkerDirs:
    """Return an existing slot only if it was created for exactly this key's lower."""
    dirs = worker_dirs(upper_root, token, index)
    try:
        with open(dirs.marker, encoding="utf-8") as handle:
            document = json.load(handle)
    except (OSError, ValueError) as exc:
        raise PathRefused(f"worker slot has no readable marker: {dirs.base!r}") from exc
    if not isinstance(document, dict) or document.get("format") != MARKER_FORMAT or document.get("key") != key:
        raise PathRefused("worker upper belongs to a different lower; reset it instead of reusing it")
    return dirs


def worker_volume(dirs: WorkerDirs, cache: Cache, key: str, name: str, **options: str) -> OverlayVolume:
    """The volume spec for a slot; the lower is always the published key's tree."""
    return overlay_volume(name, cache.require_complete(key), dirs.upper, dirs.work, **options)


def _cleanup_argv(base: str, image: str, docker: str | Sequence[str] = "docker") -> list[str]:
    _safe_path(base, "worker base")
    return [*_prefix(docker), "run", "--rm", "--mount", f"type=bind,src={base},dst=/x", image, "rm", "-rf", "/x/u", "/x/w"]


def remove_worker(
    upper_root: str,
    token: str,
    index: int,
    *,
    runner: Runner = run_argv,
    image: str | None = None,
    docker: str | Sequence[str] = "docker",
) -> None:
    """Remove one slot by exact path. The volume must already be removed (a mounted upper must not change).
    When the unprivileged removal is refused (root-owned overlay work dir), the helper container of the verified
    recipe is run once through *runner* and the removal retried."""
    dirs = worker_dirs(upper_root, token, index)
    if not os.path.lexists(dirs.base):
        return
    if os.path.islink(dirs.base):
        raise PathRefused(f"refusing to remove symlink {dirs.base!r}")
    try:
        _force_rmtree(dirs.base)
        return
    except PermissionError as exc:
        if image is None:
            raise BackendFailed("remove-worker", exc.errno, dirs.base, "permission denied and no cleanup image") from exc
    status = runner(_cleanup_argv(dirs.base, image, docker))
    if status != 0:
        raise BackendFailed("remove-worker", None, dirs.base, f"cleanup helper exited {status}")
    try:
        _force_rmtree(dirs.base)
    except OSError as exc:
        raise BackendFailed("remove-worker", exc.errno, dirs.base, str(exc)) from exc


def remove_token(
    upper_root: str,
    token: str,
    *,
    runner: Runner = run_argv,
    image: str | None = None,
    docker: str | Sequence[str] = "docker",
) -> None:
    """Remove every slot of one run token, then the token directory itself."""
    base = os.path.join(upper_root, validate_name(token))
    if not os.path.isdir(base) or os.path.islink(base):
        return
    for name in sorted(os.listdir(base)):
        match = re.fullmatch(r"w([0-9]+)", name)
        if match:
            remove_worker(upper_root, token, int(match.group(1)), runner=runner, image=image, docker=docker)
    try:
        os.rmdir(base)
    except OSError as exc:
        raise BackendFailed("remove-token", exc.errno, base, "token directory not empty") from exc


def reset_worker(
    upper_root: str,
    token: str,
    index: int,
    cache: Cache,
    key: str,
    *,
    runner: Runner = run_argv,
    image: str | None = None,
    chown: Callable[[str, int, int], None] = os.chown,
) -> WorkerDirs:
    """Repair a bad upper wholesale: remove the slot and recreate it pristine from the lower. The caller must
    have removed the volume first."""
    remove_worker(upper_root, token, index, runner=runner, image=image)
    return create_worker(upper_root, token, index, cache, key, chown=chown)


def usage_fraction(path: str) -> float:
    """Fraction of the filesystem holding *path* that is used (the upper-tmpfs degrade check)."""
    info = os.statvfs(path)
    return 1.0 - info.f_bavail / max(info.f_blocks, 1)


def upper_full(path: str, threshold: float = 0.8) -> bool:
    return usage_fraction(path) >= threshold


def _relative(rel: str) -> str:
    parts = rel.split("/")
    if not rel or rel.startswith("/") or "\x00" in rel or any(part in ("", ".", "..") for part in parts):
        raise PathRefused(f"entry must be a normalized relative path: {rel!r}")
    return rel


def _lstat(path: str) -> os.stat_result | None:
    try:
        return os.lstat(path)
    except FileNotFoundError:
        return None


def repair_entry(template_tree: str, merged_tree: str, rel: str) -> str:
    """Restore one non-directory entry in a merged tree from the template (bytes, mode, mtime, symlink target;
    an added entry is removed). Returns ``unchanged``, ``restored``, ``removed`` or ``absent``. A directory on
    either side, or a missing parent, raises ``Unrepairable``: the caller degrades the slot instead."""
    rel = _relative(rel)
    source = os.path.join(template_tree, rel)
    target = os.path.join(merged_tree, rel)
    want = _lstat(source)
    have = _lstat(target)
    if (want is not None and stat.S_ISDIR(want.st_mode)) or (have is not None and stat.S_ISDIR(have.st_mode)):
        raise Unrepairable(f"directory-level change at {rel!r}")
    if want is None:
        if have is None:
            return "absent"
        os.unlink(target)
        return "removed"
    if stat.S_ISLNK(want.st_mode):
        link = os.readlink(source)
        if have is not None and stat.S_ISLNK(have.st_mode) and os.readlink(target) == link:
            return "unchanged"
        return _replace(target, lambda path: os.symlink(link, path))
    if have is not None and stat.S_ISREG(have.st_mode) and (
        (have.st_size, stat.S_IMODE(have.st_mode), have.st_mtime_ns)
        == (want.st_size, stat.S_IMODE(want.st_mode), want.st_mtime_ns)
        and _same_bytes(source, target)
    ):
        return "unchanged"

    def fill(path: str) -> None:
        shutil.copyfile(source, path)
        os.chmod(path, stat.S_IMODE(want.st_mode))
        os.utime(path, ns=(want.st_atime_ns, want.st_mtime_ns))

    return _replace(target, fill)


def _same_bytes(left: str, right: str) -> bool:
    with open(left, "rb") as first, open(right, "rb") as second:
        while True:
            a, b = first.read(1 << 20), second.read(1 << 20)
            if a != b:
                return False
            if not a:
                return True


def _replace(target: str, fill: Callable[[str], None]) -> str:
    scratch = os.path.join(os.path.dirname(target), f".cowtree-repair-{uuid.uuid4().hex[:12]}")
    try:
        fill(scratch)
        os.replace(scratch, target)
    except FileNotFoundError as exc:
        raise Unrepairable(f"parent directory missing for {target!r}") from exc
    return "restored"

