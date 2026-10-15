from __future__ import annotations

from pathlib import Path

import pytest

from debian_install_v2.actions import HostActions
from debian_install_v2.config import Config
from debian_install_v2.disk_layout import GIB
from debian_install_v2.installer import Installer, InstallerError


def make_planner(
    tmp_path: Path,
    *,
    disk_gib: int = 20,
    root_gib: int | None = None,
    ram_gib: int = 4,
    root_resize_policy: str = "resize",
    swap_policy: str = "on",
    swap_disk_total_gb: int | None = 2,
    swap_partition_count: int = 4,
    preserve_root_size_gb: int = 2,
    root_used_gib: int = 1,
    minimum_blocks: int = 100_000,
    entries: dict[int, dict[str, str]] | None = None,
) -> Installer:
    disk_sectors = disk_gib * GIB // 512
    root_start = 2048
    root_size = (
        (root_gib * GIB // 512)
        if root_gib is not None
        else disk_sectors - root_start
    )
    config = Config(
        state_dir=str(tmp_path / "state"),
        root_resize_policy=root_resize_policy,
        swap_policy=swap_policy,
        swap_disk_total_gb=swap_disk_total_gb,
        swap_partition_count=swap_partition_count,
        preserve_root_size_gb=preserve_root_size_gb,
        telegram_bot_token="",
        telegram_chat_id="",
    )
    installer = Installer(config, HostActions(dry_run=True), inspect_host=False)
    installer.root_disk = "vda"
    installer.root_partition_path = "/dev/vda3"
    installer.root_number = 3
    installer._disk_facts = lambda: (disk_sectors, root_start, root_size)
    live = entries or {
        3: {
            "start": str(root_start),
            "size": str(root_size),
            "type": "0fc63daf-8483-4772-8e79-3d69d8477de4",
        }
    }
    installer._live_partition_entries = lambda *_args: live
    installer._memory_total_bytes = lambda: ram_gib * GIB
    installer._root_filesystem_facts = lambda: (4096, minimum_blocks)
    installer._root_used_bytes = lambda: root_used_gib * GIB
    return installer


def test_resize_policy_shrinks_to_make_requested_swap_and_keeps_four_devices(tmp_path):
    installer = make_planner(tmp_path, root_resize_policy="resize")

    partitions, target_root = installer._plan_swap_partitions()

    _disk_sectors, root_start, current_root = installer._disk_facts()
    assert target_root < current_root
    assert target_root * 512 >= installer.config.preserve_root_size_gb * GIB
    assert len(partitions) == 4
    assert all(size == partitions[0][1] for _start, size in partitions)
    assert partitions[0][0] == root_start + target_root
    assert partitions[-1][0] + partitions[-1][1] <= _disk_sectors - 2048


def _planner_with_pre_root_swap(tmp_path, *, pre_root_gib: int, swap_total_gib: int = 4):
    installer = make_planner(
        tmp_path,
        disk_gib=20,
        swap_disk_total_gb=swap_total_gib,
        swap_partition_count=4,
    )
    disk_sectors = 20 * GIB // 512
    pre_root_start = 4096
    pre_root_size = pre_root_gib * GIB // 512
    root_start = pre_root_start + pre_root_size
    root_size = disk_sectors - root_start
    live = {
        1: {"start": "2048", "size": "2048", "type": "efi"},
        2: {
            "start": str(pre_root_start), "size": str(pre_root_size),
            "type": "0657fd6d-a4ab-43c4-84e5-0933c84b4f4f",
        },
        3: {
            "start": str(root_start), "size": str(root_size),
            "type": "0fc63daf-8483-4772-8e79-3d69d8477de4",
        },
    }
    installer._disk_facts = lambda: (disk_sectors, root_start, root_size)
    installer._live_partition_entries = lambda *_args: live
    return installer, live


def test_pre_root_swap_is_retained_and_counts_toward_total_size_and_partition_count(tmp_path):
    installer, live = _planner_with_pre_root_swap(tmp_path, pre_root_gib=1)

    partitions, target_root = installer._plan_swap_partitions()
    plan_text = installer._write_sfdisk_plan(partitions, target_root)
    plan = installer._parse_partition_entries(plan_text)
    report = installer.show_plan()

    assert len(partitions) == 3
    assert sum(size for _start, size in partitions) * 512 == pytest.approx(3 * GIB, abs=4 * 1024 * 1024)
    assert plan[2] == live[2]
    assert [number for number in plan if plan[number].get("type") == "0657fd6d-a4ab-43c4-84e5-0933c84b4f4f"] == [2, 4, 5, 6]
    assert report["swap_partition_count"] == 4
    assert report["swap_retained_before_root_gb"] == pytest.approx(1.0)
    assert report["swap_after_root_gb"] == pytest.approx(3.0, abs=4 / 1024)


def test_pre_root_swap_is_activated_and_formatted_with_tail_swaps(tmp_path, monkeypatch):
    installer, _live = _planner_with_pre_root_swap(tmp_path, pre_root_gib=1)
    commands = []
    fstab_entries = []
    monkeypatch.setattr(
        installer,
        "_run",
        lambda argv, *args, **kwargs: commands.append(tuple(argv)) or "",
    )
    monkeypatch.setattr(installer, "_persist_fstab", lambda entries: fstab_entries.extend(entries))
    monkeypatch.setattr(installer, "_mark_step", lambda *args, **kwargs: None)

    installer._activate_swap_partitions()

    assert [argv[-1] for argv in commands if argv[0] == "/usr/sbin/mkswap"] == [
        "/dev/vda2", "/dev/vda4", "/dev/vda5", "/dev/vda6",
    ]
    assert [argv[-1] for argv in commands if argv[0] == "/usr/sbin/swapon"] == [
        "/dev/vda2", "/dev/vda4", "/dev/vda5", "/dev/vda6",
    ]
    assert len(fstab_entries) == 4
    assert "vbpub-swap-pre1" in commands[0][commands[0].index("-L") + 1]


def test_pre_root_swap_larger_than_total_is_kept_without_tail_partitions(tmp_path):
    installer, _live = _planner_with_pre_root_swap(tmp_path, pre_root_gib=5)

    partitions, _target_root = installer._plan_swap_partitions()
    plan = installer.show_plan()

    assert partitions == []
    assert plan["swap_partition_count"] == 1
    assert plan["swap_retained_before_root_gb"] == pytest.approx(5.0)
    assert plan["swap_after_root_gb"] == 0


@pytest.mark.parametrize("policy", ["untouched", "grow-only"])
def test_non_shrinking_root_policy_refuses_swap_that_does_not_fit(tmp_path, policy):
    installer = make_planner(tmp_path, root_resize_policy=policy)

    with pytest.raises(InstallerError, match=rf"root_resize_policy={policy} forbids the shrink"):
        installer._plan_swap_partitions()


def test_resize_refuses_when_root_data_usage_exceeds_planned_target(tmp_path):
    installer = make_planner(
        tmp_path,
        root_gib=18,
        swap_disk_total_gb=8,
        preserve_root_size_gb=2,
        root_used_gib=14,
    )
    installer.actions.dry_run = False

    with pytest.raises(InstallerError, match=r"root currently uses 14\.00 GiB.*planned root size"):
        installer._plan_swap_partitions()


def test_root_shrink_uses_pre_cleanup_usage_snapshot_instead_of_post_delete_usage(tmp_path):
    installer = make_planner(
        tmp_path,
        root_gib=18,
        swap_disk_total_gb=8,
        preserve_root_size_gb=2,
        root_used_gib=2,
    )
    installer.actions.dry_run = False
    installer._root_used_bytes = lambda: 2 * GIB  # after deleting a large swapfile
    pre_cleanup_facts = (4096, 1_000_000, 14 * GIB)

    with pytest.raises(InstallerError, match=r"root currently uses 14\.00 GiB"):
        installer._plan_swap_partitions(root_safety_facts=pre_cleanup_facts)


def test_resize_refuses_when_ext_minimum_plus_margin_exceeds_target(tmp_path):
    installer = make_planner(
        tmp_path,
        root_gib=18,
        swap_disk_total_gb=8,
        preserve_root_size_gb=2,
        root_used_gib=1,
        minimum_blocks=4_000_000,
    )

    with pytest.raises(InstallerError, match="root filesystem minimum plus 256 MiB"):
        installer._plan_swap_partitions()


def test_swap_untouched_preserves_partition_geometry_but_root_can_grow(tmp_path):
    root_start = 2048
    current_root = 8 * GIB // 512
    next_start = root_start + current_root + 2048
    live = {
        3: {"start": str(root_start), "size": str(current_root), "type": "0fc63daf"},
        4: {
            "start": str(next_start), "size": str(GIB // 512),
            "type": "0657fd6d-a4ab-43c4-84e5-0933c84b4f4f",
        },
    }
    installer = make_planner(
        tmp_path,
        root_gib=8,
        root_resize_policy="grow-only",
        swap_policy="untouched",
        entries=live,
    )

    partitions, target_root = installer._plan_swap_partitions()

    assert target_root > current_root
    assert partitions == [(next_start, GIB // 512)]


def test_untouched_swap_growth_uses_physical_partition_order_not_partition_number(tmp_path):
    root_start = 2048
    current_root = 8 * GIB // 512
    next_start = root_start + current_root + 2048
    live = {
        3: {
            "start": str(root_start), "size": str(current_root),
            "type": "0fc63daf-8483-4772-8e79-3d69d8477de4",
        },
        2: {
            "start": str(next_start), "size": str(GIB // 512),
            "type": "0fc63daf-8483-4772-8e79-3d69d8477de4",
        },
    }
    installer = make_planner(
        tmp_path,
        root_gib=8,
        root_resize_policy="grow-only",
        swap_policy="untouched",
        entries=live,
    )

    partitions, target_root = installer._plan_swap_partitions()

    assert partitions == []
    assert target_root == next_start - root_start


def test_managed_swap_refuses_non_swap_partition_physically_after_root_even_if_numbered_before(
    tmp_path,
):
    root_start = 2048
    current_root = 8 * GIB // 512
    live = {
        3: {
            "start": str(root_start), "size": str(current_root),
            "type": "0fc63daf-8483-4772-8e79-3d69d8477de4",
        },
        2: {
            "start": str(root_start + current_root + 2048), "size": str(GIB // 512),
            "type": "0fc63daf-8483-4772-8e79-3d69d8477de4",
        },
    }
    installer = make_planner(tmp_path, root_gib=8, entries=live)

    with pytest.raises(InstallerError, match=r"non-swap partition\(s\) follow root"):
        installer._plan_swap_partitions()


def test_debian_cloud_image_high_numbered_boot_partitions_before_root_are_preserved(tmp_path):
    disk_sectors = 20 * GIB // 512
    root_start = 262144
    root_size = disk_sectors - root_start
    live = {
        14: {
            "start": "2048", "size": "6144",
            "type": "21686148-6449-6e6f-744e-656564454649",
        },
        15: {
            "start": "8192", "size": "253952",
            "type": "c12a7328-f81f-11d2-ba4b-00a0c93ec93b",
        },
        1: {
            "start": str(root_start), "size": str(root_size),
            "type": "0fc63daf-8483-4772-8e79-3d69d8477de4",
        },
    }
    installer = make_planner(
        tmp_path,
        disk_gib=20,
        root_gib=None,
        root_resize_policy="resize",
        swap_disk_total_gb=4,
        preserve_root_size_gb=2,
        root_used_gib=1,
        entries=live,
    )
    installer.root_number = 1
    installer.root_partition_path = "/dev/vda1"
    installer._disk_facts = lambda: (disk_sectors, root_start, root_size)
    disk_dump = """label: gpt
device: /dev/vda
unit: sectors
sector-size: 512

/dev/vda14 : start=2048, size=6144, type=21686148-6449-6e6f-744e-656564454649
/dev/vda15 : start=8192, size=253952, type=c12a7328-f81f-11d2-ba4b-00a0c93ec93b
/dev/vda1 : start=262144, size={root_size}, type=0fc63daf-8483-4772-8e79-3d69d8477de4
""".format(root_size=root_size)
    installer._current_partition_dump = lambda *_args: disk_dump

    partitions, target_root = installer._plan_swap_partitions()
    plan_text = installer._write_sfdisk_plan(partitions, target_root)
    plan = installer._parse_partition_entries(plan_text)
    current = installer._parse_partition_entries(disk_dump)
    installer._validate_plan_geometry(current, plan, target_root)

    assert plan[14] == live[14]
    assert plan[15] == live[15]
    assert target_root < root_size
    assert sorted(
        number for number, entry in plan.items()
        if installer._is_swap_partition(entry)
    ) == [2, 3, 4, 5]


def test_tail_swap_numbers_skip_any_preserved_pre_root_partition(tmp_path):
    installer = make_planner(tmp_path)
    installer.root_number = 1

    assert installer._tail_swap_numbers(3, {2, 14, 15}) == [3, 4, 5]


def test_swap_off_leaves_no_planned_swap_and_uses_reclaimed_disk_space(tmp_path):
    root_start = 2048
    current_root = 8 * GIB // 512
    live = {
        3: {"start": str(root_start), "size": str(current_root), "type": "0fc63daf"},
        4: {
            "start": str(root_start + current_root), "size": str(GIB // 512),
            "type": "0657fd6d-a4ab-43c4-84e5-0933c84b4f4f",
        },
    }
    installer = make_planner(
        tmp_path,
        root_gib=8,
        root_resize_policy="resize",
        swap_policy="off",
        entries=live,
    )

    partitions, target_root = installer._plan_swap_partitions()

    assert partitions == []
    assert target_root == installer._disk_facts()[0] - root_start


def test_show_plan_reports_untouched_swap_count_and_off_as_disabled(tmp_path):
    root_start = 2048
    current_root = 8 * GIB // 512
    live = {
        3: {"start": str(root_start), "size": str(current_root), "type": "0fc63daf"},
        4: {
            "start": str(root_start + current_root), "size": str(GIB // 512),
            "type": "0657fd6d-a4ab-43c4-84e5-0933c84b4f4f",
        },
    }
    untouched = make_planner(
        tmp_path / "untouched",
        root_gib=8,
        root_resize_policy="untouched",
        swap_policy="untouched",
        entries=live,
    )
    untouched_plan = untouched.show_plan()
    assert untouched_plan["swap_partition_count"] == 1

    disabled = make_planner(
        tmp_path / "off", root_gib=8, swap_policy="off", entries=live
    )
    disabled_plan = disabled.show_plan()
    assert disabled_plan["swap_partition_count"] == 0


def test_untouched_swap_policy_does_not_deactivate_swap(tmp_path, monkeypatch):
    installer = make_planner(tmp_path, swap_policy="untouched")
    calls = []
    monkeypatch.setattr(installer, "_run", lambda *args, **kwargs: calls.append(args))

    installer._disable_active_swap()

    assert calls == []


def test_swap_on_removes_configured_file_and_logical_volume_after_swapoff(tmp_path, monkeypatch):
    installer = make_planner(tmp_path)
    installer.actions.dry_run = False
    swap_file = tmp_path / "managed-swapfile"
    swap_file.write_bytes(b"swap")
    logical_volume = "/dev/vg0/swap"
    active_checks = iter([
        {str(swap_file): "file", logical_volume: "partition"},
        {},
    ])
    events = []
    monkeypatch.setattr(installer, "_active_swap_sources", lambda: next(active_checks))
    monkeypatch.setattr(installer, "_fstab_swap_sources", lambda: [str(swap_file), logical_volume])
    monkeypatch.setattr(
        installer,
        "_swap_backing_removal_targets",
        lambda _active, _fstab: ([str(swap_file)], [logical_volume]),
    )
    monkeypatch.setattr(installer, "_persist_fstab", lambda entries: events.append(("fstab", entries)))
    monkeypatch.setattr(installer, "_mark_step", lambda *args, **kwargs: None)
    monkeypatch.setattr("debian_install_v2.installer.shutil.which", lambda _name: "/usr/sbin/lvremove")
    monkeypatch.setattr(
        installer,
        "_run",
        lambda argv, description="", **kwargs: events.append(("run", tuple(argv))) or "",
    )
    remove_file = installer.actions.remove_file

    def record_remove_file(path):
        events.append(("remove-file", path))
        remove_file(path)

    monkeypatch.setattr(installer.actions, "remove_file", record_remove_file)

    installer._remove_managed_swap_backings()

    assert not swap_file.exists()
    assert events == [
        ("run", ("/usr/sbin/swapoff", "-a")),
        ("fstab", []),
        ("remove-file", str(swap_file)),
        ("run", ("/usr/sbin/lvremove", "-y", logical_volume)),
    ]


def test_swap_cleanup_classifies_swapfile_and_lv_without_removing_partition(tmp_path, monkeypatch):
    installer = make_planner(tmp_path)
    installer.actions.dry_run = False
    swap_file = tmp_path / "managed-swapfile"
    swap_file.write_bytes(b"swap")
    def run(argv, *_args, **_kwargs):
        if argv[0] == "/usr/bin/lsblk":
            return {
                "/dev/vg0/swap": "lvm /dev/vg0/swap\n",
                "/dev/vda2": "part /dev/vda2\n",
            }[argv[-1]]
        return ""

    monkeypatch.setattr(installer, "_run", run)

    files, logical_volumes = installer._swap_backing_removal_targets(
        {
            str(swap_file): "file",
            "/dev/vg0/swap": "partition",
            "/dev/vda2": "partition",
        },
        [],
    )

    assert files == [str(swap_file)]
    assert logical_volumes == ["/dev/vg0/swap"]


@pytest.mark.parametrize(
    ("signature", "should_remove"),
    [("swap", True), ("ext4", False)],
)
def test_inactive_fstab_logical_volume_is_removed_only_with_swap_signature(
    tmp_path, monkeypatch, signature, should_remove,
):
    installer = make_planner(tmp_path)
    installer.actions.dry_run = False

    def run(argv, *_args, **_kwargs):
        if argv[0] == "/usr/bin/lsblk":
            return "lvm /dev/vg0/swap\n"
        if argv[0] == "/usr/sbin/blkid":
            return signature + "\n"
        raise AssertionError(f"unexpected command: {argv!r}")

    monkeypatch.setattr(installer, "_run", run)

    if should_remove:
        assert installer._swap_backing_removal_targets({}, ["/dev/vg0/swap"]) == (
            [], ["/dev/vg0/swap"]
        )
    else:
        with pytest.raises(InstallerError, match="has no swap signature"):
            installer._swap_backing_removal_targets({}, ["/dev/vg0/swap"])


def test_configured_swapfile_path_that_is_not_a_regular_file_refuses_cleanup(tmp_path):
    installer = make_planner(tmp_path)
    installer.actions.dry_run = False
    swap_dir = tmp_path / "swap-directory"
    swap_dir.mkdir()

    with pytest.raises(InstallerError, match="not a regular file"):
        installer._swap_backing_removal_targets({}, [str(swap_dir)])


def test_partx_ranges_never_span_unrelated_partition_numbers():
    assert Installer._partition_number_ranges({2, 4, 5, 8, 9}) == ["2:2", "4:5", "8:9"]
    assert Installer._partition_number_ranges(()) == []


def test_plan_overlap_check_uses_physical_order_not_partition_number(tmp_path):
    installer = make_planner(tmp_path)
    root_type = "0fc63daf-8483-4772-8e79-3d69d8477de4"
    linux_type = "0fc63daf-8483-4772-8e79-3d69d8477de4"
    swap_type = "0657fd6d-a4ab-43c4-84e5-0933c84b4f4f"
    current = {
        1: {"start": "4096", "size": "1024", "type": linux_type},
        2: {"start": "2048", "size": "1024", "type": linux_type},
        3: {"start": "6144", "size": "4096", "type": root_type},
    }
    plan = {
        **current,
        4: {"start": "10240", "size": "2048", "type": swap_type},
        5: {"start": "12288", "size": "2048", "type": swap_type},
        6: {"start": "14336", "size": "2048", "type": swap_type},
        7: {"start": "16384", "size": "2048", "type": swap_type},
    }

    installer._validate_plan_geometry(current, plan, 4096)


def test_root_growth_uses_growpart_then_resizes_filesystem(tmp_path, monkeypatch):
    installer = make_planner(tmp_path, root_gib=8, swap_policy="off")
    installer.actions.dry_run = True
    events: list[tuple[str, ...]] = []
    monkeypatch.setattr(installer, "_resolve_swap_plan", lambda: ([], 12 * GIB // 512, False))
    monkeypatch.setattr(installer, "_packages", lambda *_args: events.append(("packages",)))
    monkeypatch.setattr(installer, "_root_filesystem_facts", lambda: (4096, 100_000))
    monkeypatch.setattr(installer, "_run", lambda argv, *args, **kwargs: events.append(tuple(argv)) or "")
    monkeypatch.setattr(installer, "_mark_step", lambda *args, **kwargs: None)

    installer._grow_root_to_plan()

    commands = [event for event in events if event and event[0] != "packages"]
    assert commands[0] == ("/usr/bin/growpart", "--fudge", "0", "/dev/vda", "3")
    assert commands[1] == ("/usr/sbin/resize2fs", "/dev/vda3")


def test_root_growth_resume_resizes_filesystem_after_partition_already_reached_target(
    tmp_path, monkeypatch
):
    installer = make_planner(tmp_path, root_gib=12, swap_policy="off")
    events: list[tuple[str, ...]] = []
    monkeypatch.setattr(installer, "_resolve_swap_plan", lambda: ([], 12 * GIB // 512, False))
    monkeypatch.setattr(installer, "_root_filesystem_facts", lambda: (4096, 100_000))
    monkeypatch.setattr(installer, "_run", lambda argv, *args, **kwargs: events.append(tuple(argv)) or "")
    monkeypatch.setattr(installer, "_mark_step", lambda *args, **kwargs: None)

    installer._grow_root_to_plan()

    assert events == [("/usr/sbin/resize2fs", "/dev/vda3")]
