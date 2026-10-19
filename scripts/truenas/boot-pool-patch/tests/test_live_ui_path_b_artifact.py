from __future__ import annotations

from pathlib import Path


TRUENAS_ROOT = Path(__file__).parents[2]
LIVE_UI = TRUENAS_ROOT / "boot-pool-patch" / "acceptance" / "live_ui.py"


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


def test_dataset_ui_submission_reports_middleware_validation_dialog() -> None:
    source = LIVE_UI.read_text()
    start = source.index("def create_dataset_in_ui")
    end = source.index("def open_dataset_in_ui", start)
    helper = source[start:end]

    assert 'page.get_by_role("dialog", name="Slide-in form")' in helper
    assert 'name=re.compile(r"Validation Error", re.IGNORECASE)' in helper
    assert "TrueNAS UI rejected dataset creation" in helper
