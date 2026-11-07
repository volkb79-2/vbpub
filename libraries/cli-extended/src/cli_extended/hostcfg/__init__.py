"""Shared primitives for host configuration resources."""

from __future__ import annotations

import re
from dataclasses import dataclass

_OWNER_RE = re.compile(r"[a-z][a-z0-9-]{0,62}")


def validate_owner(owner: object) -> str:
    """Return one canonical hostcfg owner name or raise ``ValueError``."""

    if not isinstance(owner, str) or _OWNER_RE.fullmatch(owner) is None:
        raise ValueError("owner must match ^[a-z][a-z0-9-]{0,62}$")
    return owner


@dataclass(frozen=True, eq=False)
class Problem:
    """Machine-readable hostcfg finding with stable human-readable text.

    A problem compares equal to its message string, so callers that only know
    the text form keep working; ``code`` is the stable machine identifier.
    """

    code: str
    message: str
    path: str | None = None
    owner: str | None = None
    key: str | None = None

    def __str__(self) -> str:
        return self.message

    def __eq__(self, other: object) -> bool:
        if isinstance(other, str):
            return self.message == other
        if isinstance(other, Problem):
            return self.as_dict() == other.as_dict()
        return NotImplemented

    def __hash__(self) -> int:
        return hash(self.message)

    def __contains__(self, item: object) -> bool:
        return isinstance(item, str) and item in self.message

    def as_dict(self) -> dict[str, str | None]:
        return {
            "code": self.code,
            "message": self.message,
            "path": self.path,
            "owner": self.owner,
            "key": self.key,
        }


__all__ = ["Problem", "validate_owner"]
