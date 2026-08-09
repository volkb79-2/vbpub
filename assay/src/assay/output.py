"""Reserved, atomic verdict output.

P21 owns this boundary. A caller reserves before any lane command and emits
exactly once afterward. File targets are interpreted in the CLI process
namespace, never relative to the lane's project root.

Three things this module refuses to be (A-181, each a defect the JIT review
reproduced or named before it could ship):

* **A path-only readiness check.** ``os.access``/``Path.parent.exists()``
  answers a question about a *name*, and the answer is stale the moment it
  returns. Reservation instead HOLDS the opened parent directory and the
  destination's observed identity, and proves write access by creating and
  immediately removing an exclusive sibling probe. Every later operation is
  performed relative to the held descriptor, so a parent swapped underneath
  us is refused rather than silently followed.
* **A persistent pre-run temporary.** A temp file created at reservation and
  kept until emission is observable to the lane's own command and, when the
  verdict target lives inside the consumer's repository, is itself untracked
  worktree state that makes P20's dirt guard refuse the run. The probe is
  created and unlinked inside :func:`reserve_verdict_output`; nothing of ours
  exists on disk across consumer execution.
* **A destination overwriter.** Emission revalidates the identity recorded at
  reservation. An object that APPEARED (the destination was absent and now is
  not) or CHANGED (different inode/type/size/mtime) is preserved untouched
  and the emission refuses -- assay never destroys something it did not
  observe, and never invents a fallback path to write to instead (A-028: no
  uninvited writes).

Every expected I/O refusal is ``AssayError(ERROR, OUTPUT_WRITE_FAILED)``.
Invalid state transitions are programmer errors and raise ``RuntimeError``:
they are bugs in a caller, not verdicts about a consumer's filesystem, and
laundering them into a typed outcome would let a real defect ship as an
ordinary artifact-write failure.
"""

from __future__ import annotations

import os
import stat
from typing import TextIO

from .errors import AssayError, Outcome, ReasonCode

__all__ = ["VerdictOutput", "reserve_verdict_output"]

#: Reservation states. ``RESERVED`` accepts exactly one :meth:`
#: VerdictOutput.emit`; both terminals accept only :meth:`VerdictOutput.close`.
_RESERVED = "RESERVED"
_EMITTED = "EMITTED"
_CLOSED = "CLOSED"


def _refuse(message: str) -> AssayError:
    return AssayError(
        message, outcome=Outcome.ERROR, reason_code=ReasonCode.OUTPUT_WRITE_FAILED
    )


def _identity(info: os.stat_result) -> tuple[int, int, int, int, int]:
    """The destination identity compared across reservation and emission.

    Inode and device catch a replacement; the file type catches a regular
    file swapped for a directory/fifo/symlink; size and mtime catch an
    in-place rewrite that reuses the inode. All five come from ONE
    ``lstat`` -- there is no second look for a caller to race against.
    """
    return (
        info.st_dev,
        info.st_ino,
        stat.S_IFMT(info.st_mode),
        info.st_size,
        info.st_mtime_ns,
    )


class VerdictOutput:
    """One ``RESERVED -> EMITTED|CLOSED`` verdict destination.

    ``emit`` writes UTF-8 text atomically for a file target, or writes the
    complete text to the supplied stdout for ``-``. A file reservation holds
    its already-opened parent and the destination's observed identity. It
    never holds a persistent sibling temp across consumer execution.
    ``close`` is idempotent and never changes the destination.
    """

    def __init__(
        self,
        *,
        target: str,
        stdout: TextIO,
        parent_fd: int | None = None,
        name: str | None = None,
        observed: tuple[int, int, int, int, int] | None = None,
    ) -> None:
        self._target = target
        self._stdout = stdout
        self._parent_fd = parent_fd
        self._name = name
        self._observed = observed
        self._state = _RESERVED

    @property
    def target(self) -> str:
        return self._target

    def emit(self, text: str) -> None:
        """Emit once or raise ``AssayError(ERROR, OUTPUT_WRITE_FAILED)``.

        File mode revalidates the destination observed at reservation, creates
        a same-parent exclusive temporary file, writes all UTF-8 bytes, and
        replaces relative to the held parent descriptor. It preserves any
        destination that appeared or changed after reservation.
        """
        if self._state != _RESERVED:
            raise RuntimeError(
                f"verdict output for {self._target!r} is {self._state}; a "
                f"reservation emits exactly once"
            )
        if self._parent_fd is None:
            try:
                self._stdout.write(text)
                self._stdout.flush()
            except OSError as exc:
                self._state = _EMITTED
                raise _refuse(
                    f"cannot write the verdict to stdout: {exc}"
                ) from exc
            self._state = _EMITTED
            return
        self._emit_to_file(text)
        self._state = _EMITTED

    def _emit_to_file(self, text: str) -> None:
        assert self._parent_fd is not None and self._name is not None
        self._revalidate()
        payload = text.encode("utf-8")
        temp_name = f".{self._name}.assay-{os.getpid()}-{os.urandom(6).hex()}.tmp"
        try:
            handle = os.open(
                temp_name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o644,
                dir_fd=self._parent_fd,
            )
        except OSError as exc:
            raise _refuse(
                f"cannot create a temporary file beside {self._target!r}: {exc}"
            ) from exc
        try:
            with os.fdopen(handle, "wb", closefd=True) as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(
                temp_name,
                self._name,
                src_dir_fd=self._parent_fd,
                dst_dir_fd=self._parent_fd,
            )
        except OSError as exc:
            _unlink_own(self._parent_fd, temp_name)
            raise _refuse(
                f"cannot write the verdict to {self._target!r}: {exc}"
            ) from exc

    def _revalidate(self) -> None:
        """Refuse an appeared or changed destination (A-181).

        The comparison is against what reservation actually observed, so
        both directions are covered: an absent destination that now exists,
        and a regular file that has since been replaced or rewritten.
        """
        assert self._parent_fd is not None and self._name is not None
        try:
            info: os.stat_result | None = os.stat(
                self._name, dir_fd=self._parent_fd, follow_symlinks=False
            )
        except FileNotFoundError:
            info = None
        except OSError as exc:
            raise _refuse(
                f"cannot inspect the verdict destination {self._target!r}: {exc}"
            ) from exc
        current = None if info is None else _identity(info)
        if current != self._observed:
            raise _refuse(
                f"the verdict destination {self._target!r} changed after it was "
                f"reserved (observed {self._observed!r}, found {current!r}); "
                f"assay preserves an object it did not itself write and emits "
                f"no fallback artifact"
            )

    def close(self) -> None:
        """Release owned state without touching the destination; idempotent."""
        if self._parent_fd is not None:
            os.close(self._parent_fd)
            self._parent_fd = None
        if self._state == _RESERVED:
            self._state = _CLOSED

    def __enter__(self) -> "VerdictOutput":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()


def _unlink_own(parent_fd: int, name: str) -> None:
    """Remove a file assay itself created in the held parent -- a write probe
    or an emission temporary -- never the destination.

    A failure here is swallowed deliberately, and this is the ONE place that
    happens. Both callers are already reporting something more actionable (a
    refusal the caller must act on) or are finishing a successful
    reservation, and neither can be repaired by a second unlink attempt.
    Raising instead would replace the real diagnosis with a cleanup detail.
    Factored into one function rather than duplicated at both sites so the
    swallow is a single, named, independently tested decision rather than
    two incidental ``except: pass`` blocks.
    """
    try:
        os.unlink(name, dir_fd=parent_fd)
    except OSError:
        return


def _normalized_absolute(target: str) -> str:
    """*target* in the CLI PROCESS namespace (A-181), normalized once.

    Relative spellings are anchored to the process's own current working
    directory -- never to the lane's ``project_root``, which is a different
    namespace entirely and would silently relocate a consumer's requested
    artifact into the tree under test. ``.``/``..`` are collapsed
    LEXICALLY, exactly once, before any filesystem call: resolving them
    against the filesystem would follow symlinks, which the descriptor walk
    below exists to refuse.
    """
    absolute = target if os.path.isabs(target) else os.path.join(os.getcwd(), target)
    return os.path.normpath(absolute)


def _open_parent(directory: str, target: str) -> int:
    """Descriptor-walk *directory* from the filesystem root, refusing to
    follow a symlink at any component (A-181).

    ``O_NOFOLLOW`` applies to the final component of each individual
    ``openat``, so walking one component at a time makes it apply to every
    component of the path -- which a single ``os.open(directory)`` would
    not do.
    """
    parts = [part for part in directory.split(os.sep) if part]
    fd = os.open(os.sep, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in parts:
            nxt = os.open(
                part,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=fd,
            )
            os.close(fd)
            fd = nxt
    except OSError as exc:
        os.close(fd)
        raise _refuse(
            f"cannot open the parent directory of {target!r} without following "
            f"a symlink: {exc}"
        ) from exc
    return fd


def reserve_verdict_output(target: str, *, stdout: TextIO) -> VerdictOutput:
    """Preflight and reserve *target* before consumer execution.

    ``-`` checks the supplied stream without closing it. A file spelling is
    anchored to the CLI process cwd (or filesystem root when absolute), walks
    real directory components without following symlinks, accepts only an
    absent or regular non-symlink destination, and proves parent write access
    by creating and removing an exclusive sibling probe. No probe survives
    this call. Every expected I/O refusal is the typed P21 output terminal.
    """
    if target == "-":
        try:
            stdout.write("")
            stdout.flush()
        except (OSError, ValueError) as exc:
            raise _refuse(f"the supplied stdout stream is not writable: {exc}") from exc
        return VerdictOutput(target=target, stdout=stdout)

    absolute = _normalized_absolute(target)
    directory, name = os.path.split(absolute)
    if not name or name in (os.curdir, os.pardir):
        raise _refuse(
            f"the verdict destination {target!r} does not name a file "
            f"(resolved to {absolute!r})"
        )

    parent_fd = _open_parent(directory, target)
    try:
        try:
            info: os.stat_result | None = os.stat(
                name, dir_fd=parent_fd, follow_symlinks=False
            )
        except FileNotFoundError:
            info = None
        except OSError as exc:
            raise _refuse(
                f"cannot inspect the verdict destination {target!r}: {exc}"
            ) from exc
        if info is not None and not stat.S_ISREG(info.st_mode):
            raise _refuse(
                f"the verdict destination {target!r} exists and is not an "
                f"ordinary regular file; assay replaces only a file it can "
                f"account for"
            )
        _probe_parent_is_writable(parent_fd, target)
        observed = None if info is None else _identity(info)
    except BaseException:
        os.close(parent_fd)
        raise
    return VerdictOutput(
        target=target,
        stdout=stdout,
        parent_fd=parent_fd,
        name=name,
        observed=observed,
    )


def _probe_parent_is_writable(parent_fd: int, target: str) -> None:
    """Prove the held parent accepts a new file, then leave nothing behind.

    Creating and unlinking an exclusive sibling is the only check that tests
    what emission will actually do. ``os.access`` asks the wrong question
    (it consults permission bits, not a read-only mount, a full filesystem,
    or a sticky-bit refusal) and answers it about a path rather than about
    the descriptor we hold.
    """
    probe_name = f".assay-output-probe-{os.getpid()}-{os.urandom(6).hex()}"
    try:
        handle = os.open(
            probe_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o644,
            dir_fd=parent_fd,
        )
    except OSError as exc:
        raise _refuse(
            f"the parent directory of {target!r} does not accept a new file: {exc}"
        ) from exc
    os.close(handle)
    _unlink_own(parent_fd, probe_name)
