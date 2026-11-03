#!/usr/bin/env python3
"""Exercise the storage patch against a disposable live TrueNAS guest."""

from __future__ import annotations

import os
import json
import re
import shlex
import sys
import time
import uuid
from typing import Any

from remote import RemoteError, Target, flatten_datasets, wait_for_container, wait_for_vm


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RemoteError(message)


def report(stage: str, detail: str) -> None:
    print(f"[{stage}] {detail}", flush=True)


def initial_dataset_inventory(
    profile: str, root: str, rows: list[dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], bool]:
    """Require the selected root to be queryable before storage mutations."""
    datasets = flatten_datasets(rows)
    root_visible = root in datasets
    label = "Path A managed namespace" if profile == "path-a" else "Path B pool"
    require(root_visible, f"dataset query omits the {label} root {root}")
    return datasets, root_visible


def container_dataset_root(pool: str, profile: str) -> str:
    if profile == "path-a":
        return f"{pool}/user-data/.truenas_containers"
    if profile == "path-b":
        return f"{pool}/.truenas_containers"
    raise RemoteError(f"unknown storage profile: {profile!r}")


def zfs_object_inventory(
    target: Target,
    dataset: str,
    object_types: str,
    *,
    recursive: bool,
) -> list[str]:
    """Return an exact, checked ZFS object listing for before/after comparisons."""
    argv = ["zfs", "list", "-H", "-t", object_types, "-o", "name"]
    if recursive:
        argv.extend(("-r", dataset))
    else:
        argv.append(dataset)
    output = target.run(argv, timeout=45)
    return sorted(line for line in output.splitlines() if line)


def cleanup_test_mountpoints(target: Target, root_probe: str, descendants: list[str]) -> None:
    """Remove only empty mountpoint directories created by this uniquely named gate tree."""
    root_prefix = next(
        (candidate for candidate in ("boot-pool/user-data", "boot-disk-data")
         if root_probe.startswith(candidate + "/")),
        None,
    )
    require(root_prefix is not None, f"refusing unexpected test cleanup root {root_probe!r}")
    suffix = root_probe.removeprefix(root_prefix + "/")
    require(
        re.fullmatch(r"patch-[0-9]{14}[0-9a-f]{6}", suffix) is not None,
        f"refusing unexpected test cleanup name {root_probe!r}",
    )
    expected_descendants = {
        f"{root_probe}/data",
        f"{root_probe}/my-vms",
        f"{root_probe}/container-data",
    }
    require(
        set(descendants) == expected_descendants and len(descendants) == len(expected_descendants),
        f"refusing unexpected test cleanup descendants: {descendants!r}",
    )

    # Destroying the datasets unmounts them but can leave their empty mountpoint
    # directories under the managed root. rmdir is intentionally non-recursive:
    # any content or unexpected file type makes cleanup fail and remain visible.
    for dataset in [*descendants, root_probe]:
        path = f"/mnt/{dataset}"
        quoted_path = shlex.quote(path)
        target.shell(
            "set -eu; "
            f"path={quoted_path}; "
            "if [ -L \"$path\" ]; then echo 'unexpected symlink' >&2; exit 1; fi; "
            "if [ -d \"$path\" ]; then rmdir -- \"$path\"; "
            "elif [ -e \"$path\" ]; then echo 'unexpected non-directory' >&2; exit 1; fi; "
            "test ! -e \"$path\" && test ! -L \"$path\"",
            timeout=45,
        )


def main() -> int:
    target = Target.from_environment()
    profile = os.environ.get("TRUENAS_STORAGE_PROFILE", "")
    if profile not in {"path-a", "path-b"}:
        raise RemoteError("TRUENAS_STORAGE_PROFILE must be path-a or path-b")
    expected_version = os.environ.get("TRUENAS_EXPECTED_VERSION", "")
    if not expected_version:
        raise RemoteError("TRUENAS_EXPECTED_VERSION is required")
    if os.environ.get("TRUENAS_TEST_ALLOW_MUTATIONS") != "1":
        raise RemoteError("set TRUENAS_TEST_ALLOW_MUTATIONS=1 only for a disposable TrueNAS guest")

    info = target.call("system.info")
    version = str(info.get("version", ""))
    require(version == expected_version, f"expected TrueNAS {expected_version}, got {version!r}")
    require(target.shell("test ! -e /dev/kvm && printf absent") == "absent",
            "the guest exposes /dev/kvm; this run would not test the no-KVM TCG path")
    require(target.call("vm.virtualization_details").get("supported") is True,
            "patched vm.virtualization_details does not report a usable backend")
    flags = target.call("vm.flags")
    require(not any(flags.values()), f"TCG guest advertises hardware virtualization flags: {flags!r}")

    if profile == "path-a":
        pool = "boot-pool"
        root = "boot-pool/user-data"
        require(target.shell("test ! -e /dev/vda4 && printf absent") == "absent",
                "path-a guest unexpectedly has a partition 4; it is not the full-disk layout")
        rows = target.call("container.pool_choices")
        require("boot-pool" in rows, f"container.pool_choices omits provisioned boot-pool: {rows!r}")
        target.shell(
            "quota=$(zfs get -H -p -o value quota boot-pool/user-data) && "
            "case \"$quota\" in ''|*[!0-9]*) exit 1 ;; esac && "
            "test \"$quota\" -gt 0 && "
            "zfs get -H -o value mountpoint boot-pool/user-data | "
            "grep -Fx /mnt/boot-pool/user-data && "
            "zfs get -H -o value canmount boot-pool/user-data | grep -Fx on"
        )
    else:
        pool = "boot-disk-data"
        root = "boot-disk-data"
        pools = target.query("pool.query")
        require(any(row.get("name") == pool for row in pools),
                f"pool.query does not report {pool}; Path B is not provisioned")
        require(target.shell("test -b /dev/vda4 && printf present") == "present",
                "Path B partition vda4 is missing")
        rows = target.call("container.pool_choices")
        require(pool in rows, f"container.pool_choices omits {pool}: {rows!r}")

    datasets, root_visible = initial_dataset_inventory(
        profile, root, target.query("pool.dataset.query", timeout=120),
    )
    report(
        "inventory",
        f"initial pool.dataset.query returned {len(datasets)} datasets; "
        f"selected root is {'visible' if root_visible else 'not user-visible'}",
    )

    # A create under a protected system dataset must be refused without leaving
    # a dataset behind. The path is unique and is never a real system name.
    protected_probe = f"boot-pool/ROOT/patch-refusal-{uuid.uuid4().hex[:10]}"
    failure = target.run(
        ["midclt", "call", "pool.dataset.create", json.dumps({"name": protected_probe, "type": "FILESYSTEM"})],
        timeout=90,
        check=False,
    )
    if not failure:
        target.call("pool.dataset.delete", protected_probe, {"recursive": False}, timeout=90)
        raise RemoteError("dataset create below boot-pool/ROOT unexpectedly succeeded; probe removed")
    protected_zfs_datasets = set(target.shell(
        "zfs list -H -o name -t filesystem,volume -r boot-pool/ROOT",
        timeout=90,
    ).splitlines())
    require(protected_probe not in protected_zfs_datasets,
            "rejected protected dataset create left a ZFS dataset behind")
    report("preflight", f"TrueNAS {version}, {profile}, {pool} selected, /dev/kvm absent")

    suffix = time.strftime("%Y%m%d%H%M%S", time.gmtime()) + uuid.uuid4().hex[:6]
    root_probe = f"{root}/patch-{suffix}"
    fs_name = f"{root_probe}/data"
    vm_dataset = f"{root_probe}/my-vms"
    zvol_name = f"{vm_dataset}/disk"
    cache_name = f"{vm_dataset}/cache"
    nfs_id = smb_id = vm_id = container_id = None
    temporary_pool_name: str | None = None
    temporary_pool_file: str | None = None
    image_name = "alpine:3.24:amd64:default"
    container_dataset = f"{root_probe}/container-data"
    cleanup_errors: list[str] = []

    try:
        created = target.call("pool.dataset.create", {
            "name": fs_name,
            "type": "FILESYSTEM",
            "create_ancestors": True,
        })
        require(created.get("id") == fs_name, f"filesystem create returned an unexpected record: {created!r}")
        target.call("pool.dataset.update", fs_name, {"compression": "LZ4"})
        after_update = flatten_datasets(target.query("pool.dataset.query", timeout=120))
        require(fs_name in after_update, f"new dataset is missing from query: {fs_name}")
        protected = [name for name in after_update if name.startswith("boot-pool/") and (
            name.startswith("boot-pool/ROOT") or name.startswith("boot-pool/.system") or name.startswith("boot-pool/grub")
        )]
        require(not protected, f"dataset query exposes protected boot-pool datasets: {protected[:12]!r}")
        require(after_update[fs_name].get("compression", {}).get("value") == "LZ4",
                "dataset update did not persist compression=LZ4")
        report("dataset", "create, query, update passed")

        clone_source_snapshot = f"{fs_name}@clone-refusal-{suffix}"
        target.call("zfs.resource.snapshot.create", {
            "dataset": fs_name,
            "name": f"clone-refusal-{suffix}",
        })

        vm_parent = target.call("pool.dataset.create", {
            "name": vm_dataset,
            "type": "FILESYSTEM",
            "create_ancestors": False,
        })
        require(vm_parent.get("id") == vm_dataset,
                f"VM dataset parent create returned an unexpected record: {vm_parent!r}")
        zvol = target.call("pool.dataset.create", {
            "name": zvol_name,
            "type": "VOLUME",
            "volsize": 64 * 1024 * 1024,
            "sparse": True,
            "create_ancestors": False,
        })
        require(zvol.get("id") == zvol_name, f"zvol create returned an unexpected record: {zvol!r}")
        target.call("pool.dataset.update", zvol_name, {"volsize": 128 * 1024 * 1024})
        after_grow = flatten_datasets(target.query("pool.dataset.query", timeout=120))
        require(after_grow.get(zvol_name, {}).get("type") == "VOLUME", "zvol is missing after grow")
        require(after_grow[zvol_name].get("volsize", {}).get("parsed") == 128 * 1024 * 1024,
                "zvol size did not grow to 128 MiB")
        disk_choices = target.call("vm.device.disk_choices", timeout=90)
        choices_text = json.dumps(disk_choices)
        require(zvol_name in choices_text, f"VM disk choices omit user zvol {zvol_name}")
        report("zvol", "create, grow, query, VM disk choice passed")

        cache = target.call("pool.dataset.create", {
            "name": cache_name,
            "type": "VOLUME",
            "volsize": 128 * 1024 * 1024,
            "sparse": True,
            "create_ancestors": False,
        })
        require(cache.get("id") == cache_name, "cache zvol create did not return the requested dataset")
        other_pool = os.environ.get("TRUENAS_TEST_CACHE_TARGET", "")
        device_path = f"/dev/zvol/{cache_name}"
        temporary_pool_file = f"/mnt/{fs_name}/cache-target.img"
        candidate_pool_name = f"patchcache{suffix[-10:]}"
        if not other_pool:
            target.shell(
                f"truncate -s 256M {shlex.quote(temporary_pool_file)} && "
                f"zpool create -f -o cachefile=none -o altroot=/mnt -O mountpoint=none "
                f"{shlex.quote(candidate_pool_name)} {shlex.quote(temporary_pool_file)}",
                timeout=90,
            )
            other_pool = candidate_pool_name
            temporary_pool_name = candidate_pool_name
        require(other_pool != pool, "cache target must differ from the selected data pool")
        target.shell(f"zpool add {shlex.quote(other_pool)} cache {shlex.quote(device_path)}")
        cache_status = target.shell(f"zpool status -P {shlex.quote(other_pool)}")
        require(device_path in cache_status, "ZFS did not register the user zvol as a cache vdev")
        target.shell(f"zpool remove {shlex.quote(other_pool)} {shlex.quote(device_path)}")
        require(device_path not in target.shell(f"zpool status -P {shlex.quote(other_pool)}"),
                "temporary cache vdev could not be removed")
        if temporary_pool_name:
            exported_pool = temporary_pool_name
            target.shell(f"zpool export {shlex.quote(other_pool)}")
            temporary_pool_name = None
            pools_after_export = target.shell("zpool list -H -o name").splitlines()
            require(exported_pool not in pools_after_export,
                    f"temporary cache pool {exported_pool} remains imported after export")
        report("cache", f"zvol from {pool} attached to and removed from {other_pool}")

        vm_name = f"tnpatch{suffix[-8:]}"
        report("vm", f"creating disposable VM definition {vm_name}")
        vm = target.call("vm.create", {
            "name": vm_name,
            "description": "disposable boot-pool patch acceptance VM",
            "memory": 256,
            "vcpus": 1,
            "bootloader": "UEFI",
            "uuid": str(uuid.uuid4()),
            "autostart": False,
            "cpu_mode": "CUSTOM",
            "cpu_model": "qemu64",
        })
        vm_id = int(vm["id"])
        report("vm", f"created VM {vm_id}; attaching the Path A/B test zvol")
        target.call("vm.device.create", {
            "vm": vm_id,
            "order": 1000,
            "attributes": {
                "dtype": "DISK",
                "path": f"/dev/zvol/{zvol_name}",
                "type": "VIRTIO",
                "create_zvol": False,
            },
        })
        report("vm", f"starting VM {vm_id} through the middleware API")
        target.call("vm.start", vm_id, {"overcommit": False}, timeout=180)
        vm_row = wait_for_vm(target, vm_id, "RUNNING")
        xml = target.shell(
            "virsh -c 'qemu+unix:///system?socket=/run/truenas_libvirt/libvirt-sock' "
            f"dumpxml {shlex.quote(vm_row['uuid'])}",
            timeout=45,
        )
        require(re.search(r"<domain[^>]*type=['\"]qemu['\"]", xml) is not None,
                "libvirt domain XML did not select the qemu (TCG) backend")
        process_probe = (
            "python3 - " + shlex.quote(vm_row["uuid"]) + " <<'PY'\n"
            "import pathlib,sys\n"
            "needle=sys.argv[1].encode()\n"
            "matches=[]\n"
            "for p in pathlib.Path('/proc').iterdir():\n"
            "    if not p.name.isdigit(): continue\n"
            "    try: a=p.joinpath('cmdline').read_bytes().split(b'\\0')\n"
            "    except (OSError, PermissionError): continue\n"
            "    if b'qemu-system' not in b' '.join(a) or needle not in b' '.join(a): continue\n"
            "    matches.append(a)\n"
            "if not matches: raise SystemExit('no QEMU process found for VM UUID')\n"
            "if not any(any(a[i] == b'-accel' and i+1 < len(a) and b'tcg' in a[i+1] for i in range(len(a))) for a in matches):\n"
            "    raise SystemExit('VM QEMU process is not using -accel tcg')\n"
            "print('tcg')\n"
            "PY"
        )
        require(target.shell(process_probe, timeout=45) == "tcg",
                "QEMU command line does not confirm TCG")
        report("vm", "API create, zvol attachment, start, qemu XML and TCG process passed")

        container_pools = target.call("container.pool_choices")
        require(pool in container_pools, f"container pool choices lost {pool}: {container_pools!r}")
        image_rows = target.call("container.image.query_registry", timeout=120)
        image = next((row for row in image_rows if row.get("name") == image_name), None)
        require(image and image.get("versions"), f"test image {image_name} has no registry versions")
        image_version = sorted(row["version"] for row in image["versions"])[-1]
        target.call("pool.dataset.create", {
            "name": container_dataset,
            "type": "FILESYSTEM",
            "create_ancestors": True,
        })
        container_name = f"tnpatch-{suffix[-8:]}"
        created_container = target.call("container.create", {
            "name": container_name,
            "pool": pool,
            "image": {"name": image_name, "version": image_version},
            "autostart": False,
            "time": "LOCAL",
            "shutdown_timeout": 30,
            "capabilities_policy": "DEFAULT",
            "idmap": {"type": "DEFAULT"},
        }, timeout=120)
        create_job = int(created_container)
        created_result = target.wait_job(create_job, timeout=900, label="container create")
        container_id = int(created_result["id"] if isinstance(created_result, dict) else created_result)
        protected_container_dataset = (
            f"{container_dataset_root(pool, profile)}/containers/{container_name}"
        )
        protected_datasets_before_destroy = zfs_object_inventory(
            target, protected_container_dataset, "filesystem,volume", recursive=True,
        )
        require(
            protected_container_dataset in protected_datasets_before_destroy,
            "middleware-managed container dataset was not present before destroy: "
            f"{protected_datasets_before_destroy!r}",
        )
        protected_snapshots_before_destroy = zfs_object_inventory(
            target, protected_container_dataset, "snapshot", recursive=True,
        )
        protected_destroy = target.run([
            "midclt", "call", "zfs.resource.destroy",
            json.dumps({"path": protected_container_dataset, "recursive": False}),
        ], timeout=90, check=False)
        protected_datasets_after_destroy = zfs_object_inventory(
            target, protected_container_dataset, "filesystem,volume", recursive=True,
        )
        protected_snapshots_after_destroy = zfs_object_inventory(
            target, protected_container_dataset, "snapshot", recursive=True,
        )
        require(
            protected_datasets_after_destroy == protected_datasets_before_destroy,
            "public destroy changed the existing container-managed dataset tree: "
            f"before={protected_datasets_before_destroy!r} after={protected_datasets_after_destroy!r}",
        )
        require(
            protected_snapshots_after_destroy == protected_snapshots_before_destroy,
            "public destroy changed snapshots under the existing container-managed dataset: "
            f"before={protected_snapshots_before_destroy!r} after={protected_snapshots_after_destroy!r}",
        )
        require(
            "is a protected path" in protected_destroy,
            "direct zfs.resource.destroy did not refuse an existing container-managed dataset: "
            + (protected_destroy or "the call unexpectedly succeeded"),
        )
        protected_snapshot_name = "patch-refusal-" + uuid.uuid4().hex[:10]
        snapshot_path = f"{protected_container_dataset}@{protected_snapshot_name}"
        require(
            snapshot_path not in protected_snapshots_after_destroy,
            f"random protected snapshot probe already exists before create: {snapshot_path}",
        )
        protected_snapshot = target.run([
            "midclt", "call", "zfs.resource.snapshot.create",
            json.dumps({"dataset": protected_container_dataset, "name": protected_snapshot_name}),
        ], timeout=90, check=False)
        require(
            "is a protected path" in protected_snapshot,
            "direct zfs.resource.snapshot.create did not refuse an existing container-managed dataset: "
            + (protected_snapshot or "the call unexpectedly succeeded"),
        )
        protected_datasets_after_snapshot = zfs_object_inventory(
            target, protected_container_dataset, "filesystem,volume", recursive=True,
        )
        protected_snapshots_after_snapshot = zfs_object_inventory(
            target, protected_container_dataset, "snapshot", recursive=True,
        )
        require(
            protected_datasets_after_snapshot == protected_datasets_after_destroy,
            "snapshot attempt changed the existing container-managed dataset tree: "
            f"before={protected_datasets_after_destroy!r} after={protected_datasets_after_snapshot!r}",
        )
        require(
            protected_snapshots_after_snapshot == protected_snapshots_after_destroy,
            "snapshot attempt changed the snapshot tree under the existing container-managed dataset: "
            f"before={protected_snapshots_after_destroy!r} after={protected_snapshots_after_snapshot!r}",
        )
        require(
            snapshot_path not in protected_snapshots_after_snapshot,
            f"protected snapshot refusal left the attempted snapshot behind: {snapshot_path}",
        )
        report(
            "protection-state",
            f"dataset trees matched before/after destroy and snapshot attempts; "
            f"datasets={len(protected_datasets_before_destroy)} snapshots="
            f"{len(protected_snapshots_before_destroy)}; attempted snapshot absent",
        )
        report("protection", "public ZFS destroy and snapshot creation refuse an existing container dataset")
        clone_refusal_destination = (
            f"{container_dataset_root(pool, profile)}/containers/clone-refusal-{suffix}"
        )
        public_clone = target.run([
            "midclt", "call", "pool.snapshot.clone",
            json.dumps({
                "snapshot": clone_source_snapshot,
                "dataset_dst": clone_refusal_destination,
            }),
        ], timeout=90, check=False)
        require(
            "is a protected path" in public_clone,
            "public pool.snapshot.clone did not refuse a container-managed destination: "
            + (public_clone or "the call unexpectedly succeeded"),
        )
        clone_destination_inventory = target.run(
            ["zfs", "list", "-H", clone_refusal_destination], timeout=45, check=False,
        )
        require(
            "dataset does not exist" in clone_destination_inventory,
            "protected clone refusal created or found a destination dataset: "
            + clone_destination_inventory,
        )
        report("protection", "public pool.snapshot.clone refuses a container-managed destination")
        if profile == "path-a":
            internal_root = (
                f"/mnt/boot-pool/user-data/.truenas_containers/boot-pool/containers/{container_name}"
            )
            require(target.shell(f"test -d {shlex.quote(internal_root)} && printf present") == "present",
                    "container-managed root dataset is not mounted for the protected-path probe")
            denied = target.run([
                "midclt", "call", "container.device.create",
                json.dumps({
                    "container": container_id,
                    "attributes": {"dtype": "FILESYSTEM", "source": internal_root, "target": "/protected"},
                }),
            ], timeout=90, check=False)
            require("system-managed boot-pool dataset" in denied,
                    "container-managed boot-pool dataset was not rejected by path validation: "
                    + (denied or "device create unexpectedly succeeded"))
        target.call("container.device.create", {
            "container": container_id,
            "attributes": {
                "dtype": "FILESYSTEM",
                "source": f"/mnt/{container_dataset}",
                "target": "/data",
            },
        }, timeout=90)
        start_result = target.call("container.start", container_id, timeout=180)
        if isinstance(start_result, int):
            target.wait_job(start_result, timeout=300, label="container start")
        container_row = wait_for_container(target, container_id, "RUNNING")
        pid = int(container_row["status"]["pid"])
        marker = f"{suffix}-persistent"
        target.shell(
            "nsenter -t " + str(pid) + " -m -u -i -n -p -- /bin/sh -c " +
            shlex.quote("printf %s " + shlex.quote(marker) + " > /data/patch-marker.txt"),
            timeout=60,
        )
        stored_marker = target.shell(f"cat {shlex.quote('/mnt/' + container_dataset + '/patch-marker.txt')}")
        require(stored_marker == marker, "container mounted dataset did not preserve written data")
        internal_container_root = container_dataset_root(pool, profile)
        visible_after_container_create = flatten_datasets(target.query("pool.dataset.query", timeout=120))
        visible_internal = [
            name for name in visible_after_container_create
            if name == internal_container_root or name.startswith(internal_container_root + "/")
        ]
        require(not visible_internal,
                f"dataset query exposes container-managed datasets: {visible_internal[:8]!r}")
        report("container", "pool choice, create, persistent dataset mount and marker passed")

        nfs_path = f"/mnt/{fs_name}"
        nfs = target.call("sharing.nfs.create", {
            "path": nfs_path,
            "comment": "disposable storage patch acceptance share",
            "enabled": False,
            "ro": False,
            "maproot_user": "",
            "maproot_group": "",
            "mapall_user": "",
            "mapall_group": "",
            "networks": [],
            "hosts": [],
            "security": [],
        })
        nfs_id = nfs["id"]
        nfs_rows = target.query("sharing.nfs.query", [["id", "=", nfs_id]])
        require(any(row.get("id") == nfs_id and row.get("path") == nfs_path for row in nfs_rows),
                "NFS share query did not return the newly-created share")
        smb = target.call("sharing.smb.create", {
            "purpose": "DEFAULT_SHARE",
            "name": f"tnpatch-{suffix[-8:]}",
            "path": nfs_path,
            "enabled": False,
            "comment": "disposable storage patch acceptance share",
            "readonly": False,
            "browsable": True,
            "access_based_share_enumeration": False,
            "audit": {"enable": False, "watch_list": [], "ignore_list": []},
            "options": {"aapl_name_mangling": False, "hostsallow": [], "hostsdeny": []},
        })
        smb_id = smb["id"]
        smb_rows = target.query("sharing.smb.query", [["id", "=", smb_id]])
        require(any(row.get("id") == smb_id and row.get("path") == nfs_path for row in smb_rows),
                "SMB share query did not return the newly-created share")
        report("shares", "NFS and SMB create/query passed")

    finally:
        if temporary_pool_name is not None:
            try:
                target.shell(f"zpool export {shlex.quote(temporary_pool_name)}", check=False)
                imported_pools = target.shell("zpool list -H -o name").splitlines()
                if temporary_pool_name in imported_pools:
                    cleanup_errors.append(f"temporary cache pool {temporary_pool_name} remains imported")
            except Exception as exc:
                cleanup_errors.append(f"temporary cache pool {temporary_pool_name}: {exc}")
        if vm_id is not None:
            try:
                current = target.query("vm.query", [["id", "=", vm_id]], timeout=45)
                if current and current[0].get("status", {}).get("state") == "RUNNING":
                    stop_job = target.call("vm.stop", vm_id, {"force": True}, timeout=120)
                    if isinstance(stop_job, int):
                        target.wait_job(stop_job, timeout=300, label="VM stop")
                target.call("vm.delete", vm_id, timeout=120)
                if target.query("vm.query", [["id", "=", vm_id]], timeout=60):
                    cleanup_errors.append(f"VM {vm_id} still exists after delete")
            except Exception as exc:  # cleanup failures must be reported at exit
                cleanup_errors.append(f"VM {vm_id}: {exc}")
        if container_id is not None:
            try:
                current = target.query("container.query", [["id", "=", container_id]], timeout=45)
                if current and current[0].get("status", {}).get("state") == "RUNNING":
                    stop_job = target.call("container.stop", container_id, {
                        "force": False,
                        "force_after_timeout": True,
                    }, timeout=120)
                    if isinstance(stop_job, int):
                        target.wait_job(stop_job, timeout=600, label="container stop")
                delete_job = target.call("container.delete", container_id, {
                    "force": False,
                    "recursive": False,
                }, timeout=120)
                if isinstance(delete_job, int):
                    target.wait_job(delete_job, timeout=600, label="container delete")
                if target.query("container.query", [["id", "=", container_id]], timeout=60):
                    cleanup_errors.append(f"container {container_id} still exists after delete")
            except Exception as exc:
                cleanup_errors.append(f"container {container_id}: {exc}")
        if smb_id is not None:
            try:
                target.call("sharing.smb.delete", smb_id)
                if target.query("sharing.smb.query", [["id", "=", smb_id]]):
                    cleanup_errors.append(f"SMB share {smb_id} still exists after delete")
            except Exception as exc:
                cleanup_errors.append(f"SMB share {smb_id}: {exc}")
        if nfs_id is not None:
            try:
                target.call("sharing.nfs.delete", nfs_id)
                if target.query("sharing.nfs.query", [["id", "=", nfs_id]]):
                    cleanup_errors.append(f"NFS share {nfs_id} still exists after delete")
            except Exception as exc:
                cleanup_errors.append(f"NFS share {nfs_id}: {exc}")
        try:
            target.call("pool.dataset.delete", root_probe, {"recursive": True}, timeout=120)
            remaining = flatten_datasets(target.query("pool.dataset.query", timeout=120))
            leftovers = [name for name in remaining if name == root_probe or name.startswith(root_probe + "/")]
            if leftovers:
                cleanup_errors.append(f"dataset tree {root_probe} still exists: {leftovers[:8]!r}")
        except Exception as exc:
            # An absent parent is normal when preflight or an earlier create failed.
            if "does not exist" not in str(exc):
                cleanup_errors.append(f"dataset tree {root_probe}: {exc}")
        try:
            cleanup_test_mountpoints(
                target, root_probe, [fs_name, vm_dataset, container_dataset],
            )
        except Exception as exc:
            cleanup_errors.append(f"empty test mountpoint directories for {root_probe}: {exc}")
        if cleanup_errors:
            report("cleanup-error", "; ".join(cleanup_errors))

    if cleanup_errors:
        raise RemoteError("cleanup failed: " + "; ".join(cleanup_errors))
    report("cleanup", "temporary VMs, containers, shares, datasets and empty mountpoint directories removed")
    report("result", f"PASS TrueNAS {version} {profile}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RemoteError, KeyError, TypeError, ValueError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1)
