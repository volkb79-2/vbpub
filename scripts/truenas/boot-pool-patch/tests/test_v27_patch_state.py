from __future__ import annotations

import pytest

from v27_patch_state import (
    adapter_for_version,
    validate_path_a_root_properties,
    validate_pristine_target_hashes,
    validate_stale_install_record,
)


RC1 = "27.0.0-RC.1"
NIGHTLY = "27.0.0-MASTER+20261027-173422"
OLD_BUNDLE = "a" * 64


def stale_record(**changes: object) -> dict[str, object]:
    record: dict[str, object] = {
        "bundle_sha256": OLD_BUNDLE,
        "kvm_device": False,
        "patch": "v27",
        "recommended_zvol_blocksize_boot_pool": "boot-pool-defaults-to-16k-v1",
        "root_create_guard": "root-excluded-from-descendant-readiness-v1",
        "root_parent_mountpoint_guard": "root-create-skips-parent-mountpoint-none-v1",
        "supported": True,
        "version": RC1,
    }
    record.update(changes)
    return record


def test_v27_adapters_are_selected_by_exact_build_version() -> None:
    rc1 = adapter_for_version(RC1)
    nightly = adapter_for_version(NIGHTLY)

    assert rc1.directory == "v27"
    assert rc1.reapply_from is None
    assert nightly.directory == "v27-nightly-20261027"
    assert nightly.reapply_from == RC1


def test_unknown_v27_build_has_no_nearest_version_fallback() -> None:
    with pytest.raises(ValueError, match="no exact v27 adapter"):
        adapter_for_version("27.0.0-MASTER+20261028-173422")


def test_reapply_accepts_only_the_exact_stale_rc1_install_record() -> None:
    validate_stale_install_record(
        stale_record(),
        prior_version=RC1,
        expected_bundle_sha256=OLD_BUNDLE,
    )


@pytest.mark.parametrize(
    ("changes", "prior_version", "prior_digest"),
    [
        ({"version": "27.0.0-MASTER+20261027-173422"}, RC1, OLD_BUNDLE),
        ({}, NIGHTLY, OLD_BUNDLE),
        ({}, RC1, "b" * 64),
        ({"root_create_guard": "missing"}, RC1, OLD_BUNDLE),
        ({"unexpected": "field"}, RC1, OLD_BUNDLE),
    ],
)
def test_reapply_rejects_a_record_that_does_not_match_its_prior_install(
    changes: dict[str, object], prior_version: str, prior_digest: str
) -> None:
    with pytest.raises(ValueError, match="stale v27 install record"):
        validate_stale_install_record(
            stale_record(**changes),
            prior_version=prior_version,
            expected_bundle_sha256=prior_digest,
        )


def test_reapply_requires_the_complete_pristine_nightly_target_set() -> None:
    expected = {"middlewared/plugins/pool_/dataset.py": "c" * 64}
    validate_pristine_target_hashes(expected, expected)

    with pytest.raises(ValueError, match="nightly target fingerprints"):
        validate_pristine_target_hashes({}, expected)
    with pytest.raises(ValueError, match="nightly target fingerprints"):
        validate_pristine_target_hashes(
            {**expected, "middlewared/plugins/pool_/pool.py": "d" * 64}, expected
        )
    with pytest.raises(ValueError, match="nightly target fingerprints"):
        validate_pristine_target_hashes(
            {"middlewared/plugins/pool_/dataset.py": "e" * 64}, expected
        )


def test_reapply_accepts_a_bounded_path_a_root_even_if_update_left_it_unmounted() -> None:
    validate_path_a_root_properties(
        {
            "type": "filesystem",
            "quota": str(2 * 1024**3),
            "mountpoint": "/mnt/boot-pool/user-data",
            "canmount": "on",
            "mounted": "no",
        }
    )


@pytest.mark.parametrize(
    "properties",
    [
        {"type": "volume", "quota": str(2 * 1024**3), "mountpoint": "/mnt/boot-pool/user-data", "canmount": "on"},
        {"type": "filesystem", "quota": "0", "mountpoint": "/mnt/boot-pool/user-data", "canmount": "on"},
        {"type": "filesystem", "quota": str(1024**3 - 1), "mountpoint": "/mnt/boot-pool/user-data", "canmount": "on"},
        {"type": "filesystem", "quota": str(2 * 1024**3 + 1), "mountpoint": "/mnt/boot-pool/user-data", "canmount": "on"},
        {"type": "filesystem", "quota": str(2 * 1024**3), "mountpoint": "/mnt/boot-pool/other", "canmount": "on"},
        {"type": "filesystem", "quota": str(2 * 1024**3), "mountpoint": "/mnt/boot-pool/user-data", "canmount": "off"},
        {"type": "filesystem", "quota": str(2 * 1024**3), "mountpoint": "/mnt/boot-pool/user-data", "canmount": "on", "mounted": "unknown"},
    ],
)
def test_reapply_refuses_an_unbounded_or_misconfigured_path_a_root(
    properties: dict[str, str],
) -> None:
    with pytest.raises(ValueError, match="Path A root properties"):
        validate_path_a_root_properties(properties)
