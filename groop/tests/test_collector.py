from __future__ import annotations

import json
import shutil
from unittest.mock import patch
from pathlib import Path

import pytest

from conftest import fixture_root
from groop.collect.collector import Collector
from groop.collect.cgroup import read_text
from groop.config import GroopConfig
from groop.model import MetricValue, frame_from_jsonable, frame_to_jsonable

GAME_KEY = "system.slice/docker-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.scope"


def host_stub() -> dict[str, MetricValue]:
    return {
        "host_mem_total": MetricValue(16000, "host"),
        "host_mem_available": MetricValue(8000, "host"),
        "host_swap_total": MetricValue(4000, "host"),
        "host_swap_free": MetricValue(2000, "host"),
        "host_swapcached": MetricValue(100, "host"),
        "host_zswap_pool": MetricValue(50, "host"),
        "host_zswap_stored": MetricValue(100, "host"),
        "host_zswap_ratio": MetricValue(2.0, "host"),
        "host_disk_swap": MetricValue(0, "host"),
        "host_load1": MetricValue(0.1, "host"),
        "host_load5": MetricValue(0.2, "host"),
        "host_load15": MetricValue(0.3, "host"),
        "host_uptime_s": MetricValue(1000, "host"),
        "host_psi_mem_some_avg10": MetricValue(0.0, "host"),
        "host_psi_mem_full_avg10": MetricValue(0.0, "host"),
        "host_psi_io_some_avg10": MetricValue(0.0, "host"),
        "host_psi_io_full_avg10": MetricValue(0.0, "host"),
        "host_psi_cpu_some_avg10": MetricValue(0.0, "host"),
        "host_zswap_enabled": MetricValue(1, "host"),
        "host_zswap_max_pool_percent": MetricValue(20, "host"),
    }


def make_collector(root: Path, times: list[float]) -> Collector:
    return Collector(root, GroopConfig(interval=5.0, tiers={"prod": ["system.slice"]}, protected_services=("soulmask-paks.slice",)), lambda _cid: None, host_stub, lambda: times.pop(0))


def test_collects_gstammtisch_fixture_and_validates_metrics() -> None:
    frame = make_collector(fixture_root() / "cgroupfs" / "gstammtisch", [100.0]).collect_once()
    assert frame_from_jsonable(frame_to_jsonable(frame)) == frame
    game = frame.entities[GAME_KEY]
    assert game.entity.kind == "scope"
    assert game.entity.tier == "prod"
    assert game.metrics["ram"].v == 1800000000
    assert game.metrics["ratio"].v == 2.0
    assert game.metrics["swap_disk"].v == 38000000
    assert game.metrics["rf_z_per_s"].v is None


def test_second_sample_computes_rates_and_counter_reset_degrades(tmp_path: Path) -> None:
    root = tmp_path / "cg"
    shutil.copytree(fixture_root() / "cgroupfs" / "gstammtisch", root)
    collector = make_collector(root, [100.0, 105.0, 110.0])
    collector.collect_once()
    stat_path = root / GAME_KEY / "memory.stat"
    text = stat_path.read_text()
    stat_path.write_text(text.replace("workingset_refault_anon 500", "workingset_refault_anon 560").replace("zswpin 480", "zswpin 500").replace("workingset_refault_file 25", "workingset_refault_file 35"))
    game = collector.collect_once().entities[GAME_KEY].metrics
    assert game["rf_z_per_s"].v == pytest.approx(4.0)
    assert game["rf_d_per_s"].v == pytest.approx(8.0)
    assert game["rf_f_per_s"].v == pytest.approx(2.0)
    stat_path.write_text(text.replace("workingset_refault_anon 500", "workingset_refault_anon 10").replace("zswpin 480", "zswpin 5").replace("workingset_refault_file 25", "workingset_refault_file 1"))
    reset = collector.collect_once().entities[GAME_KEY].metrics
    assert reset["rf_z_per_s"].v is None
    assert reset["rf_d_per_s"].v is None
    assert reset["rf_f_per_s"].v is None


def test_missing_data_degrades_without_fabricating_zero() -> None:
    frame = make_collector(fixture_root() / "cgroupfs" / "gstammtisch", [100.0]).collect_once()
    broken = frame.entities["broken.slice"].metrics
    assert broken["ram"].v is None
    assert broken["ram"].src == "unavail_kernel"
    assert broken["psi_mem_full_avg10"].v is None


def test_permission_error_degrades_as_unavail_perm() -> None:
    with patch.object(Path, "read_text", side_effect=PermissionError):
        result = read_text(Path("/sys/fs/cgroup/memory.stat"))
    assert result.value is None
    assert result.src == "unavail_perm"


def test_golden_jsonl_frame_matches_fixture() -> None:
    frame = make_collector(fixture_root() / "cgroupfs" / "gstammtisch", [100.0]).collect_once()
    line = (fixture_root() / "frames" / "gstammtisch-once.jsonl").read_text().strip()
    assert json.loads(line) == {"type": "frame", **frame_to_jsonable(frame)}
