"""Safety policy: method-name normalisation, the read-method shapes, the refusal list and live safety state.

Default-deny: in a write session a call runs only if it is one of the exact calls of the plan being applied
(enforced by ``transport.GuardedRpc``), and even then it must pass ``refusal_for``. The refusal list below is a
second wall for calls an action declares by mistake, not the primary control.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Protocol

from .errors import Refused

BOOT_POOL = "boot-pool"
_METHOD = re.compile(r"[a-z0-9_]+(\.[a-z0-9_]+)+")

READ_EXACT = ("system.info", "system.version", "core.get_methods", "auth.me", "auth.mechanism_choices")
READ_SUFFIXES = (".query", ".details", ".get_instance", ".config", ".get_disks", ".get_state", ".choices", ".pool_choices", ".get_used",
                 ".import_find", ".validate_name", ".temperatures")

ALWAYS_REFUSED = {
    "system.reboot": "tnt never reboots or shuts down a host",
    "system.shutdown": "tnt never reboots or shuts down a host",
    "boot.attach": "boot-pool topology is out of scope (ACTION-MATRIX B-07: attach to a partition raises an internal TypeError)",
    "boot.detach": "boot-pool topology is out of scope (ACTION-MATRIX B-07)",
    "boot.replace": "boot-pool topology is out of scope (ACTION-MATRIX B-07)",
    "boot.environment.destroy": "destroying boot environments is not offered (activate/clone/keep only)",
}
GUARDED_FAMILIES = ("pool.", "zfs.", "filesystem.", "boot.", "disk.")
POOL_ID_EXEMPT = ("pool.dataset.", "pool.snapshot.", "pool.snapshottask.")  # their first parameter is a dataset or snapshot name


def normalise_method(method: Any) -> str:
    """Lower-case and strip; a name that is not dotted ``[a-z0-9_]`` words is refused (no tricks, no whitespace inside)."""
    if not isinstance(method, str):
        raise Refused("method-name", f"method name must be a string, got {type(method).__name__}")
    name = method.strip().lower()
    if not _METHOD.fullmatch(name):
        raise Refused("method-name", f"{method!r} is not a plain dotted method name")
    return name


def is_read_method(method: Any) -> bool:
    try:
        name = normalise_method(method)
    except Refused:
        return False
    return name in READ_EXACT or name.endswith(READ_SUFFIXES)


def canonical_params(params: Iterable[Any]) -> str:
    return json.dumps(list(params), sort_keys=True, default=str)


def _strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for k, v in value.items():
            yield from _strings(k)
            yield from _strings(v)
    elif isinstance(value, (list, tuple, set, frozenset)):
        for v in value:
            yield from _strings(v)


def _names_boot_pool(text: str) -> bool:
    folded = text.strip().casefold()
    return folded == BOOT_POOL or folded.startswith(BOOT_POOL + "/") or folded.startswith("/mnt/" + BOOT_POOL)


@dataclass(frozen=True)
class SafetyState:
    """Live facts for the refusal checks, read from the host just before an action runs."""

    pools: Mapping[int, str] = field(default_factory=dict)  # pool id -> name
    protected_disks: frozenset[str] = frozenset()  # disks in any pool (boot included) or with a mounted partition
    known_disks: frozenset[str] = frozenset()


def normalise_disk(name: str) -> str:
    return name.strip().removeprefix("/dev/")


def refusal_for(method: Any, params: Iterable[Any] = (), state: SafetyState | None = None) -> str | None:
    """The reason a call may never run, or None. Raises Refused for an unusable method name or disk.wipe parameters."""
    name = normalise_method(method)
    args = tuple(params)
    if name in ALWAYS_REFUSED:
        return ALWAYS_REFUSED[name]
    if name.startswith("core.") and name != "core.get_methods":
        return "core.* is an indirection (core.call/core.bulk can run any method) and is never executed"
    if name.startswith("auth.") and name not in ("auth.me", "auth.mechanism_choices"):
        return "tnt does not change credentials or sessions"
    if is_read_method(name):
        return None
    if name == "disk.wipe":
        if state is None:
            return "disk membership is unknown (no safety state): wipe is refused"
        if not args or not isinstance(args[0], str):
            raise Refused("disk-wipe", "disk.wipe takes the disk name as its first positional parameter")
        disk = normalise_disk(args[0])
        if disk not in state.known_disks:
            return f"disk {args[0]!r} is not a disk the host lists: wipe is refused"
        if disk in state.protected_disks:
            return f"disk {disk} belongs to a pool or has a mounted partition; wipe refuses disks that are in use"
        return None
    if name.startswith(GUARDED_FAMILIES):
        if any(_names_boot_pool(s) for s in _strings(args)):
            return "boot-pool and its datasets are never changed from here (ACTION-MATRIX A-08)"
        if name.startswith("pool.") and not name.startswith(POOL_ID_EXEMPT) and args and isinstance(args[0], int) and not isinstance(args[0], bool):
            pool = None if state is None else state.pools.get(args[0])
            if pool is None:
                return f"pool id {args[0]} cannot be resolved to a name: refused"
            if _names_boot_pool(pool):
                return "boot-pool is never exported, updated or deleted from here (ACTION-MATRIX A-08)"
    return None


class Caller(Protocol):
    def call(self, method: str, *params: Any, job: bool = False) -> Any: ...


_PARTITION_SUFFIX = re.compile(r"^(?P<disk>.*?[a-z])(?:p?\d+)$")


def _resolve(ref: str, disks: Mapping[str, Mapping[str, Any]]) -> str:
    """Map a vdev reference (sda, sda3, /dev/sda3, nvme0n1p2, /dev/disk/by-id/...) to a listed disk name, or refuse."""
    text = ref.strip()
    if text.startswith("/dev/disk/by-"):
        base = text.rsplit("/", 1)[1]
        for name, row in disks.items():
            ids = [str(row.get(k) or "") for k in ("serial", "wwn", "identifier", "lunid")]
            if any(len(i) >= 4 and i.lower() in base.lower() for i in ids):
                return name
        raise Refused("safety-state", f"cannot resolve {ref!r} to a disk")
    text = normalise_disk(text)
    if text in disks:
        return text
    match = _PARTITION_SUFFIX.match(text)
    if match and match.group("disk") in disks:
        return match.group("disk")
    raise Refused("safety-state", f"cannot resolve pool member {ref!r} to a disk; membership unknown, so wipe is refused")


def _members(node: Any) -> Iterable[str]:
    if isinstance(node, Mapping):
        for key in ("disk", "device", "path", "devname"):
            value = node.get(key)
            if isinstance(value, str) and value:
                yield value
        for value in node.values():
            if isinstance(value, (Mapping, list)):
                yield from _members(value)
    elif isinstance(node, list):
        for value in node:
            yield from _members(value)


def compute_state(rpc: Caller) -> SafetyState:
    """Pool ids, pool members (boot pool included) and mounted partitions from live reads; fails closed (Refused)."""
    try:
        pools = rpc.call("pool.query")
        boot = rpc.call("boot.get_state")
        rows = rpc.call("disk.query", [], {"extra": {"pools": True}})
    except Exception as exc:
        raise Refused("safety-state", f"cannot read pool membership ({exc}); refusing") from None
    if not isinstance(pools, list) or not isinstance(rows, list) or not isinstance(boot, Mapping):
        raise Refused("safety-state", "pool.query, boot.get_state or disk.query returned an unexpected shape")
    disks = {}
    for row in rows:
        if not isinstance(row, Mapping) or not isinstance(row.get("name"), str):
            raise Refused("safety-state", "disk.query returned a row without a name")
        disks[row["name"]] = row
    ids: dict[int, str] = {}
    protected: set[str] = set()
    for pool in pools:
        if not isinstance(pool, Mapping) or not isinstance(pool.get("id"), int) or not isinstance(pool.get("name"), str):
            raise Refused("safety-state", "pool.query returned a pool without id and name")
        ids[pool["id"]] = pool["name"]
        protected.update(_resolve(ref, disks) for ref in _members(pool.get("topology")))
    protected.update(_resolve(ref, disks) for ref in _members(boot.get("topology")))
    for name, row in disks.items():
        if row.get("pool"):
            protected.add(name)
        for part in row.get("partitions") or []:
            if isinstance(part, Mapping) and (part.get("mountpoint") or part.get("mounted")):
                protected.add(name)
    return SafetyState(ids, frozenset(protected), frozenset(disks))
