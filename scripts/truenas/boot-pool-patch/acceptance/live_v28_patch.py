#!/usr/bin/env python3
"""Apply and verify the exact v28 nightly adapter on a disposable guest.

The guest-side transaction refuses unknown source files, keeps a rollback
archive on the selected persistent storage path, restores /usr read-only, and
verifies the middleware API before recording an idempotency manifest.
"""

from __future__ import annotations

import base64
import gzip
import hashlib
import io
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import sys
import tarfile
import uuid

from remote import RemoteError, Target, flatten_datasets


EXPECTED_VERSION = "28.0.0-MASTER+20261029-175210"
SCRIPT_DIR = Path(__file__).resolve().parent
TRUENAS_ROOT = SCRIPT_DIR.parents[1]
PATCH_ROOT = TRUENAS_ROOT / "boot-pool-patch"
ADAPTERS = {
    EXPECTED_VERSION: PATCH_ROOT / "v28-nightly-20261029",
}
TARGET_COUNTS = {EXPECTED_VERSION: 19}
PATCH_DIR = ADAPTERS[EXPECTED_VERSION]
SHARED_DIR = TRUENAS_ROOT / "boot-pool-patch" / "shared"
STORAGE_ROOTS = {
    "path-a": ("boot-pool/user-data", "/mnt/boot-pool/user-data"),
    "path-b": ("boot-disk-data", "/mnt/boot-disk-data"),
}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RemoteError(message)


def report(stage: str, detail: str) -> None:
    print(f"[{stage}] {detail}", flush=True)


def adapter_directory(version: str) -> Path:
    try:
        return ADAPTERS[version]
    except KeyError as exc:
        raise RemoteError(
            f"no v28 adapter is registered for {version!r}; the rolling v28 base is "
            f"{EXPECTED_VERSION}. Update through TrueNAS to that exact build, verify the "
            "reported version, then rerun the patch workflow. If the updater advertises "
            "a newer build, stop until its adapter is registered."
        ) from exc


def read_target_hashes(
    path: Path | None = None, *, version: str = EXPECTED_VERSION,
) -> dict[str, str]:
    manifest_path = path or adapter_directory(version) / "targets.sha256"
    try:
        lines = manifest_path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise RemoteError(f"cannot read v28 target fingerprints {manifest_path}: {exc}") from exc
    result: dict[str, str] = {}
    for line_number, line in enumerate(lines, 1):
        fields = line.split(maxsplit=1)
        if len(fields) != 2 or not re.fullmatch(r"[0-9a-f]{64}", fields[0]):
            raise RemoteError(f"invalid v28 target fingerprint at {manifest_path}:{line_number}")
        target = PurePosixPath(fields[1].strip())
        if (target.is_absolute() or ".." in target.parts or not target.parts
                or target.parts[0] not in {"middlewared", "truenas_pylibvirt"}):
            raise RemoteError(f"unsafe v28 target path at {manifest_path}:{line_number}: {target}")
        if str(target) in result:
            raise RemoteError(f"duplicate v28 target path at {manifest_path}:{line_number}: {target}")
        result[str(target)] = fields[0]
    expected_count = TARGET_COUNTS[version]
    if len(result) != expected_count:
        raise RemoteError(f"expected {expected_count} v28 target fingerprints for {version}, found {len(result)}")
    return result


def require_guests_stopped(
    rows: list[dict[str, object]],
    label: str,
    status_lookup=None,
) -> None:
    """Require an explicit STOPPED state; resolve nullable VM status directly."""
    for row in rows:
        if not isinstance(row, dict):
            raise RemoteError(f"cannot verify {label} runtime state from malformed query row: {row!r}")
        identifier = row.get("name", row.get("id"))
        status = row.get("status")
        if status_lookup is not None and (
            not isinstance(status, dict) or not isinstance(status.get("state"), str)
        ):
            vm_id = row.get("id")
            if isinstance(vm_id, bool) or not isinstance(vm_id, int) or vm_id <= 0:
                raise RemoteError(f"cannot resolve nullable {label} status without a valid id: {row!r}")
            status = status_lookup(vm_id)
        if not isinstance(status, dict):
            raise RemoteError(f"cannot determine {label} {identifier!r} runtime state; status is null or malformed")
        state = status.get("state")
        if not isinstance(state, str) or not state.strip():
            raise RemoteError(f"cannot determine {label} {identifier!r} runtime state: {status!r}")
        if state.strip().upper() != "STOPPED":
            raise RemoteError(f"refusing to restart middleware while {label} {identifier!r} is {state!r}")


def deterministic_bundle(
    version: str = EXPECTED_VERSION,
) -> tuple[bytes, str, dict[str, str]]:
    """Create a stable bundle with closed member names and normalized metadata."""
    patch_dir = adapter_directory(version)
    from patch_drift import get_supported_build

    build = get_supported_build(version)
    series_root = build["series_root"]
    target_hashes = read_target_hashes(version=version)
    paths_content = "".join(f"{name}\n" for name in target_hashes).encode()
    # The sole supported v28 base carries its release-specific installer and
    # Path B partition adapter in this directory.
    versioned = True
    bundle_files = [
        *[
            (f"patch/{build['series_dir']}/{name}", series_root / name)
            for name in build["patches"]
        ],
        ("patch/targets.sha256", patch_dir / "targets.sha256"),
        ("patch/adapter.json", patch_dir / "adapter.json"),
        ("patch/boot_pool_mount.py", patch_dir / "boot_pool_mount.py"),
        (
            "patch/apply_v28_patch_remote.sh",
            patch_dir / "apply_v28_patch_remote.sh" if versioned else SCRIPT_DIR / "apply_v28_patch_remote.sh",
        ),
        (
            "patch/replace_v28_patch_remote.sh",
            patch_dir / "replace_v28_patch_remote.sh" if versioned else SCRIPT_DIR / "replace_v28_patch_remote.sh",
        ),
        ("shared/boot_pool_user_data.py", SHARED_DIR / "boot_pool_user_data.py"),
        ("shared/virtualization_support.py", SHARED_DIR / "virtualization_support.py"),
    ]
    if version == EXPECTED_VERSION:
        bundle_files.extend((
            ("shared/boot_disk_partition.py", patch_dir / "boot_disk_partition.py"),
            ("shared/pool_create_schema_v28.json", patch_dir / "pool_create_schema_v28.json"),
        ))
    raw = io.BytesIO()
    with gzip.GzipFile(fileobj=raw, mode="wb", filename="", mtime=0) as compressed:
        with tarfile.open(fileobj=compressed, mode="w", format=tarfile.USTAR_FORMAT) as archive:
            for archive_name, source in bundle_files:
                if not source.is_file() or source.is_symlink():
                    raise RemoteError(f"v28 adapter bundle member is missing or not a regular file: {source}")
                info = tarfile.TarInfo(archive_name)
                info.size = source.stat().st_size
                info.mode = 0o644
                info.uid = info.gid = 0
                info.uname = info.gname = ""
                info.mtime = 0
                with source.open("rb") as data:
                    archive.addfile(info, data)
            info = tarfile.TarInfo("patch/targets.list")
            info.size = len(paths_content)
            info.mode = 0o644
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            info.mtime = 0
            archive.addfile(info, io.BytesIO(paths_content))
    bundle = raw.getvalue()
    digest = hashlib.sha256(bundle).hexdigest()
    expected_digest = build.get("install_bundle_sha256")
    require(
        isinstance(expected_digest, str) and digest == expected_digest,
        f"v28 patch bundle digest differs from the exact-build registry for {version}: "
        f"expected={expected_digest!r} actual={digest}",
    )
    return bundle, digest, target_hashes


def bundle_transfer_command(stage: str, encoded: str, bundle_sha256: str) -> str:
    """Build a quoted transfer command whose checksum record ends in a newline."""
    if not re.fullmatch(r"[0-9a-f]{64}", bundle_sha256):
        raise RemoteError("bundle transfer requires a lowercase SHA-256 digest")
    archive_path = stage + "/bundle.tar.gz"
    return (
        "umask 077 && "
        f"mkdir -m 0700 {shlex.quote(stage)} && "
        f"printf %s {shlex.quote(encoded)} | base64 -d > {shlex.quote(archive_path)} && "
        f"printf '%s  %s\\n' {shlex.quote(bundle_sha256)} {shlex.quote(archive_path)} | sha256sum --check - && "
        f"tar -xzf {shlex.quote(archive_path)} --no-same-owner -C {shlex.quote(stage)}"
    )


def storage_root_for_profile(profile: str) -> tuple[str, str]:
    try:
        return STORAGE_ROOTS[profile]
    except KeyError as exc:
        raise RemoteError(f"TRUENAS_STORAGE_PROFILE must be path-a or path-b, got {profile!r}") from exc


def check_path_b_pool(target: Target) -> None:
    pools = target.query("pool.query", timeout=120)
    matches = [row for row in pools if isinstance(row, dict) and row.get("name") == "boot-disk-data"]
    require(len(matches) == 1, f"Path B must have exactly one boot-disk-data pool, found {matches!r}")
    pool = matches[0]
    require(pool.get("status") == "ONLINE", f"boot-disk-data is not ONLINE: {pool!r}")
    data_vdevs = (pool.get("topology") or {}).get("data")
    require(isinstance(data_vdevs, list) and len(data_vdevs) == 1,
            f"Path B requires one boot-disk-data data vdev: {data_vdevs!r}")
    vdev = data_vdevs[0]
    require(
        isinstance(vdev, dict) and vdev.get("path") == "/dev/vda4" and vdev.get("status") == "ONLINE",
        f"Path B boot-disk-data must be ONLINE on /dev/vda4: {vdev!r}",
    )
    boot_status = target.shell("zpool status -P boot-pool")
    require("/dev/vda3" in boot_status and "state: ONLINE" in boot_status,
            f"Path B requires boot-pool ONLINE on /dev/vda3: {boot_status!r}")


def check_target_preconditions(target: Target, version: str, profile: str) -> None:
    dataset, mountpoint = storage_root_for_profile(profile)
    info = target.call("system.info", timeout=120)
    actual = str(info.get("version", ""))
    require(actual == version, f"expected TrueNAS {version}, got {actual!r}")
    adapter_directory(version)
    require(target.shell("test ! -e /dev/kvm && printf absent") == "absent",
            "this v28 acceptance needs /dev/kvm absent so the TCG branch is exercised")
    require(target.shell("systemctl is-active --quiet middlewared.service && printf active") == "active",
            "middlewared must be active before patching")
    if profile == "path-a":
        # Path A uses the mounted boot-pool user-data root; a separate reserved
        # partition may coexist on the same disk without affecting this profile.
        target.shell(
            "test \"$(zfs get -H -o value mountpoint boot-pool/user-data)\" = "
            f"{shlex.quote(mountpoint)} && "
            "if [ \"$(zfs get -H -o value mounted boot-pool/user-data)\" != yes ]; then "
            "zfs mount boot-pool/user-data; fi && mountpoint -q "
            f"{shlex.quote(mountpoint)}",
            timeout=90,
        )
    else:
        require(target.shell("test -b /dev/vda4 && printf present") == "present",
                "Path B requires its reserved partition 4 block device")
        check_path_b_pool(target)
        require(target.shell(
            "test \"$(zfs get -H -o value mountpoint boot-disk-data)\" = "
            f"{shlex.quote(mountpoint)} && "
            "test \"$(zfs get -H -o value mounted boot-disk-data)\" = yes && "
            f"mountpoint -q {shlex.quote(mountpoint)} && printf mounted",
            timeout=90,
        ) == "mounted", "Path B boot-disk-data must already be mounted at its canonical path")
    if profile == "path-b":
        datasets = flatten_datasets(target.query(
            "pool.dataset.query", [["id", "=", dataset]], timeout=120,
        ))
        require(dataset in datasets, f"{profile} persistent backup root is not queryable: {datasets!r}")
    statfs = target.call("filesystem.statfs", mountpoint, timeout=90)
    require(
        statfs.get("source") == dataset and statfs.get("dest") == mountpoint,
        f"{profile} persistent backup mount is unavailable: {statfs!r}",
    )
    for method, label in (("vm.query", "VM"), ("container.query", "container")):
        rows = target.query(method, timeout=120)
        status_lookup = (lambda vm_id: target.call("vm.status", vm_id, timeout=60)) if method == "vm.query" else None
        require_guests_stopped(rows, label, status_lookup)


def main() -> int:
    if os.environ.get("TRUENAS_TEST_ALLOW_MUTATIONS") != "1":
        raise RemoteError("set TRUENAS_TEST_ALLOW_MUTATIONS=1 only for a disposable TrueNAS VM")
    profile = os.environ.get("TRUENAS_STORAGE_PROFILE", "")
    storage_root_for_profile(profile)
    expected_version = os.environ.get("TRUENAS_EXPECTED_VERSION", "")
    adapter_directory(expected_version)
    action = os.environ.get("TRUENAS_V28_PATCH_ACTION", "install")
    replace_guard = os.environ.get("TRUENAS_TEST_ALLOW_PATCH_REPLACE", "")
    if action not in {"install", "replace"}:
        raise RemoteError("TRUENAS_V28_PATCH_ACTION must be install or replace")
    if action == "replace" and replace_guard != "1":
        raise RemoteError("set TRUENAS_TEST_ALLOW_PATCH_REPLACE=1 only to replace the registered v28 adapter")
    if action == "install" and replace_guard:
        raise RemoteError("TRUENAS_TEST_ALLOW_PATCH_REPLACE is only valid with TRUENAS_V28_PATCH_ACTION=replace")

    prior_bundle_sha256 = os.environ.get("TRUENAS_V28_EXPECTED_PRIOR_BUNDLE_SHA256", "")
    if action == "replace":
        if not re.fullmatch(r"[0-9a-f]{64}", prior_bundle_sha256):
            raise RemoteError(
                "replace requires TRUENAS_V28_EXPECTED_PRIOR_BUNDLE_SHA256 from the installed patch record"
            )
    elif prior_bundle_sha256:
        raise RemoteError("TRUENAS_V28_EXPECTED_PRIOR_BUNDLE_SHA256 is only valid with replace")

    target = Target.from_environment()
    check_target_preconditions(target, expected_version, profile)
    bundle, bundle_sha256, target_hashes = deterministic_bundle(expected_version)
    stage = f"/tmp/truenas-v28-boot-pool-patch-{uuid.uuid4().hex}"
    encoded = base64.b64encode(bundle).decode("ascii")
    transfer = bundle_transfer_command(stage, encoded, bundle_sha256)
    target.shell(transfer, timeout=120)
    report(
        "bundle",
        f"staged deterministic v28 adapter bundle ({len(bundle)} bytes, SHA-256 {bundle_sha256}); "
        f"fingerprinted {len(target_hashes)} source files",
    )
    remote_script = (
        "replace_v28_patch_remote.sh" if action == "replace"
        else "apply_v28_patch_remote.sh"
    )
    remote_command = (
        f"bash {shlex.quote(stage + '/patch/' + remote_script)} "
        f"{shlex.quote(expected_version)} {shlex.quote(bundle_sha256)} "
        f"{shlex.quote(stage)} {shlex.quote(profile)}"
    )
    if action == "replace":
        remote_command += f" {shlex.quote(prior_bundle_sha256)}"
    output = target.shell(remote_command, timeout=900)
    for line in output.splitlines():
        if line.strip():
            report("guest", line.strip())

    info = target.call("system.info", timeout=180)
    require(str(info.get("version", "")) == expected_version,
            f"middleware reports unexpected version after patch: {info.get('version')!r}")
    details = target.call("vm.virtualization_details", timeout=180)
    require(details.get("supported") is True,
            f"v28 patch did not advertise TCG virtualization: {details!r}")
    flags = target.call("vm.flags", timeout=120)
    require(not any(flags.values()), f"v28 TCG patch changed hardware flags: {flags!r}")
    dataset, mountpoint = storage_root_for_profile(profile)
    root_datasets = flatten_datasets(target.query(
        "pool.dataset.query", [["id", "=", dataset]], timeout=120,
    ))
    require(dataset in root_datasets, f"v28 dataset query omits the {profile} storage root")
    mount = target.call("filesystem.statfs", mountpoint, timeout=90)
    require(
        mount.get("source") == dataset and mount.get("dest") == mountpoint,
        f"{profile} storage root is not mounted at its canonical path: {mount!r}",
    )
    choices = target.call("container.pool_choices", timeout=120)
    selected_pool = "boot-pool" if profile == "path-a" else "boot-disk-data"
    require(selected_pool in choices,
            f"v28 container choices omit the {profile} storage pool {selected_pool!r}: {choices!r}")
    report("result", f"PASS {action} and verified the v28 {profile} adapter on TrueNAS {expected_version}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RemoteError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1)
