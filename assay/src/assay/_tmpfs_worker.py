"""(B223) Per-worker memory-backed candidate trees.

The default materialization hands every candidate a fresh copy-on-write clone
of the sealed template.  That clone is a new inode set, so its page cache starts
cold and every candidate re-reads the repository from disk.  ``tmpfs-worker``
instead gives each mutation worker ONE tree on a memory-backed directory,
populated once from the sealed template, and restores only what a candidate
touched: the replaced file, the index, ``HEAD`` and the loose objects the
replacement commit added.

This module holds the pieces that do not need the snapshot repository: the
tmpfs capacity proof, the template copy, and :class:`WorkerTree` (the pristine
facts, the restore and the residue scan).  ``isolation.SnapshotRepository``
owns the lifecycle and the Git calls.

Reuse in place has one hazard a fresh clone does not: a candidate can leave
residue (a written ignored file, a changed byte) that the NEXT candidate would
inherit.  :meth:`WorkerTree.settle` is the guard.  Anything the candidate
added is removed; anything it changed or deleted makes the tree untrustworthy
and the caller discards it and rebuilds from the template.
"""

from __future__ import annotations

import os
import shutil
import stat
from pathlib import Path

_TMPFS_TYPES = frozenset({"tmpfs", "ramfs"})
_GIT_STATE = frozenset({".git/index", ".git/HEAD"})
#: Headroom over the template's own size (a quarter) plus a constant for the
#: candidate's transient writes.
_HEADROOM_FRACTION = 4
_HEADROOM_CONSTANT = 16 * 1024 * 1024


class TmpfsUnavailable(Exception):
    """The memory-backed directory cannot host one more worker tree."""


def _mount_type(path: str, mounts_file: str) -> str | None:
    """Filesystem type of the longest mount point covering *path*."""
    best = -1
    found: str | None = None
    with open(mounts_file, encoding="utf-8", errors="replace") as handle:
        for line in handle:
            fields = line.split()
            if len(fields) < 3:
                continue
            mount = fields[1].replace("\\040", " ")
            if (
                path == mount
                or mount == "/"
                or path.startswith(mount.rstrip("/") + "/")
            ) and len(mount) > best:
                best = len(mount)
                found = fields[2]
    return found


def check_capacity(
    directory: Path,
    need_bytes: int,
    *,
    max_bytes: int,
    mounts_file: str = "/proc/self/mounts",
) -> None:
    """Raise :class:`TmpfsUnavailable` unless *directory* can hold *need_bytes*."""
    try:
        resolved = os.path.realpath(directory)
        fstype = _mount_type(resolved, mounts_file)
        stats = os.statvfs(resolved)
    except OSError as exc:
        raise TmpfsUnavailable(f"{directory} cannot be inspected: {exc}") from exc
    if fstype not in _TMPFS_TYPES:
        raise TmpfsUnavailable(
            f"{directory} is on a {fstype or 'unknown'} filesystem, not tmpfs"
        )
    if need_bytes > max_bytes:
        raise TmpfsUnavailable(
            f"a worker tree needs {need_bytes} bytes, above the declared "
            f"tmpfs_max_mib bound of {max_bytes}"
        )
    free = stats.f_bavail * stats.f_frsize
    required = need_bytes + need_bytes // _HEADROOM_FRACTION + _HEADROOM_CONSTANT
    if free < required:
        raise TmpfsUnavailable(
            f"{directory} has {free} bytes free, a worker tree needs {required}"
        )


def tree_bytes(root: Path) -> int:
    """Total size of the regular files below *root* (links are not followed)."""
    total = 0
    for current, _dirs, files in os.walk(root):
        for name in files:
            total += os.lstat(os.path.join(current, name)).st_size
    return total


def copy_template(template: Path, destination: Path) -> None:
    """Copy the sealed template, preserving modes and mtimes (inodes differ).

    A template sealed by removing write permission (a backend without a
    read-only property) would otherwise yield an unwritable worker tree, so
    the owner write bit is added wherever it is missing.  Nothing changes for
    a template whose modes are already writable.
    """
    shutil.copytree(template, destination, symlinks=True)
    for current, directories, files in os.walk(destination):
        for name in [".", *directories, *files]:
            member = os.path.join(current, name)
            if os.path.islink(member):
                continue
            mode = os.lstat(member).st_mode
            if not mode & stat.S_IWUSR:
                os.chmod(member, mode | stat.S_IWUSR)


def _scan(root: Path):
    """Yield ``(relative, signature, is_dir)`` for every entry below *root*.

    A signature is what a restored tree must still equal: type, permission
    bits, size, mtime and link target.  Directory mtimes are excluded (a
    deletion moves them), and so are the loose-object directories' contents
    and the two Git state files, which are restored and compared separately.
    """
    pending = [""]
    while pending:
        prefix = pending.pop()
        with os.scandir(os.path.join(root, prefix)) as entries:
            for entry in entries:
                relative = f"{prefix}/{entry.name}" if prefix else entry.name
                if relative in _GIT_STATE:
                    continue
                if relative.startswith(".git/objects/") and relative.count("/") >= 3:
                    continue
                signature = _signature(entry.stat(follow_symlinks=False), entry.path)
                yield relative, signature, signature[0] == "d"
                if signature[0] == "d":
                    pending.append(relative)


def _signature(status: os.stat_result, path: str | os.PathLike[str]):
    """What a restored entry must still equal (see :func:`_scan`)."""
    if stat.S_ISDIR(status.st_mode):
        return ("d", stat.S_IMODE(status.st_mode))
    if stat.S_ISLNK(status.st_mode):
        return ("l", os.readlink(path))
    return ("f", stat.S_IMODE(status.st_mode), status.st_size, status.st_mtime_ns)


def _repair(root: Path, template: Path, relative: str, signature) -> bool:
    """Restore one entry to *signature* from the shared *template*.

    A file is unlinked and rewritten (never opened in place, so an overlay
    slot records a whole new upper file rather than copying the lower one up
    first), then given the template's mode and mtime.  ``True`` only when the
    entry reads back equal to *signature*.
    """
    target = root / relative
    try:
        current = os.lstat(target)
    except FileNotFoundError:
        current = None
    kind = signature[0]
    if current is not None and not (kind == "d" and stat.S_ISDIR(current.st_mode)):
        if stat.S_ISDIR(current.st_mode):
            shutil.rmtree(target)
        else:
            target.unlink()
        current = None
    if kind == "d":
        if current is None:
            target.mkdir()
        os.chmod(target, signature[1])
    elif kind == "l":
        os.symlink(signature[1], target)
    else:
        source = template / relative
        if not stat.S_ISREG(os.lstat(source).st_mode):
            return False
        shutil.copyfile(source, target)
        os.chmod(target, signature[1])
        os.utime(target, ns=(signature[3], signature[3]))
    return _signature(os.lstat(target), target) == signature


def _loose_objects(git_dir: Path) -> frozenset[str]:
    found = set()
    objects = git_dir / "objects"
    for fan in os.listdir(objects):
        if len(fan) == 2:
            for name in os.listdir(objects / fan):
                found.add(f"{fan}/{name}")
    return frozenset(found)


class WorkerTree:
    """One worker's long-lived tree and the pristine facts it is restored to."""

    def __init__(
        self,
        root: Path,
        *,
        template: Path | None = None,
        owned: bool = True,
        rung: str = "B",
    ) -> None:
        self.root = root
        self.git_dir = root / ".git"
        #: The shared template an unclean entry is repaired from (``None``
        #: keeps the pre-B230 rule: the tree is discarded).
        self.template = template
        #: ``False`` for a caller-provided overlay slot: its directory is the
        #: caller's mount, so it is never removed, only abandoned.
        self.owned = owned
        self.rung = rung
        self.busy = False
        #: The first candidate on a fresh tree runs the complete clone proof.
        self.fresh = True
        self._index = (self.git_dir / "index").read_bytes()
        self._head = (self.git_dir / "HEAD").read_bytes()
        self._loose = _loose_objects(self.git_dir)
        self._map = {relative: sig for relative, sig, _ in _scan(root)}

    def settle(
        self,
        *,
        path: Path,
        original: bytes,
        executable: bool,
        fixed_mtime: int,
        planted: tuple[Path, ...],
    ) -> bool:
        """Restore the tree after a candidate; ``False`` means discard it.

        Restores the replaced file (bytes, mode, mtime), the index and
        ``HEAD`` from the pristine copies, removes the planted ``link_paths``
        and every loose object the candidate added, then scans the whole tree
        against the pristine map.  Entries the candidate ADDED are removed
        (the post-command dirt check already judged them for the candidate);
        a changed or missing entry, or a state file that does not read back
        equal, returns ``False``.
        """
        for link in planted:
            if os.path.islink(link):
                os.unlink(link)
        path.unlink()
        path.write_bytes(original)
        path.chmod(0o755 if executable else 0o644)
        os.utime(path, (fixed_mtime, fixed_mtime))
        (self.git_dir / "index").write_bytes(self._index)
        (self.git_dir / "HEAD").write_bytes(self._head)
        objects = self.git_dir / "objects"
        for name in _loose_objects(self.git_dir) - self._loose:
            os.unlink(objects / name)
        seen = set()
        extra = []
        for relative, signature, is_dir in _scan(self.root):
            known = self._map.get(relative)
            if known is None:
                extra.append((relative, is_dir))
            elif known == signature:
                seen.add(relative)
        for relative, is_dir in extra:
            target = self.root / relative
            if is_dir:
                shutil.rmtree(target, ignore_errors=True)
            else:
                target.unlink(missing_ok=True)
        clean = True
        # Changed or missing entries: repaired from the shared template when
        # there is one (parents before children), else the tree is unclean.
        for relative in sorted(self._map.keys() - seen, key=lambda r: r.count("/")):
            if self.template is None or not _repair(
                self.root, self.template, relative, self._map[relative]
            ):
                clean = False
                break
        return (
            clean
            and (self.git_dir / "index").read_bytes() == self._index
            and (self.git_dir / "HEAD").read_bytes() == self._head
        )
