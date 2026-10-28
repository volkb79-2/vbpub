"""Let the pool wizard create a pool on a reserved boot-disk partition.

The stock UI wizard offers only whole disks from ``disk.details`` and
``pool.create`` accepts only disk names it can format. Path B reserves a GPT
partition labelled ``boot-disk-data`` (ZFS type) behind the boot pool. This
service lists such an unused partition as a pseudo-disk and creates the pool on
it with ``zpool create`` (no whole-disk formatting), then adopts it through the
same ``pool.import_find`` / ``pool.import_pool`` path the registered Path B
provisioning lane proves.

Safety rules: only partition number >= 4 whose GPT name is exactly the marker,
whose GPT type is ZFS, which carries no filesystem signature, is unmounted and
is not part of any pool, is ever offered. The boot pool's own partitions never
match, so mirroring them (``boot.attach``) stays refused. No other device is
touched and ``zpool create`` never runs with ``-f``.
"""

from __future__ import annotations

import asyncio
import json
import os
import re

from middlewared.service import CallError, private, Service

RESERVED_PARTITION_LABEL = "boot-disk-data"
ZFS_GPT_TYPE_GUID = "6a898cc3-1dd2-11b2-99a6-080020736631"
MIN_PARTITION_NUMBER = 4
_LSBLK_COLUMNS = "PATH,NAME,PKNAME,PARTLABEL,PARTTYPE,PARTUUID,PARTN,SIZE,FSTYPE,MOUNTPOINT,TYPE"
_PARTUUID = re.compile(r"^[0-9a-fA-F-]{36}$")


def reserved_partitions_from_lsblk(lsblk_json: dict, pool_leaf_paths: set[str]) -> list[dict]:
    """Pick the unused reserved partitions out of ``lsblk -J -b -p`` output."""
    found: list[dict] = []

    def visit(node: dict) -> None:
        if node.get("type") == "part" and _is_reserved(node, pool_leaf_paths):
            found.append({
                "name": os.path.basename(node["path"]),
                "path": node["path"],
                "parent": os.path.basename(node.get("pkname") or ""),
                "size": int(node["size"]),
                "partuuid": node["partuuid"].lower(),
            })
        for child in node.get("children") or []:
            visit(child)

    for node in lsblk_json.get("blockdevices", []):
        visit(node)
    return sorted(found, key=lambda row: row["path"])


def _is_reserved(node: dict, pool_leaf_paths: set[str]) -> bool:
    try:
        number = int(node.get("partn") or 0)
        size = int(node.get("size") or 0)
    except (TypeError, ValueError):
        return False
    partuuid = node.get("partuuid") or ""
    return bool(
        number >= MIN_PARTITION_NUMBER
        and size > 0
        and node.get("partlabel") == RESERVED_PARTITION_LABEL
        and str(node.get("parttype") or "").lower() == ZFS_GPT_TYPE_GUID
        and _PARTUUID.match(partuuid)
        and not node.get("fstype")
        and not node.get("mountpoint")
        and node.get("path") not in pool_leaf_paths
    )


def match_topology(topology: dict, reserved: list[dict]) -> tuple[dict | None, str | None]:
    """Return ``(partition, None)`` for a valid shim request, ``(None, error)`` for a
    request that mixes reserved partitions with anything else, ``(None, None)`` if the
    topology does not mention a reserved partition at all."""
    by_name = {row["name"]: row for row in reserved}
    mentioned = [
        disk
        for kind in ("data", "cache", "log", "special", "dedup")
        for vdev in topology.get(kind) or []
        for disk in vdev["disks"]
    ] + list(topology.get("spares") or [])
    hits = [disk for disk in mentioned if disk in by_name]
    if not hits:
        return None, None
    if len(mentioned) != 1 or len(hits) != 1:
        return None, "A reserved boot-disk partition can only be the single disk of a one-disk data vdev."
    data = topology.get("data") or []
    if len(data) != 1 or data[0]["type"] != "STRIPE":
        return None, "A reserved boot-disk partition can only be used as a single-disk Stripe data vdev."
    return by_name[hits[0]], None


class BootDiskPartitionService(Service):

    class Config:
        namespace = "boot_disk_partition"
        private = True

    async def _run(self, *argv: str) -> tuple[int, str, str]:
        proc = await asyncio.create_subprocess_exec(
            *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        out, err = await proc.communicate()
        return proc.returncode, out.decode(), err.decode()

    @private
    async def reserved(self) -> list[dict]:
        code, out, _ = await self._run("lsblk", "-J", "-b", "-p", "-o", _LSBLK_COLUMNS)
        if code:
            return []
        try:
            lsblk = json.loads(out, object_pairs_hook=lambda pairs: {k.lower(): v for k, v in pairs})
        except ValueError:
            return []
        code, status, _ = await self._run("zpool", "status", "-P", "-L")
        leaves = {tok for tok in status.split() if tok.startswith("/dev/")} if code == 0 else set()
        return reserved_partitions_from_lsblk(lsblk, leaves)

    @private
    async def pseudo_disks(self, rows: list[dict]) -> list[dict]:
        """Build disk.details rows for the reserved partitions from their parent disk rows."""
        result = []
        for part in await self.reserved():
            parent = next((row for row in rows if row["name"] == part["parent"]), None)
            if parent is None:
                continue
            result.append(parent | {
                "name": part["name"],
                "devname": part["name"],
                "identifier": "{partuuid}" + part["partuuid"],
                "size": part["size"],
                "model": f"Reserved partition of {parent.get('model') or parent['name']}",
                "serial": "",
                "lunid": None,
                "partitions": [],
                "imported_zpool": None,
                "exported_zpool": None,
                "duplicate_serial": [],
                "pool": None,
                "zfs_guid": None,
            })
        return result

    @private
    async def topology_match(self, topology: dict) -> tuple[dict | None, str | None]:
        return match_topology(topology, await self.reserved())

    @private
    async def create_pool(self, name: str, partition: dict) -> None:
        """zpool create on the verified partition, export, then adopt via middleware."""
        current = {row["path"]: row for row in await self.reserved()}
        row = current.get(partition["path"])
        if row is None or row["partuuid"] != partition["partuuid"]:
            raise CallError(f"{partition['path']} is no longer an unused reserved partition")
        by_id = row["path"]
        code, _, err = await self._run(
            "zpool", "create", "-o", "cachefile=none", "-O", "mountpoint=none", name, by_id,
        )
        if code:
            raise CallError(f"zpool create failed on {row['path']}: {err.strip()}")
        code, _, err = await self._run("zpool", "export", name)
        if code:
            raise CallError(f"zpool export of the new pool {name!r} failed: {err.strip()}")
        found = await (await self.middleware.call("pool.import_find")).wait(raise_error=True)
        matches = [item for item in found or [] if item.get("name") == name]
        if len(matches) != 1 or not matches[0].get("guid"):
            raise CallError(
                f"pool {name!r} was created on {row['path']} but import discovery found {len(matches)} matches"
            )
        await (await self.middleware.call(
            "pool.import_pool", {"guid": matches[0]["guid"], "name": name},
        )).wait(raise_error=True)
