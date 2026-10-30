"""Lease-aware run inventory and garbage collection."""
from __future__ import annotations

import fcntl
import json
import os
import shutil
import socket
import stat
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .backends import BtrfsBackend, LinuxOps, _validate_compression
from .detect import Area
from .errors import BackendFailed, CowError, PathRefused
from .paths import validate_name
from .run import _MAX_OWNER_MANIFEST_BYTES, _pid_starttime
from .space import record_pending_delete

_MANIFEST_FORMAT = "cowtree-run/1"
_LOCK_FLAGS = os.O_RDWR | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)


@dataclass(frozen=True)
class GcItem:
    name: str
    status: str
    detail: str = ""
    deleted_bytes: int = 0


def _manifest(run_dir: str) -> dict[str, Any] | None:
    manifest_path = os.path.join(run_dir, "owner.json")
    try:
        descriptor = os.open(
            manifest_path,
            os.O_RDONLY | os.O_CLOEXEC | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0),
        )
        with os.fdopen(descriptor, "rb") as handle:
            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                return None
            payload = handle.read(_MAX_OWNER_MANIFEST_BYTES + 1)
            if len(payload) > _MAX_OWNER_MANIFEST_BYTES:
                return None
            document = json.loads(payload)
    except (OSError, ValueError, UnicodeError):
        return None
    if not isinstance(document, dict) or document.get("format") != _MANIFEST_FORMAT:
        return None
    required = {"run_id", "tool", "labels", "backend", "api", "created_utc", "holder", "external_ref", "estimate", "owner"}
    if not required.issubset(document):
        return None
    if document.get("holder") not in ("flock", "external"):
        return None
    if not isinstance(document.get("run_id"), str) or not document["run_id"]:
        return None
    if not isinstance(document.get("tool"), str):
        return None
    if not isinstance(document.get("labels"), dict) or not isinstance(document.get("estimate"), dict):
        return None
    if document.get("backend") not in ("btrfs", "reflink", "copy"):
        return None
    if isinstance(document.get("api"), bool) or document.get("api") != 1:
        return None
    if not isinstance(document.get("created_utc"), str):
        return None
    external_ref = document.get("external_ref")
    if document["holder"] == "external":
        if not isinstance(external_ref, str) or not external_ref:
            return None
    elif external_ref is not None:
        return None
    estimate = document["estimate"]
    estimate_keys = {"ingest_bytes", "per_clone_bytes", "max_live_clones"}
    if not estimate_keys.issubset(estimate) or any(
        isinstance(estimate[key], bool) or not isinstance(estimate[key], int) or estimate[key] < 0
        for key in estimate_keys
    ):
        return None
    owner = document.get("owner")
    if not isinstance(owner, dict) or not {"uid", "pid", "pid_starttime", "boot_id", "pidns", "hostname"}.issubset(owner):
        return None
    if any(isinstance(owner[key], bool) or not isinstance(owner[key], int) or owner[key] < 0 for key in ("uid", "pid", "pid_starttime", "pidns")):
        return None
    if owner["pid"] == 0 or owner["pid_starttime"] == 0:
        return None
    if not all(isinstance(owner[key], str) and owner[key] for key in ("boot_id", "hostname")):
        return None
    return document


def _owner_status(owner: dict[str, Any]) -> str:
    """Return dead/live/unknown using boot, PID-namespace, and start-time identity."""
    try:
        hostname = socket.gethostname()
    except OSError:
        return "unknown"
    if owner["hostname"] != hostname:
        return "unknown"
    try:
        boot_id = Path("/proc/sys/kernel/random/boot_id").read_text(encoding="ascii").strip()
    except OSError:
        return "unknown"
    if not boot_id:
        return "unknown"
    if owner["boot_id"] != boot_id:
        return "dead"
    if owner["uid"] != os.geteuid():
        return "unknown"
    try:
        pidns = os.stat("/proc/self/ns/pid").st_ino
    except OSError:
        return "unknown"
    if owner["pidns"] != pidns:
        return "unknown"
    try:
        starttime = _pid_starttime(owner["pid"])
    except FileNotFoundError:
        return "dead"
    except OSError:
        return "unknown"
    return "live" if starttime == owner["pid_starttime"] else "dead"


def _allocated_bytes(path: str) -> tuple[int, int]:
    total = 0
    device = os.lstat(path).st_dev
    stack = [path]
    while stack:
        current = stack.pop()
        info = os.lstat(current)
        total += int(getattr(info, "st_blocks", 0)) * 512
        if stat.S_ISDIR(info.st_mode):
            with os.scandir(current) as entries:
                for entry in entries:
                    if entry.is_dir(follow_symlinks=False):
                        stack.append(entry.path)
                    else:
                        child = entry.stat(follow_symlinks=False)
                        total += int(getattr(child, "st_blocks", 0)) * 512
    return total, device


def _delete_run(run_dir: str, manifest: dict[str, Any], ops=None) -> None:
    if manifest["backend"] == "btrfs":
        trees_path = os.path.join(run_dir, "trees")
        try:
            trees_info = os.lstat(trees_path)
        except FileNotFoundError:
            trees_info = None
        if trees_info is not None:
            if not stat.S_ISDIR(trees_info.st_mode):
                raise PathRefused("btrfs run trees path must be a real directory")
            backend = BtrfsBackend(ops or LinuxOps())
            with os.scandir(trees_path) as entries:
                trees = sorted(entry.path for entry in entries)
            for tree in trees:
                backend.delete_tree(tree)
    shutil.rmtree(run_dir)


def gc(scan_dir: str | os.PathLike[str], *, ops=None) -> list[GcItem]:
    """Delete only valid, non-external runs whose nonblocking flock is free."""
    root = os.fspath(scan_dir)
    try:
        root_info = os.lstat(root)
    except FileNotFoundError:
        return []
    if not stat.S_ISDIR(root_info.st_mode) or stat.S_ISLNK(root_info.st_mode):
        raise PathRefused(f"gc scan path must be a real directory: {root!r}")
    pending_device = root_info.st_dev
    results: list[GcItem] = []
    with os.scandir(root) as entries:
        names = sorted(entry.name for entry in entries)
    for name in names:
        run_dir = os.path.join(root, name)
        try:
            run_info = os.lstat(run_dir)
        except OSError as exc:
            results.append(GcItem(name, "inspect-error", str(exc)))
            continue
        if not stat.S_ISDIR(run_info.st_mode) or stat.S_ISLNK(run_info.st_mode):
            results.append(GcItem(name, "not-directory"))
            continue
        manifest = _manifest(run_dir)
        if manifest is None:
            results.append(GcItem(name, "manifestless-or-invalid"))
            continue
        if manifest["holder"] == "external":
            results.append(GcItem(name, "external", str(manifest.get("external_ref") or "")))
            continue
        lock_path = os.path.join(run_dir, "owner.lock")
        lock_fd = -1
        try:
            lock_path_info = os.lstat(lock_path)
            if not stat.S_ISREG(lock_path_info.st_mode):
                results.append(GcItem(name, "invalid-lock"))
                continue
            lock_fd = os.open(lock_path, _LOCK_FLAGS)
            lock_info = os.fstat(lock_fd)
            if not stat.S_ISREG(lock_info.st_mode):
                os.close(lock_fd)
                lock_fd = -1
                results.append(GcItem(name, "invalid-lock"))
                continue
        except OSError as exc:
            if lock_fd >= 0:
                os.close(lock_fd)
            results.append(GcItem(name, "lock-unavailable", str(exc)))
            continue
        try:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                results.append(GcItem(name, "lease-held"))
                continue
            except OSError as exc:
                results.append(GcItem(name, "lock-error", str(exc)))
                continue
            owner_status = _owner_status(manifest["owner"])
            if owner_status != "dead":
                status = "owner-live" if owner_status == "live" else "owner-identity-unknown"
                results.append(GcItem(name, status))
                continue
            try:
                allocated, _run_device = _allocated_bytes(run_dir)
                _delete_run(run_dir, manifest, ops)
            except (OSError, CowError) as exc:
                results.append(GcItem(name, "delete-failed", str(exc)))
                continue
            if manifest["backend"] == "btrfs":
                record_pending_delete(pending_device, allocated)
            results.append(GcItem(name, "deleted", deleted_bytes=allocated))
        finally:
            os.close(lock_fd)
    return results


def dead_flock_runs(scan_dir: str | os.PathLike[str]) -> list[str]:
    """Return valid flock-owned run names whose lease can be acquired."""
    root = os.fspath(scan_dir)
    try:
        root_info = os.lstat(root)
    except FileNotFoundError:
        return []
    if not stat.S_ISDIR(root_info.st_mode) or stat.S_ISLNK(root_info.st_mode):
        return []
    with os.scandir(root) as entries:
        candidates = sorted(entry.name for entry in entries)
    dead: list[str] = []
    for name in candidates:
        run_dir = os.path.join(root, name)
        try:
            if not stat.S_ISDIR(os.lstat(run_dir).st_mode):
                continue
        except OSError:
            continue
        manifest = _manifest(run_dir)
        if manifest is None or manifest["holder"] != "flock":
            continue
        descriptor = -1
        try:
            descriptor = os.open(os.path.join(run_dir, "owner.lock"), _LOCK_FLAGS)
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                os.close(descriptor)
                descriptor = -1
                continue
        except OSError:
            if descriptor >= 0:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
            continue
        try:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                continue
            if _owner_status(manifest["owner"]) == "dead":
                dead.append(name)
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)
    return dead


def make_shared(area: Area, name: str, *, compression: str | None = None) -> str:
    """Create a caller-owned shared tree while holding the shared-area lease."""
    validate_name(name)
    if compression is not None:
        _validate_compression(compression)
    lock_path = os.path.join(area.private_dir, "shared.lock")
    lock_fd = -1
    try:
        lock_fd = os.open(lock_path, _LOCK_FLAGS | os.O_CREAT, 0o600)
        if not stat.S_ISREG(os.fstat(lock_fd).st_mode):
            raise PathRefused(".cowtree/shared.lock must be a regular file")
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
    except Exception as exc:
        if lock_fd >= 0:
            os.close(lock_fd)
        if isinstance(exc, OSError):
            raise BackendFailed("shared-lock", exc.errno, lock_path, str(exc)) from exc
        raise
    try:
        destination = os.path.join(area.shared_dir, name)
        if os.path.lexists(destination):
            raise PathRefused(f"shared tree already exists: {name!r}")
        try:
            area.backend.make_tree(destination, compression=compression)
        except (PathRefused, BackendFailed):
            raise
        except OSError as exc:
            raise BackendFailed("make-shared", exc.errno, destination, str(exc)) from exc
        return destination
    finally:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
        finally:
            os.close(lock_fd)


def survey(area: Area) -> dict[str, Any]:
    statvfs = os.statvfs(area.root)
    runs: list[dict[str, str]] = []
    if os.path.isdir(area.runs_dir):
        with os.scandir(area.runs_dir) as entries:
            for entry in sorted(entries, key=lambda item: item.name):
                manifest = _manifest(entry.path) if entry.is_dir(follow_symlinks=False) else None
                state = "external" if manifest and manifest.get("holder") == "external" else "managed" if manifest else "unknown"
                runs.append({"name": entry.name, "state": state})
    return {
        "backend": area.caps.backend,
        "fs_type": area.caps.fs_type,
        "root": area.root,
        "available_bytes": int(statvfs.f_bavail) * int(statvfs.f_frsize),
        "runs": runs,
    }


def gc_json(items: list[GcItem]) -> str:
    return json.dumps([asdict(item) for item in items], sort_keys=True)
