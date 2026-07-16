from __future__ import annotations

from pathlib import Path

import pytest

from topos.collect.host import SWAP_BACKEND_CODES, collect_host


def _base_proc(root: Path, swaps: str) -> Path:
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


def _base_sys(root: Path, *, zswap: bool = False) -> Path:
    sys = root / "sys"
    params = sys / "module" / "zswap" / "parameters"
    params.mkdir(parents=True)
    (params / "enabled").write_text(("Y" if zswap else "N") + "\n")
    (params / "max_pool_percent").write_text("20\n")
    zdebug = sys / "kernel" / "debug" / "zswap"
    zdebug.mkdir(parents=True)
    (zdebug / "pool_total_size").write_text("0\n")
    (zdebug / "stored_pages").write_text("0\n")
    (sys / "block").mkdir(parents=True)
    return sys


def _zram_device(sys: Path, name: str, *, mm_stat: str, io_stat: str = "0 0 0 0\n", bd_stat: str = "0 0 0\n") -> None:
    dev = sys / "block" / name
    dev.mkdir()
    (dev / "mm_stat").write_text(mm_stat)
    (dev / "io_stat").write_text(io_stat)
    (dev / "bd_stat").write_text(bd_stat)


def test_collect_host_reports_zram_only_backend_and_metrics(tmp_path: Path) -> None:
    proc = _base_proc(
        tmp_path,
        "Filename\t\t\t\tType\t\tSize\t\tUsed\t\tPriority\n/dev/zram0 partition 4096 2048 100\n",
    )
    sys = _base_sys(tmp_path)
    _zram_device(
        sys,
        "zram0",
        mm_stat="8192 4096 6144 1048576 8192 2 0 1 3\n",
        io_stat="1 2 0 10\n",
        bd_stat="5 6 7\n",
    )

    host = collect_host(proc_root=proc, sys_root=sys)

    assert host["host_swap_backend"].v == SWAP_BACKEND_CODES["zram_only"]
    assert host["host_zram_swap_devices"].v == 1
    assert host["host_disk_swap_devices"].v == 0
    assert host["host_disk_swap"].v == 0
    assert host["host_zram_orig_bytes"].v == 8192
    assert host["host_zram_compr_bytes"].v == 4096
    assert host["host_zram_mem_used_bytes"].v == 6144
    assert host["host_zram_ratio"].v == pytest.approx(2.0)
    assert host["host_zram_efficiency"].v == pytest.approx(4096 / 6144)
    assert host["host_zram_failed_reads"].v == 1
    assert host["host_zram_failed_writes"].v == 2
    assert host["host_zram_writeback_bytes"].v == 5 * 4096


def test_collect_host_reports_mixed_backend_without_per_cgroup_claims(tmp_path: Path) -> None:
    proc = _base_proc(
        tmp_path,
        "\n".join(
            (
                "Filename\t\t\t\tType\t\tSize\t\tUsed\t\tPriority",
                "/dev/zram0 partition 4096 1024 100",
                "/swapfile file 8192 4096 -2",
            )
        )
        + "\n",
    )
    sys = _base_sys(tmp_path, zswap=True)
    _zram_device(sys, "zram0", mm_stat="4096 2048 3072 0 0 0 0 0 0\n")

    host = collect_host(proc_root=proc, sys_root=sys)

    assert host["host_swap_backend"].v == SWAP_BACKEND_CODES["mixed"]
    assert host["host_zram_swap_devices"].v == 1
    assert host["host_disk_swap_devices"].v == 1
    assert host["host_disk_swap"].v == 4096 * 1024 - 128 * 1024


def test_collect_host_handles_malformed_zram_stats(tmp_path: Path) -> None:
    proc = _base_proc(
        tmp_path,
        "Filename\t\t\t\tType\t\tSize\t\tUsed\t\tPriority\n/dev/zram0 partition 4096 2048 100\n",
    )
    sys = _base_sys(tmp_path)
    _zram_device(sys, "zram0", mm_stat="bad 0\n", io_stat="bad\n", bd_stat="bad\n")

    host = collect_host(proc_root=proc, sys_root=sys)

    assert host["host_swap_backend"].v == SWAP_BACKEND_CODES["zram_only"]
    assert host["host_zram_orig_bytes"].v == 0
    assert host["host_zram_ratio"].v is None
