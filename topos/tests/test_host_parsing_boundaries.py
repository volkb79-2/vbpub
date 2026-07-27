from __future__ import annotations

from pathlib import Path

from topos.collect.host import (
    SWAP_BACKEND_CODES,
    _block_dev_counters,
    _loadavg,
    _meminfo,
    _net_dev_counters,
    _parse_stat_line,
    _psi,
    _swap_backend_metrics,
    _uptime,
    _zram_device_details,
    _zram_metrics,
    collect_host,
)
from topos.model import MetricValue


def test_corrupt_proc_scalars_are_unavailable_without_hiding_valid_meminfo(tmp_path: Path) -> None:
    """Malformed scalar proc values must be unavailable while valid neighbours survive."""
    proc = tmp_path / "proc"
    proc.mkdir()
    (proc / "meminfo").write_text("MemTotal: broken kB\nMemAvailable: 64 kB\nNoValue:\n")
    (proc / "loadavg").write_text("one two\n")
    (proc / "uptime").write_text("not-a-duration\n")
    (proc / "pressure").mkdir()
    (proc / "pressure" / "memory").write_text("some avg10=not-a-number\n")

    assert _meminfo(proc) == {"MemAvailable": 64 * 1024}
    assert [metric.src for metric in _loadavg(proc)] == ["unavail_kernel"] * 3
    assert _uptime(proc).src == "unavail_kernel"
    assert _psi(proc, "memory", "some").src == "unavail_kernel"

    host = collect_host(proc_root=proc, sys_root=tmp_path / "sys")
    assert host["host_mem_total"] == MetricValue(None, "unavail_kernel")
    assert host["host_mem_available"] == MetricValue(64 * 1024, "host")
    assert host["host_load1"].src == "unavail_kernel"
    assert host["host_uptime_s"].src == "unavail_kernel"


def test_unreadable_swaps_preserves_source_in_disk_and_backend_metrics(tmp_path: Path) -> None:
    """Absent /proc/swaps is an unavailable observation, not a claim of no swap."""
    proc = tmp_path / "proc"
    proc.mkdir()

    backend = _swap_backend_metrics(proc, MetricValue(0, "host"), MetricValue(0, "host"))
    host = collect_host(proc_root=proc, sys_root=tmp_path / "sys")

    assert backend["host_swap_backend"] == MetricValue(SWAP_BACKEND_CODES["unknown"], "unavail_kernel")
    assert backend["host_zram_swap_devices"] == MetricValue(None, "unavail_kernel")
    assert backend["host_disk_swap_devices"] == MetricValue(None, "unavail_kernel")
    assert host["host_disk_swap"] == MetricValue(None, "unavail_kernel")
    assert host["host_swap_backend"] == MetricValue(SWAP_BACKEND_CODES["unknown"], "unavail_kernel")


def test_corrupt_device_counter_rows_are_dropped_and_non_directories_are_ignored(tmp_path: Path) -> None:
    """A bad kernel row must not poison valid counters or become a phantom device."""
    proc = tmp_path / "proc"
    (proc / "net").mkdir(parents=True)
    (proc / "net" / "dev").write_text(
        "Inter-| Receive | Transmit\n"
        " face |bytes packets errs drop fifo frame compressed multicast|bytes packets errs drop fifo colls carrier compressed\n"
        " eth0: 10 2 0 0 0 0 0 0 20 3 0 0 0 0 0 0\n"
        " bad0: nope 2 0 0 0 0 0 0 20 3 0 0 0 0 0 0\n"
    )
    sys = tmp_path / "sys"
    (sys / "block" / "sda").mkdir(parents=True)
    (sys / "block" / "sda" / "stat").write_text("1 0 2 0 3 0 4\n")
    (sys / "block" / "zram-note").write_text("not a directory")

    assert [device["name"] for device in _net_dev_counters(proc)] == ["eth0"]
    assert [device["name"] for device in _block_dev_counters(sys)] == ["sda"]
    assert _parse_stat_line(sys / "block" / "missing" / "stat") == ()
    assert _zram_metrics(sys)["host_zram_device_count"] == MetricValue(0, "host")
    assert _zram_device_details(sys) == []
