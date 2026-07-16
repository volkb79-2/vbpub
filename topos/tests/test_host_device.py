from __future__ import annotations

from pathlib import Path

import pytest

from topos.collect.host import _block_dev_counters, _net_dev_counters, collect_host_meta


def test_net_dev_counters_parses_fixture(tmp_path: Path) -> None:
    """Parse a realistic /proc/net/dev fixture and verify counter values."""
    proc = tmp_path / "proc"
    net = proc / "net"
    net.mkdir(parents=True)
    (net / "dev").write_text(
        "Inter-|   Receive                                                |  Transmit\n"
        " face |bytes    packets errs drop fifo frame compressed multicast|bytes    packets errs drop fifo colls carrier compressed\n"
        "    lo:       0       0    0    0    0     0          0         0        0       0    0    0    0     0       0          0\n"
        "  eth0: 12345678    9876    0    0    0     0          0         0  9876543   12345    0    0    0     0       0          0\n"
        " veth0: 1000000     500    0    0    0     0          0         0   500000     600    0    0    0     0       0          0\n"
        "  br-1:  500000     200    0    0    0     0          0         0   300000     400    0    0    0     0       0          0\n"
    )

    devices = _net_dev_counters(proc)

    # Only eth0 should be included (veth0, br-1, lo excluded)
    assert len(devices) == 1
    assert devices[0]["name"] == "eth0"
    assert devices[0]["rx_bytes"] == 12345678
    assert devices[0]["tx_bytes"] == 9876543
    assert devices[0]["rx_packets"] == 9876
    assert devices[0]["tx_packets"] == 12345
    assert devices[0]["rx_errors"] == 0
    assert devices[0]["rx_drop"] == 0
    assert devices[0]["tx_errors"] == 0
    assert devices[0]["tx_drop"] == 0
    assert devices[0]["src"] == "host"


def test_net_dev_counters_empty_on_unreadable(tmp_path: Path) -> None:
    """Unreadable /proc/net/dev returns empty list."""
    proc = tmp_path / "proc"
    proc.mkdir()
    devices = _net_dev_counters(proc)
    assert devices == []


def test_net_dev_counters_skips_excluded_interfaces(tmp_path: Path) -> None:
    """Interfaces matching exclusion prefixes (veth*, br-*, docker*, lo) are skipped."""
    proc = tmp_path / "proc"
    net = proc / "net"
    net.mkdir(parents=True)
    (net / "dev").write_text(
        "Inter-|   Receive                                                |  Transmit\n"
        " face |bytes    packets errs drop fifo frame compressed multicast|bytes    packets errs drop fifo colls carrier compressed\n"
        "  lo: 100 2 0 0 0 0 0 0 100 2 0 0 0 0 0 0\n"
        " veth0: 200 3 0 0 0 0 0 0 200 3 0 0 0 0 0 0\n"
        " docker0: 300 4 0 0 0 0 0 0 300 4 0 0 0 0 0 0\n"
        " br-int: 400 5 0 0 0 0 0 0 400 5 0 0 0 0 0 0\n"
    )

    devices = _net_dev_counters(proc)
    # All interfaces match exclusion prefixes (lo, veth0, docker0, br-int)
    assert len(devices) == 0


def test_net_dev_counters_shows_non_excluded_interface(tmp_path: Path) -> None:
    """Non-excluded interfaces like eth0 are included."""
    proc = tmp_path / "proc"
    net = proc / "net"
    net.mkdir(parents=True)
    (net / "dev").write_text(
        "Inter-|   Receive                                                |  Transmit\n"
        " face |bytes    packets errs drop fifo frame compressed multicast|bytes    packets errs drop fifo colls carrier compressed\n"
        "  eth0: 100 2 0 0 0 0 0 0 200 3 0 0 0 0 0 0\n"
        " enp0s3: 50 1 0 0 0 0 0 0 100 2 0 0 0 0 0 0\n"
    )

    devices = _net_dev_counters(proc)
    assert len(devices) == 2
    names = {d["name"] for d in devices}
    assert "eth0" in names
    assert "enp0s3" in names
    assert devices[0]["rx_errors"] == 0
    assert devices[0]["rx_drop"] == 0
    assert devices[0]["tx_errors"] == 0
    assert devices[0]["tx_drop"] == 0


def test_block_dev_counters_parses_fixture(tmp_path: Path) -> None:
    """Parse /sys/block/*/stat fixture and verify counter values."""
    sys = tmp_path / "sys"
    block = sys / "block"
    nvme = block / "nvme0n1"
    sda = block / "sda"
    nvme.mkdir(parents=True)
    sda.mkdir(parents=True)
    # stat format: rd_ios rd_merge rd_sectors rd_ticks wr_ios wr_merge wr_sectors wr_ticks ...
    (nvme / "stat").write_text("  100  50  8000  120  200  75  16000  240  0  500  600\n")
    (sda / "stat").write_text("   50  25  4000   60  100  40   8000  120  0  250  300\n")

    devices = _block_dev_counters(sys)

    assert len(devices) == 2
    by_name = {d["name"]: d for d in devices}
    assert "nvme0n1" in by_name
    assert "sda" in by_name
    assert by_name["nvme0n1"]["rd_ios"] == 100
    assert by_name["nvme0n1"]["rd_sectors"] == 8000
    assert by_name["nvme0n1"]["wr_ios"] == 200
    assert by_name["nvme0n1"]["wr_sectors"] == 16000
    assert by_name["sda"]["rd_ios"] == 50
    assert by_name["sda"]["rd_sectors"] == 4000


def test_block_dev_counters_excludes_loop_ram_zram(tmp_path: Path) -> None:
    """loop*, ram*, zram* devices are excluded."""
    sys = tmp_path / "sys"
    block = sys / "block"
    for name in ("loop0", "ram0", "zram0", "nvme0n1"):
        (block / name).mkdir(parents=True)
    for name in ("loop0", "ram0", "zram0", "nvme0n1"):
        (block / name / "stat").write_text("0 0 0 0 0 0 0 0 0 0 0\n")

    devices = _block_dev_counters(sys)
    assert len(devices) == 1
    assert devices[0]["name"] == "nvme0n1"


def test_block_dev_counters_empty_on_no_block_dir(tmp_path: Path) -> None:
    """Absent /sys/block returns empty list."""
    sys = tmp_path / "sys"
    devices = _block_dev_counters(sys)
    assert devices == []


def test_net_dev_counters_malformed_line_skipped(tmp_path: Path) -> None:
    """Lines that can't be parsed are skipped without crashing."""
    proc = tmp_path / "proc"
    net = proc / "net"
    net.mkdir(parents=True)
    (net / "dev").write_text(
        "Inter-|   Receive                                                |  Transmit\n"
        " face |bytes    packets errs drop fifo frame compressed multicast|bytes    packets errs drop fifo colls carrier compressed\n"
        "  eth0: 12345 100 0 0 0 0 0 0 67890 200 0 0 0 0 0 0\n"
        "  badline not enough colons\n"
    )

    devices = _net_dev_counters(proc)
    assert len(devices) == 1
    assert devices[0]["name"] == "eth0"


def test_block_dev_counters_short_stat_skipped(tmp_path: Path) -> None:
    """Block devices with too few stat fields are skipped."""
    sys = tmp_path / "sys"
    block = sys / "block"
    (block / "nvme0n1").mkdir(parents=True)
    (block / "nvme0n1" / "stat").write_text("0 0 0\n")  # too short
    (block / "sda").mkdir(parents=True)
    (block / "sda" / "stat").write_text("10 5 100 15 20 10 200 30 0 0 0\n")

    devices = _block_dev_counters(sys)
    assert len(devices) == 1
    assert devices[0]["name"] == "sda"


def test_collect_host_meta_includes_device_counters(tmp_path: Path) -> None:
    """collect_host_meta includes net_device_counters and block_device_counters."""
    # Set up proc net
    proc = tmp_path / "proc"
    net = proc / "net"
    net.mkdir(parents=True)
    (net / "dev").write_text(
        "Inter-|   Receive                                                |  Transmit\n"
        " face |bytes    packets errs drop fifo frame compressed multicast|bytes    packets errs drop fifo colls carrier compressed\n"
        "  eth0: 100 10 0 0 0 0 0 0 200 20 0 0 0 0 0 0\n"
    )
    # Set up sys block
    sys = tmp_path / "sys"
    block = sys / "block"
    (block / "nvme0n1").mkdir(parents=True)
    (block / "nvme0n1" / "stat").write_text("10 5 100 15 20 10 200 30 0 0 0\n")

    meta = collect_host_meta(proc_root=proc, sys_root=sys)

    assert "net_device_counters" in meta
    assert "block_device_counters" in meta
    assert "zram_devices" in meta
    assert meta["net_device_counters"][0]["name"] == "eth0"
    assert meta["block_device_counters"][0]["name"] == "nvme0n1"


def test_apply_host_device_rates_first_sample_none() -> None:
    """First call to _apply_host_device_rates sets all rates to None (collecting)."""
    from topos.collect.collector import Collector

    c = Collector()
    c._prev_ts = None
    c._prev_device_counters = None

    host_meta: dict[str, object] = {
        "net_device_counters": [
            {"name": "eth0", "rx_bytes": 1000, "tx_bytes": 500, "rx_packets": 10, "tx_packets": 5, "rx_errors": 0, "rx_drop": 0, "tx_errors": 0, "tx_drop": 0, "src": "host"},
        ],
        "block_device_counters": [
            {"name": "nvme0n1", "rd_ios": 100, "rd_sectors": 800, "wr_ios": 50, "wr_sectors": 400, "src": "host"},
        ],
    }

    c._apply_host_device_rates(host_meta, 5.0)

    assert "net_device_counters" not in host_meta
    assert "block_device_counters" not in host_meta
    net = host_meta["net_devices"]
    assert net[0]["name"] == "eth0"
    assert net[0]["rx_bps"] is None
    assert net[0]["tx_bps"] is None
    assert net[0]["rx_pps"] is None
    assert net[0]["tx_pps"] is None
    assert net[0]["src"] == "host"
    block = host_meta["block_devices"]
    assert block[0]["name"] == "nvme0n1"
    assert block[0]["read_bps"] is None
    assert block[0]["write_iops"] is None


def test_apply_host_device_rates_second_sample_computes_rates() -> None:
    """Second call computes rates from deltas."""
    from topos.collect.collector import Collector

    c = Collector()
    c._prev_device_counters = {
        "net_device_counters": [
            {"name": "eth0", "rx_bytes": 1000, "tx_bytes": 500, "rx_packets": 10, "tx_packets": 5, "rx_errors": 0, "rx_drop": 0, "tx_errors": 0, "tx_drop": 0, "src": "host"},
        ],
        "block_device_counters": [
            {"name": "nvme0n1", "rd_ios": 100, "rd_sectors": 800, "wr_ios": 50, "wr_sectors": 400, "src": "host"},
        ],
    }

    host_meta: dict[str, object] = {
        "net_device_counters": [
            {"name": "eth0", "rx_bytes": 2000, "tx_bytes": 1000, "rx_packets": 20, "tx_packets": 10, "rx_errors": 0, "rx_drop": 0, "tx_errors": 0, "tx_drop": 0, "src": "host"},
        ],
        "block_device_counters": [
            {"name": "nvme0n1", "rd_ios": 150, "rd_sectors": 1600, "wr_ios": 75, "wr_sectors": 800, "src": "host"},
        ],
    }

    c._apply_host_device_rates(host_meta, 5.0)

    net = host_meta["net_devices"]
    assert net[0]["rx_bps"] == 200.0
    assert net[0]["tx_bps"] == 100.0
    assert net[0]["rx_pps"] == 2.0
    assert net[0]["tx_pps"] == 1.0

    block = host_meta["block_devices"]
    assert block[0]["read_bps"] == 81920.0
    assert block[0]["write_bps"] == 40960.0
    assert block[0]["read_iops"] == 10.0
    assert block[0]["write_iops"] == 5.0


def test_apply_host_device_rates_counter_reset_handled() -> None:
    """Counter regression (reset) produces zero rate (not negative)."""
    from topos.collect.collector import Collector

    c = Collector()
    c._prev_device_counters = {
        "net_device_counters": [
            {"name": "eth0", "rx_bytes": 5000, "tx_bytes": 2000, "rx_packets": 100, "tx_packets": 50, "rx_errors": 0, "rx_drop": 0, "tx_errors": 0, "tx_drop": 0, "src": "host"},
        ],
        "block_device_counters": [
            {"name": "nvme0n1", "rd_ios": 500, "rd_sectors": 4000, "wr_ios": 200, "wr_sectors": 2000, "src": "host"},
        ],
    }

    host_meta: dict[str, object] = {
        "net_device_counters": [
            {"name": "eth0", "rx_bytes": 100, "tx_bytes": 50, "rx_packets": 5, "tx_packets": 2, "rx_errors": 0, "rx_drop": 0, "tx_errors": 0, "tx_drop": 0, "src": "host"},
        ],
        "block_device_counters": [
            {"name": "nvme0n1", "rd_ios": 10, "rd_sectors": 100, "wr_ios": 5, "wr_sectors": 50, "src": "host"},
        ],
    }

    c._apply_host_device_rates(host_meta, 5.0)

    net = host_meta["net_devices"]
    assert net[0]["rx_bps"] == 0.0
    assert net[0]["tx_bps"] == 0.0
    assert net[0]["rx_pps"] == 0.0
    assert net[0]["tx_pps"] == 0.0


def test_apply_host_device_rates_new_device_none() -> None:
    """When a device is new this frame (no previous counters), rates are None."""
    from topos.collect.collector import Collector

    c = Collector()
    c._prev_device_counters = {
        "net_device_counters": [
            {"name": "eth0", "rx_bytes": 1000, "tx_bytes": 500, "rx_packets": 10, "tx_packets": 5, "rx_errors": 0, "rx_drop": 0, "tx_errors": 0, "tx_drop": 0, "src": "host"},
        ],
        "block_device_counters": [
            {"name": "nvme0n1", "rd_ios": 100, "rd_sectors": 800, "wr_ios": 50, "wr_sectors": 400, "src": "host"},
        ],
    }

    host_meta: dict[str, object] = {
        "net_device_counters": [
            {"name": "eth1", "rx_bytes": 100, "tx_bytes": 50, "rx_packets": 5, "tx_packets": 2, "rx_errors": 0, "rx_drop": 0, "tx_errors": 0, "tx_drop": 0, "src": "host"},
        ],
        "block_device_counters": [
            {"name": "sda", "rd_ios": 10, "rd_sectors": 100, "wr_ios": 5, "wr_sectors": 50, "src": "host"},
        ],
    }

    c._apply_host_device_rates(host_meta, 5.0)

    net = host_meta["net_devices"]
    assert net[0]["name"] == "eth1"
    assert net[0]["rx_bps"] is None
    assert net[0]["tx_bps"] is None
    assert net[0]["rx_errors_s"] is None
    assert net[0]["rx_drops_s"] is None
    assert net[0]["tx_errors_s"] is None
    assert net[0]["tx_drops_s"] is None


def test_net_dev_counters_includes_drops_and_errors(tmp_path: Path) -> None:
    """Non-zero rx_drop, rx_errors, tx_drop, tx_errors are parsed."""
    proc = tmp_path / "proc"
    net = proc / "net"
    net.mkdir(parents=True)
    (net / "dev").write_text(
        "Inter-|   Receive                                                |  Transmit\n"
        " face |bytes    packets errs drop fifo frame compressed multicast|bytes    packets errs drop fifo colls carrier compressed\n"
        "  eth0: 1000000   10000    5   12    0     0          0         0  2000000   20000    3    8    0     0       0          0\n"
    )

    devices = _net_dev_counters(proc)

    assert len(devices) == 1
    assert devices[0]["name"] == "eth0"
    assert devices[0]["rx_drop"] == 12
    assert devices[0]["rx_errors"] == 5
    assert devices[0]["tx_drop"] == 8
    assert devices[0]["tx_errors"] == 3
    assert devices[0]["rx_bytes"] == 1000000
    assert devices[0]["tx_bytes"] == 2000000


def test_apply_host_device_rates_computes_drop_error_rates() -> None:
    """Second sample computes drop and error rates correctly."""
    from topos.collect.collector import Collector

    c = Collector()
    c._prev_device_counters = {
        "net_device_counters": [
            {"name": "eth0", "rx_bytes": 1000, "tx_bytes": 500, "rx_packets": 10, "tx_packets": 5,
             "rx_errors": 5, "rx_drop": 12, "tx_errors": 3, "tx_drop": 8, "src": "host"},
        ],
    }

    host_meta: dict[str, object] = {
        "net_device_counters": [
            {"name": "eth0", "rx_bytes": 2000, "tx_bytes": 1000, "rx_packets": 20, "tx_packets": 10,
             "rx_errors": 15, "rx_drop": 24, "tx_errors": 6, "tx_drop": 12, "src": "host"},
        ],
    }

    c._apply_host_device_rates(host_meta, 5.0)

    net = host_meta["net_devices"]
    assert net[0]["rx_errors_s"] == 2.0    # (15-5)/5
    assert net[0]["rx_drops_s"] == 2.4     # (24-12)/5
    assert net[0]["tx_errors_s"] == 0.6    # (6-3)/5
    assert net[0]["tx_drops_s"] == 0.8     # (12-8)/5
    assert net[0]["rx_bps"] == 200.0
    assert net[0]["tx_bps"] == 100.0


def test_apply_host_device_rates_drop_error_reset_handled() -> None:
    """Counter regression for drops/errors produces zero rate (not negative)."""
    from topos.collect.collector import Collector

    c = Collector()
    c._prev_device_counters = {
        "net_device_counters": [
            {"name": "eth0", "rx_bytes": 5000, "tx_bytes": 2000, "rx_packets": 100, "tx_packets": 50,
             "rx_errors": 50, "rx_drop": 100, "tx_errors": 30, "tx_drop": 80, "src": "host"},
        ],
    }

    host_meta: dict[str, object] = {
        "net_device_counters": [
            {"name": "eth0", "rx_bytes": 100, "tx_bytes": 50, "rx_packets": 5, "tx_packets": 2,
             "rx_errors": 5, "rx_drop": 10, "tx_errors": 3, "tx_drop": 8, "src": "host"},
        ],
    }

    c._apply_host_device_rates(host_meta, 5.0)

    net = host_meta["net_devices"]
    assert net[0]["rx_errors_s"] == 0.0
    assert net[0]["rx_drops_s"] == 0.0
    assert net[0]["tx_errors_s"] == 0.0
    assert net[0]["tx_drops_s"] == 0.0
    assert net[0]["rx_bps"] == 0.0
    assert net[0]["tx_bps"] == 0.0
