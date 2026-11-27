"""Behavioral coverage for the remaining swap-span mutation survivors."""

from __future__ import annotations

from pathlib import Path

import pytest

from debian_install_v2 import installer as installer_module
from debian_install_v2.installer import InstallerError
from debian_install_v2.layout_policy import (
    SWAP_SPAN_PARTLABEL,
    ZFS_TYPE_GUID,
)

from .test_io_benchmark import (
    LINUX,
    PARTUUID,
    ROOT_SIZE,
    ROOT_START,
    install_pre_root_swap,
    make,
    make_span,
    set_benchmark_step,
    step,
)


def data_partitioning() -> dict[str, object]:
    return {
        "data": {
            "kind": "partitions",
            "volumes": [{"size": 4, "fs": "ext4", "mount": "/data", "label": "DATA"}],
        }
    }


def enable_dry_run_table_view(installer, disk, monkeypatch) -> None:
    """Expose the simulated current table while recording, but not applying, dry-run writes."""
    disk.dry_run = True
    monkeypatch.setattr(
        installer,
        "_disk_facts",
        lambda: (disk.disk_bytes // 512, ROOT_START, ROOT_SIZE),
    )
    monkeypatch.setattr(
        installer,
        "_live_partition_entries",
        lambda *_args: {number: dict(entry) for number, entry in disk.table.items()},
    )
    monkeypatch.setattr(installer, "_current_partition_dump", lambda *_args: disk.dump())


def partx_numbers(disk, option: str) -> set[int]:
    numbers: set[int] = set()
    for action in disk.planned:
        if Path(action.argv[0]).name != "partx" or option not in action.argv:
            continue
        range_text = action.argv[action.argv.index("--nr") + 1]
        first, last = (int(value) for value in range_text.split(":"))
        numbers.update(range(first, last + 1))
    return numbers


def sfdisk_write_count(disk) -> int:
    return sum(
        Path(action.argv[0]).name == "sfdisk" and "--force" in action.argv
        for action in disk.planned
    )


@pytest.mark.parametrize("field", ["number", "type", "start", "size"])
def test_plan_geometry_refuses_each_single_wrong_span_field(tmp_path, field):
    installer, disk = make(tmp_path)
    span_number = make_span(installer, disk)
    partitions, target_root = installer._plan_swap_partitions()
    before_root = installer._planned_before_root()
    expected = installer._planned_tail_swap_span(partitions, before_root)
    assert expected is not None
    plan = installer._parse_partition_entries(
        installer._write_sfdisk_plan(partitions, target_root, tail_swap_span=True)
    )
    planned_number = next(
        number for number, entry in plan.items() if entry.get("name") == SWAP_SPAN_PARTLABEL
    )
    if field == "number":
        plan[planned_number + 20] = plan.pop(planned_number)
    elif field == "type":
        plan[planned_number]["type"] = ZFS_TYPE_GUID
    elif field == "start":
        plan[planned_number]["start"] = str(int(plan[planned_number]["start"]) + 2048)
    else:
        plan[planned_number]["size"] = str(int(plan[planned_number]["size"]) + 2048)

    with pytest.raises(
        InstallerError,
        match="io_benchmark_swap_span_geometry_mismatch: the span must replace exactly",
    ):
        installer._validate_plan_geometry(
            {number: dict(entry) for number, entry in disk.table.items()},
            plan,
            target_root,
            allow_swap_span=True,
            expected_swap_span=expected,
        )


@pytest.mark.parametrize("field", ["number", "start", "size", "type"])
def test_live_table_replacement_refuses_each_single_wrong_span_field(tmp_path, field):
    installer, disk = make(tmp_path)
    span_number = make_span(installer, disk)
    partitions, target_root = installer._plan_swap_partitions()
    before_root = installer._planned_before_root()
    live = {number: dict(entry) for number, entry in disk.table.items()}
    if field == "number":
        live[span_number + 20] = live.pop(span_number)
    elif field == "start":
        live[span_number]["start"] = str(int(live[span_number]["start"]) + 2048)
    elif field == "size":
        live[span_number]["size"] = str(int(live[span_number]["size"]) + 2048)
    else:
        live[span_number]["type"] = ZFS_TYPE_GUID

    with pytest.raises(
        InstallerError,
        match="live span does not exactly replace the planned tail swaps",
    ):
        installer._validate_swap_span_table_replacement(
            live, partitions, before_root, target_root
        )


def test_allowing_a_span_does_not_allow_a_foreign_post_root_partition(tmp_path):
    installer, disk = make(tmp_path)
    make_span(installer, disk)
    partitions, target_root = installer._plan_swap_partitions()
    before_root = installer._planned_before_root()
    expected = installer._planned_tail_swap_span(partitions, before_root)
    assert expected is not None
    plan = installer._parse_partition_entries(
        installer._write_sfdisk_plan(partitions, target_root, tail_swap_span=True)
    )
    current = {number: dict(entry) for number, entry in disk.table.items()}
    root_end = int(current[installer.root_number]["start"]) + int(
        current[installer.root_number]["size"]
    )
    current[100] = {
        "start": str(root_end + 2048),
        "size": "2048",
        "type": ZFS_TYPE_GUID,
        "name": "foreign",
    }

    with pytest.raises(InstallerError, match="unsupported disk layout: non-swap partition 100 follows root"):
        installer._validate_plan_geometry(
            current,
            plan,
            target_root,
            allow_swap_span=True,
            expected_swap_span=expected,
        )


def test_default_geometry_comparison_detects_a_partlabel_only_difference(tmp_path):
    installer, disk = make(tmp_path, partitioning=data_partitioning())
    make_span(installer, disk)
    partitions, target_root = installer._plan_swap_partitions()
    data_number = next(
        number for number, entry in disk.table.items() if entry.get("name") == "DATA"
    )
    disk.table[data_number]["name"] = "OTHER-LABEL"

    with pytest.raises(
        InstallerError,
        match="io_benchmark_swap_span_state_mismatch: non-tail partition geometry differs from the plan",
    ):
        installer._validate_swap_span_table_replacement(
            disk.table, partitions, installer._planned_before_root(), target_root
        )


@pytest.mark.parametrize("boundary", ["ends-at-span-start", "starts-at-span-end"])
def test_table_replacement_accepts_partitions_touching_span_boundaries(tmp_path, boundary):
    installer, disk = make(tmp_path, partitioning=data_partitioning())
    partitions, target_root = installer._plan_swap_partitions()
    before_root = installer._planned_before_root()
    expected = installer._planned_tail_swap_span(partitions, before_root)
    assert expected is not None
    _span_number, span_start, span_size = expected
    span_end = span_start + span_size
    if boundary == "ends-at-span-start":
        other_start, other_size = span_start - 2048, 2048
    else:
        other_start, other_size = span_end, 2048
    current = {
        installer.root_number: dict(disk.table[installer.root_number]),
        100: {"start": str(other_start), "size": str(other_size), "type": LINUX, "name": "foreign"},
    }

    shape, actual_expected = installer._validate_swap_span_table_replacement(
        current, partitions, before_root, target_root
    )

    assert shape == "fresh"
    assert actual_expected == expected


def test_swap_span_identity_accepts_a_partition_starting_exactly_at_span_end(
    tmp_path, monkeypatch,
):
    installer, disk = make(tmp_path)
    span_number = make_span(installer, disk)
    partitions, target_root = installer._plan_swap_partitions()
    before_root = installer._planned_before_root()
    span_start = int(disk.table[span_number]["start"])
    short_size = max(1, int(disk.table[span_number]["size"]) // 2)
    disk.table[span_number]["size"] = str(short_size)
    disk.table[100] = {
        "start": str(span_start + short_size),
        "size": "2048",
        "type": LINUX,
        "name": "touching-boundary",
    }
    monkeypatch.setattr(installer, "_plan_swap_partitions", lambda: (partitions, target_root))
    monkeypatch.setattr(installer, "_planned_before_root", lambda: before_root)
    monkeypatch.setattr(
        installer,
        "_planned_tail_swap_span",
        lambda *_args: (span_number, span_start, short_size),
    )

    resolved = installer._swap_span_partition()

    assert resolved == (
        span_number,
        span_start,
        short_size,
        f"/dev/vda{span_number}",
        PARTUUID,
    )


@pytest.mark.parametrize("missing", ["start", "size"])
def test_replacement_refuses_a_foreign_partition_missing_either_geometry_field(
    tmp_path, missing,
):
    installer, disk = make(tmp_path)
    partitions, target_root = installer._plan_swap_partitions()
    current = {number: dict(entry) for number, entry in disk.table.items()}
    foreign = {"type": LINUX, "name": "foreign"}
    if missing == "start":
        foreign["size"] = "2048"
    else:
        foreign["start"] = str(ROOT_START + ROOT_SIZE + 2048)
    current[100] = foreign

    with pytest.raises(
        InstallerError,
        match="io_benchmark_swap_span_geometry_mismatch: another partition has incomplete geometry",
    ):
        installer._validate_swap_span_table_replacement(
            current, partitions, installer._planned_before_root(), target_root
        )


@pytest.mark.parametrize("missing", ["start", "size"])
def test_span_identity_refuses_a_foreign_partition_missing_either_geometry_field(
    tmp_path, monkeypatch, missing,
):
    installer, disk = make(tmp_path)
    span_number = make_span(installer, disk)
    partitions, target_root = installer._plan_swap_partitions()
    before_root = installer._planned_before_root()
    expected = installer._planned_tail_swap_span(partitions, before_root)
    assert expected is not None
    live = {
        installer.root_number: dict(disk.table[installer.root_number]),
        span_number: dict(disk.table[span_number]),
        100: {"type": LINUX, "name": "foreign"},
    }
    if missing == "start":
        live[100]["size"] = "2048"
    else:
        live[100]["start"] = str(ROOT_START + ROOT_SIZE + 2048)
    monkeypatch.setattr(installer, "_plan_swap_partitions", lambda: (partitions, target_root))
    monkeypatch.setattr(installer, "_planned_before_root", lambda: before_root)
    monkeypatch.setattr(installer, "_planned_tail_swap_span", lambda *_args: expected)
    monkeypatch.setattr(installer, "_live_partition_entries", lambda *_args: live)

    with pytest.raises(
        InstallerError,
        match="io_benchmark_swap_span_geometry_mismatch: another partition has incomplete geometry",
    ):
        installer._swap_span_partition()


def test_live_span_partition_replacement_default_refuses_an_unplanned_tail_shape(tmp_path):
    installer, disk = make(tmp_path)
    partitions, target_root = installer._plan_swap_partitions()
    before_root = installer._planned_before_root()
    tail_number = installer._tail_swap_numbers(len(partitions), before_root)[0]
    disk.table[tail_number]["start"] = str(int(disk.table[tail_number]["start"]) + 2048)

    with pytest.raises(
        InstallerError,
        match="io_benchmark_swap_span_state_mismatch: live tail is neither the planned swaps nor their exact span",
    ):
        installer._validate_swap_span_table_replacement(
            disk.table, partitions, before_root, target_root
        )
    # The table writer keeps the same default: a caller that omits the flag (resume) writes nothing.
    with pytest.raises(
        InstallerError,
        match="io_benchmark_swap_span_state_mismatch: live tail is neither the planned swaps nor their exact span",
    ):
        installer._apply_known_swap_shape(tail_swap_span=True)
    assert sfdisk_write_count(disk) == 0


def test_extent_probe_and_findmnt_actions_are_non_dangerous(tmp_path):
    installer, disk = make(tmp_path)
    span_number = make_span(installer, disk)

    installer._probe_swap_span_extent(2048, 4096)
    installer._assert_swap_span_partitions_quiescent(disk.table, [span_number])

    extent = next(
        action for action in disk.planned
        if Path(action.argv[0]).name == "blkid" and "-O" in action.argv
    )
    findmnt = next(
        action for action in disk.planned if Path(action.argv[0]).name == "findmnt"
    )
    assert extent.dangerous is False
    assert findmnt.dangerous is False


def test_partuuid_read_action_is_non_dangerous(tmp_path):
    installer, disk = make(tmp_path)
    make_span(installer, disk)

    installer._swap_span_partition()

    action = next(
        action for action in disk.planned
        if Path(action.argv[0]).name == "blkid" and "PARTUUID" in action.argv
    )
    assert action.dangerous is False


def test_default_dry_run_swap_shape_does_not_record_a_table_write(tmp_path, monkeypatch):
    installer, disk = make(tmp_path)
    enable_dry_run_table_view(installer, disk, monkeypatch)

    installer._apply_known_swap_shape()

    assert sfdisk_write_count(disk) == 0


@pytest.mark.parametrize("condition", ["tail-span", "record-only"])
def test_dry_run_records_a_table_write_when_either_record_condition_is_requested(
    tmp_path, monkeypatch, condition,
):
    installer, disk = make(tmp_path)
    if condition == "tail-span":
        disk.load_entries({installer.root_number: dict(disk.table[installer.root_number])})
    enable_dry_run_table_view(installer, disk, monkeypatch)

    if condition == "tail-span":
        installer._apply_known_swap_shape(tail_swap_span=True)
    else:
        installer._apply_known_swap_shape(record_in_dry_run=True)

    assert sfdisk_write_count(disk) == 1


def test_dry_run_span_cycle_records_both_table_writes(tmp_path, monkeypatch):
    installer, disk = make(tmp_path)
    enable_dry_run_table_view(installer, disk, monkeypatch)

    installer._prepare_swap_span_benchmark()

    assert sfdisk_write_count(disk) == 2


def test_dry_run_regular_swap_to_span_records_deletions_and_span_update(tmp_path, monkeypatch):
    installer, disk = make(tmp_path)
    partitions, _target_root = installer._plan_swap_partitions()
    before_root = installer._planned_before_root()
    tail_numbers = set(installer._tail_swap_numbers(len(partitions), before_root))
    span_number = min(tail_numbers)
    enable_dry_run_table_view(installer, disk, monkeypatch)

    installer._apply_known_swap_shape(tail_swap_span=True, record_in_dry_run=True)

    assert partx_numbers(disk, "-d") == tail_numbers - {span_number}
    assert span_number in partx_numbers(disk, "-u")
    assert span_number not in partx_numbers(disk, "-a")


def test_dry_run_fresh_span_records_data_and_span_as_new(tmp_path, monkeypatch):
    installer, disk = make(tmp_path, partitioning=data_partitioning())
    data_number = next(
        number for number, entry in disk.table.items() if entry.get("name") == "DATA"
    )
    partitions, _target_root = installer._plan_swap_partitions()
    before_root = installer._planned_before_root()
    expected_span = installer._planned_tail_swap_span(partitions, before_root)
    assert expected_span is not None
    disk.load_entries({installer.root_number: dict(disk.table[installer.root_number])})
    enable_dry_run_table_view(installer, disk, monkeypatch)

    installer._apply_known_swap_shape(tail_swap_span=True)

    assert {data_number, expected_span[0]} <= partx_numbers(disk, "-a")


def test_dry_run_span_to_swaps_updates_the_span_number_and_adds_remaining_swaps(
    tmp_path, monkeypatch,
):
    installer, disk = make(tmp_path, partitioning=data_partitioning())
    span_number = make_span(installer, disk)
    partitions, _target_root = installer._plan_swap_partitions()
    before_root = installer._planned_before_root()
    tail_numbers = set(installer._tail_swap_numbers(len(partitions), before_root))
    enable_dry_run_table_view(installer, disk, monkeypatch)

    installer._apply_known_swap_shape(record_in_dry_run=True)

    assert span_number in partx_numbers(disk, "-u")
    assert span_number not in partx_numbers(disk, "-a")
    assert tail_numbers - {span_number} <= partx_numbers(disk, "-a")


def test_real_regular_swap_to_span_records_deletions_and_span_update(tmp_path):
    installer, disk = make(tmp_path)
    partitions, _target_root = installer._plan_swap_partitions()
    before_root = installer._planned_before_root()
    tail_numbers = set(installer._tail_swap_numbers(len(partitions), before_root))
    span_number = min(tail_numbers)

    installer._apply_known_swap_shape(tail_swap_span=True)

    assert partx_numbers(disk, "-d") == tail_numbers - {span_number}
    assert span_number in partx_numbers(disk, "-u")
    assert span_number not in partx_numbers(disk, "-a")


def test_real_fresh_span_records_data_and_span_as_new(tmp_path):
    installer, disk = make(tmp_path, partitioning=data_partitioning())
    data_number = next(
        number for number, entry in disk.table.items() if entry.get("name") == "DATA"
    )
    partitions, _target_root = installer._plan_swap_partitions()
    before_root = installer._planned_before_root()
    expected_span = installer._planned_tail_swap_span(partitions, before_root)
    assert expected_span is not None
    disk.load_entries({installer.root_number: dict(disk.table[installer.root_number])})

    installer._apply_known_swap_shape(tail_swap_span=True)

    assert {data_number, expected_span[0]} <= partx_numbers(disk, "-a")


def test_real_span_to_swaps_updates_span_and_adds_remaining_swaps(tmp_path):
    installer, disk = make(tmp_path, partitioning=data_partitioning())
    span_number = make_span(installer, disk)
    partitions, _target_root = installer._plan_swap_partitions()
    before_root = installer._planned_before_root()
    tail_numbers = set(installer._tail_swap_numbers(len(partitions), before_root))

    installer._apply_known_swap_shape()

    assert span_number in partx_numbers(disk, "-u")
    assert span_number not in partx_numbers(disk, "-a")
    assert tail_numbers - {span_number} <= partx_numbers(disk, "-a")


@pytest.mark.parametrize("dry_run", [False, True])
@pytest.mark.parametrize("change_name", [False, True])
def test_retained_pre_root_swap_update_numbers_follow_geometry_and_name(
    tmp_path, monkeypatch, dry_run, change_name,
):
    installer, disk = make(tmp_path)
    install_pre_root_swap(installer, disk)
    retained_number = 2
    disk.table[retained_number]["name"] = "retained"
    original_write_plan = installer._write_sfdisk_plan

    def write_plan(partitions, new_root_size, *, tail_swap_span=False):
        text = original_write_plan(partitions, new_root_size, tail_swap_span=tail_swap_span)
        if change_name and tail_swap_span:
            return text.replace('name="retained"', 'name="changed"', 1)
        return text

    monkeypatch.setattr(installer, "_write_sfdisk_plan", write_plan)
    if dry_run:
        enable_dry_run_table_view(installer, disk, monkeypatch)

    installer._apply_known_swap_shape(tail_swap_span=True)

    updates = partx_numbers(disk, "-u")
    assert (retained_number in updates) is change_name


def test_retained_pre_root_active_swap_does_not_block_span_conversion(tmp_path):
    installer, disk = make(tmp_path)
    install_pre_root_swap(installer, disk)
    disk.swap_output = "/dev/vda2 partition 1G -2\n"

    installer._apply_known_swap_shape(tail_swap_span=True)

    assert len(disk.table_writes) == 1
    assert disk.table[2]["start"] == "2048"
    assert any(entry.get("name") == SWAP_SPAN_PARTLABEL for entry in disk.table.values())


@pytest.mark.parametrize("operation", ["existing-span", "span-to-swaps"])
def test_extent_probe_runs_only_for_first_swaps_to_span_conversion(tmp_path, operation):
    installer, disk = make(tmp_path)
    if operation == "existing-span":
        make_span(installer, disk)
        installer._apply_known_swap_shape(tail_swap_span=True)
    else:
        make_span(installer, disk)
        installer._apply_known_swap_shape()

    assert not any(
        Path(action.argv[0]).name == "blkid" and "-O" in action.argv
        for action in disk.planned
    )


def test_wipe_refuses_when_the_temporary_span_is_active(tmp_path):
    installer, disk = make(tmp_path)
    span_number = make_span(installer, disk)
    set_benchmark_step(installer, "success", span_identity=True)
    disk.swap_output = f"/dev/vda{span_number} partition 32G -2\n"

    with pytest.raises(InstallerError, match="io_benchmark_swap_span_partition_in_use"):
        installer._wipe_swap_span()

    assert not any(Path(action.argv[0]).name == "wipefs" for action in disk.planned)


def test_wipe_does_not_treat_an_active_retained_pre_root_swap_as_affected(tmp_path):
    installer, disk = make(tmp_path)
    install_pre_root_swap(installer, disk)
    make_span(installer, disk)
    set_benchmark_step(installer, "success", span_identity=True)
    disk.swap_output = "/dev/vda2 partition 1G -2\n"

    installer._wipe_swap_span()

    wipe = next(action for action in disk.planned if Path(action.argv[0]).name == "wipefs")
    assert wipe.dangerous is True


def test_wipe_refuses_an_active_tail_swap_starting_exactly_at_root_end(
    tmp_path, monkeypatch,
):
    installer, disk = make(tmp_path)
    span_number = make_span(installer, disk)
    number, start, size, device, partuuid = installer._swap_span_partition()
    set_benchmark_step(installer, "success", span_identity=True)
    root_end = int(disk.table[installer.root_number]["start"]) + int(
        disk.table[installer.root_number]["size"]
    )
    monkeypatch.setattr(
        installer,
        "_swap_span_partition",
        lambda: (number, start, size, device, partuuid),
    )
    disk.table[100] = {
        "start": str(root_end),
        "size": "2048",
        "type": installer_module.SWAP_TYPE_GUID,
        "name": "",
    }
    disk.swap_output = "/dev/vda100 partition 1M -2\n"

    with pytest.raises(InstallerError, match="io_benchmark_swap_span_partition_in_use"):
        installer._wipe_swap_span()

    assert span_number == number
    assert not any(Path(action.argv[0]).name == "wipefs" for action in disk.planned)


def test_pre_benchmark_wipefs_action_is_dangerous(tmp_path):
    installer, disk = make(tmp_path)
    make_span(installer, disk)

    installer._run_io_benchmark()

    wipe = next(
        action for action in disk.planned
        if Path(action.argv[0]).name == "wipefs" and "clear blank-or-swap" in action.description
    )
    assert wipe.dangerous is True


def test_four_field_fstab_swap_row_gets_both_boot_guards():
    row = f"PARTUUID={PARTUUID} none swap sw"

    normalized = installer_module.Installer._swap_fstab_entry(row)

    assert normalized.split() == [
        f"PARTUUID={PARTUUID}",
        "none",
        "swap",
        "sw,nofail,x-systemd.device-timeout=5s",
    ]


def test_no_tail_plan_refuses_a_tail_swap_starting_exactly_at_root_end(tmp_path):
    installer, disk = make(tmp_path, swap_disk_total_gb=1, swap_partition_count=1)
    install_pre_root_swap(installer, disk, size_gib=1)
    root_end = int(disk.table[installer.root_number]["start"]) + int(
        disk.table[installer.root_number]["size"]
    )
    disk.table[4] = {
        "start": str(root_end),
        "size": "2048",
        "type": installer_module.SWAP_TYPE_GUID,
        "name": "",
    }

    with pytest.raises(
        InstallerError,
        match="io_benchmark_swap_span_state_mismatch: tail swaps exist but the plan has no tail span",
    ):
        installer._prepare_swap_span_benchmark()

    assert not disk.table_writes


@pytest.mark.parametrize(
    ("status", "detail_kind"),
    [("failed", "unrelated"), ("warned", "mentions-unreapable")],
)
def test_unreapable_resume_refusal_requires_failed_status_and_matching_detail(
    tmp_path, status, detail_kind,
):
    installer, disk = make(tmp_path)
    make_span(installer, disk)
    number, start, size, _device, partuuid = installer._swap_span_partition()
    if detail_kind == "unrelated":
        detail = "a different benchmark failure"
    else:
        detail = (
            f"PARTUUID={partuuid} start={start} size={size} sectors profile=quick; "
            "iobench could not be reaped"
        )
    installer._mark_step("io_benchmark", status, detail)

    installer._prepare_swap_span_benchmark()

    assert step(installer)["status"] in {"success", "warned"}
    assert number == next(
        part_number for part_number, entry in disk.table.items()
        if entry.get("type", "").lower() == installer_module.SWAP_TYPE_GUID.lower()
        and int(entry.get("start", "-1")) >= ROOT_START + ROOT_SIZE
    )
