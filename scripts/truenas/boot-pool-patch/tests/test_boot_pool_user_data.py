from __future__ import annotations

import importlib.util
from types import SimpleNamespace
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "shared" / "boot_pool_user_data.py"
spec = importlib.util.spec_from_file_location("boot_pool_user_data", MODULE_PATH)
if spec is None or spec.loader is None:
    raise RuntimeError(f"cannot load {MODULE_PATH}")
policy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(policy)


BOOT_POOLS = ("boot-pool", "freenas-boot")
RELEASE_INTERNAL = (".ix-virt",)


class BootPoolUserDataPolicyTests(unittest.TestCase):
    def test_statfs_field_accepts_release_mapping_and_typed_model_shapes(self) -> None:
        mapping = {"source": "boot-pool/user-data", "dest": "/mnt/boot-pool/user-data"}
        model = SimpleNamespace(**mapping)

        self.assertEqual(policy.statfs_field(mapping, "source"), "boot-pool/user-data")
        self.assertEqual(policy.statfs_field(model, "dest"), "/mnt/boot-pool/user-data")
        self.assertIsNone(policy.statfs_field(SimpleNamespace(), "source"))

    def test_allows_only_the_exact_opt_in_namespace(self) -> None:
        for path in (
            "boot-pool/user-data",
            "boot-pool/user-data/my-data",
            "boot-pool/user-data/my-vms/probe",
            "boot-pool/user-data/my-data@snapshot-1",
            "freenas-boot/user-data/my-data",
        ):
            with self.subTest(path=path):
                self.assertTrue(
                    policy.is_user_data_namespace_path(
                        path, BOOT_POOLS, RELEASE_INTERNAL
                    )
                )

    def test_rejects_boot_pool_and_sibling_prefixes(self) -> None:
        for path in (
            "boot-pool",
            "boot-pool/ROOT/27.0.0-RC.1",
            "boot-pool/user-data2/my-data",
            "other-pool/user-data/my-data",
            "boot-pool//user-data/my-data",
            "boot-pool/user-data/../ROOT",
            "boot-pool/user-data@snapshot/ROOT",
            "boot-pool/user-data@",
            "boot-pool/user-data@one@two",
        ):
            with self.subTest(path=path):
                self.assertFalse(
                    policy.is_user_data_namespace_path(
                        path, BOOT_POOLS, RELEASE_INTERNAL
                    )
                )

    def test_keeps_system_and_release_internal_names_protected(self) -> None:
        for component in (
            ".system",
            ".truenas_containers",
            "ROOT",
            "grub",
            "ix-apps",
            "ix-applications",
            ".ix-virt",
        ):
            path = f"boot-pool/user-data/my-data/{component}/nested"
            with self.subTest(path=path):
                self.assertFalse(
                    policy.is_user_data_namespace_path(
                        path, BOOT_POOLS, RELEASE_INTERNAL
                    )
                )

    def test_container_dataset_name_is_shared_with_the_namespace_protection_set(self) -> None:
        self.assertEqual(policy.CONTAINER_DS_NAME, ".truenas_containers")
        self.assertIn(policy.CONTAINER_DS_NAME, policy.PROTECTED_COMPONENTS)
        self.assertFalse(
            policy.is_user_data_namespace_path(
                f"boot-pool/user-data/{policy.CONTAINER_DS_NAME}/containers/app",
                BOOT_POOLS,
                RELEASE_INTERNAL,
            )
        )

    def test_root_detection_is_component_and_snapshot_aware(self) -> None:
        self.assertEqual(
            policy.user_data_namespace_root(
                "boot-pool/user-data/my-vms/probe@snap", BOOT_POOLS
            ),
            "boot-pool/user-data",
        )
        self.assertTrue(
            policy.is_user_data_namespace_root("boot-pool/user-data@snap", BOOT_POOLS)
        )
        self.assertFalse(
            policy.is_user_data_namespace_root("boot-pool/user-data/my-data", BOOT_POOLS)
        )

    def test_boot_mount_requires_the_exact_bounded_namespace_root(self) -> None:
        properties = {
            "quota": {"value": 4 * 1024**3},
            "mountpoint": {"value": "/mnt/boot-pool/user-data"},
            "canmount": {"value": "on"},
        }
        self.assertTrue(
            policy.user_data_root_mount_config_is_valid(
                "boot-pool/user-data", properties, BOOT_POOLS
            )
        )
        for path in (
            "boot-pool",
            "boot-pool/user-data/child",
            "boot-pool/user-data@snapshot",
            "other-pool/user-data",
        ):
            with self.subTest(path=path):
                self.assertFalse(
                    policy.user_data_root_mount_config_is_valid(
                        path, properties, BOOT_POOLS
                    )
                )

    def test_boot_mount_refuses_unbounded_or_redirected_root_properties(self) -> None:
        good = {
            "quota": {"value": 4 * 1024**3},
            "mountpoint": {"value": "/mnt/boot-pool/user-data"},
            "canmount": {"value": "on"},
        }
        mutations = (
            ("quota", {"value": 0}),
            ("quota", {"value": True}),
            ("quota", {"value": "4G"}),
            ("mountpoint", {"value": "/etc"}),
            ("mountpoint", {"value": "none"}),
            ("canmount", {"value": "off"}),
            ("canmount", {"value": "noauto"}),
        )
        for key, value in mutations:
            with self.subTest(key=key, value=value):
                properties = {**good, key: value}
                self.assertFalse(
                    policy.user_data_root_mount_config_is_valid(
                        "boot-pool/user-data", properties, BOOT_POOLS
                    )
                    )

    def test_boot_mount_accepts_only_child_mountpoints_inside_namespace(self) -> None:
        for mountpoint in (
            "/mnt/boot-pool/user-data",
            "/mnt/boot-pool/user-data/my-data",
            "/mnt/boot-pool/user-data/.truenas_containers/boot-pool",
            "none",
            "legacy",
        ):
            with self.subTest(mountpoint=mountpoint):
                self.assertTrue(
                    policy.user_data_mountpoint_is_within_root(
                        mountpoint, "boot-pool/user-data"
                    )
                )

    def test_boot_mount_rejects_child_mountpoint_escape_and_prefix_confusion(self) -> None:
        for mountpoint in (
            "/etc",
            "/mnt/boot-pool/user-data2",
            "/mnt/boot-pool/user-data/../../etc",
            "relative/path",
            "",
        ):
            with self.subTest(mountpoint=mountpoint):
                self.assertFalse(
                    policy.user_data_mountpoint_is_within_root(
                        mountpoint, "boot-pool/user-data"
                    )
                )

    def test_malformed_snapshot_suffix_cannot_turn_children_into_root(self) -> None:
        for path in (
            "boot-pool/user-data@snapshot/child",
            "boot-pool/user-data@",
            "boot-pool/user-data@one@two",
        ):
            with self.subTest(path=path):
                self.assertFalse(
                    policy.is_user_data_namespace_path(path, BOOT_POOLS)
                )

    def test_container_mountpoint_accounts_for_boot_pool_altroot(self) -> None:
        self.assertEqual(
            policy.container_mountpoint_property("boot-pool", BOOT_POOLS),
            "/mnt/boot-pool/user-data/.truenas_containers/boot-pool",
        )
        self.assertEqual(
            policy.container_mountpoint_property("tank", BOOT_POOLS),
            "/.truenas_containers/tank",
        )

    def test_container_real_paths_apply_the_mnt_altroot_once(self) -> None:
        self.assertEqual(
            policy.container_mountpoint_real_path("boot-pool", BOOT_POOLS),
            "/mnt/boot-pool/user-data/.truenas_containers/boot-pool",
        )
        self.assertEqual(
            policy.container_mountpoint_real_path("tank", BOOT_POOLS),
            "/mnt/.truenas_containers/tank",
        )
        self.assertEqual(
            policy.container_instance_real_path("boot-pool", "app", BOOT_POOLS),
            "/mnt/boot-pool/user-data/.truenas_containers/boot-pool/containers/app",
        )


if __name__ == "__main__":
    unittest.main()
