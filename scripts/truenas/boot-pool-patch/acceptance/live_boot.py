#!/usr/bin/env python3
"""Verify boot-pool user-data mounts and API access across a TrueNAS reboot.

This intentionally reboots its target. Set both mutation guards only for a
disposable TrueNAS VM or host whose reboot is explicitly planned.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import sys
import time
import uuid

from remote import RemoteError, Target, flatten_datasets
from path_b_ui_setup import reserved_partition_four_name


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RemoteError(message)


def report(stage: str, detail: str) -> None:
    print(f"[{stage}] {detail}", flush=True)


def check_no_running_guests(target: Target) -> None:
    for method, label in (("vm.query", "VM"), ("container.query", "container")):
        rows = target.query(method, timeout=120)
        require(isinstance(rows, list), f"cannot verify {label} state: {method} did not return a list")
        for row in rows:
            require(isinstance(row, dict), f"cannot verify malformed {label} query row: {row!r}")
            identifier = row.get("name", row.get("id"))
            status = row.get("status")
            if method == "vm.query" and (
                not isinstance(status, dict) or not isinstance(status.get("state"), str)
            ):
                vm_id = row.get("id")
                require(
                    isinstance(vm_id, int) and not isinstance(vm_id, bool) and vm_id > 0,
                    f"cannot resolve nullable VM status without a valid id: {row!r}",
                )
                status = target.call("vm.status", vm_id, timeout=60)
            require(
                isinstance(status, dict)
                and isinstance(status.get("state"), str)
                and bool(status["state"].strip()),
                f"cannot determine {label} {identifier!r} runtime state: {status!r}",
            )
            state = status["state"].strip().upper()
            require(
                state == "STOPPED",
                f"refusing host reboot while {label} {identifier!r} is {state!r}",
            )


def wait_for_api(target: Target, expected_version: str, timeout: int = 900) -> dict:
    deadline = time.monotonic() + timeout
    last_error: str | None = None
    ssh_ready = False
    next_progress = time.monotonic() + 30
    while time.monotonic() < deadline:
        try:
            if not ssh_ready:
                target.run(["true"], timeout=12)
                ssh_ready = True
                report("reboot", "SSH is accepting connections; waiting for middleware API")
            info = target.call("system.info", timeout=45)
            version = str(info.get("version", ""))
            require(version == expected_version, f"expected TrueNAS {expected_version}, got {version!r}")
            return info
        except RemoteError as exc:
            last_error = str(exc)
            now = time.monotonic()
            if now >= next_progress:
                elapsed = int(timeout - max(0, deadline - now))
                report("reboot", f"waiting for SSH and middleware readiness ({elapsed}s elapsed)")
                if last_error:
                    report("reboot", f"last readiness check: {last_error[-240:]}")
                next_progress = now + 30
            time.sleep(5)

    raise RemoteError(
        f"TrueNAS API did not return within {timeout}s after reboot"
        + (f"; last error: {last_error}" if last_error else "")
    )


def require_mount(target: Target, dataset: str, mountpoint: str) -> None:
    statfs = target.call("filesystem.statfs", mountpoint, timeout=90)
    require(
        statfs.get("source") == dataset and statfs.get("dest") == mountpoint,
        f"{mountpoint} is not mounted from {dataset}: {statfs!r}",
    )
    mounted = target.shell(f"zfs get -H -o value mounted {shlex.quote(dataset)}")
    require(mounted == "yes", f"ZFS dataset {dataset} mounted={mounted!r}")


def boot_pool_state(target: Target) -> str:
    """Capture boot-pool GUID, health, and resolved vdev topology for comparison."""
    return "\n".join([
        target.shell("zpool get -H -o value guid,health boot-pool", timeout=45),
        target.shell("zpool status -P -L boot-pool", timeout=45),
    ])


def unmount_mounted_user_data_tree(target: Target, root: str) -> list[str]:
    """Unmount mounted filesystems below root, deepest first, without force."""
    listing = target.shell(
        f"zfs list -H -r -t filesystem -o name,mounted {shlex.quote(root)}",
        timeout=90,
    )
    mounted_datasets: list[str] = []
    for line_number, line in enumerate(listing.splitlines(), 1):
        fields = line.split("\t")
        require(
            len(fields) == 2,
            f"malformed recursive ZFS mount listing at line {line_number}: {line!r}",
        )
        dataset, mounted = fields
        require(
            dataset == root or dataset.startswith(root + "/"),
            f"recursive ZFS listing escaped the user-data root: {dataset!r}",
        )
        require(
            mounted in {"yes", "no"},
            f"cannot determine mounted state for {dataset!r}: {mounted!r}",
        )
        if mounted == "yes":
            mounted_datasets.append(dataset)

    require(root in mounted_datasets, f"user-data root {root!r} is not mounted before unmount")
    ordered = sorted(
        mounted_datasets,
        key=lambda dataset: (dataset.count("/"), dataset),
        reverse=True,
    )
    commands = ["set -euo pipefail"]
    for dataset in ordered:
        quoted_dataset = shlex.quote(dataset)
        commands.extend((
            f"zfs unmount {quoted_dataset}",
            f"test \"$(zfs get -H -o value mounted {quoted_dataset})\" = no",
        ))
    target.shell("\n".join(commands), timeout=90)
    return ordered


def partition_table_state(target: Target, disk: str) -> str:
    tree = json.loads(target.run([
        "lsblk", "-J", "--tree", "-b", "-p", "-o",
        "PATH,PARTN,PARTLABEL,PARTTYPE,PARTUUID,SIZE,TYPE", disk,
    ], timeout=45))
    partitions: list[dict[str, object]] = []

    def visit(node: dict[str, object]) -> None:
        if node.get("type") == "part":
            partitions.append({
                key: node.get(key)
                for key in ("path", "partn", "partlabel", "parttype", "partuuid", "size")
            })
        for child in node.get("children") or []:
            if isinstance(child, dict):
                visit(child)

    roots = tree.get("blockdevices")
    require(isinstance(roots, list) and len(roots) == 1 and isinstance(roots[0], dict),
            f"lsblk did not return the selected whole disk {disk!r}")
    visit(roots[0])
    partitions.sort(key=lambda row: (int(row["partn"] or 0), str(row["path"] or "")))
    gpt = target.run(["sgdisk", "--print", disk], timeout=45)
    return json.dumps(partitions, sort_keys=True) + "\n" + gpt


def path_b_pool_state(target: Target, disk: str, boot_partition: str) -> dict[str, object] | None:
    rows = target.query("pool.query", [["name", "=", "boot-disk-data"]], timeout=90)
    require(isinstance(rows, list), f"pool.query returned an invalid Path B inventory: {rows!r}")
    if not rows:
        return None
    require(len(rows) == 1 and isinstance(rows[0], dict),
            f"expected one registered boot-disk-data pool: {rows!r}")
    pool = rows[0]
    pool_id = pool.get("id")
    require(type(pool_id) is int and pool_id > 0, f"Path B pool id is invalid: {pool!r}")
    require(pool.get("status") == "ONLINE", f"Path B middleware status is not ONLINE: {pool!r}")
    guid = target.run(["zpool", "get", "-H", "-o", "value", "guid", "boot-disk-data"], timeout=45)
    require(guid.isdigit() and str(pool.get("guid")) == guid,
            f"Path B pool GUID differs between middleware and ZFS: row={pool!r}, direct={guid!r}")
    pool_status = target.run(["zpool", "list", "-H", "-o", "health", "boot-disk-data"], timeout=45)
    require(pool_status == "ONLINE", f"Path B ZFS health is not ONLINE: {pool_status!r}")
    topology = target.run(["zpool", "status", "-P", "-L", "boot-disk-data"], timeout=45)
    leaves = tuple(sorted(re.findall(
        r"(?m)^\s+(/dev/\S+)\s+(?:ONLINE|DEGRADED|FAULTED|UNAVAIL|OFFLINE)\b", topology
    )))
    expected_partition = "/dev/" + reserved_partition_four_name(disk)
    expected_leaf = target.run(["readlink", "-f", expected_partition], timeout=30)
    resolved_leaves = tuple(sorted(
        target.run(["readlink", "-f", leaf], timeout=30) for leaf in leaves
    ))
    require(resolved_leaves == (expected_leaf,),
            f"Path B pool is not solely on its reserved partition {expected_partition}: {leaves!r}")
    partuuid = target.run(["lsblk", "-dnro", "PARTUUID", expected_partition], timeout=30)
    require(re.fullmatch(r"[0-9a-fA-F-]{36}", partuuid) is not None,
            f"reserved Path B partition has an invalid PARTUUID: {partuuid!r}")
    boot_leaf = target.run(["readlink", "-f", boot_partition], timeout=30)
    boot_topology = target.run(["zpool", "status", "-P", "-L", "boot-pool"], timeout=45)
    boot_leaves = {
        target.run(["readlink", "-f", leaf], timeout=30)
        for leaf in re.findall(
            r"(?m)^\s+(/dev/\S+)\s+(?:ONLINE|DEGRADED|FAULTED|UNAVAIL|OFFLINE)\b",
            boot_topology,
        )
    }
    require(boot_leaf in boot_leaves,
            f"selected boot-pool partition is not a boot-pool leaf: {boot_partition!r}")
    return {
        "pool_id": pool_id,
        "guid": guid,
        "partition": expected_partition,
        "partuuid": partuuid.lower(),
        "leaves": resolved_leaves,
        "status": pool_status,
        "partition_table": partition_table_state(target, disk),
    }


def path_b_pool_identity(state: dict[str, object]) -> tuple[object, ...]:
    return tuple(state[key] for key in (
        "guid", "partition", "partuuid", "leaves", "status", "partition_table",
    ))


def require_path_b_pool_unchanged(
    before: dict[str, object], after: dict[str, object],
) -> None:
    if path_b_pool_identity(before) != path_b_pool_identity(after):
        raise RemoteError(
            "Path B pool or disk layout changed across reboot/import: "
            f"before={path_b_pool_identity(before)!r}, after={path_b_pool_identity(after)!r}"
        )


def export_path_b_pool(target: Target, state: dict[str, object]) -> None:
    job_id = target.call("pool.export", state["pool_id"], {"destroy": False}, timeout=120)
    require(type(job_id) is int and job_id > 0, f"pool.export returned an invalid job id: {job_id!r}")
    target.wait_job(job_id, timeout=300, label="Path B pool.export destroy=false")
    remaining = target.query("pool.query", [["name", "=", "boot-disk-data"]], timeout=90)
    require(remaining == [], f"pool.export destroy=false retained a middleware row: {remaining!r}")
    imported = target.run(["zpool", "list", "-H", "-o", "name"], timeout=45).splitlines()
    require("boot-disk-data" not in imported,
            f"pool.export destroy=false left Path B imported: {imported!r}")


def reimport_path_b_pool(target: Target, expected: dict[str, object]) -> dict[str, object]:
    find_job = target.call("pool.import_find", timeout=120)
    require(type(find_job) is int and find_job > 0,
            f"pool.import_find returned an invalid job id: {find_job!r}")
    found = target.wait_job(find_job, timeout=300, label="Path B pool.import_find")
    require(isinstance(found, list), f"pool.import_find returned an invalid result: {found!r}")
    matches = [
        row for row in found
        if isinstance(row, dict)
        and row.get("name") == "boot-disk-data"
        and str(row.get("guid")) == str(expected["guid"])
    ]
    require(len(matches) == 1,
            f"pool.import_find did not return the captured Path B GUID: {found!r}")
    import_job = target.call(
        "pool.import_pool", {"guid": str(expected["guid"]), "name": "boot-disk-data"}, timeout=120
    )
    require(type(import_job) is int and import_job > 0,
            f"pool.import_pool returned an invalid job id: {import_job!r}")
    target.wait_job(import_job, timeout=300, label="Path B pool.import_pool")
    after = path_b_pool_state(
        target,
        disk=os.environ["TRUENAS_DISK_DEVICE"],
        boot_partition=os.environ["TRUENAS_BOOT_POOL_PARTITION"],
    )
    require(after is not None, "Path B pool is missing after middleware import")
    require_path_b_pool_unchanged(expected, after)
    return after


def main() -> int:
    if os.environ.get("TRUENAS_TEST_ALLOW_MUTATIONS") != "1":
        raise RemoteError("set TRUENAS_TEST_ALLOW_MUTATIONS=1 only for a disposable TrueNAS target")
    if os.environ.get("TRUENAS_TEST_ALLOW_REBOOT") != "1":
        raise RemoteError("set TRUENAS_TEST_ALLOW_REBOOT=1 only when rebooting this target is intended")
    if os.environ.get("TRUENAS_STORAGE_PROFILE") != "path-a":
        raise RemoteError("TRUENAS_STORAGE_PROFILE must be path-a for this boot-pool mount test")

    target = Target.from_environment()
    expected_version = os.environ.get("TRUENAS_EXPECTED_VERSION", "")
    if not expected_version:
        raise RemoteError("TRUENAS_EXPECTED_VERSION is required")

    info = target.call("system.info")
    version = str(info.get("version", ""))
    require(version == expected_version, f"expected TrueNAS {expected_version}, got {version!r}")
    check_no_running_guests(target)

    initial_path_b_rows = target.query(
        "pool.query", [["name", "=", "boot-disk-data"]], timeout=90
    )
    require(isinstance(initial_path_b_rows, list),
            f"pool.query returned an invalid Path B inventory: {initial_path_b_rows!r}")
    before_path_b: dict[str, object] | None = None
    disk = os.environ.get("TRUENAS_DISK_DEVICE", "")
    boot_partition = os.environ.get("TRUENAS_BOOT_POOL_PARTITION", "")
    if initial_path_b_rows:
        require(bool(disk) and bool(boot_partition),
                "Path B exists; TRUENAS_DISK_DEVICE and TRUENAS_BOOT_POOL_PARTITION are required to verify it")
        before_path_b = path_b_pool_state(target, disk, boot_partition)
        require(before_path_b is not None, "Path B inventory disappeared during read-only preflight")
    before_boot_pool = boot_pool_state(target)
    path_b_exported = False

    root = "boot-pool/user-data"
    root_mountpoint = f"/mnt/{root}"
    root_properties = target.shell(
        "quota=$(zfs get -H -p -o value quota boot-pool/user-data) && "
        "case \"$quota\" in ''|*[!0-9]*) exit 1 ;; esac && test \"$quota\" -gt 0 && "
        "zfs get -H -o value canmount boot-pool/user-data | grep -Fx on && "
        "zfs get -H -o value mountpoint boot-pool/user-data | grep -Fx /mnt/boot-pool/user-data"
    )
    require("/mnt/boot-pool/user-data" in root_properties, "boot-pool user-data root properties are invalid")
    initial_mount = target.call("filesystem.statfs", root_mountpoint, timeout=90)
    root_initially_mounted = (
        initial_mount.get("source") == root and initial_mount.get("dest") == root_mountpoint
    )
    if root_initially_mounted:
        require_mount(target, root, root_mountpoint)
    else:
        mounted = target.shell("zfs get -H -o value mounted boot-pool/user-data")
        require(
            mounted == "no",
            f"user-data path is mounted from an unexpected filesystem and ZFS reports mounted={mounted!r}: "
            f"{initial_mount!r}",
        )
        report(
            "preflight",
            "user-data root is currently unmounted; no test data will be written until the boot hook remounts it",
        )

    suffix = time.strftime("%Y%m%d%H%M%S", time.gmtime()) + uuid.uuid4().hex[:8]
    marker_dataset = f"{root}/reboot-probe-{suffix}"
    api_probe_dataset = f"{root}/post-reboot-api-probe-{suffix}"
    marker_mountpoint = f"/mnt/{marker_dataset}"
    marker_path = f"{marker_mountpoint}/marker"
    marker = f"truenas-boot-pool-reboot-{suffix}"
    marker_created = False
    api_probe_created = False
    cleanup_errors: list[str] = []

    def create_marker() -> str:
        nonlocal marker_created
        created = target.call("pool.dataset.create", {"name": marker_dataset, "type": "FILESYSTEM"})
        require(created.get("id") == marker_dataset, f"unexpected marker dataset result: {created!r}")
        marker_created = True
        target.shell(f"printf %s {shlex.quote(marker)} > {shlex.quote(marker_path)}")
        digest = target.shell(f"sha256sum {shlex.quote(marker_path)}").split()[0]
        require(digest == hashlib.sha256(marker.encode()).hexdigest(), "marker hash changed before reboot")
        require_mount(target, marker_dataset, marker_mountpoint)
        return digest

    def reboot_and_wait(stage: str) -> None:
        result = target.run(["systemctl", "reboot", "--no-block"], timeout=20, check=False)
        report(stage, "systemctl reboot requested" + (f" ({result})" if result else ""))
        wait_for_api(target, expected_version)

    def unmount_user_data_tree() -> None:
        unmounted = unmount_mounted_user_data_tree(target, root)
        mounted = target.shell("zfs get -H -o value mounted boot-pool/user-data")
        require(mounted == "no", f"boot-pool user-data remained mounted before reboot: {mounted!r}")
        report(
            "pre-reboot",
            f"unmounted {len(unmounted)} user-data filesystem(s), deepest first, so the boot hook must mount them again",
        )

    try:
        before = None
        if root_initially_mounted:
            before = create_marker()
            report("pre-reboot", f"created {marker_dataset}; marker SHA-256 {before}")
            unmount_user_data_tree()

        # A reboot is the subject of this test and is guarded above by a second
        # explicit environment variable in addition to the mutation guard.
        reboot_and_wait("reboot")

        require(boot_pool_state(target) == before_boot_pool,
                "boot-pool GUID, health, or vdev status changed across reboot")
        if before_path_b is not None:
            after_reboot_path_b = path_b_pool_state(target, disk, boot_partition)
            require(after_reboot_path_b is not None,
                    "reserved-partition Path B pool did not return after reboot")
            require_path_b_pool_unchanged(before_path_b, after_reboot_path_b)
            report(
                "path-b-reboot",
                f"boot-disk-data GUID {before_path_b['guid']} returned ONLINE on "
                f"{before_path_b['partition']} PARTUUID {before_path_b['partuuid']}; "
                "boot-pool identity/status and GPT layout are unchanged",
            )
            export_path_b_pool(target, after_reboot_path_b)
            path_b_exported = True
            after_import = reimport_path_b_pool(target, before_path_b)
            path_b_exported = False
            require_path_b_pool_unchanged(before_path_b, after_import)
            require(boot_pool_state(target) == before_boot_pool,
                    "boot-pool GUID, health, or vdev status changed during Path B re-import")
            report("path-b-reimport", "pool.import_find and pool.import_pool restored the same Path B GUID and reserved PARTUUID")

        require_mount(target, root, root_mountpoint)
        choices = target.call("container.pool_choices", timeout=120)
        require("boot-pool" in choices, f"container.pool_choices omits mounted boot-pool: {choices!r}")

        if not root_initially_mounted:
            report("recovery", "boot hook mounted user-data; container pool choices now include boot-pool")
            before = create_marker()
            report("pre-reboot", f"created {marker_dataset}; marker SHA-256 {before}")
            unmount_user_data_tree()
            reboot_and_wait("persistence reboot")
            require(boot_pool_state(target) == before_boot_pool,
                    "boot-pool GUID, health, or vdev status changed across persistence reboot")
            if before_path_b is not None:
                after_second_reboot = path_b_pool_state(target, disk, boot_partition)
                require(after_second_reboot is not None,
                        "reserved-partition Path B pool did not return after persistence reboot")
                require_path_b_pool_unchanged(before_path_b, after_second_reboot)
            require_mount(target, root, root_mountpoint)
            choices = target.call("container.pool_choices", timeout=120)
            require("boot-pool" in choices,
                    f"container.pool_choices omits boot-pool after recovery reboot: {choices!r}")

        assert before is not None
        require_mount(target, marker_dataset, marker_mountpoint)
        after = target.shell(f"sha256sum {shlex.quote(marker_path)}").split()[0]
        require(after == before, f"marker changed across reboot: before={before}, after={after}")
        pool_health = target.shell("zpool status -x boot-pool")
        require("is healthy" in pool_health, f"boot-pool is unhealthy after reboot: {pool_health!r}")

        rows = flatten_datasets(target.query("pool.dataset.query", timeout=120))
        require(marker_dataset in rows, "pool.dataset.query omitted the persisted marker dataset after reboot")
        created = target.call("pool.dataset.create", {"name": api_probe_dataset, "type": "FILESYSTEM"})
        require(created.get("id") == api_probe_dataset, f"post-reboot API create returned {created!r}")
        api_probe_created = True
        require_mount(target, api_probe_dataset, f"/mnt/{api_probe_dataset}")
        report("post-reboot", "root and child mounts, marker hash, pool health, dataset query/create passed")
    finally:
        if path_b_exported and before_path_b is not None:
            try:
                restored = reimport_path_b_pool(target, before_path_b)
                require_path_b_pool_unchanged(before_path_b, restored)
                path_b_exported = False
            except Exception as exc:
                cleanup_errors.append(f"Path B pool re-import after failed probe: {exc}")
        for name, created in (
            (api_probe_dataset, api_probe_created),
            (marker_dataset, marker_created),
        ):
            if not created:
                continue
            try:
                present = flatten_datasets(target.query("pool.dataset.query", timeout=120))
                if name in present:
                    target.call("pool.dataset.delete", name, {"recursive": True}, timeout=120)
                    remaining = flatten_datasets(target.query("pool.dataset.query", timeout=120))
                    if name in remaining:
                        cleanup_errors.append(f"dataset {name} remains after deletion")
            except Exception as exc:
                cleanup_errors.append(f"dataset {name}: {exc}")

    if cleanup_errors:
        raise RemoteError("cleanup failed: " + "; ".join(cleanup_errors))
    report("cleanup", "temporary reboot datasets removed")
    report("result", f"PASS TrueNAS {version} boot-pool reboot persistence")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RemoteError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1)
