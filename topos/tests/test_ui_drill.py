from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from textual.app import App
from textual.widgets import Static

from topos.config import ToposConfig
from topos.diag.score import ScoreInput
from topos.model import DockerMeta, Entity, EntityFrame, Finding, Frame, MetricValue
from topos.record.ring import HistoryRing
from topos.ui.damon_control import DamonConfirmScreen
from topos.ui.drill import DrillDownScreen, render_drill_text


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
        findings: list[Finding] = []
    else:
        findings = [Finding("memory.high", "warn", "memory limit reached", "raise memory.high")]
    governance = {
        "summary": {"origin": "systemd", "drift": True, "severity": "warn"},
        "limits": {"mem_min": {"live_value": 10, "recorded_value": 20, "origin": "unit", "severity": "warn"}},
    }
    network = {"source_label": "netns", "confidence": "high", "aggregation": "sum", "unavailable_reason": "provider delayed", "proto": "tcp"}
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
            }, {
                "class_bytes": {"hot": 1024 ** 5},
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
    assert "session covers 1/2 pids" in text and "coverage: unattributed" in text
    assert "regions: hot=1 warm=0 cold=0 idle=0" in text and "hot=1024.0TiB" in text
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


def test_render_drill_text_omits_thresholds_for_thresholdless_score_input(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import topos.diag.score as score_mod

    input_spec = ScoreInput(
        key="thresholdless_metric", label="Thresholdless", metrics=("thresholdless_metric",),
        weight_key="thresholdless_metric", threshold_key=None, default_band=None, detail="supported without threshold",
    )
    monkeypatch.setattr(score_mod, "_INPUTS", (input_spec,))
    frame = _frame()
    frame.entities[KEY].metrics["thresholdless_metric"] = MetricValue(5, "exact")
    text = _render(frame, HistoryRing(2), tmp_path)
    contribution = next(line for line in text.splitlines() if "Thresholdless" in line)
    assert "value=5 [exact/exact]" in contribution
    assert "warn=" not in contribution and "crit=" not in contribution


def test_render_drill_text_preserves_no_history_for_all_missing_recorded_series(tmp_path: Path) -> None:
    frame = _frame()
    ring = HistoryRing(2, tracked_metrics=("rf_d_per_s", "cpu_pct", "ram"))
    ring.append_frame(Frame(
        1, 100, 5, {},
        {KEY: EntityFrame(frame.entities[KEY].entity, {
            "rf_d_per_s": MetricValue(None, "unavail_kernel"),
            "cpu_pct": MetricValue(None, "unavail_kernel"),
            "ram": MetricValue(None, "unavail_kernel"),
        })},
    ))
    text = _render(frame, ring, tmp_path)
    assert "rf_d_per_s   no history" in text
    assert "cpu_pct      no history" in text
    assert "ram          no history" in text


def test_mounted_drill_screen_surfaces_unavailable_damon_controls(tmp_path: Path) -> None:
    async def run() -> None:
        screen = DrillDownScreen(
            _frame(), KEY, config=ToposConfig(), ring=HistoryRing(2),
            cgroup_root=tmp_path / "missing-cgroup", proc_root=tmp_path / "proc",
            damon_root=tmp_path / "missing-damon", damon_state_dir=tmp_path / "state",
            damon_require_root=False,
        )
        marker = tmp_path / "state" / "damon" / "kdamond-0.json"
        marker.parent.mkdir(parents=True)
        marker.write_text("not json\n")
        app = App()
        async with app.run_test(size=(140, 40)) as pilot:
            app.push_screen(screen)
            await pilot.pause()
            await pilot.press("d")
            await pilot.pause()
            assert "DAMON CONTROL\n  start unavailable:" in str(screen.query_one("#drill-body", Static).render())
            await pilot.press("s")
            await pilot.pause()
            assert "DAMON CONTROL\n  stop unavailable:" in str(screen.query_one("#drill-body", Static).render())

    asyncio.run(run())


def test_mounted_drill_screen_reports_public_confirmation_cancellation(tmp_path: Path) -> None:
    async def run() -> None:
        cgroup = tmp_path / "cgroup" / KEY
        cgroup.mkdir(parents=True)
        (cgroup / "cgroup.procs").write_text("123\n")
        damon = tmp_path / "damon"
        damon.mkdir()
        (damon / "nr_kdamonds").write_text("1\n")
        slot = damon / "0"
        slot.mkdir()
        (slot / "state").write_text("off\n")
        screen = DrillDownScreen(
            _frame(), KEY, config=ToposConfig(), ring=HistoryRing(2),
            cgroup_root=tmp_path / "cgroup", proc_root=tmp_path / "proc",
            damon_root=damon, damon_state_dir=tmp_path / "state", damon_require_root=False,
        )
        app = App()
        async with app.run_test(size=(140, 40)) as pilot:
            app.push_screen(screen)
            await pilot.pause()
            await pilot.press("d")
            await pilot.pause()
            assert isinstance(app.screen, DamonConfirmScreen)
            await pilot.press("escape")
            await pilot.pause()
            assert app.screen is screen
            assert "DAMON CONTROL\n  start cancelled" in str(screen.query_one("#drill-body", Static).render())

    asyncio.run(run())
