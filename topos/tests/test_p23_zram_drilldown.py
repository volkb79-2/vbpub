from __future__ import annotations

from pathlib import Path

import pytest

from conftest import fixture_root
from topos.collect.collector import Collector
from topos.collect.host import SWAP_BACKEND_CODES, collect_host, collect_host_meta
from topos.config import DamonConfig, ToposConfig
from topos.model import Entity, EntityFrame, Frame, MetricValue, frame_from_jsonable, frame_to_jsonable
from topos.ui.hostmem import render_host_memory_text


def _make_zram_device(name: str, sys: Path, *, mm_stat: str, io_stat: str = "0 0 0 0\n", bd_stat: str = "0 0 0\n") -> None:
    dev = sys / "block" / name
    dev.mkdir(parents=True)
    (dev / "mm_stat").write_text(mm_stat)
    (dev / "io_stat").write_text(io_stat)
    (dev / "bd_stat").write_text(bd_stat)


def _minimal_frame(*, host_meta: dict[str, object] | None = None) -> Frame:
    entity = Entity(key="", kind="root", parent=None)
    return Frame(
        schema_version=1,
        ts=100.0,
        interval_s=5.0,
        host={"host_mem_total": MetricValue(16000, "host")},
        entities={"": EntityFrame(entity=entity, metrics={})},
        host_meta=host_meta,
    )


def test_frame_serialization_round_trip_with_zram_metadata() -> None:
    """A frame with host_meta containing zram devices round-trips faithfully."""
    zram_devices = [
        {
            "name": "zram0",
            "orig_bytes": 8192,
            "compr_bytes": 4096,
            "mem_used_bytes": 6144,
            "mem_limit_bytes": 1048576,
            "mem_used_max_bytes": 8192,
            "same_pages": 2,
            "huge_pages": 1,
            "failed_reads": 1,
            "failed_writes": 2,
            "writeback_bytes": 20480,
            "ratio": 2.0,
            "efficiency": 0.6666666666666666,
        },
        {
            "name": "zram1",
            "orig_bytes": 16384,
            "compr_bytes": 8192,
            "mem_used_bytes": 12288,
            "mem_limit_bytes": 0,
            "mem_used_max_bytes": 16384,
            "same_pages": 0,
            "huge_pages": 0,
            "failed_reads": 0,
            "failed_writes": 0,
            "writeback_bytes": 0,
            "ratio": 2.0,
            "efficiency": 0.6666666666666666,
        },
    ]
    original = _minimal_frame(host_meta={"zram_devices": zram_devices})
    jsonable = frame_to_jsonable(original)
    assert "host_meta" in jsonable
    assert jsonable["host_meta"]["zram_devices"] == zram_devices

    restored = frame_from_jsonable(jsonable)
    assert restored.host_meta is not None
    assert restored.host_meta["zram_devices"] == zram_devices


def test_frame_serialization_old_frame_compat() -> None:
    """A frame dict without host_meta deserializes without error."""
    jsonable = {
        "schema_version": 1,
        "ts": 100.0,
        "interval_s": 5.0,
        "host": {"host_mem_total": [16000, "host"]},
        "entities": {},
    }
    restored = frame_from_jsonable(jsonable)
    assert restored.host_meta is None
    assert restored.host["host_mem_total"].v == 16000


def test_frame_serialization_old_frame_compat_with_entities() -> None:
    """An old frame (schema v1) without host_meta, with entities, loads cleanly."""
    jsonable = {
        "schema_version": 1,
        "ts": 200.0,
        "interval_s": 5.0,
        "host": {"host_swap_backend": [2, "host"]},
        "entities": {
            "system.slice": {
                "entity": {"key": "system.slice", "kind": "slice", "parent": ""},
                "metrics": {"ram": [1048576, "exact"]},
                "findings": [],
            }
        },
    }
    restored = frame_from_jsonable(jsonable)
    assert restored.host_meta is None
    assert restored.entities["system.slice"].entity.key == "system.slice"


def test_host_memory_text_renders_zram_devices() -> None:
    """Host-memory text includes a table with per-device zram details."""
    zram_devices = [
        {
            "name": "zram0",
            "orig_bytes": 8_388_608,   # 8 MiB
            "compr_bytes": 4_194_304,   # 4 MiB
            "mem_used_bytes": 6_291_456,  # 6 MiB
            "mem_limit_bytes": 0,
            "mem_used_max_bytes": 8_388_608,
            "same_pages": 2,
            "huge_pages": 1,
            "failed_reads": 1,
            "failed_writes": 2,
            "writeback_bytes": 20480,
            "ratio": 2.0,
            "efficiency": 0.6667,
        },
    ]
    frame = _minimal_frame(host_meta={"zram_devices": zram_devices})
    config = ToposConfig(damon=DamonConfig())
    text = render_host_memory_text(frame, config=config, damon_root=Path("/sys/kernel/mm/damon/admin"))
    assert "ZRAM DEVICES" in text
    assert "zram0" in text
    assert "8.0MiB" in text  # orig_bytes
    assert "4.0MiB" in text  # compr_bytes
    assert "6.0MiB" in text  # mem_used_bytes
    assert "2.0" in text     # ratio
    assert "1" in text       # failed_reads
    assert "2" in text       # failed_writes
    assert "20.0KiB" in text # writeback_bytes
    assert "per-cgroup zram compression/cost attribution is unavailable" in text


def test_host_memory_text_renders_no_zram_devices() -> None:
    """Host-memory text shows a no-device state when no zram metadata exists."""
    frame = _minimal_frame(host_meta=None)
    config = ToposConfig(damon=DamonConfig())
    text = render_host_memory_text(frame, config=config, damon_root=Path("/sys/kernel/mm/damon/admin"))
    assert "ZRAM DEVICES" in text
    assert "(no zram devices)" in text
    assert "per-cgroup zram compression/cost attribution is unavailable" in text


def test_host_memory_text_renders_no_zram_devices_empty_list() -> None:
    """Host-memory text shows no-device state when zram_devices is an empty list."""
    frame = _minimal_frame(host_meta={"zram_devices": []})
    config = ToposConfig(damon=DamonConfig())
    text = render_host_memory_text(frame, config=config, damon_root=Path("/sys/kernel/mm/damon/admin"))
    assert "ZRAM DEVICES" in text
    assert "(no zram devices)" in text


def test_host_memory_text_handles_missing_host_meta_key() -> None:
    """Host-memory text handles a host_meta that lacks the zram_devices key."""
    frame = _minimal_frame(host_meta={"other_key": "value"})
    config = ToposConfig(damon=DamonConfig())
    text = render_host_memory_text(frame, config=config, damon_root=Path("/sys/kernel/mm/damon/admin"))
    assert "ZRAM DEVICES" in text
    assert "(no zram devices)" in text


def test_host_memory_text_handles_malformed_replay_metadata() -> None:
    """Host-memory text tolerates malformed metadata from replay files."""
    frame = _minimal_frame(
        host_meta={
            "zram_devices": [
                {
                    "name": "zram0",
                    "orig_bytes": "bad",
                    "compr_bytes": None,
                    "mem_used_bytes": object(),
                    "ratio": "bad",
                    "failed_reads": "bad",
                    "failed_writes": None,
                    "writeback_bytes": "bad",
                }
            ]
        }
    )
    config = ToposConfig(damon=DamonConfig())
    text = render_host_memory_text(frame, config=config, damon_root=Path("/sys/kernel/mm/damon/admin"))
    assert "zram0" in text
    assert "     -" in text


def test_collect_host_meta_with_devices(tmp_path: Path) -> None:
    """collect_host_meta returns per-device details when zram devices exist."""
    sys = tmp_path / "sys"
    (sys / "block").mkdir(parents=True)
    _make_zram_device("zram0", sys, mm_stat="8192 4096 6144 1048576 8192 2 0 1 3\n", io_stat="1 2 0 10\n", bd_stat="5 6 7\n")
    _make_zram_device("zram1", sys, mm_stat="16384 8192 12288 0 16384 0 0 0 0\n")

    meta = collect_host_meta(sys_root=sys)
    assert "zram_devices" in meta
    devices = meta["zram_devices"]
    assert isinstance(devices, list)
    assert len(devices) == 2

    d0 = dict(devices[0])  # type: ignore[arg-type]
    assert d0["name"] == "zram0"
    assert d0["orig_bytes"] == 8192
    assert d0["compr_bytes"] == 4096
    assert d0["mem_used_bytes"] == 6144
    assert d0["mem_limit_bytes"] == 1048576
    assert d0["failed_reads"] == 1
    assert d0["failed_writes"] == 2
    assert d0["writeback_bytes"] == 5 * 4096
    assert d0["ratio"] == pytest.approx(2.0)
    assert d0["efficiency"] == pytest.approx(4096 / 6144)

    d1 = dict(devices[1])  # type: ignore[arg-type]
    assert d1["name"] == "zram1"
    assert d1["orig_bytes"] == 16384
    assert d1["compr_bytes"] == 8192
    assert d1["mem_used_bytes"] == 12288
    assert d1["failed_reads"] == 0
    assert d1["failed_writes"] == 0
    assert d1["ratio"] == pytest.approx(2.0)


def test_collect_host_meta_malformed_stats(tmp_path: Path) -> None:
    """collect_host_meta gracefully handles malformed stat files."""
    sys = tmp_path / "sys"
    (sys / "block").mkdir(parents=True)
    _make_zram_device("zram0", sys, mm_stat="bad 0\n", io_stat="bad\n", bd_stat="bad\n")

    meta = collect_host_meta(sys_root=sys)
    devices = meta.get("zram_devices", [])
    assert len(devices) == 1
    d0 = dict(devices[0])  # type: ignore[arg-type]
    assert d0["name"] == "zram0"
    assert d0["orig_bytes"] == 0
    assert d0["compr_bytes"] == 0
    assert d0["mem_used_bytes"] == 0
    assert d0["ratio"] is None
    assert d0["efficiency"] is None


def test_collect_host_meta_no_zram_devices(tmp_path: Path) -> None:
    """collect_host_meta returns empty list when no zram devices exist."""
    sys = tmp_path / "sys"
    (sys / "block").mkdir(parents=True)
    meta = collect_host_meta(proc_root=tmp_path, sys_root=sys)
    assert meta["zram_devices"] == []
    assert "net_device_counters" in meta
    assert "block_device_counters" in meta


def test_collect_host_aggregate_metrics_unchanged(tmp_path: Path) -> None:
    """Host aggregate ZRAM metrics are unchanged by the addition of host_meta."""
    proc = _base_proc(tmp_path, "Filename\t\t\t\tType\t\tSize\t\tUsed\t\tPriority\n/dev/zram0 partition 4096 2048 100\n")
    sys = _base_sys(tmp_path)
    _make_zram_device(
        "zram0", sys,
        mm_stat="8192 4096 6144 1048576 8192 2 0 1 3\n",
        io_stat="1 2 0 10\n",
        bd_stat="5 6 7\n",
    )

    host = collect_host(proc_root=proc, sys_root=sys)
    assert host["host_swap_backend"].v == SWAP_BACKEND_CODES["zram_only"]
    assert host["host_zram_orig_bytes"].v == 8192
    assert host["host_zram_compr_bytes"].v == 4096
    assert host["host_zram_mem_used_bytes"].v == 6144
    assert host["host_zram_failed_reads"].v == 1
    assert host["host_zram_failed_writes"].v == 2
    assert host["host_zram_writeback_bytes"].v == 5 * 4096


def test_collector_default_host_uses_configured_sys_root(tmp_path: Path) -> None:
    """Collector sys_root feeds both aggregate host metrics and host_meta."""
    proc = _base_proc(
        tmp_path,
        "Filename\t\t\t\tType\t\tSize\t\tUsed\t\tPriority\n/dev/zram0 partition 4096 2048 100\n",
    )
    sys = _base_sys(tmp_path)
    _make_zram_device("zram0", sys, mm_stat="8192 4096 6144 0 0 0 0 0 0\n")

    collector = Collector(
        cgroup_root=fixture_root() / "cgroupfs" / "gstammtisch",
        config=ToposConfig(interval=5.0),
        docker_inspect=lambda _cid: None,
        now=lambda: 100.0,
        network_providers=(),
        proc_root=proc,
        sys_root=sys,
        damon_root=fixture_root() / "damonfs" / "no-root" / "kdamonds",
    )

    frame = collector.collect_once()

    assert frame.host["host_zram_orig_bytes"].v == 8192
    assert frame.host_meta == {
        "zram_devices": [
            {
                "name": "zram0",
                "orig_bytes": 8192,
                "compr_bytes": 4096,
                "mem_used_bytes": 6144,
                "mem_limit_bytes": 0,
                "mem_used_max_bytes": 0,
                "same_pages": 0,
                "huge_pages": 0,
                "failed_reads": 0,
                "failed_writes": 0,
                "writeback_bytes": 0,
                "ratio": 2.0,
                "efficiency": pytest.approx(4096 / 6144),
            }
        ],
        "net_devices": [],
        "block_devices": [],
    }


def test_zram_device_lines_ratio_none_on_zero_compr(tmp_path: Path) -> None:
    """When compressed size is zero, ratio is None."""
    sys = tmp_path / "sys"
    (sys / "block").mkdir(parents=True)
    _make_zram_device("zram0", sys, mm_stat="8192 0 6144 0 0 0 0 0 0\n")
    meta = collect_host_meta(sys_root=sys)
    devices = meta["zram_devices"]
    assert len(devices) == 1
    d0 = dict(devices[0])  # type: ignore[arg-type]
    assert d0["ratio"] is None
    assert d0["efficiency"] == 0.0  # compr=0 / mem_used=6144 => 0.0


# -- helpers matching existing test_host_swap.py conventions --


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
