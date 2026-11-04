#!/usr/bin/env python3
"""Reserve the guarded Path B free-tail partition for creation through the UI."""

from __future__ import annotations

import os
import sys

from path_b_provisioner import provision_path_b
from remote import RemoteError, Target


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RemoteError(message)


def main() -> int:
    if os.environ.get("TRUENAS_TEST_ALLOW_MUTATIONS") != "1":
        raise RemoteError("set TRUENAS_TEST_ALLOW_MUTATIONS=1 for this disposable guest")
    if os.environ.get("TRUENAS_TEST_ALLOW_DISK_PROVISION") != "1":
        raise RemoteError("set TRUENAS_TEST_ALLOW_DISK_PROVISION=1 for this disk-mutation lane")
    if os.environ.get("TRUENAS_STORAGE_PROFILE") != "path-b":
        raise RemoteError("TRUENAS_STORAGE_PROFILE must be path-b")

    expected_version = os.environ.get("TRUENAS_EXPECTED_VERSION", "")
    disk = os.environ.get("TRUENAS_DISK_DEVICE", "")
    boot_partition = os.environ.get("TRUENAS_BOOT_POOL_PARTITION", "")
    try:
        minimum_bytes = int(os.environ.get("TRUENAS_MINIMUM_FREE_BYTES", "1073741824"))
    except ValueError as exc:
        raise RemoteError("TRUENAS_MINIMUM_FREE_BYTES must be an integer") from exc
    if not expected_version or not disk or not boot_partition:
        raise RemoteError(
            "TRUENAS_EXPECTED_VERSION, TRUENAS_DISK_DEVICE, and "
            "TRUENAS_BOOT_POOL_PARTITION are required"
        )

    target = Target.from_environment()
    require(target.user == "root", "Path B partition test requires SSH user root")
    pools = target.query("pool.query", timeout=90)
    require(not any(row.get("name") == "boot-disk-data" for row in pools),
            "fresh Path B target already has boot-disk-data; partition-only lane requires a fresh target")
    for method, label in (("vm.query", "VM"), ("container.query", "container")):
        rows = target.query(method, timeout=120)
        running = [
            row.get("name", row.get("id"))
            for row in rows
            if (row.get("status") or {}).get("state") == "RUNNING"
        ]
        require(not running, f"refusing partitioning while guest {label}s are running: {running!r}")

    result = provision_path_b(
        target,
        disk=disk,
        boot_pool_partition=boot_partition,
        expected_version=expected_version,
        minimum_free_bytes=minimum_bytes,
        apply=True,
        confirmed_disk=disk,
        partition_only=True,
    )
    require(result.get("state") in {"partition_created", "already_partitioned"},
            f"partition-only provisioner did not reserve the free tail: {result!r}")
    pools = target.query("pool.query", timeout=90)
    require(not any(row.get("name") == "boot-disk-data" for row in pools),
            "partition-only lane created or registered boot-disk-data outside the UI")
    require(target.shell("! zpool list -H -o name | grep -Fxq boot-disk-data", timeout=45) == "",
            "partition-only lane created a ZFS pool; UI must create boot-disk-data")
    print(
        f"[result] PASS reserved {result['partition']} on the guarded free tail; "
        "no ZFS pool was created so the registered UI lane can create boot-disk-data",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RemoteError, ValueError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr, flush=True)
        raise SystemExit(1)
