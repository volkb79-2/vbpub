"""Validate and inspect data recorded by the Path A UI workload gate."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import shlex

from remote import RemoteError, Target


DATASET_RE = re.compile(r"boot-pool/user-data/ui-acceptance-[0-9]{14}[0-9a-f]{6}")
CONTAINER_RE = re.compile(r"uiacceptance[0-9a-f]{8}")
VM_RE = re.compile(r"uiacceptancevm[0-9a-f]{8}")
EXPECTED_MARKER_BYTES = 65_536


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RemoteError(message)


def report(stage: str, detail: str) -> None:
    print(f"[{stage}] {detail}", flush=True)


def read_manifest(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RemoteError(f"cannot read UI workload manifest: {type(exc).__name__}") from None
    require(isinstance(value, dict) and value.get("schema_version") == 1,
            "unsupported UI workload manifest schema")
    require(value.get("profile") == "path-a" and value.get("pool") == "boot-pool",
            "UI workload manifest is not for Path A on boot-pool")
    filesystem = value.get("filesystem")
    container = value.get("container")
    vm = value.get("vm")
    zvols = value.get("zvols")
    require(all(isinstance(item, dict) for item in (filesystem, container, vm, zvols)),
            "UI workload manifest is missing its filesystem, container, VM, or zvol records")
    dataset = filesystem.get("dataset")
    mountpoint = filesystem.get("mountpoint")
    probe_file = filesystem.get("probe_file")
    require(isinstance(dataset, str) and DATASET_RE.fullmatch(dataset) is not None,
            "UI workload manifest has an invalid dataset path")
    require(mountpoint == f"/mnt/{dataset}",
            "UI workload manifest mountpoint does not match its dataset")
    require(probe_file == f"{mountpoint}/ui-disk-probe.bin",
            "UI workload manifest has an unexpected filesystem probe path")
    require(filesystem.get("probe_bytes") == EXPECTED_MARKER_BYTES,
            "UI workload manifest has an unexpected filesystem probe size")
    require(re.fullmatch(r"[0-9a-f]{64}", str(filesystem.get("probe_sha256", ""))) is not None,
            "UI workload manifest has an invalid filesystem probe digest")
    require(isinstance(container.get("name"), str) and CONTAINER_RE.fullmatch(container["name"]),
            "UI workload manifest has an invalid container name")
    require(type(container.get("id")) is int, "UI workload manifest has an invalid container id")
    require(container.get("pool") == "boot-pool", "UI workload container pool is not boot-pool")
    require(container.get("dataset") == (
        f"boot-pool/user-data/.truenas_containers/containers/{container['name']}"
    ), "UI workload manifest container dataset does not match its name")
    require(isinstance(vm.get("name"), str) and VM_RE.fullmatch(vm["name"]),
            "UI workload manifest has an invalid VM name")
    require(type(vm.get("id")) is int, "UI workload manifest has an invalid VM id")
    require(vm.get("pool") == "boot-pool", "UI workload VM pool is not boot-pool")
    require(vm.get("zvol") == f"{dataset}/vm-disk", "UI workload manifest VM zvol is unexpected")
    require(vm.get("device_path") == f"/dev/zvol/{vm['zvol']}",
            "UI workload manifest VM device path does not match its zvol")
    for key, expected_suffix in (("vm_disk", "vm-disk"), ("cache_device", "cache-device")):
        item = zvols.get(key)
        require(isinstance(item, dict), f"UI workload manifest is missing zvol entry {key}")
        require(item.get("dataset") == f"{dataset}/{expected_suffix}",
                f"UI workload manifest zvol {key} has an unexpected dataset")
        require(item.get("bytes") == EXPECTED_MARKER_BYTES,
                f"UI workload manifest zvol {key} has an unexpected probe size")
        require(re.fullmatch(r"[0-9a-f]{64}", str(item.get("sha256", ""))) is not None,
                f"UI workload manifest zvol {key} has an invalid digest")
    return value


def state_of(row: dict) -> str:
    return str((row.get("status") or {}).get("state", "")).upper()


def one_record(target: Target, method: str, object_id: int, expected_name: str) -> dict:
    rows = target.query(method, [["id", "=", object_id]], timeout=120)
    require(len(rows) == 1, f"{method} returned {len(rows)} records for id {object_id}")
    row = rows[0]
    require(row.get("name") == expected_name,
            f"{method} id {object_id} is {row.get('name')!r}, expected {expected_name!r}")
    return row


def verify_records(target: Target, manifest: dict) -> tuple[dict, dict]:
    container = manifest["container"]
    vm = manifest["vm"]
    container_row = one_record(target, "container.query", container["id"], container["name"])
    vm_row = one_record(target, "vm.query", vm["id"], vm["name"])
    require(container_row.get("dataset") == container["dataset"],
            f"container record dataset is {container_row.get('dataset')!r}, "
            f"expected {container['dataset']!r}")
    if container_row.get("pool") is not None:
        require(container_row["pool"] == "boot-pool",
                f"container record is on {container_row['pool']!r}, not boot-pool")
    vm_devices = target.query("vm.device.query", [["vm", "=", vm["id"]]], timeout=120)
    attached = [
        (device.get("attributes") or {}).get("path", device.get("path"))
        for device in vm_devices
    ]
    require(vm["device_path"] in attached,
            f"VM no longer references its recorded boot-pool zvol: {attached!r}")
    report(
        "inventory",
        f"container {container['name']} state={state_of(container_row)}; "
        f"VM {vm['name']} state={state_of(vm_row)}; VM disk={vm['device_path']}",
    )
    return container_row, vm_row


def verify_data(target: Target, manifest: dict, *, stage: str) -> bool:
    """Verify zvol bytes and, when mounted, the filesystem bytes. Return mount state."""
    filesystem = manifest["filesystem"]
    dataset = filesystem["dataset"]
    mounted = target.shell(f"zfs get -H -o value mounted {shlex.quote(dataset)}")
    if mounted != "yes":
        report(stage, f"filesystem dataset {dataset} mounted={mounted}; filesystem hash not yet readable")
        filesystem_mounted = False
    else:
        mountpoint = filesystem["mountpoint"]
        statfs = target.call("filesystem.statfs", mountpoint, timeout=90)
        require(statfs.get("source") == dataset and statfs.get("dest") == mountpoint,
                f"{stage}: filesystem is not accessible from its expected Path A mountpoint: {statfs!r}")
        digest = target.shell(f"sha256sum {shlex.quote(filesystem['probe_file'])}").split()[0]
        require(digest == filesystem["probe_sha256"],
                f"{stage}: filesystem probe hash changed: {digest} != {filesystem['probe_sha256']}")
        filesystem_mounted = True
    for key, item in manifest["zvols"].items():
        device = f"/dev/zvol/{item['dataset']}"
        target.shell(f"test -b {shlex.quote(device)}")
        output = target.shell(
            f"dd if={shlex.quote(device)} bs=4096 count=16 status=none | sha256sum",
            timeout=90,
        )
        digest = output.split()[0] if output else ""
        require(re.fullmatch(r"[0-9a-f]{64}", digest) is not None,
                f"{stage}: could not read probe data from {device}")
        require(digest == item["sha256"],
                f"{stage}: zvol probe hash changed for {key}: {digest} != {item['sha256']}")
    report(stage, f"zvol probes match; filesystem mounted={filesystem_mounted}")
    return filesystem_mounted
