"""Hermetic installer-level tests for the `partitioning` plan: numbering, sfdisk plan, stage-two commands.

Every argv goes through the production HostActions._validate (dry-run HostActions, FakeActions below, or
argv_guard). Pure planner/schema tests are in test_layout_policy.py; the real loop-device proof is test_data_area_vm.py.
"""

from __future__ import annotations

import json
import random
import shlex

import pytest

from debian_install_v2 import layout_policy as lp
from debian_install_v2.actions import ActionError, HostActions
from debian_install_v2.config import Config, ConfigError, load_config, validate_config
from debian_install_v2.customscript import build_customscript_bundle
from debian_install_v2.disk_layout import GIB
from debian_install_v2.installer import Installer, InstallerError
from debian_install_v2.state import StateStore

from .argv_guard import validate_argv
from .test_disk_policy import make_planner

S = lp.SECTORS_PER_GIB
LINUX = "0fc63daf-8483-4772-8e79-3d69d8477de4"
SWAP = "0657fd6d-a4ab-43c4-84e5-0933c84b4f4f"

COW = {"fs": "btrfs", "mount": "/srv/cow", "label": "MDT_COW"}
ROOT160_DATA_STAR = {"root": 160, "data": {"kind": "partitions", "volumes": [COW]}}
LVM_PLAN = {
    "root": 160,
    "data": {"kind": "lvm", "vg": "vg_data", "volumes": [
        {"name": "cow", "size": "60%", "fs": "btrfs", "mount": "/srv/cow", "label": "MDT_COW"},
        {"name": "spare", "fs": "none"},
    ]},
}
TWO_PARTS = {"root": 100, "data": {"kind": "partitions", "volumes": [
    {"size": 200, "fs": "btrfs", "mount": "/srv/cow", "label": "MDT_COW"},
    {"fs": "xfs", "mount": "/srv/scratch", "label": "SCRATCH"},
]}}


def _planner(tmp_path, partitioning=None, *, swap_count=4, swap_total=16, **kw):
    return make_planner(
        tmp_path, disk_gib=512, root_gib=500, swap_disk_total_gb=swap_total, swap_partition_count=swap_count,
        partitioning=partitioning or ROOT160_DATA_STAR, **kw,
    )


def _table(installer):
    """Plan, write the sfdisk plan and return (entries by number, swap extents, root size)."""
    swap, root = installer._plan_swap_partitions()
    entries = installer._parse_partition_entries(installer._write_sfdisk_plan(swap, root))
    return entries, swap, root


def _assert_numbers_follow_disk_position(entries):
    ordered = sorted(entries, key=lambda number: int(entries[number]["start"]))
    assert ordered == sorted(entries), f"numbers do not follow disk position: {ordered}"


def _assert_whole_disk_used(entries, disk_sectors, root_start):
    """Check contiguity and that the tail ends before backup GPT metadata plus alignment slack."""
    tail = sorted((e for n, e in entries.items() if int(e["start"]) >= root_start), key=lambda e: int(e["start"]))
    for first, second in zip(tail, tail[1:]):
        assert int(first["start"]) + int(first["size"]) == int(second["start"]), "gap inside the plan"
    assert disk_sectors - (int(tail[-1]["start"]) + int(tail[-1]["size"])) <= lp.SWAP_END_SLACK_SECTORS


# ---------------------------------------------------------------- numbering and geometry (the v1001 bug)

def test_root_160_data_star_numbers_follow_disk_position_and_leave_no_gap(tmp_path):
    installer = _planner(tmp_path)
    entries, swap, root = _table(installer)
    assert root * 512 == 160 * GIB
    assert sorted(entries) == [3, 4, 5, 6, 7, 8, 9]  # root, data, raw iobench, then swaps by position
    data = entries[4]
    assert data["name"] == "MDT_COW" and data["type"].upper() == lp.LINUX_FS_TYPE_GUID
    assert entries[5]["name"] == lp.IOBENCH_PARTLABEL
    assert [entries[n]["type"].lower() for n in (6, 7, 8, 9)] == [SWAP] * 4
    assert int(entries[3]["start"]) + int(entries[3]["size"]) == int(data["start"])  # no hole behind root
    _assert_numbers_follow_disk_position(entries)
    _assert_whole_disk_used(entries, 512 * S, int(entries[3]["start"]))
    assert int(data["size"]) > 320 * S  # remainder after swap and the planned raw benchmark partition
    installer._validate_plan_geometry(installer._parse_partition_entries(
        installer._current_partition_dump(2048, 500 * S)), entries, root)


def test_lvm_pv_partition_has_the_pv_type_label_and_the_remainder(tmp_path):
    installer = _planner(tmp_path, LVM_PLAN)
    entries, _swap, _root = _table(installer)
    pv = entries[4]
    assert pv["name"] == lp.LVM_PV_PARTLABEL and pv["type"].upper() == lp.LINUX_LVM_TYPE_GUID
    _assert_numbers_follow_disk_position(entries)
    _assert_whole_disk_used(entries, 512 * S, int(entries[3]["start"]))


def test_two_data_partitions_get_consecutive_numbers_in_config_order(tmp_path):
    installer = _planner(tmp_path, TWO_PARTS)
    entries, _swap, root = _table(installer)
    assert sorted(entries) == [3, 4, 5, 6, 7, 8, 9, 10]
    assert (entries[4]["name"], int(entries[4]["size"])) == ("MDT_COW", 200 * S)
    assert entries[5]["name"] == "SCRATCH" and int(entries[5]["size"]) > 180 * S
    assert entries[6]["name"] == lp.IOBENCH_PARTLABEL
    assert root * 512 == 100 * GIB
    _assert_numbers_follow_disk_position(entries)
    _assert_whole_disk_used(entries, 512 * S, int(entries[3]["start"]))


def test_cloud_image_root_one_with_preserved_14_and_15_numbers_data_then_swap(tmp_path):
    """Root is partition 1 on cloud images, 14/15 sit before it: tail numbers just ascend after root."""
    disk = 512 * S
    entries = {
        14: {"start": "2048", "size": "6144", "type": LINUX},
        15: {"start": "8192", "size": "217088", "type": "c12a7328-f81f-11d2-ba4b-00a0c93ec93b"},
        1: {"start": "262144", "size": str(3 * S), "type": LINUX},
    }
    installer = make_planner(
        tmp_path, disk_gib=512, root_gib=3, root_start=262144, swap_disk_total_gb=16, swap_partition_count=4,
        partitioning=ROOT160_DATA_STAR, entries=entries,
    )
    installer.root_number = 1
    installer._disk_facts = lambda: (disk, 262144, 3 * S)
    swap, root = installer._plan_swap_partitions()
    plan = installer._parse_partition_entries(installer._write_sfdisk_plan(swap, root))
    assert plan[14] == entries[14] and plan[15] == entries[15]
    assert plan[2]["name"] == "MDT_COW" and plan[3]["name"] == lp.IOBENCH_PARTLABEL
    assert sorted(n for n in plan if n not in (1, 14, 15)) == [2, 3, 4, 5, 6, 7]
    ordered = sorted((n for n in plan if int(plan[n]["start"]) >= 262144), key=lambda n: int(plan[n]["start"]))
    assert ordered == [1, 2, 3, 4, 5, 6, 7]  # raw benchmark partition precedes the swaps


@pytest.mark.parametrize("seed", range(6))
def test_property_numbers_follow_position_and_sizes_account_for_the_disk(tmp_path, seed):
    rng = random.Random(seed)
    for case in range(12):
        n_data = rng.choice([0, 1, 2, 3])
        volumes = [
            {"fs": rng.choice(["btrfs", "ext4", "xfs"]), "mount": f"/srv/v{i}", "label": f"V{i}",
             **({"size": rng.choice([20, 30, "5%"])} if i else {})}
            for i in range(n_data)
        ]
        partitioning = {"root": rng.choice([60, 100, "keep", "*"]), "reserve_gb": "*"}
        if volumes:
            partitioning["data"] = {"kind": "partitions", "volumes": volumes}
        if partitioning["root"] == "*" and volumes:
            partitioning["data"]["volumes"][0]["size"] = 50
        if any(isinstance(v.get("size"), str) for v in volumes) and any(isinstance(v.get("size"), int) for v in volumes):
            continue
        swap_count = rng.choice([1, 2, 4, 8])
        installer = _planner(tmp_path / f"{seed}-{case}", partitioning, swap_count=swap_count, swap_total=rng.choice([4, 16]))
        try:
            entries, swap, root = _table(installer)
        except InstallerError:
            continue
        assert len(swap) == swap_count and sorted(entries)[0] == 3
        assert sorted(entries) == list(range(3, 3 + 1 + n_data + 1 + swap_count))
        _assert_numbers_follow_disk_position(entries)
        starts = [int(entries[n]["start"]) for n in sorted(entries)]
        assert starts == sorted(starts) and all(s % 2048 == 0 for s in starts[1:])
        assert disk_end_ok(entries)


def disk_end_ok(entries):
    last = max(entries.values(), key=lambda e: int(e["start"]))
    return 512 * S - (int(last["start"]) + int(last["size"])) <= lp.SWAP_END_SLACK_SECTORS


def test_root_keep_leaves_the_root_alone_and_data_takes_the_rest(tmp_path):
    installer = make_planner(
        tmp_path, disk_gib=512, root_gib=20, swap_disk_total_gb=16, swap_partition_count=4,
        partitioning={"root": "keep", "data": {"kind": "partitions", "volumes": [COW]}},
    )
    entries, _swap, root = _table(installer)
    assert root == 20 * S and int(entries[3]["size"]) == 20 * S
    assert int(entries[4]["start"]) == int(entries[3]["start"]) + 20 * S
    _assert_numbers_follow_disk_position(entries)
    _assert_whole_disk_used(entries, 512 * S, int(entries[3]["start"]))
    assert installer._root_policy() == "untouched"


def test_swap_off_ends_the_data_pool_before_the_planned_raw_partition(tmp_path):
    installer = _planner(tmp_path, ROOT160_DATA_STAR, swap_count=1)
    installer.config = installer.config.__class__(**{**installer.config.__dict__, "swap_policy": "off"})
    swap, root = installer._plan_swap_partitions()
    assert swap == []
    entries = installer._parse_partition_entries(installer._write_sfdisk_plan(swap, root))
    assert sorted(entries) == [3, 4, 5]
    assert entries[5]["name"] == lp.IOBENCH_PARTLABEL
    assert int(entries[4]["start"]) + int(entries[4]["size"]) == int(entries[5]["start"])
    assert int(entries[5]["start"]) + int(entries[5]["size"]) == lp.gpt_safe_end(512 * S)


@pytest.mark.parametrize("disk_gib", [20, 21, 32, 128, 512, 2048, 8192])
@pytest.mark.parametrize("remainder", [0, 1, 777, 2047])
@pytest.mark.parametrize("swap_policy", ["on", "off"])
@pytest.mark.parametrize(
    "partitioning",
    [
        {"root": "*"},
        {"root": "30%", "data": {"kind": "partitions", "volumes": [{**COW, "size": "70%"}]}},
        {"root": "*", "data": {"kind": "partitions", "volumes": [{**COW, "size": 5}]}},
    ],
)
def test_pool_end_is_shared_and_gpt_safe_for_all_plan_shapes(
    tmp_path, disk_gib, remainder, swap_policy, partitioning,
):
    """Kills M-swapoff-pool-end and guards on/off against the backup GPT area."""
    disk_sectors = disk_gib * S + remainder
    root_start = 2_500_608
    installer = make_planner(
        tmp_path / f"{disk_gib}-{remainder}-{swap_policy}-{hash(str(partitioning)) & 0xffff}",
        disk_gib=disk_gib,
        root_gib=8,
        root_start=root_start,
        ram_gib=8,
        swap_policy=swap_policy,
        swap_disk_total_gb=2,
        swap_partition_count=2,
        preserve_root_size_gb=2,
        partitioning=partitioning,
    )
    installer._disk_facts = lambda: (disk_sectors, root_start, 8 * S)
    swaps, root_size = installer._plan_swap_partitions()
    extents = installer._data_area_layout(swaps, [])
    entries = installer._parse_partition_entries(installer._write_sfdisk_plan(swaps, root_size))
    ordered = sorted(entries.items(), key=lambda item: int(item[1]["start"]))

    assert installer._pool_end(swaps) == (
        min(start for start, _size in swaps)
        if swap_policy == "on"
        else lp.gpt_safe_end(disk_sectors)
    )
    assert [n for n, _entry in ordered] == sorted(n for n, _entry in ordered)
    assert all(int(entry["start"]) % 2048 == 0 for _n, entry in ordered[1:])
    for (_n1, first), (_n2, second) in zip(ordered, ordered[1:]):
        assert int(first["start"]) + int(first["size"]) == int(second["start"])
    last_end = max(int(entry["start"]) + int(entry["size"]) for entry in entries.values())
    assert last_end <= disk_sectors - lp.GPT_LAST_USABLE_OFFSET
    assert disk_sectors - last_end <= lp.SWAP_END_SLACK_SECTORS
    assert len(extents) == installer._disk_spec().partition_count


def test_installer_data_recognition_requires_the_planned_partlabel(tmp_path):
    installer = _planner(tmp_path, ROOT160_DATA_STAR)
    assert installer._is_data_partition({"type": lp.LINUX_FS_TYPE_GUID, "name": "MDT_COW"})
    assert not installer._is_data_partition({"type": lp.LINUX_FS_TYPE_GUID, "name": "FOREIGN"})


def test_swap_off_percent_and_root_star_fixed_data_do_not_overlap(tmp_path):
    for partitioning in (
        {"root": "30%", "data": {"kind": "partitions", "volumes": [{**COW, "size": "70%"}]}},
        {"root": "*", "data": {"kind": "partitions", "volumes": [{**COW, "size": 5}]}},
    ):
        installer = _planner(tmp_path / str(partitioning), partitioning, swap_count=1)
        installer.config = Config(**{**installer.config.__dict__, "swap_policy": "off"})
        swaps, root_size = installer._plan_swap_partitions()
        assert swaps == []
        table = installer._parse_partition_entries(installer._write_sfdisk_plan(swaps, root_size))
        assert int(table[3]["start"]) + int(table[3]["size"]) == int(table[4]["start"])
        assert int(table[4]["start"]) + int(table[4]["size"]) <= 512 * S - 2048


# ---------------------------------------------------------------- refusals at plan time

def test_planner_refuses_unfitting_and_unaccounted_plans_and_writes_nothing(tmp_path):
    with pytest.raises(InstallerError, match="over-allocates the pool"):
        _planner(tmp_path / "a", {"root": 600, "data": {"kind": "partitions", "volumes": [COW]}})._plan_swap_partitions()
    with pytest.raises(InstallerError, match="fixed sizes without a growable entry"):
        _planner(tmp_path / "b", {"root": 100, "data": {"kind": "partitions", "volumes": [{**COW, "size": 200}]}})._plan_swap_partitions()
    with pytest.raises(InstallerError, match="ambiguous"):
        _planner(tmp_path / "c", {"root": 100, "data": {"kind": "partitions", "volumes": [{**COW, "size": "50%"}]}})._plan_swap_partitions()


def test_root_below_preserve_floor_is_refused(tmp_path):
    installer = _planner(tmp_path, {"root": 5, "data": {"kind": "partitions", "volumes": [COW]}}, preserve_root_size_gb=10)
    with pytest.raises(InstallerError, match="root is .* below the minimum of 10240 MiB"):
        installer._plan_swap_partitions()


def test_shrink_path_the_table_write_follows_the_shrunk_root(tmp_path):
    """Root 500 GiB -> 160 GiB: data would overlap the live root until the filesystem shrink has happened."""
    installer = _planner(tmp_path)
    swap, root = installer._plan_swap_partitions()
    assert root * 512 == 160 * GIB < 500 * GIB
    current = installer._parse_partition_entries(installer._current_partition_dump(2048, 500 * S))
    # (1) the table written AFTER the shrink (root at the target) is valid
    planned = installer._parse_partition_entries(installer._write_sfdisk_plan(swap, root))
    installer._validate_plan_geometry(current, planned, root)
    raw_number = next(number for number, entry in planned.items() if entry.get("name") == lp.IOBENCH_PARTLABEL)
    data_number = next(number for number, entry in planned.items() if entry.get("name") == "MDT_COW")
    swap_numbers = [number for number, entry in planned.items() if entry.get("type", "").lower() == SWAP]
    assert int(planned[3]["size"]) == 160 * S
    assert int(planned[data_number]["start"]) + int(planned[data_number]["size"]) == int(planned[raw_number]["start"])
    assert int(planned[raw_number]["start"]) + int(planned[raw_number]["size"]) == int(planned[min(swap_numbers)]["start"])
    _assert_whole_disk_used(planned, 512 * S, int(planned[3]["start"]))
    # (2) writing it while the old, larger root is still in the table overlaps and is refused
    overlapping = installer._parse_partition_entries(installer._write_sfdisk_plan(swap, 500 * S))
    with pytest.raises(InstallerError, match="overlap"):
        installer._validate_plan_geometry(current, overlapping, 500 * S)
    # (3) stage two refuses to write the full table before the shrink happened
    installer.actions.dry_run = False
    installer._run = lambda *a, **k: ""
    installer._preflight_disk_transaction = lambda: {}
    with pytest.raises(InstallerError, match="root shrink was required"):
        installer._apply_known_swap_shape()


def test_root_shrink_interim_table_has_no_hole_before_raw_partition_or_swap(tmp_path):
    installer = _planner(tmp_path)
    swaps, target_root = installer._plan_swap_partitions()
    installer._plan_root_shrink((swaps, target_root, True))
    plan_text = installer.actions.dry_run_writes["/etc/vbpub/root-shrink-plan.sfdisk"]
    interim = installer._parse_partition_entries(plan_text)
    raw_number = next(number for number, entry in interim.items() if entry.get("name") == lp.IOBENCH_PARTLABEL)
    swap_numbers = [number for number, entry in interim.items() if entry.get("type", "").lower() == SWAP]
    assert int(interim[4]["start"]) + int(interim[4]["size"]) == int(interim[raw_number]["start"])
    assert int(interim[raw_number]["start"]) + int(interim[raw_number]["size"]) == int(interim[min(swap_numbers)]["start"])
    _assert_whole_disk_used(interim, 512 * S, int(interim[3]["start"]))


def test_a_resume_recomputes_the_same_plan_whatever_the_root_size_is_now(tmp_path):
    """The plan depends on (config, disk size, root START), never on the current root SIZE: crash after the
    table write (root still small) and after root growth give identical extents."""
    installer = _planner(tmp_path)
    swap, root = installer._plan_swap_partitions()
    expected = installer._data_area_layout(swap, [])
    for current_root in (3 * S, 160 * S, 500 * S):
        installer._disk_facts = lambda c=current_root: (512 * S, 2048, c)
        assert installer._data_area_layout(swap, []) == expected


def test_existing_installer_data_partition_is_not_a_foreign_partition(tmp_path):
    installer = _planner(tmp_path)
    entries, swap, root = _table(installer)
    entries[3]["size"] = str(root)
    installer._live_partition_entries = lambda *_a: entries  # second run: the table already holds data + swap
    installer._disk_facts = lambda: (512 * S, 2048, root)
    swap2, root2 = installer._plan_swap_partitions()
    assert (swap2, root2) == (swap, root)
    installer.config = Config(**{**installer.config.__dict__, "partitioning": {}})  # no plan: data is foreign
    with pytest.raises(InstallerError, match="non-swap partition"):
        installer._plan_swap_partitions()


def test_foreign_linux_partition_after_root_is_not_recognized_without_partlabel(tmp_path):
    root_start = 2048
    root_size = 8 * S
    foreign = {
        3: {"start": str(root_start), "size": str(root_size), "type": LINUX, "name": "root"},
        4: {"start": str(root_start + root_size), "size": str(2 * S), "type": LINUX, "name": "FOREIGN"},
    }
    installer = make_planner(
        tmp_path,
        disk_gib=20,
        root_gib=8,
        partitioning={"root": "*", "iobench_raw_gb": False},
        entries=foreign,
        swap_policy="off",
        preserve_root_size_gb=2,
    )
    installer._disk_facts = lambda: (20 * S, root_start, root_size)
    with pytest.raises(InstallerError, match="non-swap partition"):
        installer._plan_swap_partitions()


def test_gpt_128th_partition_is_accepted_and_129th_is_refused(tmp_path):
    """Kills M-128-partition by proving number 128 fits and number 129 does not."""
    installer = _planner(tmp_path, {"root": "*", "iobench_raw_gb": False})
    installer.root_number = 1
    assert len(installer._tail_numbers(126, [2])) == 126
    assert installer._tail_numbers(126, [2])[-1] == 128
    with pytest.raises(InstallerError, match="GPT partition-number limit reached"):
        installer._tail_numbers(127, [2])


# ---------------------------------------------------------------- stage two commands (dry-run, allowlist checked)

def _argvs(installer):
    return [list(action.argv) for action in installer.actions.planned]


def _stage_two(tmp_path, partitioning):
    installer = _planner(tmp_path, partitioning)
    installer._read_fstab_lines = lambda: ["UUID=abc / ext4 defaults 0 1"]
    installer._apt_get = lambda *a, **k: None
    installer._configure_data_area()
    for argv in _argvs(installer):
        validate_argv(argv)
    return installer


def test_partition_commands_pass_the_allowlist(tmp_path):
    installer = _stage_two(tmp_path, ROOT160_DATA_STAR)
    argvs = _argvs(installer)
    assert ["/usr/sbin/mkfs.btrfs", "-L", "MDT_COW", "/dev/vda4"] in argvs  # data is partition 4, before the swaps
    assert any(a[0].endswith("mount") and a[1] == "/srv/cow" for a in argvs)
    written = installer.actions.dry_run_writes["/etc/fstab"]
    assert "LABEL=MDT_COW /srv/cow btrfs noatime,user_subvol_rm_allowed,nofail 0 0" in written  # dry run: no UUID yet
    assert "UUID=abc / ext4" in written
    assert "x-systemd.required-by=docker.service" not in written


def test_two_partitions_each_get_their_own_filesystem_and_fstab_line_sorted_by_path(tmp_path):
    installer = _stage_two(tmp_path, TWO_PARTS)
    argvs = _argvs(installer)
    assert ["/usr/sbin/mkfs.btrfs", "-L", "MDT_COW", "/dev/vda4"] in argvs
    assert ["/usr/sbin/mkfs.xfs", "-L", "SCRATCH", "/dev/vda5"] in argvs
    lines = installer.actions.dry_run_writes["/etc/fstab"].splitlines()
    mounts = [line.split()[1] for line in lines if line.split()[1:2] in (["/srv/cow"], ["/srv/scratch"])]
    assert mounts == ["/srv/cow", "/srv/scratch"]


def test_raw_partition_is_planned_but_never_formatted_or_mounted(tmp_path):
    plan = {"root": 100, "data": {"kind": "partitions", "volumes": [
        {"size": 20, "fs": "none", "type": "zfs", "label": "ZPOOL"}, COW]}}
    installer = _stage_two(tmp_path, plan)
    names = [a[0].rsplit("/", 1)[-1] for a in _argvs(installer)]
    assert names.count("mkfs.btrfs") == 1
    entries, _swap, _root = _table(_planner(tmp_path / "t", plan))
    assert entries[4]["type"].upper() == lp.ZFS_TYPE_GUID and entries[4]["name"] == "ZPOOL"


def test_default_iobench_partition_is_raw_fixed_sized_and_precedes_swap(tmp_path):
    installer = _stage_two(tmp_path, ROOT160_DATA_STAR)
    entries, swaps, _root = _table(_planner(tmp_path / "table", ROOT160_DATA_STAR))
    raw_number = next(number for number, entry in entries.items() if entry.get("name") == lp.IOBENCH_PARTLABEL)
    raw = entries[raw_number]
    assert int(raw["size"]) == 8 * S
    assert raw["type"].lower() == lp.LINUX_FS_TYPE_GUID.lower()
    assert raw_number == min(n for n, _ in zip(range(raw_number, raw_number + 1), [raw]))
    assert raw_number < min(n for n in entries if n not in (3, raw_number) and entries[n]["type"].lower() == SWAP)
    names = [action.argv[0].rsplit("/", 1)[-1] for action in installer.actions.planned]
    assert "mkfs.ext4" not in names and "mkfs.btrfs" in names
    assert not any(action.argv[0].endswith("mount") and action.argv[-1] == f"/dev/vda{raw_number}" for action in installer.actions.planned)
    assert swaps and raw["name"] == "iobench-raw"


def test_install_benchmark_wipes_then_runs_on_the_planned_raw_partition_only(tmp_path):
    installer = _planner(tmp_path)
    installer.config = Config(**{**installer.config.__dict__, "run_io_benchmark": True})
    StateStore(installer.config.state_dir).save_new(StateStore.new(installer.config))

    installer._run_io_benchmark()

    actions = installer.actions.planned
    wipe_index = next(i for i, action in enumerate(actions) if action.argv[0].endswith("wipefs"))
    run_index = next(i for i, action in enumerate(actions) if action.argv[0].endswith("iobench-install-runner"))
    assert wipe_index < run_index
    assert actions[wipe_index].argv == ("/usr/sbin/wipefs", "-a", "/dev/vda5")
    assert actions[run_index].argv[2] == "/dev/vda5"
    assert actions[run_index].argv[4] == str(8 * GIB)
    assert not any(
        action.argv[0].endswith(("mkfs.ext4", "mkfs.btrfs", "mkfs.xfs", "mount", "umount"))
        for action in actions
    )


def test_raw_linux_partition_is_accepted_and_planned(tmp_path):
    plan = {"root": 100, "data": {"kind": "partitions", "volumes": [
        {"size": 20, "fs": "none", "type": "linux", "label": "RAWDISK"}, COW]}}
    spec = lp.disk_spec(plan)
    assert spec.data.volumes[0].type_guid == lp.LINUX_FS_TYPE_GUID
    installer = _planner(tmp_path, plan)
    entries, _swaps, _root = _table(installer)
    raw = next(entry for entry in entries.values() if entry.get("name") == "RAWDISK")
    assert raw["type"].lower() == lp.LINUX_FS_TYPE_GUID.lower()


def test_lvm_commands_pass_the_allowlist_star_lv_is_last_and_unformatted_lv_is_skipped(tmp_path):
    plan = {"root": 160, "data": {"kind": "lvm", "vg": "vg_data", "volumes": [
        {"name": "scratch", "fs": "ext4", "mount": "/srv/scratch", "label": "SCRATCH"},
        {"name": "cow", "size": "60%", "fs": "btrfs", "mount": "/srv/cow", "label": "MDT_COW"},
        {"name": "spare", "size": 2, "fs": "none"}]}}
    installer = _stage_two(tmp_path, plan)
    argvs = _argvs(installer)
    lvcreates = [a for a in argvs if a[0].endswith("lvcreate")]
    assert [a[a.index("-n") + 1] for a in lvcreates] == ["cow", "spare", "scratch"]  # "*" LV last
    assert lvcreates[1][-3:] == ["-L", "2G", "vg_data"] and lvcreates[2][-3:] == ["-l", "100%FREE", "vg_data"]
    assert lvcreates[0][-3] == "-l" and lvcreates[0][-2].isdigit()  # percent of the VG, in extents
    names = [a[0].rsplit("/", 1)[-1] for a in argvs]
    assert names.count("mkfs.btrfs") == 1 and names.count("mkfs.ext4") == 1
    written = installer.actions.dry_run_writes["/etc/fstab"]
    assert "/dev/vg_data/cow /srv/cow btrfs" in written and "spare" not in written


@pytest.mark.parametrize("mount", ["/srv/cow", "/home/runner"])
def test_supported_data_mounts_write_fstab_without_docker_dependency(tmp_path, mount):
    plan = {"root": 160, "data": {"kind": "partitions", "volumes": [{**COW, "mount": mount}]}}
    installer = _stage_two(tmp_path, plan)
    line = next(line for line in installer.actions.dry_run_writes["/etc/fstab"].splitlines() if mount in line)
    assert f" {mount} btrfs " in line
    assert "x-systemd.required-by=docker.service" not in line


def test_mount_on_package_database_is_refused_with_the_protected_path(tmp_path):
    plan = {"root": 160, "data": {"kind": "partitions", "volumes": [{**COW, "mount": "/var/lib/dpkg"}]}}
    with pytest.raises(InstallerError, match=r"protected system path '/var/lib/dpkg'"):
        _stage_two(tmp_path, plan)


def test_new_commands_are_allowlisted_strictly():
    actions = HostActions(dry_run=True)
    actions.run(["/usr/sbin/lvcreate", "-y", "-n", "x", "-L", "1G", "vg"])
    actions.run(["/usr/sbin/lvcreate", "-y", "-n", "x", "-l", "100%FREE", "vg"])
    with pytest.raises(ActionError):
        actions.run(["/usr/sbin/lvcreate", "--force", "-n", "x", "vg"])
    with pytest.raises(ActionError):
        actions.run(["/usr/sbin/mkfs.btrfs", "--rootdir", "/", "/dev/x"])
    with pytest.raises(ActionError):
        actions.run(["/usr/sbin/vgcreate", "-f", "vg", "/dev/x"])


class FakeActions(HostActions):
    """Non-dry-run runner with scripted blkid/vgs/lvs/findmnt answers; records every command."""

    def __init__(self, signatures=None, vg_pvs="", lvs="", blkid_errors=None, label_devices=(), uuids=None, extents=40000):
        super().__init__(dry_run=False)
        self.blkid_errors = dict(blkid_errors or {})
        self.label_devices = list(label_devices)
        self.uuids = dict(uuids or {})
        self.signatures = dict(signatures or {})
        self.vg_pvs, self.lvs, self.extents = vg_pvs, lvs, extents
        self.calls: list[list[str]] = []
        self.files: dict[str, str] = {}

    def run(self, argv, description="", dangerous=False, input=None, timeout=None):
        self._validate(list(argv))
        self.calls.append(list(argv))
        tool = argv[0].rsplit("/", 1)[-1]
        if tool == "blkid":
            device = argv[-1]
            if device in self.blkid_errors:
                raise ActionError(f"action failed ({self.blkid_errors[device]}): blkid")
            if "-t" in argv:
                return "".join(f"{d}\n" for d in self.label_devices)
            if "UUID" in argv and device in self.uuids:
                return self.uuids[device] + "\n"
            if "TYPE" in argv and self.signatures.get(device):
                return self.signatures[device] + "\n"
            raise ActionError("action failed (2): blkid")
        if tool == "vgs":
            if "vg_extent_count" in argv:
                return f"  {self.extents}\n"
            if not self.vg_pvs:
                raise ActionError("action failed (5): vgs")
            return self.vg_pvs
        if tool == "lvs":
            return self.lvs
        if tool == "findmnt":
            raise ActionError("action failed (1): not mounted")
        return ""

    def write_file(self, path, content, mode=0o644):
        self.files[path] = content


def _real_stage_two(tmp_path, actions, partitioning):
    installer = _planner(tmp_path, partitioning)
    swap, root = installer._plan_swap_partitions()
    entries = installer._parse_partition_entries(installer._write_sfdisk_plan(swap, root))
    entries[3]["size"] = str(root)
    installer.actions = actions
    installer._live_partition_entries = lambda *_a: entries
    installer._disk_facts = lambda: (512 * S, 2048, root)
    installer._read_fstab_lines = lambda: []
    installer._apt_get = lambda *a, **k: None
    installer._mark_step = lambda *a, **k: None
    installer._plan_swap_partitions = lambda **_k: (swap, root)
    return installer


def test_blank_partition_is_formatted_once_and_existing_same_fs_is_kept(tmp_path):
    actions = FakeActions()
    _real_stage_two(tmp_path, actions, ROOT160_DATA_STAR)._configure_data_area()
    assert any(c[0].endswith("mkfs.btrfs") for c in actions.calls)
    actions = FakeActions(signatures={"/dev/vda4": "btrfs"})
    _real_stage_two(tmp_path, actions, ROOT160_DATA_STAR)._configure_data_area()  # rerun: idempotent
    assert not any(c[0].rsplit("/", 1)[-1].startswith("mkfs") for c in actions.calls)


def test_foreign_signature_is_refused_never_overwritten(tmp_path):
    actions = FakeActions(signatures={"/dev/vda4": "ext4"})
    with pytest.raises(InstallerError, match="already holds a ext4 signature"):
        _real_stage_two(tmp_path, actions, ROOT160_DATA_STAR)._configure_data_area()
    assert not any(c[0].rsplit("/", 1)[-1].startswith("mkfs") for c in actions.calls)
    actions = FakeActions(signatures={"/dev/vda4": "crypto_LUKS"})
    with pytest.raises(InstallerError, match="LVM physical volume"):
        _real_stage_two(tmp_path, actions, LVM_PLAN)._configure_data_area()
    assert not any(c[0].rsplit("/", 1)[-1] == "pvcreate" for c in actions.calls)


def test_lvm_rerun_creates_only_what_is_missing(tmp_path):
    actions = FakeActions(
        signatures={"/dev/vda4": "LVM2_member", "/dev/vg_data/cow": "btrfs"}, vg_pvs="/dev/vda4", lvs="cow",
    )
    _real_stage_two(tmp_path, actions, LVM_PLAN)._configure_data_area()
    tools = [c[0].rsplit("/", 1)[-1] for c in actions.calls]
    assert "pvcreate" not in tools and "vgcreate" not in tools and "mkfs.btrfs" not in tools
    assert tools.count("lvcreate") == 1  # only the missing "*" LV (spare)
    actions = FakeActions(signatures={"/dev/vda4": "LVM2_member"}, vg_pvs="/dev/vdb1")
    with pytest.raises(InstallerError, match="already exists on other physical volumes"):
        _real_stage_two(tmp_path, actions, LVM_PLAN)._configure_data_area()


def test_lv_sizes_use_the_real_extent_count_of_the_vg(tmp_path):
    actions = FakeActions(extents=1000)
    _real_stage_two(tmp_path, actions, LVM_PLAN)._configure_data_area()
    lvcreates = [c for c in actions.calls if c[0].endswith("lvcreate")]
    assert lvcreates[0][-3:] == ["-l", "600", "vg_data"]  # 60% of 1000 extents
    actions = FakeActions(extents=10)
    with pytest.raises(InstallerError, match="below the minimum"):
        _real_stage_two(tmp_path / "t", actions, LVM_PLAN)._configure_data_area()


def test_blkid_failure_is_not_blank_fail_closed(tmp_path):
    actions = FakeActions(blkid_errors={"/dev/vda4": 4})  # probe error, not "no signature"
    with pytest.raises(InstallerError, match="cannot determine whether /dev/vda4 is blank"):
        _real_stage_two(tmp_path, actions, ROOT160_DATA_STAR)._configure_data_area()
    assert not any(c[0].rsplit("/", 1)[-1].startswith("mkfs") for c in actions.calls)
    actions = FakeActions(blkid_errors={"/dev/vda4": 4})
    with pytest.raises(InstallerError, match="cannot determine whether"):
        _real_stage_two(tmp_path, actions, LVM_PLAN)._configure_data_area()
    assert not any(c[0].rsplit("/", 1)[-1] == "pvcreate" for c in actions.calls)


def test_fstab_uses_uuid_and_ambiguous_label_is_refused(tmp_path):
    actions = FakeActions(uuids={"/dev/vda4": "1234-abcd"}, label_devices=["/dev/vda4"])
    _real_stage_two(tmp_path, actions, ROOT160_DATA_STAR)._configure_data_area()
    assert "UUID=1234-abcd /srv/cow btrfs noatime,user_subvol_rm_allowed,nofail 0 0" in actions.files["/etc/fstab"]
    actions = FakeActions(uuids={"/dev/vda4": "1234-abcd"}, label_devices=["/dev/vda4", "/dev/vdb1"])
    with pytest.raises(InstallerError, match="also on /dev/vdb1"):
        _real_stage_two(tmp_path, actions, ROOT160_DATA_STAR)._configure_data_area()
    assert "/etc/fstab" not in actions.files


def test_rerun_with_changed_swap_count_cannot_disturb_a_populated_data_partition(tmp_path):
    """Swaps follow the data partitions now, so a changed swap count never renumbers data; a changed DATA plan
    (here: a second data partition shifting the first one's neighbours) is refused when a signature exists."""
    old = _planner(tmp_path)
    current, _swap, _root = _table(old)
    new = _planner(tmp_path / "n", {"root": 100, "data": {"kind": "partitions", "volumes": [{**COW, "size": 150}, {"fs": "xfs", "mount": "/srv/x", "label": "X"}]}})
    planned, _s, _r = _table(new)
    # populated: refused with a clear error
    new.actions = FakeActions(signatures={"/dev/vda4": "btrfs"})
    with pytest.raises(InstallerError, match="refusing to delete or resize data-area partition"):
        new._refuse_to_disturb_data_partitions(current, planned)
    new.actions = FakeActions(blkid_errors={"/dev/vda4": 1})
    with pytest.raises(InstallerError, match="cannot determine"):
        new._refuse_to_disturb_data_partitions(current, planned)
    new.actions = FakeActions()  # provably blank: may be re-planned
    new._refuse_to_disturb_data_partitions(current, planned)
    new.actions = FakeActions(signatures={"/dev/vda4": "btrfs"})  # unchanged layout: never probed
    new._refuse_to_disturb_data_partitions(current, current)


def test_changed_swap_count_keeps_the_data_partition_number(tmp_path):
    a, _s, _r = _table(_planner(tmp_path / "a", swap_count=4))
    b, _s, _r = _table(_planner(tmp_path / "b", swap_count=2))
    assert a[4]["name"] == b[4]["name"] == "MDT_COW" and a[4]["start"] == b[4]["start"]


# ---------------------------------------------------------------- show_plan, customscript

def test_show_plan_lists_the_data_partitions(tmp_path):
    plan = _planner(tmp_path).show_plan()
    assert plan["partitioning"] == ROOT160_DATA_STAR
    assert [p["device"] for p in plan["data_partitions"]] == ["/dev/vda4"]
    assert plan["plan_table"]
    assert plan["plan_table"][0]["entry"] == "root"
    assert any(row["partlabel"] == lp.IOBENCH_PARTLABEL and row["gib"] == 8.0 for row in plan["plan_table"])
    assert plan["swap_partitions"][0]["device"] == "/dev/vda6"
    from debian_install_v2.bootstrap import _render_plan
    rendered = _render_plan(plan)
    assert "Resolved plan table (GiB):" in rendered and "iobench-raw" in rendered


def test_customscript_carries_the_partitioning_object():
    config = Config(partitioning=LVM_PLAN, telegram_bot_token="", telegram_chat_id="")
    bundle = build_customscript_bundle(config)
    assert bundle["config"]["partitioning"] == LVM_PLAN
    token = next(
        part for part in shlex.split(bundle["customScript"]) if part.startswith("VBPUB_CONFIG_EXTRA_JSON=")
    )
    embedded = json.loads(token.split("=", 1)[1])
    assert load_config(raw_json=json.dumps(embedded)).partitioning == LVM_PLAN


def test_customscript_refuses_an_invalid_plan():
    config = Config(
        partitioning={"data": {"kind": "partitions", "volumes": []}}, telegram_bot_token="", telegram_chat_id="",
    )
    with pytest.raises(ConfigError, match="non-empty list"):
        build_customscript_bundle(config)
    with pytest.raises(ConfigError):
        validate_config(Config(partitioning={"root": "fast"}, telegram_bot_token="", telegram_chat_id=""))


@pytest.mark.parametrize(
    "partitioning",
    [
        {"root": 10, "iobench_raw_gb": False, "data": {"kind": "partitions", "volumes": [
            {**COW, "size": 20},
        ]}},
        {"root": "40%", "iobench_raw_gb": False, "data": {"kind": "partitions", "volumes": [
            {**COW, "size": "50%"},
        ]}},
        {"root": 10, "iobench_raw_gb": False, "data": {"kind": "partitions", "volumes": [
            {**COW, "size": "50%"},
        ]}},
    ],
)
def test_customscript_refuses_static_impossible_size_shapes(partitioning):
    config = Config(partitioning=partitioning, telegram_bot_token="", telegram_chat_id="")
    with pytest.raises(ConfigError):
        build_customscript_bundle(config)
