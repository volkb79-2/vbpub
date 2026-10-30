"""Exact-build adapter and post-update record validation for TrueNAS v27."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Mapping


RC1_VERSION = "27.0.0-RC.1"
NIGHTLY_VERSION = "27.0.0-MASTER+20261027-173422"
RC1_BUNDLE_SHA256 = "07fd9ba7e4df11349525203ac29ee6377b56ff55e1391b7b2c4b72123914895f"
PATH_A_ROOT = "boot-pool/user-data"
PATH_A_MOUNTPOINT = "/mnt/boot-pool/user-data"
MIN_PATH_A_QUOTA = 1024**3
MAX_PATH_A_QUOTA = 2 * 1024**3


@dataclass(frozen=True)
class V27Adapter:
    version: str
    directory: str
    reapply_from: str | None = None
    reapply_from_bundle_sha256: str | None = None


_ADAPTERS = {
    RC1_VERSION: V27Adapter(version=RC1_VERSION, directory="v27"),
    NIGHTLY_VERSION: V27Adapter(
        version=NIGHTLY_VERSION,
        directory="v27-nightly-20261027",
        reapply_from=RC1_VERSION,
        reapply_from_bundle_sha256=RC1_BUNDLE_SHA256,
    ),
}


def adapter_for_version(version: str) -> V27Adapter:
    try:
        return _ADAPTERS[version]
    except KeyError as exc:
        raise ValueError(f"no exact v27 adapter for {version!r}") from exc


def validate_stale_install_record(
    record: Mapping[str, object], *, prior_version: str, expected_bundle_sha256: str,
) -> None:
    expected = {
        "bundle_sha256": expected_bundle_sha256,
        "kvm_device": False,
        "patch": "v27",
        "recommended_zvol_blocksize_boot_pool": "boot-pool-defaults-to-16k-v1",
        "root_create_guard": "root-excluded-from-descendant-readiness-v1",
        "root_parent_mountpoint_guard": "root-create-skips-parent-mountpoint-none-v1",
        "supported": True,
        "version": prior_version,
    }
    if (
        prior_version != RC1_VERSION
        or not re.fullmatch(r"[0-9a-f]{64}", expected_bundle_sha256)
        or dict(record) != expected
    ):
        raise ValueError(f"stale v27 install record does not match the exact prior RC.1 install: {dict(record)!r}")


def validate_pristine_target_hashes(
    actual: Mapping[str, str], expected: Mapping[str, str],
) -> None:
    valid = (
        bool(expected)
        and dict(actual) == dict(expected)
        and all(re.fullmatch(r"[0-9a-f]{64}", digest) for digest in actual.values())
    )
    if not valid:
        raise ValueError(
            "nightly target fingerprints do not match the complete exact-build manifest"
        )


def validate_path_a_root_properties(properties: Mapping[str, object]) -> None:
    quota = properties.get("quota")
    try:
        quota_bytes = int(str(quota))
    except (TypeError, ValueError):
        quota_bytes = 0
    mounted = properties.get("mounted", "no")
    if (
        properties.get("type") != "filesystem"
        or not MIN_PATH_A_QUOTA <= quota_bytes <= MAX_PATH_A_QUOTA
        or properties.get("mountpoint") != PATH_A_MOUNTPOINT
        or properties.get("canmount") != "on"
        or mounted not in {"yes", "no"}
    ):
        raise ValueError(
            "Path A root properties must describe a filesystem with a 1-2 GiB quota, "
            "the pinned mountpoint, canmount=on, and a known mounted state"
        )
