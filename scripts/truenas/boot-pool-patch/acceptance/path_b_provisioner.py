"""Provision Path B on one explicitly selected TrueNAS boot disk.

Partition-table and ZFS mutations are intentionally separate from the API
acceptance lane. The caller must supply the exact device and confirmation;
this module only uses an unused tail after the live boot-pool partition.
"""

from __future__ import annotations

import base64
import json
import re
import shlex
from pathlib import Path
import sys
import time
import uuid
from typing import Any

SHARED = Path(__file__).resolve().parents[1] / "shared"
sys.path.insert(0, str(SHARED))
from path_b_layout import (
    DATA_POOL_NAME,
    DEFAULT_MINIMUM_FREE_BYTES,
    GPTLayout,
    LayoutError,
    PathBPlan,
    make_path_b_plan,
    parse_sgdisk_print,
    parse_sgdisk_sector,
    validate_disk_path,
    verify_existing_path_b,
)
from remote import RemoteError, Target


def _report(message: str) -> None:
    print(f"[path-b] {message}", flush=True)


def _json_output(target: Target, argv: list[str]) -> dict[str, Any]:
    output = target.run(argv, timeout=90)
    try:
        value = json.loads(output)
    except json.JSONDecodeError as exc:
        raise RemoteError(f"{shlex.join(argv)} returned invalid JSON") from exc
    if not isinstance(value, dict):
        raise RemoteError(f"{shlex.join(argv)} returned an unexpected JSON shape")
    return value


def _canonical_device(target: Target, path: str) -> str:
    value = target.run(["readlink", "-e", path])
    return validate_disk_path(value)


def _partition_nodes(disk_json: dict[str, Any]) -> dict[int, dict[str, Any]]:
    roots = disk_json.get("blockdevices")
    if not isinstance(roots, list) or len(roots) != 1 or not isinstance(roots[0], dict):
        raise RemoteError("lsblk did not return exactly one selected disk")
    root = roots[0]
    if root.get("type") != "disk":
        raise RemoteError(f"selected device is {root.get('type')!r}, not a whole disk")
    if root.get("ro") not in (False, 0, "0") or root.get("rm") not in (False, 0, "0"):
        raise RemoteError("selected disk is read-only or removable")

    result: dict[int, dict[str, Any]] = {}
    for item in root.get("children") or []:
        if not isinstance(item, dict) or item.get("type") != "part":
            raise RemoteError("selected disk contains a non-partition child; layout is ambiguous")
        number = item.get("partn")
        if type(number) is not int or number in result:
            raise RemoteError(f"lsblk returned an invalid partition number: {number!r}")
        result[number] = item
    return result


def _pool_leaf_paths(target: Target, pool_name: str) -> tuple[str, ...]:
    output = target.run(["zpool", "status", "-P", "-L", pool_name], timeout=45)
    paths = re.findall(r"^\s+(/dev/\S+)\s+(?:ONLINE|DEGRADED|FAULTED|UNAVAIL|OFFLINE)\b", output, re.M)
    if not paths:
        raise RemoteError(f"could not identify leaf devices for ZFS pool {pool_name!r}")
    return tuple(_canonical_device(target, path) for path in paths)


def _read_layout(target: Target, disk: str) -> tuple[GPTLayout, int, int, str]:
    table_output = target.run(["sgdisk", "--print", disk], timeout=45)
    layout = parse_sgdisk_print(table_output)
    verify = target.run(["sgdisk", "--verify", disk], timeout=45)
    if "No problems found" not in verify:
        raise LayoutError("sgdisk --verify did not certify the current GPT")
    start_output = target.run(["sgdisk", "--first-aligned-in-largest", disk], timeout=45)
    end_output = target.run(["sgdisk", "--end-of-largest", disk], timeout=45)
    start = parse_sgdisk_sector(start_output, label="--first-aligned-in-largest")
    end = parse_sgdisk_sector(end_output, label="--end-of-largest")
    return layout, start, end, table_output


def _importable_pools(target: Target) -> list[dict[str, Any]]:
    job_id = target.call("pool.import_find", timeout=120)
    if type(job_id) is not int:
        raise RemoteError(f"pool.import_find returned non-job result {job_id!r}")
    result = target.wait_job(job_id, timeout=180, label="pool.import_find")
    if not isinstance(result, list) or any(not isinstance(row, dict) for row in result):
        raise RemoteError("pool.import_find returned an unexpected result")
    return result


def _same_preexisting_partitions(before: tuple[Any, ...], after: GPTLayout) -> bool:
    return after.partitions[:len(before)] == before


def _existing_pool_result(
    target: Target,
    *,
    disk: str,
    boot_partition: str,
    layout: GPTLayout,
    boot_leaves: tuple[str, ...],
    pool_rows: list[dict[str, Any]],
    disk_partitions: dict[int, dict[str, Any]],
) -> str | None:
    if not pool_rows:
        return None
    if len(pool_rows) != 1:
        raise LayoutError(f"middleware returned duplicate {DATA_POOL_NAME} records")
    if 4 not in disk_partitions:
        raise LayoutError(f"middleware has {DATA_POOL_NAME}, but the selected GPT has no partition 4")
    pool = pool_rows[0]
    leaves = _pool_leaf_paths(target, DATA_POOL_NAME)
    data_device = verify_existing_path_b(
        disk=disk,
        boot_pool_partition=boot_partition,
        layout=layout,
        boot_pool_leaf_paths=boot_leaves,
        data_pool_leaf_paths=leaves,
        pool_status=str(pool.get("status", "")),
    )
    node = disk_partitions[4]
    if _canonical_device(target, str(node.get("path") or node.get("name"))) != data_device:
        raise LayoutError("lsblk partition 4 does not identify the registered Path B vdev")
    return data_device


def provision_path_b(
    target: Target,
    *,
    disk: str,
    boot_pool_partition: str,
    expected_version: str,
    minimum_free_bytes: int = DEFAULT_MINIMUM_FREE_BYTES,
    apply: bool = False,
    confirmed_disk: str | None = None,
    partition_only: bool = False,
) -> dict[str, Any]:
    """Inspect, plan, or provision a reserved-tail Path B pool.

    A repeated successful invocation returns ``already_configured`` without
    changes. A partial GPT/ZFS state is refused for manual inspection rather
    than guessed at or destroyed.
    """
    if target.user != "root":
        raise RemoteError("Path B provisioning requires an SSH connection as root")
    if not expected_version:
        raise RemoteError("an exact --expected-version is required")
    actual_disk = _canonical_device(target, disk)
    actual_boot_partition = _canonical_device(target, boot_pool_partition)
    if apply and confirmed_disk != disk:
        raise RemoteError("--apply requires --confirm-disk to exactly repeat --disk")
    if not apply and confirmed_disk is not None:
        raise RemoteError("--confirm-disk is only meaningful together with --apply")

    system = target.call("system.info")
    version = system.get("version") if isinstance(system, dict) else None
    if version != expected_version:
        raise RemoteError(f"expected TrueNAS {expected_version!r}, got {version!r}")
    if not str(version).startswith(("27.", "28.")):
        raise RemoteError(f"Path B provisioner supports TrueNAS 27+; got {version!r}")

    disk_json = _json_output(
        target,
        ["lsblk", "--json", "--tree", "--paths", "--bytes", "--output",
         "PATH,NAME,TYPE,SIZE,RO,RM,PARTN,PARTTYPE,PARTLABEL", actual_disk],
    )
    roots = disk_json.get("blockdevices")
    if not isinstance(roots, list) or len(roots) != 1 or not isinstance(roots[0], dict):
        raise RemoteError("lsblk did not identify exactly the requested disk")
    reported_disk = roots[0].get("path")
    if reported_disk and validate_disk_path(str(reported_disk)) != actual_disk:
        raise RemoteError(
            f"lsblk selected {reported_disk!r}, which differs from requested {actual_disk!r}"
        )
    partition_nodes = _partition_nodes(disk_json)
    layout, free_start, free_end, _ = _read_layout(target, actual_disk)
    boot_leaves = _pool_leaf_paths(target, "boot-pool")
    if actual_boot_partition not in {str(node.get("path")) for node in partition_nodes.values()}:
        raise LayoutError(f"{actual_boot_partition} is not a partition of {actual_disk}")

    pool_rows = target.query("pool.query", [["name", "=", DATA_POOL_NAME]], timeout=90)
    if not isinstance(pool_rows, list):
        raise RemoteError("pool.query returned an unexpected result")
    already = _existing_pool_result(
        target,
        disk=actual_disk,
        boot_partition=actual_boot_partition,
        layout=layout,
        boot_leaves=boot_leaves,
        pool_rows=pool_rows,
        disk_partitions=partition_nodes,
    )
    if already:
        if partition_only:
            raise LayoutError(
                f"{DATA_POOL_NAME} already exists; partition-only mode requires the UI to create the pool"
            )
        _report(f"{DATA_POOL_NAME} is already ONLINE on {already}; no changes made")
        return {"state": "already_configured", "disk": actual_disk, "partition": already}

    partition_manifest_path = "/root/truenas-path-b-backups/boot-disk-data-partition.json"
    if 4 in partition_nodes:
        if not partition_only:
            raise LayoutError(
                "partition 4 exists but middleware has no verified Path B pool; "
                "refusing to reuse or overwrite this partial/unknown state"
            )
        manifest_raw = target.run(["cat", partition_manifest_path], check=False)
        if not manifest_raw:
            raise LayoutError(
                "partition 4 exists without this provisioner's saved partition-only manifest; "
                "refusing to reuse or overwrite this partial/unknown state"
            )
        try:
            manifest = json.loads(manifest_raw)
        except json.JSONDecodeError as exc:
            raise LayoutError("saved Path B partition manifest is not valid JSON") from exc
        live_partitions = [
            {
                "number": part.number,
                "start": part.start,
                "end": part.end,
                "type_code": part.type_code,
                "name": part.name,
            }
            for part in layout.partitions
        ]
        if (
            manifest.get("schema") != 1
            or manifest.get("expected_version") != expected_version
            or manifest.get("disk") != actual_disk
            or manifest.get("boot_pool_partition") != actual_boot_partition
            or manifest.get("logical_sector_bytes") != layout.logical_sector_bytes
            or manifest.get("last_usable_sector") != layout.last_usable_sector
            or manifest.get("partitions") != live_partitions
        ):
            raise LayoutError("current Path B GPT differs from the saved partition-only manifest")
        if _pool_leaf_paths(target, "boot-pool") != (actual_boot_partition,):
            raise LayoutError("boot-pool no longer uses the selected partition 3")
        importable = _importable_pools(target)
        if any(row.get("name") == DATA_POOL_NAME for row in importable):
            raise LayoutError(
                f"an unregistered pool named {DATA_POOL_NAME} exists on the reserved partition"
            )
        _report(f"{DATA_POOL_NAME} partition {actual_disk}p4 already matches the saved safe plan; no changes made")
        return {
            "state": "already_partitioned",
            "disk": actual_disk,
            "partition": f"{actual_disk}p4" if actual_disk[-1:].isdigit() else f"{actual_disk}4",
        }
    importable = _importable_pools(target)
    name_conflicts = [row for row in importable if row.get("name") == DATA_POOL_NAME]
    if name_conflicts:
        raise LayoutError(
            f"an unregistered pool named {DATA_POOL_NAME} is already importable; "
            "inspect its device before provisioning"
        )

    plan = make_path_b_plan(
        disk=actual_disk,
        boot_pool_partition=actual_boot_partition,
        layout=layout,
        aligned_free_start=free_start,
        largest_free_end=free_end,
        boot_pool_leaf_paths=boot_leaves,
        minimum_free_bytes=minimum_free_bytes,
    )
    if partition_only:
        _report(
            f"TrueNAS {version}; boot-pool remains on {plan.boot_pool_partition}; "
            f"will reserve {plan.new_partition}, sectors {plan.start_sector}..{plan.end_sector} "
            f"({plan.partition_bytes} bytes) for pool creation through the TrueNAS UI"
        )
    else:
        _report(
            f"TrueNAS {version}; boot-pool remains on {plan.boot_pool_partition}; "
            f"will create {DATA_POOL_NAME} on {plan.new_partition}, sectors "
            f"{plan.start_sector}..{plan.end_sector} ({plan.partition_bytes} bytes)"
        )
    _report("partition 3 and all existing GPT extents will be preserved")
    if not apply:
        _report("dry run only; pass --apply --confirm-disk with the exact --disk path to proceed")
        return {
            "state": "planned",
            "disk": plan.disk,
            "partition": plan.new_partition,
            "start_sector": plan.start_sector,
            "end_sector": plan.end_sector,
            "bytes": plan.partition_bytes,
        }

    # Re-read critical facts immediately before the first write. If anything
    # changed since planning, make no partition-table modification.
    fresh_layout, fresh_start, fresh_end, _ = _read_layout(target, actual_disk)
    fresh_boot_leaves = _pool_leaf_paths(target, "boot-pool")
    if (
        fresh_layout != layout
        or fresh_start != free_start
        or fresh_end != free_end
        or fresh_boot_leaves != boot_leaves
    ):
        raise RemoteError("disk or boot-pool facts changed after preflight; refusing to write")
    fresh_pools = target.query("pool.query", [["name", "=", DATA_POOL_NAME]], timeout=90)
    if fresh_pools:
        raise RemoteError(f"{DATA_POOL_NAME} appeared after preflight; refusing to write")

    backup_path = (
        f"/root/truenas-path-b-gpt-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}-"
        f"{uuid.uuid4().hex[:8]}.bin"
    )
    target.run(["install", "-d", "-m", "0700", "/root/truenas-path-b-backups"])
    backup_path = backup_path.replace("/root/", "/root/truenas-path-b-backups/")
    target.run(["sgdisk", f"--backup={backup_path}", actual_disk], timeout=90)
    _report(f"saved the original GPT to {backup_path}")

    target.run([
        "sgdisk",
        f"--new=4:{plan.start_sector}:{plan.end_sector}",
        f"--typecode=4:{'BF01'}",
        f"--change-name=4:{DATA_POOL_NAME}",
        actual_disk,
    ], timeout=90)
    target.run(["partprobe", actual_disk], timeout=45)
    target.run(["udevadm", "settle"], timeout=45)

    written_layout, _, _, _ = _read_layout(target, actual_disk)
    if not _same_preexisting_partitions(plan.preexisting_partitions, written_layout):
        raise RemoteError("post-write GPT check found a changed preexisting partition")
    if len(written_layout.partitions) != 4:
        raise RemoteError("post-write GPT check did not find exactly one new partition 4")
    new_part = written_layout.partitions[3]
    if (
        new_part.number != 4
        or new_part.start != plan.start_sector
        or new_part.end != plan.end_sector
        or new_part.type_code != "BF01"
        or new_part.name != DATA_POOL_NAME
    ):
        raise RemoteError(f"post-write partition 4 differs from the plan: {new_part!r}")

    refreshed_disk = _json_output(
        target,
        ["lsblk", "--json", "--tree", "--paths", "--bytes", "--output",
         "PATH,NAME,TYPE,SIZE,RO,RM,PARTN,PARTTYPE,PARTLABEL", actual_disk],
    )
    refreshed_partitions = _partition_nodes(refreshed_disk)
    if 4 not in refreshed_partitions:
        raise RemoteError("kernel did not expose the newly created partition 4")
    new_device = _canonical_device(
        target, str(refreshed_partitions[4].get("path") or refreshed_partitions[4].get("name"))
    )
    if new_device != plan.new_partition:
        raise RemoteError(f"kernel partition path {new_device} differs from planned {plan.new_partition}")

    if partition_only:
        live_partitions = [
            {
                "number": part.number,
                "start": part.start,
                "end": part.end,
                "type_code": part.type_code,
                "name": part.name,
            }
            for part in written_layout.partitions
        ]
        manifest = {
            "schema": 1,
            "expected_version": expected_version,
            "disk": actual_disk,
            "boot_pool_partition": actual_boot_partition,
            "logical_sector_bytes": written_layout.logical_sector_bytes,
            "last_usable_sector": written_layout.last_usable_sector,
            "partitions": live_partitions,
        }
        encoded = base64.b64encode(json.dumps(manifest, sort_keys=True).encode()).decode()
        target.run([
            "python3", "-c",
            "import base64,os,pathlib,sys; p=pathlib.Path(sys.argv[1]); "
            "fd=os.open(p,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600); "
            "os.write(fd,base64.b64decode(sys.argv[2])); os.fsync(fd); os.close(fd)",
            partition_manifest_path,
            encoded,
        ], timeout=45)
        _report(
            f"reserved {new_device}; boot-pool partition extents are unchanged; "
            "no ZFS pool was created so the TrueNAS UI can create it"
        )
        return {
            "state": "partition_created",
            "disk": actual_disk,
            "partition": new_device,
            "start_sector": plan.start_sector,
            "end_sector": plan.end_sector,
            "bytes": plan.partition_bytes,
            "gpt_backup": backup_path,
            "partition_manifest": partition_manifest_path,
        }

    # Do not use zpool -f: any existing signature is a refusal, not something
    # to overwrite. On failure after this point, the backup and partial-state
    # diagnostics are retained for an operator to inspect.
    target.run([
        "zpool", "create",
        "-o", "cachefile=none",
        "-O", "mountpoint=none",
        DATA_POOL_NAME,
        new_device,
    ], timeout=180)
    target.run(["zpool", "export", DATA_POOL_NAME], timeout=90)
    _report("created the empty ZFS pool and exported it for middleware registration")

    available = _importable_pools(target)
    matches = [row for row in available if row.get("name") == DATA_POOL_NAME]
    if len(matches) != 1:
        raise RemoteError(
            f"pool.import_find returned {len(matches)} matching {DATA_POOL_NAME} pools; "
            f"GPT backup retained at {backup_path}"
        )
    guid = matches[0].get("guid")
    if not isinstance(guid, str) or not guid:
        raise RemoteError("pool.import_find did not return a nonempty GUID")
    import_job = target.call("pool.import_pool", {"guid": guid, "name": DATA_POOL_NAME}, timeout=120)
    if type(import_job) is not int:
        raise RemoteError(f"pool.import_pool returned non-job result {import_job!r}")
    target.wait_job(import_job, timeout=300, label=f"import {DATA_POOL_NAME}")
    _report("middleware pool import completed")

    final_rows = target.query("pool.query", [["name", "=", DATA_POOL_NAME]], timeout=90)
    if not isinstance(final_rows, list) or len(final_rows) != 1:
        raise RemoteError("middleware did not return exactly one registered Path B pool")
    final_leaves = _pool_leaf_paths(target, DATA_POOL_NAME)
    final_device = verify_existing_path_b(
        disk=actual_disk,
        boot_pool_partition=actual_boot_partition,
        layout=written_layout,
        boot_pool_leaf_paths=fresh_boot_leaves,
        data_pool_leaf_paths=final_leaves,
        pool_status=str(final_rows[0].get("status", "")),
    )
    if not _same_preexisting_partitions(plan.preexisting_partitions, written_layout):
        raise RemoteError("final GPT verification found changes to an existing partition")
    _report(f"verified middleware ONLINE status and vdev {final_device}; GPT backup: {backup_path}")
    return {
        "state": "created",
        "disk": actual_disk,
        "partition": final_device,
        "start_sector": plan.start_sector,
        "end_sector": plan.end_sector,
        "bytes": plan.partition_bytes,
        "gpt_backup": backup_path,
    }
