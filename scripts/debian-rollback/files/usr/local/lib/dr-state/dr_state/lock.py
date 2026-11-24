"""Writer locking and durable atomic file replacement."""

from __future__ import annotations

import fcntl
import os
import tempfile
import time
from pathlib import Path
from types import TracebackType

from .errors import LockTimeout

SCRATCH_PREFIX = ".dr-state.tmp."


class FileLock:
    """A shared or exclusive ``flock`` with a bounded wait."""

    def __init__(
        self,
        path: str | Path,
        *,
        shared: bool = False,
        timeout: float = 60.0,
    ) -> None:
        if timeout < 0:
            raise ValueError("lock timeout cannot be negative")
        self.path = Path(path)
        self.shared = shared
        self.timeout = timeout
        self._fd: int | None = None
        self._held = False

    @property
    def held(self) -> bool:
        """Whether this lock object currently owns a kernel lock."""

        return self._held

    def require_exclusive(self) -> None:
        """Refuse to let a caller continue without this exclusive lock."""

        if not self._held or self.shared:
            raise RuntimeError(f"an exclusive lock on {self.path} is required")

    def _open(self) -> int:
        return os.open(self.path, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o600)

    def acquire(self) -> None:
        """Acquire the lock, or raise ``LockTimeout``."""

        if self._held:
            return
        fd = self._open()
        operation = fcntl.LOCK_SH if self.shared else fcntl.LOCK_EX
        deadline = time.monotonic() + self.timeout
        try:
            while True:
                try:
                    fcntl.flock(fd, operation | fcntl.LOCK_NB)
                    self._fd = fd
                    self._held = True
                    return
                except BlockingIOError:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise LockTimeout(
                            f"timed out waiting {self.timeout:g}s for {self.path}"
                        ) from None
                    time.sleep(min(0.05, remaining))
        except BaseException:
            os.close(fd)
            raise

    def release(self) -> None:
        """Release the lock if this object holds it."""

        if self._fd is None:
            return
        try:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
        finally:
            os.close(self._fd)
            self._fd = None
            self._held = False

    def __enter__(self) -> FileLock:
        self.acquire()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.release()


def fsync_directory(directory: Path) -> None:
    """Flush a directory's entries to stable storage."""

    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_write(path: str | Path, data: str | bytes, *, mode: int = 0o600) -> None:
    """Replace ``path`` with complete, fsynced contents."""

    destination = Path(path)
    payload = data.encode("utf-8") if isinstance(data, str) else data
    parent = destination.parent
    fd, temporary_name = tempfile.mkstemp(
        dir=parent,
        prefix=SCRATCH_PREFIX,
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "wb") as stream:
            fd = -1
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
        fsync_directory(parent)
    except BaseException:
        if fd >= 0:
            os.close(fd)
        temporary.unlink(missing_ok=True)
        raise
