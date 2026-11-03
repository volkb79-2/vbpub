from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


ACCEPTANCE = Path(__file__).parents[1] / "acceptance"
sys.path.insert(0, str(ACCEPTANCE))
import patch_install_record  # noqa: E402


def make_v28_record(version: str) -> dict:
    targets = patch_install_record.target_names(version)
    helpers = {
        "middlewared/plugins/boot_pool_user_data.py",
        "middlewared/plugins/vm/virtualization_support.py",
        "middlewared/plugins/pool_/boot_pool_mount.py",
    }
    if version == "28.0.0-MASTER+20261029-175210":
        helpers |= {
            "middlewared/plugins/boot_disk_partition.py",
            "middlewared/plugins/pool_create_schema_v28.json",
        }
    return {
        "schema": 1,
        "version": version,
        "storage_profile": "path-a",
        "bundle_sha256": "a" * 64,
        "target_before": {name: "b" * 64 for name in targets},
        "target_after": {name: "c" * 64 for name in targets},
        "helpers": {name: "d" * 64 for name in helpers},
        "backup": "/boot-pool/user-data/.truenas-patch-backup",
    }


@pytest.mark.parametrize(
    ("version", "expected"),
    [
        ("27.0.0-RC.1", ("/root/v27-boot-pool-patch-install.json",)),
        (
            "28.0.0-MASTER+20261029-175210",
            (
                "/mnt/boot-pool/user-data/.truenas-boot-pool-patch-v28.json",
                "/mnt/boot-disk-data/.truenas-boot-pool-patch-v28.json",
            ),
        ),
    ],
)
def test_registered_patch_record_path_is_version_specific(version: str, expected: tuple[str, ...]) -> None:
    assert patch_install_record.manifest_paths(version) == expected


def test_unregistered_release_has_no_patch_record_fallback() -> None:
    with pytest.raises(ValueError, match="no registered patch record"):
        patch_install_record.manifest_paths("28.0.0-MASTER+unknown")


def test_v28_record_selection_requires_one_exact_installed_profile_record() -> None:
    version = "28.0.0-MASTER+20261029-175210"
    path_a, path_b = patch_install_record.manifest_paths(version)
    record = make_v28_record(version)
    assert patch_install_record.select_v28_record({path_a: json.dumps(record)}, version) == (path_a, record)

    with pytest.raises(ValueError, match="found 0"):
        patch_install_record.select_v28_record({}, version)
    with pytest.raises(ValueError, match="found 2"):
        patch_install_record.select_v28_record(
            {path_a: json.dumps(record), path_b: json.dumps(record)}, version,
        )


@pytest.mark.parametrize(
    "version",
    ["28.0.0-MASTER+20261029-175210"],
)
def test_v28_patch_record_requires_exact_version_profile_and_closed_hash_sets(version: str) -> None:
    record = make_v28_record(version)
    assert patch_install_record.v28_record_matches(record, version)

    wrong_version = {**record, "version": "28.0.0-MASTER+unknown"}
    assert not patch_install_record.v28_record_matches(wrong_version, version)

    wrong_profile = {**record, "storage_profile": "invented"}
    assert not patch_install_record.v28_record_matches(wrong_profile, version)

    extra_target = {**record, "target_after": {**record["target_after"], "unexpected": "c" * 64}}
    assert not patch_install_record.v28_record_matches(extra_target, version)

    missing_helper = {**record, "helpers": {}}
    assert not patch_install_record.v28_record_matches(missing_helper, version)

    invalid_digest = {**record, "bundle_sha256": "not-a-sha256"}
    assert not patch_install_record.v28_record_matches(invalid_digest, version)
