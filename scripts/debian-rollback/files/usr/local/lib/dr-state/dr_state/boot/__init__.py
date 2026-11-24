"""Boot-publication values and the backend interface."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Sequence

from ..ids import StateId
from ..storage import KernelPair


@dataclass(frozen=True, slots=True)
class BootTarget:
    """Everything needed to describe one bootable managed state."""

    state: StateId
    title: str
    kernel: KernelPair
    root_args: str
    locator: str


@dataclass(frozen=True, slots=True)
class Selector:
    """A parsed default or one-shot selector."""

    raw: str
    state: str | None
    entry: str | None
    known: bool


@dataclass(frozen=True, slots=True)
class Violation:
    """One boot invariant finding for the deferred check surface."""

    code: str
    severity: str
    subject: str
    detail: str


@dataclass(frozen=True, slots=True)
class Inspection:
    """The boot backend's view for check and publication."""

    published: frozenset[str]
    stale_owned: tuple[str, ...]
    violations: tuple[Violation, ...]


class Boot(Protocol):
    """The boot operations used by the cross-backend core."""

    name: str
    supports_oneshot: bool

    def install(self) -> None: ...

    def capacity(self, keep: Sequence[BootTarget]) -> int | None: ...

    def payload(self, target: BootTarget) -> list[tuple[str, int]]: ...

    def publish(self, targets: Sequence[BootTarget], default: StateId) -> None: ...

    def default(self, *, strict: bool) -> Selector | None: ...

    def oneshot(self, *, strict: bool) -> Selector | None: ...

    def set_oneshot(self, state: StateId) -> None: ...

    def protected(self) -> frozenset[str]: ...

    def inspect(self) -> Inspection: ...
