from __future__ import annotations

from pathlib import Path

import pytest

from conftest import fixture_root
from groop.collect.host import (
    _zfs_arc_compute_hit_ratio,
    _zfs_arc_metrics,
    collect_host,
    collect_host_meta,
    reset_zfs_arc_rate_state,
)
from groop.collect.collector import Collector
from groop.config import DamonConfig, GroopConfig
from groop.model import Entity, EntityFrame, Frame, MetricValue
from groop.ui.banner import render_banner


def _arcstats_proc_root(root: Path) -> Path:
    """Create a minimal procfs tree with the ZFS arcstats fixture under spl/kstat/zfs/."""
    proc = root / "proc"
    arcpath = proc / "spl" / "kstat" / "zfs"
    arcpath.mkdir(parents=True)
    src = fixture_root() / "procfs" / "zfs" / "arcstats"
    (arcpath / "arcstats").write_text(src.read_text())
    return proc


def _base_proc(root: Path, swaps: str) -> Path:
    """Create a minimal procfs tree WITHOUT ZFS for baseline tests."""
    proc = root / "proc"
    (proc / "pressure").mkdir(parents=True)
    (proc / "meminfo").write_text(
        "\n".join(
            (
                "MemTotal:       16384 kB",
                "MemAvailable:    8192 kB",
                "SwapTotal:       4096 kB",
                "SwapFree:        1024 kB",
                "SwapCached:       128 kB",
                "Zswap:              0 kB",
                "Zswapped:           0 kB",
            )
        )
        + "\n"
    )
    (proc / "loadavg").write_text("0.10 0.20 0.30 1/2 3\n")
    (proc / "uptime").write_text("100.0 50.0\n")
    for name in ("memory", "io", "cpu"):
        (proc / "pressure" / name).write_text("some avg10=0.00 avg60=0.00 avg300=0.00 total=0\nfull avg10=0.00 avg60=0.00 avg300=0.00 total=0\n")
    (proc / "swaps").write_text(swaps)
    return proc


def _base_sys(root: Path) -> Path:
    sys = root / "sys"
    params = sys / "module" / "zswap" / "parameters"
    params.mkdir(parents=True)
    (params / "enabled").write_text("N\n")
    (params / "max_pool_percent").write_text("20\n")
    zdebug = sys / "kernel" / "debug" / "zswap"
    zdebug.mkdir(parents=True)
    (zdebug / "pool_total_size").write_text("0\n")
    (zdebug / "stored_pages").write_text("0\n")
    (sys / "block").mkdir(parents=True)
    return sys


@pytest.fixture(autouse=True)
def _reset_arc_state() -> None:
    reset_zfs_arc_rate_state()


# ── Oracle 1: Present-ZFS fixture ──────────────────────────────────────────


def test_zfs_arc_present_fixture_exact_values(tmp_path: Path) -> None:
    """Present ZFS fixture yields the five metrics with exact expected values."""
    proc = _arcstats_proc_root(tmp_path)
    metrics = _zfs_arc_metrics(proc)

    assert metrics["host_zfs_arc_size"].v == 12884901888
    assert metrics["host_zfs_arc_size"].src == "host"
    assert metrics["host_zfs_arc_target"].v == 17179869184
    assert metrics["host_zfs_arc_target"].src == "host"
    assert metrics["host_zfs_arc_max"].v == 34359738368
    assert metrics["host_zfs_arc_max"].src == "host"
    assert metrics["host_zfs_arc_min"].v == 13743895347
    assert metrics["host_zfs_arc_min"].src == "host"

    # First call: hit ratio is None (no previous sample), raw carries hit counter
    assert metrics["host_zfs_arc_hit_ratio"].v is None
    assert metrics["host_zfs_arc_hit_ratio"].src == "derived"
    assert metrics["host_zfs_arc_hit_ratio"].raw == 5000000000


# ── Oracle 2: Absent-ZFS ──────────────────────────────────────────────────


def test_zfs_arc_absent_fixture_all_unavail(tmp_path: Path) -> None:
    """Absent ZFS file yields all five metrics as v=None, src=unavail_kernel."""
    proc = tmp_path / "proc"  # no spl/kstat/zfs/arcstats at all
    metrics = _zfs_arc_metrics(proc)

    for name in ("host_zfs_arc_size", "host_zfs_arc_target", "host_zfs_arc_max", "host_zfs_arc_min", "host_zfs_arc_hit_ratio"):
        assert metrics[name].v is None, f"{name}.v should be None, got {metrics[name].v}"
        assert metrics[name].src == "unavail_kernel", f"{name}.src should be unavail_kernel, got {metrics[name].src}"


# ── Oracle 3: Malformed kstat ─────────────────────────────────────────────


def test_zfs_arc_malformed_truncated(tmp_path: Path) -> None:
    """Truncated kstat (mid-line) degrades to unavail_kernel, does not raise."""
    proc = tmp_path / "proc"
    arcpath = proc / "spl" / "kstat" / "zfs"
    arcpath.mkdir(parents=True)
    (arcpath / "arcstats").write_text("size 4\nc 4 17179869184\n")

    metrics = _zfs_arc_metrics(proc)
    for name in ("host_zfs_arc_size", "host_zfs_arc_target", "host_zfs_arc_max", "host_zfs_arc_min", "host_zfs_arc_hit_ratio"):
        assert metrics[name].v is None
        assert metrics[name].src == "unavail_kernel"


def test_zfs_arc_malformed_non_numeric(tmp_path: Path) -> None:
    """Non-numeric data column degrades to unavail_kernel, does not raise."""
    proc = tmp_path / "proc"
    arcpath = proc / "spl" / "kstat" / "zfs"
    arcpath.mkdir(parents=True)
    (arcpath / "arcstats").write_text(
        "size 4 12884901888\nc 4 bad_value\nc_max 4 34359738368\nc_min 4 13743895347\nhits 8 5000000000\nmisses 8 150000000\n"
    )

    metrics = _zfs_arc_metrics(proc)
    for name in ("host_zfs_arc_size", "host_zfs_arc_target", "host_zfs_arc_max", "host_zfs_arc_min", "host_zfs_arc_hit_ratio"):
        assert metrics[name].v is None
        assert metrics[name].src == "unavail_kernel"


def test_zfs_arc_malformed_missing_size(tmp_path: Path) -> None:
    """Missing size row degrades only the size metric to unavail_kernel, does not raise."""
    proc = tmp_path / "proc"
    arcpath = proc / "spl" / "kstat" / "zfs"
    arcpath.mkdir(parents=True)
    (arcpath / "arcstats").write_text(
        "c 4 17179869184\nc_max 4 34359738368\nc_min 4 13743895347\nhits 8 5000000000\nmisses 8 150000000\n"
    )

    metrics = _zfs_arc_metrics(proc)
    # size missing -> unavail_kernel
    assert metrics["host_zfs_arc_size"].v is None
    assert metrics["host_zfs_arc_size"].src == "unavail_kernel"
    # Other fields present -> valid
    assert metrics["host_zfs_arc_target"].v == 17179869184
    assert metrics["host_zfs_arc_max"].v == 34359738368
    assert metrics["host_zfs_arc_min"].v == 13743895347
    # hits/misses present -> hit_ratio derived (first call, no prev)
    assert metrics["host_zfs_arc_hit_ratio"].v is None
    assert metrics["host_zfs_arc_hit_ratio"].src == "derived"


# ── Oracle 4: Hit-ratio rate over two sweeps ──────────────────────────────


def test_zfs_arc_hit_ratio_rate_over_two_sweeps() -> None:
    """Two consecutive reads with known deltas produce the exact expected ratio."""
    reset_zfs_arc_rate_state()
    r1 = _zfs_arc_compute_hit_ratio(1000000, 50000)
    assert r1.v is None  # first sample, no previous
    assert r1.raw == 1000000

    r2 = _zfs_arc_compute_hit_ratio(1001000, 50100)
    h_delta = 1001000 - 1000000
    m_delta = 50100 - 50000
    expected = h_delta / (h_delta + m_delta)
    assert r2.v == pytest.approx(expected)
    assert r2.raw == 1001000


def test_zfs_arc_hit_ratio_counter_regression() -> None:
    """Counter regression (pool export/import) emits v=None and reseeds."""
    reset_zfs_arc_rate_state()
    _zfs_arc_compute_hit_ratio(1000000, 50000)  # seed
    r = _zfs_arc_compute_hit_ratio(500000, 30000)  # regression
    assert r.v is None
    assert r.raw == 500000  # reseeded to the new (lower) value


def test_zfs_arc_hit_ratio_no_delta_regression() -> None:
    """Zero delta (no new hits/misses) emits v=None."""
    reset_zfs_arc_rate_state()
    _zfs_arc_compute_hit_ratio(1000000, 50000)
    r = _zfs_arc_compute_hit_ratio(1000000, 50000)  # no change
    assert r.v is None
    assert r.raw == 1000000


# ── Oracle 5: Banner annotation ───────────────────────────────────────────


def test_zfs_arc_banner_present(tmp_path: Path) -> None:
    """Banner contains ARC segment when ZFS is present."""
    proc = _arcstats_proc_root(tmp_path)
    sys = _base_sys(tmp_path)
    _base_proc(tmp_path, "Filename\t\t\t\tType\t\tSize\t\tUsed\t\tPriority\n")
    host = collect_host(proc_root=proc, sys_root=sys)
    meta = collect_host_meta(proc_root=proc, sys_root=sys)
    frame = _minimal_frame(host=host, host_meta=meta)
    snapshot = render_banner(frame, GroopConfig())
    lines = "\n".join(snapshot.lines)
    assert "ARC" in lines or "ZFS" in lines


def test_zfs_arc_banner_absent(tmp_path: Path) -> None:
    """Banner does NOT contain ARC segment when ZFS is absent."""
    proc = _base_proc(tmp_path, "Filename\t\t\t\tType\t\tSize\t\tUsed\t\tPriority\n")
    sys = _base_sys(tmp_path)
    host = collect_host(proc_root=proc, sys_root=sys)
    meta = collect_host_meta(proc_root=proc, sys_root=sys)
    frame = _minimal_frame(host=host, host_meta=meta)
    snapshot = render_banner(frame, GroopConfig())
    lines = "\n".join(snapshot.lines)
    assert "ARC" not in lines


# ── Oracle 6: Golden frames unaffected ────────────────────────────────────


def test_zfs_arc_non_zfs_fixtures_unaffected(tmp_path: Path) -> None:
    """Non-ZFS fixtures produce the same host metrics as before the ZFS change.
    The ZFS metrics should be absent (unavail_kernel) but no other metric
    should change value or source.
    """
    proc = _base_proc(tmp_path, "Filename\t\t\t\tType\t\tSize\t\tUsed\t\tPriority\n")
    sys = _base_sys(tmp_path)
    host = collect_host(proc_root=proc, sys_root=sys)

    # Verify ZFS metrics are all unavail_kernel
    for name in ("host_zfs_arc_size", "host_zfs_arc_target", "host_zfs_arc_max", "host_zfs_arc_min", "host_zfs_arc_hit_ratio"):
        assert host[name].v is None
        assert host[name].src == "unavail_kernel"

    # Verify existing metrics are unchanged
    assert host["host_mem_total"].v == 16384 * 1024
    assert host["host_mem_available"].v == 8192 * 1024
    assert host["host_load1"].v == 0.10
    assert host["host_uptime_s"].v == 100.0
    assert host["host_zswap_enabled"].v == 0


# ── Helpers ────────────────────────────────────────────────────────────────


def _minimal_frame(*, host: dict[str, MetricValue] | None = None, host_meta: dict[str, object] | None = None) -> Frame:
    entity = Entity(key="", kind="root", parent=None)
    if host is None:
        host = {"host_mem_total": MetricValue(16000, "host")}
    return Frame(
        schema_version=1,
        ts=100.0,
        interval_s=5.0,
        host=host,
        entities={"": EntityFrame(entity=entity, metrics={})},
        host_meta=host_meta,
    )