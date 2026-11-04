from __future__ import annotations

from pathlib import Path
import re
import subprocess
import tomllib


PATCH_ROOT = Path(__file__).parents[1]
PATCH_PATH = PATCH_ROOT / "v27" / "legacy" / "storage.patch"
BOOT_MOUNT_PATCH_PATH = PATCH_ROOT / "v27" / "legacy" / "boot-mount.patch"
ZVOL_BLOCKSIZE_PATCH_PATH = PATCH_ROOT / "v27" / "legacy" / "zvol-blocksize.patch"
TARGETS_PATH = PATCH_ROOT / "v27" / "targets.sha256"
SHARED_PATH = PATCH_ROOT / "shared" / "boot_pool_user_data.py"
RUN_GATE_CONFIG = PATCH_ROOT.parent / "run-gate.toml"
LIVE_V27_PATCH = PATCH_ROOT / "acceptance" / "live_v27_patch.py"
REMOTE_V27_PATCH = PATCH_ROOT / "acceptance" / "apply_v27_patch_remote.sh"
REMOTE_V27_REPAIR = PATCH_ROOT / "acceptance" / "repair_v27_root_create_remote.sh"
REMOTE_V27_BLOCKSIZE_REPAIR = PATCH_ROOT / "acceptance" / "repair_v27_zvol_blocksize_remote.sh"
LIVE_GATE_RUNNER = PATCH_ROOT / "run-live-gate.py"


def patch_section(text: str, target: str) -> str:
    marker = f"--- a/{target}\n"
    start = text.index(marker)
    end = text.find("\n--- a/", start + len(marker))
    return text[start:] if end < 0 else text[start:end]


def additions(text: str) -> str:
    return "\n".join(line[1:] for line in text.splitlines() if line.startswith("+"))


def test_v27_storage_patch_targets_have_original_fingerprints() -> None:
    patch_text = PATCH_PATH.read_text()
    patch_targets = set(re.findall(r"^--- a/(.+)$", patch_text, re.MULTILINE))
    fingerprint_targets = {
        line.split(None, 1)[1]
        for line in TARGETS_PATH.read_text().splitlines()
        if line.strip()
    }
    assert patch_targets <= fingerprint_targets
    assert "middlewared/plugins/zfs/utils.py" in patch_targets


def test_v27_boot_pool_zvol_recommendation_skips_hidden_pool_query() -> None:
    patch_text = ZVOL_BLOCKSIZE_PATCH_PATH.read_text()
    target = "middlewared/plugins/pool_/dataset_info.py"
    assert set(re.findall(r"^--- a/(.+)$", patch_text, re.MULTILINE)) == {target}
    targets = {
        line.split(None, 1)[1]: line.split(None, 1)[0]
        for line in TARGETS_PATH.read_text().splitlines()
        if line.strip()
    }
    assert targets[target] == "437ee090a00fb877c8a030d60646b0e17ed1958b9817d6dcba9a046e95670720"
    assert "if pool == 'boot-pool':" in patch_text
    assert "return '16K'" in patch_text
    assert patch_text.index("if pool == 'boot-pool':") < patch_text.index(
        "pool = await self.middleware.call('pool.query'"
    )
    assert len([line for line in patch_text.splitlines() if line.startswith("+")]) == 6


def test_v27_readonly_inventory_reports_direct_mount_and_patch_target_hashes() -> None:
    source = (PATCH_ROOT / "acceptance" / "live_inspect.py").read_text()
    assert '"middleware_target_source_sha256": middleware_target_hashes' in source
    assert '"direct_user_data_root_properties": user_data_root_properties' in source
    assert '"direct_user_data_root_mount_source": user_data_root_mount_source' in source
    assert '"direct_user_data_root_snapshots": user_data_root_snapshots' in source


def test_v27_zfs_guard_allows_only_ordinary_boot_pool_user_data_descendants() -> None:
    patch_text = PATCH_PATH.read_text()
    target = "middlewared/plugins/zfs/utils.py"
    section = patch_section(patch_text, target)

    assert target in {
        line.split(None, 1)[1]
        for line in TARGETS_PATH.read_text().splitlines()
        if line.strip()
    }
    assert "+        from middlewared.plugins.boot_pool_user_data import (" in section
    assert "+from middlewared.plugins.boot_pool_user_data import (" not in section
    assert "is_user_data_namespace_path" in section
    assert "is_user_data_namespace_root" in section
    assert "or not is_user_data_namespace_path(" in section
    assert "path, BOOT_POOL_NAME_VALID, INTERNAL_PATHS" in section
    assert "components[1] in INTERNAL_PATHS" in section


def test_v27_path_a_root_ui_create_requires_bounded_quota_and_pins_mount_config() -> None:
    patch_text = PATCH_PATH.read_text()
    dataset = additions(patch_section(patch_text, "middlewared/plugins/pool_/dataset.py"))

    assert "if mode == 'UPDATE':" in dataset
    assert "data.get('type') != 'FILESYSTEM'" in dataset
    assert "type(data.get('quota')) is not int or data['quota'] < 1024 ** 3" in dataset
    assert (
        "user_data_root and not is_user_data_namespace_root(data['name'], BOOT_POOL_NAME_VALID)"
        " and is_user_data_namespace_path("
    ) in dataset
    assert "not await self.boot_pool_user_data_root_is_ready(data['name'])" in dataset
    assert "zprops['quota'] = data['quota']" in dataset
    assert 'zprops[\'mountpoint\'] = f"/mnt/{data[\'name\']}"' in dataset
    assert "zprops['canmount'] = 'on'" in dataset
    assert "if is_user_data_namespace_root(data['name'], BOOT_POOL_NAME_VALID):" in dataset
    assert "boot-pool uses mountpoint=none; it is not a parent path to stat." in dataset
    assert "parent_mp = None" in dataset
    assert "mountpoint = (properties.get('mountpoint') or {}).get('value')" in dataset


def test_v27_boot_mount_patch_is_fingerprinted_and_runs_before_pool_post_import() -> None:
    patch_text = BOOT_MOUNT_PATCH_PATH.read_text()
    target = "middlewared/plugins/pool_/import_pool.py"
    fingerprint_targets = {
        line.split(None, 1)[1]
        for line in TARGETS_PATH.read_text().splitlines()
        if line.strip()
    }
    assert target in fingerprint_targets
    assert set(re.findall(r"^--- a/(.+)$", patch_text, re.MULTILINE)) == {target}
    assert "user_data_root_mount_config_is_valid" in patch_text
    assert "user_data_mountpoint_is_within_root" in patch_text
    assert "self.s.zfs.resource.mount, root" in patch_text
    assert "self.s.zfs.resource.mount, name" in patch_text
    assert "recursive=True" not in patch_text
    assert "filesystem.statfs" in patch_text

    storage = PATCH_PATH.read_text()
    assert "@@ -2,9 +2,17 @@" in storage
    assert "+import errno" in storage

    boot_import = patch_text.index("self.mount_boot_pool_user_data()")
    post_import = patch_text.index("self.logger.debug('Calling pool.post_import')")
    assert boot_import < post_import
    assert "except Exception:" in patch_text


def test_v27_fresh_patch_lane_refuses_preconfigured_storage() -> None:
    config = tomllib.loads(RUN_GATE_CONFIG.read_text())
    lane = config["lanes"]["live-v27-patch"]
    networked = config["environments"]["networked-tester"]
    assert lane["environment"] == "networked-tester"
    assert set(lane["required_env"]) <= set(networked["forward_env"])

    controller = LIVE_V27_PATCH.read_text()
    compile(controller, str(LIVE_V27_PATCH), "exec")
    assert "require_fresh_unpatched_target(target, profile, adapter.version)" in controller
    assert 'target.query("container.query"' in controller
    assert 'target.query("vm.query"' in controller
    assert 'target.call("vm.virtualization_details"' in controller
    assert 'zpool list -H -o name,health' in controller
    assert 'target.call("container.pool_choices"' not in controller
    assert 'target.query("pool.query"' not in controller
    assert "bundle_from_environment()" in controller
    assert "TRUENAS_V27_PATCH_BUNDLE_SHA256" in controller
    assert 'action == "reapply-after-update"' in controller
    assert 'action == "uninstall-stale-record"' in controller
    assert "adapter_for_version(version)" in controller
    assert 'get_supported_build(version)' in controller
    installer = REMOTE_V27_PATCH.read_text()
    assert '"0009-boot-pool-zvol-default.patch"' in installer
    assert '"repair_v27_zvol_blocksize_remote.sh"' in controller
    assert '"reapply_v27_after_update_remote.sh"' in controller

    live_runner = LIVE_GATE_RUNNER.read_text()
    assert 'if args.lane == "live-v27-patch"' in live_runner
    assert "make_bundle(" in live_runner
    assert 'child_env["TRUENAS_STORAGE_PROFILE"]' in live_runner
    assert 'child_env["TRUENAS_EXPECTED_VERSION"]' in live_runner
    assert "TRUENAS_V27_PATCH_BUNDLE" in live_runner

    ui = (PATCH_ROOT / "acceptance" / "live_ui.py").read_text()
    assert 'target.call("container.pool_choices"' in ui

    assert "zfs list -H -o name boot-pool/user-data" in installer
    assert "zpool list -H -o name | grep -Fxq boot-disk-data" in installer
    assert "sha256sum --check" in installer
    assert installer.index("patch --dry-run") < installer.index("zfs set readonly=off")
    assert 'series_dir != "series"' in installer
    assert "middlewared/plugins/pool_/dataset_info.py" in installer
    assert "restore_usr_readonly" in installer
    assert '"root_create_guard": "root-excluded-from-descendant-readiness-v1"' in installer
    assert '"root_parent_mountpoint_guard": "root-create-skips-parent-mountpoint-none-v1"' in installer
    assert '"recommended_zvol_blocksize_boot_pool": "boot-pool-defaults-to-16k-v1"' in installer


def test_v27_root_create_repair_is_digest_guarded_and_transactional() -> None:
    repair = REMOTE_V27_REPAIR.read_text()
    assert subprocess.run(["bash", "-n", str(REMOTE_V27_REPAIR)], check=False).returncode == 0
    assert 'storage_profile" = "path-a"' in repair
    assert 'boot-pool/user-data already exists; refusing repair after storage setup' in repair
    assert 'the Path A user-data mountpoint already exists; refusing repair' in repair
    assert 'boot-disk-data exists; refusing repair after storage setup' in repair
    assert 'expected no containers' in repair
    assert 'expected no VMs' in repair
    assert 'root-excluded-from-descendant-readiness-v1' in repair
    assert 'prior_bundle_sha256=${2:?prior v27 bundle SHA-256 is required}' in repair
    assert 'new_bundle_sha256=${3:?replacement v27 bundle SHA-256 is required}' in repair
    assert 'repair_stage=${6:?repair stage is required}' in repair
    assert 'parent_mp = None' in repair
    assert 'root_parent_mountpoint_guard' in repair
    assert 'restore_previous_source' in repair and 'transaction_committed=1' in repair
    assert 'cp --archive -- "$transaction_dir/dataset.py" "$dataset_py"' in repair

    controller = LIVE_V27_PATCH.read_text()
    assert 'action == "repair-root-create"' in controller
    assert 'action == "repair-root-parent-mountpoint"' in controller
    assert 'require_root_parent_path_repair_target' in controller
    assert 'require_root_create_repair_target(target, profile)' in controller
    assert 'prior_bundle_from_environment()' in controller
    assert 'repair_v27_root_create_remote.sh' in controller
    assert 'TRUENAS_V27_EXPECTED_PRIOR_BUNDLE_SHA256' in controller
    assert 'f"boot-pool/user-data already exists; refusing {label} after storage setup"' in controller
    assert 'the Path A user-data mountpoint already exists' in controller

    gate = tomllib.loads(RUN_GATE_CONFIG.read_text())
    forwarded = set(gate["environments"]["networked-tester"]["forward_env"])
    assert {
        "TRUENAS_V27_PATCH_ACTION",
        "TRUENAS_TEST_ALLOW_PATCH_REPAIR",
        "TRUENAS_V27_PRIOR_PATCH_BUNDLE",
        "TRUENAS_V27_EXPECTED_PRIOR_BUNDLE_SHA256",
        "TRUENAS_V27_EXPECTED_PRIOR_DATASET_SHA256",
        "TRUENAS_V27_EXPECTED_PRIOR_DATASET_INFO_SHA256",
    } <= forwarded


def test_v27_post_update_reapply_is_exact_and_transactional() -> None:
    recovery = PATCH_ROOT / "acceptance" / "reapply_v27_after_update_remote.sh"
    recovery_text = recovery.read_text()
    assert subprocess.run(["bash", "-n", str(recovery)], check=False).returncode == 0
    assert '"target_count": len(targets)' in recovery_text
    assert 'manifest != expected' in recovery_text
    assert 'mode=${3:?expected mode is required}' in recovery_text
    assert 'stale v27 install record' in recovery_text
    assert 'sha256sum --check' in recovery_text
    assert 'boot-pool/user-data' in recovery_text
    assert 'zfs mount "$dataset"' in recovery_text
    assert 'restore_previous_source' in recovery_text
    assert 'transaction_committed=1' in recovery_text
    assert 'middlewared.service' in recovery_text

    runner = LIVE_GATE_RUNNER.read_text()
    assert '"reapply-after-update", "uninstall-stale-record"' in runner
    assert 'TRUENAS_TEST_ALLOW_PATCH_REAPPLY' in runner
    assert 'TRUENAS_TEST_ALLOW_PATCH_UNINSTALL' in runner
    assert 'TRUENAS_V27_EXPECTED_PRIOR_BUNDLE_SHA256' in runner

    gate = tomllib.loads(RUN_GATE_CONFIG.read_text())
    forwarded = set(gate["environments"]["networked-tester"]["forward_env"])
    assert {
        "TRUENAS_TEST_ALLOW_PATCH_REAPPLY",
        "TRUENAS_TEST_ALLOW_PATCH_UNINSTALL",
        "TRUENAS_V27_EXPECTED_PRIOR_BUNDLE_SHA256",
    } <= forwarded


def test_v27_latest_nightly_adapter_has_an_exact_version_and_19_target_manifest() -> None:
    import json

    adapter_dir = PATCH_ROOT / "v27-nightly-20261027"
    manifest = json.loads((adapter_dir / "adapter.json").read_text())
    assert manifest == {
        "id": "v27-nightly-20261027",
        "version": "27.0.0-MASTER+20261027-173422",
        "train": "TrueNAS-27-Nightlies",
        "reapply_from": "27.0.0-RC.1",
        "reapply_from_bundle_sha256": "23e7667f4893f26f4861b17edc20e73f00ae2f8b797f9ff95cde446af1f25de1",
        "target_count": 19,
        "target_manifest_sha256": __import__("hashlib").sha256((adapter_dir / "targets.sha256").read_bytes()).hexdigest(),
    }
    targets = [line.split(None, 1) for line in (adapter_dir / "targets.sha256").read_text().splitlines()]
    assert len(targets) == manifest["target_count"]
    assert len({path for _, path in targets}) == manifest["target_count"]
    assert all(len(digest) == 64 and set(digest) <= set("0123456789abcdef") for digest, _ in targets)
    assert any(path == "middlewared/plugins/boot.py" for _, path in targets)
    readme = (adapter_dir / "README.md").read_text()
    assert "19 middleware target hashes" in readme
    assert "boot.attach" in readme and "reserved partition" in readme
    for filename in ("middleware.patch", "storage.patch", "libvirt.patch", "boot-mount.patch", "zvol-blocksize.patch", "ui-pools.patch"):
        assert (adapter_dir / filename).is_file()


def test_v27_bundle_is_deterministic_and_contains_only_reviewed_members() -> None:
    import gzip
    import importlib.util
    import io
    import tarfile

    acceptance = PATCH_ROOT / "acceptance"
    import sys

    sys.path.insert(0, str(acceptance))
    try:
        spec = importlib.util.spec_from_file_location("live_v27_patch_test", acceptance / "live_v27_patch.py")
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        from v27_patch_state import RC1_BUNDLE_SHA256

        first_path, first_digest = module.make_bundle("path-a")
        second_path, second_digest = module.make_bundle("path-a")
    finally:
        sys.path.remove(str(acceptance))

    assert first_path.read_bytes() == second_path.read_bytes()
    assert first_digest == second_digest
    assert first_digest == RC1_BUNDLE_SHA256
    with gzip.GzipFile(fileobj=io.BytesIO(first_path.read_bytes()), mode="rb") as compressed:
        with tarfile.open(fileobj=compressed, mode="r:") as archive:
            members = {member.name for member in archive.getmembers()}
            acceptance_contents = b"\n".join(
                archive.extractfile(name).read()
                for name in members if name.startswith("acceptance/")
            )
    assert "v27/targets.sha256" in members
    assert "acceptance/apply_v27_patch_remote.sh" in members
    assert len(members) == 20
    assert b"23e7667f4893f26f4861b17edc20e73f00ae2f8b797f9ff95cde446af1f25de1" not in acceptance_contents
    assert len([name for name in members if name.startswith("v27/series/") and name.endswith(".patch")]) == 9
    assert "v27/storage.patch" not in members


def test_all_patch_installers_compare_every_target_hash_and_disable_fuzz() -> None:
    scripts = (
        PATCH_ROOT / "acceptance" / "apply_v27_patch_remote.sh",
        PATCH_ROOT / "acceptance" / "reapply_v27_after_update_remote.sh",
        PATCH_ROOT / "acceptance" / "apply_v28_patch_remote.sh",
        PATCH_ROOT / "v28-nightly-20261029" / "apply_v28_patch_remote.sh",
    )
    for path in scripts:
        source = path.read_text()
        assert subprocess.run(["bash", "-n", str(path)], check=False).returncode == 0
        assert "pre-patch source fingerprint mismatch" in source
        assert "expected={expected} actual={actual}" in source
        assert "--fuzz=0" in source
        assert "--reverse" in source


def test_v27_patch_verification_record_uses_defined_atomic_file_operations() -> None:
    source = REMOTE_V27_PATCH.read_text()
    blocks = re.findall(r"<<'PY'\n(.*?)\nPY", source, re.DOTALL)
    final_record_writer = next(block for block in blocks if 'verify_path = Path(verify_record)' in block)

    assert "temporary.chmod(0o600)" in final_record_writer
    assert "temporary.replace(path)" in final_record_writer
    assert "write_private_json(verify_path, verification)" in final_record_writer
    marker_write = 'write_private_json(Path("/root/v27-boot-pool-patch-install.json"), record)'
    assert marker_write in final_record_writer
    assert final_record_writer.index(marker_write) > final_record_writer.index("target_after = {")
    assert "os." not in final_record_writer


def test_v27_nightly_boot_attach_guard_runs_before_boot_format() -> None:
    adapter_dir = PATCH_ROOT / "v27-nightly-20261027"
    patch_text = (adapter_dir / "middleware.patch").read_text()
    section = patch_section(patch_text, "middlewared/plugins/boot.py")
    assert "boot_disk_partition.assert_boot_attach_allowed" in section
    assert section.index("assert_boot_attach_allowed") < section.index("boot.format")
    helper = (PATCH_ROOT / "shared" / "boot_disk_partition.py").read_text()
    assert "cannot use reserved boot-disk partition" in helper


def test_v27_zvol_form_repair_is_digest_bound_and_preserves_the_ui_root() -> None:
    repair = REMOTE_V27_BLOCKSIZE_REPAIR.read_text()
    assert subprocess.run(["bash", "-n", str(REMOTE_V27_BLOCKSIZE_REPAIR)], check=False).returncode == 0
    assert 'profile" = "path-a"' in repair
    assert 'bundle_sha256": prior_bundle' in repair
    assert 'root_create_guard": "root-excluded-from-descendant-readiness-v1"' in repair
    assert 'root_parent_mountpoint_guard": "root-create-skips-parent-mountpoint-none-v1"' in repair
    assert 'recommended_zvol_blocksize boot-pool' in repair
    assert "2147483648\\n/mnt/boot-pool/user-data\\non" in repair
    assert 'zfs list -H -r -o name boot-pool/user-data' in repair
    assert 'find /mnt/boot-pool/user-data -mindepth 1 -print -quit' in repair
    assert '0009-boot-pool-zvol-default.patch' in repair
    assert 'prior_source_sha' in repair
    assert 'install-record.json' in repair
    assert 'restore_previous_source' in repair and 'transaction_committed=1' in repair

    controller = LIVE_V27_PATCH.read_text()
    assert '"repair-zvol-blocksize"' in controller
    assert 'require_zvol_blocksize_repair_target' in controller
    assert 'repair_v27_zvol_blocksize_remote.sh' in controller
    assert 'TRUENAS_V27_EXPECTED_PRIOR_DATASET_INFO_SHA256' in controller


def test_container_path_wrappers_import_the_helpers_they_call() -> None:
    patch_text = PATCH_PATH.read_text()
    section = patch_section(patch_text, "middlewared/plugins/container/utils.py")
    additions = "\n".join(line[1:] for line in section.splitlines() if line.startswith("+"))

    shared = SHARED_PATH.read_text()
    for imported, alias, wrapper in (
        ("container_mountpoint_real_path", "_container_mountpoint_real_path", "container_dataset_real_path"),
        ("container_instance_real_path", "_container_instance_real_path", "container_instance_real_path"),
    ):
        assert re.search(rf"\b{imported}\s+as\s+{alias}\b", additions)
        assert re.search(rf"\b{alias}\s*\(", additions)
        assert re.search(rf"^def {wrapper}\b", additions, re.MULTILINE)
        assert re.search(rf"^def {imported}\b", shared, re.MULTILINE)


def test_container_callers_use_the_normalized_real_paths() -> None:
    patch_text = PATCH_PATH.read_text()
    image = patch_section(patch_text, "middlewared/plugins/container/image.py")
    lifecycle = patch_section(patch_text, "middlewared/plugins/container/lifecycle.py")
    assert "container_dataset_real_path" in image
    assert "container_instance_real_path" in lifecycle


def test_container_start_enforces_the_selected_pools_managed_parent() -> None:
    patch_text = PATCH_PATH.read_text()
    lifecycle = patch_section(patch_text, "middlewared/plugins/container/lifecycle.py")
    additions = "\n".join(line[1:] for line in lifecycle.splitlines() if line.startswith("+"))

    assert "container_dataset_real_path" in additions
    assert 'pool = container["dataset"].split("/")[0]' in additions
    assert "enforce_dir_perms(os.path.dirname(container_dataset_real_path(pool)))" in additions
    assert not any(
        line.startswith("+") and "CONTAINER_DS_PARENT_DIR" in line
        for line in lifecycle.splitlines()
    )


def test_boot_pool_path_validation_avoids_private_middleware_calls() -> None:
    patch_text = PATCH_PATH.read_text()
    async_validator = patch_section(patch_text, "middlewared/async_validators.py")
    container = patch_section(patch_text, "middlewared/plugins/container/container.py")
    assert "middleware.services.zfs.resource.query_impl" in async_validator
    assert "pool.dataset.boot_pool_user_data_root_is_ready'" not in async_validator
    assert "pool.dataset.boot_pool_user_data_root_is_ready'" not in container
    assert "import errno" in async_validator


def test_v27_api_and_boot_paths_share_root_mount_policy() -> None:
    patch_text = PATCH_PATH.read_text()
    dataset = additions(patch_section(patch_text, "middlewared/plugins/pool_/dataset.py"))
    assert "user_data_root_mount_config_is_valid" in dataset


def test_v27_pool_create_schema_data_is_bundled_and_installed_with_the_helper() -> None:
    bundle_builder = LIVE_V27_PATCH.read_text()
    installer = REMOTE_V27_PATCH.read_text()

    assert '"shared" / "pool_create_schema_v27.json"' in bundle_builder
    assert '"shared/pool_create_schema_v27.json"' in bundle_builder
    assert '"$bundle_root/shared/pool_create_schema_v27.json" middlewared/plugins/' in installer
