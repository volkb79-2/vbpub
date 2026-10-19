#!/usr/bin/env python3
"""Verify boot-pool user-data mounts and API access across a TrueNAS reboot.

This intentionally reboots its target. Set both mutation guards only for a
disposable TrueNAS VM or host whose reboot is explicitly planned.
"""

from __future__ import annotations

import hashlib
import os
import shlex
import sys
import time
import uuid

from remote import RemoteError, Target, flatten_datasets


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

        require_mount(target, root, root_mountpoint)
        choices = target.call("container.pool_choices", timeout=120)
        require("boot-pool" in choices, f"container.pool_choices omits mounted boot-pool: {choices!r}")

        if not root_initially_mounted:
            report("recovery", "boot hook mounted user-data; container pool choices now include boot-pool")
            before = create_marker()
            report("pre-reboot", f"created {marker_dataset}; marker SHA-256 {before}")
            unmount_user_data_tree()
            reboot_and_wait("persistence reboot")
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
