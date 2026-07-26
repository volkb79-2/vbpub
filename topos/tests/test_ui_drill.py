from __future__ import annotations

from pathlib import Path

import pytest

from topos.config import ToposConfig
from topos.model import DockerMeta, Entity, EntityFrame, Finding, Frame, MetricValue
from topos.record.ring import HistoryRing
from topos.ui.drill import render_drill_text


KEY = "system.slice/demo.scope"


def _frame(*, damon: dict[str, object] | None = None, details: bool = True) -> Frame:
    entity = Entity(
        key=KEY,
        kind="scope",
        parent="system.slice",
        docker=DockerMeta("abc", "abcdef", "demo", "image:latest"),
        tier="prod",
        is_protected=True,
    )
    metrics = {
        "ram": MetricValue(2048, "exact"),
        "cpu_pct": MetricValue(12.5, "derived"),
        "io_r_bps": MetricValue(None, "unavail_kernel"),
        "net_rx_bps": MetricValue(100, "netns"),
        "governance_mem_min": MetricValue(1, "exact"),
        "damon_mode": MetricValue(1, "exact"),
        "damon_hot_pct": MetricValue(110, "derived"),
        "damon_warm_pct": MetricValue(-5, "derived"),
    }
    if not details:
        metrics = {"ram": MetricValue(None, "unavail_perm")}
    governance = {
        "summary": {"origin": "systemd", "drift": True, "severity": "warn"},
        "limits": {"mem_min": {"live_value": 10, "recorded_value": 20, "origin": "unit", "severity": "warn"}},
    }
    network = {"source_label": "netns", "confidence": "high", "aggregation": "sum", "unavailable_reason": "provider delayed", "proto": "tcp"}
    findings = [Finding("memory.high", "warn", "memory limit reached", "raise memory.high")]
    return Frame(1, 100.0, 5.0, {}, {KEY: EntityFrame(entity, metrics, findings, governance, network, damon)})


def _render(frame: Frame, ring: HistoryRing, root: Path) -> str:
    return render_drill_text(frame, KEY, config=ToposConfig(), ring=ring, cgroup_root=root / "cg", proc_root=root / "proc")


def test_render_drill_text_renders_real_sections_and_fixtures(tmp_path: Path) -> None:
    frame = _frame(
        damon={
            "summary": {"total_bytes": 1536},
            "sessions": [{
                "mode": "vaddr", "kdamond_idx": 0, "context_idx": 1, "scheme_idx": 2, "state": "on",
                "target_pids": [123], "covered_pid_count": 1, "entity_pid_count": 2, "sample_age_s": 1.25,
                "sample_us": 1000, "aggr_us": 2000, "update_us": 3000, "scheme_count": 3,
                "class_bytes": {"hot": 1024, "warm": 512, "cold": 0, "idle": 0},
                "class_pct": {"hot": 110, "warm": -5, "cold": 0, "idle": None},
                "regions": [{"class": "hot"}, {"class": "bad"}, "malformed"],
            }],
            "host_sessions": [{"mode": "paddr", "covered_pid_count": 3}],
        }
    )
    cg = tmp_path / "cg" / KEY
    cg.mkdir(parents=True)
    (cg / "cgroup.procs").write_text("123\n")
    proc = tmp_path / "proc" / "123"
    proc.mkdir(parents=True)
    (proc / "comm").write_text("worker\n")
    (proc / "cmdline").write_text("worker\0--serve\0")
    (proc / "status").write_text("Name:\tworker\nVmRSS:\t42 kB\nVmSwap:\t7 kB\n")
    ring = HistoryRing(8, tracked_metrics=("rf_d_per_s", "cpu_pct", "ram"))
    for cpu, ram in ((1.0, 100), (2.0, 100), (None, 300)):
        ring.append_frame(Frame(1, 100, 5, {}, {KEY: EntityFrame(frame.entities[KEY].entity, {"cpu_pct": MetricValue(cpu, "derived"), "ram": MetricValue(ram, "exact"), "rf_d_per_s": MetricValue(1, "exact")})}))
    text = _render(frame, ring, tmp_path)
    assert "DETAIL system.slice/demo.scope" in text and "name: demo" in text
    assert "DAMON" in text and "summary: total=1.5KiB" in text
    assert "session covers 1/2 pids" in text and "regions: hot=1 warm=0 cold=0 idle=0" in text
    assert "110.0%" in text and "############" in text and "0.0%" in text
    assert "GOVERNANCE" in text and "mem_min    live=10 recorded=20" in text
    assert "NETWORK" in text and "reason: provider delayed" in text and "proto: tcp" in text
    assert "HISTORY" in text and "cpu_pct      ▁█·" in text and "ram          ▁▁█" in text
    assert "FINDINGS" in text and "remedy: raise memory.high" in text
    assert "pid=123 rss=43008 swap=7168 comm=worker cmd=worker --serve" in text


@pytest.mark.parametrize("damon", [None, {}, {"sessions": ["bad"]}, {"host_sessions": ["bad"]}])
def test_render_drill_text_degrades_optional_metadata_without_fabrication(tmp_path: Path, damon: dict[str, object] | None) -> None:
    frame = _frame(damon=damon, details=False)
    text = _render(frame, HistoryRing(2), tmp_path)
    assert "DAMON" in text
    if not damon or damon == {}:
        assert "state: unavailable" in text
    assert "ram                      - [unavail_perm]" in text
    assert "no history" in text and "no visible processes" in text
    assert "FINDINGS\n  none" in text
