from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys

import pytest


MODULE_PATH = Path(__file__).parents[1] / "shared" / "path_b_layout.py"
PATCH_ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(PATCH_ROOT / "acceptance"))
import path_b_provisioner

spec = importlib.util.spec_from_file_location("path_b_provisioner_policy", MODULE_PATH)
if spec is None or spec.loader is None:
    raise RuntimeError(f"cannot load {MODULE_PATH}")
policy = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = policy
spec.loader.exec_module(policy)


SGDISK_20_GIB_TAIL = """Disk /dev/vda: 41943040 sectors, 20.0 GiB
Sector size (logical): 512 bytes
Disk identifier (GUID): B26DE526-AED3-4567-A66C-CC259DB2E48C
Partition table holds up to 128 entries
Main partition table begins at sector 2 and ends at sector 33
First usable sector is 34, last usable sector is 41943006
Partitions will be aligned on 2048-sector boundaries
Total free space is 8390622 sectors (4.0 GiB)

Number  Start (sector)    End (sector)  Size       Code  Name
   1            2048            4095   1024.0 KiB  EF02  BIOS
   2            4096         1052671   512.0 MiB   EF00  EFI
   3         1052672        33554398   15.5 GiB    BF01  boot-pool
"""

SGDISK_20_GIB_TRUENAS_28 = """Disk /dev/vda: 41943040 sectors, 20.0 GiB
Sector size (logical/physical): 512/512 bytes
Disk identifier (GUID): B1D8C77F-E2E3-47AF-939E-70B469FFE39D
Partition table holds up to 128 entries
Main partition table begins at sector 2 and ends at sector 33
First usable sector is 34, last usable sector is 41943006
Partitions will be aligned on 2048-sector boundaries
Total free space is 7337917 sectors (3.5 GiB)

Number  Start (sector)    End (sector)  Size       Code  Name
   1            4096            6143   1024.0 KiB  EF02
   2            6144         1054719   512.0 MiB   EF00
   3         1054720        34609151   16.0 GiB    BF01
"""


def plan(**overrides):
    values = {
        "disk": "/dev/vda",
        "boot_pool_partition": "/dev/vda3",
        "layout": policy.parse_sgdisk_print(SGDISK_20_GIB_TAIL),
        "aligned_free_start": 33554432,
        "largest_free_end": 41943006,
        "boot_pool_leaf_paths": ("/dev/vda3",),
    }
    values.update(overrides)
    return policy.make_path_b_plan(**values)


def test_parse_sgdisk_print_and_build_exact_tail_plan() -> None:
    layout = policy.parse_sgdisk_print(SGDISK_20_GIB_TAIL)

    assert layout.logical_sector_bytes == 512
    assert layout.last_usable_sector == 41943006
    assert [(p.number, p.start, p.end, p.type_code) for p in layout.partitions] == [
        (1, 2048, 4095, "EF02"),
        (2, 4096, 1052671, "EF00"),
        (3, 1052672, 33554398, "BF01"),
    ]
    result = plan()
    assert result.new_partition == "/dev/vda4"
    assert (result.start_sector, result.end_sector) == (33554432, 41943006)
    assert result.partition_bytes == 4294950400
    assert result.preexisting_partitions == layout.partitions


def test_parse_true_nas_28_sgdisk_sector_size_and_plan_reserved_tail() -> None:
    layout = policy.parse_sgdisk_print(SGDISK_20_GIB_TRUENAS_28)

    assert layout.logical_sector_bytes == 512
    assert layout.last_usable_sector == 41943006
    assert [(p.number, p.start, p.end, p.type_code) for p in layout.partitions] == [
        (1, 4096, 6143, "EF02"),
        (2, 6144, 1054719, "EF00"),
        (3, 1054720, 34609151, "BF01"),
    ]
    result = plan(
        layout=layout,
        aligned_free_start=34611200,
        largest_free_end=41943006,
    )
    assert result.new_partition == "/dev/vda4"
    assert (result.start_sector, result.end_sector) == (34611200, 41943006)


@pytest.mark.parametrize(
    "changes, message",
    [
        ({"disk": "/dev/nvme0n1", "boot_pool_partition": "/dev/vda3"}, "expected boot-pool"),
        ({"boot_pool_leaf_paths": ("/dev/vda3", "/dev/vdb3")}, "single leaf"),
        ({"aligned_free_start": 33554300}, "not a tail"),
        ({"largest_free_end": 33554431}, "empty or reversed"),
        ({"minimum_free_bytes": 5 * 1024**3}, "below the required"),
        ({"minimum_free_bytes": True}, "positive integer"),
        ({"disk": "/dev/vda/../../etc"}, "disk path"),
    ],
)
def test_plan_refuses_ambiguous_or_unsafe_layouts(changes, message) -> None:
    with pytest.raises(policy.LayoutError, match=message):
        plan(**changes)


def test_plan_refuses_extra_or_non_zfs_partitions() -> None:
    extra = SGDISK_20_GIB_TAIL + "   4        33554432        41943006   4.0 GiB    8300  unexpected\n"
    layout = policy.parse_sgdisk_print(extra)
    with pytest.raises(policy.LayoutError, match="exactly GPT partitions 1, 2, and 3"):
        plan(layout=layout)

    non_zfs = SGDISK_20_GIB_TAIL.replace("BF01  boot-pool", "8300  boot-pool")
    with pytest.raises(policy.LayoutError, match="expected BF01"):
        plan(layout=policy.parse_sgdisk_print(non_zfs))


def test_existing_complete_path_b_is_verified_as_partition_four_only() -> None:
    existing = SGDISK_20_GIB_TAIL + (
        "   4        33554432        41943006   4.0 GiB    BF01  boot-disk-data\n"
    )
    data_partition = policy.verify_existing_path_b(
        disk="/dev/vda",
        boot_pool_partition="/dev/vda3",
        layout=policy.parse_sgdisk_print(existing),
        boot_pool_leaf_paths=("/dev/vda3",),
        data_pool_leaf_paths=("/dev/vda4",),
        pool_status="ONLINE",
    )
    assert data_partition == "/dev/vda4"


@pytest.mark.parametrize(
    "changes, message",
    [
        ({"data_pool_leaf_paths": ("/dev/vdb1",)}, "single vdev"),
        ({"boot_pool_leaf_paths": ("/dev/vda2",)}, "partition 3"),
        ({"pool_status": "DEGRADED"}, "status is"),
    ],
)
def test_existing_path_b_requires_healthy_expected_vdevs(changes, message) -> None:
    existing = SGDISK_20_GIB_TAIL + (
        "   4        33554432        41943006   4.0 GiB    BF01  boot-disk-data\n"
    )
    values = {
        "disk": "/dev/vda",
        "boot_pool_partition": "/dev/vda3",
        "layout": policy.parse_sgdisk_print(existing),
        "boot_pool_leaf_paths": ("/dev/vda3",),
        "data_pool_leaf_paths": ("/dev/vda4",),
        "pool_status": "ONLINE",
    }
    values.update(changes)
    with pytest.raises(policy.LayoutError, match=message):
        policy.verify_existing_path_b(**values)


def test_existing_path_b_refuses_misnamed_or_overlapping_partition_four() -> None:
    misnamed = SGDISK_20_GIB_TAIL + (
        "   4        33554432        41943006   4.0 GiB    BF01  other\n"
    )
    with pytest.raises(policy.LayoutError, match="must be named"):
        policy.verify_existing_path_b(
            disk="/dev/vda",
            boot_pool_partition="/dev/vda3",
            layout=policy.parse_sgdisk_print(misnamed),
            boot_pool_leaf_paths=("/dev/vda3",),
            data_pool_leaf_paths=("/dev/vda4",),
            pool_status="ONLINE",
        )

    overlapping = SGDISK_20_GIB_TAIL + (
        "   4        33554390        41943006   4.0 GiB    BF01  boot-disk-data\n"
    )
    with pytest.raises(policy.LayoutError):
        policy.parse_sgdisk_print(overlapping)


class FakePartitionOnlyTarget:
    user = "root"

    def __init__(self) -> None:
        self.partition_created = False
        self.commands: list[list[str]] = []

    def run(self, argv, *, timeout=45, check=True):
        self.commands.append(argv)
        if argv[0] == "readlink":
            return argv[-1]
        if argv[0] == "lsblk":
            partitions = [
                {"path": "/dev/vda1", "name": "vda1", "type": "part", "partn": 1},
                {"path": "/dev/vda2", "name": "vda2", "type": "part", "partn": 2},
                {"path": "/dev/vda3", "name": "vda3", "type": "part", "partn": 3},
            ]
            if self.partition_created:
                partitions.append({
                    "path": "/dev/vda4", "name": "vda4", "type": "part", "partn": 4,
                })
            return json.dumps({"blockdevices": [{
                "path": "/dev/vda", "name": "vda", "type": "disk",
                "ro": False, "rm": False, "children": partitions,
            }]})
        if argv[0] == "sgdisk":
            if "--print" in argv:
                return SGDISK_20_GIB_TAIL + (
                    "   4        33554432        41943006   4.0 GiB    BF01  boot-disk-data\n"
                    if self.partition_created else ""
                )
            if "--verify" in argv:
                return "No problems found. 0 free sectors (0 bytes)"
            if "--first-aligned-in-largest" in argv:
                return "33554432"
            if "--end-of-largest" in argv:
                return "41943006"
            if any(arg.startswith("--new=4:") for arg in argv):
                self.partition_created = True
            return ""
        if argv[0] == "zpool" and "status" in argv:
            return "  pool: boot-pool\n state: ONLINE\n  /dev/vda3 ONLINE\n"
        if argv[0] in {"install", "partprobe", "udevadm", "python3"}:
            return ""
        raise AssertionError(f"unexpected remote command: {argv!r}")

    def call(self, method, *params, timeout=90):
        if method == "system.info":
            return {"version": "27.0.0-RC.1"}
        if method == "pool.import_find":
            return 7
        raise AssertionError(f"unexpected middleware call: {method!r}")

    def query(self, method, *params, timeout=90):
        assert method == "pool.query"
        return []

    def wait_job(self, job_id, *, timeout=600, label="middleware job"):
        assert job_id == 7
        return []


def test_partition_only_guard_reserves_tail_without_creating_a_pool() -> None:
    target = FakePartitionOnlyTarget()
    result = path_b_provisioner.provision_path_b(
        target,
        disk="/dev/vda",
        boot_pool_partition="/dev/vda3",
        expected_version="27.0.0-RC.1",
        apply=True,
        confirmed_disk="/dev/vda",
        partition_only=True,
    )

    assert result["state"] == "partition_created"
    assert result["partition"] == "/dev/vda4"
    assert target.partition_created
    assert not any(argv[:2] == ["zpool", "create"] for argv in target.commands)
    assert any(argv[0] == "python3" and "/root/truenas-path-b-backups/boot-disk-data-partition.json" in argv
               for argv in target.commands)
