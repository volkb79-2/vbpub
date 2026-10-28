from __future__ import annotations

from pathlib import Path
import sys

import pytest


TRUENAS_ROOT = Path(__file__).parents[2]
LIVE_UI = TRUENAS_ROOT / "boot-pool-patch" / "acceptance" / "live_ui.py"
ACCEPTANCE = TRUENAS_ROOT / "boot-pool-patch" / "acceptance"
sys.path.insert(0, str(ACCEPTANCE))
from path_b_ui_setup import (  # noqa: E402
    contains_pool_create_request,
    pool_create_request_evidence,
    reserved_partition_four_name,
    resolve_path_b_setup_mode,
    validate_pool_create_topology,
)


@pytest.mark.parametrize(
    ("profile", "value", "expected"),
    [
        ("path-a", "", None),
        ("path-b", "ui-wizard", "ui-wizard"),
        ("path-b", "preprovisioned", "preprovisioned"),
        ("path-b", " preprovisioned ", "preprovisioned"),
    ],
)
def test_path_b_setup_mode_is_explicit(profile: str, value: str, expected: str | None) -> None:
    assert resolve_path_b_setup_mode(profile, value) == expected


@pytest.mark.parametrize(
    ("profile", "value"),
    [
        ("path-b", ""),
        ("path-b", "unknown"),
        ("path-a", "preprovisioned"),
        ("other", ""),
    ],
)
def test_path_b_setup_mode_rejects_missing_or_inapplicable_values(profile: str, value: str) -> None:
    with pytest.raises(ValueError):
        resolve_path_b_setup_mode(profile, value)


@pytest.mark.parametrize(
    "rpc_request",
    [
        {"method": "pool.create", "params": [{"name": "boot-disk-data"}]},
        {"method": "pool.create", "params": [{"name": "unexpected-pool"}]},
    ],
)
def test_preprovisioned_path_b_rejects_any_browser_pool_create_request(rpc_request: dict) -> None:
    assert contains_pool_create_request([rpc_request])


def test_pool_create_guard_ignores_other_methods_and_malformed_rows() -> None:
    assert not contains_pool_create_request([
        {"method": "pool.query", "params": []},
        None,
        "pool.create",
    ])


def test_ui_wizard_derives_partition_name_from_the_selected_disk() -> None:
    assert reserved_partition_four_name("/dev/vda") == "vda4"
    assert reserved_partition_four_name("/dev/nvme0n1") == "nvme0n1p4"
    with pytest.raises(ValueError):
        reserved_partition_four_name("vda")


def test_pool_create_evidence_prefers_this_browser_request() -> None:
    browser = [{
        "method": "pool.create",
        "params": [{"name": "boot-disk-data", "topology": {"data": [
            {"type": "STRIPE", "disks": ["vda4"]},
        ]}}],
    }]
    old_jobs = [{"id": 9, "method": "pool.create", "arguments": [{"name": "boot-disk-data"}]}]
    new_jobs = [{"id": 10, "method": "pool.create", "arguments": [{"name": "boot-disk-data"}]}]
    data, evidence = pool_create_request_evidence(browser, old_jobs, new_jobs, "boot-disk-data")
    assert data["topology"]["data"][0]["disks"] == ["vda4"]
    assert evidence == "browser WebSocket request"


def test_pool_create_evidence_ignores_old_job_and_requires_one_new_job() -> None:
    old = [{"id": 9, "method": "pool.create", "arguments": [{"name": "boot-disk-data"}]}]
    data = {"name": "boot-disk-data", "topology": {"data": [
        {"type": "STRIPE", "disks": ["vda4"]},
    ]}}
    new = [{"id": 9, "method": "pool.create", "arguments": [data]}]
    with pytest.raises(ValueError, match="found 0"):
        pool_create_request_evidence([], old, new, "boot-disk-data")
    data, evidence = pool_create_request_evidence(
        [], old, [{"id": 10, "method": "pool.create", "arguments": [data]}], "boot-disk-data",
    )
    assert data["name"] == "boot-disk-data"
    assert evidence == "new middleware pool.create job id=10"


def test_pool_create_topology_must_use_only_the_expected_reserved_partition() -> None:
    valid = {"data": [{"type": "STRIPE", "disks": ["vda4"]}], "cache": []}
    validate_pool_create_topology(valid, "vda4")
    for invalid in (
        {"data": [{"type": "MIRROR", "disks": ["vda4"]}]},
        {"data": [{"type": "STRIPE", "disks": ["vda4", "vda"]}]},
        {"data": [{"type": "STRIPE", "disks": ["vda4"]}], "log": [
            {"type": "STRIPE", "disks": ["vdb"]},
        ]},
    ):
        with pytest.raises(ValueError):
            validate_pool_create_topology(invalid, "vda4")


def test_path_b_inspects_partition_controls_without_mutating_disk_state() -> None:
    source = LIVE_UI.read_text()
    start = source.index("def inspect_path_b_partition_ui")
    end = source.index("def select_dataset_parent", start)
    helper = source[start:end]

    assert 'storage_url = base_url + "/ui/storage"' in helper
    assert 'page.get_by_role(role, name=re.compile(r"^disks$", re.IGNORECASE))' in helper
    assert helper.count(".click(") == 1
    assert "control.click()" in helper
    assert '"path-b-partition-ui-inventory"' in helper
    assert "partition_mentions" in helper
    assert "disk_related_controls" in helper
    assert 'page.goto(storage_url, wait_until="domcontentloaded", timeout=120_000)' in helper
    assert 'page.get_by_role("button", name=re.compile(r"^create pool$", re.IGNORECASE))' in helper
    assert ".fill(" not in helper
    assert ".select_option(" not in helper
    assert ".check(" not in helper
    assert "target." not in helper
    assert "midclt" not in helper

    assert source.index("inspect_path_b_partition_ui(page, base_url)") < source.index(
        "pool = create_path_b_pool_in_ui(\n                        page, base_url, target, pool_create_requests, expected_partition,",
    )


def test_path_b_pool_ui_requires_browser_create_request_for_reserved_partition() -> None:
    source = LIVE_UI.read_text()
    start = source.index("def create_path_b_pool_in_ui")
    end = source.index("def inspect_path_b_partition_ui", start)
    helper = source[start:end]

    assert "pool_create_requests: list[dict]" in helper
    assert "pool_create_request_evidence(" in helper
    assert 'disk.details", {"type": "UNUSED", "join_partitions": False}' in helper
    assert "unused_names == [expected_partition]" in helper
    assert '"boot-disk-data"' in helper
    assert "validate_pool_create_topology(request.get(\"topology\"), expected_partition)" in helper
    assert "selected = True" not in helper
    assert "jobs_before" in helper and "jobs_after" in helper
    assert "newest job" not in helper
    assert "evidence={evidence_source}" in helper
    assert 'target.query("pool.query"' in helper
    assert 'target.call("pool.create"' not in helper
    assert 'target.run(["lsblk", "-no", "PARTUUID", f"/dev/{expected_partition}"])' in helper
    assert 'expected_vdev = f"/dev/disk/by-partuuid/{partuuid}"' in helper
    assert "require(expected_vdev in vdev" in helper
    assert 'observe_rpc_method(page, "pool.create", log_response=True)' in source


def test_path_b_preprovisioned_ui_checks_the_strict_registered_pool_without_creating_it() -> None:
    source = LIVE_UI.read_text()
    start = source.index("def verify_preprovisioned_path_b_pool")
    end = source.index("def create_path_b_pool_in_ui", start)
    helper = source[start:end]

    assert "provision_path_b(" in helper
    assert "apply=False" in helper
    assert 'result.get("state") == "already_configured"' in helper
    assert '"pool creation remains outside the UI storage acceptance"' in source
    guard = 'not contains_pool_create_request(pool_create_requests)'
    assert source.count(guard) == 2
    assert source.rindex(guard) > source.index(
        'report("shares-ui", "SMB and NFS share pages/actions are available")'
    )
    assert 'target.call("pool.create"' not in source
    assert 'target.call("container.pool_choices"' in source
    assert 'root_mountpoint == f"/mnt/{pool}"' in source


def test_dataset_ui_submission_reports_middleware_validation_dialog() -> None:
    source = LIVE_UI.read_text()
    start = source.index("def create_dataset_in_ui")
    end = source.index("def open_dataset_in_ui", start)
    helper = source[start:end]

    assert 'page.get_by_role("dialog", name="Slide-in form")' in helper
    assert 'name=re.compile(r"Validation Error", re.IGNORECASE)' in helper
    assert "TrueNAS UI rejected dataset creation" in helper


def test_preprovisioned_fresh_check_ignores_the_platform_system_dataset() -> None:
    source = LIVE_UI.read_text()
    # boot-disk-data becomes the system dataset pool, so <pool>/.system/* exists
    # before any UI mutation and is not a test descendant.
    assert "truenas_zfstierd|truesearch|vm|webshare" in source  # known middleware subtree only
    assert 'startswith(f"{pool}/.system/")' not in source
    assert "require(user_names == [pool]," in source
