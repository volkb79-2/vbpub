from __future__ import annotations

import importlib.util
from pathlib import Path
import sys


TRUENAS_ROOT = Path(__file__).parents[2]
ACCEPTANCE_DIR = TRUENAS_ROOT / "boot-pool-patch" / "acceptance"
LIVE_STORAGE = ACCEPTANCE_DIR / "live_storage.py"

sys.path.insert(0, str(ACCEPTANCE_DIR))
spec = importlib.util.spec_from_file_location("live_storage_acceptance", LIVE_STORAGE)
if spec is None or spec.loader is None:
    raise RuntimeError(f"cannot load {LIVE_STORAGE}")
live_storage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(live_storage)


def test_path_a_initial_inventory_requires_adapter_managed_root_visibility() -> None:
    rows = [{"id": "boot-pool", "children": [{"id": "boot-pool/user-data", "children": []}]}]
    datasets, root_visible = live_storage.initial_dataset_inventory(
        "path-a", "boot-pool/user-data", rows,
    )

    assert "boot-pool/user-data" in datasets
    assert root_visible is True


def test_path_a_initial_inventory_refuses_missing_managed_root() -> None:
    try:
        live_storage.initial_dataset_inventory("path-a", "boot-pool/user-data", [])
    except live_storage.RemoteError as exc:
        assert "Path A managed namespace root" in str(exc)
    else:
        raise AssertionError("Path A dataset query omission must fail preflight")


def test_path_b_initial_inventory_requires_ordinary_pool_root_visibility() -> None:
    row = {"id": "boot-disk-data", "children": []}

    datasets, root_visible = live_storage.initial_dataset_inventory(
        "path-b", "boot-disk-data", [row],
    )

    assert "boot-disk-data" in datasets
    assert root_visible is True


def test_path_b_initial_inventory_refuses_missing_pool_root() -> None:
    try:
        live_storage.initial_dataset_inventory("path-b", "boot-disk-data", [])
    except live_storage.RemoteError as exc:
        assert "Path B pool root" in str(exc)
    else:
        raise AssertionError("Path B dataset query omission must fail preflight")


def test_container_dataset_root_maps_path_a_and_b_to_the_managed_tree() -> None:
    assert live_storage.container_dataset_root("boot-pool", "path-a") == (
        "boot-pool/user-data/.truenas_containers"
    )
    assert live_storage.container_dataset_root("boot-disk-data", "path-b") == (
        "boot-disk-data/.truenas_containers"
    )


def test_zfs_object_inventory_uses_checked_recursive_zfs_listing() -> None:
    class FakeTarget:
        argv: list[str] | None = None

        def run(self, argv: list[str], *, timeout: int) -> str:
            self.argv = argv
            assert timeout == 45
            return "tank/managed/child\ntank/managed\n"

    target = FakeTarget()

    assert live_storage.zfs_object_inventory(
        target, "tank/managed", "filesystem,volume", recursive=True,
    ) == ["tank/managed", "tank/managed/child"]
    assert target.argv == [
        "zfs", "list", "-H", "-t", "filesystem,volume", "-o", "name", "-r", "tank/managed",
    ]


def test_live_storage_cleanup_uses_nonrecursive_rmdir_for_exact_test_mountpoints() -> None:
    class FakeTarget:
        scripts: list[str]

        def __init__(self) -> None:
            self.scripts = []

        def shell(self, script: str, *, timeout: int) -> str:
            self.scripts.append(script)
            assert timeout == 45
            return ""

    target = FakeTarget()
    root = "boot-pool/user-data/patch-2026101901374586fc51"
    descendants = [f"{root}/data", f"{root}/my-vms", f"{root}/container-data"]

    live_storage.cleanup_test_mountpoints(target, root, descendants)

    assert len(target.scripts) == 4
    assert [script.split("path=", 1)[1].split(";", 1)[0] for script in target.scripts] == [
        f"/mnt/{dataset}" for dataset in [*descendants, root]
    ]
    assert all('rmdir -- "$path"' in script for script in target.scripts)
    assert all("rm -r" not in script and "rm -f" not in script for script in target.scripts)


def test_live_storage_cleanup_refuses_unknown_test_paths_before_shell_mutation() -> None:
    class FakeTarget:
        called = False

        def shell(self, script: str, *, timeout: int) -> str:
            self.called = True
            return ""

    target = FakeTarget()
    try:
        live_storage.cleanup_test_mountpoints(
            target,
            "boot-pool/user-data/important-data",
            [
                "boot-pool/user-data/important-data/data",
                "boot-pool/user-data/important-data/my-vms",
                "boot-pool/user-data/important-data/container-data",
            ],
        )
    except live_storage.RemoteError as exc:
        assert "refusing unexpected test cleanup name" in str(exc)
    else:
        raise AssertionError("cleanup must reject a path without the unique gate-owned name")
    assert target.called is False


def test_live_storage_requires_created_child_visibility_and_checks_existing_container_protection() -> None:
    source = LIVE_STORAGE.read_text()

    assert "require(fs_name in after_update" in source
    assert "zfs list -H -o name -t filesystem,volume -r boot-pool/ROOT" in source
    assert "rejected protected dataset create left a ZFS dataset behind" in source
    assert "require(root_visible" in source
    assert "zfs.resource.destroy" in source
    assert "zfs.resource.snapshot.create" in source
    assert "protected_container_dataset = (" in source
    assert "existing container-managed dataset" in source
    assert "protected_datasets_before_destroy = zfs_object_inventory(" in source
    assert "protected_snapshots_before_destroy = zfs_object_inventory(" in source
    assert "protected_datasets_after_destroy == protected_datasets_before_destroy" in source
    assert "protected_snapshots_after_destroy == protected_snapshots_before_destroy" in source
    assert "protected_datasets_after_snapshot == protected_datasets_after_destroy" in source
    assert "protected_snapshots_after_snapshot == protected_snapshots_after_destroy" in source
    assert "snapshot_path not in protected_snapshots_after_snapshot" in source
    cleanup_helper = source[source.index("def cleanup_test_mountpoints"):source.index("def main")]
    assert 'rmdir -- \\"$path\\"' in cleanup_helper
    assert "rm -rf" not in cleanup_helper
    dataset_cleanup = source.index('target.call("pool.dataset.delete", root_probe')
    mountpoint_cleanup = source.index("cleanup_test_mountpoints(\n                target, root_probe")
    assert mountpoint_cleanup > dataset_cleanup
    assert "pool.snapshot.clone" in source
    assert "clone-refusal-{suffix}" in source
    assert "public pool.snapshot.clone did not refuse a container-managed destination" in source
    assert "is a protected path" in source
    assert "public ZFS destroy and snapshot creation refuse an existing container dataset" in source

    container_creation = source.index('target.call("container.create", {')
    protection_probe = source.index('target.run([\n            "midclt", "call", "zfs.resource.destroy"')
    assert protection_probe > container_creation
    destroy_survival_check = source.index("protected_datasets_after_destroy", protection_probe)
    destroy_refusal_check = source.index(
        '"direct zfs.resource.destroy did not refuse',
        protection_probe,
    )
    assert destroy_survival_check < destroy_refusal_check
    snapshot_state_check = source.index(
        "protected_snapshots_after_snapshot == protected_snapshots_after_destroy",
        destroy_refusal_check,
    )
    assert snapshot_state_check > source.index("snapshot_path =", destroy_refusal_check)
    inventory_helper = source[source.index("def zfs_object_inventory"):source.index("def main")]
    assert "check=False" not in inventory_helper
    clone_probe = source.index('"midclt", "call", "pool.snapshot.clone"')
    assert clone_probe > container_creation
