"""Keyed read-only tree cache: atomic publish, leases, and holder-checked garbage collection.

Layout under an explicitly configured root (a plain directory path; no environment or host policy is read here).
It is the layout of ``HOST-CONTRACT.md`` item 3, which a host GC (for example ``mdt_tree_cache.py``) consumes::

    <root>/<key>/tree/             published content (read-only by mount policy, optionally chmod-sealed)
    <root>/<key>/COMPLETE          verification manifest (JSON, digest over every entry)
    <root>/<key>/.lease            lease file: every lessee holds LOCK_SH (flock) on it for its whole life
    <root>/<key>/.last-used        mtime = last use (the idle clock)
    <root>/<key>.tmp-<token>       build or removal in progress (never leased)
    <root>/.locks/<key>            key lock: EX while building/removing, SH while taking a lease (cowtree internal)
    <root>/.locks/.gc              global garbage-collection lock (cowtree internal)

Only ``tree/`` is ever chmod-sealed; the key directory itself stays group-writable so ``.last-used`` can be touched
and ``.lease`` opened under a sealed tree. Every directory and file this module creates gets an explicit mode
(``0770``/``0660``, keeping the root's setgid bit) whatever the umask, so a cache shared by a group works at umask 022.
Files that the *build callable* writes inside ``tree/`` keep the permissions the callable gives them.
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

from .errors import BackendFailed, CacheBusy, KeyCollision, KeyMissing, PathRefused, RootInvalid, TreeInvalid
from .paths import _unescape_mount_field, mount_fstype, normalized_absolute, require_directory
from .run import _utc_now

COMPLETE_FORMAT = "cowtree-tree/1"

_KEY = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{7,127}\Z")
_TMP = re.compile(r"(?P<key>[A-Za-z0-9][A-Za-z0-9_-]{7,127})\.tmp-[0-9a-f]{12}\Z")
_LOCKS = ".locks"
_GC_LOCK = ".gc"
_LEASE = ".lease"
_LAST_USED = ".last-used"
_CHUNK = 1 << 20
_POLL_SECONDS = 0.05
_FILE_MODE = 0o660
_DIR_MODE = 0o770
_SETGID = 0o2000
_MAX_IDENTITY = 512
_OPEN_RETRIES = 100


def validate_key(key: str) -> str:
    """Keys are caller-supplied content/identity hashes: 8-128 chars of [A-Za-z0-9_-], never dotted.

    A key must be collision resistant (a hash of at least 128 bits over everything that determines the content).
    ``ensure`` trusts it unless the caller also passes ``identity=`` (see :meth:`Cache.ensure`)."""
    if not isinstance(key, str) or _KEY.fullmatch(key) is None:
        raise PathRefused("key must match [A-Za-z0-9][A-Za-z0-9_-]{7,127}")
    return key


def _read_mountinfo() -> str:
    with open("/proc/self/mountinfo", encoding="utf-8") as handle:
        return handle.read()


def _proc_gone(pid: str, proc: str = "/proc") -> bool:
    """True when a process vanished or is a zombie/dead entry (it then holds no mount namespace)."""
    if not os.path.exists(f"{proc}/{pid}"):
        return True
    try:
        with open(f"{proc}/{pid}/stat", encoding="utf-8", errors="replace") as handle:
            tail = handle.read().rpartition(")")[2].split()
    except OSError:
        return False
    return bool(tail) and tail[0] in ("Z", "X", "x")


def _read_all_mountinfo(proc: str = "/proc") -> str:
    """The mountinfo of every process (a bind inside another mount namespace is invisible from ours).

    A process that exits while scanned is skipped; any other unreadable entry raises ``RootInvalid``, because a
    half-read holder table could make a mounted key look unused (the GC then keeps the key)."""
    chunks: list[str] = []
    try:
        pids = [name for name in os.listdir(proc) if name.isdigit()]
    except OSError as exc:
        raise RootInvalid(f"cannot list {proc}: {exc}") from exc
    for pid in pids:
        retried = False
        while True:
            try:
                with open(f"{proc}/{pid}/mountinfo", encoding="utf-8", errors="replace") as handle:
                    chunks.append(handle.read())
                break
            except OSError as exc:
                if _proc_gone(pid, proc):
                    break
                if retried:
                    raise RootInvalid(f"cannot read {proc}/{pid}/mountinfo: {exc}") from exc
                retried = True
                time.sleep(0.05)
    return "\n".join(chunks)


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


def _make_removable(directory: str) -> None:
    """chmod every directory below *directory* (itself included) so it can be listed and emptied; never follows symlinks."""
    os.chmod(directory, stat.S_IMODE(os.lstat(directory).st_mode) | 0o700)
    with os.scandir(directory) as handle:
        children = [entry.path for entry in handle if entry.is_dir(follow_symlinks=False)]
    for child in children:
        _make_removable(child)


def _force_rmtree(path: str) -> None:
    """Remove a (possibly sealed, even mode-000) tree by exact path; symlinks are removed, never followed."""
    _make_removable(path)
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


def mount_holders(key_path: str, mountinfo: str, holders_text: str | None = None) -> list[str]:
    """Mountpoints that hold *key_path*: a bind of it or below, a mount below it, or an overlay lowerdir naming it.

    *mountinfo* is this process's table (it decides which filesystem the key lives on); *holders_text* is the table
    scanned for holders (default: the same text; pass every process's mountinfo concatenated to see binds in
    other mount namespaces)."""
    owners = [row for row in _mount_rows(mountinfo) if _under(key_path, row.mountpoint)]
    if not owners:
        raise RootInvalid(f"no mountinfo row covers {key_path!r}")
    owner = max(owners, key=lambda row: len(row.mountpoint))
    rel = owner.root.rstrip("/") + key_path[len(owner.mountpoint.rstrip("/")):]
    found: list[str] = []
    for row in _mount_rows(mountinfo if holders_text is None else holders_text):
        named = any(
            _under(value, key_path)
            for option in row.options.split(",")
            if option.partition("=")[0] in ("lowerdir", "upperdir", "workdir")
            for value in option.partition("=")[2].split(":")
        )
        if (_under(row.mountpoint, key_path) or named or (row.devno == owner.devno and _under(row.root, rel))) \
                and row.mountpoint not in found:
            found.append(row.mountpoint)
    return found


# ---------------------------------------------------------------- leases

@dataclass
class Lease:
    """A held lease on a published key: a ``LOCK_SH`` flock on ``<key>/.lease``. Release it (or use it as a context
    manager) when the lane ends; the kernel also drops it when the holder dies, so a crash never leaves a stale lease.

    ``owner`` is a label kept in memory only (nothing is recorded on disk). The descriptor is close-on-exec; a
    ``fork()`` without exec shares the flock (the lock survives until every copy is closed), so fork children must
    not outlive the lessee unaware of it."""

    key: str
    path: str
    tree: str
    owner: str
    _descriptor: int | None
    _touch: Callable[[], None] = field(repr=False)

    def heartbeat(self) -> None:
        """Record use (the idle clock for GC). Liveness itself needs no heartbeat: the flock dies with the holder."""
        self._touch()

    def release(self) -> None:
        if self._descriptor is None:
            return
        with contextlib.suppress(OSError, KeyMissing):
            self._touch()
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
    """One configured cache root. All paths derive from ``root``; nothing outside it is ever touched.

    ``require_tmpfs`` is mandatory and has no default: the shared cache is a tmpfs area (``HOST-CONTRACT.md`` item 1),
    and with ``True`` the root must lie on a ``tmpfs``/``ramfs`` mount (checked at construction and again at every
    ``gc``), so nothing is ever deleted from a real disk. ``False`` is the explicit opt-out for a private cache the
    caller owns on a normal filesystem (tests, a level-C/D work directory, a third party's disk cache).

    ``mountinfo`` is this process's mount table; ``all_mountinfo`` is the table scanned for holders. By default
    ``all_mountinfo`` reads ``/proc/*/mountinfo`` of every process (so a bind inside another mount namespace is seen);
    when only ``mountinfo`` is given (tests), it is used for both."""

    def __init__(
        self,
        root: str,
        *,
        require_tmpfs: bool,
        mountinfo: Callable[[], str] = _read_mountinfo,
        all_mountinfo: Callable[[], str] | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.root = _checked_root(root, "cache root")
        self._require_tmpfs = bool(require_tmpfs)
        self._mountinfo = mountinfo
        self._all_mountinfo = all_mountinfo or (_read_all_mountinfo if mountinfo is _read_mountinfo else mountinfo)
        self._clock = clock
        self._dir_mode = _DIR_MODE | (os.lstat(self.root).st_mode & _SETGID)
        self._check_tmpfs()

    def _check_tmpfs(self) -> None:
        if not self._require_tmpfs:
            return
        kind = mount_fstype(self.root, self._mountinfo())
        if kind not in ("tmpfs", "ramfs"):
            raise RootInvalid(
                f"cache root {self.root!r} is on {kind!r}, not a tmpfs; pass require_tmpfs=False to use a "
                "private cache on a normal filesystem"
            )

    # -- paths
    def key_path(self, key: str) -> str:
        return os.path.join(self.root, validate_key(key))

    def tree_path(self, key: str) -> str:
        return os.path.join(self.key_path(key), "tree")

    # -- creation with explicit modes (independent of the umask)
    def _mkdir(self, path: str) -> bool:
        """Create *path* with the shared directory mode; False when it already exists."""
        try:
            os.mkdir(path, self._dir_mode)
        except FileExistsError:
            return False
        os.chmod(path, self._dir_mode)
        return True

    @staticmethod
    def _create_file(path: str) -> int:
        """Exclusively create a group-shared regular file and return its descriptor (mode set explicitly)."""
        descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW, _FILE_MODE)
        os.fchmod(descriptor, _FILE_MODE)
        return descriptor

    # -- locking
    def _lock(self, name: str, mode: int, timeout: float) -> int:
        self._mkdir(os.path.join(self.root, _LOCKS))
        path = os.path.join(self.root, _LOCKS, name)
        attempts = 0
        while True:
            try:
                descriptor = self._create_file(path)
                break
            except FileExistsError:
                try:
                    descriptor = os.open(path, os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW)
                    break
                except PermissionError:
                    # the creator has not applied the group mode yet (umask window of a concurrent first create)
                    attempts += 1
                    if attempts >= _OPEN_RETRIES:
                        raise
                    time.sleep(0.01)
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
        if "identity" in document and not isinstance(document["identity"], str):
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

    # -- publish
    def ensure(
        self,
        key: str,
        build: Callable[[str], None],
        *,
        owner: str | None = None,
        seal: bool = False,
        identity: str | None = None,
        lock_timeout: float = 600.0,
    ) -> Published:
        """Publish *key* once. ``build(tree_dir)`` fills an empty directory inside a private
        ``<key>.tmp-<token>``; the verified result is renamed into place atomically. Concurrent callers wait on
        the key lock, then reuse the winner's tree. With *owner*, a lease is taken before the lock is released so
        GC cannot slip in between publish and use.

        Keys are caller-chosen, so a key that is not a collision-resistant content hash can alias different content.
        With *identity* (an opaque string of at most 512 characters, for example the hash inputs or a source
        revision) it is recorded in ``COMPLETE`` on first publish, and a later ``ensure`` with a different identity
        raises ``KeyCollision`` instead of silently reusing the tree. A key published without an identity cannot
        be verified against one and is refused as well. Without *identity* the key is trusted."""
        key = validate_key(key)
        if identity is not None and (not isinstance(identity, str) or not identity or len(identity) > _MAX_IDENTITY):
            raise PathRefused(f"identity must be a non-empty string of at most {_MAX_IDENTITY} characters")
        descriptor = self._lock(key, fcntl.LOCK_EX, lock_timeout)
        try:
            document = self._complete(key)
            built = document is None
            if document is None:
                if os.path.lexists(self.key_path(key)):
                    self._remove_entry(key)
                document = self._build(key, build, seal, identity)
            else:
                recorded = document.get("identity")
                if identity is not None and recorded != identity:
                    raise KeyCollision(
                        f"key {key!r} is already published with identity {recorded!r}, not {identity!r}"
                    )
                self._restore_markers(key)
            lease = self._new_lease(key, owner, lock_timeout) if owner is not None else None
            self._touch(key)
            return Published(
                key, self.key_path(key), self.tree_path(key), document["digest"],
                document["files"], document["bytes"], built, lease,
            )
        finally:
            os.close(descriptor)

    def _restore_markers(self, key: str) -> None:
        """Recreate a missing ``.lease`` / ``.last-used`` of a published key (a foreign publisher may omit them)."""
        for name in (_LEASE, _LAST_USED):
            with contextlib.suppress(FileExistsError):
                os.close(self._create_file(os.path.join(self.key_path(key), name)))

    def _build(self, key: str, build: Callable[[str], None], seal: bool, identity: str | None) -> dict[str, Any]:
        name = self._tmp_name(key)
        temp = os.path.join(self.root, name)
        tree = os.path.join(temp, "tree")
        if not self._mkdir(temp):
            raise PathRefused(f"scratch directory already exists: {temp!r}")
        try:
            self._mkdir(tree)
            build(tree)
            if seal:
                _seal(tree)
            manifest = tree_manifest(tree)
            document: dict[str, Any] = {
                "format": COMPLETE_FORMAT, "key": key, "created_utc": _utc_now()[1], **manifest,
            }
            if identity is not None:
                document["identity"] = identity
            with os.fdopen(self._create_file(os.path.join(temp, "COMPLETE")), "w", encoding="utf-8") as handle:
                json.dump(document, handle, sort_keys=True)
            for marker in (_LEASE, _LAST_USED):
                os.close(self._create_file(os.path.join(temp, marker)))
            os.rename(temp, self.key_path(key))
        except BaseException:
            self._remove_entry(name)
            raise
        return document

    def verify(self, key: str, *, deep: bool = True) -> dict[str, Any]:
        """Check a published key against its manifest (``deep`` re-reads every file). Read-only."""
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
        marker = os.path.join(self.key_path(key), _LAST_USED)
        now = self._clock()
        try:
            os.utime(marker, times=(now, now))
        except FileNotFoundError:
            try:
                os.close(self._create_file(marker))
            except FileNotFoundError:
                raise KeyMissing(f"key {key!r} is not published") from None
            except FileExistsError:
                pass
            os.utime(marker, times=(now, now))

    def touch_last_used(self, key: str) -> None:
        self._touch(validate_key(key))

    def lease(self, key: str, owner: str, *, lock_timeout: float = 600.0) -> Lease:
        """Lease a published key: touch ``.last-used`` first, then hold ``LOCK_SH`` on ``<key>/.lease`` until
        ``release()`` (or the holder's death). Take it before mounting or binding anything of the key. Raises
        ``KeyMissing`` if the key is not (or no longer) published, ``CacheBusy`` if a build or removal holds
        the key past *lock_timeout*."""
        key = validate_key(key)
        descriptor = self._lock(key, fcntl.LOCK_SH, lock_timeout)
        try:
            if self._complete(key) is None:
                raise KeyMissing(f"key {key!r} is not published")
            return self._new_lease(key, owner, lock_timeout)
        finally:
            os.close(descriptor)

    def _lease_valid(self, descriptor: int, key: str) -> bool:
        """Re-validate after taking the lock (HOST-CONTRACT item 3): a host GC may have removed the key between
        our ``open`` and ``flock``, leaving a lock on an unlinked inode."""
        try:
            held = os.fstat(descriptor)
            current = os.stat(os.path.join(self.key_path(key), _LEASE))
            os.stat(os.path.join(self.key_path(key), "COMPLETE"))
        except OSError:
            return False
        return (
            stat.S_ISREG(held.st_mode) and held.st_nlink > 0
            and (held.st_dev, held.st_ino) == (current.st_dev, current.st_ino)
        )

    def _new_lease(self, key: str, owner: str, timeout: float) -> Lease:
        path = os.path.join(self.key_path(key), _LEASE)
        with contextlib.suppress(OSError, KeyMissing):
            self._touch(key)
        deadline = time.monotonic() + timeout
        while True:
            try:
                descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK)
            except FileNotFoundError:
                raise KeyMissing(f"key {key!r} is not published") from None
            except OSError as exc:
                raise BackendFailed("lease-open", exc.errno, path, str(exc)) from exc
            try:
                fcntl.flock(descriptor, fcntl.LOCK_SH | fcntl.LOCK_NB)
                if self._lease_valid(descriptor, key):
                    return Lease(key, path, self.tree_path(key), owner, descriptor, lambda: self._touch(key))
            except BlockingIOError:
                pass  # a GC pass holds the lease exclusively while it decides; retry
            os.close(descriptor)
            if time.monotonic() >= deadline:
                raise CacheBusy(f"lease of {key!r} is held exclusively elsewhere")
            time.sleep(_POLL_SECONDS)

    # -- garbage collection
    def _probe_lease(self, key: str) -> tuple[str, int | None]:
        """Try ``LOCK_EX|LOCK_NB`` on ``<key>/.lease``: ``("free", fd)`` (caller closes fd), ``("held", None)``,
        ``("none", None)`` (no lease file: nobody can hold one) or ``("invalid", None)`` (not a regular file, or
        unreadable: never judged)."""
        path = os.path.join(self.key_path(key), _LEASE)
        try:
            if not stat.S_ISREG(os.lstat(path).st_mode):
                return "invalid", None
            descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK)
        except FileNotFoundError:
            return "none", None
        except OSError:
            return "invalid", None
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                os.close(descriptor)
                return "invalid", None
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(descriptor)
            return "held", None
        except OSError:
            os.close(descriptor)
            return "invalid", None
        return "free", descriptor

    def _idle_seconds(self, key: str) -> float:
        try:
            used = os.stat(os.path.join(self.key_path(key), _LAST_USED)).st_mtime
        except FileNotFoundError:
            used = os.stat(os.path.join(self.key_path(key), "COMPLETE")).st_mtime
        return self._clock() - used

    def gc(self, idle_seconds: float, *, lock_timeout: float = 0.0) -> list[CacheGcItem]:
        """Remove unused keys and stale scratch. The root must (unless ``require_tmpfs=False``) still be a tmpfs.
        Policy, in order, per entry (each under that key's EX lock, taken without waiting; a busy key is skipped,
        never waited on):

        1. ``<key>.tmp-*`` scratch and key directories without a valid COMPLETE are removed (the build lock is
           held for a whole build, so an unlocked one is dead).
        2. ``LOCK_EX|LOCK_NB`` on ``<key>/.lease`` must succeed; a live lessee keeps the key (the lock is held
           until the key is gone, so a late lessee cannot slip in). A lease that is not a regular file keeps it.
        3. A key idle (``.last-used``, else ``COMPLETE`` mtime) for less than *idle_seconds* is kept.
        4. A key named by any mountinfo row of any process (bind of it or below, or an overlay
           lowerdir/upperdir/workdir) is kept; if some process's mountinfo cannot be read the key is kept.
        5. Otherwise the key is renamed to scratch and removed by exact path.

        A global GC lock makes concurrent GC runs exclusive (``CacheBusy`` if held longer than *lock_timeout*).
        """
        self._check_tmpfs()
        gate = self._lock(_GC_LOCK, fcntl.LOCK_EX, lock_timeout)
        try:
            return [self._gc_entry(name, idle_seconds) for name in sorted(os.listdir(self.root)) if name != _LOCKS]
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
            if self._complete(key) is None:
                self._remove_entry(name)
                return CacheGcItem(name, "removed", "no valid COMPLETE")
            state, lease_fd = self._probe_lease(key)
            if state == "held":
                return CacheGcItem(name, "leased", "live lease")
            if state == "invalid":
                return CacheGcItem(name, "kept", ".lease is not a regular file or cannot be opened")
            try:
                idle = self._idle_seconds(key)
                if idle < idle_seconds:
                    return CacheGcItem(name, "kept", f"idle {idle:.0f}s")
                try:
                    holders = mount_holders(self.key_path(key), self._mountinfo(), self._all_mountinfo())
                except RootInvalid as exc:
                    return CacheGcItem(name, "kept", f"cannot judge mounts: {exc}")
                if holders:
                    return CacheGcItem(name, "mounted", ",".join(holders))
                scratch = self._tmp_name(key)
                os.rename(self.key_path(key), os.path.join(self.root, scratch))
                self._remove_entry(scratch)
                return CacheGcItem(name, "removed", f"idle {idle:.0f}s")
            finally:
                if lease_fd is not None:
                    os.close(lease_fd)
        finally:
            os.close(descriptor)

    def survey(self) -> list[dict[str, Any]]:
        """Inventory of keys. Takes no cache lock and creates nothing, so it works on a read-only root; a lease
        probe holds ``.lease`` exclusively for an instant, which a lessee simply retries. ``leased`` is ``None``
        when the lease file cannot be opened by this user."""
        rows: list[dict[str, Any]] = []
        for name in sorted(os.listdir(self.root)):
            if name == _LOCKS:
                continue
            match = _TMP.fullmatch(name)
            key = match.group("key") if match else name
            if _KEY.fullmatch(key) is None:
                rows.append({"name": name, "kind": "foreign"})
            elif match:
                rows.append({"name": name, "kind": "scratch"})
            else:
                document = self._complete(key)
                leased: bool | None = None
                if document:
                    state, lease_fd = self._probe_lease(key)
                    if lease_fd is not None:
                        os.close(lease_fd)
                    leased = {"held": True, "free": False, "none": False}.get(state)
                rows.append({
                    "name": name,
                    "kind": "key" if document else "incomplete",
                    "bytes": document["bytes"] if document else 0,
                    "files": document["files"] if document else 0,
                    "leased": leased,
                    "idle_seconds": round(self._idle_seconds(key), 1) if document else None,
                })
        return rows
