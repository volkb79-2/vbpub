"""Let the pool wizard use one explicitly reserved boot-disk partition.

The shim offers only an unused GPT partition labelled ``boot-disk-data`` on
the disk that contains ``boot-pool``. It creates a stock-property ZFS pool by
PARTUUID, then adopts the pool through middleware. It never formats a whole
disk, and destructive export is refused because stock middleware resolves a
partition vdev to its parent disk before wiping it.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import re

from middlewared.plugins.pool_.utils import ZPOOL_CACHE_FILE
from middlewared.service import CallError, private, Service

RESERVED_PARTITION_LABEL = "boot-disk-data"
ZFS_GPT_TYPE_GUID = "6a898cc3-1dd2-11b2-99a6-080020736631"
MIN_PARTITION_NUMBER = 4
_LSBLK_COLUMNS = "PATH,NAME,PKNAME,PARTLABEL,PARTTYPE,PARTUUID,PARTN,SIZE,FSTYPE,MOUNTPOINT,TYPE"
_PARTUUID = re.compile(r"^[0-9a-fA-F-]{36}$")
_POOL_GUID = re.compile(r"^\d+$")
_COMMAND_TIMEOUT = 15
ALLOWED_CREATE_FIELDS = frozenset({"name", "topology"})
REFUSED_CREATE_FIELDS = frozenset({
    "all_sed", "allow_duplicate_serials", "checksum", "dedup_table_quota",
    "dedup_table_quota_value", "deduplication", "encryption", "encryption_options",
    "force_topology",
})
_POOL_CREATE_SCHEMA = json.loads(
    Path(__file__).with_name("pool_create_schema_v27.json").read_text(encoding="utf-8")
)
if _POOL_CREATE_SCHEMA.get("method") != "pool.create" or set(_POOL_CREATE_SCHEMA.get("fields", {})) != (
    ALLOWED_CREATE_FIELDS | REFUSED_CREATE_FIELDS
):
    raise RuntimeError("recorded v27 pool.create schema does not match the shim field classifications")


def _walk_lsblk(lsblk_json: dict):
    def visit(node: dict):
        yield node
        for child in node.get("children") or []:
            yield from visit(child)

    for node in lsblk_json.get("blockdevices", []):
        yield from visit(node)


def _node_index(lsblk_json: dict) -> tuple[dict[str, dict], dict[str, dict]]:
    by_path: dict[str, dict] = {}
    by_partuuid: dict[str, dict] = {}
    for node in _walk_lsblk(lsblk_json):
        path = node.get("path")
        if path:
            by_path[path] = node
        if partuuid := node.get("partuuid"):
            by_partuuid[str(partuuid).lower()] = node
    return by_path, by_partuuid


def _lookup_device(path: str, by_path: dict[str, dict], by_partuuid: dict[str, dict]) -> dict | None:
    if node := by_path.get(path):
        return node
    if path.startswith("/dev/disk/by-partuuid/"):
        return by_partuuid.get(path.rsplit("/", 1)[-1].lower())
    return None


def _parent_disk_path(node: dict, by_path: dict[str, dict]) -> str | None:
    if node.get("type") == "disk":
        return node.get("path")
    parent = node.get("pkname")
    if parent in by_path and by_path[parent].get("type") == "disk":
        return parent
    return parent if isinstance(parent, str) and parent.startswith("/dev/") else None


def boot_disk_paths_from_lsblk(lsblk_json: dict, boot_pool_leaf_paths: set[str]) -> set[str]:
    """Resolve boot-pool leaf paths to their containing whole-disk paths."""
    by_path, by_partuuid = _node_index(lsblk_json)
    result = set()
    for path in boot_pool_leaf_paths:
        node = _lookup_device(path, by_path, by_partuuid)
        if node is None:
            continue
        if disk_path := _parent_disk_path(node, by_path):
            result.add(disk_path)
    return result


def _partition_identity_rows(lsblk_json: dict, boot_pool_leaf_paths: set[str]) -> list[dict]:
    by_path, _ = _node_index(lsblk_json)
    boot_disks = boot_disk_paths_from_lsblk(lsblk_json, boot_pool_leaf_paths)
    rows = []
    for node in _walk_lsblk(lsblk_json):
        if node.get("type") != "part":
            continue
        try:
            number = int(node.get("partn") or 0)
            size = int(node.get("size") or 0)
        except (TypeError, ValueError):
            continue
        path = node.get("path")
        partuuid = str(node.get("partuuid") or "")
        parent = _parent_disk_path(node, by_path)
        if not (
            number >= MIN_PARTITION_NUMBER
            and size > 0
            and node.get("partlabel") == RESERVED_PARTITION_LABEL
            and str(node.get("parttype") or "").lower() == ZFS_GPT_TYPE_GUID
            and _PARTUUID.fullmatch(partuuid)
            and parent in boot_disks
            and isinstance(path, str)
        ):
            continue
        rows.append({
            "name": os.path.basename(path),
            "path": path,
            "parent": os.path.basename(parent),
            "parent_path": parent,
            "size": size,
            "partuuid": partuuid.lower(),
            "partlabel": node.get("partlabel"),
            "parttype": str(node.get("parttype") or "").lower(),
            "partn": number,
            "fstype": node.get("fstype"),
            "mountpoint": node.get("mountpoint"),
        })
    return rows


def reserved_partitions_from_lsblk(
    lsblk_json: dict,
    pool_leaf_paths: set[str],
    boot_pool_leaf_paths: set[str],
) -> list[dict]:
    """Pick unused marked partitions on the disk that contains boot-pool."""
    found = []
    for row in _partition_identity_rows(lsblk_json, boot_pool_leaf_paths):
        if row["fstype"] or row["mountpoint"] or row["path"] in pool_leaf_paths:
            continue
        found.append({key: row[key] for key in (
            "name", "path", "parent", "parent_path", "size", "partuuid",
        )})
    return sorted(found, key=lambda row: row["path"])


def unsupported_create_options(data: dict) -> list[str]:
    """Refuse every reserved-pool create field except its schema default."""
    unsupported = []
    if not isinstance(data, dict):
        return ["pool.create payload"]
    fields = _POOL_CREATE_SCHEMA["fields"]
    for name, value in data.items():
        if name in ALLOWED_CREATE_FIELDS:
            continue
        if name not in REFUSED_CREATE_FIELDS or name not in fields:
            unsupported.append(name)
            continue
        field_schema = fields[name]
        if "default" in field_schema:
            default = field_schema["default"]
        elif "nested_defaults" in field_schema:
            default = field_schema["nested_defaults"]
        else:
            unsupported.append(name)
            continue
        if not _matches_schema_default(value, default):
            unsupported.append(name)
    return unsupported


def _matches_schema_default(value, default) -> bool:
    if hasattr(value, "get_secret_value"):
        value = value.get_secret_value()
    if isinstance(default, dict):
        if not isinstance(value, dict) or value.keys() != default.keys():
            return False
        return all(_matches_schema_default(value[key], child) for key, child in default.items())
    if isinstance(default, list):
        return isinstance(value, list) and len(value) == len(default) and all(
            _matches_schema_default(actual, expected)
            for actual, expected in zip(value, default, strict=True)
        )
    return type(value) is type(default) and value == default


def match_topology(topology: dict, reserved: list[dict]) -> tuple[dict | None, str | None]:
    """Return the selected reserved partition or a reason to refuse the topology."""
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


def _status_leaf_paths(output: str) -> set[str]:
    return {token for token in output.split() if token.startswith("/dev/")}


def _pool_import_records(output: str) -> list[dict]:
    """Parse pool names and numeric GUIDs from the read-only ``zpool import`` scan."""
    starts = list(re.finditer(r"(?m)^\s*pool:\s*(\S+)\s*$", output))
    records = []
    for index, start in enumerate(starts):
        end = starts[index + 1].start() if index + 1 < len(starts) else len(output)
        section = output[start.start():end]
        guid = re.search(r"(?m)^\s*id:\s*(\d+)\s*$", section)
        records.append({"name": start.group(1), "guid": guid.group(1) if guid else None})
    return records


def _topology_leaf_paths(topology) -> set[str]:
    paths = set()

    def visit(value):
        if isinstance(value, dict):
            if value.get("type") == "DISK":
                for key in ("path", "device", "name"):
                    path = value.get(key)
                    if isinstance(path, str) and path.startswith("/dev/"):
                        paths.add(path)
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(topology)
    return paths


class BootDiskPartitionService(Service):

    class Config:
        namespace = "boot_disk_partition"
        private = True

    async def _run(self, *argv: str, timeout: int = _COMMAND_TIMEOUT) -> tuple[int, str, str]:
        proc = await asyncio.create_subprocess_exec(
            *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.communicate()
            raise
        return proc.returncode, out.decode(), err.decode()

    async def _read_lsblk(self) -> dict:
        code, out, err = await self._run(
            "lsblk", "-J", "-b", "-p", "-o", _LSBLK_COLUMNS, timeout=_COMMAND_TIMEOUT,
        )
        if code:
            raise CallError(f"lsblk failed while checking reserved partitions: {err.strip()}")
        try:
            return json.loads(out, object_pairs_hook=lambda pairs: {key.lower(): value for key, value in pairs})
        except (TypeError, ValueError) as exc:
            raise CallError("lsblk returned invalid JSON while checking reserved partitions") from exc

    async def _read_boot_pool_leaf_paths(self) -> set[str]:
        code, status, err = await self._run(
            "zpool", "status", "-P", "-L", "boot-pool", timeout=_COMMAND_TIMEOUT,
        )
        if code:
            raise CallError(f"could not read boot-pool leaves: {err.strip()}")
        return _status_leaf_paths(status)

    async def _verify_partition_identity(self, expected: dict) -> dict:
        """Re-read PARTUUID, GPT marker, and boot-disk parent before each write."""
        try:
            lsblk = await self._read_lsblk()
            boot_leaves = await self._read_boot_pool_leaf_paths()
        except Exception as exc:
            raise CallError(f"could not re-verify reserved partition identity: {exc}") from exc
        rows = _partition_identity_rows(lsblk, boot_leaves)
        row = next((item for item in rows if item["path"] == expected["path"]), None)
        if row is None or (
            row["partuuid"] != expected["partuuid"]
            or row["parent_path"] != expected.get("parent_path")
        ):
            raise CallError(f"{expected['path']} changed identity or is no longer on the boot-pool disk")
        return row

    async def _verify_labelclear_target(self, expected: dict, device: str) -> dict:
        """Recheck the reserved parent and mount state at the label-clear boundary."""
        row = await self._verify_partition_identity(expected)
        if row.get("parent_path") != expected.get("parent_path"):
            raise CallError(
                f"{expected['path']} is no longer on its verified boot-pool disk; labels were left untouched"
            )
        if row.get("mountpoint"):
            raise CallError(
                f"refusing to clear labels on mounted partition {expected['path']} "
                f"({row['mountpoint']!r})"
            )
        try:
            code, mounts, err = await self._run(
                "findmnt", "-rn", "-S", device, timeout=_COMMAND_TIMEOUT,
            )
        except asyncio.TimeoutError as exc:
            raise CallError(
                f"timed out rechecking mount state for {expected['path']}; labels were left untouched"
            ) from exc
        if code == 0:
            raise CallError(
                f"refusing to clear labels on mounted partition {expected['path']}: "
                f"{mounts.strip() or err.strip() or 'findmnt found a mount'}"
            )
        if code != 1:
            raise CallError(
                f"could not verify mount state for {expected['path']}: {err.strip() or code}; "
                "labels were left untouched"
            )
        return row

    async def _pool_name_available(self, name: str) -> None:
        try:
            code, imported, err = await self._run(
                "zpool", "list", "-H", "-o", "name", timeout=_COMMAND_TIMEOUT,
            )
        except asyncio.TimeoutError as exc:
            raise CallError(
                f"timed out after {_COMMAND_TIMEOUT}s listing imported pools before creating {name!r}"
            ) from exc
        if code:
            raise CallError(f"could not check imported pool names before creating {name!r}: {err.strip()}")
        try:
            code, exported, err = await self._run("zpool", "import", timeout=_COMMAND_TIMEOUT)
        except asyncio.TimeoutError as exc:
            raise CallError(
                f"timed out after {_COMMAND_TIMEOUT}s scanning exported pools before creating {name!r}"
            ) from exc
        if code:
            raise CallError(f"could not scan exported pools before creating {name!r}: {err.strip()}")
        exported_names = {record["name"] for record in _pool_import_records(exported)}
        if name in set(imported.split()) or name in exported_names:
            raise CallError(f"pool {name!r} already exists as an imported or exported pool")

    async def _assert_pool_on_partition(self, name: str, partition: dict) -> None:
        code, status, err = await self._run(
            "zpool", "status", "-P", "-L", name, timeout=_COMMAND_TIMEOUT,
        )
        leaves = _status_leaf_paths(status)
        if code or leaves != {partition["path"]}:
            raise CallError(
                f"pool {name!r} is not verified as using only reserved partition "
                f"{partition['path']}: {err.strip() or sorted(leaves)!r}"
            )

    async def _clear_created_partition(self, name: str, partition: dict, device: str) -> None:
        await self._verify_partition_identity(partition)
        code, status, err = await self._run("zpool", "status", "-P", "-L", timeout=_COMMAND_TIMEOUT)
        if code or partition["path"] in _status_leaf_paths(status):
            raise CallError(f"cannot clear labels while {partition['path']} may still be imported: {err.strip()}")
        try:
            code, scan, err = await self._run("zpool", "import", "-d", device, timeout=_COMMAND_TIMEOUT)
        except asyncio.TimeoutError as exc:
            raise CallError(
                f"timed out after {_COMMAND_TIMEOUT}s checking exported pools on {partition['path']}; "
                "partition labels were left untouched"
            ) from exc
        if code or _pool_import_records(scan):
            raise CallError(
                f"cannot clear labels while an exported pool may remain on {partition['path']}: "
                f"{err.strip() or _pool_import_records(scan)!r}; partition labels were left untouched"
            )
        await self._verify_labelclear_target(partition, device)
        code, _, err = await self._run("zpool", "labelclear", "-f", device, timeout=_COMMAND_TIMEOUT)
        if code:
            raise CallError(f"labelclear failed on verified partition {partition['path']}: {err.strip()}")

    async def _read_pool_guid(self, name: str) -> str:
        code, output, err = await self._run(
            "zpool", "get", "-H", "-o", "value", "guid", name, timeout=_COMMAND_TIMEOUT,
        )
        guid = output.strip()
        if code or not _POOL_GUID.fullmatch(guid):
            raise CallError(
                f"could not capture/verify the numeric GUID for pool {name!r}: {err.strip() or guid!r}"
            )
        return guid

    async def _assert_pool_guid(self, name: str, expected_guid: str) -> None:
        actual_guid = await self._read_pool_guid(name)
        if actual_guid != expected_guid:
            raise CallError(
                f"pool {name!r} GUID changed: expected {expected_guid}, found {actual_guid}; "
                "pool was left untouched for manual recovery"
            )

    async def _query_pool_rows(self, name: str) -> list[dict]:
        rows = await self.middleware.call(
            "pool.query", [["name", "=", name]], {"select": ["id", "name", "guid"]},
        )
        if not isinstance(rows, list):
            raise CallError(f"pool.query returned {type(rows).__name__} while checking {name!r}")
        return rows

    async def _preserve_partial_registration(
        self, name: str, partition: dict, expected_guid: str,
    ) -> bool:
        """Unregister a partial import through middleware while preserving its pool."""
        rows = await self._query_pool_rows(name)
        if not rows:
            return False
        if (
            len(rows) != 1
            or rows[0].get("name") != name
            or str(rows[0].get("guid")) != expected_guid
            or not isinstance(rows[0].get("id"), int)
        ):
            raise CallError(
                f"middleware pool row for {name!r} does not identify captured GUID {expected_guid}; "
                "pool and row were left untouched for manual recovery"
            )
        await self._assert_pool_guid(name, expected_guid)
        await self._assert_pool_on_partition(name, partition)
        job = await self.middleware.call("pool.export", rows[0]["id"], {"destroy": False})
        await job.wait(raise_error=True)

        remaining = await self._query_pool_rows(name)
        if remaining:
            raise CallError(f"middleware pool row for {name!r} remains after pool.export destroy=false")
        code, status, err = await self._run(
            "zpool", "status", "-P", "-L", timeout=_COMMAND_TIMEOUT,
        )
        if code or partition["path"] in _status_leaf_paths(status):
            raise CallError(
                f"pool.export did not leave {name!r} exported from {partition['path']}: {err.strip()}"
            )
        device = f"/dev/disk/by-partuuid/{partition['partuuid']}"
        code, scan, err = await self._run("zpool", "import", "-d", device, timeout=_COMMAND_TIMEOUT)
        records = _pool_import_records(scan)
        if code or len(records) != 1 or records[0] != {"name": name, "guid": expected_guid}:
            raise CallError(
                f"middleware row was removed, but exported pool GUID {expected_guid} could not be "
                f"verified on {partition['path']}: {err.strip() or records!r}"
            )
        return True

    async def _rollback_created_pool(self, name: str, partition: dict, expected_guid: str) -> None:
        """Destroy only the captured pool GUID proved on the verified PARTUUID."""
        if not _POOL_GUID.fullmatch(expected_guid):
            raise CallError("rollback has no captured numeric pool GUID; manual recovery is required")
        device = f"/dev/disk/by-partuuid/{partition['partuuid']}"
        await self._verify_partition_identity(partition)
        code, status, err = await self._run(
            "zpool", "status", "-P", "-L", name, timeout=_COMMAND_TIMEOUT,
        )
        if code == 0:
            await self._assert_pool_guid(name, expected_guid)
            await self._assert_pool_on_partition(name, partition)
        else:
            try:
                scan_code, scan, scan_err = await self._run(
                    "zpool", "import", "-d", device, timeout=_COMMAND_TIMEOUT,
                )
            except asyncio.TimeoutError as exc:
                raise CallError(
                    f"timed out after {_COMMAND_TIMEOUT}s scanning exported rollback pools on "
                    f"{partition['path']}; partition labels were left untouched"
                ) from exc
            if scan_code:
                raise CallError(
                    f"could not inspect exported rollback pool on {partition['path']}: "
                    f"{scan_err.strip()}; partition labels were left untouched"
                )
            records = _pool_import_records(scan)
            if records:
                if len(records) != 1 or records[0] != {"name": name, "guid": expected_guid}:
                    raise CallError(
                        f"rollback scan did not find only captured pool GUID {expected_guid} named {name!r}; "
                        "partition labels were left untouched"
                    )
                await self._verify_partition_identity(partition)
                code, _, err = await self._run(
                    "zpool", "import", "-f", "-N", expected_guid, timeout=_COMMAND_TIMEOUT,
                )
                if code:
                    raise CallError(f"could not re-import the created pool for rollback: {err.strip()}")
                await self._assert_pool_guid(name, expected_guid)
                await self._assert_pool_on_partition(name, partition)
            else:
                raise CallError(
                    f"captured pool GUID {expected_guid} is neither imported nor found in the "
                    f"export scan on {partition['path']}; labels were left untouched for manual recovery"
                )

        await self._verify_partition_identity(partition)
        await self._assert_pool_guid(name, expected_guid)
        await self._assert_pool_on_partition(name, partition)
        code, _, err = await self._run("zpool", "destroy", "-f", name, timeout=_COMMAND_TIMEOUT)
        if code:
            raise CallError(f"zpool destroy -f failed for verified pool {name!r}: {err.strip()}")
        await self._clear_created_partition(name, partition, device)

    async def _assert_pool_unregistered(self, name: str) -> None:
        """Report a stale middleware row left by a partially failed import."""
        try:
            rows = await self._query_pool_rows(name)
        except Exception as exc:
            raise CallError(
                f"could not verify middleware registration cleanup for pool {name!r}: {exc}"
            ) from exc
        if not isinstance(rows, list):
            raise CallError(
                f"could not verify middleware registration cleanup for pool {name!r}: "
                f"pool.query returned {type(rows).__name__}"
            )
        if any(isinstance(row, dict) and row.get("name") == name for row in rows):
            raise CallError(
                f"stale middleware pool record for {name!r} remains after verified ZFS rollback"
            )

    @private
    async def reserved(self) -> list[dict]:
        try:
            lsblk = await self._read_lsblk()
            code, status, err = await self._run(
                "zpool", "status", "-P", "-L", timeout=_COMMAND_TIMEOUT,
            )
            if code:
                raise CallError(f"could not read imported pool leaves: {err.strip()}")
            boot_leaves = await self._read_boot_pool_leaf_paths()
            return reserved_partitions_from_lsblk(
                lsblk, _status_leaf_paths(status), boot_leaves,
            )
        except Exception:
            self.logger.warning("Could not inspect reserved boot-disk partitions", exc_info=True)
            return []

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
    async def unsupported_create_options(self, data: dict) -> list[str]:
        return unsupported_create_options(data)

    @private
    async def pool_uses_reserved_partition(self, topology: dict) -> bool:
        """Identify any data partition 4+ on the disk that contains boot-pool."""
        leaves = _topology_leaf_paths(topology)
        if not leaves:
            return False
        try:
            lsblk = await self._read_lsblk()
            boot_leaves = await self._read_boot_pool_leaf_paths()
        except Exception as exc:
            raise CallError(
                f"could not verify pool backing against the reserved boot-disk partition; "
                f"refusing destructive export: {exc}"
            ) from exc
        boot_disks = boot_disk_paths_from_lsblk(lsblk, boot_leaves)
        real_leaves = {os.path.realpath(path) for path in leaves}
        for node in _walk_lsblk(lsblk):
            if node.get("type") != "part":
                continue
            try:
                number = int(node.get("partn") or 0)
            except (TypeError, ValueError):
                continue
            path = node.get("path")
            if (
                number >= MIN_PARTITION_NUMBER
                and isinstance(path, str)
                and _parent_disk_path(node, _node_index(lsblk)[0]) in boot_disks
                and (path in leaves or os.path.realpath(path) in real_leaves)
            ):
                return True
        return False

    @private
    async def create_pool(self, name: str, partition: dict) -> None:
        """Create on one PARTUUID, then adopt; roll back only that verified pool."""
        current = {row["path"]: row for row in await self.reserved()}
        row = current.get(partition["path"])
        if row is None or row["partuuid"] != partition["partuuid"]:
            raise CallError(f"{partition['path']} is no longer an unused reserved partition")

        await self._pool_name_available(name)
        row = await self._verify_partition_identity(row)
        device = f"/dev/disk/by-partuuid/{row['partuuid']}"
        created_guid: str | None = None
        try:
            try:
                code, _, err = await self._run(
                    "zpool", "create",
                    "-o", "feature@lz4_compress=enabled",
                    "-o", "altroot=/mnt",
                    "-o", f"cachefile={ZPOOL_CACHE_FILE}",
                    "-o", "failmode=continue",
                    "-o", "autoexpand=on",
                    "-o", "ashift=12",
                    "-O", "atime=off",
                    "-O", "acltype=posix",
                    "-O", "aclinherit=discard",
                    "-O", "aclmode=discard",
                    "-O", "compression=lz4",
                    "-O", "xattr=sa",
                    "-O", f"mountpoint=/{name}",
                    name, device,
                    timeout=_COMMAND_TIMEOUT,
                )
            except asyncio.TimeoutError as exc:
                raise CallError(
                    f"zpool create timed out after {_COMMAND_TIMEOUT}s on verified partition {row['path']}"
                ) from exc
            if code:
                raise CallError(
                    f"zpool create failed on verified partition {row['path']}: {err.strip()}; "
                    "the outcome is ambiguous and no pool GUID was captured"
                )

            created_guid = await self._read_pool_guid(name)

            await self._assert_pool_guid(name, created_guid)
            await self._assert_pool_on_partition(name, row)
            await self._verify_partition_identity(row)
            await self._assert_pool_on_partition(name, row)
            code, _, err = await self._run("zpool", "export", name, timeout=_COMMAND_TIMEOUT)
            if code:
                raise CallError(f"zpool export of the new pool {name!r} failed: {err.strip()}")

            find_job = await self.middleware.call("pool.import_find")
            found = await find_job.wait(raise_error=True)
            related = [
                item for item in found or []
                if item.get("name") == name or str(item.get("guid")) == created_guid
            ]
            if (
                len(related) != 1
                or related[0].get("name") != name
                or str(related[0].get("guid")) != created_guid
            ):
                raise CallError(
                    f"pool GUID {created_guid} created on {row['path']} did not have one exact "
                    f"name/GUID match in import discovery: {related!r}"
                )
            await self._verify_partition_identity(row)
            import_job = await self.middleware.call(
                "pool.import_pool", {"guid": created_guid, "name": name},
            )
            await import_job.wait(raise_error=True)
            await self._assert_pool_guid(name, created_guid)
            await self._assert_pool_on_partition(name, row)
        except Exception as exc:
            if created_guid is None:
                raise CallError(
                    f"pool {name!r} creation/adoption failed before a GUID was captured: {exc}; "
                    "the create outcome is ambiguous, labels were left untouched, and manual recovery is required"
                ) from exc
            preserved = False
            try:
                preserved = await self._preserve_partial_registration(name, row, created_guid)
                if not preserved:
                    await self._rollback_created_pool(name, row, created_guid)
                    await self._assert_pool_unregistered(name)
            except Exception as cleanup_exc:
                raise CallError(
                    f"pool {name!r} creation/adoption failed: {exc}; cleanup incomplete: {cleanup_exc}"
                ) from exc
            if preserved:
                raise CallError(
                    f"pool {name!r} creation/adoption failed: {exc}; partial middleware registration "
                    f"was removed through pool.export destroy=false and captured GUID {created_guid} "
                    "was left exported for manual recovery"
                ) from exc
            raise CallError(
                f"pool {name!r} creation/adoption failed: {exc}; verified reserved partition cleanup completed"
            ) from exc
