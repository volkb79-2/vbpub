from __future__ import annotations

from pathlib import Path

import pytest

from conftest import fixture_root
from groop.collect.host import (
    _gpu_metrics,
    collect_host,
    collect_host_meta,
)
from groop.config import GroopConfig
from groop.model import Entity, EntityFrame, Frame, MetricValue
from groop.ui.banner import render_banner


def _amdgpu_sys(root: Path) -> Path:
    """Create a sysfs tree with a single amdgpu card."""
    sys = root / "sys"
    drm = sys / "class" / "drm"
    amd = drm / "card0" / "device"
    amd.mkdir(parents=True)
    src = fixture_root() / "sysfs" / "drm" / "amdgpu" / "card0" / "device"
    (amd / "mem_info_vram_total").write_text((src / "mem_info_vram_total").read_text())
    (amd / "mem_info_vram_used").write_text((src / "mem_info_vram_used").read_text())
    (amd / "gpu_busy_percent").write_text((src / "gpu_busy_percent").read_text())
    return sys


def _base_sys(root: Path) -> Path:
    """Create a minimal sysfs tree WITHOUT DRM for baseline tests."""
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


def _base_proc(root: Path) -> Path:
    """Create a minimal procfs tree."""
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
    (proc / "swaps").write_text("Filename\t\t\t\tType\t\tSize\t\tUsed\t\tPriority\n")
    return proc


def _empty_sys(root: Path) -> Path:
    """Create a sysfs with /sys/class/drm but no cards."""
    sys = _base_sys(root)
    drm = sys / "class" / "drm"
    drm.mkdir(parents=True)
    return sys


# --- Oracle 1: Present-GPU fixture (amdgpu) ----------------------------


def test_gpu_present_fixture_exact_values(tmp_path: Path) -> None:
    """Present amdgpu fixture yields the four metrics with exact expected values."""
    sys = _amdgpu_sys(tmp_path)
    metrics = _gpu_metrics(sys)

    assert metrics["host_gpu_vram_total"].v == 8589934592
    assert metrics["host_gpu_vram_total"].src == "host"
    assert metrics["host_gpu_vram_used"].v == 4509715660
    assert metrics["host_gpu_vram_used"].src == "host"
    assert metrics["host_gpu_busy_pct"].v == 37
    assert metrics["host_gpu_busy_pct"].src == "host"
    assert metrics["host_gpu_count"].v == 1
    assert metrics["host_gpu_count"].src == "host"


# --- Oracle 2: Absent-GPU ------------------------------------------------


def test_gpu_absent_no_drm_dir(tmp_path: Path) -> None:
    """No /sys/class/drm at all: all four metrics unavail_kernel, count=0."""
    sys = _base_sys(tmp_path)  # no drm directory at all
    metrics = _gpu_metrics(sys)

    assert metrics["host_gpu_vram_total"].v is None
    assert metrics["host_gpu_vram_total"].src == "unavail_kernel"
    assert metrics["host_gpu_vram_used"].v is None
    assert metrics["host_gpu_vram_used"].src == "unavail_kernel"
    assert metrics["host_gpu_busy_pct"].v is None
    assert metrics["host_gpu_busy_pct"].src == "unavail_kernel"
    assert metrics["host_gpu_count"].v == 0
    assert metrics["host_gpu_count"].src == "host"


def test_gpu_absent_empty_drm(tmp_path: Path) -> None:
    """Empty /sys/class/drm (no card dirs): all four metrics unavail_kernel, count=0."""
    sys = _empty_sys(tmp_path)
    metrics = _gpu_metrics(sys)

    assert metrics["host_gpu_vram_total"].v is None
    assert metrics["host_gpu_vram_total"].src == "unavail_kernel"
    assert metrics["host_gpu_vram_used"].v is None
    assert metrics["host_gpu_vram_used"].src == "unavail_kernel"
    assert metrics["host_gpu_busy_pct"].v is None
    assert metrics["host_gpu_busy_pct"].src == "unavail_kernel"
    assert metrics["host_gpu_count"].v == 0
    assert metrics["host_gpu_count"].src == "host"


# --- Oracle 3: Driver without the files (i915) ---------------------------


def test_gpu_i915_present_but_no_vram_files(tmp_path: Path) -> None:
    """Card present but driver exposes none of the DRM sysfs files.

    VRAM metrics unavail_kernel (driver absent), count still counts the card.
    This separates 'no GPU' from 'a GPU I cannot read'.
    """
    sys = _base_sys(tmp_path)
    drm = sys / "class" / "drm"
    drm.mkdir(parents=True)
    (drm / "card0" / "device").mkdir(parents=True)
    # No mem_info_* or gpu_busy_percent files -- i915 does not expose them

    metrics = _gpu_metrics(sys)
    assert metrics["host_gpu_vram_total"].v is None
    assert metrics["host_gpu_vram_total"].src == "unavail_kernel"
    assert metrics["host_gpu_vram_used"].v is None
    assert metrics["host_gpu_vram_used"].src == "unavail_kernel"
    assert metrics["host_gpu_busy_pct"].v is None
    assert metrics["host_gpu_busy_pct"].src == "unavail_kernel"
    # Card IS present, count reflects it
    assert metrics["host_gpu_count"].v == 1
    assert metrics["host_gpu_count"].src == "host"


# --- Oracle 4: Multi-GPU -------------------------------------------------


def test_gpu_multi_gpu_sum_and_max(tmp_path: Path) -> None:
    """Two amdgpu cards: VRAM sums, busy is max, count is 2.

    Fixture engineered so max (90%) differs from mean (50%).
    """
    sys = _base_sys(tmp_path)
    drm = sys / "class" / "drm"
    multi_src = fixture_root() / "sysfs" / "drm" / "multi"

    for card in ("card0", "card1"):
        dev = drm / card / "device"
        dev.mkdir(parents=True)
        card_src = multi_src / card / "device"
        (dev / "mem_info_vram_total").write_text((card_src / "mem_info_vram_total").read_text())
        (dev / "mem_info_vram_used").write_text((card_src / "mem_info_vram_used").read_text())
        (dev / "gpu_busy_percent").write_text((card_src / "gpu_busy_percent").read_text())

    metrics = _gpu_metrics(sys)

    # Sum: 4294967296 + 4294967296 = 8589934592
    assert metrics["host_gpu_vram_total"].v == 8589934592
    # Sum: 2147483648 + 1073741824 = 3221225472
    assert metrics["host_gpu_vram_used"].v == 3221225472
    # Max: max(10, 90) = 90 -- NOT mean 50
    assert metrics["host_gpu_busy_pct"].v == 90
    assert metrics["host_gpu_count"].v == 2


# --- Oracle 5: Connector nodes are not counted ---------------------------


def test_gpu_connector_nodes_not_counted(tmp_path: Path) -> None:
    """card0-DP-1 and card0-HDMI-A-1 are connector nodes, not counted as cards."""
    sys = _base_sys(tmp_path)
    drm = sys / "class" / "drm"
    conn_src = fixture_root() / "sysfs" / "drm" / "connectors"

    # Actual card
    dev = drm / "card0" / "device"
    dev.mkdir(parents=True)
    card_src = conn_src / "card0" / "device"
    (dev / "mem_info_vram_total").write_text((card_src / "mem_info_vram_total").read_text())
    (dev / "mem_info_vram_used").write_text((card_src / "mem_info_vram_used").read_text())
    (dev / "gpu_busy_percent").write_text((card_src / "gpu_busy_percent").read_text())

    # Connector nodes -- must NOT be counted as cards
    (drm / "card0-DP-1").mkdir()
    (drm / "card0-HDMI-A-1").mkdir()

    metrics = _gpu_metrics(sys)
    # card0 alone should be counted: count=1
    assert metrics["host_gpu_count"].v == 1
    # VRAM should be from card0 only
    assert metrics["host_gpu_vram_total"].v == 8589934592


# --- Oracle 6: Malformed sysfs -------------------------------------------


def test_gpu_malformed_non_numeric(tmp_path: Path) -> None:
    """Non-numeric mem_info_vram_used: that metric degrades to unavail_kernel.

    The rest of the frame (including other GPU metrics) remains intact.
    """
    sys = _base_sys(tmp_path)
    drm = sys / "class" / "drm"
    dev = drm / "card0" / "device"
    dev.mkdir(parents=True)

    # Valid files
    (dev / "mem_info_vram_total").write_text("8589934592\n")
    (dev / "gpu_busy_percent").write_text("37\n")
    # Non-numeric vram_used
    (dev / "mem_info_vram_used").write_text("not_a_number\n")

    metrics = _gpu_metrics(sys)
    assert metrics["host_gpu_vram_total"].v == 8589934592
    assert metrics["host_gpu_vram_total"].src == "host"
    assert metrics["host_gpu_vram_used"].v is None
    assert metrics["host_gpu_vram_used"].src == "unavail_kernel"
    assert metrics["host_gpu_busy_pct"].v == 37
    assert metrics["host_gpu_count"].v == 1


def test_gpu_malformed_truncated(tmp_path: Path) -> None:
    """Truncated gpu_busy_percent (empty): degrades to unavail_kernel, does not raise."""
    sys = _base_sys(tmp_path)
    drm = sys / "class" / "drm"
    dev = drm / "card0" / "device"
    dev.mkdir(parents=True)

    (dev / "mem_info_vram_total").write_text("8589934592\n")
    (dev / "mem_info_vram_used").write_text("4509715660\n")
    # Empty file
    (dev / "gpu_busy_percent").write_text("\n")

    metrics = _gpu_metrics(sys)
    assert metrics["host_gpu_vram_total"].v == 8589934592
    assert metrics["host_gpu_vram_used"].v == 4509715660
    assert metrics["host_gpu_busy_pct"].v is None
    assert metrics["host_gpu_busy_pct"].src == "unavail_kernel"
    assert metrics["host_gpu_count"].v == 1


# --- Oracle 7: Banner ----------------------------------------------------


def test_gpu_banner_present(tmp_path: Path) -> None:
    """Banner contains GPU segment when amdgpu is present.

    Assert the rendered cells, not just the substring.
    """
    proc = _base_proc(tmp_path)
    sys = _amdgpu_sys(tmp_path)
    # Add zswap baseline so collect_host doesn't fail on zswap reads
    _base_sys(tmp_path)
    host = collect_host(proc_root=proc, sys_root=sys)
    # Prove collection code ran
    assert "host_gpu_vram_total" in host
    assert host["host_gpu_vram_total"].v == 8589934592

    meta = collect_host_meta(proc_root=proc, sys_root=sys)
    frame = _minimal_frame(host=host, host_meta=meta)
    snapshot = render_banner(frame, GroopConfig())

    assert "GPU 4.2GiB/8.0GiB (busy 37%)" in snapshot.lines


def test_gpu_banner_absent(tmp_path: Path) -> None:
    """Banner does NOT contain GPU segment when no GPU is present."""
    proc = _base_proc(tmp_path)
    sys = _base_sys(tmp_path)  # no DRM directory
    host = collect_host(proc_root=proc, sys_root=sys)
    # Prove collection code ran
    assert "host_gpu_vram_total" in host
    assert host["host_gpu_vram_total"].v is None
    assert host["host_gpu_vram_total"].src == "unavail_kernel"

    meta = collect_host_meta(proc_root=proc, sys_root=sys)
    frame = _minimal_frame(host=host, host_meta=meta)
    snapshot = render_banner(frame, GroopConfig())
    lines = "\n".join(snapshot.lines)
    assert "GPU" not in lines


def test_gpu_banner_i915_no_segment(tmp_path: Path) -> None:
    """Banner does NOT contain GPU segment when driver exposes no DRM facts.

    A card exists (i915) but with no readable VRAM files -> no banner segment.
    This is the key case: 'a GPU I cannot read' must not look like an idle GPU.
    """
    proc = _base_proc(tmp_path)
    sys = _base_sys(tmp_path)
    drm = sys / "class" / "drm"
    drm.mkdir(parents=True)
    (drm / "card0" / "device").mkdir(parents=True)  # i915: no VRAM files

    host = collect_host(proc_root=proc, sys_root=sys)
    meta = collect_host_meta(proc_root=proc, sys_root=sys)
    frame = _minimal_frame(host=host, host_meta=meta)
    snapshot = render_banner(frame, GroopConfig())
    lines = "\n".join(snapshot.lines)

    assert "GPU" not in lines
    assert host["host_gpu_count"].v == 1  # card IS detected


def test_gpu_banner_multi_gpu(tmp_path: Path) -> None:
    """Multi-GPU banner includes count suffix x2."""
    sys = _base_sys(tmp_path)
    proc = _base_proc(tmp_path)
    drm = sys / "class" / "drm"
    multi_src = fixture_root() / "sysfs" / "drm" / "multi"

    for card in ("card0", "card1"):
        dev = drm / card / "device"
        dev.mkdir(parents=True)
        card_src = multi_src / card / "device"
        (dev / "mem_info_vram_total").write_text((card_src / "mem_info_vram_total").read_text())
        (dev / "mem_info_vram_used").write_text((card_src / "mem_info_vram_used").read_text())
        (dev / "gpu_busy_percent").write_text((card_src / "gpu_busy_percent").read_text())

    host = collect_host(proc_root=proc, sys_root=sys)
    meta = collect_host_meta(proc_root=proc, sys_root=sys)
    frame = _minimal_frame(host=host, host_meta=meta)
    snapshot = render_banner(frame, GroopConfig())

    # card0=4.0GiB/4.0GiB (busy 10%), card1=1.0GiB/4.0GiB (busy 90%)
    # Sum: total=8.0GiB, used=5.0GiB -- wait let me recalculate
    # card0: total=4294967296(4GiB), used=2147483648(2GiB), busy=10%
    # card1: total=4294967296(4GiB), used=1073741824(1GiB), busy=90%
    # Sum total: 8589934592(8GiB), Sum used: 3221225472(3GiB), max busy: 90%
    assert "GPU 3.0GiB/8.0GiB (busy 90%) x2" in snapshot.lines


# --- Oracle 8: Golden frames unaffected ----------------------------------


def test_gpu_non_gpu_fixtures_unaffected(tmp_path: Path) -> None:
    """Non-GPU fixtures produce the same host metrics as before.

    GPU metrics should be unavail_kernel but no other metric should change.
    """
    proc = _base_proc(tmp_path)
    sys = _base_sys(tmp_path)  # no DRM at all
    host = collect_host(proc_root=proc, sys_root=sys)

    # Verify GPU metrics are all unavail_kernel (count=0)
    for name in ("host_gpu_vram_total", "host_gpu_vram_used", "host_gpu_busy_pct"):
        assert host[name].v is None, f"{name}.v should be None"
        assert host[name].src == "unavail_kernel", f"{name}.src should be unavail_kernel"
    assert host["host_gpu_count"].v == 0
    assert host["host_gpu_count"].src == "host"

    # Verify existing metrics are unchanged
    assert host["host_mem_total"].v == 16384 * 1024
    assert host["host_mem_available"].v == 8192 * 1024
    assert host["host_load1"].v == 0.10
    assert host["host_uptime_s"].v == 100.0
    assert host["host_zswap_enabled"].v == 0


# --- Oracle 9: host_meta GPU detail -------------------------------------


def test_gpu_detail_present(tmp_path: Path) -> None:
    """GPU host_meta contains per-card detail."""
    sys = _amdgpu_sys(tmp_path)
    proc = _base_proc(tmp_path)
    meta = collect_host_meta(proc_root=proc, sys_root=sys)

    assert "gpu" in meta
    gpu = meta["gpu"]
    assert isinstance(gpu, dict)
    assert "card0" in gpu
    assert gpu["card0"]["vram_total"] == 8589934592
    assert gpu["card0"]["vram_used"] == 4509715660
    assert gpu["card0"]["busy_pct"] == 37


def test_gpu_detail_absent(tmp_path: Path) -> None:
    """GPU host_meta absent when no DRM cards exist."""
    proc = _base_proc(tmp_path)
    sys = _base_sys(tmp_path)  # no DRM
    meta = collect_host_meta(proc_root=proc, sys_root=sys)

    assert meta.get("gpu") is None


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
