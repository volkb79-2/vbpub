"""Registry of resource kinds: how a kind loads, serializes, renders and reverts.

A kind is added by registering one ``KindSpec`` from its own module; the engine
has no per-kind code. SPEC, "Adding a resource kind", is the contract.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import Change, PostAction, Problem, Resource

_RESERVED_KEYS = frozenset({"augeaswrap", "writer"})


@dataclass(frozen=True, kw_only=True)
class RevertHooks:
    """The two functions that make a kind revertible.

    ``snapshot(change, observed)`` returns a JSON-safe record of what the commit
    overwrites; it may carry ``data_b64`` plus ``sha256`` of those bytes.
    ``restore(address, record, actions)`` validates a stored record (``ValueError``
    refuses the revert) and returns a resource that puts that state back and whose
    ``post_actions`` return ``actions``.
    """

    snapshot: Callable[[Change, object], Mapping[str, Any]]
    restore: Callable[[str, Mapping[str, Any], tuple[PostAction, ...]], Resource]


@dataclass(frozen=True, kw_only=True)
class KindSpec:
    """Everything the engine needs to know about one resource kind.

    ``revert`` has no default: pass ``RevertHooks`` or ``None`` (declared
    unsupported), so a plan can state ``revert: unsupported`` before apply.
    """

    kind: str
    parse: Callable[[Mapping[str, Any]], Resource]
    to_toml: Callable[[Resource], Mapping[str, Any]]
    revert: RevertHooks | None
    render: Callable[[Change, Any], Sequence[str]] | None = None
    shadow_check: Callable[[Resource, Path], list[Problem]] | None = None


_REGISTRY: dict[str, KindSpec] = {}


def register_kind(spec: KindSpec) -> KindSpec:
    """Register a kind once; a second registration of the same name is an error."""

    if not isinstance(spec.kind, str) or not spec.kind or spec.kind in _RESERVED_KEYS:
        raise ValueError(f"invalid resource kind name {spec.kind!r}")
    if spec.kind in _REGISTRY:
        raise ValueError(f"resource kind {spec.kind!r} is already registered")
    _REGISTRY[spec.kind] = spec
    return spec


def kind_spec(kind: str) -> KindSpec | None:
    """Return the registered spec for ``kind`` or ``None`` for an unregistered kind."""

    return _REGISTRY.get(kind)


def registered_kinds() -> tuple[str, ...]:
    """Registered kind names in registration order (the TOML load order)."""

    return tuple(_REGISTRY)


def is_revertible(kind: str) -> bool:
    spec = _REGISTRY.get(kind)
    return spec is not None and spec.revert is not None


def resource_tables(data: Mapping[str, Any], kind: str) -> list[Mapping[str, Any]]:
    """Return the ``[[kind]]`` tables of a request mapping, strictly typed."""

    raw = data.get(kind, [])
    if not isinstance(raw, list) or any(not isinstance(item, Mapping) for item in raw):
        raise ValueError(f"{kind} must be an array of tables")
    return list(raw)


def require_keys(table: Mapping[str, Any], *, allowed: set[str], required: set[str], where: str) -> None:
    """Refuse non-string keys, unknown keys and missing required keys."""

    keys = set(table)
    if any(not isinstance(key, str) for key in keys):
        raise ValueError(f"{where} keys must be strings")
    unknown = keys - allowed
    missing = required - set(table)
    if unknown:
        raise ValueError(f"unknown {where} key(s): {', '.join(sorted(unknown))}")
    if missing:
        raise ValueError(f"missing {where} field(s): {', '.join(sorted(missing))}")


__all__ = [
    "KindSpec",
    "RevertHooks",
    "is_revertible",
    "kind_spec",
    "register_kind",
    "registered_kinds",
    "require_keys",
    "resource_tables",
]
