#!/usr/bin/env python3
"""Install the v27 adapter or run its guarded empty-Path-A root-create repair."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tarfile
import uuid

from remote import RemoteError, Target


PATCH_ROOT = Path(__file__).resolve().parents[1]
EXPECTED_VERSION = "27.0.0-RC.1"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RemoteError(message)


def report(stage: str, detail: str) -> None:
    print(f"[{stage}] {detail}", flush=True)


def require_fresh_unpatched_target(target: Target, profile: str) -> None:
    info = target.call("system.info", timeout=120)
    version = str(info.get("version", ""))
    require(version == EXPECTED_VERSION, f"expected {EXPECTED_VERSION}, got {version!r}")
    require(target.shell("test ! -e /dev/kvm", timeout=30) == "",
            "fresh v27 TCG target unexpectedly has /dev/kvm")
    require(target.shell("test ! -e /root/v27-boot-pool-patch-install.json", timeout=30) == "",
            "v27 patch install record already exists")
    require(target.shell("! zfs list -H -o name boot-pool/user-data >/dev/null 2>&1", timeout=30) == "",
            "boot-pool/user-data already exists; this is not a fresh Path A target")

    pool_rows = target.shell("zpool list -H -o name,health", timeout=90).splitlines()
    pools = {line.split("\t", 1)[0]: line.split("\t", 1)[1]
             for line in pool_rows if "\t" in line}
    require(pools.get("boot-pool") == "ONLINE",
            f"direct ZFS inventory does not show boot-pool ONLINE: {pool_rows!r}")
    require("boot-disk-data" not in pools,
            "boot-disk-data already exists; refusing to patch a configured Path B target")
    containers = target.query("container.query", timeout=120)
    vms = target.query("vm.query", timeout=120)
    require(containers == [], f"fresh target has existing container records: {containers!r}")
    require(vms == [], f"fresh target has existing VM records: {vms!r}")

    report(
        "readonly-inventory",
        f"profile={profile} version={version} direct_pools={pools!r} "
        f"user-data-absent=true containers=0 vms=0 kvm-absent=true",
    )


def make_bundle(profile: str) -> tuple[Path, str]:
    artifacts = PATCH_ROOT / "artifacts"
    artifacts.mkdir(parents=True, exist_ok=True)
    bundle = artifacts / f"v27-fresh-patch-bundle-{profile}-{uuid.uuid4().hex[:10]}.tar.gz"
    members = (
        (PATCH_ROOT / "v27" / "middleware.patch", "v27/middleware.patch"),
        (PATCH_ROOT / "v27" / "storage.patch", "v27/storage.patch"),
        (PATCH_ROOT / "v27" / "libvirt.patch", "v27/libvirt.patch"),
        (PATCH_ROOT / "v27" / "boot-mount.patch", "v27/boot-mount.patch"),
        (PATCH_ROOT / "v27" / "zvol-blocksize.patch", "v27/zvol-blocksize.patch"),
        (PATCH_ROOT / "v27" / "targets.sha256", "v27/targets.sha256"),
        (PATCH_ROOT / "shared" / "virtualization_support.py", "shared/virtualization_support.py"),
        (PATCH_ROOT / "shared" / "boot_pool_user_data.py", "shared/boot_pool_user_data.py"),
        (PATCH_ROOT / "acceptance" / "apply_v27_patch_remote.sh",
         "acceptance/apply_v27_patch_remote.sh"),
        (PATCH_ROOT / "acceptance" / "repair_v27_root_create_remote.sh",
         "acceptance/repair_v27_root_create_remote.sh"),
        (PATCH_ROOT / "acceptance" / "repair_v27_zvol_blocksize_remote.sh",
         "acceptance/repair_v27_zvol_blocksize_remote.sh"),
    )
    with tarfile.open(bundle, "w:gz") as archive:
        for source, arcname in members:
            if not source.is_file():
                raise RemoteError(f"v27 patch bundle input is missing: {source}")
            archive.add(source, arcname=arcname)
    bundle.chmod(0o600)
    digest = hashlib.sha256(bundle.read_bytes()).hexdigest()
    return bundle, digest


def bundle_from_environment() -> tuple[Path, str]:
    path_value = os.environ.get("TRUENAS_V27_PATCH_BUNDLE", "")
    expected_digest = os.environ.get("TRUENAS_V27_PATCH_BUNDLE_SHA256", "")
    if not path_value or not expected_digest:
        raise RemoteError("the registered live-gate wrapper must provide the v27 patch bundle and digest")
    if len(expected_digest) != 64 or any(char not in "0123456789abcdef" for char in expected_digest):
        raise RemoteError("TRUENAS_V27_PATCH_BUNDLE_SHA256 must be a lowercase SHA-256 digest")
    bundle_input = Path(path_value)
    if bundle_input.is_symlink():
        raise RemoteError(f"v27 patch bundle must not be a symlink: {bundle_input}")
    bundle = bundle_input.resolve(strict=True)
    artifacts = (PATCH_ROOT / "artifacts").resolve(strict=True)
    try:
        bundle.relative_to(artifacts)
    except ValueError as exc:
        raise RemoteError(f"v27 patch bundle must be preserved below {artifacts}") from exc
    if not bundle.is_file():
        raise RemoteError(f"v27 patch bundle is not a regular file: {bundle}")
    actual_digest = hashlib.sha256(bundle.read_bytes()).hexdigest()
    if actual_digest != expected_digest:
        raise RemoteError(
            f"v27 patch bundle digest differs from the host-prepared digest: {actual_digest}"
        )
    return bundle, actual_digest


def prior_bundle_from_environment() -> tuple[Path, str]:
    path_value = os.environ.get("TRUENAS_V27_PRIOR_PATCH_BUNDLE", "")
    expected_digest = os.environ.get("TRUENAS_V27_EXPECTED_PRIOR_BUNDLE_SHA256", "")
    if not path_value or not expected_digest:
        raise RemoteError("guarded root-create repair requires the prior v27 bundle and its exact SHA-256")
    if len(expected_digest) != 64 or any(char not in "0123456789abcdef" for char in expected_digest):
        raise RemoteError("TRUENAS_V27_EXPECTED_PRIOR_BUNDLE_SHA256 must be a lowercase SHA-256 digest")
    bundle_input = Path(path_value)
    if bundle_input.is_symlink():
        raise RemoteError(f"prior v27 patch bundle must not be a symlink: {bundle_input}")
    bundle = bundle_input.resolve(strict=True)
    artifacts = (PATCH_ROOT / "artifacts").resolve(strict=True)
    try:
        bundle.relative_to(artifacts)
    except ValueError as exc:
        raise RemoteError(f"prior v27 patch bundle must be preserved below {artifacts}") from exc
    if not bundle.is_file():
        raise RemoteError(f"prior v27 patch bundle is not a regular file: {bundle}")
    actual_digest = hashlib.sha256(bundle.read_bytes()).hexdigest()
    if actual_digest != expected_digest:
        raise RemoteError(f"prior v27 patch bundle digest differs from the expected digest: {actual_digest}")
    return bundle, actual_digest


def require_root_create_repair_target(target: Target, profile: str) -> None:
    require(profile == "path-a", "the guarded v27 root-create repair only supports a fresh Path A target")
    info = target.call("system.info", timeout=120)
    version = str(info.get("version", ""))
    require(version == EXPECTED_VERSION, f"expected {EXPECTED_VERSION}, got {version!r}")
    require(target.shell("test ! -e /dev/kvm", timeout=30) == "",
            "v27 root-create repair requires the registered TCG target")
    record_text = target.shell("cat /root/v27-boot-pool-patch-install.json", timeout=30)
    try:
        record = json.loads(record_text)
    except ValueError as exc:
        raise RemoteError("the existing v27 install record is not valid JSON") from exc
    require(record == {
        "version": EXPECTED_VERSION,
        "patch": "v27",
        "supported": True,
        "kvm_device": False,
    }, f"root-create repair only supports the prior registered fresh-install record: {record!r}")
    require_root_repair_storage_empty(target, "root-create repair")


def require_root_parent_path_repair_target(
    target: Target,
    profile: str,
    prior_bundle_digest: str,
    prior_dataset_digest: str,
) -> None:
    require(profile == "path-a", "the guarded v27 parent-mountpoint repair only supports Path A")
    info = target.call("system.info", timeout=120)
    version = str(info.get("version", ""))
    require(version == EXPECTED_VERSION, f"expected {EXPECTED_VERSION}, got {version!r}")
    require(target.shell("test ! -e /dev/kvm", timeout=30) == "",
            "v27 parent-mountpoint repair requires the registered TCG target")
    record_text = target.shell("cat /root/v27-boot-pool-patch-install.json", timeout=30)
    try:
        record = json.loads(record_text)
    except ValueError as exc:
        raise RemoteError("the existing v27 install record is not valid JSON") from exc
    require(record == {
        "version": EXPECTED_VERSION,
        "patch": "v27",
        "supported": True,
        "kvm_device": False,
        "bundle_sha256": prior_bundle_digest,
        "root_create_guard": "root-excluded-from-descendant-readiness-v1",
        "repaired_from_bundle_sha256": "61331d7a4f2afd02fbe4e91e13436db70c2028639ba8d7b99a5659a2e9974b7e",
        "repaired_dataset_py_sha256": prior_dataset_digest,
    }, f"parent-mountpoint repair only supports the exact clean root-create repair record: {record!r}")
    source_digest = target.shell(
        "sha256sum /usr/lib/python3/dist-packages/middlewared/plugins/pool_/dataset.py | cut -d ' ' -f1",
        timeout=45,
    )
    require(source_digest == prior_dataset_digest,
            f"installed dataset.py differs from the selected prior repair source: {source_digest}")
    require_root_repair_storage_empty(target, "parent-mountpoint repair")


def require_root_repair_storage_empty(target: Target, label: str) -> None:
    require(target.shell("! zfs list -H -o name boot-pool/user-data >/dev/null 2>&1", timeout=30) == "",
            f"boot-pool/user-data already exists; refusing {label} after storage setup")
    require(target.shell("test ! -e /mnt/boot-pool/user-data", timeout=30) == "",
            f"the Path A user-data mountpoint already exists; refusing {label}")
    require(target.shell("! zpool list -H -o name | grep -Fxq boot-disk-data", timeout=30) == "",
            f"boot-disk-data already exists; refusing {label} after storage setup")
    pool_rows = target.shell("zpool list -H -o name,health", timeout=90).splitlines()
    pools = {line.split("\t", 1)[0]: line.split("\t", 1)[1]
             for line in pool_rows if "\t" in line}
    require(pools.get("boot-pool") == "ONLINE", f"boot-pool is not ONLINE: {pools!r}")
    containers = target.query("container.query", timeout=120)
    vms = target.query("vm.query", timeout=120)
    require(containers == [], f"existing container records prevent {label}: {containers!r}")
    require(vms == [], f"existing VM records prevent {label}: {vms!r}")
    report("readonly-inventory", f"clean Path A v27 target confirmed for {label}; user-data root, containers, VMs, and Path B pool are absent")


def require_zvol_blocksize_repair_target(
    target: Target,
    profile: str,
    prior_bundle_digest: str,
    prior_dataset_info_digest: str,
) -> None:
    require(profile == "path-a", "the guarded v27 zvol-form repair only supports Path A")
    info = target.call("system.info", timeout=120)
    version = str(info.get("version", ""))
    require(version == EXPECTED_VERSION, f"expected {EXPECTED_VERSION}, got {version!r}")
    require(target.shell("test ! -e /dev/kvm", timeout=30) == "",
            "v27 zvol-form repair requires the registered TCG target")
    record_text = target.shell("cat /root/v27-boot-pool-patch-install.json", timeout=30)
    try:
        record = json.loads(record_text)
    except ValueError as exc:
        raise RemoteError("the existing v27 install record is not valid JSON") from exc
    require(record == {
        "version": EXPECTED_VERSION,
        "patch": "v27",
        "supported": True,
        "kvm_device": False,
        "bundle_sha256": prior_bundle_digest,
        "root_create_guard": "root-excluded-from-descendant-readiness-v1",
        "repaired_from_bundle_sha256": "61331d7a4f2afd02fbe4e91e13436db70c2028639ba8d7b99a5659a2e9974b7e",
        "repaired_dataset_py_sha256": "1cd5b385da5175980c96cdf1697888eb8cccd580bf97de935d7cfb889f021232",
        "root_parent_mountpoint_guard": "root-create-skips-parent-mountpoint-none-v1",
        "parent_mountpoint_repaired_from_bundle_sha256": "4c72faac25ce748b197d118e0b036642518bf44d23d06e6f24ac738b3ed8bd05",
        "parent_mountpoint_repaired_dataset_py_sha256": "82e997cefaaac4e20623c11808d8b30ef3b2372fe2b11eeb86bebc5a785115cf",
    }, f"zvol-form repair only supports the exact current r4 install record: {record!r}")
    source_digest = target.shell(
        "sha256sum /usr/lib/python3/dist-packages/middlewared/plugins/pool_/dataset_info.py | cut -d ' ' -f1",
        timeout=45,
    )
    require(source_digest == prior_dataset_info_digest,
            f"installed dataset_info.py differs from the read-only inventory digest: {source_digest}")
    pool_rows = target.shell("zpool list -H -o name,health", timeout=90).splitlines()
    pools = {line.split("\t", 1)[0]: line.split("\t", 1)[1]
             for line in pool_rows if "\t" in line}
    require(pools.get("boot-pool") == "ONLINE",
            f"boot-pool is not ONLINE: {pools!r}")
    require("boot-disk-data" not in pools,
            "boot-disk-data exists; refusing the Path A zvol-form repair")
    root_properties = target.shell(
        "zfs get -H -p -o value quota,mountpoint,canmount boot-pool/user-data",
        timeout=45,
    ).splitlines()
    require(root_properties == ["2147483648", "/mnt/boot-pool/user-data", "on"],
            f"the UI-created user-data root has unexpected properties: {root_properties!r}")
    require(target.shell(
        "findmnt -n -o SOURCE --target /mnt/boot-pool/user-data",
        timeout=30,
    ) == "boot-pool/user-data", "the UI-created user-data root is not mounted from its ZFS dataset")
    descendants = target.shell(
        "zfs list -H -r -o name boot-pool/user-data",
        timeout=45,
    ).splitlines()
    require(descendants == ["boot-pool/user-data"],
            f"the UI-created root has descendants; refusing repair: {descendants!r}")
    files = target.shell(
        "find /mnt/boot-pool/user-data -mindepth 1 -print -quit",
        timeout=30,
    )
    require(not files, f"the UI-created root contains data; refusing repair: {files!r}")
    all_snapshots = target.shell(
        "zfs list -H -t snapshot -o name",
        timeout=90,
    ).splitlines()
    snapshots = [name for name in all_snapshots if name.startswith("boot-pool/user-data@")]
    require(not snapshots, f"the UI-created root has snapshots; refusing repair: {snapshots!r}")
    containers = target.query("container.query", timeout=120)
    vms = target.query("vm.query", timeout=120)
    require(containers == [], f"existing container records prevent repair: {containers!r}")
    require(vms == [], f"existing VM records prevent repair: {vms!r}")
    report(
        "readonly-inventory",
        "exact r4 install record, original dataset_info.py digest, and empty UI-created "
        "Path A root confirmed before zvol-form repair; boot-pool ONLINE, no descendants, "
        "snapshots, containers, VMs, or Path B pool",
    )


def scp_bundle(target: Target, bundle: Path, remote_path: str) -> None:
    argv = [
        "scp",
        "-P", str(target.port),
        "-i", str(target.key),
        "-o", "BatchMode=yes",
        "-o", "IdentitiesOnly=yes",
        "-o", "StrictHostKeyChecking=yes",
        "-o", f"UserKnownHostsFile={target.known_hosts}",
        "-o", "ConnectTimeout=10",
        str(bundle),
        f"{target.user}@{target.host}:{remote_path}",
    ]
    try:
        result = subprocess.run(argv, text=True, capture_output=True, timeout=120, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RemoteError(f"could not stage v27 patch bundle with scp: {exc}") from exc
    if result.returncode:
        detail = (result.stderr or result.stdout).strip()[-1800:]
        raise RemoteError(f"scp failed with exit {result.returncode}: {detail}")


def main() -> int:
    if os.environ.get("TRUENAS_TEST_ALLOW_MUTATIONS") != "1":
        raise RemoteError("set TRUENAS_TEST_ALLOW_MUTATIONS=1 only for a disposable TrueNAS VM")
    profile = os.environ.get("TRUENAS_STORAGE_PROFILE", "")
    if profile not in {"path-a", "path-b"}:
        raise RemoteError("TRUENAS_STORAGE_PROFILE must be path-a or path-b")
    version = os.environ.get("TRUENAS_EXPECTED_VERSION", "")
    require(version == EXPECTED_VERSION, f"TRUENAS_EXPECTED_VERSION must be {EXPECTED_VERSION}")

    target = Target.from_environment()
    action = os.environ.get("TRUENAS_V27_PATCH_ACTION", "install")
    require(action in {
        "install", "repair-root-create", "repair-root-parent-mountpoint",
        "repair-zvol-blocksize",
    },
            "TRUENAS_V27_PATCH_ACTION is not a supported v27 patch action")
    bundle, digest = bundle_from_environment()
    remote_dir = f"/tmp/truenas-v27-patch-{uuid.uuid4().hex}"
    target.run(["mkdir", "-m", "0700", remote_dir], timeout=30)
    remote_bundle = f"{remote_dir}/bundle.tar.gz"
    report("stage", f"uploading fingerprinted v27 adapter bundle sha256={digest}")
    scp_bundle(target, bundle, remote_bundle)
    if action == "install":
        require(not os.environ.get("TRUENAS_TEST_ALLOW_PATCH_REPAIR"),
                "TRUENAS_TEST_ALLOW_PATCH_REPAIR is only valid for a guarded repair")
        require(not os.environ.get("TRUENAS_V27_PRIOR_PATCH_BUNDLE"),
                "TRUENAS_V27_PRIOR_PATCH_BUNDLE is only valid for a guarded repair")
        require(not os.environ.get("TRUENAS_V27_EXPECTED_PRIOR_BUNDLE_SHA256"),
                "TRUENAS_V27_EXPECTED_PRIOR_BUNDLE_SHA256 is only valid for a guarded repair")
        require(not os.environ.get("TRUENAS_V27_EXPECTED_PRIOR_DATASET_SHA256"),
                "TRUENAS_V27_EXPECTED_PRIOR_DATASET_SHA256 is only valid for parent-mountpoint repair")
        require(not os.environ.get("TRUENAS_V27_EXPECTED_PRIOR_DATASET_INFO_SHA256"),
                "TRUENAS_V27_EXPECTED_PRIOR_DATASET_INFO_SHA256 is only valid for zvol blocksize repair")
        require_fresh_unpatched_target(target, profile)
        remote_script = (
            "set -euo pipefail; "
            f"tar -xzf {shlex.quote(remote_bundle)} -C {shlex.quote(remote_dir)}; "
            f"bash {shlex.quote(remote_dir + '/acceptance/apply_v27_patch_remote.sh')} "
            f"{shlex.quote(remote_dir)}"
        )
    elif action in {
        "repair-root-create", "repair-root-parent-mountpoint", "repair-zvol-blocksize",
    }:
        require(os.environ.get("TRUENAS_TEST_ALLOW_PATCH_REPAIR") == "1",
                "set TRUENAS_TEST_ALLOW_PATCH_REPAIR=1 only for this disposable guest repair")
        prior_bundle, prior_digest = prior_bundle_from_environment()
        prior_dataset_digest = ""
        if action == "repair-root-create":
            require(not os.environ.get("TRUENAS_V27_EXPECTED_PRIOR_DATASET_SHA256"),
                    "TRUENAS_V27_EXPECTED_PRIOR_DATASET_SHA256 is only valid for parent-mountpoint repair")
            require_root_create_repair_target(target, profile)
            repair_stage = "root-create"
        elif action == "repair-root-parent-mountpoint":
            prior_dataset_digest = os.environ.get("TRUENAS_V27_EXPECTED_PRIOR_DATASET_SHA256", "")
            require(
                len(prior_dataset_digest) == 64
                and all(char in "0123456789abcdef" for char in prior_dataset_digest),
                "parent-mountpoint repair requires the direct inventory's dataset.py SHA-256",
            )
            require_root_parent_path_repair_target(
                target, profile, prior_digest, prior_dataset_digest
            )
            repair_stage = "parent-mountpoint"
            prior_dataset_info_digest = ""
        else:
            prior_dataset_info_digest = os.environ.get(
                "TRUENAS_V27_EXPECTED_PRIOR_DATASET_INFO_SHA256", ""
            )
            require(
                len(prior_dataset_info_digest) == 64
                and all(char in "0123456789abcdef" for char in prior_dataset_info_digest),
                "zvol blocksize repair requires the direct inventory's dataset_info.py SHA-256",
            )
            require(not os.environ.get("TRUENAS_V27_EXPECTED_PRIOR_DATASET_SHA256"),
                    "TRUENAS_V27_EXPECTED_PRIOR_DATASET_SHA256 is only valid for parent-mountpoint repair")
            require_zvol_blocksize_repair_target(
                target, profile, prior_digest, prior_dataset_info_digest
            )
            repair_stage = "zvol-blocksize"
        prior_dir = f"{remote_dir}/prior"
        target.run(["mkdir", "-m", "0700", prior_dir], timeout=30)
        remote_prior_bundle = f"{prior_dir}/bundle.tar.gz"
        report("stage", f"uploading verified prior v27 bundle sha256={prior_digest}")
        scp_bundle(target, prior_bundle, remote_prior_bundle)
        if action == "repair-zvol-blocksize":
            remote_script = (
                "set -euo pipefail; "
                f"tar -xzf {shlex.quote(remote_bundle)} -C {shlex.quote(remote_dir)}; "
                f"bash {shlex.quote(remote_dir + '/acceptance/repair_v27_zvol_blocksize_remote.sh')} "
                f"{shlex.quote(EXPECTED_VERSION)} {shlex.quote(prior_digest)} "
                f"{shlex.quote(digest)} {shlex.quote(profile)} {shlex.quote(remote_dir)} "
                f"{shlex.quote(prior_dataset_info_digest)}"
            )
        else:
            remote_script = (
                "set -euo pipefail; "
                f"tar -xzf {shlex.quote(remote_bundle)} -C {shlex.quote(remote_dir)}; "
                f"bash {shlex.quote(remote_dir + '/acceptance/repair_v27_root_create_remote.sh')} "
                f"{shlex.quote(EXPECTED_VERSION)} {shlex.quote(prior_digest)} "
                f"{shlex.quote(digest)} {shlex.quote(profile)} {shlex.quote(remote_dir)} "
                f"{shlex.quote(repair_stage)} {shlex.quote(prior_dataset_digest)}"
            )
    else:
        raise RemoteError("TRUENAS_V27_PATCH_ACTION is not a supported v27 patch action")
    output = target.run(["bash", "-lc", remote_script], timeout=1800)
    for line in output.splitlines():
        print(line, flush=True)

    record = target.call("vm.virtualization_details", timeout=120)
    require(record.get("supported") is True, f"patched TCG backend is not supported: {record!r}")
    require(target.shell("test ! -e /dev/kvm", timeout=30) == "",
            "/dev/kvm appeared after patch installation")
    if action == "repair-zvol-blocksize":
        require(target.shell(
            "zfs get -H -p -o value quota,mountpoint,canmount boot-pool/user-data",
            timeout=45,
        ).splitlines() == ["2147483648", "/mnt/boot-pool/user-data", "on"],
            "zvol blocksize repair changed the UI-created Path A root properties")
        require(target.shell(
            "findmnt -n -o SOURCE --target /mnt/boot-pool/user-data", timeout=30
        ) == "boot-pool/user-data", "zvol blocksize repair changed the UI-created root mount")
    else:
        require(target.shell("test ! -e /mnt/boot-pool/user-data", timeout=30) == "",
                "patch install unexpectedly created the Path A user-data namespace")
    try:
        install_record = json.loads(
            target.shell("cat /root/v27-boot-pool-patch-install.json", timeout=30)
        )
    except ValueError as exc:
        raise RemoteError("v27 install record is not valid JSON") from exc
    require(
        install_record.get("bundle_sha256") == digest
        and install_record.get("root_create_guard") == "root-excluded-from-descendant-readiness-v1",
        f"v27 patch marker does not match the bundle with the root-create guard: {install_record!r}",
    )
    require(
        install_record.get("root_parent_mountpoint_guard")
        == "root-create-skips-parent-mountpoint-none-v1",
        f"v27 patch marker does not bind the parent mountpoint=none fix: {install_record!r}",
    )
    if action == "repair-root-create":
        require(
            install_record.get("repaired_from_bundle_sha256")
            == os.environ.get("TRUENAS_V27_EXPECTED_PRIOR_BUNDLE_SHA256"),
            f"root-create repair marker does not bind to the selected prior bundle: {install_record!r}",
        )
    elif action == "repair-root-parent-mountpoint":
        source_digest = target.shell(
            "sha256sum /usr/lib/python3/dist-packages/middlewared/plugins/pool_/dataset.py | cut -d ' ' -f1",
            timeout=45,
        )
        require(
            install_record.get("parent_mountpoint_repaired_from_bundle_sha256")
            == os.environ.get("TRUENAS_V27_EXPECTED_PRIOR_BUNDLE_SHA256")
            and install_record.get("parent_mountpoint_repaired_dataset_py_sha256") == source_digest,
            f"parent-mountpoint repair marker does not bind to the selected prior bundle and installed source: {install_record!r}",
        )
    elif action == "install":
        require(
            install_record.get("recommended_zvol_blocksize_boot_pool")
            == "boot-pool-defaults-to-16k-v1",
            f"v27 patch marker does not bind the boot-pool zvol recommendation: {install_record!r}",
        )
    elif action == "repair-zvol-blocksize":
        source_digest = target.shell(
            "sha256sum /usr/lib/python3/dist-packages/middlewared/plugins/pool_/dataset_info.py | cut -d ' ' -f1",
            timeout=45,
        )
        require(
            install_record.get("recommended_zvol_blocksize_boot_pool")
            == "boot-pool-defaults-to-16k-v1"
            and install_record.get("zvol_blocksize_repaired_from_bundle_sha256")
            == os.environ.get("TRUENAS_V27_EXPECTED_PRIOR_BUNDLE_SHA256")
            and install_record.get("zvol_blocksize_repaired_dataset_info_py_sha256") == source_digest,
            f"zvol blocksize repair marker does not bind the prior bundle and installed source: {install_record!r}",
        )
    pool_rows = target.shell("zpool list -H -o name,health", timeout=90).splitlines()
    pool_health = {line.split("\t", 1)[0]: line.split("\t", 1)[1]
                   for line in pool_rows if "\t" in line}
    require("boot-disk-data" not in pool_health,
            "patch install unexpectedly created Path B storage")
    storage_state = (
        "the verified UI-created Path A root remains configured"
        if action == "repair-zvol-blocksize"
        else "Path A/B storage remains unconfigured"
    )
    report(
        "result",
        f"PASS TrueNAS {EXPECTED_VERSION} Path {profile[-1:].upper()} has the v27 relaxation patch; "
        f"{storage_state}",
    )
    report("bundle", f"{bundle.name} sha256={digest}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RemoteError, OSError, ValueError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr, flush=True)
        raise SystemExit(1)
