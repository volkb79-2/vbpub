from __future__ import annotations

from pathlib import Path
import json
import re
import subprocess
import tomllib


PATCH_DIR = Path(__file__).parents[1] / "v28-nightly-20261014"
STORAGE_PATCH = PATCH_DIR / "storage.patch"
MIDDLEWARE_PATCH = PATCH_DIR / "middleware.patch"
BOOT_MOUNT_PATCH = PATCH_DIR / "boot-mount.patch"
BOOT_MOUNT_HELPER = PATCH_DIR / "boot_pool_mount.py"
TARGETS = PATCH_DIR / "targets.sha256"


def targets(patch: Path) -> set[str]:
    return set(re.findall(r"^--- a/(.+)$", patch.read_text(), re.MULTILINE))


def section(text: str, target: str) -> str:
    marker = f"--- a/{target}\n"
    start = text.index(marker)
    end = text.find("\n--- a/", start + len(marker))
    return text[start:] if end < 0 else text[start:end]


def additions(text: str) -> str:
    return "\n".join(line[1:] for line in text.splitlines() if line.startswith("+"))


def assert_unified_hunk_counts(text: str) -> None:
    lines = text.splitlines()
    for index, header in enumerate(lines):
        match = re.match(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", header)
        if match is None:
            continue
        expected_old = int(match.group(2) or 1)
        expected_new = int(match.group(4) or 1)
        actual_old = actual_new = 0
        for line in lines[index + 1:]:
            if line.startswith(("@@ ", "--- a/")):
                break
            if line.startswith(" "):
                actual_old += 1
                actual_new += 1
            elif line.startswith("-"):
                actual_old += 1
            elif line.startswith("+"):
                actual_new += 1
            elif line.startswith("\\"):
                continue
            else:
                raise AssertionError(f"malformed unified hunk line after {header!r}: {line!r}")
        assert (actual_old, actual_new) == (expected_old, expected_new), (
            f"unified hunk {header!r} declares {(expected_old, expected_new)} "
            f"lines but contains {(actual_old, actual_new)}"
        )


def test_v28_patch_targets_are_fingerprinted_once() -> None:
    storage_targets = targets(STORAGE_PATCH)
    middleware_targets = targets(MIDDLEWARE_PATCH)
    boot_mount_targets = targets(BOOT_MOUNT_PATCH)
    fingerprint_targets = [
        line.split(None, 1)[1]
        for line in TARGETS.read_text().splitlines()
        if line.strip()
    ]
    assert len(fingerprint_targets) == len(set(fingerprint_targets))
    assert storage_targets.isdisjoint(middleware_targets)
    assert storage_targets.isdisjoint(boot_mount_targets)
    assert middleware_targets.isdisjoint(boot_mount_targets)
    assert storage_targets | middleware_targets | boot_mount_targets <= set(fingerprint_targets)


def test_exact_v28_adapter_manifests_match_the_supported_build_registry() -> None:
    patch_root = PATCH_DIR.parent
    registry = json.loads((patch_root / "supported-builds.json").read_text())
    for directory, version in (
        ("v28-nightly-20261014", "28.0.0-MASTER+20261014-230204"),
        ("v28-nightly-20261027", "28.0.0-MASTER+20261027-175229"),
    ):
        adapter_dir = patch_root / directory
        adapter = json.loads((adapter_dir / "adapter.json").read_text())
        supported = registry["supported_builds"][version]
        import hashlib
        assert adapter == {
            "id": directory,
            "version": version,
            "train": "TrueNAS-28-Nightlies",
            "target_count": supported["target_count"],
            "target_manifest_sha256": hashlib.sha256((adapter_dir / "targets.sha256").read_bytes()).hexdigest(),
        }
        assert adapter["target_count"] == len((adapter_dir / "targets.sha256").read_text().splitlines())
        assert supported["directory"] == directory


def test_stale_october_27_update_metadata_cannot_be_selected_for_october_27_adapter() -> None:
    patch_root = PATCH_DIR.parent
    metadata = patch_root / "v28-nightly-20261027" / "update-artifact.json"
    assert not metadata.exists(), "Oct. 27 adapter directory must not carry stale Oct. 14 update metadata"


def test_v28_unified_patch_hunk_counts_match_the_patch_bodies() -> None:
    for patch in (STORAGE_PATCH, MIDDLEWARE_PATCH, BOOT_MOUNT_PATCH):
        assert_unified_hunk_counts(patch.read_text())


def test_v28_boot_mount_adapter_uses_release_internal_paths_and_runs_before_post_import() -> None:
    patch_text = BOOT_MOUNT_PATCH.read_text()
    helper_text = BOOT_MOUNT_HELPER.read_text()
    target = "middlewared/plugins/pool_/import_pool.py"
    fingerprint_targets = {
        line.split(None, 1)[1]
        for line in TARGETS.read_text().splitlines()
        if line.strip()
    }

    assert targets(BOOT_MOUNT_PATCH) == {target}
    assert target in fingerprint_targets
    assert "from .boot_pool_mount import mount_boot_pool_user_data" in patch_text
    assert "INTERNAL_PATHS" in helper_text
    assert "BOOT_POOL_NAME_VALID" in helper_text
    assert "CONTAINER_DS_NAME" in helper_text
    assert "INTERNAL_DATASETS" not in helper_text
    assert "user_data_root_mount_config_is_valid" in helper_text
    assert "user_data_mountpoint_is_within_root" in helper_text
    assert "service.s.zfs.resource.mount, root" in helper_text
    assert "service.s.zfs.resource.mount, name" in helper_text
    assert "recursive=True" not in helper_text
    assert "filesystem.statfs" in helper_text
    assert "service.s.zfs.resource.list_impl" in helper_text
    assert "service.s.zfs.resource.query_impl" not in helper_text

    boot_import = patch_text.index("mount_boot_pool_user_data(self)")
    post_import = patch_text.index("self.logger.debug('Calling pool.post_import')")
    assert boot_import < post_import


def test_v28_path_validator_checks_real_dataset_mount_ownership() -> None:
    patch_text = STORAGE_PATCH.read_text()
    validator = additions(section(patch_text, "middlewared/async_validators.py"))
    assert "_boot_pool_user_data_root_is_ready" in validator
    assert "filesystem.statfs" in validator
    assert "statfs_field(mounted, 'source') == root" in validator
    assert "statfs_field(mounted, 'dest') == mountpoint" in validator
    assert "from middlewared.plugins.zfs.utils import INTERNAL_PATHS" in validator
    assert "INTERNAL_DATASETS" not in validator
    assert "dataset_query_utils" not in validator
    assert "INTERNAL_PATHS" in validator
    assert "Path resolves to a system-managed boot-pool dataset." in validator
    assert "middleware.services.zfs.resource.list_impl" in validator
    assert "middleware.services.zfs.resource.query_impl" not in validator


def test_v28_statfs_consumers_support_typed_results_without_dictionary_gets() -> None:
    patch_text = STORAGE_PATCH.read_text()
    statfs_consumers = "\n".join((
        additions(section(patch_text, "middlewared/async_validators.py")),
        additions(section(patch_text, "middlewared/plugins/container/info.py")),
        additions(section(patch_text, "middlewared/plugins/pool_/dataset.py")),
        BOOT_MOUNT_HELPER.read_text(),
    ))
    assert "statfs_field(" in statfs_consumers
    assert not re.search(
        r"\b(?:mounted|parent|statfs|current)\.get\(['\"](?:source|dest)['\"]",
        statfs_consumers,
    )
    dataset_ready = additions(section(patch_text, "middlewared/plugins/pool_/dataset.py"))
    assert "mountpoint = (properties.get('mountpoint') or {}).get('value')" in dataset_ready


def test_v28_container_paths_apply_the_altroot_once_in_all_lifecycle_paths() -> None:
    patch_text = STORAGE_PATCH.read_text()
    utils = additions(section(patch_text, "middlewared/plugins/container/utils.py"))
    pull_section = section(patch_text, "middlewared/plugins/container/query_pull_images.py")
    pull = additions(pull_section)
    lifecycle = additions(section(patch_text, "middlewared/plugins/container/lifecycle.py"))

    for helper in ("container_dataset_real_path", "container_instance_real_path"):
        assert helper in utils
    assert "container_dataset_real_path(pool)" in pull
    assert "os.path.join(container_dataset_real_path(pool), suffix)" in pull
    assert "container_instance_real_path(pool, data['name'])" in lifecycle
    assert "os.path.dirname(container_dataset_real_path(pool))" in lifecycle
    assert "CONTAINER_DS_PARENT_DIR" not in lifecycle
    assert "-            mountpoint = f'/mnt{container_dataset_mountpoint(pool)}/{suffix}'" in pull_section


def test_v28_container_pool_choice_requires_a_finite_mounted_user_root() -> None:
    patch_text = STORAGE_PATCH.read_text()
    pool_choices = additions(section(patch_text, "middlewared/plugins/container/info.py"))
    dataset = additions(section(patch_text, "middlewared/plugins/pool_/dataset.py"))

    assert "type(quota) is not int or quota <= 0" in pool_choices
    assert "canmount != 'on'" in pool_choices
    assert "filesystem.statfs" in pool_choices
    assert "statfs_field(mounted, 'source') != ds['name']" in pool_choices
    assert "statfs_field(mounted, 'dest') != mountpoint" in pool_choices
    assert "filesystem.statfs" in dataset
    assert "statfs_field(mounted, 'source') == root" in dataset
    assert "statfs_field(mounted, 'dest') == mountpoint" in dataset
    assert "user_data_root_mount_config_is_valid" in dataset


def test_v28_keeps_the_namespace_root_managed_and_internal_children_hidden() -> None:
    patch_text = STORAGE_PATCH.read_text()
    encryption = additions(section(patch_text, "middlewared/plugins/zfs/encryption_info.py"))
    zfs_utils = additions(section(patch_text, "middlewared/plugins/zfs/utils.py"))
    dataset = additions(section(patch_text, "middlewared/plugins/pool_/dataset.py"))

    assert "is_user_data_namespace_root" in zfs_utils
    assert "is_user_data_namespace_path" in zfs_utils
    assert "CONTAINER_DS_NAME" in zfs_utils
    assert "is_user_data_namespace_path" in encryption
    assert "if path in BOOT_POOL_NAME_VALID:\n        return False" in encryption
    assert "The boot-pool user-data namespace root is managed" in dataset
    assert "INTERNAL_PATHS" in dataset


def test_v28_query_can_traverse_boot_pool_root_without_exposing_protected_children() -> None:
    patch_text = STORAGE_PATCH.read_text()
    encryption = additions(section(patch_text, "middlewared/plugins/zfs/encryption_info.py"))

    assert "if path in BOOT_POOL_NAME_VALID:\n        return False" in encryption
    assert "return not is_user_data_namespace_path(" in encryption
    assert "(*INTERNAL_PATHS, CONTAINER_DS_NAME)" in encryption


def test_v28_container_image_pull_restores_all_internal_bypasses_only() -> None:
    patch_text = STORAGE_PATCH.read_text()
    image_cleanup = additions(section(patch_text, "middlewared/plugins/container/query_pull_images.py"))
    destroy_calls = [
        line.strip() for line in image_cleanup.splitlines()
        if "context.s.zfs.resource.destroy_impl, dataset_name" in line
    ]
    assert len(destroy_calls) == 2
    assert all(call.endswith("bypass=True)") for call in destroy_calls)
    assert image_cleanup.count("bypass=True") == 3
    assert (
        "-                name='image'\n"
        "+                name='image',\n"
        "+                bypass=True,"
    ) in section(patch_text, "middlewared/plugins/container/query_pull_images.py")

    encryption = additions(section(patch_text, "middlewared/plugins/zfs/encryption_info.py"))
    assert "(*INTERNAL_PATHS, CONTAINER_DS_NAME)" in encryption
    assert "middlewared/plugins/zfs/resource_destroy.py" not in targets(STORAGE_PATCH)


def test_v28_public_zfs_guard_protects_container_datasets_on_every_pool() -> None:
    patch_text = STORAGE_PATCH.read_text()
    zfs_utils = section(patch_text, "middlewared/plugins/zfs/utils.py")
    hunks = [
        tuple(map(int, match))
        for match in re.findall(r"^@@ -(\d+),(\d+) \+(\d+),(\d+) @@", zfs_utils, re.MULTILINE)
    ]

    assert len(hunks) == 2
    assert hunks[1][2] - hunks[1][0] == hunks[0][3] - hunks[0][1]
    assert "+    CONTAINER_DS_NAME, is_user_data_namespace_path, is_user_data_namespace_root," in zfs_utils
    assert "middlewared.plugins.container.utils import CONTAINER_DS_NAME" not in zfs_utils
    assert (
        "+                path, BOOT_POOL_NAME_VALID, (*INTERNAL_PATHS, CONTAINER_DS_NAME)"
        in zfs_utils
    )
    assert (
        "+    return len(components) > 1 and "
        "components[1] in (*INTERNAL_PATHS, CONTAINER_DS_NAME)"
    ) in zfs_utils
    assert "middlewared/plugins/zfs/resource_destroy.py" not in targets(STORAGE_PATCH)


def test_v28_container_same_pool_clone_uses_private_bypass_and_mount() -> None:
    patch_text = STORAGE_PATCH.read_text()
    clone_section = section(patch_text, "middlewared/plugins/container/crud.py")
    clone_changes = additions(clone_section)

    assert "ZFSResourceSnapshotCloneQuery" in clone_changes
    assert "self.s.zfs.resource.snapshot.clone_impl" in clone_changes
    assert "dataset=dataset" in clone_changes
    assert "bypass=True" in clone_changes
    assert "self.s.zfs.resource.mount, dataset" in clone_changes
    assert 'self.middleware.call("pool.snapshot.clone"' not in clone_changes
    assert "middlewared/plugins/pool_/snapshot.py" not in targets(STORAGE_PATCH)


def test_v28_patch_replacement_is_guarded_and_has_a_verified_rollback_path() -> None:
    acceptance = Path(__file__).parents[1] / "acceptance"
    remote_script = acceptance / "replace_v28_patch_remote.sh"
    client = (acceptance / "live_v28_patch.py").read_text()
    gate_config = tomllib.loads((Path(__file__).parents[2] / "run-gate.toml").read_text())
    remote = remote_script.read_text()

    syntax = subprocess.run(["bash", "-n", str(remote_script)], text=True, capture_output=True)
    assert syntax.returncode == 0, syntax.stderr
    assert "TRUENAS_V28_PATCH_ACTION" in client
    assert 'action == "replace" and replace_guard != "1"' in client
    assert "replace_v28_patch_remote.sh" in client
    assert 'expected_prior_bundle_sha256=${5:?expected prior bundle SHA-256 is required}' in remote
    assert 'record.get("bundle_sha256") != expected_bundle' in remote
    assert "previous_targets = record.get(\"target_before\")" in remote
    assert "len(previous_targets) not in {14, 15}" in remote
    assert "set(after) != set(previous_targets)" in remote
    assert "rollback-files.json" in remote
    assert "member.isfile()" in remote and "hashlib.sha256(content.read()).hexdigest()" in remote
    assert "patched-files.tar.gz.sha256" in remote
    assert "restore_previous_adapter" in remote and "transaction_committed=1" in remote
    assert "TRUENAS_V28_PATCH_ACTION" in gate_config["environments"]["networked-tester"]["forward_env"]
    assert "TRUENAS_TEST_ALLOW_PATCH_REPLACE" in gate_config["environments"]["networked-tester"]["forward_env"]
    assert "TRUENAS_V28_EXPECTED_PRIOR_BUNDLE_SHA256" in gate_config["environments"]["networked-tester"]["forward_env"]


def test_v28_tcg_adapter_changes_only_the_three_reviewed_middleware_gates() -> None:
    patch_text = MIDDLEWARE_PATCH.read_text()
    expected = {
        "middlewared/plugins/vm/__init__.py",
        "middlewared/plugins/vm/info.py",
        "middlewared/plugins/vm/crud.py",
    }
    assert targets(MIDDLEWARE_PATCH) == expected
    assert "virtualization_backend() is not None" in patch_text
    assert "virtualization_backend() is None" in patch_text
