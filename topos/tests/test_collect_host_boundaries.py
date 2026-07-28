from __future__ import annotations

from pathlib import Path

from topos.collect.host import (
    SWAP_BACKEND_CODES,
    _gpu_detail,
    _gpu_metrics,
    _net_dev_counters,
    _parse_arcstats,
    _swap_backend_metrics,
    _swap_devices,
    _zfs_arc_detail,
    _zfs_arc_metrics,
    _zswap_param,
)
from topos.model import MetricValue


def test_malformed_swap_values_are_ignored_without_claiming_disk_swap(tmp_path: Path) -> None:
    """Malformed kernel swap values must not become a fabricated disk-swap reading."""
    sys = tmp_path / "sys"
    params = sys / "module" / "zswap" / "parameters"
    params.mkdir(parents=True)
    (params / "max_pool_percent").write_text("not-a-number\n")
    proc = tmp_path / "proc"
    proc.mkdir()
    (proc / "swaps").write_text(
        "Filename Type Size Used Priority\n"
        "/swapfile file not-a-size not-a-used -2\n"
    )

    assert _zswap_param(sys, "max_pool_percent") == MetricValue(None, "unavail_kernel")
    assert _swap_devices(proc) == ([], "host")

    backend = _swap_backend_metrics(proc, MetricValue(0, "host"), MetricValue(1, "host"))
    assert backend["host_swap_backend"] == MetricValue(SWAP_BACKEND_CODES["zswap_only"], "host")
    assert backend["host_zram_swap_devices"] == MetricValue(0, "host")
    assert backend["host_disk_swap_devices"] == MetricValue(0, "host")


def test_partial_and_non_metric_zfs_rows_remain_unavailable(tmp_path: Path) -> None:
    """A partial kstat has gauges but no valid hit ratio, while non-metric rows are rejected."""
    proc = tmp_path / "proc"
    arcstats = proc / "spl" / "kstat" / "zfs" / "arcstats"
    arcstats.parent.mkdir(parents=True)
    arcstats.write_text("size 4 12\nc 4 20\nc_max 4 30\nc_min 4 5\n")

    metrics = _zfs_arc_metrics(proc)
    assert metrics["host_zfs_arc_size"] == MetricValue(12, "host")
    assert metrics["host_zfs_arc_hit_ratio"] == MetricValue(None, "unavail_kernel", None)

    arcstats.write_text("\nheader 0 non-metric\n")
    assert _parse_arcstats(arcstats.read_text()) is None
    assert _zfs_arc_detail(proc) is None


def test_card_node_without_device_and_empty_gpu_detail_are_not_measurements(tmp_path: Path) -> None:
    """A DRM card entry without a device directory counts as present but exposes no facts."""
    sys = tmp_path / "sys"
    (sys / "class" / "drm" / "card0").mkdir(parents=True)

    metrics = _gpu_metrics(sys)
    assert metrics["host_gpu_count"] == MetricValue(1, "host")
    for name in ("host_gpu_vram_total", "host_gpu_vram_used", "host_gpu_busy_pct"):
        assert metrics[name] == MetricValue(None, "unavail_kernel")
    assert _gpu_detail(sys) is None


def test_truncated_proc_net_row_is_dropped(tmp_path: Path) -> None:
    """A row with an interface name but fewer than 16 kernel counters is unusable."""
    proc = tmp_path / "proc"
    net = proc / "net"
    net.mkdir(parents=True)
    (net / "dev").write_text(
        "Inter-| Receive | Transmit\n"
        " face |bytes packets errs drop|bytes packets errs drop\n"
        " eth0: 10 2 0 0 20 3\n"
    )

    assert _net_dev_counters(proc) == []
