from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path

import pytest

from conftest import fixture_root
from topos.collect.collector import Collector
from topos.config import ToposConfig
from topos.damon import annotate_frame_damon
from topos.model import Frame, MetricValue
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


def _collector(*, damon_root: Path, now: float, metrics_mode: str = "full") -> Collector:
    return Collector(
        cgroup_root=fixture_root() / "cgroupfs" / "gstammtisch",
        config=ToposConfig(interval=5.0, tiers={"prod": ["system.slice"]}),
        docker_inspect=lambda _cid: None,
        host_collector=_host_stub,
        now=lambda: now,
        network_providers=(),
        proc_root=fixture_root() / "procfs" / "network",
        damon_root=damon_root,
        metrics_mode=metrics_mode,
    )


def _damon_fixture(tmp_path: Path, kind: str = "passive-vaddr") -> tuple[Path, Path, Path]:
    root = tmp_path / "kdamonds"
    shutil.copytree(fixture_root() / "damonfs" / kind / "kdamonds", root)
    proc = tmp_path / "proc"
    shutil.copytree(fixture_root() / "procfs" / "network", proc)
    cgroup = tmp_path / "cgroup"
    shutil.copytree(fixture_root() / "cgroupfs" / "gstammtisch", cgroup)
    return root, proc, cgroup


def _region(root: Path, idx: str, start: int, end: int, accesses: int, age: int) -> None:
    path = root / "0" / "contexts" / "0" / "schemes" / "0" / "tried_regions" / idx
    path.mkdir(parents=True, exist_ok=True)
    for name, value in (("start", start), ("end", end), ("nr_accesses", accesses), ("age", age)):
        (path / name).write_text(f"{value}\n")


def _collector_with_inputs(
    root: Path,
    proc: Path,
    cgroup: Path,
    now: float = 100.0,
    damon_state_dir: Path | None = None,
) -> Collector:
    return Collector(
        cgroup_root=cgroup,
        config=ToposConfig(interval=5.0, tiers={"prod": ["system.slice"]}),
        docker_inspect=lambda _cid: None,
        host_collector=_host_stub,
        now=lambda: now,
        network_providers=(),
        proc_root=proc,
        damon_root=root,
        damon_state_dir=damon_state_dir,
    )


def test_collect_once_passive_regions_observe_classifier_boundaries(tmp_path: Path) -> None:
    root, proc, cgroup = _damon_fixture(tmp_path)
    regions = root / "0" / "contexts" / "0" / "schemes" / "0" / "tried_regions"
    shutil.rmtree(regions)
    _region(root, "0", 0, 4096, 0, 1)
    _region(root, "1", 4096, 8192, 1, 40)
    _region(root, "2", 8192, 4096, 20, 1)
    (root / "0" / "contexts" / "0" / "monitoring_attrs" / "intervals" / "sample_us").write_text("0\n")
    frame = _collector_with_inputs(root, proc, cgroup).collect_once()
    session = frame.entities[GAME_KEY].damon["sessions"][0]
    assert [r["class"] for r in session["regions"]] == ["warm", "cold", "warm"]
    assert session["regions"][2]["access_rate_pct"] == 0.0
    assert [r["size_bytes"] for r in session["regions"]] == [4096, 4096, 0]
    assert frame.entities[GAME_KEY].damon["summary"]["total_bytes"] == 8192
    assert frame.entities[GAME_KEY].metrics["damon_cold_bytes"].v == 4096


def test_collect_once_stat_scheme_wins_and_empty_candidate_is_skipped(tmp_path: Path) -> None:
    root, proc, cgroup = _damon_fixture(tmp_path)
    schemes = root / "0" / "contexts" / "0" / "schemes"
    second = schemes / "1" / "tried_regions"
    second.mkdir(parents=True)
    (schemes / "1" / "action").write_text("pageout\n")
    (second / "total_bytes").write_text("999\n")
    (schemes / "2").mkdir()
    (schemes / "2" / "action").write_text("pageout\n")
    session = _collector_with_inputs(root, proc, cgroup).collect_once().entities[GAME_KEY].damon["sessions"][0]
    assert session["scheme_idx"] == 0
    assert session["scheme_action"] == "stat"
    assert session["scheme_count"] == 3


def test_collect_once_valid_scheme_uses_total_bytes_fallback(tmp_path: Path) -> None:
    root, proc, cgroup = _damon_fixture(tmp_path, "passive-paddr")
    tried = root / "0" / "contexts" / "0" / "schemes" / "0" / "tried_regions"
    shutil.rmtree(tried)
    tried.mkdir()
    (tried / "total_bytes").write_text("12345\n")
    frame = _collector_with_inputs(root, proc, cgroup).collect_once()
    assert frame.host["host_damon_warm_bytes"].v == 0
    assert frame.entities[""].damon["host_sessions"][0]["total_bytes"] == 12345


def test_collect_once_vaddr_requires_one_resolved_entity(tmp_path: Path) -> None:
    root, proc, cgroup = _damon_fixture(tmp_path)
    target = root / "0" / "contexts" / "0" / "targets" / "0" / "pid_target"
    target.write_text("not-a-pid\n")
    assert _collector_with_inputs(root, proc, cgroup).collect_once().entities[GAME_KEY].damon is None
    target.write_text("9999\n")
    assert _collector_with_inputs(root, proc, cgroup).collect_once().entities[GAME_KEY].damon is None
    target.write_text("1001\n")
    (root / "0" / "contexts" / "0" / "targets" / "1").mkdir()
    (root / "0" / "contexts" / "0" / "targets" / "1" / "pid_target").write_text("3001\n")
    assert _collector_with_inputs(root, proc, cgroup).collect_once().entities[GAME_KEY].damon is None
    shutil.rmtree(root / "0" / "contexts" / "0" / "targets" / "1")
    (cgroup / GAME_KEY / "cgroup.procs").unlink()
    session = _collector_with_inputs(root, proc, cgroup).collect_once().entities[GAME_KEY].damon["sessions"][0]
    assert session["entity_pid_count"] is None


def test_collect_once_paddr_is_host_only_and_unsupported_context_is_omitted(tmp_path: Path) -> None:
    root, proc, cgroup = _damon_fixture(tmp_path, "passive-paddr")
    bad = root / "0" / "contexts" / "1"
    bad.mkdir()
    (bad / "operations").write_text("unsupported\n")
    frame = _collector_with_inputs(root, proc, cgroup).collect_once()
    assert frame.entities[GAME_KEY].damon is None
    assert frame.entities[""].damon["host_sessions"][0]["mode"] == "paddr"
    assert frame.host["host_damon_mode"].v == 2


def test_collect_once_degrades_for_paddr_without_a_usable_scheme(tmp_path: Path) -> None:
    root, proc, cgroup = _damon_fixture(tmp_path, "passive-paddr")
    schemes = root / "0" / "contexts" / "0" / "schemes"
    shutil.rmtree(schemes)
    schemes.mkdir()
    frame = _collector_with_inputs(root, proc, cgroup).collect_once()

    session = frame.entities[""].damon["host_sessions"][0]
    assert session["mode"] == "paddr"
    assert "scheme_idx" not in session
    assert session["sample_age_s"] is None
    assert frame.host["host_damon_warm_bytes"] == MetricValue(0, "exact")
    assert frame.host["host_damon_sample_age_s"] == MetricValue(None, "unavail_kernel")


def test_collect_once_omits_vaddr_with_an_unattributable_entity(tmp_path: Path) -> None:
    root, proc, cgroup = _damon_fixture(tmp_path)
    target = root / "0" / "contexts" / "0" / "targets" / "0" / "pid_target"
    target.write_text("1001\n")
    (proc / "1001" / "cgroup").write_text("0::/missing-but-string\n")
    frame = _collector_with_inputs(root, proc, cgroup).collect_once()

    assert frame.entities[GAME_KEY].damon is None
    assert all(value.v is None for name, value in frame.entities[GAME_KEY].metrics.items() if name.startswith("damon_"))


def test_collect_once_skips_bad_regions_and_degrades_bad_proc_and_samples(tmp_path: Path) -> None:
    root, proc, cgroup = _damon_fixture(tmp_path)
    tried = root / "0" / "contexts" / "0" / "schemes" / "0" / "tried_regions"
    bad = tried / "9"
    bad.mkdir()
    for name in ("start", "end", "nr_accesses", "age"):
        (bad / name).write_text("not-a-number\n")
    (proc / "1001" / "cgroup").write_text(f"garbage\n1:memory\n0::/{GAME_KEY}\n")
    (cgroup / GAME_KEY / "cgroup.procs").write_text("not-a-pid\n1001\n")
    frame = _collector_with_inputs(root, proc, cgroup).collect_once()

    game = frame.entities[GAME_KEY]
    assert game.damon is not None
    assert game.damon["sessions"][0]["covered_pids"] == [1001]


def test_collect_once_accepts_root_cgroup_after_malformed_proc_lines(tmp_path: Path) -> None:
    root, proc, cgroup = _damon_fixture(tmp_path)
    (proc / "1001" / "cgroup").write_text("garbage\n1:memory:/irrelevant\n0::/\n")
    (cgroup / "cgroup.procs").write_text("not-a-pid\n1001\n")
    frame = _collector_with_inputs(root, proc, cgroup).collect_once()

    assert frame.entities[""].damon["sessions"][0]["entity_key"] == ""


def test_collect_once_omits_vaddr_when_no_cgroup_line_identifies_an_entity(tmp_path: Path) -> None:
    root, proc, cgroup = _damon_fixture(tmp_path)
    (proc / "1001" / "cgroup").write_text("garbage\n1:memory\n")

    frame = _collector_with_inputs(root, proc, cgroup).collect_once()

    assert frame.entities[GAME_KEY].damon is None


def test_annotate_frame_damon_preserves_paddr_host_metadata_without_root_entity(tmp_path: Path) -> None:
    root, proc, cgroup = _damon_fixture(tmp_path, "passive-paddr")
    frame = Frame(schema_version=1, ts=100.0, interval_s=5.0, host={}, entities={})

    assert annotate_frame_damon(
        frame,
        damon_root=root,
        proc_root=proc,
        cgroup_root=cgroup,
        config=ToposConfig().damon,
        now=100.0,
        state_dir=tmp_path / "state",
    ).host["host_damon_mode"] == MetricValue(2, "exact")
    assert frame.entities == {}


@pytest.mark.parametrize("marker", [None, "{", '{"owner": "other"}', '{"owner": "topos", "damon_root": "/other"}'])
def test_collect_once_reports_foreign_for_unowned_markers(tmp_path: Path, marker: str | None) -> None:
    root, proc, cgroup = _damon_fixture(tmp_path, "passive-paddr")
    state = tmp_path / "state"
    marker_path = state / "damon" / "kdamond-0.json"
    if marker is not None:
        marker_path.parent.mkdir(parents=True)
        marker_path.write_text(marker)
    frame = _collector_with_inputs(root, proc, cgroup, damon_state_dir=state).collect_once()
    assert frame.entities[""].damon["host_sessions"][0]["owner"] == "foreign"


def test_collect_once_reports_topos_for_matching_marker(tmp_path: Path) -> None:
    root, proc, cgroup = _damon_fixture(tmp_path, "passive-paddr")
    state = tmp_path / "state"
    marker = state / "damon" / "kdamond-0.json"
    marker.parent.mkdir(parents=True)
    marker.write_text(json.dumps({"owner": "topos", "damon_root": str(root)}))
    frame = _collector_with_inputs(root, proc, cgroup, damon_state_dir=state).collect_once()
    assert frame.entities[""].damon["host_sessions"][0]["owner"] == "topos"


def test_fieldlist_damon_preserves_structured_damon_block(tmp_path: Path) -> None:
    damon_root = tmp_path / "kdamonds"
    shutil.copytree(fixture_root() / "damonfs" / "passive-vaddr" / "kdamonds", damon_root)
    tried_regions = damon_root / "0" / "contexts" / "0" / "schemes" / "0" / "tried_regions"
    for path in tried_regions.rglob("*"):
        if path.is_file():
            os.utime(path, (90.0, 90.0))

    frame = _collector(damon_root=damon_root, now=100.0, metrics_mode="damon").collect_once()
    game = frame.entities[GAME_KEY]

    assert game.damon is not None
    assert game.damon["sessions"][0]["entity_key"] == GAME_KEY
    assert game.network is None
    assert game.governance is None
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
