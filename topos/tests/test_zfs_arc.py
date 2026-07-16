from __future__ import annotations

from pathlib import Path

import pytest

from conftest import fixture_root
from topos.collect.host import (
    _zfs_arc_metrics,
    collect_host,
    collect_host_meta,
)
from topos.collect.collector import Collector
from topos.config import ToposConfig
from topos.model import Entity, EntityFrame, Frame, MetricValue
from topos.ui.banner import render_banner


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


# --- Oracle 1: Present-ZFS fixture ---------------------------------------


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


# --- Oracle 2: Absent-ZFS ------------------------------------------------


def test_zfs_arc_absent_fixture_all_unavail(tmp_path: Path) -> None:
    """Absent ZFS file yields all five metrics as v=None, src=unavail_kernel."""
    proc = tmp_path / "proc"  # no spl/kstat/zfs/arcstats at all
    metrics = _zfs_arc_metrics(proc)

    for name in ("host_zfs_arc_size", "host_zfs_arc_target", "host_zfs_arc_max", "host_zfs_arc_min", "host_zfs_arc_hit_ratio"):
        assert metrics[name].v is None, f"{name}.v should be None, got {metrics[name].v}"
        assert metrics[name].src == "unavail_kernel", f"{name}.src should be unavail_kernel, got {metrics[name].src}"


# --- Oracle 3: Malformed kstat -------------------------------------------


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


# --- Oracle 4: Hit-ratio rate over two sweeps ----------------------------
#
# The ratio is a rate, so it only exists on the Collector that holds the
# previous sample. These drive the real `collect_once()` path and assert on
# the Frame, not on a helper: a test that pokes a private accumulator would
# pass even if nothing ever wired it into a frame.


def _write_arcstats(proc: Path, *, hits: int, misses: int) -> None:
    arcpath = proc / "spl" / "kstat" / "zfs"
    arcpath.mkdir(parents=True, exist_ok=True)
    (arcpath / "arcstats").write_text(
        "\n".join(
            (
                "13 1 0x01 98 26656 4319023 1234567890",
                "name                            type data",
                "size                            4    12884901888",
                "c                               4    17179869184",
                "c_max                           4    34359738368",
                "c_min                           4    13743895347",
                f"hits                            4    {hits}",
                f"misses                          4    {misses}",
            )
        )
        + "\n"
    )


def _zfs_collector(tmp_path: Path, *, hits: int, misses: int) -> tuple[Collector, Path]:
    """A Collector whose procfs has ZFS, so collect_once() sees the ARC kstat."""
    proc = _base_proc(tmp_path, "Filename\t\t\t\tType\t\tSize\t\tUsed\t\tPriority\n")
    _write_arcstats(proc, hits=hits, misses=misses)
    cgroup_root = tmp_path / "cgroup"
    cgroup_root.mkdir()
    collector = Collector(
        cgroup_root,
        ToposConfig(interval=5.0),
        network_providers=(),
        now=lambda: 100.0,
        proc_root=proc,
        sys_root=_base_sys(tmp_path),
        damon_root=fixture_root() / "damonfs" / "no-root" / "kdamonds",
    )
    return collector, proc


def _hit_ratio(collector: Collector) -> MetricValue:
    return collector.collect_once().host["host_zfs_arc_hit_ratio"]


def test_zfs_arc_hit_ratio_rate_over_two_sweeps(tmp_path: Path) -> None:
    """Two sweeps with known deltas put the exact expected ratio in the frame."""
    collector, proc = _zfs_collector(tmp_path, hits=1_000_000, misses=50_000)

    first = _hit_ratio(collector)
    assert first.v is None, "first sample has no baseline to diff against"
    assert first.raw == 1_000_000

    _write_arcstats(proc, hits=1_001_000, misses=50_100)
    second = _hit_ratio(collector)
    assert second.v == pytest.approx(1000 / (1000 + 100))
    assert second.raw == 1_001_000


def test_zfs_arc_hit_ratio_counter_regression(tmp_path: Path) -> None:
    """A pool export/import walks the counters back: emit None and reseed."""
    collector, proc = _zfs_collector(tmp_path, hits=1_000_000, misses=50_000)
    _hit_ratio(collector)  # seed
    _write_arcstats(proc, hits=1_001_000, misses=50_100)
    assert _hit_ratio(collector).v is not None  # seeded, ratio flowing

    _write_arcstats(proc, hits=500_000, misses=30_000)  # counters go backwards
    regressed = _hit_ratio(collector)
    assert regressed.v is None, "a regression must not yield a negative or absurd ratio"
    assert regressed.raw == 500_000  # reseeded to the new, lower value

    # Reseeded, so the very next sweep produces a ratio again from the new base.
    _write_arcstats(proc, hits=500_900, misses=30_100)
    assert _hit_ratio(collector).v == pytest.approx(900 / (900 + 100))


def test_zfs_arc_hit_ratio_idle_interval(tmp_path: Path) -> None:
    """No ARC accesses in the interval: there is no ratio to report, not 0.0."""
    collector, _proc = _zfs_collector(tmp_path, hits=1_000_000, misses=50_000)
    _hit_ratio(collector)  # seed
    idle = _hit_ratio(collector)  # identical counters
    assert idle.v is None
    assert idle.raw == 1_000_000


def test_zfs_arc_hit_ratio_state_is_per_collector(tmp_path: Path) -> None:
    """A fresh Collector must not inherit another Collector's counter baseline.

    Regression guard: the ARC rate was first built on a module-level global, so
    a second Collector in the same process reported a ratio on its very first
    sweep, computed against a previous Collector's counters.
    """
    warm, proc = _zfs_collector(tmp_path, hits=1_000_000, misses=50_000)
    _hit_ratio(warm)  # seed
    _write_arcstats(proc, hits=1_001_000, misses=50_100)
    assert _hit_ratio(warm).v is not None  # warm collector has a baseline

    fresh, _ = _zfs_collector(tmp_path / "second", hits=1_002_000, misses=50_200)
    assert _hit_ratio(fresh).v is None, "a fresh Collector has no baseline of its own"


# --- Oracle 5: Banner annotation -----------------------------------------


def test_zfs_arc_banner_present(tmp_path: Path) -> None:
    """Banner contains ARC segment when ZFS is present."""
    proc = _arcstats_proc_root(tmp_path)
    sys = _base_sys(tmp_path)
    _base_proc(tmp_path, "Filename\t\t\t\tType\t\tSize\t\tUsed\t\tPriority\n")
    host = collect_host(proc_root=proc, sys_root=sys)
    # Prove collection code ran: ARC metrics exist in the host dict
    assert "host_zfs_arc_size" in host
    assert host["host_zfs_arc_size"].v == 12884901888
    meta = collect_host_meta(proc_root=proc, sys_root=sys)
    frame = _minimal_frame(host=host, host_meta=meta)
    snapshot = render_banner(frame, ToposConfig())

    # Assert the rendered cells, not just that the substring "ARC" occurs:
    # `assert "ARC" in lines` passes even when both figures render wrong. A
    # single read carries no hit ratio (a ratio needs two samples), so the
    # segment is size/max only -- see the two-sweep test below for `(hit N%)`.
    assert "ARC 12.0GiB/32.0GiB" in snapshot.lines


def test_zfs_arc_banner_hit_ratio_reaches_the_banner(tmp_path: Path) -> None:
    """The derived ratio must render in the banner, not just sit in the frame.

    Guards the wiring end to end: Collector._apply_zfs_arc_rate -> Frame.host ->
    banner. The first sweep has no baseline, so the segment carries no ratio;
    the second sweep must render `(hit 91%)` from a 1000/100 hit/miss delta.
    """
    collector, proc = _zfs_collector(tmp_path, hits=1_000_000, misses=50_000)

    first = render_banner(collector.collect_once(), ToposConfig())
    assert "ARC 12.0GiB/32.0GiB" in first.lines, "no ratio before a baseline exists"

    _write_arcstats(proc, hits=1_001_000, misses=50_100)
    second = render_banner(collector.collect_once(), ToposConfig())
    assert "ARC 12.0GiB/32.0GiB (hit 91%)" in second.lines


def test_zfs_arc_banner_absent(tmp_path: Path) -> None:
    """Banner does NOT contain ARC segment when ZFS is absent.

    Also verifies the ARC collection code ran: the metrics exist in the host
    dict as unavail_kernel. If the collection were deleted/stubbed, the metrics
    would not be present at all and this test would fail with KeyError.
    """
    proc = _base_proc(tmp_path, "Filename\t\t\t\tType\t\tSize\t\tUsed\t\tPriority\n")
    sys = _base_sys(tmp_path)
    host = collect_host(proc_root=proc, sys_root=sys)
    # Prove collection code ran: ARC metrics exist as unavail_kernel
    assert "host_zfs_arc_size" in host
    assert host["host_zfs_arc_size"].v is None
    assert host["host_zfs_arc_size"].src == "unavail_kernel"
    meta = collect_host_meta(proc_root=proc, sys_root=sys)
    frame = _minimal_frame(host=host, host_meta=meta)
    snapshot = render_banner(frame, ToposConfig())
    lines = "\n".join(snapshot.lines)
    assert "ARC" not in lines


# --- Oracle 6: Golden frames unaffected ----------------------------------


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


# --- Helpers -------------------------------------------------------------


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
