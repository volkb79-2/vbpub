"""Hermetic tests for the disk layout policy (pure arithmetic, no devices)."""

from __future__ import annotations

import pytest

from debian_install_v2 import layout_policy as lp
from debian_install_v2.config import ConfigError, load_config
from debian_install_v2.disk_layout import GIB
from debian_install_v2.installer import InstallerError

from .test_disk_policy import make_planner

S = lp.SECTORS_PER_GIB


def test_min_root_rule_scales_with_disk_and_is_bounded():
    assert lp.min_root_gib(20) == 8
    assert lp.min_root_gib(128) == 26
    assert lp.min_root_gib(512) == 100
    assert lp.min_root_gib(4096) == 100


def test_recommended_layout_for_512_gib_matches_documented_numbers():
    rec = lp.recommended_layout(512, swap_gib=16)
    assert rec.root_gib == 160
    assert rec.cow_gib == 168
    assert rec.swap_gib == 16
    assert rec.reserve_gib == pytest.approx(512 - 1.25 - 160 - 168 - 16)
    assert rec.root_gib >= lp.min_root_gib(512)


def test_recommended_layout_refuses_when_pieces_do_not_fit():
    with pytest.raises(lp.LayoutPolicyError):
        lp.recommended_layout(64, swap_gib=60)


def test_recommended_root_is_clamped():
    assert lp.recommended_root_gib(40) == 24
    assert lp.recommended_root_gib(4096) == 200


def test_old_r1002_layout_is_refused():
    # vda3 40G root, swap in the middle, cow after it: swap is not last.
    disk = 512 * S
    root = (2048 + 1220608, 40 * S)
    swap = [(root[0] + root[1] + i * 4 * S, 4 * S) for i in range(4)]
    cow = (swap[-1][0] + swap[-1][1], disk - (swap[-1][0] + swap[-1][1]) - 2048)
    with pytest.raises(lp.LayoutPolicyError, match="last allocation"):
        lp.validate_swap_last(swap, [root, cow], disk)
    with pytest.raises(lp.LayoutPolicyError, match="below the minimum"):
        lp.validate_root_size(root[1], disk)


def test_swap_with_free_space_after_it_is_refused():
    disk = 512 * S
    swap = [(100 * S, 16 * S)]
    with pytest.raises(lp.LayoutPolicyError, match="END of the disk"):
        lp.validate_swap_last(swap, [(2048, 50 * S)], disk)


def test_new_layout_with_gap_before_end_swap_is_accepted():
    disk = 512 * S
    swap_start = disk - 2048 - 16 * S
    lp.validate_swap_last([(swap_start, 16 * S)], [(2048, 160 * S), (200 * S, 168 * S)], disk)
    lp.validate_root_size(160 * S, disk)


def test_config_accepts_dev_host_and_rejects_bad_combinations():
    assert load_config(raw_json='{"disk_layout":"dev-host"}').disk_layout == "dev-host"
    assert load_config(raw_json="{}").disk_layout == "full-root"
    with pytest.raises(ConfigError, match="disk_layout"):
        load_config(raw_json='{"disk_layout":"swap-middle"}')
    with pytest.raises(ConfigError, match="dev-host"):
        load_config(raw_json='{"disk_layout":"dev-host","swap_policy":"off"}')
    with pytest.raises(ConfigError, match="dev-host"):
        load_config(raw_json='{"disk_layout":"dev-host","root_resize_policy":"grow-only"}')


def test_planner_dev_host_places_swap_last_and_root_by_rule(tmp_path):
    installer = make_planner(
        tmp_path, disk_gib=512, root_gib=500, swap_disk_total_gb=16, swap_partition_count=4,
    )
    object.__setattr__(installer.config, "disk_layout", "dev-host")
    partitions, root = installer._plan_swap_partitions()
    disk_sectors, root_start, _ = installer._disk_facts()
    assert root * 512 == 160 * GIB - 0 or abs(root * 512 - 160 * GIB) < GIB
    assert len(partitions) == 4
    last_end = partitions[-1][0] + partitions[-1][1]
    assert disk_sectors - last_end <= lp.SWAP_END_SLACK_SECTORS
    # the unallocated gap (cow area + reserve) is large and sits between root and swap
    gap = partitions[0][0] - (root_start + root)
    assert gap * 512 > 300 * GIB


def test_planner_refuses_root_cap_below_minimum(tmp_path):
    installer = make_planner(
        tmp_path, disk_gib=512, root_gib=500, root_max_size_gb=40,
        swap_disk_total_gb=16, preserve_root_size_gb=10,
    )
    with pytest.raises(InstallerError, match="below the minimum"):
        installer._plan_swap_partitions()


def test_planner_default_layout_still_ends_with_swap(tmp_path):
    installer = make_planner(tmp_path, disk_gib=64, root_gib=63, swap_disk_total_gb=8)
    partitions, _root = installer._plan_swap_partitions()
    disk_sectors, _s, _r = installer._disk_facts()
    assert disk_sectors - (partitions[-1][0] + partitions[-1][1]) <= lp.SWAP_END_SLACK_SECTORS
