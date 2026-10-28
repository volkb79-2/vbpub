"""Per-API-generation adapters. ``select`` picks one from the version string the host reports."""
from __future__ import annotations

import re
from typing import Any, Mapping

from ..errors import UnsupportedVersion
from .base import Backend
from .v27 import V27
from .v28 import V28

GENERATIONS: dict[int, type[Backend]] = {27: V27, 28: V28}
_MAJOR = re.compile(r"(\d{2})\.(\d{1,2})")


def parse_major(version: str) -> int:
    """'TrueNAS-27.0.0-RC.1' -> 27; a nightly such as '28.04-MASTER-20261014-...' -> 28."""
    match = _MAJOR.search(version)
    if not match:
        raise UnsupportedVersion(f"cannot read an API generation from the reported version {version!r}")
    return int(match.group(1))


def select(version: str) -> Backend:
    major = parse_major(version)
    cls = GENERATIONS.get(major)
    if cls is None:
        raise UnsupportedVersion(f"no adapter for API generation {major} (version {version!r}); tnt supports {sorted(GENERATIONS)}")
    return cls(version)


def detect(rpc: Any) -> Backend:
    return select(str(rpc.call("system.version")))


def missing_methods(backend: Backend, available: Mapping[str, Any] | set[str] | list[str]) -> dict[str, list[str]]:
    """Feature -> methods the manifest requires but the host does not list. Empty means the manifest holds."""
    have = set(available)
    out = {feature: sorted(m for m in methods if m not in have) for feature, methods in backend.manifest.items()}
    return {feature: miss for feature, miss in out.items() if miss}
