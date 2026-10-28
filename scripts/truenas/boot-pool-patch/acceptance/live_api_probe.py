#!/usr/bin/env python3
"""Assert the middleware API limits and the safety of the pool patch.

Profile ``path-b`` (partition-only state left by ``live-path-b-partition``)
asserts the partition-pool shim: the reserved partition is offered by
``disk.details``, mixed/mirror requests are refused, ``boot.attach`` on the
partition fails without changing the boot pool, ``pool.create`` on the
partition creates a pool whose only vdev is that partition, and a hand-made pool
on the partition is adopted by ``pool.import_find``/``pool.import_pool``.

Profile ``path-a`` (patched guest with the mounted ``boot-pool/user-data``
root) asserts the synthetic read-only ``boot-pool`` row of ``pool.query`` and
that every id- and name-based mutation on it is refused with the boot pool
unchanged.

Every check prints ``[check] <key> PASS|FAIL <detail>``; any FAIL fails the lane.
The only mutations are throwaway pools on the reserved partition, removed and
label-cleared in a ``finally`` block, with the cleanup asserted.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shlex
import sys

from path_b_ui_setup import reserved_partition_four_name
from remote import RemoteError, Target

POOL = "apiprobepool"
MARKER = "boot-disk-data"
ZFS_TYPE_GUID = "6a898cc3-1dd2-11b2-99a6-080020736631"
FAILURES: list[str] = []
POOL_CREATE_SCHEMA_PATH = Path(__file__).resolve().parents[1] / "shared" / "pool_create_schema_v27.json"


def check(key: str, ok: bool, detail: object = "") -> None:
    print(f"[check] {key} {'PASS' if ok else 'FAIL'} {str(detail)[:400]}", flush=True)
    if not ok:
        FAILURES.append(key)


def attempt(target: Target, method: str, *params, job: bool = False, timeout: int = 180) -> tuple[bool, str]:
    """Return ``(succeeded, exact output or error text)``."""
    argv = ["midclt", "call"] + (["-j"] if job else []) + [method]
    argv += [p if isinstance(p, str) else json.dumps(p, separators=(",", ":")) for p in params]
    try:
        return True, " ".join(target.run(argv, timeout=timeout).split())
    except RemoteError as exc:
        return False, " ".join(str(exc).split())


def normalized_pool_create_schema(method: dict) -> dict:
    """Keep the installed top-level fields and nested defaults used by the shim."""
    signatures = method.get("accepts")
    if not isinstance(signatures, list) or len(signatures) != 1 or not isinstance(signatures[0], dict):
        raise ValueError("core.get_methods returned an unexpected pool.create signature")
    schema = signatures[0]
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        raise ValueError("core.get_methods omitted pool.create properties")
    required = sorted(schema.get("required") or [])
    fields = {}
    for name, field in sorted(properties.items()):
        if not isinstance(field, dict):
            raise ValueError(f"core.get_methods returned an invalid field schema for {name!r}")
        entry = {}
        if name in required:
            entry["required"] = True
        if "default" in field:
            entry["default"] = field["default"]
        nested = field.get("properties") or {}
        if nested:
            entry["nested_fields"] = sorted(nested)
            entry["nested_defaults"] = {
                child: child_schema["default"]
                for child, child_schema in sorted(nested.items())
                if isinstance(child_schema, dict) and "default" in child_schema
            }
        fields[name] = entry
    return {"required_fields": required, "fields": fields}


def live_pool_create_schema_matches(target: Target) -> tuple[bool, str]:
    try:
        recorded = json.loads(POOL_CREATE_SCHEMA_PATH.read_text(encoding="utf-8"))
        installed_methods = target.call("core.get_methods", timeout=120)
        installed_method = installed_methods.get("pool.create") if isinstance(installed_methods, dict) else None
        if not isinstance(installed_method, dict):
            return False, "core.get_methods did not return pool.create"
        installed = normalized_pool_create_schema(installed_method)
    except Exception as exc:
        return False, f"could not fetch/normalize pool.create schema ({type(exc).__name__})"

    expected = {
        "required_fields": recorded.get("required_fields", []),
        "fields": recorded.get("fields", {}),
    }
    if installed == expected:
        return True, f"installed pool.create schema matches {len(installed['fields'])} recorded fields"
    return False, {
        "recorded_fields": sorted(expected["fields"]),
        "installed_fields": sorted(installed["fields"]),
        "defaults_match": installed == expected,
    }.__repr__()


def boot_pool_state(target: Target) -> str:
    return "\n".join([
        target.shell("zpool get -H -o value guid,health boot-pool", timeout=45),
        target.shell("zpool status -P -L boot-pool", timeout=45),
    ])


def synthetic_row_refused(ok: bool, text: str) -> bool:
    return not ok and "pool 0 does not exist" in text.lower()


def status_leaf_paths(output: str) -> set[str]:
    return {token for token in output.split() if token.startswith("/dev/")}


def partition_table_state(target: Target, disk: str) -> str:
    raw = target.run([
        "lsblk", "-J", "--tree", "-b", "-p", "-o",
        "PATH,PKNAME,PARTN,PARTLABEL,PARTTYPE,PARTUUID,SIZE,TYPE,FSTYPE,MOUNTPOINT", disk,
    ], timeout=45)
    tree = json.loads(raw)
    partitions = []

    def visit(node):
        if node.get("type") == "part":
            partitions.append({
                key: node.get(key)
                for key in ("path", "pkname", "partn", "partlabel", "parttype", "partuuid", "size")
            })
        for child in node.get("children") or []:
            visit(child)

    for node in tree.get("blockdevices", []):
        visit(node)
    partitions.sort(key=lambda row: (int(row["partn"] or 0), row["path"] or ""))
    gpt = target.run(["sgdisk", "-p", disk], timeout=45)
    return json.dumps(partitions, sort_keys=True) + "\n" + gpt


def destroy_source_evidence(target: Target) -> str:
    command = (
        "cd /usr/lib/python3/dist-packages/middlewared && "
        "grep -nE 'pool.get_disks|disk.wipe|labelclear' plugins/pool_/export.py || true; "
        "grep -nE '_get_whole_disk|partition' plugins/zpool/get_zpool_disks_impl.py; "
        "grep -nE 'disk_path|size - \\(CHUNK|wipe first 32MB|wipe last 32MB' plugins/disk_/wipe.py"
    )
    return target.shell(command, timeout=60)


def run_path_a(target: Target) -> None:
    before = boot_pool_state(target)
    ok, rows = attempt(target, "pool.query", [], {"select": ["id", "name", "path"]})
    synthetic = [r for r in json.loads(rows) if r["name"] == "boot-pool"] if ok else []
    check("synthetic-row-present", len(synthetic) == 1 and synthetic[0]["id"] == 0, rows)
    mutations = [
        ("pool.export", (0, {})), ("pool.update", (0, {"autotrim": "ON"})), ("pool.expand", (0,)),
        ("pool.upgrade", (0,)), ("pool.replace", (0, {"label": "x", "disk": "vdz"})),
        ("pool.attach", (0, {"target_vdev": "x", "new_disk": "vdz"})),
        ("pool.detach", (0, {"label": "x"})), ("pool.offline", (0, {"label": "x"})),
        ("pool.online", (0, {"label": "x"})), ("pool.remove", (0, {"label": "x"})),
    ]
    # pool.scrub.run takes a pool NAME and, in stock v27, is accepted for boot-pool as well
    # (a scrub is non-destructive and boot.scrub offers the same); it is deliberately not
    # part of the refusal set, see ACTION-MATRIX.md.
    job_methods = {"pool.export", "pool.update", "pool.expand", "pool.replace", "pool.attach", "pool.remove"}
    for method, params in mutations:
        ok, text = attempt(target, method, *params, job=method in job_methods)
        check(f"refused-{method}", synthetic_row_refused(ok, text), text)
    check("boot-pool-unchanged", boot_pool_state(target) == before, "zpool guid/health/status identical")


def reserved_partition(target: Target) -> tuple[str, str]:
    disk = os.environ.get("TRUENAS_DISK_DEVICE", "")
    boot_partition = os.environ.get("TRUENAS_BOOT_POOL_PARTITION", "")
    if (re.fullmatch(r"/dev/[A-Za-z0-9_.+-]+", disk) is None
            or re.fullmatch(r"/dev/[A-Za-z0-9_.+-]+", boot_partition) is None):
        raise RemoteError("TRUENAS_DISK_DEVICE and TRUENAS_BOOT_POOL_PARTITION must name /dev paths")
    try:
        expected_name = reserved_partition_four_name(disk)
    except ValueError as exc:
        raise RemoteError(str(exc)) from exc
    expected = f"/dev/{expected_name}"
    boot_name = os.path.basename(disk)
    boot_suffix = "p3" if boot_name.startswith(("nvme", "pmem")) else "3"
    if boot_partition != f"/dev/{boot_name}{boot_suffix}":
        raise RemoteError(f"configured boot-pool partition is not partition 3 of {disk!r}")
    partition = target.run(["readlink", "-f", f"/dev/disk/by-partlabel/{MARKER}"], timeout=30)
    if partition != expected or partition == boot_partition:
        raise RemoteError(f"the reserved partition is {partition!r}, expected {expected!r}")
    info = json.loads(target.run([
        "lsblk", "-J", "-b", "-p", "-o", "PATH,PARTLABEL,PARTTYPE,FSTYPE,MOUNTPOINT", partition,
    ], timeout=30))["blockdevices"][0]
    if info["partlabel"] != MARKER or str(info["parttype"]).lower() != ZFS_TYPE_GUID \
            or info["fstype"] or info["mountpoint"]:
        raise RemoteError(f"partition {partition} is not an empty reserved partition: {info!r}")
    if target.shell(f"zdb -l {partition} 2>/dev/null | grep -c 'version:' || true", timeout=30).strip() not in ("", "0"):
        raise RemoteError(f"partition {partition} carries ZFS labels")
    if partition in target.shell("zpool status -P -L", timeout=45).split():
        raise RemoteError(f"partition {partition} belongs to an imported pool")
    if MARKER in target.shell("zpool list -H -o name", timeout=30).split():
        raise RemoteError(f"pool {MARKER} already exists")
    return disk, partition


def _partition_mount_clear(target: Target, device: str) -> None:
    script = (
        f"findmnt -rn -S {shlex.quote(device)} >/dev/null 2>&1; result=$?; "
        '[ "$result" -eq 1 ]'
    )
    target.run(["bash", "-lc", script], timeout=30)


def _verify_probe_partition(
    target: Target, partition: str, partition_uuid: str, boot_disk: str,
) -> str:
    if re.fullmatch(r"[0-9a-fA-F-]{36}", partition_uuid) is None:
        raise RemoteError("cleanup requires the captured partition PARTUUID")
    device = f"/dev/disk/by-partuuid/{partition_uuid}"
    resolved = target.run(["readlink", "-f", device], timeout=30)
    expected_path = target.run(["readlink", "-f", partition], timeout=30)
    if resolved != expected_path:
        raise RemoteError(
            f"PARTUUID {partition_uuid} now resolves to {resolved!r}, expected {expected_path!r}"
        )
    if target.run(["lsblk", "-dnro", "PARTUUID", device], timeout=30).lower() != partition_uuid.lower():
        raise RemoteError(f"{device} no longer has captured PARTUUID {partition_uuid}")
    if target.run(["lsblk", "-dnro", "PARTLABEL", device], timeout=30) != MARKER:
        raise RemoteError(f"{device} no longer carries reserved GPT label {MARKER!r}")
    if target.run(["lsblk", "-dnro", "PARTTYPE", device], timeout=30).lower() != ZFS_TYPE_GUID:
        raise RemoteError(f"{device} no longer has the reserved ZFS GPT type")
    part_number = target.run(["lsblk", "-dnro", "PARTN", device], timeout=30)
    if not part_number.isdecimal() or int(part_number) < 4:
        raise RemoteError(f"{device} is no longer a reserved partition number 4 or later")
    fstype = target.run(["lsblk", "-dnro", "FSTYPE", device], timeout=30)
    if fstype not in {"", "zfs_member"}:
        raise RemoteError(f"refusing cleanup of {device} with unexpected filesystem {fstype!r}")
    if target.run(["lsblk", "-dnro", "MOUNTPOINT", device], timeout=30):
        raise RemoteError(f"refusing cleanup of mounted partition {device}")
    _partition_mount_clear(target, device)

    parent = target.run(["lsblk", "-dnro", "PKNAME", device], timeout=30)
    expected_parent = target.run(["readlink", "-f", boot_disk], timeout=30)
    if not parent or f"/dev/{parent}" != expected_parent:
        raise RemoteError(f"{device} is no longer on the captured boot-pool disk {boot_disk!r}")
    boot_status = target.run(["zpool", "status", "-P", "-L", "boot-pool"], timeout=45)
    boot_leaf_parents = {
        target.run(["lsblk", "-dnro", "PKNAME", leaf], timeout=30)
        for leaf in status_leaf_paths(boot_status)
    }
    if parent not in boot_leaf_parents:
        raise RemoteError(f"{device} is no longer on a disk containing a boot-pool leaf")
    return device


def _import_scan(target: Target, device: str) -> list[dict]:
    output = target.run(["zpool", "import", "-d", device], timeout=45)
    starts = list(re.finditer(r"(?m)^\s*pool:\s*(\S+)\s*$", output))
    records = []
    for index, start in enumerate(starts):
        end = starts[index + 1].start() if index + 1 < len(starts) else len(output)
        section = output[start.start():end]
        guid = re.search(r"(?m)^\s*id:\s*(\d+)\s*$", section)
        config = re.search(r"(?ms)^\s*config:\s*$(.*)", section)
        leaves = re.findall(
            r"(?m)^\s+(/dev/\S+)\s+(?:ONLINE|DEGRADED|FAULTED|UNAVAIL|OFFLINE)\b",
            config.group(1) if config else "",
        )
        records.append({
            "name": start.group(1),
            "guid": guid.group(1) if guid else None,
            "leaves": leaves,
        })
    return records


def _assert_exported_probe_pool(
    target: Target, device: str, partition: str, expected_guid: str,
) -> None:
    records = _import_scan(target, device)
    if len(records) != 1 or records[0].get("name") != POOL \
            or str(records[0].get("guid")) != expected_guid:
        raise RemoteError(
            f"export scan on {device} does not identify only pool {POOL!r} with GUID {expected_guid}: "
            f"{records!r}"
        )
    leaves = {target.run(["readlink", "-f", leaf], timeout=30) for leaf in records[0]["leaves"]}
    expected_leaf = target.run(["readlink", "-f", partition], timeout=30)
    if leaves != {expected_leaf}:
        raise RemoteError(
            f"exported pool GUID {expected_guid} is not a single-leaf pool on {partition}: {leaves!r}"
        )


def _assert_probe_partition_offered(target: Target, partition: str, partition_uuid: str) -> None:
    rows = target.call("disk.details", {"type": "UNUSED", "join_partitions": False}, timeout=120)
    name = os.path.basename(partition)
    offered = [
        row for row in rows
        if row.get("name") == name
        and row.get("identifier") == "{partuuid}" + partition_uuid.lower()
    ]
    if len(offered) != 1:
        raise RemoteError(f"disk.details did not offer captured PARTUUID {partition_uuid}: {rows!r}")


def cleanup(
    target: Target, partition: str, partition_uuid: str, expected_pool_guid: str, boot_disk: str,
) -> None:
    """Export and clear only the captured pool GUID on the captured PARTUUID."""
    if re.fullmatch(r"\d+", str(expected_pool_guid)) is None:
        raise RemoteError("cleanup requires the captured numeric pool GUID; manual recovery is required")
    device = _verify_probe_partition(target, partition, partition_uuid, boot_disk)
    rows = target.query(
        "pool.query", [["name", "=", POOL]], {"select": ["id", "name", "guid"]}, timeout=90,
    )
    if not isinstance(rows, list):
        raise RemoteError(f"pool.query returned {type(rows).__name__} during cleanup")
    if rows:
        if (
            len(rows) != 1
            or rows[0].get("name") != POOL
            or str(rows[0].get("guid")) != expected_pool_guid
            or not isinstance(rows[0].get("id"), int)
        ):
            raise RemoteError(
                f"middleware row does not identify captured pool GUID {expected_pool_guid}; "
                "cleanup stopped for manual recovery"
            )
        imported = target.run(["zpool", "list", "-H", "-o", "name"], timeout=30).splitlines()
        if POOL not in imported:
            raise RemoteError("middleware row exists for an exported pool; cleanup stopped for manual recovery")
        actual_guid = target.run(["zpool", "get", "-H", "-o", "value", "guid", POOL], timeout=30)
        if actual_guid != expected_pool_guid:
            raise RemoteError(f"imported pool GUID changed from {expected_pool_guid} to {actual_guid!r}")
        status = target.run(["zpool", "status", "-P", "-L", POOL], timeout=45)
        leaves = status_leaf_paths(status)
        expected_leaf = target.run(["readlink", "-f", partition], timeout=30)
        resolved_leaves = {target.run(["readlink", "-f", leaf], timeout=30) for leaf in leaves}
        if resolved_leaves != {expected_leaf}:
            raise RemoteError(f"pool GUID {expected_pool_guid} is not solely on {partition}: {leaves!r}")
        target.run([
            "midclt", "call", "-j", "pool.export", str(rows[0]["id"]),
            '{"destroy":false}',
        ], timeout=240)
        rows = target.query(
            "pool.query", [["name", "=", POOL]], {"select": ["id", "name", "guid"]}, timeout=90,
        )
        if rows:
            raise RemoteError("pool.export destroy=false left a middleware pool row; labels were not cleared")
    else:
        imported = target.run(["zpool", "list", "-H", "-o", "name"], timeout=30).splitlines()
        if POOL in imported:
            raise RemoteError("pool is imported without its middleware row; cleanup stopped for manual recovery")

    global_status = target.run(["zpool", "status", "-P", "-L"], timeout=45)
    expected_leaf = target.run(["readlink", "-f", partition], timeout=30)
    imported_leaves = {
        target.run(["readlink", "-f", leaf], timeout=30)
        for leaf in status_leaf_paths(global_status)
    }
    if expected_leaf in imported_leaves:
        raise RemoteError(f"an imported pool still uses {partition}; labels were left untouched")

    records = _import_scan(target, device)
    if not records:
        fstype = target.run(["lsblk", "-dnro", "FSTYPE", device], timeout=30)
        if fstype:
            raise RemoteError(
                f"no exported pool was found but {device} still reports {fstype!r}; "
                "cleanup stopped for manual recovery"
            )
        _assert_probe_partition_offered(target, partition, partition_uuid)
        return

    _assert_exported_probe_pool(target, device, partition, expected_pool_guid)
    # Recheck all write-boundary facts after export/import discovery.
    device = _verify_probe_partition(target, partition, partition_uuid, boot_disk)
    global_status = target.run(["zpool", "status", "-P", "-L"], timeout=45)
    imported_leaves = {
        target.run(["readlink", "-f", leaf], timeout=30)
        for leaf in status_leaf_paths(global_status)
    }
    if expected_leaf in imported_leaves:
        raise RemoteError(f"an imported pool began using {partition}; labels were left untouched")
    _assert_exported_probe_pool(target, device, partition, expected_pool_guid)
    target.run(["zpool", "labelclear", "-f", device], timeout=45)
    _assert_probe_partition_offered(target, partition, partition_uuid)


def run_path_b(target: Target) -> None:
    disk, partition = reserved_partition(target)
    name = partition.rsplit("/", 1)[1]
    partition_uuid = target.run(["lsblk", "-dnro", "PARTUUID", partition], timeout=30)
    if re.fullmatch(r"[0-9a-fA-F-]{36}", partition_uuid) is None:
        raise RemoteError(f"reserved partition {partition} has no valid PARTUUID")
    before = boot_pool_state(target)
    table_before = partition_table_state(target, disk)
    pool_guid: str | None = None
    pool_create_attempted = False
    try:
        evidence = destroy_source_evidence(target)
        check(
            "destroy-source-parent-disk-wipe",
            "pool.get_disks" in evidence and "disk.wipe" in evidence
            and "_get_whole_disk" in evidence and "size - (CHUNK" in evidence,
            evidence,
        )
        check("boot-pool-online-before", "ONLINE" in before, before)
        ok, text = attempt(target, "disk.details", {"type": "UNUSED", "join_partitions": False})
        names = [row["name"] for row in json.loads(text)] if ok else []
        check("details-lists-reserved-partition", names == [name], text[:300])

        ok, text = attempt(target, "pool.create", {"name": "probemix", "topology": {"data": [
            {"type": "MIRROR", "disks": [name, disk.rsplit("/", 1)[1]]}]}}, job=True)
        check(
            "create-mixed-refused",
            not ok and "reserved boot-disk partition can only be the single disk of a one-disk data vdev" in text,
            text,
        )
        ok, text = attempt(target, "pool.create", {"name": "probemirror", "topology": {"data": [
            {"type": "MIRROR", "disks": [name]}]}}, job=True)
        check(
            "create-mirror-refused",
            not ok and "reserved boot-disk partition can only be used as a single-disk stripe data vdev" in text.lower(),
            text,
        )

        unsupported_options = (
            ("encryption", {"encryption": True}),
            ("encryption_options", {"encryption_options": {"generate_key": True}}),
            ("deduplication", {"deduplication": "ON"}),
            ("dedup_table_quota", {"dedup_table_quota": "CUSTOM"}),
            ("dedup_table_quota_value", {"dedup_table_quota_value": 1048576}),
            ("checksum", {"checksum": "SHA256"}),
            ("allow_duplicate_serials", {"allow_duplicate_serials": True}),
            ("all_sed", {"all_sed": True}),
            ("force_topology", {"force_topology": True}),
        )
        for index, (option, value) in enumerate(unsupported_options):
            payload = {
                "name": f"probeopt{index}",
                "topology": {"data": [{"type": "STRIPE", "disks": [name]}]},
                **value,
            }
            ok, text = attempt(target, "pool.create", payload, job=True)
            expected_refusal = f"{option} is not supported for reserved boot-disk partitions"
            check(f"create-option-refused-{option}", not ok and expected_refusal in text, text)

        ok, text = attempt(target, "boot.attach", name, {}, job=True)
        check("boot-attach-refused-unchanged", (not ok) and boot_pool_state(target) == before, text)

        pool_create_attempted = True
        ok, text = attempt(target, "pool.create", {"name": POOL, "topology": {"data": [
            {"type": "STRIPE", "disks": [name]}]}}, job=True)
        leaves = target.shell(f"zpool status -P -L {POOL} 2>&1", timeout=45)
        check("shim-create-on-partition", ok and partition in leaves.split() and "ONLINE" in leaves,
              f"create ok={ok}: {leaves[:200]}")
        if not ok:
            raise RemoteError("pool.create did not return success; its outcome is ambiguous, so cleanup needs manual recovery")
        pool_guid = target.run(["zpool", "get", "-H", "-o", "value", "guid", POOL], timeout=30)
        if re.fullmatch(r"\d+", pool_guid) is None:
            raise RemoteError("pool.create succeeded but its pool GUID could not be captured; manual recovery is required")
        ok, rows = attempt(target, "pool.query", [["name", "=", POOL]], {"select": ["name", "status"]})
        check("shim-pool-registered", ok and POOL in rows and "ONLINE" in rows, rows)
        ok, text = attempt(target, "disk.details", {"type": "UNUSED", "join_partitions": False})
        check("details-no-longer-offers-used-partition", ok and name not in text, text[:200])

        pool = target.query("pool.query", [["name", "=", POOL]], {"get": True}, timeout=90)
        if str(pool.get("guid")) != pool_guid:
            raise RemoteError(
                f"middleware row GUID {pool.get('guid')!r} differs from captured ZFS GUID {pool_guid}"
            )
        pool_id = pool["id"]
        vdev_guid = str(pool["topology"]["data"][0]["guid"])

        ok, text = attempt(target, "pool.export", pool_id, {"destroy": True}, job=True)
        check(
            "shim-destroy-refused",
            not ok and "refusing destructive export of pool" in text.lower()
            and "destroy=false" in text.lower(),
            text,
        )
        pool_status = target.shell(f"zpool status -P -L {POOL}", timeout=45)
        check(
            "shim-destroy-partitions-unchanged",
            boot_pool_state(target) == before and "ONLINE" in before
            and partition_table_state(target, disk) == table_before
            and partition in pool_status.split() and "ONLINE" in pool_status,
            f"pool={pool_status[:240]}; boot-pool ONLINE GUID/status and GPT/lsblk layout unchanged",
        )

        ok, text = attempt(
            target, "pool.replace", pool_id,
            {"label": vdev_guid, "disk": "{serial}missing-review-probe"}, job=True,
        )
        check("shim-replace-does-not-touch-other-partitions",
              boot_pool_state(target) == before and partition_table_state(target, disk) == table_before,
              f"ok={ok}; {text}")

        ok, text = attempt(
            target, "pool.offline", pool_id, {"label": vdev_guid}, job=False,
        )
        check("shim-offline-does-not-touch-other-partitions",
              boot_pool_state(target) == before and partition_table_state(target, disk) == table_before,
              f"ok={ok}; {text}")
        online_ok, online_text = attempt(
            target, "pool.online", pool_id, {"label": vdev_guid}, job=False,
        )
        check("shim-offline-restored", online_ok and "ONLINE" in target.shell(
            f"zpool status -P -L {POOL}", timeout=45), online_text)

        ok, text = attempt(
            target, "pool.detach", pool_id, {"label": vdev_guid, "wipe": False}, job=False,
        )
        check("shim-detach-does-not-touch-other-partitions",
              boot_pool_state(target) == before and partition_table_state(target, disk) == table_before,
              f"ok={ok}; {text}")

        ok, text = attempt(
            target, "pool.remove", pool_id, {"label": vdev_guid}, job=True,
        )
        check("shim-remove-does-not-touch-other-partitions",
              boot_pool_state(target) == before and partition_table_state(target, disk) == table_before,
              f"ok={ok}; {text}")

        cleanup(target, partition, partition_uuid, pool_guid, disk)
        pool_guid = None
        pool_create_attempted = False

        pool_create_attempted = True
        target.run(["zpool", "create", "-o", "cachefile=none", "-O", "mountpoint=none", POOL,
                    f"/dev/disk/by-partuuid/{partition_uuid}"], timeout=120)
        pool_guid = target.run(["zpool", "get", "-H", "-o", "value", "guid", POOL], timeout=30)
        if re.fullmatch(r"\d+", pool_guid) is None:
            raise RemoteError("hand-made pool was created but its pool GUID could not be captured; manual recovery is required")
        target.run(["zpool", "export", POOL], timeout=120)
        ok, found = attempt(target, "pool.import_find", job=True)
        matches = [
            row for row in json.loads(found)
            if row.get("name") == POOL and str(row.get("guid")) == pool_guid
        ] if ok else []
        if len(matches) != 1:
            raise RemoteError(
                f"pool.import_find did not return captured pool GUID {pool_guid}: {found[:300]}"
            )
        ok2, imported = attempt(
            target, "pool.import_pool", {"guid": pool_guid, "name": POOL}, job=True,
        )
        ok3, rows = attempt(target, "pool.query", [["name", "=", POOL]], {"select": ["name", "status"]})
        check("import-hand-made-pool", ok and ok2 and ok3 and "ONLINE" in rows, f"{found[:150]} / {imported} / {rows}")
    finally:
        if pool_guid is not None:
            cleanup(target, partition, partition_uuid, pool_guid, disk)
        elif pool_create_attempted:
            raise RemoteError(
                "probe pool creation was attempted but no GUID was captured; cleanup stopped and manual recovery is required"
            )
    gone = POOL not in target.shell("zpool list -H -o name", timeout=30).split()
    fstype = target.run(["lsblk", "-dnro", "FSTYPE", f"/dev/disk/by-partuuid/{partition_uuid}"], timeout=30)
    remaining_exports = _import_scan(target, f"/dev/disk/by-partuuid/{partition_uuid}")
    check(
        "cleanup-complete", gone and not fstype and not remaining_exports,
        f"pool gone={gone} filesystem={fstype!r} exported pools={remaining_exports!r}",
    )
    ok, text = attempt(target, "disk.details", {"type": "UNUSED", "join_partitions": False})
    details = json.loads(text) if ok else []
    offered = [
        row for row in details
        if row.get("name") == name
        and row.get("identifier") == "{partuuid}" + partition_uuid.lower()
    ]
    check("cleanup-partition-offered-again", ok and len(offered) == 1, text[:300])
    check(
        "boot-pool-unchanged",
        boot_pool_state(target) == before and partition_table_state(target, disk) == table_before,
        "zpool guid/health/status and GPT/lsblk partition layout identical",
    )


def main() -> int:
    if os.environ.get("TRUENAS_TEST_ALLOW_MUTATIONS") != "1":
        raise RemoteError("set TRUENAS_TEST_ALLOW_MUTATIONS=1 for this disposable guest")
    profile = os.environ.get("TRUENAS_STORAGE_PROFILE", "")
    if profile not in {"path-a", "path-b"}:
        raise RemoteError("TRUENAS_STORAGE_PROFILE must be path-a or path-b")
    target = Target.from_environment()
    expected = os.environ.get("TRUENAS_EXPECTED_VERSION", "")
    version = str(target.call("system.info").get("version", ""))
    if not expected or version != expected:
        raise RemoteError(f"expected TrueNAS {expected!r}, found {version!r}")
    schema_ok, schema_detail = live_pool_create_schema_matches(target)
    check("pool-create-schema-live-match", schema_ok, schema_detail)
    if not schema_ok:
        raise RemoteError("installed pool.create schema differs from the recorded v27 schema; stopped before storage mutations")
    (run_path_a if profile == "path-a" else run_path_b)(target)
    if FAILURES:
        raise RemoteError(f"failed checks: {', '.join(FAILURES)}")
    print(f'[result] "PASS {profile} API limits and patch safety asserted"', flush=True)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except RemoteError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        sys.exit(1)
