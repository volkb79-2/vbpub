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

import enum
import os
import posixpath
import re
import stat as stat_module
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path, PurePosixPath
from types import MappingProxyType, TracebackType
from typing import Self

from .errors import AssayError, Outcome, ReasonCode
from .records import record

__all__ = [
    "ABSENT",
    "Absent",
    "DirectoryArtifact",
    "DirectoryArtifactReservation",
    "OutputReservation",
    "read_bounded_file",
    "read_bounded_input",
    "reserve_directory_output",
    "reserve_output",
]

_OPEN_DIR_FLAGS = os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
_OPEN_READ_FLAGS = os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC
_PROC_FD_ROOT_RE = re.compile(
    r"/proc/(self|[1-9][0-9]*)/fd/(0|[1-9][0-9]*)(?:/(.*))?\Z"
)


def _refuse(message: str) -> AssayError:
    return AssayError(message, outcome=Outcome.ERROR, reason_code=ReasonCode.UNREADABLE_ARTIFACT)


def _is_proc_fd_root(path: Path | str) -> bool:
    """Whether *path* names an exact proc-fd root or a no-follow child of it."""
    return _PROC_FD_ROOT_RE.fullmatch(os.fspath(path)) is not None


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
    """Open a trusted root, with one explicit Linux descriptor-path form.

    Ordinary roots keep the existing final-component ``O_NOFOLLOW`` check.
    A caller may also name an already-open directory as
    ``/proc/{self|PID}/fd/FD[/child/... ]``.  In that spelling only the
    kernel-owned ``fd/FD`` link is followed; every suffix component is then
    opened relative to the pinned descriptor with ``O_NOFOLLOW``.  This lets
    gate launchers pass an admitted directory descriptor across a subprocess
    boundary without converting it back to a raceable pathname.
    """
    spelling = os.fspath(project_root)
    match = _PROC_FD_ROOT_RE.fullmatch(spelling)
    if match is not None:
        process, descriptor, suffix = match.groups()
        descriptor_path = f"/proc/{process}/fd/{descriptor}"
        try:
            current = os.open(
                descriptor_path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC
            )
        except OSError as exc:
            raise _refuse(
                f"pinned directory root {descriptor_path} is not an openable directory: {exc}"
            ) from exc
        try:
            if not stat_module.S_ISDIR(os.fstat(current).st_mode):
                raise _refuse(f"pinned directory root {descriptor_path} is not a directory")
            if suffix is not None:
                parts = suffix.split("/")
                if any(part in {"", ".", ".."} for part in parts):
                    raise _refuse(
                        f"pinned directory root {spelling!r} has an unsafe suffix"
                    )
                for part in parts:
                    try:
                        next_fd = os.open(part, _OPEN_DIR_FLAGS, dir_fd=current)
                    except OSError as exc:
                        raise _refuse(
                            f"pinned directory root {spelling!r} has a missing, "
                            f"symlinked, or non-directory suffix component {part!r}: {exc}"
                        ) from exc
                    os.close(current)
                    current = next_fd
            return current
        except BaseException:
            os.close(current)
            raise
    try:
        return os.open(spelling, _OPEN_DIR_FLAGS)
    except OSError as exc:
        raise _refuse(f"project root {project_root} is not an openable, non-symlink directory: {exc}") from exc


def _open_child_dir(
    name: str, *, dir_fd: int, artifact: str, create_missing: bool = False
) -> int:
    """Open child directory *name* relative to *dir_fd*, ``O_NOFOLLOW``.

    *create_missing* (B006(b), default ``False``, the module-level default a
    caller must explicitly override -- see :func:`reserve_output`) governs
    ONLY the ``ENOENT`` case. A component that exists as anything other than
    an openable, non-symlink directory (a symlink, a regular file, a FIFO)
    refuses exactly as it always has: creation is never attempted, and never
    trusted to substitute, for a component that is already something else.

    When *create_missing* is set and the component is genuinely absent, the
    directory is created ``dir_fd``-relative (``mkdir`` at *dir_fd*, mode
    ``0o700``) and then REOPENED with the identical ``O_DIRECTORY|O_NOFOLLOW``
    flags used for a pre-existing component -- never trusted blind. This is
    the discipline that stops ``os.makedirs``-style recursion from ever
    following a symlink: if a race replaces the just-created name with a
    symlink before the reopen, the reopen's own ``O_NOFOLLOW`` refuses it
    exactly as it would a pre-existing one.
    """
    try:
        return os.open(name, _OPEN_DIR_FLAGS, dir_fd=dir_fd)
    except FileNotFoundError as exc:
        if not create_missing:
            raise _refuse(
                f"declared artifact {artifact!r}: parent component {name!r} "
                f"does not exist -- the parent could not be opened (missing, "
                f"and creation was not requested)"
            ) from exc
        try:
            os.mkdir(name, 0o700, dir_fd=dir_fd)
            return os.open(name, _OPEN_DIR_FLAGS, dir_fd=dir_fd)
        except OSError as create_exc:
            raise _refuse(
                f"declared artifact {artifact!r}: parent component {name!r} "
                f"could not be created or opened: {create_exc}"
            ) from create_exc
    except OSError as exc:
        raise _refuse(
            f"declared artifact {artifact!r}: parent component {name!r} is "
            f"not an existing, non-symlink directory: {exc}"
        ) from exc


def _open_parent_chain(
    project_root: Path,
    parts: tuple[str, ...],
    *,
    artifact: str,
    create_missing: bool = False,
) -> int:
    """Open and return a descriptor for the directory that directly contains
    the final component of *parts*, walking every intermediate component
    ``dir_fd``-relative with ``O_NOFOLLOW`` so a symlinked or missing parent
    is refused rather than silently followed.

    *create_missing* (default ``False``, B006(b)) additionally ``mkdir``s a
    genuinely absent intermediate component -- see :func:`_open_child_dir`
    for the exact discipline. It never changes how a component that exists
    as the WRONG thing is handled: that is still always a refusal.
    """
    current = _open_root(project_root)
    try:
        for name in parts[:-1]:
            next_fd = _open_child_dir(
                name, dir_fd=current, artifact=artifact, create_missing=create_missing
            )
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


def _refuse_if_held_directory_replaced(held_fd: int, *, artifact: str, where: str) -> None:
    """(B049/A-408) Refuse a reservation whose HELD directory the lane's own
    tool deleted and recreated during the run.

    :func:`reserve_output` opens the artifact's parent directory ONCE,
    before the lane's command starts, and the reservation holds that
    descriptor for the command's whole execution; ``consume`` then opens the
    basename relative to it. A tool that does ``rm -rf <dir> && mkdir <dir>
    && write <artifact>`` -- Vitest's own default ``coverage.clean = true``,
    and a common convention well beyond it -- writes into a DIFFERENT inode
    that merely answers to the same path. The held descriptor still refers
    to the orphaned original, whose directory entry is gone, so the basename
    lookup raises ``FileNotFoundError`` and ``_safe_bounded_read`` returns
    ``None`` -- which every caller reads as "the command wrote nothing at
    all" (``NO_MEASUREMENT``/``EMPTY_COVERAGE`` for a coverage read, a
    ``crashed`` mutant for :mod:`assay.mutation`'s absent read). Each of
    those messages asserts a checkable falsehood over an artifact that was
    complete on disk at the declared path the whole time.

    ``st_nlink == 0`` on the already-held descriptor is the exact, name-free
    witness for that state: an unlinked directory inode keeps its own open
    descriptor alive with a zero link count even though a NEW inode now
    answers to the same path (measured on this filesystem: held fd
    ``nlink=0``, recreated directory a different ``st_ino``). Reading it
    costs one ``fstat`` on a descriptor the reservation already owns, so --
    unlike re-opening the parent chain by NAME (option (1) in B049) -- it
    adds no TOCTOU surface at all.

    *artifact* names the declared output in the message and *where* is the
    already-quoted description of the held directory (``repr`` of its path,
    or "the project root"). The refusal is ``ERROR``/``UNREADABLE_ARTIFACT``,
    which ``assay.coverage``'s own vocabulary note already reserves for "an
    object that exists but cannot be trusted ... or a race the caller's own
    reservation already detected". No new reason code, no schema change
    (A-138/A-170).

    A directory that was RENAMED away (not unlinked) keeps ``st_nlink`` above
    zero, so this check does not see it; :class:`DirectoryArtifactReservation`
    adds a by-name identity comparison for that case.
    """
    st = os.fstat(held_fd)
    if st.st_nlink != 0:
        return
    raise _refuse(
        f"{artifact!r}: the directory {where} that assay reserved "
        f"this output in was deleted and recreated while the lane's "
        f"command ran, so assay's reservation was orphaned and the "
        f"artifact your tool actually wrote is unreachable through it. "
        f"Turn the tool's clean/rm-first option off (for example "
        f"Vitest's `coverage.clean = false`) or have it write into the "
        f"existing directory instead of replacing it."
    )


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

        (B049/A-408) Before reading, the HELD parent descriptor is
        ``fstat``'d and refused when its link count has fallen to zero:
        that is a tool which deleted and recreated its own output
        directory while the reservation was open. See
        :meth:`_refuse_if_parent_was_replaced`.
        """
        if self._parent_fd is None:
            raise RuntimeError("reservation already closed or consumed")
        if not self._armed:
            raise RuntimeError("reservation not armed")
        parent_fd = self._parent_fd
        try:
            self._refuse_if_parent_was_replaced(parent_fd)
            data = _safe_bounded_read(
                self._basename, dir_fd=parent_fd, limit=self._limit, require_single_link=True
            )
        finally:
            os.close(parent_fd)
            self._parent_fd = None
        return data

    def _refuse_if_parent_was_replaced(self, parent_fd: int) -> None:
        """(B049/A-408) Refuse a reservation whose parent directory the
        lane's own tool deleted and recreated during the run -- see
        :func:`_refuse_if_held_directory_replaced`, the shared module
        function that holds the rule and its rationale.
        """
        parent = posixpath.dirname(self._artifact)
        _refuse_if_held_directory_replaced(
            parent_fd,
            artifact=self._artifact,
            where=repr(parent) if parent else "the project root",
        )

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
    create_missing_parents: bool = False,
) -> OutputReservation:
    """Reserve a lexical project-relative output without mutating it.

    Open ``project_root`` and every existing parent with directory-relative
    ``O_DIRECTORY|O_NOFOLLOW`` operations.  Reject absolute/empty/dot/dotdot
    spellings, missing/symlinked parents, symlink/special destinations, and a
    non-positive limit.  Hold only the final parent descriptor on success.

    *create_missing_parents* (B006(b)) defaults to ``False`` -- the CONTRACT,
    not an implementation accident. Only an explicit, opt-in ``True`` from a
    call site that KNOWS it owns an ephemeral, assay-managed snapshot (never
    the caller's real worktree) may set it. Left at its default, a missing
    parent refuses exactly as it always has: this is what keeps an R0/direct
    (in-place) caller's behaviour unchanged even after this capability ships.
    When ``True``, each missing parent component is created descriptor-
    relative (``mkdir`` at ``dir_fd``, mode ``0o700``) and reopened with the
    same ``O_DIRECTORY|O_NOFOLLOW`` used for a pre-existing one -- see
    :func:`_open_child_dir`. A component that already exists as a symlink or
    any non-directory is refused exactly as before; creation only ever fires
    on genuine absence, never as a substitute for something already there.
    """
    if limit <= 0:
        raise ValueError(f"limit must be positive, got {limit}")
    parts = _lexical_components(artifact)
    parent_fd = _open_parent_chain(
        project_root, parts, artifact=artifact, create_missing=create_missing_parents
    )
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
    parent_fd = _open_parent_chain(project_root, parts, artifact=artifact)
    try:
        return _safe_bounded_read(parts[-1], dir_fd=parent_fd, limit=limit)
    finally:
        os.close(parent_fd)


def _open_parent_chain_or_missing(project_root: Path, parts: tuple[str, ...]) -> int | None:
    """Like :func:`_open_parent_chain`, but ``ENOENT`` at any intermediate
    component returns ``None`` instead of raising.

    A declared attestation's directory may simply never have been created by
    its producer -- that is the same absence as a missing final file, not an
    unsafe object that happens to be present (A-210). Every OTHER failure
    (a symlinked or non-directory component, a permission error, a race)
    still raises exactly as :func:`_open_parent_chain` does.
    """
    current = _open_root(project_root)
    for name in parts[:-1]:
        try:
            next_fd = os.open(name, _OPEN_DIR_FLAGS, dir_fd=current)
        except FileNotFoundError:
            os.close(current)
            return None
        except OSError as exc:
            os.close(current)
            raise _refuse(
                f"path component {name!r} is not an existing, non-symlink directory: {exc}"
            ) from exc
        os.close(current)
        current = next_fd
    return current


def read_bounded_input(
    project_root: Path, relative_path: str, *, limit: int
) -> bytes | None:
    """Descriptor-walk one input; ``ENOENT`` anywhere is ``None``, never a
    guess (P26/A-210).

    *relative_path* is a lexical, project-relative POSIX spelling -- the same
    grammar :func:`read_bounded_file`/:func:`reserve_output` already enforce
    (no absolute path, no empty/``.``/``..`` component). Absence of any
    directory component in the walk, or of the final name itself, returns
    ``None``: the declared producer supplied no record. A symlink, a
    non-directory parent, a non-regular final object, a permission failure, a
    race, or a read exceeding *limit* raises
    ``AssayError(ERROR, UNREADABLE_ARTIFACT)`` -- present, but untrustworthy.
    Never a pathname precheck followed by a second open: every step walks an
    already-open ``dir_fd`` with ``O_NOFOLLOW``.
    """
    if limit <= 0:
        raise ValueError(f"limit must be positive, got {limit}")
    parts = _lexical_components(relative_path)
    parent_fd = _open_parent_chain_or_missing(project_root, parts)
    if parent_fd is None:
        return None
    try:
        return _safe_bounded_read(parts[-1], dir_fd=parent_fd, limit=limit)
    finally:
        os.close(parent_fd)


# --- directory-artifact reservation (B245 / U1) --------------------------------


class Absent(enum.Enum):
    """The one value :meth:`DirectoryArtifactReservation.consume` returns,
    rather than raises, when the tool wrote nothing at the declared child.

    A distinguished value and not ``None``: "the child is absent" is a fact the
    caller has to decide on (a zero-mutant run), and it must never be confusable
    with an unset or never-consumed reservation.
    """

    ABSENT = "absent"


ABSENT = Absent.ABSENT


@record
class DirectoryArtifact:
    """What a consumed :class:`DirectoryArtifactReservation` read, as bytes.

    *artifact* is the project-relative path of the child directory,
    *identity* its ``(st_dev, st_ino)`` as opened, *report* the bytes of its
    report file, and *members* the bytes of each declared member keyed by the
    name the *members* callable returned (``diff/<name>.diff``), in sorted key
    order.
    """

    artifact: str
    identity: tuple[int, int]
    report: bytes
    members: Mapping[str, bytes]


def _identity(st: os.stat_result) -> tuple[int, int]:
    return (st.st_dev, st.st_ino)


def _require_component(label: str, name: str) -> None:
    """A programmer-supplied single path component (never data-shaped)."""
    if not name or name in {".", ".."} or "/" in name or "\0" in name:
        raise ValueError(f"{label} must be one non-empty path component, got {name!r}")


def _lstat_or_refuse(name: str, *, dir_fd: int, artifact: str) -> os.stat_result | None:
    """:func:`_lstat_relative` for the directory reservation: absence is still
    ``None``, but any OTHER ``OSError`` (``EACCES`` on a directory the command
    chmod'ed after the descriptor was opened, ``ESTALE``, ...) is a data-shaped
    ``ERROR``/``UNREADABLE_ARTIFACT`` refusal, never a raw exception.
    :class:`OutputReservation` keeps its own behaviour and does not use this."""
    try:
        return _lstat_relative(name, dir_fd=dir_fd)
    except OSError as exc:
        raise _refuse(f"{artifact!r}: {name!r} could not be inspected: {exc}") from exc


def _is_strict_utf8(text: str) -> bool:
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def _member_basename(name: object) -> str:
    """Return the component of a ``diff/<name>`` member, or refuse.

    The grammar (B245 design section 8 item 3): exactly ``diff/<name>`` with
    ``<name>`` one non-empty component ending ``.diff`` and holding no NUL, ``/``
    or ``\\``. Ending in ``.diff`` already excludes ``.`` and ``..``. Any other
    character, non-ASCII included, is safe because the member is opened by NAME
    through a held directory descriptor and never globbed or joined into a path.

    A non-``str`` name refuses, and so does a ``str`` that is not strict UTF-8
    (a lone surrogate, which JSON can spell as ``"\\ud800"``): ``os.open`` would
    raise ``UnicodeEncodeError`` for it, or, for a ``surrogateescape`` spelling
    such as ``"\\udcff"``, silently open the file whose name holds the raw byte
    ``0xff``, so two distinct strings could alias one file and defeat the
    duplicate check. Requiring a strict UTF-8 round trip makes distinct names
    distinct byte strings by construction.
    """
    prefix, separator, base = name.partition("/") if isinstance(name, str) else ("", "", "")
    if (
        prefix != "diff"
        or not separator
        or not base.endswith(".diff")
        or any(character in base for character in ("/", "\\", "\0"))
        or not _is_strict_utf8(base)
    ):
        raise _refuse(
            f"member name {name!r} is not exactly 'diff/<name>.diff' with one "
            f"path component, no NUL, '/' or backslash, and strict UTF-8 text"
        )
    return base


class DirectoryArtifactReservation:
    """A single-owner reservation for a DIRECTORY artifact a tool writes into
    a directory assay holds (B245 / U1): RESERVED -> ARMED -> CONSUMED/CLOSED.

    The held descriptor is the reserved ``<out>`` directory itself, never the
    *child* the tool creates inside it, because a tool such as cargo-mutants
    renames a pre-existing ``child`` to *rotated* and a held ``child`` would
    follow the rotated inode. ``<out>`` is empty when reserved and nothing is
    ever written into it or unlinked from it by this object, so a pre-existing
    *child* or *rotated* is never accepted (it is refused at reserve and again
    at arm), and only an empty directory is ever copied into a tool's own work
    tree. :meth:`consume` RAISES ``AssayError(ERROR, UNREADABLE_ARTIFACT)`` on
    every refusal and RETURNS :data:`ABSENT` when *child* is absent; this module
    names no outcome state beyond that (the runner owns the rest).
    ``close`` is idempotent and never mutates the filesystem. Invalid state
    transitions are programmer defects and raise ``RuntimeError``.
    """

    def __init__(
        self,
        *,
        project_root: Path,
        out_dir: str,
        out_parts: tuple[str, ...],
        held_fd: int,
        out_identity: tuple[int, int],
        child: str,
        rotated: str,
        report_name: str,
        report_limit: int,
        member_limit: int,
        max_members: int,
        max_total_bytes: int,
    ) -> None:
        self._project_root = project_root
        self._out_dir = out_dir
        self._out_parts = out_parts
        self._held_fd: int | None = held_fd
        self._out_identity = out_identity
        self._child = child
        self._rotated = rotated
        self._report_name = report_name
        self._report_limit = report_limit
        self._member_limit = member_limit
        self._max_members = max_members
        self._max_total_bytes = max_total_bytes
        self._armed = False

    @property
    def artifact(self) -> str:
        """The project-relative path of the child directory."""
        return "/".join((*self._out_parts, self._child))

    def _lstat(self, name: str, held_fd: int) -> os.stat_result | None:
        return _lstat_or_refuse(name, dir_fd=held_fd, artifact=self._out_dir)

    def _refuse_if_child_or_rotated_present(self, held_fd: int, *, when: str) -> None:
        for name in (self._child, self._rotated):
            if self._lstat(name, held_fd) is not None:
                raise _refuse(
                    f"{'/'.join((*self._out_parts, name))!r} {when} -- assay reserved "
                    f"{self._out_dir!r} empty and never accepts an object it did not "
                    f"observe absent, because the tool would rotate or reuse it"
                )

    def _refuse_if_not_empty(self, held_fd: int, *, when: str) -> None:
        """Only an EMPTY ``<out>`` may exist when the command starts: the tool
        copies it into every mutant tree, where a test could read what is in it.
        Hidden entries count; this lists the held descriptor, never a path."""
        try:
            entries = os.listdir(held_fd)
        except OSError as exc:
            raise _refuse(f"{self._out_dir!r} could not be listed: {exc}") from exc
        if entries:
            raise _refuse(
                f"{self._out_dir!r} is not empty {when} -- only an empty directory is "
                f"ever reserved, because it is copied into the tool's work tree and a "
                f"test could read what is in it"
            )

    def arm(self) -> None:
        """Repeat the absence and emptiness checks of reserve time; unlinks
        nothing."""
        if self._held_fd is None:
            raise RuntimeError("reservation already closed")
        if self._armed:
            raise RuntimeError("reservation already armed")
        self._refuse_if_child_or_rotated_present(
            self._held_fd, when="appeared after reservation but before arming"
        )
        self._refuse_if_not_empty(self._held_fd, when="at arming")
        self._armed = True

    def _refuse_if_out_was_renamed(self) -> None:
        """Re-walk ``<out>`` by NAME and compare it with the held inode.

        :func:`_refuse_if_held_directory_replaced` sees a deleted directory
        (``st_nlink == 0``) but not one that was RENAMED away and replaced:
        that inode keeps a link count above zero. Only the by-name comparison
        catches it, so the artifact the tool wrote at the declared path would
        otherwise be read through an orphaned descriptor.
        """
        parent_fd = _open_parent_chain(
            self._project_root, self._out_parts, artifact=self._out_dir
        )
        try:
            current = _lstat_or_refuse(
                self._out_parts[-1], dir_fd=parent_fd, artifact=self._out_dir
            )
        finally:
            os.close(parent_fd)
        if current is None or _identity(current) != self._out_identity:
            raise _refuse(
                f"{self._out_dir!r} was renamed away or replaced while the lane's "
                f"command ran, so the directory assay reserved is not the one now "
                f"at that path -- refusing to read through the orphaned reservation"
            )

    def consume(self, *, members: Callable[[bytes], Sequence[str]]) -> DirectoryArtifact | Absent:
        """Read the child directory once and close the reservation.

        *members* maps the report's BYTES to the member names to read, each
        exactly ``diff/<name>.diff`` (see :func:`_member_basename`).

        Every refusal raises ``AssayError(ERROR, UNREADABLE_ARTIFACT)``. Every
        descriptor opened here is closed in ``finally``, and so is the held
        one, which is also what makes a second call raise ``RuntimeError``.
        """
        if self._held_fd is None:
            raise RuntimeError("reservation already closed or consumed")
        if not self._armed:
            raise RuntimeError("reservation not armed")
        held_fd = self._held_fd
        child_fd: int | None = None
        try:
            _refuse_if_held_directory_replaced(
                held_fd, artifact=self.artifact, where=repr(self._out_dir)
            )
            self._refuse_if_out_was_renamed()
            if self._lstat(self._rotated, held_fd) is not None:
                raise _refuse(
                    f"{'/'.join((*self._out_parts, self._rotated))!r} exists after the "
                    f"run -- the tool rotated a {self._child!r} that was absent at "
                    f"arming, so the command ran twice or something else wrote there"
                )
            child_stat = self._lstat(self._child, held_fd)
            if child_stat is None:
                return ABSENT
            if not stat_module.S_ISDIR(child_stat.st_mode):
                raise _refuse(
                    f"{self.artifact!r} is not a directory (mode {oct(child_stat.st_mode)}) "
                    f"-- a symlink or other object is refused, never followed"
                )
            try:
                child_fd = os.open(self._child, _OPEN_DIR_FLAGS, dir_fd=held_fd)
            except OSError as exc:
                raise _refuse(f"{self.artifact!r} could not be opened safely: {exc}") from exc
            identity = _identity(os.fstat(child_fd))
            if identity != _identity(child_stat):
                raise _refuse(
                    f"{self.artifact!r} was swapped between inspection and opening"
                )
            report = _safe_bounded_read(
                self._report_name,
                dir_fd=child_fd,
                limit=self._report_limit,
                require_single_link=True,
            )
            if report is None:
                raise _refuse(
                    f"{self.artifact!r} has no {self._report_name!r} -- the tool "
                    f"died mid-run or wrote an incomplete directory"
                )
            files = self._read_members(child_fd, members(report))
            final = self._lstat(self._child, held_fd)
            if final is None or _identity(final) != identity:
                raise _refuse(f"{self.artifact!r} was replaced while it was being read")
            return DirectoryArtifact(
                artifact=self.artifact,
                identity=identity,
                report=report,
                members=MappingProxyType(files),
            )
        finally:
            if child_fd is not None:
                os.close(child_fd)
            os.close(held_fd)
            self._held_fd = None

    def _read_members(self, child_fd: int, names: Sequence[str]) -> dict[str, bytes]:
        """Read every member of ``diff/`` by name, bounded in count, per-member
        size and aggregate size; refuse a duplicate, missing or unsafe one."""
        if len(names) > self._max_members:
            raise _refuse(
                f"{self.artifact!r} names {len(names)} members, above the "
                f"{self._max_members}-member bound"
            )
        bases: dict[str, str] = {}
        for name in names:
            base = _member_basename(name)  # first: a non-str name must not reach a hash
            if name in bases:
                raise _refuse(f"member {name!r} is named twice")
            bases[name] = base
        if not bases:
            return {}
        try:
            diff_fd = os.open("diff", _OPEN_DIR_FLAGS, dir_fd=child_fd)
        except OSError as exc:
            raise _refuse(f"{self.artifact!r}/diff could not be opened safely: {exc}") from exc
        try:
            collected: dict[str, bytes] = {}
            total = 0
            for name, base in bases.items():
                data = _safe_bounded_read(
                    base, dir_fd=diff_fd, limit=self._member_limit, require_single_link=True
                )
                if data is None:
                    raise _refuse(f"member {name!r} is named by the report but missing")
                total += len(data)
                if total > self._max_total_bytes:
                    raise _refuse(
                        f"members of {self.artifact!r} exceed the "
                        f"{self._max_total_bytes}-byte aggregate bound"
                    )
                collected[name] = data
        finally:
            os.close(diff_fd)
        return dict(sorted(collected.items()))

    def close(self) -> None:
        """Close the owned descriptor without unlinking; safe more than once."""
        if self._held_fd is not None:
            os.close(self._held_fd)
            self._held_fd = None


def reserve_directory_output(
    project_root: Path,
    out_dir: str,
    *,
    child: str,
    rotated: str,
    report_name: str,
    report_limit: int,
    member_limit: int,
    max_members: int,
    max_total_bytes: int,
    create_missing_parents: bool = False,
) -> DirectoryArtifactReservation:
    """Reserve a lexical project-relative directory *out_dir* that a tool will
    write the directory *child* (with *report_name* and ``diff/*.diff``) into.

    *out_dir* itself is held open, not *child*. It is created empty (0700) only
    when *create_missing_parents* is true, exactly as in :func:`reserve_output`
    (B006(b)), and otherwise must already exist, and EMPTY: a non-empty
    pre-existing *out_dir* is refused, because only an empty directory may ever
    be copied into the tool's work tree. *child* and *rotated* must both be
    absent, and *child* is never created. Non-positive limits and a *child*,
    *rotated* or *report_name* that is not a single path component are
    programmer defects (``ValueError``).
    """
    for label, bound in (
        ("report_limit", report_limit),
        ("member_limit", member_limit),
        ("max_members", max_members),
        ("max_total_bytes", max_total_bytes),
    ):
        if bound <= 0:
            raise ValueError(f"{label} must be positive, got {bound}")
    for label, name in (("child", child), ("rotated", rotated), ("report_name", report_name)):
        _require_component(label, name)
    parts = _lexical_components(out_dir)
    parent_fd = _open_parent_chain(
        project_root, parts, artifact=out_dir, create_missing=create_missing_parents
    )
    try:
        held_fd = _open_child_dir(
            parts[-1], dir_fd=parent_fd, artifact=out_dir, create_missing=create_missing_parents
        )
    finally:
        os.close(parent_fd)
    reservation = DirectoryArtifactReservation(
        project_root=project_root,
        out_dir=out_dir,
        out_parts=parts,
        held_fd=held_fd,
        out_identity=_identity(os.fstat(held_fd)),
        child=child,
        rotated=rotated,
        report_name=report_name,
        report_limit=report_limit,
        member_limit=member_limit,
        max_members=max_members,
        max_total_bytes=max_total_bytes,
    )
    try:
        reservation._refuse_if_child_or_rotated_present(
            held_fd, when="already exists at reservation"
        )
        reservation._refuse_if_not_empty(held_fd, when="at reservation")
    except BaseException:
        reservation.close()
        raise
    return reservation
