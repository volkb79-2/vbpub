#!/usr/bin/env python3
"""Destructively test the guarded Path B provisioner on a disposable guest."""

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
    minimum_bytes = int(os.environ.get("TRUENAS_MINIMUM_FREE_BYTES", "1073741824"))
    if not expected_version or not disk or not boot_partition:
        raise RemoteError(
            "TRUENAS_EXPECTED_VERSION, TRUENAS_DISK_DEVICE, and "
            "TRUENAS_BOOT_POOL_PARTITION are required"
        )

    target = Target.from_environment()
    require(target.user == "root", "Path B partition test requires SSH user root")
    for method, label in (("vm.query", "VM"), ("container.query", "container")):
        rows = target.query(method, timeout=120)
        running = [
            row.get("name", row.get("id"))
            for row in rows
            if row.get("status", {}).get("state") == "RUNNING"
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
    )
    require(result.get("state") in {"created", "already_configured"},
            f"provisioner did not create or recognize the pool: {result!r}")
    # The second invocation must recognize the completed state without another
    # GPT write, zpool create, or pool import.
    repeated = provision_path_b(
        target,
        disk=disk,
        boot_pool_partition=boot_partition,
        expected_version=expected_version,
        minimum_free_bytes=minimum_bytes,
        apply=True,
        confirmed_disk=disk,
    )
    require(repeated.get("state") == "already_configured",
            f"repeat invocation was not an idempotent no-op: {repeated!r}")
    pools = target.query("pool.query", [["name", "=", "boot-disk-data"]], timeout=90)
    require(len(pools) == 1 and pools[0].get("status") == "ONLINE",
            f"middleware does not report one ONLINE Path B pool: {pools!r}")
    print(
        f"[result] PASS {expected_version}: {result['partition']} is registered ONLINE; "
        "repeat invocation made no changes",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RemoteError, ValueError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1)
