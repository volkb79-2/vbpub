"""Validate a read-only source fingerprint for one exploratory TrueNAS target."""

from __future__ import annotations

import json
from pathlib import Path
import re


REMOTE_ROOT = "/usr/lib/python3/dist-packages"
EXPECTED_FILES = frozenset({
    "middlewared/plugins/vm/info.py",
    "middlewared/async_validators.py",
    "middlewared/plugins/pool_/dataset_query_utils.py",
    "middlewared/plugins/pool_/dataset.py",
    "middlewared/plugins/pool_/dataset_info.py",
    "middlewared/plugins/container/container.py",
    "middlewared/plugins/container/dataset.py",
    "middlewared/plugins/container/utils.py",
    "middlewared/plugins/container/image.py",
    "middlewared/plugins/container/lifecycle.py",
    "middlewared/plugins/vm/vm_devices.py",
    "middlewared/utils/libvirt/storage_devices.py",
    "truenas_pylibvirt/domain/vm/xml.py",
    "middlewared/plugins/pool_/import_pool.py",
    "middlewared/plugins/zfs/utils.py",
    "middlewared/plugins/boot_pool_user_data.py",
    "middlewared/plugins/vm/virtualization_support.py",
})


class ObservedPatchManifestError(ValueError):
    """The manifest is malformed or does not describe the requested target."""


def load_observed_patch_manifest(
    path: str | Path,
    *,
    target_host: str,
    expected_version: str,
    profile: str,
) -> dict:
    try:
        document = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise ObservedPatchManifestError("cannot read observed-code fingerprint JSON") from None
    if not isinstance(document, dict) or document.get("schema_version") != 1:
        raise ObservedPatchManifestError("observed-code fingerprint must use schema version 1")
    if document.get("target_host") != target_host:
        raise ObservedPatchManifestError("observed-code fingerprint is for a different target host")
    if document.get("version") != expected_version or expected_version != "27.0.0-RC.1":
        raise ObservedPatchManifestError("observed-code fingerprint does not match the expected v27 build")
    if profile != "path-a" or document.get("profile") != profile:
        raise ObservedPatchManifestError("observed-code fingerprint supports only the Path A profile")
    if document.get("install_record") != "absent":
        raise ObservedPatchManifestError("observed-code fingerprint must describe an absent patch-install record")
    files = document.get("files")
    if not isinstance(files, dict) or set(files) != EXPECTED_FILES:
        raise ObservedPatchManifestError("observed-code fingerprint has an unexpected file set")
    for name, digest in files.items():
        if (not isinstance(name, str) or name.startswith("/") or ".." in Path(name).parts
                or "\\" in name or not re.fullmatch(r"[A-Za-z0-9_./-]+", name)):
            raise ObservedPatchManifestError(f"observed-code fingerprint has an unsafe path: {name!r}")
        if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
            raise ObservedPatchManifestError(f"observed-code fingerprint has an invalid digest for {name}")
    return document


def verify_observed_patch_files(target, document: dict) -> None:
    files: dict[str, str] = document["files"]
    paths = {f"{REMOTE_ROOT}/{name}": name for name in files}
    output = target.run(["sha256sum", "--", *paths], timeout=120)
    actual: dict[str, str] = {}
    for line in output.splitlines():
        digest, separator, remote_path = line.partition("  ")
        if not separator or remote_path not in paths:
            raise ObservedPatchManifestError("target returned malformed observed-code checksums")
        if remote_path in actual:
            raise ObservedPatchManifestError("target returned duplicate observed-code checksums")
        actual[remote_path] = digest
    if set(actual) != set(paths):
        raise ObservedPatchManifestError("target did not return a checksum for every observed source file")
    mismatches = [
        f"{paths[remote_path]} expected={files[paths[remote_path]]} actual={actual[remote_path]}"
        for remote_path in paths
        if actual[remote_path] != files[paths[remote_path]]
    ]
    if mismatches:
        raise ObservedPatchManifestError(
            "target source no longer matches its observed-code fingerprint: " + "; ".join(mismatches)
        )
