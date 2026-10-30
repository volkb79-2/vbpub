"""Input-derived space admission and recent btrfs delete accounting."""
from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass
from typing import Protocol

from .errors import SpaceRefused


class _Capabilities(Protocol):
    cheap_clone: bool
    backend: str


@dataclass(frozen=True)
class SpaceEstimate:
    ingest_bytes: int
    per_clone_bytes: int
    max_live_clones: int

    def __post_init__(self) -> None:
        for name, value in (
            ("ingest_bytes", self.ingest_bytes),
            ("per_clone_bytes", self.per_clone_bytes),
            ("max_live_clones", self.max_live_clones),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")

    def needed(self, caps: _Capabilities) -> int:
        if caps.cheap_clone:
            return self.ingest_bytes + self.max_live_clones * self.per_clone_bytes
        return (1 + self.max_live_clones) * max(self.ingest_bytes, self.per_clone_bytes)

    def as_dict(self) -> dict[str, int]:
        return {
            "ingest_bytes": self.ingest_bytes,
            "per_clone_bytes": self.per_clone_bytes,
            "max_live_clones": self.max_live_clones,
        }


_PENDING_SECONDS = 60.0
_pending_lock = threading.Lock()
_pending_deletes: list[tuple[float, int, int]] = []


def record_pending_delete(st_dev: int, allocated_bytes: int, *, now: float | None = None) -> None:
    if allocated_bytes <= 0:
        return
    timestamp = time.monotonic() if now is None else now
    with _pending_lock:
        _prune(timestamp)
        _pending_deletes.append((timestamp, st_dev, allocated_bytes))


def pending_delete_bytes(st_dev: int, *, now: float | None = None) -> int:
    timestamp = time.monotonic() if now is None else now
    with _pending_lock:
        _prune(timestamp)
        return sum(size for _, device, size in _pending_deletes if device == st_dev)


def _prune(now: float) -> None:
    _pending_deletes[:] = [entry for entry in _pending_deletes if now - entry[0] < _PENDING_SECONDS]


def available_bytes(path: str, *, statvfs=None) -> int:
    stat = (statvfs or os.statvfs)(path)
    return int(stat.f_bavail) * int(stat.f_frsize)


def admit(
    estimate: SpaceEstimate,
    caps: _Capabilities,
    path: str,
    *,
    statvfs=None,
    pending_bytes: int = 0,
) -> tuple[int, int]:
    needed = estimate.needed(caps) + (pending_bytes if caps.backend == "btrfs" else 0)
    available = available_bytes(path, statvfs=statvfs)
    if needed > available:
        raise SpaceRefused(needed, available, estimate.as_dict())
    return needed, available
