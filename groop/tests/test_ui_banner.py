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
    top_index = snapshot.lines.index("TOP PRESSURE")
    assert snapshot.lines[top_index + 1].startswith("1 /")


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


def _make_base_frame() -> Frame:
    """Helper: frame with required host metrics but no entities or host_meta."""
    return Frame(
        1, 1.0, 5.0,
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
            "host_swap_backend": MetricValue(3, "host"),
            "host_zram_orig_bytes": MetricValue(0, "host"),
            "host_zram_mem_used_bytes": MetricValue(0, "host"),
            "host_zram_ratio": MetricValue(None, "host"),
            "host_zram_swap_devices": MetricValue(0, "host"),
            "host_disk_swap": MetricValue(0, "host"),
            "host_disk_swap_devices": MetricValue(0, "host"),
        },
        {},
    )


def test_banner_renders_net_and_disk_lines() -> None:
    """NET and DISK lines appear when rates are present in host_meta."""
    frame = _make_base_frame()
    frame.host_meta = {
        "net_devices": [
            {"name": "eth0", "rx_bps": 1000000.0, "tx_bps": 500000.0, "rx_pps": 1000.0, "tx_pps": 500.0, "src": "host"},
        ],
        "block_devices": [
            {"name": "nvme0n1", "read_bps": 50000000.0, "write_bps": 20000000.0, "read_iops": 5000.0, "write_iops": 2000.0, "src": "host"},
        ],
    }
    snapshot = render_banner(frame, GroopConfig())
    lines = "\n".join(snapshot.lines)
    assert "NET" in lines
    assert "eth0" in lines
    assert "DISK" in lines
    assert "nvme0n1" in lines
    # Verify byte formatting
    assert "976.6KiB/s" in lines  # 1000000 B/s approximately 976.6 KiB/s
    assert "47.7MiB/s" in lines  # 50000000 B/s approximately 47.7 MiB/s


def test_banner_renders_collecting_line_on_first_sample() -> None:
    """First sample with None rates shows 'collecting...'."""
    frame = _make_base_frame()
    frame.host_meta = {
        "net_devices": [
            {"name": "eth0", "rx_bps": None, "tx_bps": None, "rx_pps": None, "tx_pps": None, "src": "host"},
        ],
        "block_devices": [
            {"name": "nvme0n1", "read_bps": None, "write_bps": None, "read_iops": None, "write_iops": None, "src": "host"},
        ],
    }
    snapshot = render_banner(frame, GroopConfig())
    lines = "\n".join(snapshot.lines)
    assert "NET collecting..." in lines
    assert "DISK collecting..." in lines


def test_banner_renders_n_a_when_no_host_meta() -> None:
    """Absent host_meta does not crash and NET/DISK lines are absent."""
    frame = _make_base_frame()
    snapshot = render_banner(frame, GroopConfig())
    lines = "\n".join(snapshot.lines)
    assert "NET" not in lines
    assert "DISK" not in lines


def test_banner_ignores_malformed_host_meta_device_entries() -> None:
    frame = _make_base_frame()
    frame.host_meta = {
        "net_devices": [
            "bad-entry",
            {"rx_bps": 1.0, "tx_bps": 2.0},
            {"name": "eth0", "rx_bps": 1.0, "tx_bps": 2.0, "rx_pps": None, "tx_pps": None},
        ],
        "block_devices": [
            None,
            {"read_bps": 1.0, "write_bps": 2.0},
            {"name": "nvme0n1", "read_bps": 1.0, "write_bps": 2.0, "read_iops": None, "write_iops": None},
        ],
    }

    snapshot = render_banner(frame, GroopConfig())
    lines = "\n".join(snapshot.lines)

    assert "eth0" in lines
    assert "nvme0n1" in lines


def test_banner_shows_busiest_two_devices() -> None:
    """Only the busiest 2-3 devices are shown in the banner."""
    frame = _make_base_frame()
    frame.host_meta = {
        "net_devices": [
            {"name": "eth0", "rx_bps": 1000000.0, "tx_bps": 500000.0, "rx_pps": 1000.0, "tx_pps": 500.0, "src": "host"},
            {"name": "eth1", "rx_bps": 100.0, "tx_bps": 50.0, "rx_pps": 1.0, "tx_pps": 0.5, "src": "host"},
            {"name": "eth2", "rx_bps": 10.0, "tx_bps": 5.0, "rx_pps": 0.1, "tx_pps": 0.05, "src": "host"},
            {"name": "eth3", "rx_bps": 1.0, "tx_bps": 0.5, "rx_pps": 0.01, "tx_pps": 0.01, "src": "host"},
        ],
    }
    snapshot = render_banner(frame, GroopConfig())
    # eth3 (least busy) should not appear
    assert "eth3" not in "\n".join(snapshot.lines)
    assert "eth0" in snapshot.lines[4]
    assert "eth1" in snapshot.lines[4] or "eth2" in snapshot.lines[4]


def test_frame_round_trip_preserves_net_and_block_devices() -> None:
    """Frame JSON round-trip preserves host_meta device lists."""
    from groop.model import frame_from_jsonable, frame_to_jsonable

    frame = _make_base_frame()
    frame.host_meta = {
        "net_devices": [
            {"name": "eth0", "rx_bps": 1000.0, "tx_bps": 500.0, "rx_pps": 10.0, "tx_pps": 5.0, "src": "host"},
            {"name": "eth1", "rx_bps": 2000.0, "tx_bps": 1000.0, "rx_pps": 20.0, "tx_pps": 10.0, "src": "host"},
        ],
        "block_devices": [
            {"name": "nvme0n1", "read_bps": 50000000.0, "write_bps": 20000000.0, "read_iops": 5000.0, "write_iops": 2000.0, "src": "host"},
        ],
    }
    restored = frame_from_jsonable(frame_to_jsonable(frame))
    assert restored.host_meta is not None
    assert restored.host_meta["net_devices"] == frame.host_meta["net_devices"]
    assert restored.host_meta["block_devices"] == frame.host_meta["block_devices"]
