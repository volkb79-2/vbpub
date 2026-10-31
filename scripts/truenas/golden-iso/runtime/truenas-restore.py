#!/usr/bin/python3
"""Restore the bundled TrueNAS whole-disk image onto one unambiguous disk."""

from __future__ import annotations

import hashlib
import json
import os
import re
import socket
import stat
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


MEDIA_ROOT = Path("/run/live/medium")
INSTALL_ROOT = MEDIA_ROOT / "truenas-restore"
MANIFEST_PATH = INSTALL_ROOT / "manifest.json"
RUNTIME_CONFIG = Path("/etc/truenas-unattended-restore/runtime.json")
GIB_BYTES = 1024**3
MIN_GOLDEN_DISK_BYTES = 16 * GIB_BYTES
GPT_LOADER = r"\EFI\BOOT\BOOTX64.EFI"
PARTITION_TYPE_CODES = {
    "21686148-6449-6e6f-744e-656564454649": "EF02",
    "c12a7328-f81f-11d2-ba4b-00a0c93ec93b": "EF00",
    "6a898cc3-1dd2-11b2-99a6-080020736631": "BF01",
}
EFI_PARTITION_TYPE_GUID = "c12a7328-f81f-11d2-ba4b-00a0c93ec93b"
RESTORE_RECEIPT_RELATIVE_PATH = Path("EFI") / "TRUENAS-RESTORE" / "manifest.json"


class RestoreError(RuntimeError):
    pass


def golden_image_payload_name(raw_size_bytes: int) -> str:
    if (
        isinstance(raw_size_bytes, bool)
        or not isinstance(raw_size_bytes, int)
        or raw_size_bytes < MIN_GOLDEN_DISK_BYTES
        or raw_size_bytes % GIB_BYTES
        or raw_size_bytes % 512
    ):
        raise RestoreError(
            "restore manifest image size must be a whole-GiB, sector-aligned disk of at least 16 GiB"
        )
    return f"truenas-27-{raw_size_bytes // GIB_BYTES}GiB-golden.raw.zst"


def log(message: str) -> None:
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"{stamp} {message}", flush=True)


def run(
    argv: list[str],
    *,
    check: bool = True,
    timeout: int = 120,
) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            argv,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RestoreError(f"could not run {argv[0]}: {type(exc).__name__}") from None
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout).strip().splitlines()
        tail = detail[-3:] if detail else []
        suffix = "; ".join(tail)
        raise RestoreError(
            f"{argv[0]} exited {result.returncode}"
            + (f": {suffix}" if suffix else "")
        )
    return result


class Installer:
    def __init__(self) -> None:
        if os.geteuid() != 0:
            raise RestoreError("installer service must run as root")
        self.config = self._read_runtime_config()
        self.webhook = self.config["mattermost_webhook_url"]
        self._notification_retry_after = 0.0
        self.manifest = self._read_manifest()
        self.image_path = INSTALL_ROOT / golden_image_payload_name(
            self.manifest["raw_image_size_bytes"]
        )
        self.media_device = self._mounted_media_device()

    def _read_runtime_config(self) -> dict[str, Any]:
        try:
            mode = stat.S_IMODE(RUNTIME_CONFIG.stat().st_mode)
            data = json.loads(RUNTIME_CONFIG.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RestoreError(
                f"cannot read runtime notification configuration: {type(exc).__name__}"
            ) from None
        if mode & 0o077:
            raise RestoreError("runtime notification file must be readable by root only")
        webhook = data.get("mattermost_webhook_url")
        if not isinstance(webhook, str) or not webhook.startswith("https://"):
            raise RestoreError("runtime Mattermost webhook is missing or is not HTTPS")
        return data

    def _read_manifest(self) -> dict[str, Any]:
        try:
            manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RestoreError(
                f"cannot read restore manifest: {type(exc).__name__}"
            ) from None
        if manifest.get("schema_version") != 1:
            raise RestoreError("unsupported restore manifest schema")
        image_size = manifest.get("raw_image_size_bytes")
        raw_sha = manifest.get("raw_image_sha256")
        compressed_sha = manifest.get("compressed_image_sha256")
        partitions = manifest.get("partitions")
        if (
            not isinstance(image_size, int)
            or image_size < MIN_GOLDEN_DISK_BYTES
            or image_size % GIB_BYTES
            or not isinstance(raw_sha, str)
            or not re.fullmatch(r"[0-9a-f]{64}", raw_sha)
            or not isinstance(compressed_sha, str)
            or not re.fullmatch(r"[0-9a-f]{64}", compressed_sha)
            or not isinstance(partitions, list)
            or [p.get("number") for p in partitions] != [1, 2, 3]
        ):
            raise RestoreError("restore manifest has invalid image or GPT facts")
        return manifest

    def _mounted_media_device(self) -> str:
        result = run(
            [
                "findmnt",
                "--noheadings",
                "--output",
                "SOURCE",
                "--target",
                str(MEDIA_ROOT),
            ],
            check=False,
        )
        if result.returncode != 0:
            raise RestoreError(f"{MEDIA_ROOT} is not mounted as live media")
        source = result.stdout.strip().split("[", 1)[0]
        if not source.startswith("/dev/"):
            raise RestoreError("live media source is not a block device")
        return os.path.realpath(source)

    def notify(self, title: str, detail: str) -> None:
        message = (
            f"**TrueNAS unattended restore: {title}**\n"
            f"Host: {socket.gethostname()}\n"
            f"{detail}"
        )
        log(f"NOTIFY {title}: {detail.replace(chr(10), ' | ')}")
        payload = json.dumps({"text": message}, ensure_ascii=False).encode("utf-8")
        if time.monotonic() < self._notification_retry_after:
            log("WARN Mattermost retry is paused after a recent delivery failure")
            return
        request = urllib.request.Request(
            self.webhook,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        for attempt in range(1, 3):
            try:
                with urllib.request.urlopen(request, timeout=5) as response:
                    if not 200 <= response.status < 300:
                        raise RestoreError(f"Mattermost returned HTTP {response.status}")
                    log(f"Mattermost notification delivered: {title}")
                    return
            except urllib.error.HTTPError as exc:
                log(f"WARN Mattermost notification failed with HTTP {exc.code}")
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                log(f"WARN Mattermost notification failed: {type(exc).__name__}")
            if attempt < 2:
                time.sleep(2)
        self._notification_retry_after = time.monotonic() + 60
        log("WARN continuing without Mattermost delivery for the next minute")

    def _lsblk(
        self,
    ) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]], dict[str, str]]:
        result = run(
            [
                "lsblk",
                "--json",
                "--tree",
                "--bytes",
                "--output",
                "NAME,PATH,TYPE,SIZE,RO,RM,MODEL,SERIAL,TRAN,MOUNTPOINTS,PKNAME,PARTN,PARTTYPE,PARTUUID",
            ]
        )
        tree = json.loads(result.stdout)["blockdevices"]
        nodes: dict[str, dict[str, Any]] = {}
        parent_disk: dict[str, str] = {}
        disks: list[dict[str, Any]] = []

        def visit(node: dict[str, Any], disk_path: str | None = None) -> None:
            path = node.get("path") or f"/dev/{node['name']}"
            node["path"] = os.path.realpath(path)
            nodes[node["path"]] = node
            own_disk = node["path"] if node.get("type") == "disk" else disk_path
            if node.get("type") == "disk":
                disks.append(node)
            if own_disk:
                parent_disk[node["path"]] = own_disk
            for child in node.get("children") or []:
                visit(child, own_disk)

        for node in tree:
            visit(node)
        return disks, nodes, parent_disk

    @staticmethod
    def _mounted(node: dict[str, Any]) -> list[str]:
        mounts = list(node.get("mountpoints") or [])
        for child in node.get("children") or []:
            mounts.extend(Installer._mounted(child))
        return [mount for mount in mounts if mount]

    def _describe_disks(
        self, disks: list[dict[str, Any]], source_disk: str | None
    ) -> tuple[dict[str, Any], list[str]]:
        descriptions: list[str] = []
        eligible: list[dict[str, Any]] = []
        for disk in disks:
            path = disk["path"]
            source_media = source_disk is not None and path == source_disk
            mounted = self._mounted(disk)
            size = int(disk.get("size") or 0)
            readonly = bool(disk.get("ro"))
            removable = bool(disk.get("rm"))
            item = (
                f"{path}: {size} bytes, model={disk.get('model') or 'unknown'}, "
                f"serial={disk.get('serial') or 'unknown'}, "
                f"transport={disk.get('tran') or 'unknown'}, "
                f"removable={removable}, read_only={readonly}, "
                f"mounts={','.join(mounted) if mounted else 'none'}"
            )
            if source_media:
                item += " [live-media device]"
            descriptions.append(item)
            if source_media or readonly or removable:
                continue
            eligible.append(disk)

        summary = "\n".join(f"- {item}" for item in descriptions) or "- no disks reported"
        self.notify("block-device discovery", summary)
        if len(eligible) != 1:
            raise RestoreError(
                "expected exactly one writable, non-removable disk apart from "
                f"the live media; found {len(eligible)}"
            )
        selected = eligible[0]
        mounts = self._mounted(selected)
        if mounts:
            raise RestoreError(
                f"refusing to overwrite {selected['path']}; mounted descendants: "
                + ", ".join(mounts)
            )
        if int(selected.get("size") or 0) < self.manifest["raw_image_size_bytes"]:
            raise RestoreError(
                f"{selected['path']} is smaller than the bundled "
                f"{self.manifest['raw_image_size_bytes']}-byte image"
            )
        sector_size = int(run(["blockdev", "--getss", selected["path"]]).stdout.strip())
        if sector_size != self.manifest["logical_sector_size_bytes"]:
            raise RestoreError(
                f"{selected['path']} uses {sector_size}-byte logical sectors; "
                f"the image requires {self.manifest['logical_sector_size_bytes']}"
            )
        return selected, descriptions

    @staticmethod
    def _partition_info(device: str, number: int) -> dict[str, Any]:
        result = run(["sgdisk", "--info", str(number), device])
        fields: dict[str, str] = {}
        patterns = {
            "start": r"First sector:\s*(\d+)",
            "end": r"Last sector:\s*(\d+)",
            "type_code": r"Partition GUID code:\s*([0-9A-Fa-f-]{36})",
            "unique_guid": r"Partition unique GUID:\s*([0-9A-Fa-f-]{36})",
        }
        for key, pattern in patterns.items():
            match = re.search(pattern, result.stdout)
            if not match:
                raise RestoreError(f"could not parse GPT partition {number} field {key}")
            fields[key] = match.group(1)
        return {
            "number": number,
            "start": int(fields["start"]),
            "end": int(fields["end"]),
            "type_code": PARTITION_TYPE_CODES.get(
                fields["type_code"].lower(), fields["type_code"].upper()
            ),
            "unique_guid": fields["unique_guid"].lower(),
        }

    def _matches_golden_gpt(self, device: str) -> bool:
        try:
            disk = run(["sgdisk", "--print", device], check=False)
            if disk.returncode != 0:
                return False
            match = re.search(
                r"Disk identifier \(GUID\):\s*([0-9A-Fa-f-]{36})",
                disk.stdout,
            )
            if not match or match.group(1).lower() != self.manifest["disk_guid"].lower():
                return False
            for expected in self.manifest["partitions"]:
                actual = self._partition_info(device, int(expected["number"]))
                for key in ("start", "end", "type_code", "unique_guid"):
                    if actual[key] != expected[key]:
                        return False
            return True
        except RestoreError:
            return False

    def _restore_receipt(self) -> dict[str, Any]:
        efi = next(
            item for item in self.manifest["partitions"] if int(item["number"]) == 2
        )
        return {
            "schema_version": 1,
            "raw_image_sha256": self.manifest["raw_image_sha256"],
            "raw_image_size_bytes": self.manifest["raw_image_size_bytes"],
            "disk_guid": self.manifest["disk_guid"].lower(),
            "efi_partition_guid": str(efi["unique_guid"]).lower(),
        }

    def _efi_partition_device(self, device: str) -> str:
        result = run(
            [
                "lsblk",
                "--json",
                "--tree",
                "--bytes",
                "--output",
                "PATH,TYPE,PARTN,PARTTYPE,PARTUUID",
                device,
            ]
        )
        try:
            roots = json.loads(result.stdout)["blockdevices"]
        except (KeyError, json.JSONDecodeError) as exc:
            raise RestoreError(
                f"could not inspect EFI partition on {device}: {type(exc).__name__}"
            ) from None
        if len(roots) != 1 or roots[0].get("type") != "disk":
            raise RestoreError(f"{device} did not resolve to exactly one disk")
        if os.path.realpath(roots[0].get("path", "")) != os.path.realpath(device):
            raise RestoreError(f"lsblk returned a different disk while inspecting {device}")

        partitions: list[dict[str, Any]] = []

        def visit(node: dict[str, Any]) -> None:
            if node.get("type") == "part":
                partitions.append(node)
            for child in node.get("children") or []:
                visit(child)

        visit(roots[0])
        matches = [part for part in partitions if str(part.get("partn")) == "2"]
        if len(matches) != 1:
            raise RestoreError(f"could not identify exactly one EFI partition on {device}")
        partition = matches[0]
        expected = self._restore_receipt()["efi_partition_guid"]
        if (
            str(partition.get("parttype", "")).lower() != EFI_PARTITION_TYPE_GUID
            or str(partition.get("partuuid", "")).lower() != expected
        ):
            raise RestoreError(
                f"partition 2 on {device} is not the golden image's EFI partition"
            )
        path = partition.get("path")
        if not isinstance(path, str) or not path.startswith("/dev/"):
            raise RestoreError(f"lsblk returned no block-device path for {device} partition 2")
        return os.path.realpath(path)

    @contextmanager
    def _mounted_efi(self, device: str, *, read_only: bool) -> Iterator[Path]:
        partition = self._efi_partition_device(device)
        mountpoint = Path(tempfile.mkdtemp(prefix="truenas-restore-efi-", dir="/run"))
        mounted = False
        try:
            options = (
                "ro,nosuid,nodev,noexec"
                if read_only
                else "rw,nosuid,nodev,noexec,umask=077"
            )
            run(["mount", "-t", "vfat", "-o", options, partition, str(mountpoint)])
            mounted = True
            source = run(
                [
                    "findmnt",
                    "--noheadings",
                    "--output",
                    "SOURCE",
                    "--target",
                    str(mountpoint),
                ]
            ).stdout.strip().split("[", 1)[0]
            if os.path.realpath(source) != partition:
                raise RestoreError(
                    f"EFI mount at {mountpoint} came from unexpected source {source}"
                )
            yield mountpoint
        finally:
            unmount_error: RestoreError | None = None
            if mounted:
                try:
                    run(["umount", str(mountpoint)], timeout=60)
                except RestoreError as exc:
                    unmount_error = exc
            try:
                mountpoint.rmdir()
            except OSError as exc:
                if unmount_error is None:
                    raise RestoreError(
                        f"could not remove EFI mount directory {mountpoint}: "
                        f"{type(exc).__name__}"
                    ) from None
            if unmount_error is not None:
                raise unmount_error

    def _read_restore_receipt(self, device: str) -> dict[str, Any] | None:
        with self._mounted_efi(device, read_only=True) as mountpoint:
            path = mountpoint / RESTORE_RECEIPT_RELATIVE_PATH
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except FileNotFoundError:
                return None
            except json.JSONDecodeError:
                log("WARN EFI restore receipt is malformed; a fresh restore will run")
                return None
            except OSError as exc:
                raise RestoreError(
                    f"could not read EFI restore receipt: {type(exc).__name__}"
                ) from None
        return value if isinstance(value, dict) else None

    def _write_restore_receipt(self, device: str) -> None:
        document = self._restore_receipt()
        payload = (json.dumps(document, sort_keys=True, indent=2) + "\n").encode("utf-8")
        with self._mounted_efi(device, read_only=False) as mountpoint:
            directory = mountpoint / RESTORE_RECEIPT_RELATIVE_PATH.parent
            directory.mkdir(parents=True, exist_ok=True)
            path = mountpoint / RESTORE_RECEIPT_RELATIVE_PATH
            temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                with os.fdopen(fd, "wb") as stream:
                    stream.write(payload)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, path)
            finally:
                if temporary.exists():
                    temporary.unlink()
            run(["sync"], timeout=120)
        self.notify(
            "restore receipt written",
            f"EFI partition on {device} now records image SHA-256 "
            f"{document['raw_image_sha256']}. This receipt was written only after "
            "the full image read-back and GPT layout checks succeeded.",
        )

    def _already_installed(self, device: str) -> bool:
        """Skip a repeat ISO boot only when the matching image receipt is present."""
        if not self._matches_golden_gpt(device):
            return False
        receipt = self._read_restore_receipt(device)
        if receipt != self._restore_receipt():
            self.notify(
                "restore receipt missing or mismatched",
                f"{device} has matching GPT identifiers, but no valid receipt for "
                f"image SHA-256 {self.manifest['raw_image_sha256']}. GPT identity "
                "alone does not prove the image contents; the full restore will run.",
            )
            return False
        self.notify(
            "restore receipt matched",
            f"{device} has a post-write verification receipt for image SHA-256 "
            f"{self.manifest['raw_image_sha256']}. The disk write can be skipped.",
        )
        return True

    def _verify_compressed_image(self) -> None:
        if not self.image_path.is_file():
            raise RestoreError(f"bundled image not found at {self.image_path}")
        image_size = self.image_path.stat().st_size
        self.notify(
            "image preflight",
            f"Compressed image: {image_size} bytes\n"
            f"Raw size: {self.manifest['raw_image_size_bytes']} bytes\n"
            f"Raw SHA-256: {self.manifest['raw_image_sha256']}\n"
            "Checking the Zstandard frame checksum before writing any target sectors.",
        )
        digest = hashlib.sha256()
        with self.image_path.open("rb") as source:
            for chunk in iter(lambda: source.read(16 * 1024 * 1024), b""):
                digest.update(chunk)
        if digest.hexdigest() != self.manifest["compressed_image_sha256"]:
            raise RestoreError("bundled compressed image SHA-256 does not match manifest")
        run(["zstd", "--test", "--quiet", str(self.image_path)], timeout=1800)

    def _verify_uefi_boot_environment(self) -> None:
        if not Path("/sys/firmware/efi/efivars").is_dir():
            raise RestoreError(
                "restore ISO did not boot in UEFI mode; refusing to overwrite the disk"
            )
        variables = run(["efibootmgr", "--verbose"], check=False)
        if variables.returncode != 0:
            raise RestoreError(
                "firmware boot variables are unavailable; refusing to overwrite the disk"
            )
        self.notify(
            "UEFI preflight",
            "Live ISO is running in UEFI mode and firmware boot variables are readable.",
        )

    def _write_image(self, device: str) -> None:
        expected_size = int(self.manifest["raw_image_size_bytes"])
        expected_hash = self.manifest["raw_image_sha256"]
        self.notify(
            "restore started",
            f"Writing the first {expected_size} bytes to {device}.\n"
            "Other sectors after the image are left untouched, except the "
            "backup GPT metadata relocated to the physical disk end. "
            "No GPT data partition or ZFS pool is grown.",
        )
        try:
            decompressor = subprocess.Popen(
                ["zstd", "--quiet", "--decompress", "--stdout", str(self.image_path)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except OSError as exc:
            raise RestoreError(f"could not start zstd: {type(exc).__name__}") from None
        if decompressor.stdout is None or decompressor.stderr is None:
            decompressor.kill()
            raise RestoreError("could not open Zstandard output")

        fd = os.open(device, os.O_WRONLY | os.O_CLOEXEC)
        digest = hashlib.sha256()
        written = 0
        last_percent = -10
        try:
            while True:
                chunk = decompressor.stdout.read(16 * 1024 * 1024)
                if not chunk:
                    break
                if written + len(chunk) > expected_size:
                    raise RestoreError("decompressed image exceeds its manifest size")
                digest.update(chunk)
                view = memoryview(chunk)
                while view:
                    amount = os.write(fd, view)
                    if amount <= 0:
                        raise RestoreError("target disk returned a short write")
                    view = view[amount:]
                written += len(chunk)
                percent = written * 100 // expected_size
                if percent >= last_percent + 10:
                    last_percent = percent
                    self.notify(
                        "restore progress",
                        f"Target {device}: {written} of {expected_size} bytes "
                        f"({percent}%).",
                    )
            os.fsync(fd)
        except Exception:
            decompressor.terminate()
            decompressor.wait(timeout=10)
            raise
        finally:
            os.close(fd)
            decompressor.stdout.close()
        stderr = decompressor.stderr.read().decode("utf-8", errors="replace").strip()
        returncode = decompressor.wait()
        if returncode != 0:
            raise RestoreError(
                f"zstd failed while restoring (exit {returncode})"
                + (f": {stderr[-500:]}" if stderr else "")
            )
        if written != expected_size:
            raise RestoreError(f"restored {written} bytes; expected {expected_size}")
        actual_hash = digest.hexdigest()
        if actual_hash != expected_hash:
            raise RestoreError(
                f"restored stream SHA-256 mismatch: expected {expected_hash}, "
                f"got {actual_hash}"
            )
        run(["blockdev", "--flushbufs", device])
        log(f"Image stream SHA-256 verified: {actual_hash}")

        self.notify(
            "read-back verification",
            f"Reading back the first {expected_size} bytes of {device} and "
            "checking the whole-disk SHA-256 before GPT boot repair.",
        )
        readback_hash = hashlib.sha256()
        read_fd = os.open(device, os.O_RDONLY | os.O_CLOEXEC)
        try:
            offset = 0
            next_report = 25
            while offset < expected_size:
                chunk = os.pread(
                    read_fd,
                    min(16 * 1024 * 1024, expected_size - offset),
                    offset,
                )
                if not chunk:
                    raise RestoreError(
                        f"disk read-back ended at byte {offset}, before image end"
                    )
                readback_hash.update(chunk)
                offset += len(chunk)
                percent = offset * 100 // expected_size
                if percent >= next_report:
                    self.notify(
                        "read-back progress",
                        f"Target {device} verified through {percent}% "
                        f"({offset} bytes).",
                    )
                    next_report += 25
        finally:
            os.close(read_fd)
        if readback_hash.hexdigest() != expected_hash:
            raise RestoreError(
                "disk read-back SHA-256 differs from the golden image: "
                f"{readback_hash.hexdigest()}"
            )
        log(f"Target read-back SHA-256 verified: {expected_hash}")

    def _verify_partition_layout(self, device: str) -> None:
        run(["blockdev", "--rereadpt", device])
        run(["udevadm", "settle", "--timeout=30"])
        before = [
            self._partition_info(device, int(entry["number"]))
            for entry in self.manifest["partitions"]
        ]
        self.notify(
            "GPT relocation",
            f"Moving only the backup GPT header to the end of {device}. "
            "Partition start/end sectors, including the ZFS partition, remain "
            "the captured values.",
        )
        run(["sgdisk", "--move-second-header", device])
        run(["blockdev", "--rereadpt", device], check=False)
        run(["udevadm", "settle", "--timeout=30"])
        run(["sgdisk", "--verify", device])
        after = [
            self._partition_info(device, int(entry["number"]))
            for entry in self.manifest["partitions"]
        ]
        if before != after:
            raise RestoreError(
                "GPT backup-header repair changed partition geometry or GUIDs"
            )
        expected_zfs = next(item for item in after if int(item["number"]) == 3)
        self.notify(
            "partition layout verified",
            "GPT is valid and all partition identities and extents match the "
            "golden image.\n"
            f"ZFS partition: sectors {expected_zfs['start']}-{expected_zfs['end']} "
            f"({expected_zfs['end'] - expected_zfs['start'] + 1} sectors). "
            "It was not expanded.",
        )

    def _efi_partition_path(self, device: str) -> str:
        result = run(
            [
                "lsblk",
                "--json",
                "--tree",
                "--output",
                "PATH,TYPE,PARTN,PARTTYPE",
                device,
            ]
        )
        blockdevices = json.loads(result.stdout)["blockdevices"]
        parts: list[dict[str, Any]] = []

        def visit(node: dict[str, Any]) -> None:
            if node.get("type") == "part":
                parts.append(node)
            for child in node.get("children") or []:
                visit(child)

        for blockdevice in blockdevices:
            visit(blockdevice)
        match = [
            part
            for part in parts
            if int(part.get("partn") or 0) == 2
            and str(part.get("parttype") or "").lower()
            == "c12a7328-f81f-11d2-ba4b-00a0c93ec93b"
        ]
        if len(match) != 1:
            details = ", ".join(
                f"{part.get('path') or 'unknown'}"
                f"(number={part.get('partn')}, type={part.get('parttype')})"
                for part in parts
            ) or "no partition rows returned"
            raise RestoreError(
                "could not identify exactly one EFI System partition on "
                f"{device}; found {len(match)} matches among: {details}"
            )
        path = match[0].get("path")
        if not isinstance(path, str) or not path:
            raise RestoreError("EFI System partition row has no device path")
        return os.path.realpath(path)

    def _ensure_efi_bootnext(self, device: str) -> str:
        if not Path("/sys/firmware/efi/efivars").is_dir():
            raise RestoreError("ISO did not boot in UEFI mode; cannot configure disk boot")
        esp = self._efi_partition_path(device)
        with tempfile.TemporaryDirectory(prefix="truenas-esp-") as directory:
            mountpoint = Path(directory)
            run(["mount", "-t", "vfat", "-o", "ro", esp, str(mountpoint)])
            try:
                loader = mountpoint / "EFI" / "BOOT" / "BOOTX64.EFI"
                if not loader.is_file():
                    raise RestoreError(
                        "golden EFI partition is missing EFI/BOOT/BOOTX64.EFI"
                    )
            finally:
                run(["umount", str(mountpoint)])

        partition_guid = next(
            str(p["unique_guid"]).lower()
            for p in self.manifest["partitions"]
            if int(p["number"]) == 2
        )

        def find_entry(output: str) -> str | None:
            for line in output.splitlines():
                match = re.match(r"Boot([0-9A-Fa-f]{4})\*?\s", line)
                if (
                    match
                    and f"hd(2,gpt,{partition_guid}" in line.lower()
                    and "file(\\efi\\boot\\bootx64.efi)" in line.lower()
                ):
                    return match.group(1).upper()
            return None

        current = run(["efibootmgr", "--verbose"], check=False)
        if current.returncode != 0:
            raise RestoreError("efibootmgr cannot read firmware boot variables")
        entry_id = find_entry(current.stdout)
        if entry_id is None:
            created = run(
                [
                    "efibootmgr",
                    "--create",
                    "--disk",
                    device,
                    "--part",
                    "2",
                    "--label",
                    "TrueNAS Golden Restore",
                    "--loader",
                    GPT_LOADER,
                ]
            )
            id_match = re.search(r"Boot([0-9A-Fa-f]{4})\*?", created.stdout)
            entry_id = id_match.group(1).upper() if id_match else None
            if entry_id is None:
                after_create = run(["efibootmgr", "--verbose"])
                entry_id = find_entry(after_create.stdout)
        if entry_id is None:
            raise RestoreError("could not create or find the TrueNAS UEFI boot entry")
        run(["efibootmgr", "--bootnext", entry_id])
        after = run(["efibootmgr"])
        if not re.search(
            rf"^BootNext:\s*{re.escape(entry_id)}\s*$",
            after.stdout,
            flags=re.MULTILINE,
        ):
            raise RestoreError("firmware did not retain the TrueNAS BootNext entry")
        return entry_id

    def run(self) -> None:
        self.notify(
            "started",
            f"Debian Live restore ISO booted from {self.media_device}.\n"
            f"Bundled image: {self.manifest['raw_image_size_bytes']} bytes, "
            f"SHA-256 {self.manifest['raw_image_sha256']}.\n"
            "This run identifies one target disk, restores the captured whole "
            "disk, verifies the bytes, preserves ZFS partition size, configures "
            "UEFI BootNext, and reboots.",
        )
        self._verify_uefi_boot_environment()
        self._verify_compressed_image()
        disks, nodes, parent_disk = self._lsblk()
        media_node = nodes.get(self.media_device)
        media_parent = (
            parent_disk.get(self.media_device)
            or (
                self.media_device
                if media_node and media_node.get("type") == "disk"
                else None
            )
        )
        selected, _ = self._describe_disks(disks, media_parent)
        device = selected["path"]
        self.notify(
            "target selected",
            f"Target: {device}\n"
            f"Model: {selected.get('model') or 'unknown'}\n"
            f"Serial: {selected.get('serial') or 'unknown'}\n"
            f"Capacity: {selected.get('size')} bytes\n"
            f"Logical sector size: {self.manifest['logical_sector_size_bytes']} bytes\n"
            "The ISO is excluded. Exactly one writable non-removable disk was "
            "found, it is unmounted, and it is large enough.\n"
            f"The restore writes only {self.manifest['raw_image_size_bytes']} "
            "image bytes. The only write beyond that boundary is the backup "
            "GPT metadata relocated to the physical disk end.",
        )

        if self._already_installed(device):
            self.notify(
                "image already present",
                f"{device} has the golden image's GPT disk and partition "
                "identities. Skipping another disk write. The ISO likely "
                "remained first in firmware boot order; powering off so the "
                "controller can detach it and boot the disk.",
            )
            self._verify_partition_layout(device)
            entry = self._ensure_efi_bootnext(device)
            self.notify(
                "restore complete; powering off",
                f"TrueNAS UEFI entry {entry} is selected for the next boot. "
                "Detach the ISO through Netcup SCP, set boot order to HDD first, "
                "then power the host on.",
            )
            run(["systemctl", "poweroff"], check=False)
            return

        self._write_image(device)
        self._verify_partition_layout(device)
        self._write_restore_receipt(device)
        entry = self._ensure_efi_bootnext(device)
        self.notify(
            "restore complete; rebooting",
            f"Whole-disk image verified on {device}.\n"
            f"UEFI entry {entry} is selected through BootNext. "
            "Partition geometry is unchanged and the ZFS partition was not grown. "
            "Only GPT backup metadata was written beyond the image boundary. "
            "The host is rebooting now.",
        )
        reboot = run(["systemctl", "reboot"], check=False, timeout=30)
        if reboot.returncode != 0:
            self.notify(
                "reboot command failed",
                f"systemctl reboot exited {reboot.returncode}; the image is "
                "restored and BootNext is set. Use Netcup SCP to detach the ISO "
                "and power-cycle the host.",
            )
            raise RestoreError("systemctl reboot did not accept the request")


def main() -> int:
    installer: Installer | None = None
    try:
        installer = Installer()
        installer.run()
    except RestoreError as exc:
        log(f"ERROR {exc}")
        if installer is not None:
            try:
                installer.notify(
                    "FAILED",
                    f"Installer stopped before reporting completion.\nReason: {exc}",
                )
            except Exception:
                pass
        return 1
    except Exception as exc:
        log(f"ERROR unexpected {type(exc).__name__}")
        if installer is not None:
            try:
                installer.notify(
                    "FAILED",
                    "Installer stopped after an unexpected internal error: "
                    f"{type(exc).__name__}. Inspect the live console log.",
                )
            except Exception:
                pass
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
