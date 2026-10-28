"""The Backend interface: domain operations, implemented per API generation.

``manifest`` maps a feature to the RPC methods the adapter needs. ``verified`` records HOW the manifest was
obtained, so a stub never poses as measured: "reference" = read from https://api.truenas.com/v27.0 only,
"live" = confirmed against a running host, "copied" = inherited from the previous generation, unconfirmed.
"""
from __future__ import annotations

from typing import Any, ClassVar, Mapping

from ..domain import Disk, Partition


def _size(value: Any) -> int | None:
    """A non-negative integer size, else None (unknown). Never 0 for a missing or unparseable value."""
    if isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def parse_partition(raw: Mapping[str, Any]) -> Partition:
    return Partition(name=str(raw.get("name", "")), size=_size(raw.get("size")), start=raw.get("start") if isinstance(raw.get("start"), int) else None,
                     kind=raw.get("partition_type_name") or raw.get("type"))


class Backend:
    generation: ClassVar[str]
    verified: ClassVar[str]
    manifest: ClassVar[Mapping[str, tuple[str, ...]]]

    def __init__(self, version: str):
        self.version = version

    # -- disks --------------------------------------------------------------------------------------------
    def disk_calls(self) -> tuple[tuple[str, tuple[Any, ...]], ...]:
        """The read calls that produce the disk inventory, as (method, params)."""
        return (("disk.query", ([], {"extra": {"pools": True}})),
                ("disk.details", ({"type": "BOTH", "join_partitions": True},)))

    def list_disks(self, rpc: Any) -> list[Disk]:
        queried = {str(d.get("name")): d for d in rpc.call(self.disk_calls()[0][0], *self.disk_calls()[0][1]) or []}
        details = self.disk_details(rpc.call(self.disk_calls()[1][0], *self.disk_calls()[1][1]))
        # Evidence that the API really joined partition data: at least one disk shows partitions. If every disk shows
        # none, "no partitions" cannot be told from "cannot report partitions" (the matrix measured `disk.details`
        # as empty for a disk that has a partition), so every empty list stays unknown.
        joined = any(isinstance(d.get("partitions"), list) and d["partitions"] for d in details.values()) or any(
            isinstance(d.get("partitions"), list) and d["partitions"] for d in queried.values())
        disks = []
        for name in sorted(queried):
            q = queried[name]
            detail = details.get(name, {})
            raw_parts = detail.get("partitions", q.get("partitions"))
            parts = tuple(parse_partition(p) for p in raw_parts) if isinstance(raw_parts, list) else None
            pool = q.get("pool") or detail.get("imported_zpool") or None
            if parts == () and (pool or not joined):
                parts = None  # an empty list that contradicts pool membership, or from an API that joins no partitions at all
            disks.append(Disk(name=name, size=_size(q.get("size")), model=str(q.get("model") or ""), serial=str(q.get("serial") or ""),
                              kind=str(q.get("type") or ""), pool=pool, partitions=parts))
        return disks

    @staticmethod
    def disk_details(result: Any) -> dict[str, Mapping[str, Any]]:
        """disk.details answers {'used': [...], 'unused': [...]} or a flat list depending on the requested type."""
        rows = (result.get("used", []) + result.get("unused", [])) if isinstance(result, dict) else (result or [])
        return {str(r.get("name")): r for r in rows if isinstance(r, dict)}
