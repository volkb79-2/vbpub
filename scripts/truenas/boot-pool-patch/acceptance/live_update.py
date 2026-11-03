#!/usr/bin/env python3
"""Install one verified TrueNAS update on a disposable Path A guest.

This lane changes the boot environment and reboots the target. It is deliberately
guarded by separate update, mutation, and reboot switches. The ISO is attached
read-only by tcg_vm.py, so its update payload does not consume boot-pool space.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import sys
import time
import uuid

from remote import RemoteError, Target
from workload_manifest import (
    read_manifest as read_ui_workload_manifest,
    verify_data as verify_ui_workload_data,
    verify_records as verify_ui_workload_records,
)


MEDIA_MOUNT = "/mnt/truenas-update-media"
UPDATE_PATH = f"{MEDIA_MOUNT}/TrueNAS.update"
UPDATE_TIMEOUT_SECONDS = 3600
REBOOT_TIMEOUT_SECONDS = 1800
UPDATE_POLL_INTERVAL_SECONDS = 15


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RemoteError(message)


def report(stage: str, detail: str) -> None:
    print(f"[{stage}] {detail}", flush=True)


def resume_request_from_environment() -> tuple[int, str, str] | None:
    names = (
        "TRUENAS_UPDATE_JOB_ID",
        "TRUENAS_UPDATE_PROOF_DATASET",
        "TRUENAS_UPDATE_PROOF_SHA256",
    )
    values = [os.environ.get(name, "").strip() for name in names]
    if not any(values):
        return None
    if not all(values):
        raise RemoteError(
            "resuming an update requires TRUENAS_UPDATE_JOB_ID, "
            "TRUENAS_UPDATE_PROOF_DATASET, and TRUENAS_UPDATE_PROOF_SHA256 together"
        )
    job_id_text, dataset, marker_hash = values
    if not job_id_text.isdecimal() or int(job_id_text) <= 0:
        raise RemoteError("TRUENAS_UPDATE_JOB_ID must be a positive decimal integer")
    if not re.fullmatch(r"boot-pool/user-data/update-proof-[A-Za-z0-9-]+", dataset):
        raise RemoteError("TRUENAS_UPDATE_PROOF_DATASET is not a guarded update-proof dataset")
    if not re.fullmatch(r"[0-9a-f]{64}", marker_hash):
        raise RemoteError("TRUENAS_UPDATE_PROOF_SHA256 must be a lowercase SHA-256 digest")
    return int(job_id_text), dataset, marker_hash


def is_transient_update_poll_error(exc: RemoteError) -> bool:
    message = str(exc).lower()
    return "remote command failed (255)" in message and any(
        phrase in message
        for phrase in (
            "connection timed out during banner exchange",
            "connection timed out",
            "connection refused",
            "connection reset by peer",
            "no route to host",
            "network is unreachable",
        )
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_update_metadata(path: Path) -> dict:
    try:
        value = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise RemoteError(f"cannot read update metadata {path}: {exc}") from exc
    if not isinstance(value, dict) or value.get("schema") != 1:
        raise RemoteError(f"unsupported update metadata schema in {path}")
    required = {
        "source_iso_sha256": str,
        "update_member": str,
        "update_sha256": str,
        "version": str,
        "manifest_size_bytes": int,
    }
    for key, value_type in required.items():
        item = value.get(key)
        if isinstance(item, bool) or not isinstance(item, value_type):
            raise RemoteError(f"update metadata field {key!r} must be {value_type.__name__}")
    for key in ("source_iso_sha256", "update_sha256"):
        if not re.fullmatch(r"[0-9a-f]{64}", value[key]):
            raise RemoteError(f"update metadata field {key!r} is not a lowercase SHA-256 digest")
    if value["update_member"] != "/TrueNAS.update":
        raise RemoteError("update metadata must name the ISO's /TrueNAS.update member")
    if value["manifest_size_bytes"] <= 0:
        raise RemoteError("update manifest size must be a positive integer")
    return value


def check_no_running_guests(target: Target) -> None:
    for method, label in (("vm.query", "VM"), ("container.query", "container")):
        rows = target.query(method, timeout=120)
        running = [
            row.get("name", row.get("id"))
            for row in rows
            if row.get("status", {}).get("state") == "RUNNING"
        ]
        require(not running, f"refusing update/reboot while {label}s are running: {running!r}")


def wait_for_update_job(target: Target, job_id: int, timeout: int = UPDATE_TIMEOUT_SECONDS) -> dict:
    deadline = time.monotonic() + timeout
    last_signature: tuple[object, ...] | None = None
    next_progress = time.monotonic() + 30
    retry_delay = 5
    while time.monotonic() < deadline:
        try:
            jobs = target.call("core.get_jobs", [["id", "=", job_id]], timeout=60)
            retry_delay = 5
        except RemoteError as exc:
            if not is_transient_update_poll_error(exc):
                raise
            delay = min(retry_delay, max(0, int(deadline - time.monotonic())))
            report(
                "update",
                f"job {job_id} status connection unavailable; retrying in {delay}s: "
                f"{str(exc)[-240:]}",
            )
            time.sleep(delay)
            retry_delay = min(retry_delay * 2, 30)
            continue
        if jobs:
            job = jobs[0]
            state = job.get("state")
            progress = job.get("progress") or {}
            percent = progress.get("percent") if isinstance(progress, dict) else None
            description = progress.get("description") if isinstance(progress, dict) else None
            signature = (state, percent, description)
            if signature != last_signature:
                report(
                    "update",
                    f"job={job_id} state={state!r} percent={percent!r} "
                    f"description={description or ''!r}",
                )
                last_signature = signature
            if state == "SUCCESS":
                return job
            if state in {"FAILED", "ABORTED"}:
                raise RemoteError(
                    f"update job {job_id} ended {state}: "
                    f"{job.get('error') or job.get('exception') or job}"
                )
        now = time.monotonic()
        if now >= next_progress:
            elapsed = int(timeout - max(0, deadline - now))
            report("update", f"job {job_id} is still running after {elapsed}s")
            next_progress = now + 30
        time.sleep(UPDATE_POLL_INTERVAL_SECONDS)
    raise RemoteError(f"update job {job_id} did not finish within {timeout}s")


def wait_for_api(target: Target, expected_version: str, timeout: int = REBOOT_TIMEOUT_SECONDS) -> dict:
    deadline = time.monotonic() + timeout
    last_error: str | None = None
    ssh_ready = False
    next_progress = time.monotonic() + 30
    while time.monotonic() < deadline:
        try:
            if not ssh_ready:
                target.run(["true"], timeout=12)
                ssh_ready = True
                report("reboot", "SSH is available; waiting for middleware API")
            info = target.call("system.info", timeout=60)
            version = str(info.get("version", ""))
            if version == expected_version:
                return info
            last_error = f"middleware reports {version!r}, expected {expected_version!r}"
        except RemoteError as exc:
            last_error = str(exc)
        now = time.monotonic()
        if now >= next_progress:
            elapsed = int(timeout - max(0, deadline - now))
            report("reboot", f"waiting for {expected_version}; {elapsed}s elapsed")
            if last_error:
                report("reboot", f"last readiness result: {last_error[-240:]}")
            next_progress = now + 30
        time.sleep(10)
    raise RemoteError(
        f"TrueNAS API did not report {expected_version} within {timeout}s"
        + (f"; last result: {last_error}" if last_error else "")
    )


def main() -> int:
    for variable in (
        "TRUENAS_TEST_ALLOW_MUTATIONS",
        "TRUENAS_TEST_ALLOW_UPDATE",
        "TRUENAS_TEST_ALLOW_REBOOT",
    ):
        if os.environ.get(variable) != "1":
            raise RemoteError(f"set {variable}=1 only for a disposable TrueNAS update target")
    if os.environ.get("TRUENAS_STORAGE_PROFILE") != "path-a":
        raise RemoteError("TRUENAS_STORAGE_PROFILE must be path-a")

    expected_before = os.environ.get("TRUENAS_EXPECTED_VERSION", "")
    expected_after = os.environ.get("TRUENAS_UPDATE_EXPECTED_VERSION", "")
    if not expected_before or not expected_after:
        raise RemoteError("both current and update target versions are required")
    resume_request = resume_request_from_environment()
    workload_manifest_value = os.environ.get("TRUENAS_UI_WORKLOAD_MANIFEST", "").strip()
    workload_manifest = (
        read_ui_workload_manifest(Path(workload_manifest_value))
        if workload_manifest_value else None
    )
    if workload_manifest is not None:
        require(
            workload_manifest.get("target_version") == expected_before,
            "UI workload manifest was not created on the selected pre-update version",
        )

    iso_path = Path(os.environ.get("TRUENAS_UPDATE_ISO", ""))
    metadata_path = Path(os.environ.get("TRUENAS_UPDATE_METADATA", ""))
    if not iso_path.is_file() or not metadata_path.is_file():
        raise RemoteError("the selected update ISO and its metadata must both be readable files")
    metadata = load_update_metadata(metadata_path)
    if metadata["version"] != expected_after:
        raise RemoteError(
            f"update metadata identifies {metadata['version']!r}, "
            f"not requested target {expected_after!r}"
        )
    iso_sha256 = sha256_file(iso_path)
    if iso_sha256 != metadata["source_iso_sha256"]:
        raise RemoteError(
            f"selected update ISO SHA-256 mismatch: {iso_sha256} != {metadata['source_iso_sha256']}"
        )
    report(
        "media",
        f"verified {iso_path.name} ({iso_path.stat().st_size} bytes, SHA-256 {iso_sha256}) "
        f"for TrueNAS {expected_after}",
    )

    target = Target.from_environment()
    before = target.call("system.info", timeout=120)
    version = str(before.get("version", ""))
    require(version == expected_before, f"expected TrueNAS {expected_before}, got {version!r}")
    check_no_running_guests(target)

    user_data_root = "boot-pool/user-data"
    root_mountpoint = "/mnt/boot-pool/user-data"
    root_stat = target.call("filesystem.statfs", root_mountpoint, timeout=90)
    require(
        root_stat.get("source") == user_data_root and root_stat.get("dest") == root_mountpoint,
        f"Path A user-data root is not mounted as expected: {root_stat!r}",
    )
    root_mounted_before = target.shell(
        f"zfs get -H -o value mounted {shlex.quote(user_data_root)}"
    )
    require(root_mounted_before == "yes", "Path A user-data root is not mounted before the update")
    if workload_manifest is not None:
        verify_ui_workload_records(target, workload_manifest)
        require(
            verify_ui_workload_data(target, workload_manifest, stage="before-update"),
            "manifest-backed UI filesystem is not mounted before the update",
        )

    update_pool_free = int(target.shell("zfs get -Hp -o value available boot-pool"))
    required_free = int(metadata["manifest_size_bytes"])
    if resume_request is None:
        report(
            "preflight",
            f"boot-pool available={update_pool_free} bytes; update manifest requires "
            f"strictly more than {required_free} bytes",
        )
        require(
            update_pool_free > required_free,
            "boot-pool free space does not exceed the update manifest size; refusing update",
        )
    else:
        require(update_pool_free > 0, "boot-pool has no free space while resuming an update")
        report(
            "preflight",
            f"resuming an update already accepted with sufficient space; current "
            f"boot-pool availability is {update_pool_free} bytes",
        )

    devices = target.shell("blkid -c /dev/null -t TYPE=iso9660 -o device").splitlines()
    require(len(devices) == 1, f"expected one attached ISO9660 media device, found {devices!r}")
    media_device = devices[0].strip()
    require(
        re.fullmatch(r"/dev/[A-Za-z0-9._-]+", media_device) is not None,
        f"TrueNAS returned an unsafe ISO device path: {media_device!r}",
    )
    read_only = target.shell(f"blockdev --getro {shlex.quote(media_device)}")
    require(read_only == "1", f"attached update media {media_device} is not read-only")
    media_bytes = int(target.shell(f"blockdev --getsize64 {shlex.quote(media_device)}"))
    require(
        media_bytes == iso_path.stat().st_size,
        f"attached update media size differs from the selected ISO: {media_bytes} != {iso_path.stat().st_size}",
    )
    target.shell(
        f"mkdir -p {shlex.quote(MEDIA_MOUNT)} && "
        f"if ! mountpoint -q {shlex.quote(MEDIA_MOUNT)}; then "
        f"mount -t iso9660 -o ro {shlex.quote(media_device)} {shlex.quote(MEDIA_MOUNT)}; fi && "
        f"test -r {shlex.quote(UPDATE_PATH)}"
    )
    report("media", f"mounted read-only {media_device}; update file is {UPDATE_PATH}")

    if resume_request is None:
        suffix = time.strftime("%Y%m%d%H%M%S", time.gmtime()) + uuid.uuid4().hex[:8]
        probe_dataset = f"{user_data_root}/update-proof-{suffix}"
        probe_mountpoint = f"{root_mountpoint}/update-proof-{suffix}"
        marker_path = f"{probe_mountpoint}/marker"
        marker = f"truenas-update-survival-{suffix}"
        created = target.call("pool.dataset.create", {"name": probe_dataset, "type": "FILESYSTEM"})
        require(created.get("id") == probe_dataset, f"unexpected update probe dataset result: {created!r}")
        target.shell(f"printf %s {shlex.quote(marker)} > {shlex.quote(marker_path)}")
        marker_hash = hashlib.sha256(marker.encode()).hexdigest()
        observed_hash = target.shell(f"sha256sum {shlex.quote(marker_path)}").split()[0]
        require(observed_hash == marker_hash, "update marker hash differed before installation")
        report("probe", f"created {probe_dataset}; marker SHA-256 {marker_hash}")

        report("update", f"starting update.manual for {expected_after}; no reboot is requested yet")
        job_id = target.call("update.manual", UPDATE_PATH, {"cleanup": False}, timeout=120)
        require(
            isinstance(job_id, int) and not isinstance(job_id, bool) and job_id > 0,
            f"update.manual returned an invalid job id: {job_id!r}",
        )
    else:
        job_id, probe_dataset, marker_hash = resume_request
        suffix = probe_dataset.removeprefix(f"{user_data_root}/update-proof-")
        probe_mountpoint = f"{root_mountpoint}/update-proof-{suffix}"
        marker_path = f"{probe_mountpoint}/marker"
        actual_mountpoint = target.shell(
            f"zfs get -H -o value mountpoint {shlex.quote(probe_dataset)}"
        )
        require(
            actual_mountpoint == probe_mountpoint,
            f"resume proof dataset mountpoint changed: {actual_mountpoint!r}",
        )
        mounted = target.shell(f"zfs get -H -o value mounted {shlex.quote(probe_dataset)}")
        if mounted != "yes":
            target.shell(f"zfs mount {shlex.quote(probe_dataset)}")
        observed_hash = target.shell(f"sha256sum {shlex.quote(marker_path)}").split()[0]
        require(
            observed_hash == marker_hash,
            f"resume proof marker hash differs: {observed_hash} != {marker_hash}",
        )
        report("probe", f"verified existing {probe_dataset}; marker SHA-256 {marker_hash}")
        report("update", f"resuming existing update.manual job {job_id}; no second update will be started")

    completed_job = wait_for_update_job(target, job_id)
    report("update", f"job {job_id} completed successfully: {completed_job.get('state')}")

    target.run(["systemctl", "reboot", "--no-block"], timeout=20, check=False)
    report("reboot", "update job succeeded; requested a controlled system reboot")
    after = wait_for_api(target, expected_after)
    require(
        str(after.get("version", "")) == expected_after,
        f"post-update version mismatch: {after.get('version')!r}",
    )
    pool_health = target.shell("zpool status -x boot-pool")
    require("is healthy" in pool_health, f"boot-pool is unhealthy after update: {pool_health!r}")

    root_mounted_after = target.shell(
        f"zfs get -H -o value mounted {shlex.quote(user_data_root)}"
    )
    proof_mounted_after = target.shell(
        f"zfs get -H -o value mounted {shlex.quote(probe_dataset)}"
    )
    workload_mounted_after = None
    if workload_manifest is not None:
        workload_dataset = workload_manifest["filesystem"]["dataset"]
        workload_mounted_after = target.shell(
            f"zfs get -H -o value mounted {shlex.quote(workload_dataset)}"
        )
        verify_ui_workload_records(target, workload_manifest)
        verify_ui_workload_data(target, workload_manifest, stage="after-update-before-remount")
    report(
        "post-update-auto-mount",
        f"root={root_mounted_after}; update-proof={proof_mounted_after}; "
        f"ui-filesystem={workload_mounted_after if workload_mounted_after is not None else 'not-recorded'}",
    )

    mount_repair: list[str] = []
    if root_mounted_after != "yes":
        target.shell(f"zfs mount {shlex.quote(user_data_root)}")
        mount_repair.append(user_data_root)
    if workload_manifest is not None and workload_mounted_after != "yes":
        mounted_now = target.shell(
            f"zfs get -H -o value mounted {shlex.quote(workload_manifest['filesystem']['dataset'])}"
        )
        if mounted_now != "yes":
            target.shell(f"zfs mount {shlex.quote(workload_manifest['filesystem']['dataset'])}")
            mount_repair.append(workload_manifest["filesystem"]["dataset"])
    if proof_mounted_after != "yes":
        target.shell(f"zfs mount {shlex.quote(probe_dataset)}")
        mount_repair.append(probe_dataset)
    if workload_manifest is not None:
        verify_ui_workload_records(target, workload_manifest)
        require(
            verify_ui_workload_data(target, workload_manifest, stage="after-update-after-remount"),
            "manifest-backed UI filesystem remained unavailable after the recorded mount check",
        )
    for dataset in (user_data_root, probe_dataset):
        actual_mountpoint = target.shell(f"zfs get -H -o value mountpoint {shlex.quote(dataset)}")
        require(actual_mountpoint.startswith(root_mountpoint),
                f"unexpected mountpoint for {dataset}: {actual_mountpoint!r}")
    after_hash = target.shell(f"sha256sum {shlex.quote(marker_path)}").split()[0]
    require(after_hash == marker_hash, f"user-data marker did not survive update: {after_hash} != {marker_hash}")
    report(
        "result",
        f"PASS update to {expected_after}; boot-pool is healthy and marker dataset "
        f"{probe_dataset} survived with SHA-256 {after_hash}; manual mount repairs={mount_repair!r}; "
        f"UI workload records and their filesystem/zvol hashes "
        f"{'survived' if workload_manifest is not None else 'were not included'}. "
        f"The v28 mount adapter still needs reapplication.",
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RemoteError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1)
