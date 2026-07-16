from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

import pytest

from conftest import fixture_root
from topos.collect.collector import Collector
from topos.config import ToposConfig
from topos.model import MetricValue
from topos.record.ring import HistoryRing
from topos.ui.drill import render_drill_text

GAME_KEY = "system.slice/docker-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.scope"


def _host_stub() -> dict[str, MetricValue]:
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


def _collector(*, damon_root: Path, now: float) -> Collector:
    return Collector(
        cgroup_root=fixture_root() / "cgroupfs" / "gstammtisch",
        config=ToposConfig(interval=5.0, tiers={"prod": ["system.slice"]}),
        docker_inspect=lambda _cid: None,
        host_collector=_host_stub,
        now=lambda: now,
        network_providers=(),
        proc_root=fixture_root() / "procfs" / "network",
        damon_root=damon_root,
    )
def test_passive_vaddr_classification_and_attribution(tmp_path: Path) -> None:
    damon_root = tmp_path / "kdamonds"
    shutil.copytree(fixture_root() / "damonfs" / "passive-vaddr" / "kdamonds", damon_root)
    tried_regions = damon_root / "0" / "contexts" / "0" / "schemes" / "0" / "tried_regions"
    for path in tried_regions.rglob("*"):
        if path.is_file():
            os.utime(path, (90.0, 90.0))

    frame = _collector(damon_root=damon_root, now=100.0).collect_once()
    game = frame.entities[GAME_KEY]
    assert game.metrics["damon_hot_bytes"] == MetricValue(69632, "exact")
    assert game.metrics["damon_warm_bytes"] == MetricValue(8192, "exact")
    assert game.metrics["damon_cold_bytes"] == MetricValue(16384, "exact")
    assert game.metrics["damon_idle_bytes"] == MetricValue(32768, "exact")
    assert game.metrics["damon_hot_pct"].v == pytest.approx(54.8387, rel=1e-4)
    assert game.metrics["damon_warm_pct"].v == pytest.approx(6.4516, rel=1e-4)
    assert game.metrics["damon_cold_pct"].v == pytest.approx(12.9032, rel=1e-4)
    assert game.metrics["damon_idle_pct"].v == pytest.approx(25.8064, rel=1e-4)
    assert game.metrics["damon_sample_age_s"] == MetricValue(10.0, "exact")
    assert game.metrics["damon_mode"] == MetricValue(1, "exact")
    assert game.damon is not None
    session = game.damon["sessions"][0]
    assert session["entity_key"] == GAME_KEY
    assert session["covered_pids"] == [1001]
    assert session["covered_pid_count"] == 1
    assert session["entity_pid_count"] == 2


def test_passive_no_root_degrades_without_fabricating_values(tmp_path: Path) -> None:
    frame = _collector(damon_root=tmp_path / "missing" / "kdamonds", now=100.0).collect_once()
    game = frame.entities[GAME_KEY]
    assert game.metrics["damon_hot_bytes"].v is None
    assert game.metrics["damon_hot_bytes"].src == "unavail_kernel"
    assert game.damon is None


def test_passive_paddr_stays_host_metadata_only(tmp_path: Path) -> None:
    damon_root = tmp_path / "kdamonds"
    shutil.copytree(fixture_root() / "damonfs" / "passive-paddr" / "kdamonds", damon_root)
    for path in (damon_root / "0" / "contexts" / "0" / "schemes" / "0" / "tried_regions").rglob("*"):
        if path.is_file():
            os.utime(path, (90.0, 90.0))
    frame = _collector(damon_root=damon_root, now=100.0).collect_once()
    game = frame.entities[GAME_KEY]
    root = frame.entities[""]
    assert game.metrics["damon_hot_bytes"].v is None
    assert game.metrics["damon_hot_bytes"].src == "unavail_kernel"
    assert frame.host["host_damon_warm_bytes"] == MetricValue(4096, "exact")
    assert frame.host["host_damon_idle_bytes"] == MetricValue(8192, "exact")
    assert frame.host["host_damon_warm_pct"].v == pytest.approx(33.3333, rel=1e-4)
    assert frame.host["host_damon_idle_pct"].v == pytest.approx(66.6667, rel=1e-4)
    assert frame.host["host_damon_sample_age_s"] == MetricValue(10.0, "exact")
    assert frame.host["host_damon_mode"] == MetricValue(2, "exact")
    assert root.damon is not None
    assert root.damon["host_sessions"][0]["mode"] == "paddr"
    assert root.damon["host_sessions"][0]["owner"] == "foreign"


def test_render_drill_text_includes_damon_panel(tmp_path: Path) -> None:
    damon_root = tmp_path / "kdamonds"
    shutil.copytree(fixture_root() / "damonfs" / "passive-vaddr" / "kdamonds", damon_root)
    tried_regions = damon_root / "0" / "contexts" / "0" / "schemes" / "0" / "tried_regions"
    for path in tried_regions.rglob("*"):
        if path.is_file():
            os.utime(path, (95.0, 95.0))
    frame = _collector(damon_root=damon_root, now=100.0).collect_once()
    text = render_drill_text(
        frame,
        GAME_KEY,
        config=ToposConfig(),
        ring=HistoryRing(capacity=4, tracked_metrics=("damon_hot_pct",)),
        cgroup_root=fixture_root() / "cgroupfs" / "gstammtisch",
        proc_root=fixture_root() / "procfs" / "network",
    )
    assert "DAMON" in text
    assert "mode=vaddr" in text
    assert "session covers 1/2 pids of this entity" in text
    assert "hot=68.0KiB" in text


def test_passive_source_audit_confirms_read_only_access() -> None:
    source = (fixture_root().parents[1] / "src" / "topos" / "damon" / "passive.py").read_text()
    assert "write_text(" not in source
    assert ".write(" not in source
    assert "commit" not in source
    assert re.search(r"open\([^)]*,\s*['\"][^'\"]*[wa+][^'\"]*['\"]", source) is None
