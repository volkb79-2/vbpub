"""Storage values and the backend interface."""

from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol, Sequence

from ..ids import StateId

Kind = Literal["live", "checkpoint"]


@dataclass(frozen=True, slots=True)
class Stored:
    """One top-level object as storage reports it."""

    name: str
    kind: Kind | None
    origin: str | None


@dataclass(frozen=True, slots=True)
class KernelPair:
    """One complete kernel/initrd pair, as host and loader paths."""

    version: str
    vmlinuz: Path
    initrd: Path
    fs_vmlinuz: str
    fs_initrd: str


@dataclass(frozen=True, slots=True)
class Space:
    """Storage bytes and currently usable free space."""

    size: int
    free: int


class Storage(Protocol):
    """The storage operations used by the cross-backend core."""

    name: str

    @property
    def control_dir(self) -> Path: ...

    def session(self) -> AbstractContextManager[None]: ...

    def list(self) -> list[Stored]: ...

    def running(self) -> str | None: ...

    def snapshot(self, source: StateId, target: StateId) -> None: ...

    def activate(self, state: StateId) -> None: ...

    def delete(self, state: StateId) -> None: ...

    def kernels(self, state: str) -> list[KernelPair]: ...

    def parent(self, state: StateId) -> StateId | None: ...

    def locator(self, state: StateId) -> str: ...

    def root_args(self, state: StateId) -> str: ...

    def space(self) -> Space: ...


def by_name(stored: Sequence[Stored]) -> list[Stored]:
    """Return storage objects in stable name order."""

    return sorted(stored, key=lambda item: item.name)
