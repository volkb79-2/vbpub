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
    resolve_path_b_setup_mode,
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
        "pool = create_path_b_pool_in_ui(page, base_url, target, pool_create_requests)",
    )


def test_path_b_pool_ui_requires_browser_create_request_for_reserved_partition() -> None:
    source = LIVE_UI.read_text()
    start = source.index("def create_path_b_pool_in_ui")
    end = source.index("def inspect_path_b_partition_ui", start)
    helper = source[start:end]

    assert "pool_create_requests: list[dict]" in helper
    assert '"pool.create", "boot-disk-data"' in helper
    assert '"vda4" in selected_devices and "vda" not in selected_devices' in helper
    assert "browser pool.create submitted boot-disk-data with vda4 only" in helper
    assert 'target.query("pool.query"' in helper
    assert 'target.call("pool.create"' not in helper
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
