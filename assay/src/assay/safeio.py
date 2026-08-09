"""Bounded descriptor-relative input and single-use output reservations.

P20 freezes this module's public seam.  Reservation metadata is immutable to
callers, while the owned directory descriptor follows one explicit state
machine: RESERVED -> ARMED -> CONSUMED/CLOSED.  That mutable ownership state is
necessary: treating a closed integer file descriptor as an immutable reusable
value can target an unrelated descriptor after the number is reused.

Every path this module opens is walked component-by-component from an already
open ``project_root`` descriptor, using ``dir_fd``-relative, ``O_NOFOLLOW``
operations at every step -- never a whole-path open (``open(str(path))``),
which resolves symlinks/renames along the way and cannot detect a parent
swapped out from under it. Holding the final parent directory open across the
reservation's lifetime is what makes a renamed-and-recreated parent, or a
relinked prior inode, detectable instead of silently followed (P20 JIT
probe).  This module raises nothing but :class:`~assay.errors.AssayError` for
data-shaped refusals; there is no locally-defined exception type here
(A-091/A-092 -- ``errors.py`` is outside this package's ``scope.touch``).
"""

from __future__ import annotations

import os
import stat as stat_module
from pathlib import Path, PurePosixPath
from types import TracebackType
from typing import Self

from .errors import AssayError, Outcome, ReasonCode

__all__ = ["OutputReservation", "read_bounded_file", "reserve_output"]

_OPEN_DIR_FLAGS = os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
_OPEN_READ_FLAGS = os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC


def _refuse(message: str) -> AssayError:
    return AssayError(message, outcome=Outcome.ERROR, reason_code=ReasonCode.UNREADABLE_ARTIFACT)


def _lexical_components(artifact: str) -> tuple[str, ...]:
    """Split *artifact* into POSIX components, refusing every spelling that
    is not a non-empty, project-relative, traversal-free path (module
    docstring's own grammar: no absolute path, empty component, ``.`` or
    ``..``).

    ``PurePosixPath`` itself already collapses an empty component, a bare
    ``.``, and repeated slashes out of ``.parts`` during lexical parsing
    (verified directly: ``PurePosixPath("a/./b").parts == ("a", "b")``) --
    those spellings reach here with nothing left to reject, so only ``..``
    (which ``PurePosixPath`` deliberately does NOT collapse, since doing so
    would change the effective target) needs an explicit check.
    """
    if not artifact:
        raise _refuse("artifact spelling is empty -- a lexical project-relative path is required")
    pure = PurePosixPath(artifact)
    if pure.is_absolute():
        raise _refuse(f"artifact spelling {artifact!r} is absolute -- only a project-relative path is accepted")
    parts = pure.parts
    if not parts:
        raise _refuse(f"artifact spelling {artifact!r} has no path components")
    if ".." in parts:
        raise _refuse(
            f"artifact spelling {artifact!r} contains a '..' component -- "
            f"traversal outside the project root is refused"
        )
    return parts


def _open_root(project_root: Path) -> int:
    try:
        return os.open(os.fspath(project_root), _OPEN_DIR_FLAGS)
    except OSError as exc:
        raise _refuse(f"project root {project_root} is not an openable, non-symlink directory: {exc}") from exc


def _open_child_dir(name: str, *, dir_fd: int) -> int:
    try:
        return os.open(name, _OPEN_DIR_FLAGS, dir_fd=dir_fd)
    except OSError as exc:
        raise _refuse(f"path component {name!r} is not an existing, non-symlink directory: {exc}") from exc


def _open_parent_chain(project_root: Path, parts: tuple[str, ...]) -> int:
    """Open and return a descriptor for the directory that directly contains
    the final component of *parts*, walking every intermediate component
    ``dir_fd``-relative with ``O_NOFOLLOW`` so a symlinked or missing parent
    is refused rather than silently followed or auto-created.
    """
    current = _open_root(project_root)
    try:
        for name in parts[:-1]:
            next_fd = _open_child_dir(name, dir_fd=current)
            os.close(current)
            current = next_fd
    except BaseException:
        os.close(current)
        raise
    return current


def _lstat_relative(basename: str, *, dir_fd: int) -> os.stat_result | None:
    try:
        return os.lstat(basename, dir_fd=dir_fd)
    except FileNotFoundError:
        return None


def _swap_identity(st: os.stat_result) -> tuple[int, int, int]:
    """(device, inode, status-change time) -- used to detect whether a
    PRE-EXISTING object at the reserved path is still the same object
    between :func:`reserve_output` and :meth:`OutputReservation.arm`. Inode
    numbers alone are insufficient: a freed inode can be reused by the very
    next file created at the same path (verified empirically -- an
    unlink-then-rewrite at the same name reused the identical inode number
    on this ext4 filesystem), which would let a real content swap pass this
    check unnoticed if only ``(dev, ino)`` were compared.
    """
    return (st.st_dev, st.st_ino, st.st_ctime_ns)


def _require_safe_pre_existing(basename: str, st: os.stat_result) -> None:
    """A pre-existing object at *basename* must be an ordinary regular file,
    never a symlink or another special type -- the same "reject a
    symlink/special destination" rule the module docstring and the safeio
    skeleton both name.
    """
    if stat_module.S_ISLNK(st.st_mode):
        raise _refuse(f"{basename!r} is a symlink -- refused, never followed")
    if not stat_module.S_ISREG(st.st_mode):
        raise _refuse(f"{basename!r} exists but is not a regular file (mode {oct(st.st_mode)})")


class OutputReservation:
    """A single-owner reservation for one project-relative output.

    ``arm`` is the only operation allowed to unlink the pre-run regular file;
    ``consume`` opens exactly the held parent/basename and consumes the
    reservation once.  Unsafe filesystem objects raise
    ``AssayError(ERROR, UNREADABLE_ARTIFACT)``.  Missing output returns ``None``.
    Invalid state transitions are programmer defects and raise ``RuntimeError``.
    ``close`` is idempotent and never mutates the artifact path.
    """

    def __init__(
        self,
        *,
        artifact: str,
        limit: int,
        parent_fd: int,
        basename: str,
        pre_identity: tuple[int, int, int] | None,
    ) -> None:
        self._artifact = artifact
        self._limit = limit
        self._parent_fd: int | None = parent_fd
        self._basename = basename
        self._pre_identity = pre_identity
        self._armed = False

    @property
    def artifact(self) -> str:
        return self._artifact

    @property
    def limit(self) -> int:
        return self._limit

    def arm(self) -> None:
        """Verify the reserved object is unchanged, then remove it if regular."""
        if self._parent_fd is None:
            raise RuntimeError("reservation already closed")
        if self._armed:
            raise RuntimeError("reservation already armed")
        current = _lstat_relative(self._basename, dir_fd=self._parent_fd)
        current_identity = None if current is None else _swap_identity(current)
        if self._pre_identity is None:
            if current is not None:
                raise _refuse(
                    f"{self._artifact!r} appeared after reservation but before "
                    f"arming -- refusing rather than trusting an object this "
                    f"reservation never observed"
                )
        else:
            if current is None or current_identity != self._pre_identity:
                raise _refuse(
                    f"{self._artifact!r} changed between reservation and arming "
                    f"-- the object this reservation captured is no longer "
                    f"there, so removing whatever now exists could destroy "
                    f"unrelated content"
                )
            _require_safe_pre_existing(self._basename, current)
            os.unlink(self._basename, dir_fd=self._parent_fd)
        self._armed = True

    def consume(self) -> bytes | None:
        """Read at most ``limit + 1`` bytes and consume/close this handle.

        Closes the held descriptor unconditionally (whether the read
        succeeds or raises) before returning, which is also what makes a
        repeated call raise: the SAME ``parent_fd is None`` check
        :meth:`arm`/``close`` already use catches "already consumed" for
        free, since consuming is exactly what nulls it -- a separate
        ``_consumed`` flag would only ever be checked after that null
        check already fired.
        """
        if self._parent_fd is None:
            raise RuntimeError("reservation already closed or consumed")
        if not self._armed:
            raise RuntimeError("reservation not armed")
        parent_fd = self._parent_fd
        try:
            data = _safe_bounded_read(
                self._basename, dir_fd=parent_fd, limit=self._limit, require_single_link=True
            )
        finally:
            os.close(parent_fd)
            self._parent_fd = None
        return data

    def close(self) -> None:
        """Close the owned descriptor without unlinking; safe more than once."""
        if self._parent_fd is not None:
            os.close(self._parent_fd)
            self._parent_fd = None

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()


def _safe_bounded_read(
    basename: str, *, dir_fd: int, limit: int, require_single_link: bool = False
) -> bytes | None:
    """Open *basename* relative to *dir_fd*, nonblocking and no-follow, and
    read at most ``limit + 1`` bytes -- the shared body of
    :meth:`OutputReservation.consume` and :func:`read_bounded_file`.

    Missing returns ``None``. A symlink, FIFO, device, directory or other
    non-regular object, or an oversized read, raises
    ``ERROR``/``UNREADABLE_ARTIFACT``.

    *require_single_link* (``consume``'s own request only) additionally
    refuses an object with more than one hard link. A relinked prior inode
    -- the P20 JIT probe's attack, a hardlink recreating access to exactly
    the content ``arm`` already removed -- is not otherwise distinguishable
    from genuinely fresh output by inode number alone: freeing an inode and
    immediately reusing that same NUMBER for an unrelated fresh file is
    ordinary, observed filesystem behaviour (verified empirically on ext4),
    so testing identity against what was removed produces false positives
    on the common case. Link count does not: a single-owner output this
    reservation itself created has exactly one name; the attack's own
    ``os.link`` is what gives the object a second one.
    """
    try:
        fd = os.open(basename, _OPEN_READ_FLAGS, dir_fd=dir_fd)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise _refuse(f"{basename!r} could not be opened safely: {exc}") from exc
    try:
        st = os.fstat(fd)
        if not stat_module.S_ISREG(st.st_mode):
            raise _refuse(f"{basename!r} is not a regular file (mode {oct(st.st_mode)})")
        if require_single_link and st.st_nlink != 1:
            raise _refuse(
                f"{basename!r} has {st.st_nlink} hard links -- a single-owner "
                f"output must have exactly one name; another link means this "
                f"content is reachable from somewhere this reservation never "
                f"produced"
            )
        chunks: list[bytes] = []
        remaining = limit + 1
        while remaining > 0:
            chunk = os.read(fd, remaining)
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
        if len(data) > limit:
            raise _refuse(f"{basename!r} exceeds the {limit}-byte bound")
        return data
    finally:
        os.close(fd)


def reserve_output(
    project_root: Path,
    artifact: str,
    *,
    limit: int,
) -> OutputReservation:
    """Reserve a lexical project-relative output without mutating it.

    Open ``project_root`` and every existing parent with directory-relative
    ``O_DIRECTORY|O_NOFOLLOW`` operations.  Reject absolute/empty/dot/dotdot
    spellings, missing/symlinked parents, symlink/special destinations, and a
    non-positive limit.  Hold only the final parent descriptor on success.
    """
    if limit <= 0:
        raise ValueError(f"limit must be positive, got {limit}")
    parts = _lexical_components(artifact)
    parent_fd = _open_parent_chain(project_root, parts)
    basename = parts[-1]
    try:
        pre = _lstat_relative(basename, dir_fd=parent_fd)
        pre_identity = None
        if pre is not None:
            _require_safe_pre_existing(basename, pre)
            pre_identity = _swap_identity(pre)
    except BaseException:
        os.close(parent_fd)
        raise
    return OutputReservation(
        artifact=artifact,
        limit=limit,
        parent_fd=parent_fd,
        basename=basename,
        pre_identity=pre_identity,
    )


def read_bounded_file(
    project_root: Path,
    artifact: str,
    *,
    limit: int,
) -> bytes | None:
    """Read an existing project-relative regular file safely, or return None.

    This is the no-unlink sibling used for inputs not produced by the current
    command.  It applies the same no-symlink traversal, ``O_NONBLOCK`` open,
    regular-file ``fstat``, and ``limit + 1`` read ceiling as ``consume``.
    """
    if limit <= 0:
        raise ValueError(f"limit must be positive, got {limit}")
    parts = _lexical_components(artifact)
    parent_fd = _open_parent_chain(project_root, parts)
    try:
        return _safe_bounded_read(parts[-1], dir_fd=parent_fd, limit=limit)
    finally:
        os.close(parent_fd)
