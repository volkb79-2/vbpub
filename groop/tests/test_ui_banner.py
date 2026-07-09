from __future__ import annotations

from conftest import fixture_frame
from groop.config import GroopConfig
from groop.model import Entity, EntityFrame, Frame, MetricValue
from groop.ui.banner import render_banner


def test_banner_snapshot_renders_golden_fixture_summary() -> None:
    snapshot = render_banner(fixture_frame(), GroopConfig())
    assert snapshot.verdict == "OK"
    assert snapshot.lines[0] == "HOST OK"
    assert snapshot.lines[1].startswith("LOAD 0.10/0.20/0.30 | MEM 7.8KiB avail / 15.6KiB total")
    assert snapshot.lines[4] == "TOP PRESSURE"
    assert snapshot.lines[5].startswith("1 /")


def test_banner_counts_unavailable_permissions_and_shows_notice() -> None:
    frame = Frame(
        1,
        1.0,
        5.0,
        {
            "host_load1": MetricValue(0.1, "host"),
            "host_load5": MetricValue(0.2, "host"),
            "host_load15": MetricValue(0.3, "host"),
            "host_mem_available": MetricValue(None, "unavail_perm"),
            "host_mem_total": MetricValue(1024, "host"),
            "host_swap_free": MetricValue(1024, "host"),
            "host_swap_total": MetricValue(2048, "host"),
            "host_psi_mem_full_avg10": MetricValue(0.0, "host"),
            "host_psi_mem_some_avg10": MetricValue(0.0, "host"),
            "host_psi_io_full_avg10": MetricValue(0.0, "host"),
            "host_psi_io_some_avg10": MetricValue(0.0, "host"),
            "host_psi_cpu_some_avg10": MetricValue(0.0, "host"),
            "host_zswap_pool": MetricValue(0, "host"),
            "host_zswap_stored": MetricValue(0, "host"),
            "host_zswap_ratio": MetricValue(None, "unavail_perm"),
            "host_disk_swap": MetricValue(0, "host"),
        },
        {
            "x.slice": EntityFrame(
                entity=Entity("x.slice", "slice", ""),
                metrics={"ram": MetricValue(None, "unavail_perm"), "pressure": MetricValue(None, "unavail_kernel")},
            )
        },
    )
    snapshot = render_banner(frame, GroopConfig())
    assert snapshot.unprivileged_count == 3
    assert "running unprivileged - 3 fields unavailable" in snapshot.lines[0]
    assert "[dim]-[/]" in snapshot.lines[1]


def test_banner_renders_swap_backend_line() -> None:
    frame = Frame(
        1,
        1.0,
        5.0,
        {
            "host_load1": MetricValue(0.1, "host"),
            "host_load5": MetricValue(0.2, "host"),
            "host_load15": MetricValue(0.3, "host"),
            "host_mem_available": MetricValue(1024, "host"),
            "host_mem_total": MetricValue(2048, "host"),
            "host_swap_free": MetricValue(1024, "host"),
            "host_swap_total": MetricValue(2048, "host"),
            "host_psi_mem_full_avg10": MetricValue(0.0, "host"),
            "host_psi_mem_some_avg10": MetricValue(0.0, "host"),
            "host_psi_io_full_avg10": MetricValue(0.0, "host"),
            "host_psi_io_some_avg10": MetricValue(0.0, "host"),
            "host_psi_cpu_some_avg10": MetricValue(0.0, "host"),
            "host_zswap_pool": MetricValue(0, "host"),
            "host_zswap_stored": MetricValue(0, "host"),
            "host_zswap_ratio": MetricValue(None, "host"),
            "host_swap_backend": MetricValue(2, "host"),
            "host_zram_orig_bytes": MetricValue(8192, "host"),
            "host_zram_mem_used_bytes": MetricValue(4096, "host"),
            "host_zram_ratio": MetricValue(2.0, "host"),
            "host_zram_swap_devices": MetricValue(1, "host"),
            "host_disk_swap": MetricValue(0, "host"),
            "host_disk_swap_devices": MetricValue(0, "host"),
        },
        {},
    )

    snapshot = render_banner(frame, GroopConfig())

    assert snapshot.lines[3].startswith("SWAP backend zram")
    assert "disk 0B devs 0" in snapshot.lines[3]
