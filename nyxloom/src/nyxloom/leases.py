"""flock(2)-based leases. FROZEN CORE (ARCHITECTURE §4, SPEC §11).

The LOCK is the mutual exclusion; file CONTENT is dashboard metadata only.
Kernel releases the lock when the holder process dies — there is no stale-
lock recovery protocol by design. Acquisition is always non-blocking
(SPEC §11): an unavailable lease keeps a task QUEUED.

Exclusive lease  -> flock on <leases_dir>/<name>.lock
Counted lease    -> flock on the first free of <name>.0.lock .. <name>.{cap-1}.lock
"""

from __future__ import annotations

import fcntl
import json
import os
from dataclasses import dataclass
from pathlib import Path

from . import paths
from .types import iso, utc_now


@dataclass
class Lease:
    name: str
    path: Path
    fd: int

    def release(self) -> None:
        try:
            fcntl.flock(self.fd, fcntl.LOCK_UN)
        finally:
            os.close(self.fd)


def _try_lock(path: Path, owner: str, purpose: str) -> Lease | None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        return None
    meta = json.dumps({"owner": owner, "purpose": purpose, "since": iso(utc_now())})
    os.ftruncate(fd, 0)
    os.pwrite(fd, meta.encode(), 0)
    return Lease(name=path.stem.removesuffix(".lock"), path=path, fd=fd)


def acquire(name: str, *, owner: str, purpose: str = "", capacity: int = 1) -> Lease | None:
    """Non-blocking. Returns a held Lease or None if unavailable."""
    d = paths.leases_dir()
    if capacity <= 1:
        return _try_lock(d / f"{name}.lock", owner, purpose)
    for slot in range(capacity):
        lease = _try_lock(d / f"{name}.{slot}.lock", owner, purpose)
        if lease is not None:
            return lease
    return None


def holder_info(name: str, capacity: int = 1) -> list[dict]:
    """Dashboard metadata: [{slot, held, owner?, purpose?, since?}].

    'held' is probed by attempting a non-blocking shared... no: a shared lock
    would succeed against no holder AND block real acquirers momentarily.
    Instead attempt LOCK_EX|LOCK_NB and release immediately on success — if
    we could take it, nobody held it (the momentary hold is harmless because
    all real acquisition is also non-blocking retry-next-tick).
    """
    d = paths.leases_dir()
    files = [d / f"{name}.lock"] if capacity <= 1 else [
        d / f"{name}.{s}.lock" for s in range(capacity)
    ]
    out = []
    for slot, p in enumerate(files):
        info: dict = {"slot": slot, "held": False}
        if p.exists():
            fd = os.open(p, os.O_RDWR)
            try:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    fcntl.flock(fd, fcntl.LOCK_UN)
                except BlockingIOError:
                    info["held"] = True
                    try:
                        info.update(json.loads(p.read_text() or "{}"))
                    except (json.JSONDecodeError, OSError):
                        pass
            finally:
                os.close(fd)
        out.append(info)
    return out
