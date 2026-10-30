"""Version-specific on-guest records for the registered storage adapters."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


V27_INSTALL_RECORD = "/root/v27-boot-pool-patch-install.json"
V28_INSTALL_RECORD = ".truenas-boot-pool-patch-v28.json"
V28_PATHS = (
    f"/mnt/boot-pool/user-data/{V28_INSTALL_RECORD}",
    f"/mnt/boot-disk-data/{V28_INSTALL_RECORD}",
)
V27_RELEASES = frozenset({"27.0.0-RC.1", "27.0.0-MASTER+20261027-173422"})
V28_RELEASE_TARGET_COUNTS = {
    "28.0.0-MASTER+20261014-230204": 15,
    "28.0.0-MASTER+20261027-175229": 19,
}
V28_TARGET_MANIFESTS = {
    "28.0.0-MASTER+20261014-230204": "v28-nightly-20261014/targets.sha256",
    "28.0.0-MASTER+20261027-175229": "v28-nightly-20261027/targets.sha256",
}
V28_BASE_HELPERS = frozenset({
    "middlewared/plugins/boot_pool_user_data.py",
    "middlewared/plugins/vm/virtualization_support.py",
    "middlewared/plugins/pool_/boot_pool_mount.py",
})
V28_PARTITION_HELPERS = frozenset({
    "middlewared/plugins/boot_disk_partition.py",
    "middlewared/plugins/pool_create_schema_v28.json",
})
SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")


def target_names(version: str) -> frozenset[str]:
    """Read exact adapter target names from the registered release manifest."""
    manifest = V28_TARGET_MANIFESTS.get(version)
    if manifest is None:
        raise ValueError(f"no registered v28 target manifest for TrueNAS {version!r}")
    path = Path(__file__).resolve().parents[1] / manifest
    names: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        fields = line.split(maxsplit=1)
        if len(fields) != 2 or SHA256_RE.fullmatch(fields[0]) is None:
            raise ValueError(f"invalid target row in {path}")
        if fields[1] in names:
            raise ValueError(f"duplicate target in {path}: {fields[1]}")
        names.add(fields[1])
    if len(names) != V28_RELEASE_TARGET_COUNTS[version]:
        raise ValueError(f"unexpected target count in {path}: {len(names)}")
    return frozenset(names)


def manifest_paths(version: str) -> tuple[str, ...]:
    """Return only the exact on-guest manifest locations for a supported build."""
    if version in V27_RELEASES:
        return (V27_INSTALL_RECORD,)
    if version in V28_RELEASE_TARGET_COUNTS:
        return V28_PATHS
    raise ValueError(f"no registered patch record for TrueNAS {version!r}")


def v28_record_matches(record: Any, version: str) -> bool:
    """Validate the release, profile and closed before/after/helper hash sets."""
    target_count = V28_RELEASE_TARGET_COUNTS.get(version)
    if target_count is None or not isinstance(record, dict):
        return False
    if (type(record.get("schema")) is not int or record.get("schema") != 1
            or record.get("version") != version
            or record.get("storage_profile") not in {"path-a", "path-b"}
            or not isinstance(record.get("backup"), str) or not record["backup"].startswith("/")):
        return False
    bundle_hash = record.get("bundle_sha256")
    if not isinstance(bundle_hash, str) or SHA256_RE.fullmatch(bundle_hash) is None:
        return False

    before = record.get("target_before")
    after = record.get("target_after")
    if not isinstance(before, dict) or not isinstance(after, dict):
        return False
    try:
        expected_targets = target_names(version)
    except (OSError, ValueError):
        return False
    if set(before) != expected_targets or set(after) != expected_targets:
        return False
    if any(not isinstance(name, str) or not isinstance(digest, str)
           or SHA256_RE.fullmatch(digest) is None for name, digest in before.items()):
        return False
    if any(not isinstance(digest, str) or SHA256_RE.fullmatch(digest) is None
           for digest in after.values()):
        return False

    expected_helpers = V28_BASE_HELPERS
    if version == "28.0.0-MASTER+20261027-175229":
        expected_helpers |= V28_PARTITION_HELPERS
    helpers = record.get("helpers")
    return (
        isinstance(helpers, dict)
        and set(helpers) == expected_helpers
        and all(isinstance(digest, str) and SHA256_RE.fullmatch(digest) is not None
                for digest in helpers.values())
    )


def select_v28_record(contents: dict[str, str], version: str) -> tuple[str, dict[str, Any]]:
    """Select exactly one valid v28 adapter record from the registered locations."""
    paths = V28_PATHS
    if version not in V28_RELEASE_TARGET_COUNTS:
        raise ValueError(f"no registered v28 patch record for TrueNAS {version!r}")
    if set(contents) - set(paths):
        raise ValueError("v28 patch record scan included an unregistered location")

    records: list[tuple[str, dict[str, Any]]] = []
    for path, raw in contents.items():
        try:
            record = json.loads(raw)
        except (TypeError, json.JSONDecodeError) as exc:
            raise ValueError(f"invalid v28 patch record at {path}") from exc
        if not v28_record_matches(record, version):
            raise ValueError(f"v28 patch record at {path} does not match {version}")
        records.append((path, record))
    if len(records) != 1:
        raise ValueError(f"expected one registered v28 patch record, found {len(records)}")
    return records[0]
