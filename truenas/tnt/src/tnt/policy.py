"""Safety policy: method-name normalisation, the read-method shapes, the refusal list and live safety state.

Default-deny: in a write session a call runs only if it is one of the exact calls of the plan being applied
(enforced by ``transport.GuardedRpc``), and even then it must pass ``refusal_for``. The refusal list below is a
second wall for calls an action declares by mistake, not the primary control.
"""
from __future__ import annotations

import json
import hashlib
import posixpath
import re
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Iterable, Mapping, Protocol

from .errors import Refused
from .profile import Secret

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
# The boot family is default-deny: only read methods (is_read_method) run, plus whatever an explicit later decision adds here
# (TNT-13 will add boot.environment clone/activate/keep). boot.format, boot.install_loader, boot.scrub, ... stay refused.
BOOT_WRITE_ALLOWED: frozenset[str] = frozenset()
# Methods whose FIRST parameter is a pool id (or name). Anything else under pool.* (pool.scrub.delete takes a task id,
# pool.dataset.* a dataset name, pool.create a dict) has no pool-id position and is not resolved as one.
POOL_ID_METHODS = frozenset({"pool.export", "pool.update", "pool.delete", "pool.expand", "pool.scrub", "pool.attach", "pool.detach",
                             "pool.replace", "pool.remove", "pool.offline", "pool.online", "pool.upgrade"})
POOL_NAME_METHODS = frozenset({"pool.scrub.run", "pool.scrub.scrub"})
# Methods that destroy data. An action containing one must be DESTRUCTIVE (probe + typed confirmation); the v27 manifest
# exposes this set as ``Backend.destructive``.
DESTRUCTIVE_METHODS = frozenset({"disk.wipe", "pool.export", "pool.delete", "pool.detach", "pool.remove", "pool.dataset.delete",
                                 "pool.snapshot.delete", "pool.snapshot.rollback", "container.delete", "container.stop",
                                 "vm.delete", "vm.stop", "vm.poweroff", "vm.device.delete", "user.delete",
                                 "sharing.smb.delete", "sharing.nfs.delete",
                                 "app.uninstall", "pool.dataset.destroy_snapshots", "filesystem.rmdir", "filesystem.unlink",
                                 "filesystem.remove", "container.restart", "zfs.pool.delete", "zfs.resource.destroy",
                                 "zfs.resource.snapshot.destroy"})
DESTRUCTIVE_SUFFIXES = (".delete", ".destroy", ".wipe", ".rollback", ".replace", ".attach", ".offline", ".promote",
                        ".rename", ".stop", ".reset", ".clear", ".format", ".recursive_destroy", ".import_pool",
                        ".uninstall", ".destroy_snapshots", ".rmdir", ".unlink", ".remove")
# Methods already used by the action/refusal model but not currently exposed in the adapter's feature manifest.
# New write methods must be added to a version manifest or deliberately reviewed into this small allow-list.
EXPLICIT_WRITE_ALLOWLIST = frozenset({
    "pool.export", "pool.update", "pool.delete", "pool.expand", "pool.scrub", "pool.attach", "pool.detach",
    "pool.replace", "pool.remove", "pool.offline", "pool.online", "pool.upgrade", "pool.scrub.delete",
    "pool.dataset.update", "pool.dataset.delete", "pool.snapshot.delete", "zfs.pool.delete", "zfs.resource.destroy",
    "zfs.resource.snapshot.destroy", "disk.wipe",
})


def is_destructive_method(method: Any) -> bool:
    name = normalise_method(method)
    spec = _manifest_spec(name)
    if spec is not None:
        return spec.classification == "destructive"
    return name in DESTRUCTIVE_METHODS or name.endswith(DESTRUCTIVE_SUFFIXES)


def needs_membership(method: Any, params: Iterable[Any] = ()) -> bool:
    """Whether judging this call needs live pool/disk state (so the state is only read for actions that touch disks or pools)."""
    name = normalise_method(method)
    return not is_read_method(name) and (name.startswith(("pool.", "zfs.", "boot.", "disk.")) or
                                         _contains_pool_reference(params))


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
    spec = _manifest_spec(name)
    if spec is not None:
        return spec.classification == "read"
    return name in READ_EXACT or name.endswith(READ_SUFFIXES)


@lru_cache(maxsize=None)
def _manifest_spec(name: str) -> Any:
    """Read explicit classifications from the selected adapter without coupling imports at module load time."""
    try:
        from .adapters import GENERATIONS
    except ImportError:
        return None
    for backend in GENERATIONS.values():
        spec = backend.method_spec(name)
        if spec is not None:
            return spec
    return None


_SECRET_KEYS = frozenset({"password", "old_password", "new_password", "api_key", "sshpubkey", "private_key"})


def _canonical_value(value: Any, key: Any = None) -> Any:
    if isinstance(value, Secret):
        value = value.reveal()
        return {"secret_sha256": hashlib.sha256(value.encode()).hexdigest()}
    if isinstance(key, str) and key.casefold() in _SECRET_KEYS:
        raw = value if isinstance(value, str) else repr(value)
        return {"secret_sha256": hashlib.sha256(raw.encode()).hexdigest()}
    if isinstance(value, Mapping):
        return {k: _canonical_value(v, k) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_canonical_value(v) for v in value]
    return value


def canonical_params(params: Iterable[Any]) -> str:
    return json.dumps(_canonical_value(list(params)), sort_keys=True, default=str)


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


def _ambiguous_path(text: str) -> bool:
    return "/" in text and ".." in re.sub(r"/+", "/", text).split("/")


def _names_boot_pool(text: str) -> bool:
    """True when the string names boot-pool or something under it: a bare name, ``boot-pool/x``, ``boot-pool@snap``,
    ``/mnt/boot-pool/x`` or ``/dev/zvol/boot-pool/x`` after collapsing slashes and dots. Whole components only:
    ``boot-pool-data`` and ``/mnt/boot-pool-data/x`` are different names."""
    # Normalise before stripping known mount/device prefixes. This also collapses a leading /./ and embedded dots.
    t = re.sub(r"/+", "/", text.strip().casefold())
    t = posixpath.normpath(t) if t else t
    for prefix in ("/mnt/", "/dev/zvol/", "mnt/"):
        if t.startswith(prefix):
            t = t[len(prefix):]
    t = t.lstrip("/")
    return re.split(r"[/@#]", t)[0] == BOOT_POOL


POOL_REFERENCE_KEYS = frozenset({"pool", "pools", "pool_id", "pool_name", "pool_ids", "pool_names"})


def _pool_reference_key(key: Any) -> str:
    if not isinstance(key, str):
        return ""
    separated = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", key)
    return separated.casefold().replace("-", "_")


def _contains_pool_reference(value: Any) -> bool:
    if isinstance(value, Mapping):
        return any(_pool_reference_key(key) in POOL_REFERENCE_KEYS or _contains_pool_reference(child)
                   for key, child in value.items())
    if isinstance(value, (list, tuple, set, frozenset)):
        return any(_contains_pool_reference(child) for child in value)
    return False


def _pool_value_refusal(value: Any, state: SafetyState | None) -> str | None:
    if value is None:
        return "a pool reference is null and cannot be resolved: refused"
    if isinstance(value, bool):
        return "a pool reference is a boolean and is ambiguous"
    if isinstance(value, int):
        if state is None:
            return f"pool id {value} cannot be resolved to a name: refused"
        pool = state.pools.get(value)
        if pool is None:
            return f"pool id {value} cannot be resolved to a name: refused"
        if _names_boot_pool(pool):
            return "a pool reference resolves to boot-pool, which is never changed from here (ACTION-MATRIX A-08)"
        return None
    if isinstance(value, str):
        if _names_boot_pool(value):
            return "a pool reference resolves to boot-pool, which is never changed from here (ACTION-MATRIX A-08)"
        if value.strip().isdigit():
            if state is not None and _names_boot_pool(state.pools.get(int(value.strip()), "")):
                return "a pool reference resolves to boot-pool, which is never changed from here (ACTION-MATRIX A-08)"
            return f"pool reference {value!r} is an ambiguous string of digits: refused"
        if state is None:
            return f"pool reference {value!r} cannot be resolved without live pool state: refused"
        if value.strip().casefold() not in {pool.casefold() for pool in state.pools.values()}:
            return f"pool reference {value!r} is not a pool the host lists: refused"
        return None
    if isinstance(value, (list, tuple, set, frozenset)):
        for child in value:
            reason = _pool_value_refusal(child, state)
            if reason and "boot-pool" in reason:
                return reason
        return "a pool reference list is ambiguous: refused"
    if isinstance(value, Mapping):
        return "a pool reference mapping is ambiguous: refused"
    return f"a pool reference of type {type(value).__name__} is ambiguous: refused"


def _pool_mapping_refusal(value: Any, state: SafetyState | None) -> str | None:
    """Resolve known pool-key spellings recursively; malformed or unresolved pool references fail closed."""
    if isinstance(value, Mapping):
        for key, child in value.items():
            if _pool_reference_key(key) in POOL_REFERENCE_KEYS:
                reason = _pool_value_refusal(child, state)
                if reason:
                    return reason
            reason = _pool_mapping_refusal(child, state)
            if reason:
                return reason
    elif isinstance(value, (list, tuple, set, frozenset)):
        for child in value:
            reason = _pool_mapping_refusal(child, state)
            if reason:
                return reason
    return None


@dataclass(frozen=True)
class SafetyState:
    """Live facts for the refusal checks, read from the host just before an action runs."""

    pools: Mapping[int, str] = field(default_factory=dict)  # pool id -> name
    protected_disks: frozenset[str] = frozenset()  # disks in any pool (boot included) or with a mounted partition
    known_disks: frozenset[str] = frozenset()
    disk_sizes: Mapping[str, int | None] = field(default_factory=dict)
    unresolved: tuple[str, ...] = ()  # unmapped members or pools without usable topology (blocks disk.wipe only)


def normalise_disk(name: str) -> str:
    return name.strip().removeprefix("/dev/")


def _pool_id_refusal(name: str, args: tuple[Any, ...], state: SafetyState | None) -> str | None:
    if not args:
        return None
    first = args[0]
    names = {n.casefold() for n in state.pools.values()} if state else set()
    if isinstance(first, bool) or isinstance(first, float) or isinstance(first, (list, tuple, set)):
        return f"{name}: the pool must be given as an integer id or a known pool name, not {first!r}"
    if isinstance(first, Mapping):
        return f"{name}: a pool given as a mapping is ambiguous: refused"
    if isinstance(first, int):
        pool = None if state is None else state.pools.get(first)
        if pool is None:
            return f"pool id {first} cannot be resolved to a name: refused"
        return "boot-pool is never exported, updated or deleted from here (ACTION-MATRIX A-08)" if _names_boot_pool(pool) else None
    if isinstance(first, str):
        if first.strip().isdigit():
            return f"{name}: pool {first!r} is a string of digits, which is ambiguous with an id: refused"
        if first.strip().casefold() not in names:
            return f"{name}: {first!r} is not a pool the host lists: refused"
        return None
    return f"{name}: unusable pool parameter {first!r}: refused"


def _pool_name_refusal(name: str, args: tuple[Any, ...], state: SafetyState | None) -> str | None:
    if not args or not isinstance(args[0], str):
        return f"{name}: the pool must be given as its name string: refused"
    if state is None:
        return f"{name}: cannot verify the pool name without live pool state: refused"
    pool = args[0].strip()
    if _names_boot_pool(pool):
        return "boot-pool is never modified from here (ACTION-MATRIX A-08)"
    if pool.casefold() not in {known.casefold() for known in state.pools.values()}:
        return f"{name}: {pool!r} is not a pool the host lists: refused"
    return None


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
    if name.startswith("boot.") and name not in BOOT_WRITE_ALLOWED:
        return "the boot family is read-only from tnt (default-deny; boot.format, install_loader, scrub, ... are never executed)"
    texts = list(_strings(args))
    if any(_ambiguous_path(text) for text in texts):
        return "a path with '..' is ambiguous and refused"
    if any(_names_boot_pool(text) for text in texts):
        return "boot-pool and its datasets are never changed from here (ACTION-MATRIX A-08)"
    if reason := _pool_mapping_refusal(args, state):
        return reason
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
        if state.unresolved:
            return f"pool membership is unknown ({list(state.unresolved)}): wipe is refused"
        size = state.disk_sizes.get(disk)
        if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
            return f"disk {disk} reports no usable size: wipe is refused"
        return None
    if name in POOL_ID_METHODS:
        return _pool_id_refusal(name, args, state)
    if name in POOL_NAME_METHODS:
        return _pool_name_refusal(name, args, state)
    return None


class Caller(Protocol):
    def call(self, method: str, *params: Any, job: bool = False) -> Any: ...


_NVME_STYLE = re.compile(r"^(?P<disk>.*\d)p\d+$")  # nvme0n1p2, mmcblk0p1
_SD_STYLE = re.compile(r"^(?P<disk>.*[a-z])\d+$")  # sda3, vdb1
_TOKEN_SPLIT = re.compile(r"[-_:.\s]+")
PARTITION_ID_KEYS = ("name", "path", "partuuid", "partition_uuid", "uuid", "id", "label", "devname")
DISK_ID_KEYS = ("serial", "wwn", "identifier", "lunid", "devname", "name")


def _disk_candidates(name: str) -> Iterable[str]:
    yield name
    for pattern in (_NVME_STYLE, _SD_STYLE):
        match = pattern.match(name)
        if match:
            yield match.group("disk")


def _alias_map(disks: Mapping[str, Mapping[str, Any]]) -> dict[str, str]:
    """Exact identifiers (serial, wwn, partition uuids/names, ...) -> disk name, from disk.query rows. An identifier shared
    by two disks maps to neither (ambiguous): it must not resolve."""
    seen: dict[str, set[str]] = {}
    for disk, row in disks.items():
        values = [row.get(k) for k in DISK_ID_KEYS]
        for part in row.get("partitions") or []:
            if isinstance(part, Mapping):
                values += [part.get(k) for k in PARTITION_ID_KEYS]
        for value in values:
            if isinstance(value, str) and len(value) >= 4:
                seen.setdefault(value.casefold(), set()).add(disk)
    return {alias: next(iter(owners)) for alias, owners in seen.items() if len(owners) == 1}


def _resolve(ref: str, disks: Mapping[str, Mapping[str, Any]], aliases: Mapping[str, str]) -> str | None:
    """Map one vdev reference to a listed disk name, or None. ``/dev/disk/by-*`` references match a known identifier
    exactly or by a whole token of the link name (never by substring)."""
    text = ref.strip()
    if text.startswith("/dev/disk/by-"):
        base = text.rsplit("/", 1)[1].casefold()
        if base in aliases:
            return aliases[base]
        hits = {aliases[t] for t in _TOKEN_SPLIT.split(base) if t in aliases}
        return next(iter(hits)) if len(hits) == 1 else None
    text = normalise_disk(text)
    for candidate in _disk_candidates(text):
        if candidate in disks:
            return candidate
    return aliases.get(text.casefold())


def _vdev_nodes(node: Any) -> Iterable[Mapping[str, Any]]:
    """Every dict anywhere in a topology (mirror/raidz/draid nesting, data/cache/log/spare/special/dedup groups)."""
    if isinstance(node, Mapping):
        yield node
        for value in node.values():
            if isinstance(value, (Mapping, list)):
                yield from _vdev_nodes(value)
    elif isinstance(node, list):
        for value in node:
            yield from _vdev_nodes(value)


def _node_refs(node: Mapping[str, Any]) -> list[str]:
    """The references one vdev carries, best first: its ``disk`` key, then device/devname, then (for a DISK) its name, then path."""
    keys = ["disk", "device", "devname"] + (["name"] if str(node.get("type", "")).upper() == "DISK" else []) + ["path"]
    return [node[k] for k in keys if isinstance(node.get(k), str) and node[k]]


def _has_usable_topology(topology: Any) -> bool:
    """A pool topology is usable only when at least one vdev carries a disk reference."""
    return any(_node_refs(node) for node in _vdev_nodes(topology))


def _members(topology: Any, disks: Mapping[str, Mapping[str, Any]], aliases: Mapping[str, str]) -> tuple[set[str], list[str]]:
    """(listed disks that are pool members, references that could not be resolved). All resolvable references of a vdev
    are included; a vdev with references but none resolvable is reported unresolved."""
    members: set[str] = set()
    unresolved: list[str] = []
    for node in _vdev_nodes(topology):
        refs = _node_refs(node)
        if not refs:
            continue
        resolved = False
        for ref in refs:
            disk = _resolve(ref, disks, aliases)
            if disk:
                members.add(disk)
                resolved = True
        if not resolved:
            unresolved.append(refs[-1])
    return members, unresolved


def compute_state(rpc: Caller) -> SafetyState:
    """Pool ids, pool members (boot pool included), mounted partitions and disk sizes from live reads; fails closed.

    Called only for actions whose calls touch disks or pools (``needs_membership``). An unresolvable pool member or a
    pool without usable topology does not raise: it is recorded in ``unresolved`` and blocks disk.wipe only.
    """
    try:
        pools = rpc.call("pool.query")
        boot = rpc.call("boot.get_state")
        rows = rpc.call("disk.query", [], {"extra": {"pools": True}})
    except Exception as exc:
        raise Refused("safety-state", f"cannot read pool membership ({exc}); refusing") from None
    if not isinstance(pools, list) or not isinstance(rows, list) or not isinstance(boot, Mapping):
        raise Refused("safety-state", "pool.query, boot.get_state or disk.query returned an unexpected shape")
    disks: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        if not isinstance(row, Mapping) or not isinstance(row.get("name"), str):
            raise Refused("safety-state", "disk.query returned a row without a name")
        disks[row["name"]] = row
    aliases = _alias_map(disks)
    ids: dict[int, str] = {}
    protected: set[str] = set()
    unresolved: list[str] = []
    boot_topology = boot.get("topology")
    boot_name = boot.get("name")
    boot_topology_usable = _has_usable_topology(boot_topology)
    topologies = [boot_topology]
    pool_rows: list[Mapping[str, Any]] = []
    for pool in pools:
        if not isinstance(pool, Mapping) or not isinstance(pool.get("id"), int) or not isinstance(pool.get("name"), str):
            raise Refused("safety-state", "pool.query returned a pool without id and name")
        pool_rows.append(pool)
        ids[pool["id"]] = pool["name"]
        topology = pool.get("topology")
        if not _has_usable_topology(topology) and not (_names_boot_pool(pool["name"]) and boot_topology_usable):
            unresolved.append(f"pool {pool['name']!r} has no usable topology")
        topologies.append(topology)
    # boot.get_state can be the usable topology source when pool.query omits it. A named boot pool with no topology in
    # either response is indeterminate and blocks disk.wipe too.
    if isinstance(boot_name, str) and _names_boot_pool(boot_name) and not boot_topology_usable:
        listed_boot_usable = any(_names_boot_pool(pool["name"]) and _has_usable_topology(pool.get("topology"))
                                 for pool in pool_rows)
        if not listed_boot_usable:
            unresolved.append("boot-pool has no usable topology")
    for topology in topologies:
        members, lost = _members(topology, disks, aliases)
        protected |= members
        unresolved += lost
    for name, row in disks.items():
        if row.get("pool") or row.get("exported_zpool"):
            protected.add(name)
        for part in row.get("partitions") or []:
            if isinstance(part, Mapping) and (part.get("mountpoint") or part.get("mounted")):
                protected.add(name)
    sizes = {name: row.get("size") if isinstance(row.get("size"), int) and not isinstance(row.get("size"), bool) else None
             for name, row in disks.items()}
    return SafetyState(ids, frozenset(protected), frozenset(disks), sizes, tuple(unresolved))
