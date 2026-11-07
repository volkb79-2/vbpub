"""Pinned wheel and daemon-lock parsing for the service installer.

This module is stdlib-only so ``toposd install`` can start from zipimport
before the installed runtime environment exists.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


PIN_RE = re.compile(
    r"^([0-9]+\.[0-9]+\.[0-9]+[A-Za-z0-9.+_-]*)@sha256:([0-9a-f]{64})$"
)
LOCK_LINE_RE = re.compile(
    r"^([A-Za-z0-9][A-Za-z0-9_.-]*)==([A-Za-z0-9][A-Za-z0-9.+_-]*) "
    r"--hash=sha256:([0-9a-f]{64})$"
)


class LockError(ValueError):
    """A malformed pin or daemon requirements lock."""


@dataclass(frozen=True)
class LockRequirement:
    name: str
    version: str
    sha256: str


def parse_pin(value: str, label: str = "wheel") -> tuple[str, str]:
    """Return ``(version, sha256)`` for the strict installer pin syntax."""
    match = PIN_RE.fullmatch(value or "")
    if match is None:
        raise LockError(f"{label} must be <version>@sha256:<64 lowercase hex>")
    return match.group(1), match.group(2)


def parse_lock(contents: str) -> tuple[LockRequirement, ...]:
    """Parse the deliberately narrow, fully hashed pip requirements format."""
    requirements: list[LockRequirement] = []
    names: set[str] = set()
    for number, raw_line in enumerate(contents.splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = LOCK_LINE_RE.fullmatch(line)
        if match is None:
            raise LockError(f"daemon lock line {number} must pin one version and sha256 hash")
        name, version, digest = match.groups()
        normalized = re.sub(r"[-_.]+", "-", name).lower()
        if normalized in {"topos", "cli-extended"}:
            raise LockError(f"daemon lock must not include {name}; it is installed from its pinned wheel")
        if normalized in names:
            raise LockError(f"daemon lock contains duplicate requirement {name!r}")
        names.add(normalized)
        requirements.append(LockRequirement(name, version, digest))
    return tuple(requirements)


__all__ = ["LOCK_LINE_RE", "PIN_RE", "LockError", "LockRequirement", "parse_lock", "parse_pin"]
