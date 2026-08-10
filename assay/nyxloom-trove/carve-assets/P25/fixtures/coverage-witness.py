"""Run the pinned Topos pytest command and export its exact coverage bytes.

Assay consumes the project-relative artifact from its private committed
snapshot.  The copied bytes are the independent comparator's witness after
that snapshot has been destroyed.  This file is copied byte-for-byte into the
disposable HEAD; it never imports Assay or interprets the report.
"""

from __future__ import annotations

import errno
import os
import resource
import shutil
import stat
import subprocess
import sys
from pathlib import Path

ARTIFACT = Path(".assay/topos-coverage.json")
MAX_PROFILE_BYTES = 16 * 1024 * 1024
MAX_LOG_BYTES = 64 * 1024 * 1024


def _copy_new_regular(source: Path, destination: Path) -> None:
    source_fd = os.open(source, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    source_info = os.fstat(source_fd)
    if not stat.S_ISREG(source_info.st_mode) or source_info.st_size > MAX_PROFILE_BYTES:
        os.close(source_fd)
        raise SystemExit("P25 coverage witness is not a bounded regular file")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW
    try:
        fd = os.open(destination, flags, 0o600)
    except OSError as exc:
        os.close(source_fd)
        if exc.errno in {errno.EEXIST, errno.ELOOP}:
            raise SystemExit("P25 witness destination already exists or is a symlink") from exc
        raise
    try:
        with os.fdopen(source_fd, "rb", closefd=True) as incoming, os.fdopen(
            fd, "wb", closefd=True
        ) as outgoing:
            shutil.copyfileobj(incoming, outgoing, length=1024 * 1024)
            outgoing.flush()
            os.fsync(outgoing.fileno())
    except BaseException:
        try:
            destination.unlink()
        except OSError:
            pass
        raise


def _limit_child_output() -> None:
    resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_LOG_BYTES, MAX_LOG_BYTES))


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    witness_text = os.environ.get("ASSAY_P25_WITNESS")
    if not witness_text:
        raise SystemExit("ASSAY_P25_WITNESS is required")
    witness = Path(witness_text)
    if not witness.is_absolute():
        raise SystemExit("ASSAY_P25_WITNESS must be absolute")
    log_text = os.environ.get("ASSAY_P25_LOG")
    if not log_text or not Path(log_text).is_absolute():
        raise SystemExit("ASSAY_P25_LOG must be an absolute path")
    log = Path(log_text)
    log_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW
    log_fd = os.open(log, log_flags, 0o600)
    with os.fdopen(log_fd, "wb", closefd=True) as log_stream:
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                *args,
                "--cov=topos/src/topos",
                "--cov-branch",
                f"--cov-report=json:{ARTIFACT}",
            ],
            stdout=log_stream,
            stderr=subprocess.STDOUT,
            preexec_fn=_limit_child_output,
            check=False,
        )
    if log.stat().st_size > MAX_LOG_BYTES:
        raise SystemExit("P25 pytest log exceeded its fixed byte bound")
    if proc.returncode:
        with log.open("rb") as stream:
            stream.seek(max(0, log.stat().st_size - 64 * 1024))
            sys.stderr.buffer.write(stream.read())
    if proc.returncode == 0:
        _copy_new_regular(ARTIFACT, witness)
    return proc.returncode


if __name__ == "__main__":
    raise SystemExit(main())
