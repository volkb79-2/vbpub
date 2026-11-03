#!/usr/bin/env python3
"""Record a read-only inventory of a disposable TrueNAS guest."""

from __future__ import annotations

import json
import os
import re
import sys
from typing import Any

from remote import RemoteError, Target, flatten_datasets


def middleware_dataset_source_context(target: Target) -> str:
    """Capture the installed v27 create path and absolute-path validators read-only."""
    script = r'''python3 - <<'PY'
from pathlib import Path

root = Path('/usr/lib/python3/dist-packages/middlewared')
dataset = root / 'plugins/pool_/dataset.py'
lines = dataset.read_text().splitlines()
for label, marker in (
    ('parent mountpoint handling', "parent_mp = parent_ds['mountpoint']"),
    ('patched root zfs properties', "zprops['quota'] = data['quota']"),
):
    index = next((number for number, line in enumerate(lines) if marker in line), None)
    if index is None:
        print(f'=== {dataset}: marker not present: {marker} ===')
        continue
    start = max(0, index - 3)
    end = min(len(lines), index + 12)
    print(f'=== {dataset}:{start + 1}-{end} {label} ===')
    for number in range(start, end):
        print(f'{number + 1}: {lines[number]}')

matches = []
for path in root.rglob('*.py'):
    try:
        source = path.read_text(errors='replace').splitlines()
    except OSError:
        continue
    for index, line in enumerate(source):
        if 'path must be absolute' in line.lower():
            start = max(0, index - 5)
            end = min(len(source), index + 6)
            matches.append((path, start, end, source))
for path, start, end, source in matches:
    print(f'=== {path}:{start + 1}-{end} absolute-path validation ===')
    for number in range(start, end):
        print(f'{number + 1}: {source[number]}')
if not matches:
    print('No literal "path must be absolute" found under middlewared package.')
PY'''
    return target.shell(script, timeout=120)


def report(label: str, value: Any) -> None:
    print(f"[{label}] {json.dumps(value, sort_keys=True, default=str)}", flush=True)


def direct_storage_inventory(filesystems: list[str], pool_health: list[str]) -> dict[str, Any]:
    """Expose exact target dataset and pool names; counts cannot prove absence."""
    names = sorted({row.split("\t", 1)[0] for row in filesystems if row})
    pools = {row.split("\t", 1)[0] for row in pool_health if row}
    boot_pool_names = [name for name in names if name == "boot-pool" or name.startswith("boot-pool/")]
    boot_disk_data_names = [
        name for name in names if name == "boot-disk-data" or name.startswith("boot-disk-data/")
    ]
    root = "boot-pool/user-data"
    return {
        "direct_dataset_names": names,
        "direct_boot_pool_dataset_names": boot_pool_names,
        "direct_user_data_root_present": root in names,
        "direct_user_data_descendants": [name for name in names if name.startswith(root + "/")],
        "direct_boot_disk_data_pool_present": "boot-disk-data" in pools,
        "direct_boot_disk_data_dataset_names": boot_disk_data_names,
    }


def validate_storage_profile(profile: str, inventory: dict[str, Any]) -> None:
    """Require the exact Path A root and Path B pool shape requested by the caller."""
    expected = {
        "path-a": (True, False),
        "path-b": (False, True),
        "both": (True, True),
    }
    if profile not in expected:
        raise RemoteError("TRUENAS_STORAGE_PROFILE must be path-a, path-b, or both")
    observed = (
        inventory["direct_user_data_root_present"],
        inventory["direct_boot_disk_data_pool_present"],
    )
    if observed != expected[profile]:
        raise RemoteError(
            f"storage profile {profile!r} expected Path A/Path B presence "
            f"{expected[profile]}, got {observed}"
        )


def main() -> int:
    target = Target.from_environment()
    profile = os.environ.get("TRUENAS_STORAGE_PROFILE", "")
    expected = os.environ.get("TRUENAS_EXPECTED_VERSION", "")
    if profile not in {"path-a", "path-b", "both"} or not expected:
        raise RemoteError(
            "TRUENAS_STORAGE_PROFILE must be path-a, path-b, or both; "
            "TRUENAS_EXPECTED_VERSION is also required"
        )

    info = target.call("system.info", timeout=120)
    version = str(info.get("version", ""))
    if version != expected:
        raise RemoteError(f"expected TrueNAS {expected}, got {version!r}")
    pools = target.query("pool.query", timeout=120)
    datasets = target.query("pool.dataset.query", timeout=120)
    containers = target.query("container.query", timeout=120)
    vms = target.query("vm.query", timeout=120)
    filesystems = target.shell(
        "zfs list -H -p -t filesystem,volume -o name,type,used,refer,creation",
        timeout=120,
    ).splitlines()
    snapshots = target.shell(
        "zfs list -H -p -t snapshot -o name,used,creation 2>/dev/null || true",
        timeout=120,
    ).splitlines()
    user_data_root_snapshots = [
        row for row in snapshots if row.startswith("boot-pool/user-data@")
    ]
    domains = target.shell(
        "virsh -c 'qemu+unix:///system?socket=/run/truenas_libvirt/libvirt-sock' "
        "list --all 2>&1",
        timeout=60,
        check=False,
    )
    pool_health = target.shell("zpool list -H -o name,health", timeout=90).splitlines()
    block_device_inventory = target.shell(
        "lsblk --json --tree --paths --bytes --output "
        "PATH,NAME,TYPE,SIZE,RO,RM,PARTN,PARTTYPE,PARTLABEL && "
        "printf '\\n=== boot-pool leaf devices ===\\n' && zpool status -P boot-pool",
        timeout=90,
    )
    usr_mount = target.shell("findmnt -n -o SOURCE,OPTIONS --target /usr", timeout=45)
    root_mount = target.shell("findmnt -n -o SOURCE,FSTYPE --target /root", timeout=45)
    user_data_mountpoint_state = target.shell(
        "if [ -e /mnt/boot-pool/user-data ] || [ -L /mnt/boot-pool/user-data ]; "
        "then printf present; else printf absent; fi",
        timeout=30,
    )
    patch_marker = target.shell(
        "test -f /root/v27-boot-pool-patch-install.json && cat /root/v27-boot-pool-patch-install.json || true",
        timeout=30,
    )
    async_validators_head = target.shell(
        "sed -n '1,24p' /usr/lib/python3/dist-packages/middlewared/async_validators.py",
        timeout=45,
    )
    middleware_source_context = middleware_dataset_source_context(target)
    middleware_target_hashes = target.shell(
        "sha256sum "
        "/usr/lib/python3/dist-packages/middlewared/plugins/pool_/dataset.py "
        "/usr/lib/python3/dist-packages/middlewared/plugins/pool_/dataset_info.py",
        timeout=45,
    )
    user_data_root_properties = target.shell(
        "if zfs list -H -o name boot-pool/user-data >/dev/null 2>&1; then "
        "zfs get -H -p -o property,value quota,mountpoint,canmount boot-pool/user-data; "
        "else printf absent; fi",
        timeout=45,
    )
    user_data_root_mount_source = target.shell(
        "findmnt -n -o SOURCE --target /mnt/boot-pool/user-data 2>/dev/null || true",
        timeout=30,
    )

    managed = [row for row in filesystems if ".truenas_containers" in row]
    probes = [
        row for row in filesystems
        if re.search(r"(?:patch-|ui-acceptance-|uiacceptance|probe)", row, re.IGNORECASE)
    ]
    probe_snapshots = [
        row for row in snapshots
        if re.search(r"(?:patch-refusal|clone-refusal|ui-acceptance|probe)", row, re.IGNORECASE)
    ]
    flattened = flatten_datasets(datasets)
    direct_storage = direct_storage_inventory(filesystems, pool_health)
    validate_storage_profile(profile, direct_storage)
    report("readonly-inventory", {
        "version": version,
        "profile": profile,
        "middleware_pool_query": pools,
        "direct_pool_health": pool_health,
        "direct_block_device_inventory": block_device_inventory,
        "usr_mount": usr_mount,
        "root_backup_mount": root_mount,
        "direct_user_data_mountpoint_state": user_data_mountpoint_state,
        "direct_user_data_root_properties": user_data_root_properties,
        "direct_user_data_root_mount_source": user_data_root_mount_source,
        "direct_user_data_root_snapshots": user_data_root_snapshots,
        **direct_storage,
        "v27_patch_install_record": patch_marker,
        "async_validators_first_24_lines": async_validators_head,
        "middleware_dataset_create_and_absolute_path_validation_source": middleware_source_context,
        "middleware_target_source_sha256": middleware_target_hashes,
        "middleware_dataset_count": len(flattened),
        "middleware_container_records": containers,
        "middleware_vm_records": vms,
        "direct_managed_container_datasets": managed,
        "direct_probe_datasets": probes,
        "direct_probe_snapshots": probe_snapshots,
        "libvirt_domains": domains,
    })
    report("result", "PASS read-only guest inventory completed; no guest mutation methods were called")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RemoteError, KeyError, TypeError, ValueError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr, flush=True)
        raise SystemExit(1)
