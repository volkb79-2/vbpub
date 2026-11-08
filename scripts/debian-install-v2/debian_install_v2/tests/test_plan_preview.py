"""Host-independent plan resolution and capacity refusal diagnostics."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from debian_install_v2 import bootstrap
from debian_install_v2.config import Config
from debian_install_v2.layout_policy import GPT_LAST_USABLE_OFFSET, IOBENCH_PARTLABEL
from debian_install_v2.plan_preview import PlanPreviewError, disk_size_bytes, resolve_plan


def test_disk_size_parser_accepts_binary_gib_and_tib_units():
    assert disk_size_bytes("20GiB") == 20 * 1024**3
    assert disk_size_bytes("512G") == 512 * 1024**3
    assert disk_size_bytes("2TiB") == 2048 * 1024**3
    with pytest.raises(PlanPreviewError, match="look like"):
        disk_size_bytes("512GB decimal")


@pytest.mark.parametrize("swap_policy", ["on", "off"])
def test_resolved_512_gib_plan_shows_data_raw_and_swap_in_disk_order(swap_policy):
    config = Config(
        partitioning={"root": 160, "data": {"kind": "partitions", "volumes": [
            {"size": "*", "fs": "btrfs", "mount": "/srv/cow", "label": "MDT_COW"},
        ]}},
        swap_policy=swap_policy,
        swap_disk_total_gb=16,
        swap_partition_count=4,
    )
    result = resolve_plan(config, disk_size="512GiB")
    rows = result["plan_table"]
    assert [row["entry"] for row in rows[:3]] == ["root", "data", "iobench raw"]
    raw = next(row for row in rows if row["partlabel"] == IOBENCH_PARTLABEL)
    assert raw["number"] == 3 and raw["gib"] == 8.0
    swaps = [row for row in rows if row["entry"] == "swap"]
    assert len(swaps) == (4 if swap_policy == "on" else 0)
    last = max(row["start"] + row["sectors"] for row in rows)
    disk_sectors = result["disk_sectors"]
    assert last <= disk_sectors - GPT_LAST_USABLE_OFFSET
    numbered = [row["number"] for row in rows if row["number"] is not None]
    assert numbered == sorted(numbered)
    for left, right in zip(rows, rows[1:]):
        assert left["start"] + left["sectors"] == right["start"]


def test_netcup_preview_uses_the_real_root_start_and_partition_number():
    config = Config(
        partitioning={"root": 160, "data": {"kind": "partitions", "volumes": [
            {"size": "*", "fs": "btrfs", "mount": "/srv/cow", "label": "MDT_COW"},
        ]}},
        swap_policy="on",
        swap_disk_total_gb=16,
        swap_partition_count=4,
    )
    result = resolve_plan(
        config,
        disk_size="512GiB",
        root_start_sector=2_500_608,
        root_number=3,
    )
    rows = result["plan_table"]
    assert [(row["entry"], row["number"], row["start"], row["sectors"]) for row in rows] == [
        ("root", 3, 2_500_608, 335_544_320),
        ("data", 4, 338_044_928, 685_363_200),
        ("iobench raw", 5, 1_023_408_128, 16_777_216),
        ("swap", 6, 1_040_185_344, 8_388_608),
        ("swap", 7, 1_048_573_952, 8_388_608),
        ("swap", 8, 1_056_962_560, 8_388_608),
        ("swap", 9, 1_065_351_168, 8_388_608),
    ]


@pytest.mark.parametrize("swap_policy", ["on", "off"])
def test_20_gib_and_2_tib_are_resolved_with_explicit_swap_size(swap_policy):
    config = Config(
        partitioning={}, swap_policy=swap_policy, swap_disk_total_gb=2,
        swap_partition_count=1, preserve_root_size_gb=9,
    )
    small = resolve_plan(config, disk_size="20GiB")
    large = resolve_plan(config, disk_size="2TiB")
    assert small["disk_size_gib"] == 20
    assert large["disk_size_gib"] == 2048
    assert next(row for row in small["plan_table"] if row["entry"] == "iobench raw")["gib"] == 8.0
    assert all(row["gib"] > 0 for row in large["plan_table"])


def test_auto_swap_requires_ram_and_fit_refusal_prints_capacity_table():
    config = Config(partitioning={}, swap_policy="on", swap_disk_total_gb=None)
    with pytest.raises(PlanPreviewError, match="needs --ram-gib"):
        resolve_plan(config, disk_size="512GiB")
    too_small = Config(
        partitioning={}, swap_policy="on", swap_disk_total_gb=16,
        preserve_root_size_gb=10,
    )
    with pytest.raises(PlanPreviewError, match="(?s)Resolved capacity table.*disk.*root-start.*usable-plan-end"):
        resolve_plan(too_small, disk_size="20GiB")


def test_root_floor_refusal_prints_the_resolvable_partition_table():
    config = Config(
        partitioning={"root": 5, "data": {"kind": "partitions", "volumes": [
            {"fs": "btrfs", "mount": "/srv/cow", "label": "MDT_COW"},
        ]}, "iobench_raw_gb": False},
        swap_policy="off",
        preserve_root_size_gb=10,
    )
    with pytest.raises(PlanPreviewError) as exc:
        resolve_plan(config, disk_size="512GiB")
    assert "below the minimum" in str(exc.value)
    assert "Resolved plan table (GiB):" in str(exc.value)
    assert "root" in str(exc.value) and "MDT_COW" in str(exc.value)


def test_disabling_raw_partition_removes_it_from_the_resolved_table():
    config = Config(partitioning={"root": "*", "iobench_raw_gb": False}, swap_policy="off")
    result = resolve_plan(config, disk_size="512GiB")
    assert [row["entry"] for row in result["plan_table"]] == ["root"]


def test_offline_resolver_accepts_partition_128_and_refuses_129():
    def config(data_count: int) -> Config:
        volumes = [
            {"size": 1, "fs": "none", "label": f"V{index:03d}"}
            for index in range(data_count)
        ]
        return Config(
            partitioning={"root": "*", "iobench_raw_gb": False, "data": {
                "kind": "partitions", "volumes": volumes,
            }},
            swap_policy="off",
        )

    accepted = resolve_plan(config(127), disk_size="512GiB")
    assert accepted["plan_table"][-1]["number"] == 128
    with pytest.raises(PlanPreviewError, match="above the GPT limit of 128"):
        resolve_plan(config(128), disk_size="512GiB")


def test_offline_resolver_defaults_to_root_partition_one_at_sector_2048():
    config = Config(partitioning={"root": "*", "iobench_raw_gb": False}, swap_policy="off")

    result = resolve_plan(config, disk_size="20GiB")

    assert result["root_number"] == 1
    assert result["root_start_sector"] == 2048
    assert result["plan_table"][0]["number"] == 1
    assert result["plan_table"][0]["start"] == 2048


@pytest.mark.parametrize("root_number", [0, 129, True])
def test_offline_resolver_rejects_root_numbers_outside_gpt_range(root_number):
    config = Config(partitioning={"root": "*", "iobench_raw_gb": False}, swap_policy="off")

    with pytest.raises(PlanPreviewError, match="root partition number must be an integer from 1 to 128"):
        resolve_plan(config, disk_size="20GiB", root_number=root_number)


def test_json_plan_includes_the_selected_root_number(monkeypatch):
    config = Config(partitioning={"root": "*", "iobench_raw_gb": False}, swap_policy="off")
    installer = SimpleNamespace(config=config)
    monkeypatch.setattr(bootstrap, "_make_installer", lambda *args, **kwargs: installer)

    class Output:
        def primary(self, value):
            self.value = value

    output = Output()
    runtime = SimpleNamespace(json_mode=True, output=output)
    args = SimpleNamespace(
        disk_size="20GiB", ram_gib=None, root_start_sector=4096, root_number=3,
        root_current_size_gib=None,
    )

    assert bootstrap._plan(args, runtime) == 0
    assert output.value["root_number"] == 3
    assert output.value["plan_table"][0]["number"] == 3


def test_live_plan_failure_diagnostic_uses_detected_root_number(monkeypatch):
    from debian_install_v2.installer import InstallerError

    config = Config(
        partitioning={"root": "*", "iobench_raw_gb": False},
        swap_policy="off",
    )

    class FakeInstaller:
        root_number = 3

        def __init__(self, installer_config):
            self.config = installer_config

        def show_plan(self):
            raise InstallerError("test diagnostic path")

        def _disk_facts(self):
            return 512 * 1024**3 // 512, 2_500_608, 40 * 1024**3 // 512

        def _memory_total_bytes(self):
            return 8 * 1024**3

    installer = FakeInstaller(config)
    monkeypatch.setattr(bootstrap, "_make_installer", lambda *args, **kwargs: installer)
    original_resolve_plan = bootstrap.resolve_plan
    observed = {}

    def record_root_number(*args, **kwargs):
        observed["root_number"] = kwargs["root_number"]
        return original_resolve_plan(*args, **kwargs)

    monkeypatch.setattr(bootstrap, "resolve_plan", record_root_number)
    runtime = SimpleNamespace(json_mode=False, output=SimpleNamespace(primary=lambda _value: None))
    args = SimpleNamespace(disk_size=None)

    with pytest.raises(bootstrap.CliFailure, match="test diagnostic path"):
        bootstrap._plan(args, runtime)
    assert observed["root_number"] == 3
