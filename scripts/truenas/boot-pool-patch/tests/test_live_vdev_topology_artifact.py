from __future__ import annotations

from pathlib import Path


TRUENAS_ROOT = Path(__file__).parents[2]
TOPOLOGY = TRUENAS_ROOT / "boot-pool-patch" / "acceptance" / "live_vdev_topology.py"


def test_auxiliary_topology_covers_single_disk_and_raidz1_api_roles() -> None:
    source = TOPOLOGY.read_text()

    assert 'create_pool(target, pool_single, "STRIPE", [extra_disk1])' in source
    assert 'create_pool(target, pool_raidz, "RAIDZ1", raidz_disks)' in source
    for role in ("cache", "log", "special"):
        assert f'"{role}": [' in source
        assert f'for role in ("cache", "log", "special")' in source
    assert '"type": "MIRROR"' in source
    assert '"type": "STRIPE"' in source
    assert 'target.call("pool.update", pool["id"], request' in source
    assert 'target.shell(f"zpool status -P {name}"' in source
    assert "exercise_metadata_and_sync_write(" in source
    assert "zpool iostat" in source
    assert 'target.call("pool.dataset.update", dataset, {"sync": "ALWAYS"}' in source


def test_ui_probe_checks_role_controls_and_reports_zvol_selectability() -> None:
    source = TOPOLOGY.read_text()
    probe_start = source.index("def inspect_aux_vdev_selectors(")
    probe_end = source.index("\ndef target_preflight(", probe_start)
    probe = source[probe_start:probe_end]

    assert 'expected_roles = {"cache", "log", "special"}' in source
    assert "missing_roles = sorted(expected_roles - role_steps_seen)" in source
    assert 'report("ui-aux-vdev-selectors"' in source
    assert 'report(\n            "ui-zvol-vdev-selection"' in source
    assert "did not display the UI-created " in source
    assert 'create_zvol_in_ui(' in source
    assert 'row.get("serial") == "TNEXTRA004"' in probe
    assert 'name=re.compile(r"manual disk selection", re.IGNORECASE)' in probe
    assert 'name=re.compile(r"^name$", re.IGNORECASE)' in probe
    assert 'wizard_steps = ("general info", "data", "log", "spare", "cache", "special", "dedup", "review")' in probe
    assert 'name=re.compile(r"^layout$", re.IGNORECASE)' in probe
    assert 'name=re.compile(r"^stripe$", re.IGNORECASE)' in probe
    assert "layout_before = layout.evaluate(" in probe
    assert "layout_after = layout.evaluate(" in probe
    assert 'for role_step in ("log", "cache", "special")' in probe
    assert 'name=re.compile(rf"^{role_step}\\s+\\(optional\\)$", re.IGNORECASE)' in probe
    assert "def active_step(snapshot: str) -> str | None:" in probe
    assert "step = active_step(snapshot)" in probe
    assert "candidate.check()" in probe
    assert 'report("ui-pool-wizard-step"' in source
    assert 'ui_probe = inspect_aux_vdev_selectors(page, base_url, zvol_names, disk_responses)' in source
    assert 'if missing_ui_roles != []:' in source
    assert "def pool_create_control(container):" in source
    assert 'for role in ("button", "link")' in source
    assert "the browser disk inventory therefore offers no boot-pool zvol" in source
    assert 'if ui_probe.get("wizard_role_labels_missing") != []:' in source
    assert "stopping before API pool mutations" in source


def test_zvol_device_and_single_disk_and_raidz1_api_attempts_are_reported() -> None:
    source = TOPOLOGY.read_text()

    assert 'zfs_property(target, zvol, "volmode")' in source
    assert 'report("zvol-device"' in source
    assert 'stat -Lc \'%F:%t:%T\'' in source
    assert 'for pool, pool_name, topology, log_device, special_devices in (' in source
    assert 'topology_errors.append(f"{pool_name}: {exc}")' in source
    assert 'api_refusals.append(f"{pool_name}: {exc}")' in source
    assert 'report("pool-topology-api-refusal"' in source
    assert 'if topology_errors:' in source
    assert 'report("topology-capability-gap"' in source
    assert 'The following disks were not found in system' in source
    assert 'all(not role_state[role] for role in role_state)' in source
    assert 'require(not final_errors, "; ".join(final_errors))' in source
    assert 'report("ui-pool-inventory-navigation-failure"' in source
    assert "were refused safely and remain an open capability requirement" in source


def test_disk_query_accepts_kernel_name_without_a_dev_prefix() -> None:
    source = TOPOLOGY.read_text()

    assert 'row["devname"].startswith("/dev/")' not in source
    assert "disk.query did not return a kernel device name" in source
    assert 'extra_disk1 = disks["TNEXTRA001"]["name"]' in source
    assert 'raidz_disks = [disks[serial]["name"]' in source


def test_path_a_topology_child_is_created_under_ui_selected_root() -> None:
    source = TOPOLOGY.read_text()
    root = source.index("create_path_a_root_in_ui(page, base_url, target, dataset_requests)")
    parent = source.index('parent = select_dataset_parent(page, base_url, "path-a")', root)
    child = source.index("created = create_dataset_in_ui(page, parent,", parent)
    cleanup_guard = source.index("created_dataset = True", child)

    assert root < parent < child < cleanup_guard
