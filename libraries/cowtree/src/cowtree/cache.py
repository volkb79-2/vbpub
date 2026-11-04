"""Keyed read-only tree cache: atomic publish, leases, and holder-checked garbage collection.

Layout under an explicitly configured root (a plain directory path; no environment or host policy is read here)::

    <root>/<key>/tree/             published content (read-only by mount policy, optionally chmod-sealed)
    <root>/<key>/COMPLETE          verification manifest (JSON, digest over every entry)
    <root>/<key>.tmp-<token>       build or removal in progress (never leased)
    <root>/.locks/<key>            key lock: EX while building/removing, SH while taking a lease
    <root>/.locks/.gc              global garbage-collection lock
    <root>/.state/<key>/last-used  mtime = last use
    <root>/.state/<key>/leases/<id>.json   one lease per holder

State lives beside the key (not inside it) so a sealed, read-only key directory never blocks lease writes.
"""
from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import re
import shutil
import stat
import time
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any

from .errors import BackendFailed, CacheBusy, KeyMissing, PathRefused, RootInvalid, TreeInvalid
from .paths import _unescape_mount_field, normalized_absolute, require_directory
from .run import _owner_identity, _pid_starttime, _utc_now

COMPLETE_FORMAT = "cowtree-tree/1"
LEASE_FORMAT = "cowtree-lease/1"

_KEY = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{7,127}\Z")
_TMP = re.compile(r"(?P<key>[A-Za-z0-9][A-Za-z0-9_-]{7,127})\.tmp-[0-9a-f]{12}\Z")
_LOCKS = ".locks"
_STATE = ".state"
_GC_LOCK = ".gc"
_CHUNK = 1 << 20
_POLL_SECONDS = 0.05


def validate_key(key: str) -> str:
    """Keys are caller-supplied content/identity hashes: 8-128 chars of [A-Za-z0-9_-], never dotted."""
    if not isinstance(key, str) or _KEY.fullmatch(key) is None:
        raise PathRefused("key must match [A-Za-z0-9][A-Za-z0-9_-]{7,127}")
    return key


def _read_mountinfo() -> str:
    with open("/proc/self/mountinfo", encoding="utf-8") as handle:
        return handle.read()


def _checked_root(path: str, label: str) -> str:
    if not normalized_absolute(path):
        raise RootInvalid(f"{label} must be an absolute, normalized path without NUL")
    if os.path.realpath(path) != path:
        raise RootInvalid(f"{label} must not contain symlinks: {path!r}")
    info = require_directory(path, label=label)
    if info.st_mode & 0o002:
        raise RootInvalid(f"{label} must not be world-writable")
    return path


# ---------------------------------------------------------------- manifest

def _entries(base: str, rel: str = "") -> Iterator[tuple[str, os.stat_result]]:
    with os.scandir(os.path.join(base, rel)) as handle:
        names = sorted(entry.name for entry in handle)
    for name in names:
        child = os.path.join(rel, name)
        info = os.lstat(os.path.join(base, child))
        yield child, info
        if stat.S_ISDIR(info.st_mode):
            yield from _entries(base, child)


def tree_manifest(tree: str) -> dict[str, Any]:
    """Digest of every entry's type, path, permission bits, size and content hash (or symlink target)."""
    digest = hashlib.sha256()
    files = 0
    total = 0
    for rel, info in _entries(tree):
        mode = stat.S_IMODE(info.st_mode)
        full = os.path.join(tree, rel)
        if stat.S_ISDIR(info.st_mode):
            line = f"d {rel} {mode:o}"
        elif stat.S_ISLNK(info.st_mode):
            line = f"l {rel} {os.readlink(full)}"
        elif stat.S_ISREG(info.st_mode):
            content = hashlib.sha256()
            with open(full, "rb") as handle:
                for block in iter(lambda: handle.read(_CHUNK), b""):
                    content.update(block)
            line = f"f {rel} {mode:o} {info.st_size} {content.hexdigest()}"
            files += 1
            total += info.st_size
        else:
            raise PathRefused(f"unsupported entry type in published tree: {rel!r}")
        digest.update(line.encode("utf-8", "surrogateescape") + b"\n")
    return {"digest": digest.hexdigest(), "files": files, "bytes": total}


def _seal(tree: str) -> None:
    for rel, info in _entries(tree):
        if not stat.S_ISLNK(info.st_mode):
            os.chmod(os.path.join(tree, rel), stat.S_IMODE(info.st_mode) & ~0o222)
    info = os.lstat(tree)
    os.chmod(tree, stat.S_IMODE(info.st_mode) & ~0o222)


def _force_rmtree(path: str) -> None:
    """Remove a (possibly sealed) tree by exact path; symlinks are removed, never followed."""
    for dirpath, _dirs, _files in os.walk(path):
        os.chmod(dirpath, stat.S_IMODE(os.lstat(dirpath).st_mode) | 0o700)
    shutil.rmtree(path)


# ---------------------------------------------------------------- mountinfo holders

@dataclass(frozen=True)
class _Mount:
    devno: str
    root: str
    mountpoint: str
    options: str


def _mount_rows(text: str) -> list[_Mount]:
    rows: list[_Mount] = []
    for number, line in enumerate(text.splitlines(), 1):
        if not line:
            continue
        try:
            before, after = line.split(" - ", 1)
            left = before.split()
            right = after.split()
            rows.append(_Mount(
                left[2],
                _unescape_mount_field(left[3]),
                _unescape_mount_field(left[4]),
                right[2] if len(right) > 2 else "",
            ))
        except (IndexError, ValueError) as exc:
            raise RootInvalid(f"malformed mountinfo row {number}") from exc
    return rows


def _under(path: str, parent: str) -> bool:
    return path == parent or path.startswith(parent.rstrip("/") + "/")


def mount_holders(key_path: str, mountinfo: str) -> list[str]:
    """Mountpoints that hold *key_path*: a bind of it or below, a mount below it, or an overlay lowerdir naming it."""
    rows = _mount_rows(mountinfo)
    owners = [row for row in rows if _under(key_path, row.mountpoint)]
    if not owners:
        raise RootInvalid(f"no mountinfo row covers {key_path!r}")
    owner = max(owners, key=lambda row: len(row.mountpoint))
    rel = owner.root.rstrip("/") + key_path[len(owner.mountpoint.rstrip("/")):]
    found: list[str] = []
    for row in rows:
        named = any(
            _under(value, key_path)
            for option in row.options.split(",")
            if option.partition("=")[0] in ("lowerdir", "upperdir", "workdir")
            for value in option.partition("=")[2].split(":")
        )
        if _under(row.mountpoint, key_path) or named or (row.devno == owner.devno and _under(row.root, rel)):
            found.append(row.mountpoint)
    return found


# ---------------------------------------------------------------- leases

def _valid_lease(document: Any) -> bool:
    if not isinstance(document, dict) or document.get("format") != LEASE_FORMAT:
        return False
    if document.get("mode") not in ("flock", "pid"):
        return False
    return all(isinstance(document.get(name), int) for name in ("pid", "pid_starttime")) and all(
        isinstance(document.get(name), (str, int)) for name in ("boot_id", "pidns")
    )


def _pid_holds(document: dict[str, Any]) -> bool:
    """pid-mode lease: alive when pid and start time match. A different boot or pid namespace cannot be judged,
    so it is conservatively alive (never reclaimed by guesswork)."""
    here = _owner_identity()
    if document["boot_id"] != here["boot_id"] or document["pidns"] != here["pidns"]:
        return True
    try:
        return _pid_starttime(document["pid"]) == document["pid_starttime"]
    except OSError:
        return False


def _lease_state(path: str) -> bool:
    """Return whether the lease is live. The kernel flock is authoritative for flock-mode leases (it dies with its holder
    and is immune to pid reuse); pid-mode leases are judged by pid plus start time."""
    try:
        descriptor = os.open(path, os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW)
    except FileNotFoundError:
        return False
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        try:
            document = json.loads(os.pread(descriptor, 65536, 0))
        except ValueError:
            document = None
        return _valid_lease(document) and document["mode"] == "pid" and _pid_holds(document)
    finally:
        os.close(descriptor)


@dataclass
class Lease:
    """A held lease on a published key. Release it (or use it as a context manager) when the lane ends."""

    key: str
    lease_id: str
    path: str
    tree: str
    owner: str
    _descriptor: int | None
    _touch: Callable[[], None] = field(repr=False)

    def heartbeat(self) -> None:
        """Record use (the idle clock for GC). Liveness itself needs no heartbeat: the flock dies with the holder."""
        self._touch()

    def release(self) -> None:
        self._touch()
        try:
            os.unlink(self.path)
        except FileNotFoundError:
            pass
        if self._descriptor is not None:
            os.close(self._descriptor)
            self._descriptor = None

    def __enter__(self) -> "Lease":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.release()


@dataclass(frozen=True)
class Published:
    key: str
    path: str
    tree: str
    digest: str
    files: int
    bytes: int
    built: bool
    lease: Lease | None = None


@dataclass(frozen=True)
class CacheGcItem:
    name: str
    status: str
    detail: str = ""


class Cache:
    """One configured cache root. All paths derive from ``root``; nothing outside it is ever touched."""

    def __init__(
        self,
        root: str,
        *,
        mountinfo: Callable[[], str] = _read_mountinfo,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.root = _checked_root(root, "cache root")
        self._mountinfo = mountinfo
        self._clock = clock
        os.makedirs(os.path.join(root, _LOCKS), mode=0o770, exist_ok=True)
        os.makedirs(os.path.join(root, _STATE), mode=0o770, exist_ok=True)

    # -- paths
    def key_path(self, key: str) -> str:
        return os.path.join(self.root, validate_key(key))

    def tree_path(self, key: str) -> str:
        return os.path.join(self.key_path(key), "tree")

    def _state(self, key: str) -> str:
        return os.path.join(self.root, _STATE, key)

    # -- locking
    def _lock(self, name: str, mode: int, timeout: float) -> int:
        descriptor = os.open(
            os.path.join(self.root, _LOCKS, name),
            os.O_RDWR | os.O_CREAT | os.O_CLOEXEC | os.O_NOFOLLOW,
            0o660,
        )
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(descriptor, mode | fcntl.LOCK_NB)
                return descriptor
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    os.close(descriptor)
                    raise CacheBusy(f"lock {name!r} is held elsewhere") from None
                time.sleep(_POLL_SECONDS)

    # -- COMPLETE marker
    def _complete(self, key: str) -> dict[str, Any] | None:
        try:
            with open(os.path.join(self.key_path(key), "COMPLETE"), encoding="utf-8") as handle:
                document = json.load(handle)
        except (OSError, ValueError):
            return None
        if not isinstance(document, dict) or document.get("format") != COMPLETE_FORMAT or document.get("key") != key:
            return None
        if not all(isinstance(document.get(name), int) for name in ("files", "bytes")) or not isinstance(
            document.get("digest"), str
        ):
            return None
        return document

    def require_complete(self, key: str) -> str:
        """The tree path of a published key, or ``KeyMissing``."""
        if self._complete(key) is None:
            raise KeyMissing(f"key {key!r} is not published")
        return self.tree_path(key)

    # -- removal by exact path
    def _remove_entry(self, name: str) -> None:
        match = _TMP.fullmatch(name)
        validate_key(match.group("key") if match else name)
        path = os.path.join(self.root, name)
        info = os.lstat(path)
        if not stat.S_ISDIR(info.st_mode):
            raise PathRefused(f"refusing to remove non-directory {path!r}")
        _force_rmtree(path)

    def _tmp_name(self, key: str) -> str:
        return f"{key}.tmp-{uuid.uuid4().hex[:12]}"

    def _forget_state(self, key: str) -> None:
        state = self._state(key)
        if os.path.isdir(state):
            _force_rmtree(state)

    # -- publish
    def ensure(
        self,
        key: str,
        build: Callable[[str], None],
        *,
        owner: str | None = None,
        seal: bool = False,
        lock_timeout: float = 600.0,
    ) -> Published:
        """Publish *key* once. ``build(tree_dir)`` fills an empty directory inside a private
        ``<key>.tmp-<token>``; the verified result is renamed into place atomically. Concurrent callers wait on
        the key lock, then reuse the winner's tree. With *owner*, a lease is taken before the lock is released so
        GC cannot slip in between publish and use."""
        key = validate_key(key)
        descriptor = self._lock(key, fcntl.LOCK_EX, lock_timeout)
        try:
            document = self._complete(key)
            built = document is None
            if document is None:
                if os.path.lexists(self.key_path(key)):
                    self._remove_entry(key)
                document = self._build(key, build, seal)
            lease = self._new_lease(key, owner, None) if owner is not None else None
            self._touch(key)
            return Published(
                key, self.key_path(key), self.tree_path(key), document["digest"],
                document["files"], document["bytes"], built, lease,
            )
        finally:
            os.close(descriptor)

    def _build(self, key: str, build: Callable[[str], None], seal: bool) -> dict[str, Any]:
        name = self._tmp_name(key)
        temp = os.path.join(self.root, name)
        tree = os.path.join(temp, "tree")
        os.mkdir(temp, 0o770)
        try:
            os.mkdir(tree, 0o770)
            build(tree)
            if seal:
                _seal(tree)
            manifest = tree_manifest(tree)
            document = {"format": COMPLETE_FORMAT, "key": key, "created_utc": _utc_now()[1], **manifest}
            with open(os.path.join(temp, "COMPLETE"), "w", encoding="utf-8") as handle:
                json.dump(document, handle, sort_keys=True)
            os.rename(temp, self.key_path(key))
        except BaseException:
            self._remove_entry(name)
            raise
        return document

    def verify(self, key: str, *, deep: bool = True) -> dict[str, Any]:
        """Check a published key against its manifest (``deep`` re-reads every file)."""
        document = self._complete(key)
        if document is None:
            raise KeyMissing(f"key {key!r} is not published")
        if deep:
            actual = tree_manifest(self.tree_path(key))
            if actual["digest"] != document["digest"]:
                raise TreeInvalid(f"key {key!r} no longer matches its manifest")
        return document

    # -- last used / leases
    def _touch(self, key: str) -> None:
        os.makedirs(self._state(key), mode=0o770, exist_ok=True)
        marker = os.path.join(self._state(key), "last-used")
        descriptor = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_CLOEXEC | os.O_NOFOLLOW, 0o660)
        os.close(descriptor)
        os.utime(marker, times=(self._clock(), self._clock()))

    def touch_last_used(self, key: str) -> None:
        self._touch(validate_key(key))

    def lease(
        self,
        key: str,
        owner: str,
        *,
        pid: int | None = None,
        lock_timeout: float = 600.0,
    ) -> Lease:
        """Lease a published key. By default the lease is a flock held by this process (dies with it; no pid
        reuse hazard). ``pid=N`` records an external holder instead (pid plus start time plus boot and pid
        namespace); release is then explicit, and a holder in another namespace is conservatively kept alive."""
        key = validate_key(key)
        descriptor = self._lock(key, fcntl.LOCK_SH, lock_timeout)
        try:
            if self._complete(key) is None:
                raise KeyMissing(f"key {key!r} is not published")
            return self._new_lease(key, owner, pid)
        finally:
            os.close(descriptor)

    def _new_lease(self, key: str, owner: str, pid: int | None) -> Lease:
        leases = os.path.join(self._state(key), "leases")
        os.makedirs(leases, mode=0o770, exist_ok=True)
        identity = _owner_identity()
        if pid is not None:
            try:
                identity["pid"] = pid
                identity["pid_starttime"] = _pid_starttime(pid)
            except OSError as exc:
                raise BackendFailed("lease-pid", exc.errno, f"/proc/{pid}", str(exc)) from exc
        lease_id = uuid.uuid4().hex
        path = os.path.join(leases, f"{lease_id}.json")
        descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW, 0o660)
        document = {
            "format": LEASE_FORMAT, "mode": "flock" if pid is None else "pid", "id": lease_id, "key": key,
            "owner": owner, "created_utc": _utc_now()[1], **identity,
        }
        os.write(descriptor, json.dumps(document, sort_keys=True).encode("utf-8"))
        if pid is None:
            fcntl.flock(descriptor, fcntl.LOCK_SH)
            held: int | None = descriptor
        else:
            os.close(descriptor)
            held = None
        return Lease(key, lease_id, path, self.tree_path(key), owner, held, lambda: self._touch(key))

    # -- garbage collection
    def _prune_leases(self, key: str) -> int:
        """Delete dead lease files; return the number of live ones. Caller holds the key lock EX."""
        leases = os.path.join(self._state(key), "leases")
        live = 0
        if os.path.isdir(leases):
            for name in sorted(os.listdir(leases)):
                path = os.path.join(leases, name)
                if _lease_state(path):
                    live += 1
                else:
                    with contextlib.suppress(FileNotFoundError):
                        os.unlink(path)
        return live

    def _idle_seconds(self, key: str) -> float:
        try:
            used = os.stat(os.path.join(self._state(key), "last-used")).st_mtime
        except FileNotFoundError:
            used = os.stat(os.path.join(self.key_path(key), "COMPLETE")).st_mtime
        return self._clock() - used

    def gc(self, idle_seconds: float, *, lock_timeout: float = 0.0) -> list[CacheGcItem]:
        """Remove unused keys and stale scratch. Policy, in order, per entry (each under that key's EX lock,
        taken without waiting; a busy key is skipped, never waited on):

        1. ``<key>.tmp-*`` scratch and key directories without a valid COMPLETE are removed (the build lock is
           held for a whole build, so an unlocked one is dead).
        2. Dead lease files are deleted; any live lease keeps the key.
        3. A key idle for less than *idle_seconds* is kept.
        4. A key named by any mountinfo row (bind of it or below, or an overlay lowerdir/upperdir/workdir) is kept.
        5. Otherwise the key is renamed to scratch and removed by exact path.

        A global GC lock makes concurrent GC runs exclusive (``CacheBusy`` if held longer than *lock_timeout*).
        """
        gate = self._lock(_GC_LOCK, fcntl.LOCK_EX, lock_timeout)
        try:
            items = [self._gc_entry(name, idle_seconds) for name in sorted(os.listdir(self.root)) if name not in (_LOCKS, _STATE)]
            keys = {item.name for item in items}
            for stale in sorted(os.listdir(os.path.join(self.root, _STATE))):
                if stale not in keys and _KEY.fullmatch(stale):
                    items.append(self._gc_state_orphan(stale))
            return items
        finally:
            os.close(gate)

    def _gc_entry(self, name: str, idle_seconds: float) -> CacheGcItem:
        match = _TMP.fullmatch(name)
        key = match.group("key") if match else name
        if _KEY.fullmatch(key) is None or not stat.S_ISDIR(os.lstat(os.path.join(self.root, name)).st_mode):
            return CacheGcItem(name, "foreign", "not a cache entry; left alone")
        try:
            descriptor = self._lock(key, fcntl.LOCK_EX, 0.0)
        except CacheBusy:
            return CacheGcItem(name, "busy", "key lock held")
        try:
            if match:
                self._remove_entry(name)
                return CacheGcItem(name, "removed", "stale scratch")
            document = self._complete(key)
            if document is None:
                self._remove_entry(name)
                self._forget_state(key)
                return CacheGcItem(name, "removed", "no valid COMPLETE")
            if self._prune_leases(key):
                return CacheGcItem(name, "leased", "live lease")
            idle = self._idle_seconds(key)
            if idle < idle_seconds:
                return CacheGcItem(name, "kept", f"idle {idle:.0f}s")
            holders = mount_holders(self.key_path(key), self._mountinfo())
            if holders:
                return CacheGcItem(name, "mounted", ",".join(holders))
            scratch = self._tmp_name(key)
            os.rename(self.key_path(key), os.path.join(self.root, scratch))
            self._remove_entry(scratch)
            self._forget_state(key)
            return CacheGcItem(name, "removed", f"idle {idle:.0f}s")
        finally:
            os.close(descriptor)

    def _gc_state_orphan(self, key: str) -> CacheGcItem:
        try:
            descriptor = self._lock(key, fcntl.LOCK_EX, 0.0)
        except CacheBusy:
            return CacheGcItem(key, "busy", "key lock held")
        try:
            if os.path.lexists(self.key_path(key)):
                return CacheGcItem(key, "kept", "key appeared")
            self._forget_state(key)
            return CacheGcItem(key, "removed", "orphan state")
        finally:
            os.close(descriptor)

    def survey(self) -> list[dict[str, Any]]:
        """Read-only inventory of keys (no locks taken, nothing changed)."""
        rows: list[dict[str, Any]] = []
        for name in sorted(os.listdir(self.root)):
            if name in (_LOCKS, _STATE):
                continue
            match = _TMP.fullmatch(name)
            key = match.group("key") if match else name
            if _KEY.fullmatch(key) is None:
                rows.append({"name": name, "kind": "foreign"})
            elif match:
                rows.append({"name": name, "kind": "scratch"})
            else:
                document = self._complete(key)
                leases = os.path.join(self._state(key), "leases")
                count = sum(1 for lease in os.listdir(leases) if _lease_state(os.path.join(leases, lease))) \
                    if os.path.isdir(leases) else 0
                rows.append({
                    "name": name,
                    "kind": "key" if document else "incomplete",
                    "bytes": document["bytes"] if document else 0,
                    "files": document["files"] if document else 0,
                    "live_leases": count,
                    "idle_seconds": round(self._idle_seconds(key), 1) if document else None,
                })
        return rows
