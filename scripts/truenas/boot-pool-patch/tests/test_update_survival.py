from __future__ import annotations

import importlib.util
from pathlib import Path
import re
import sys
import tomllib

import pytest


SCRIPT = Path(__file__).parents[1] / "acceptance" / "accept_update_survival.py"


def load_script():
    sys.path.insert(0, str(SCRIPT.parent))
    spec = importlib.util.spec_from_file_location("truenas_update_survival", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_canonical_dump_sorts_records_and_hashes_secret_values() -> None:
    survival = load_script()

    value = {
        "rows": [{"id": 2, "name": "b"}, {"id": 1, "name": "a"}],
        "api_key": "fixture-secret",
        "password": "test-password",
    }

    actual = survival.canonicalize(value)

    assert actual["rows"] == [{"id": 1, "name": "a"}, {"id": 2, "name": "b"}]
    assert actual["api_key"] == {"sha256": survival.sha256_text("fixture-secret")}
    assert actual["password"] == {"sha256": survival.sha256_text("test-password")}
    assert "fixture-secret" not in repr(actual)
    assert "test-password" not in repr(actual)


def test_only_listed_differences_are_accepted_and_unexpected_changes_are_precise() -> None:
    survival = load_script()
    before = {
        "system": {"version": "27.0.0-RC.1", "hostname": "tn-test"},
        "records": {"users": [{"username": "survival-user"}]},
    }
    after = {
        "system": {"version": "27.0.0-MASTER+20261027-173422", "hostname": "tn-test"},
        "records": {"users": []},
    }

    expected, unexpected = survival.compare_snapshots(
        before,
        after,
        expected_paths={"system.version"},
    )

    assert [row.path for row in expected] == ["system.version"]
    assert [row.path for row in unexpected] == ["records.users"]


def test_expected_difference_values_must_match_the_declared_target() -> None:
    survival = load_script()
    before = {"system": {"version": "27.0.0-RC.1", "hostname": "tn-test"}}
    after = {"system": {"version": "27.0.0-MASTER+20261027-173422", "hostname": "tn-test"}}

    expected, unexpected = survival.compare_snapshots(
        before,
        after,
        expected_paths={"system.version"},
        expected_values={"system.version": "27.0.0-MASTER+20261027-173422"},
    )
    assert [row.path for row in expected] == ["system.version"]
    assert unexpected == []

    expected, unexpected = survival.compare_snapshots(
        before,
        {"system": {"version": "27.0.0-RC.2", "hostname": "tn-test"}},
        expected_paths={"system.version"},
        expected_values={"system.version": "27.0.0-MASTER+20261027-173422"},
    )
    assert expected == []
    assert [row.path for row in unexpected] == ["system.version"]


def test_expected_after_state_is_checked_even_when_a_path_did_not_change() -> None:
    survival = load_script()
    manifest = {
        "mode": "vanilla", "profile": "path-a",
        "objects": {"records": {"vm": [7]}},
    }
    expected = survival.expected_difference_values(manifest, "27.0.0-MASTER+20261027-173422")
    snapshot = {
        "system": {"version": "27.0.0-MASTER+20261027-173422"},
        "boot_environment": {"active": "27.0.0-MASTER-20261027-173422"},
        "storage": {"path_a": {"auto_mounted": True}},
        "middleware": {"records": {"selected": {"vms": [{"id": 7, "video_model": "VGA"}]}}},
    }

    with pytest.raises(survival.SurvivalError, match="storage.path_a.auto_mounted"):
        survival.require_expected_snapshot_values(snapshot, expected)

    snapshot["storage"]["path_a"]["auto_mounted"] = False
    survival.require_expected_snapshot_values(snapshot, expected)


def test_update_train_selects_an_available_profile_before_listing_the_feed() -> None:
    survival = load_script()

    class FakeTarget:
        def __init__(self, profile: str) -> None:
            self.config = {"id": 1, "autocheck": True, "profile": profile}
            self.calls: list[tuple[str, object]] = []

        def call(self, method: str, *args, **kwargs):
            self.calls.append((method, args[0] if args else None))
            if method == "update.config":
                return dict(self.config)
            if method == "update.profile_choices":
                return {
                    "DEVELOPER": {"available": True},
                    "EARLY_ADOPTER": {"available": True},
                    "GENERAL": {"available": False},
                }
            if method == "update.update":
                self.config.update(args[0])
                return dict(self.config)
            raise AssertionError(f"unexpected middleware call {method}")

    target = FakeTarget("EARLY_ADOPTER")
    profile = survival.ensure_update_profile(target, "TrueNAS-27-Nightlies")

    assert profile == "DEVELOPER"
    assert target.config["profile"] == "DEVELOPER"
    assert [name for name, _ in target.calls] == [
        "update.config", "update.profile_choices", "update.update", "update.config",
    ]


def test_update_profile_refuses_unavailable_or_unregistered_train() -> None:
    survival = load_script()

    class FakeTarget:
        def call(self, method: str, *args, **kwargs):
            if method == "update.config":
                return {"profile": "GENERAL"}
            if method == "update.profile_choices":
                return {"DEVELOPER": {"available": False}}
            raise AssertionError(f"unexpected middleware call {method}")

    with pytest.raises(survival.SurvivalError, match="not available"):
        survival.ensure_update_profile(FakeTarget(), "TrueNAS-27-Nightlies")
    with pytest.raises(survival.SurvivalError, match="no update profile policy"):
        survival.ensure_update_profile(FakeTarget(), "TrueNAS-27-GA")


def test_record_lists_flatten_by_id_to_keep_upgrade_diffs_narrow() -> None:
    survival = load_script()

    actual = survival.flatten({"records": {"vms": [
        {"id": 7, "name": "fixture-vm", "video_model": "VGA"},
    ]}})

    assert actual == {
        "records.vms[id=7].id": 7,
        "records.vms[id=7].name": "fixture-vm",
        "records.vms[id=7].video_model": "VGA",
    }


def test_middleware_canonicalization_removes_only_volatile_replication_time() -> None:
    survival = load_script()
    before = [{"id": 3, "name": "fixture", "enabled": False,
               "state": {"datetime": {"$date": 1}, "state": "ERROR", "error": "same"}}]
    after = [{"id": 3, "name": "fixture", "enabled": False,
              "state": {"datetime": {"$date": 2}, "state": "ERROR", "error": "same"}}]

    canonical_before = survival.canonical_middleware_value("replication.query", before)
    canonical_after = survival.canonical_middleware_value("replication.query", after)

    assert canonical_before == canonical_after
    after[0]["state"]["error"] = "changed"
    assert survival.canonical_middleware_value("replication.query", after) != canonical_before


def test_runtime_network_nameserver_order_is_normalized_but_membership_is_not() -> None:
    survival = load_script()
    before = {
        "nameserver1": "", "nameserver2": "", "nameserver3": "",
        "state": {"nameserver1": "192.0.2.11", "nameserver2": "192.0.2.12", "nameserver3": ""},
    }
    reordered = {
        **before,
        "state": {"nameserver1": "192.0.2.12", "nameserver2": "192.0.2.11", "nameserver3": ""},
    }
    changed = {
        **before,
        "state": {"nameserver1": "192.0.2.12", "nameserver2": "192.0.2.13", "nameserver3": ""},
    }

    assert survival.canonical_middleware_value("network.configuration.config", before) == \
        survival.canonical_middleware_value("network.configuration.config", reordered)
    assert survival.canonical_middleware_value("network.configuration.config", before) != \
        survival.canonical_middleware_value("network.configuration.config", changed)


def test_network_runtime_gateway_is_ignored_but_persisted_gateway_remains_compared() -> None:
    survival = load_script()
    before = {
        "ipv6gateway": "",
        "state": {"ipv6gateway": "fe80::1"},
    }
    after = {
        "ipv6gateway": "",
        "state": {"ipv6gateway": ""},
    }
    changed_setting = {
        "ipv6gateway": "2001:db8::1",
        "state": {"ipv6gateway": ""},
    }

    assert survival.canonical_middleware_value("network.configuration.config", before) == \
        survival.canonical_middleware_value("network.configuration.config", after)
    assert survival.canonical_middleware_value("network.configuration.config", after) != \
        survival.canonical_middleware_value("network.configuration.config", changed_setting)


def test_container_default_network_migrates_to_a_generated_nic_without_losing_attachment() -> None:
    survival = load_script()
    v27 = {"id": 7, "name": "fixture", "default_network": "truenasbr0", "devices": []}
    v28 = {
        "id": 7, "name": "fixture", "devices": [{
            "id": 9, "container": 7,
            "attributes": {"dtype": "NIC", "mac": "00:a0:98:00:00:01",
                           "nic_attach": "truenasbr0", "trust_guest_rx_filters": False,
                           "type": "VIRTIO"},
        }],
    }

    assert survival.canonical_container_record(v27) == survival.canonical_container_record(v28)
    v28["devices"][0]["attributes"]["nic_attach"] = "different-bridge"
    assert survival.canonical_container_record(v27) != survival.canonical_container_record(v28)


def test_cross_series_stock_v28_omits_only_path_a_middleware_dataset_rows() -> None:
    survival = load_script()
    snapshot = {
        "middleware": {"datasets": {
            "boot-pool/user-data/fixture": {"name": "boot-pool/user-data/fixture"},
            "tank/fixture": {"name": "tank/fixture"},
        }},
        "direct_zfs": {"dataset_properties": {
            "boot-pool/user-data/fixture": {"quota": "1G"},
        }},
        "data_checksums": {"/mnt/boot-pool/user-data/fixture/probe": "a" * 64},
    }

    actual = survival.normalize_survival_snapshot(snapshot, hide_path_a_middleware=True)

    assert "boot-pool/user-data/fixture" not in actual["middleware"]["datasets"]
    assert "tank/fixture" in actual["middleware"]["datasets"]
    assert actual["direct_zfs"] == snapshot["direct_zfs"]
    assert actual["data_checksums"] == snapshot["data_checksums"]


def test_stock_same_series_update_hides_path_a_middleware_but_keeps_direct_zfs_proof() -> None:
    survival = load_script()
    manifest = {"patched_target": "none", "profile": "path-a"}
    snapshot = {
        "middleware": {"datasets": {
            "boot-pool/user-data/fixture": {"name": "boot-pool/user-data/fixture"},
            "tank/fixture": {"name": "tank/fixture"},
        }},
        "direct_zfs": {"dataset_properties": {
            "boot-pool/user-data/fixture": {"quota": "1G", "mountpoint": "/mnt/fixture"},
        }},
        "data_checksums": {"/mnt/boot-pool/user-data/fixture/probe": "b" * 64},
    }

    assert survival.stock_updated_build_hides_path_a_from_middleware(
        manifest, "27.0.0-MASTER+20261029-173417",
    )
    assert not survival.stock_updated_build_hides_path_a_from_middleware(
        {"patched_target": "reapply", "profile": "path-a"}, "27.0.0-MASTER+20261029-173417",
    )
    actual = survival.normalize_survival_snapshot(
        snapshot, hide_path_a_middleware=survival.stock_updated_build_hides_path_a_from_middleware(
            manifest, "27.0.0-MASTER+20261029-173417",
        ),
    )

    assert "boot-pool/user-data/fixture" not in actual["middleware"]["datasets"]
    assert "tank/fixture" in actual["middleware"]["datasets"]
    assert actual["direct_zfs"] == snapshot["direct_zfs"]
    assert actual["data_checksums"] == snapshot["data_checksums"]


def test_dataset_canonicalization_omits_only_volatile_available_counter() -> None:
    survival = load_script()
    before = {"name": "tank/fixture", "quota": {"rawvalue": "1G"},
              "available": {"parsed": 100, "rawvalue": "100", "value": "100 B"}}
    after = {"name": "tank/fixture", "quota": {"rawvalue": "1G"},
             "available": {"parsed": 50, "rawvalue": "50", "value": "50 B"}}

    assert survival.canonical_dataset_record(before) == survival.canonical_dataset_record(after)
    after["quota"]["rawvalue"] = "2G"
    assert survival.canonical_dataset_record(before) != survival.canonical_dataset_record(after)


def test_dataset_accounting_and_reservation_metadata_are_normalized_but_effective_value_is_not() -> None:
    survival = load_script()
    before = {
        "name": "boot-pool/user-data/fixture/record-zvol",
        "refreservation": {"parsed": 70254592, "rawvalue": "70254592", "source": "LOCAL",
                            "source_info": None, "value": "67 MiB"},
        "used": {"parsed": 70254592, "rawvalue": "70254592", "source": "NONE", "value": "67 MiB"},
    }
    after = {
        "name": before["name"],
        "refreservation": {"parsed": None, "rawvalue": "0", "source": "LOCAL",
                            "source_info": None, "value": None},
        "used": {"parsed": 61440, "rawvalue": "61440", "source": "NONE", "value": "60 KiB"},
    }

    assert survival.canonical_dataset_record(before) != survival.canonical_dataset_record(after)
    source_only = {**before, "refreservation": {**before["refreservation"], "source": "DEFAULT"}}
    assert survival.canonical_dataset_record(before) == survival.canonical_dataset_record(source_only)
    formatted_only = {**before, "refreservation": {**before["refreservation"], "value": "about 67 MiB"}}
    assert survival.canonical_dataset_record(before) == survival.canonical_dataset_record(formatted_only)
    accounting_only = {**before, "used": {"parsed": 1, "rawvalue": "1", "source": "NONE", "value": "1 B"}}
    assert survival.canonical_dataset_record(before) == survival.canonical_dataset_record(accounting_only)


def test_dataset_normalization_reaches_nested_records_and_omits_only_volatile_property_fields() -> None:
    survival = load_script()
    before = {
        "name": "boot-pool/user-data/fixture",
        "children": [{
            "name": "boot-pool/user-data/fixture/nested",
            "refreservation": {"parsed": None, "rawvalue": "0", "source": "DEFAULT",
                                "source_info": None, "value": "0 bytes"},
            "used": {"parsed": 100, "rawvalue": "100", "value": "100 B"},
        }],
    }
    after = {
        "name": "boot-pool/user-data/fixture",
        "children": [{
            "name": "boot-pool/user-data/fixture/nested",
            "refreservation": {"parsed": None, "rawvalue": "0", "source": "LOCAL",
                                "source_info": None, "value": None},
            "used": {"parsed": 50, "rawvalue": "50", "value": "50 B"},
        }],
    }

    assert survival.canonical_dataset_record(before) == survival.canonical_dataset_record(after)
    after["children"][0]["refreservation"]["rawvalue"] = "4096"
    assert survival.canonical_dataset_record(before) != survival.canonical_dataset_record(after)


def test_patched_rc1_to_registered_nightly_has_one_exact_reservation_migration() -> None:
    survival = load_script()
    manifest = {
        "before_version": "27.0.0-RC.1", "mode": "patched", "profile": "both",
        "objects": {"datasets": ["boot-pool/user-data/fixture/record-zvol"], "records": {"vm": []}},
        "baseline": {
            "middleware": {"datasets": {
                "boot-pool/user-data/fixture/record-zvol": {
                    "refreservation": {"parsed": 70254592, "rawvalue": "70254592"},
                    "volsize": {"rawvalue": "67108864"},
                    "volblocksize": {"rawvalue": "16384"},
                },
            }},
            "patch": {"install_record": {
                "version": "27.0.0-RC.1",
                "bundle_sha256": "23e7667f4893f26f4861b17edc20e73f00ae2f8b797f9ff95cde446af1f25de1",
            }},
        },
    }
    manifest["patched_target"] = "reapply"
    expected = survival.expected_differences(
        mode="patched", profile="both", dataset_names=manifest["objects"]["datasets"],
        before_version=manifest["before_version"], expected_after="27.0.0-MASTER+20261029-173417",
        baseline=manifest["baseline"],
        patched_target="reapply",
    )
    values = survival.expected_difference_values(manifest, "27.0.0-MASTER+20261029-173417")

    reservation_path = (
        'middleware.datasets.boot-pool/user-data/fixture.'
        'children[id="boot-pool/user-data/fixture/record-zvol"].refreservation.rawvalue'
    )
    assert reservation_path in expected
    assert values[reservation_path] == "0"
    direct_reservation_path = (
        "middleware.datasets.boot-pool/user-data/fixture/record-zvol.refreservation.rawvalue"
    )
    assert direct_reservation_path in expected
    assert values[direct_reservation_path] == "0"
    assert not any(path.endswith("record-zvol.volsize.rawvalue") for path in expected)
    assert values["patch.install_record.version"] == "27.0.0-MASTER+20261029-173417"
    assert values["patch.install_record.bundle_sha256"] == \
        "60b3696c44768f9c6958791e8b46c2a205d5daede9c8ab29b23228c141b58e76"

    vanilla = survival.expected_differences(
        mode="vanilla", profile="both", dataset_names=manifest["objects"]["datasets"],
        before_version=manifest["before_version"], expected_after="27.0.0-MASTER+20261029-173417",
        baseline=manifest["baseline"],
    )
    assert not any("refreservation" in path for path in vanilla)


def test_patched_v27_to_v28_keeps_old_install_marker_and_expects_unpatched_mount_state() -> None:
    survival = load_script()
    before_version = "27.0.0-MASTER+20261027-173422"
    before_bundle = "489be0c005e4e64cff1cf81077418546b8c9a03cd77bfafa2711048a52a1c0fe"
    manifest = {
        "before_version": before_version, "mode": "patched", "profile": "both",
        "objects": {"datasets": [], "records": {"vm": []}},
        "baseline": {
            "patch": {"install_record": {
                "version": before_version, "bundle_sha256": before_bundle,
            }},
            "storage": {
                "path_a": {"exists": True, "auto_mounted": True},
                "path_b": {"exists": True, "auto_mounted": True},
            },
        },
    }

    expected = survival.expected_differences(
        mode="patched", profile="both", before_version=before_version,
        expected_after="28.0.0-MASTER+20261029-175210", baseline=manifest["baseline"],
    )
    values = survival.expected_difference_values(manifest, "28.0.0-MASTER+20261029-175210")

    assert "patch.install_record.version" not in expected
    assert values["patch.install_record.version"] == before_version
    assert values["patch.install_record.bundle_sha256"] == before_bundle
    assert values["storage.path_a.auto_mounted"] is False
    assert values["storage.path_b.auto_mounted"] is True


def test_snapshot_normalization_applies_to_legacy_baseline_and_keeps_replication_error() -> None:
    survival = load_script()
    snapshot = {
        "middleware": {
            "configuration": {"replication.query": {"status": "available", "value": [
                {"id": 8, "state": {"datetime": {"$date": 1}, "state": "ERROR",
                                     "error": "task missing"}},
            ]}},
            "datasets": {"tank/fixture": {"name": "tank/fixture", "available": {"rawvalue": "12"},
                                           "quota": {"rawvalue": "1G"}}},
        },
    }

    actual = survival.normalize_survival_snapshot(snapshot)

    row = actual["middleware"]["configuration"]["replication.query"]["value"][0]
    assert row["state"] == {"state": "ERROR", "error": "task missing"}
    assert actual["middleware"]["datasets"]["tank/fixture"] == {
        "name": "tank/fixture", "quota": {"rawvalue": "1G"},
    }
    assert snapshot["middleware"]["configuration"]["replication.query"]["value"][0]["state"]["datetime"]


def test_snapshot_normalization_canonicalizes_runtime_network_nameservers_in_old_and_new_baselines() -> None:
    survival = load_script()
    first = {"middleware": {"configuration": {
        "network.configuration.config": {"status": "available", "value": {
            "state": {"nameserver1": "192.0.2.11", "nameserver2": "192.0.2.12", "nameserver3": ""},
        }},
    }}}
    reordered = {"middleware": {"configuration": {
        "network.configuration.config": {"status": "available", "value": {
            "state": {"nameserver1": "192.0.2.12", "nameserver2": "192.0.2.11", "nameserver3": ""},
        }},
    }}}
    changed = {"middleware": {"configuration": {
        "network.configuration.config": {"status": "available", "value": {
            "state": {"nameserver1": "192.0.2.12", "nameserver2": "192.0.2.13", "nameserver3": ""},
        }},
    }}}

    assert survival.normalize_survival_snapshot(first) == survival.normalize_survival_snapshot(reordered)
    assert survival.normalize_survival_snapshot(first) != survival.normalize_survival_snapshot(changed)


def test_fixture_checksum_mounts_are_restored_even_when_read_fails() -> None:
    survival = load_script()

    class FakeTarget:
        def __init__(self) -> None:
            self.mounted = {"tank/root": False, "tank/root/nested": False, "tank/clone": True}
            self.calls: list[str] = []

        def shell(self, command: str, *, timeout: int, check: bool) -> str:
            self.calls.append(command)
            name = re.search(r"tank/[A-Za-z0-9_/-]+", command)
            dataset = name.group(0) if name else ""
            if "zfs list -H -o name" in command:
                return f"{dataset}\n__TRUENAS_EXIT__0"
            if "zfs get -H -o value mounted" in command:
                value = "yes" if self.mounted[dataset] else "no"
                return f"{value}\n__TRUENAS_EXIT__0"
            if "zfs mount " in command:
                self.mounted[dataset] = True
                return "__TRUENAS_EXIT__0"
            if "zfs unmount " in command:
                self.mounted[dataset] = False
                return "__TRUENAS_EXIT__0"
            raise AssertionError(f"unexpected remote command: {command}")

    target = FakeTarget()
    properties = {
        "tank/root": {"type": "filesystem"},
        "tank/root/nested": {"type": "filesystem"},
        "tank/clone": {"type": "filesystem"},
        "tank/volume": {"type": "volume"},
    }
    initial_mounts = survival.fixture_mount_states(target, list(properties), properties)
    assert initial_mounts == {"tank/root": False, "tank/root/nested": False, "tank/clone": True}
    # Simulate an inventory query mounting data after the initial state read.
    target.mounted["tank/root"] = True
    target.mounted["tank/root/nested"] = True

    with pytest.raises(RuntimeError, match="simulated checksum failure"):
        with survival.temporary_fixture_mounts(target, initial_mounts):
            assert target.mounted == {
                "tank/root": True, "tank/root/nested": True, "tank/clone": True,
            }
            raise RuntimeError("simulated checksum failure")

    assert target.mounted == {"tank/root": False, "tank/root/nested": False, "tank/clone": True}
    unmounts = [call for call in target.calls if "zfs unmount" in call]
    assert "zfs unmount tank/root/nested" in unmounts[0]
    assert "zfs unmount tank/root" in unmounts[1]
    assert not any(
        any(operation in call for operation in ("zfs get", "zfs mount", "zfs unmount"))
        and "tank/volume" in call
        for call in target.calls
    )


def test_manifest_rejects_missing_checksums_and_path_escape(tmp_path: Path) -> None:
    survival = load_script()
    manifest = {
        "schema_version": 1,
        "fixture_id": "20261030-abcdef1234",
        "before_version": "27.0.0-RC.1",
        "objects": {
            "files": [{"path": "/mnt/boot-pool/user-data/test.bin", "sha256": "a" * 64}],
            "zvols": [],
            "datasets": ["boot-pool/user-data/test"],
        },
    }
    path = tmp_path / "manifest.json"
    path.write_text(__import__("json").dumps(manifest), encoding="utf-8")
    assert survival.validate_manifest(path)["fixture_id"] == manifest["fixture_id"]

    manifest["objects"]["files"][0]["sha256"] = "wrong"
    path.write_text(__import__("json").dumps(manifest), encoding="utf-8")
    with pytest.raises(survival.SurvivalError, match="SHA-256"):
        survival.validate_manifest(path)

    manifest["objects"]["files"][0].update(path="/mnt/../../etc/passwd", sha256="a" * 64)
    path.write_text(__import__("json").dumps(manifest), encoding="utf-8")
    with pytest.raises(survival.SurvivalError, match="unsafe fixture path"):
        survival.validate_manifest(path)


def test_shell_status_reads_the_appended_remote_exit_marker_for_expected_refusals() -> None:
    survival = load_script()

    class FakeTarget:
        def shell(self, command: str, *, timeout: int, check: bool) -> str:
            assert "__TRUENAS_EXIT__" in command
            assert timeout == 45
            assert check is False
            # Target.shell strips leading/trailing whitespace from remote stdout.
            return "__TRUENAS_EXIT__1"

    status, output = survival.shell_status(FakeTarget(), "test -f /root/patch.json")

    assert status == 1
    assert output == ""


def test_known_expected_differences_are_explicit_by_update_mode() -> None:
    survival = load_script()

    vanilla = survival.expected_differences(mode="vanilla", profile="path-a")
    patched = survival.expected_differences(mode="patched", profile="path-a")
    reapplied = survival.expected_differences(
        mode="patched", profile="path-a", expected_after="27.0.0-MASTER+20261029-173417",
        patched_target="reapply",
    )

    assert "system.version" in vanilla
    assert "boot_environment.active" in vanilla
    assert "storage.path_a.auto_mounted" in vanilla
    assert "system.version" in patched
    assert "boot_environment.active" in patched
    assert "storage.path_a.auto_mounted" in patched
    assert "patch.install_record.version" not in patched
    assert "patch.install_record.bundle_sha256" not in patched
    assert "patch.install_record.version" in reapplied
    assert "patch.install_record.bundle_sha256" in reapplied


def test_path_a_root_resolves_to_its_imported_pool() -> None:
    survival = load_script()
    roots = [
        ("path-a", "boot-pool/user-data", "/mnt/boot-pool/user-data"),
        ("path-b", "boot-disk-data", "/mnt/boot-disk-data"),
        ("nonreserved", "tank", "/mnt/tank"),
    ]

    survival.validate_root_pools(roots, {"boot-pool", "boot-disk-data", "tank"})

    with pytest.raises(survival.SurvivalError, match="not imported"):
        survival.validate_root_pools(roots, {"boot-pool", "tank"})


def test_storage_root_existence_uses_dataset_name_not_mountpoint() -> None:
    survival = load_script()

    class FakeTarget:
        def __init__(self) -> None:
            self.commands: list[str] = []

        def shell(self, command: str, *, timeout: int = 45, check: bool = True) -> str:
            self.commands.append(command)
            return "boot-pool/user-data\n__TRUENAS_EXIT__0"

    target = FakeTarget()

    assert survival.storage_root_exists(target, "path_a") is True
    assert len(target.commands) == 1
    assert "zfs list" in target.commands[0]
    assert "boot-pool/user-data" in target.commands[0]
    assert "/mnt/boot-pool/user-data" not in target.commands[0]


def test_zvols_are_checked_as_devices_not_mounted_as_filesystems() -> None:
    survival = load_script()

    assert survival.dataset_mount_required({"type": "filesystem"}) is True
    assert survival.dataset_mount_required({"type": "volume"}) is False
    with pytest.raises(survival.SurvivalError, match="unknown ZFS dataset type"):
        survival.dataset_mount_required({"type": "snapshot"})


def test_fixture_user_home_uses_a_real_pool_query_row_and_skips_synthetic_boot_pool() -> None:
    survival = load_script()

    home = survival.fixture_user_home(
        [
            {"path": "/mnt/boot-pool/user-data/fixture/nested/probe-path-a.bin", "role": "path-a"},
            {"path": "/mnt/boot-disk-data/fixture/nested/probe-path-b.bin", "role": "path-b"},
            {"path": "/mnt/tank/fixture/nested/probe-nonreserved.bin", "role": "nonreserved"},
        ],
        "20261030-abcdef1234",
        [
            {"name": "boot-pool", "path": "/"},
            {"name": "boot-disk-data", "path": "/mnt/boot-disk-data"},
            {"name": "tank", "path": "/mnt/tank"},
        ],
    )

    assert home == "/mnt/boot-disk-data/fixture/nested/account-home-abcdef1234"


def test_fixture_user_home_refuses_when_only_synthetic_boot_pool_is_available() -> None:
    survival = load_script()

    with pytest.raises(survival.SurvivalError, match="no fixture file is inside a real pool.query path"):
        survival.fixture_user_home(
            [{"path": "/mnt/boot-pool/user-data/fixture/nested/probe-path-a.bin", "role": "path-a"}],
            "20261030-abcdef1234",
            [{"name": "boot-pool", "path": "/"}],
        )


def test_survival_api_key_uses_a_privileged_existing_account() -> None:
    survival = load_script()

    assert survival.survival_api_key_data("20261030-abcdef1234") == {
        "name": "update-survival-abcdef1234", "username": "root",
    }


def test_scheduled_task_root_uses_real_pool_query_data_not_synthetic_boot_pool() -> None:
    survival = load_script()

    selected = survival.middleware_root_dataset(
        [
            "boot-pool/user-data/update-survival-abcdef1234",
            "boot-disk-data/update-survival-abcdef1234",
        ],
        [
            {"name": "boot-pool", "path": "/"},
            {"name": "boot-disk-data", "path": "/mnt/boot-disk-data"},
        ],
        "20261030-abcdef1234",
    )

    assert selected == "boot-disk-data/update-survival-abcdef1234"


def test_middleware_record_fixture_prefers_real_data_pool_zvol() -> None:
    survival = load_script()

    assert survival.preferred_middleware_pool_role([
        {"role": "path-a"}, {"role": "path-b"},
    ]) == "path-b"
    assert survival.preferred_middleware_pool_role([{"role": "path-a"}]) == "path-a"


def test_local_replication_record_binds_its_disabled_snapshot_task() -> None:
    survival = load_script()

    actual = survival.replication_create_data(
        "20261030-abcdef1234", "boot-disk-data/source", "boot-disk-data/target", 42,
    )

    assert actual["periodic_snapshot_tasks"] == [42]
    assert actual["source_datasets"] == ["boot-disk-data/source"]
    assert actual["enabled"] is False


def test_vm_record_fixture_is_stopped_deterministic_and_keeps_zvol_device() -> None:
    survival = load_script()
    fixture_id = "20261030-abcdef1234"

    first = survival.vm_configuration_data(fixture_id)
    second = survival.vm_configuration_data(fixture_id)
    device = survival.vm_disk_device_data(19, "/dev/zvol/boot-disk-data/survival")

    assert first == second
    assert first["name"] == "survivalvmabcdef1234"
    assert first["autostart"] is False
    assert first["uuid"]
    assert device == {
        "vm": 19, "order": 1000,
        "attributes": {
            "dtype": "DISK", "path": "/dev/zvol/boot-disk-data/survival",
            "type": "VIRTIO", "create_zvol": False,
        },
    }


def test_vm_create_refusal_classifier_only_accepts_missing_virtualization() -> None:
    survival = load_script()

    assert survival.vm_create_refused_only_for_missing_virtualization(
        survival.RemoteError("[EINVAL] vm_create: This system does not support virtualization.")
    )
    assert not survival.vm_create_refused_only_for_missing_virtualization(
        survival.RemoteError("[EINVAL] vm_create: invalid device configuration")
    )


def test_fixture_snapshot_query_uses_v27_data_object_and_planted_dataset_paths() -> None:
    survival = load_script()
    calls = []

    class FakeTarget:
        def call(self, method: str, data: dict[str, object], *, timeout: int) -> list[dict[str, object]]:
            calls.append((method, data, timeout))
            return [
                {"name": "pool/update-survival-abc/nested@survival-abc"},
                {"name": "pool/unrelated@keep"},
            ]

    result = survival.fixture_snapshot_query(
        FakeTarget(), {"zfs.resource.snapshot.query"},
        {"datasets": ["pool/update-survival-abc", "pool/update-survival-abc/nested",
                      "pool/update-survival-clone-abc"], "snapshot_names": ["survival-abc"]},
    )

    assert calls == [(
        "zfs.resource.snapshot.query",
        {"paths": ["pool/update-survival-abc/nested"], "recursive": False},
        120,
    )]
    assert result == [{"name": "pool/update-survival-abc/nested@survival-abc"}]


def test_zfs_dataset_exists_accepts_exact_row_with_existing_descendants() -> None:
    survival = load_script()

    class FakeTarget:
        def shell(self, command: str, *, timeout: int, check: bool) -> str:
            assert "zfs list -H -o name" in command
            return "boot-pool/user-data\nboot-pool/user-data/old-fixture\n__TRUENAS_EXIT__0"

    assert survival.zfs_dataset_exists(FakeTarget(), "boot-pool/user-data")


def test_direct_zfs_dataset_inventory_requires_every_requested_property() -> None:
    survival = load_script()
    property_names = "type,mountpoint,compression,quota,refquota,recordsize,volsize".split(",")

    class FakeTarget:
        def run(self, argv: list[str], *, timeout: int) -> str:
            assert argv[:3] == ["zfs", "get", "-H"]
            assert timeout == 60
            return "\n".join(f"{name}\tvalue-{name}" for name in property_names)

    actual = survival.direct_dataset_properties(FakeTarget(), ["boot-disk-data/test"])

    assert actual["boot-disk-data/test"] == {name: f"value-{name}" for name in property_names}


def test_mutating_host_guard_accepts_only_disposable_v1001() -> None:
    survival = load_script()

    survival.require_v1001_target("v1001.vxxu.de", 22)
    with pytest.raises(survival.SurvivalError, match="v1001.vxxu.de:22"):
        survival.require_v1001_target("other.example", 22)
    with pytest.raises(survival.SurvivalError, match="v1001.vxxu.de:22"):
        survival.require_v1001_target("v1001.vxxu.de", 2222)


def test_update_feed_selection_requires_exact_latest_build_and_manifest() -> None:
    survival = load_script()
    rows = [
        {"train": "TrueNAS-27-Nightlies", "version": {
            "version": "27.0.0-MASTER+20261026-173525",
            "manifest": {"version": "27.0.0-MASTER+20261026-173525", "filename": "old.update",
                         "filesize": 100, "checksum": "a" * 64},
        }},
        {"train": "TrueNAS-27-Nightlies", "version": {
            "version": "27.0.0-MASTER+20261027-173422",
            "manifest": {"version": "27.0.0-MASTER+20261027-173422", "filename": "latest.update",
                         "filesize": 200, "checksum": "b" * 64},
        }},
        {"train": "TrueNAS-28-Nightlies", "version": {
            "version": "28.0.0-MASTER+20261029-175210",
            "manifest": {"version": "28.0.0-MASTER+20261029-175210", "filename": "v28.update",
                         "filesize": 300, "checksum": "c" * 64},
        }},
    ]

    selected = survival.select_update_manifest(
        rows, train="TrueNAS-27-Nightlies", expected_after="27.0.0-MASTER+20261027-173422"
    )

    assert selected["filename"] == "latest.update"
    assert selected["checksum"] == "b" * 64
    with pytest.raises(survival.SurvivalError, match="not the newest available"):
        survival.select_update_manifest(
            rows, train="TrueNAS-27-Nightlies", expected_after="27.0.0-MASTER+20261026-173525"
        )
    with pytest.raises(survival.SurvivalError, match="no available update manifests"):
        survival.select_update_manifest([], train="TrueNAS-27-Nightlies", expected_after="27.0.0-MASTER+20261027-173422")


def test_update_survival_steps_are_registered_and_forward_their_environment() -> None:
    truenas_root = Path(__file__).parents[2]
    config = tomllib.loads((truenas_root / "run-gate.toml").read_text(encoding="utf-8"))
    forwarded = set(config["environments"]["networked-tester"]["forward_env"])
    assert {
        "TRUENAS_SURVIVAL_MODE", "TRUENAS_SURVIVAL_PROFILE", "TRUENAS_SURVIVAL_MANIFEST",
        "TRUENAS_SURVIVAL_REPORT", "TRUENAS_SURVIVAL_UPDATE_RECORD",
        "TRUENAS_SURVIVAL_EXPECTED_AFTER", "TRUENAS_SURVIVAL_SSH_PUBLIC_KEY_FILE",
        "TRUENAS_UPDATE_TRAIN",
    } <= forwarded
    for suffix in ("plant", "update", "verify"):
        lane = config["lanes"][f"live-update-survival-{suffix}"]
        assert lane["environment"] == "networked-tester"
        assert "accept-update-survival.py" in " ".join(lane["argv"])
