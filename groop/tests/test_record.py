from __future__ import annotations

import json
import os
import subprocess
import sys
from array import array
from pathlib import Path

from conftest import fixture_frame, fixture_root, systemctl_fixture_runner
from groop.collect.collector import Collector
from groop.config import GroopConfig
from groop.model import Entity, EntityFrame, Frame, MetricValue, frame_from_jsonable, frame_to_jsonable
from groop.record.live import live_frame_stream, sample_timing
from groop.record.reader import RecordReader
from groop.record.replay import ReplayDriver
from groop.record.ring import DEFAULT_HISTORY_METRICS, HistoryRing
from groop.record.writer import RecordWriter


def _fixture_frame() -> Frame:
    return fixture_frame()


def _entity_frame(key: str, value: float) -> EntityFrame:
    return EntityFrame(
        entity=Entity(key=key, kind="scope", parent=""),
        metrics={"ram": MetricValue(value, "exact")},
    )


def _host_stub() -> dict[str, MetricValue]:
    return {
        "host_mem_total": MetricValue(16000, "host"),
        "host_mem_available": MetricValue(8000, "host"),
        "host_swap_total": MetricValue(4000, "host"),
        "host_swap_free": MetricValue(2000, "host"),
        "host_swapcached": MetricValue(100, "host"),
        "host_zswap_pool": MetricValue(50, "host"),
        "host_zswap_stored": MetricValue(100, "host"),
        "host_zswap_ratio": MetricValue(2.0, "host"),
        "host_disk_swap": MetricValue(0, "host"),
        "host_load1": MetricValue(0.1, "host"),
        "host_load5": MetricValue(0.2, "host"),
        "host_load15": MetricValue(0.3, "host"),
        "host_uptime_s": MetricValue(1000, "host"),
        "host_psi_mem_some_avg10": MetricValue(0.0, "host"),
        "host_psi_mem_full_avg10": MetricValue(0.0, "host"),
        "host_psi_io_some_avg10": MetricValue(0.0, "host"),
        "host_psi_io_full_avg10": MetricValue(0.0, "host"),
        "host_psi_cpu_some_avg10": MetricValue(0.0, "host"),
        "host_zswap_enabled": MetricValue(1, "host"),
        "host_zswap_max_pool_percent": MetricValue(20, "host"),
    }


def _fixture_collector(*, times: list[float]) -> Collector:
    return Collector(
        cgroup_root=fixture_root() / "cgroupfs" / "gstammtisch",
        config=GroopConfig(interval=5.0, tiers={"prod": ["system.slice"]}, protected_services=("soulmask-paks.slice",)),
        docker_inspect=lambda _cid: None,
        host_collector=_host_stub,
        now=lambda: times.pop(0),
        network_providers=(),
        systemctl_show_runner=systemctl_fixture_runner("gstammtisch"),
    )


def _assert_record_round_trip_and_append_safe(tmp_path: Path, suffix: str) -> None:
    base = _fixture_frame()
    frames = [
        Frame(
            schema_version=base.schema_version,
            ts=base.ts + (index * base.interval_s),
            interval_s=base.interval_s,
            host=base.host,
            entities=base.entities,
        )
        for index in range(100)
    ]
    path = tmp_path / f"record{suffix}"
    with RecordWriter(path, started_at=base.ts) as writer:
        for frame in frames[:40]:
            writer.write_frame(frame)
    with RecordWriter(path, started_at=base.ts + 1) as writer:
        for frame in frames[40:]:
            writer.write_frame(frame)
    lines = path.read_text().splitlines() if suffix == ".jsonl" else None
    if lines is not None:
        assert json.loads(lines[0])["type"] == "header"
        assert sum(1 for line in lines if json.loads(line)["type"] == "header") == 1
    assert list(RecordReader(path)) == frames


def test_record_round_trip_jsonl(tmp_path: Path) -> None:
    _assert_record_round_trip_and_append_safe(tmp_path, ".jsonl")


def test_record_round_trip_zst_path(tmp_path: Path) -> None:
    _assert_record_round_trip_and_append_safe(tmp_path, ".jsonl.zst")


def test_live_fixture_stream_records_and_replays_canonical_frames(tmp_path: Path) -> None:
    record_path = tmp_path / "live.jsonl"
    collector = _fixture_collector(times=[100.0, 105.0])
    monotonic_values = iter([0.0, 1.0, 5.0, 11.0])
    sleeps: list[float] = []
    with RecordWriter(record_path, config=collector.config, started_at=100.0) as writer:
        stream = live_frame_stream(
            collector,
            writer=writer,
            monotonic=lambda: next(monotonic_values),
            sleeper=sleeps.append,
        )
        frames = [next(stream), next(stream)]
    assert len(frames) == 2
    assert all(frame.entities for frame in frames)
    assert sleeps == [4.0]
    assert list(RecordReader(record_path)) == frames
    replayed = ReplayDriver.from_path(record_path, config=collector.config).frames
    assert [frame_to_jsonable(frame) for frame in replayed] == [frame_to_jsonable(frame) for frame in frames]


def test_sample_timing_skips_negative_sleep_on_overrun() -> None:
    timing = sample_timing(5.0, 7.25)
    assert timing.sleep_s == 0.0
    assert timing.overrun_s == 2.25
    assert timing.skipped_sleep is True


def test_reader_tolerates_truncated_final_line(tmp_path: Path) -> None:
    base = _fixture_frame()
    path = tmp_path / "truncated.jsonl"
    with RecordWriter(path, started_at=base.ts) as writer:
        writer.write_frame(base)
    with path.open("a", encoding="utf-8") as fh:
        fh.write('{"type":"frame","schema_version":1,"ts"')
    assert list(RecordReader(path)) == [base]


def test_history_ring_last_minmax_and_grace() -> None:
    ring = HistoryRing(capacity=4, tracked_metrics=("ram",), entity_grace_frames=1)
    ring.append_frame(Frame(1, 1.0, 1.0, {}, {"a.scope": _entity_frame("a.scope", 1.0)}))
    ring.append_frame(Frame(1, 2.0, 1.0, {}, {"a.scope": _entity_frame("a.scope", 3.0)}))
    assert ring.last("a.scope", "ram", 4) == [1.0, 3.0]
    assert ring.minmax("a.scope", "ram", 4) == (1.0, 3.0)
    ring.append_frame(Frame(1, 3.0, 1.0, {}, {}))
    assert ring.last("a.scope", "ram", 4) == [1.0, 3.0, None]
    ring.append_frame(Frame(1, 4.0, 1.0, {}, {}))
    assert not ring.has_series("a.scope", "ram")
    ring.append_frame(Frame(1, 5.0, 1.0, {}, {"b.scope": _entity_frame("b.scope", 7.0)}))
    assert ring.last("b.scope", "ram", 4) == [7.0]


def test_history_ring_storage_budget_uses_numeric_arrays() -> None:
    assert len(DEFAULT_HISTORY_METRICS) == 24
    entities = {
        f"entity-{index}.scope": EntityFrame(
            entity=Entity(key=f"entity-{index}.scope", kind="scope", parent=""),
            metrics={
                metric: MetricValue(index + metric_index + 1.0, "exact")
                for metric_index, metric in enumerate(DEFAULT_HISTORY_METRICS)
            },
        )
        for index in range(40)
    }
    frame = Frame(1, 100.0, 5.0, {}, entities)
    ring = HistoryRing(capacity=2880, tracked_metrics=DEFAULT_HISTORY_METRICS, entity_grace_frames=0)
    for _ in range(2880):
        ring.append_frame(frame)
    assert ring.series_count == 40 * len(DEFAULT_HISTORY_METRICS)
    assert ring.storage_bytes == 40 * len(DEFAULT_HISTORY_METRICS) * 2880 * array("f").itemsize
    assert all(isinstance(series.samples, array) for series in ring._series.values())
    estimated_bytes = sys.getsizeof(ring._series) + sum(sys.getsizeof(series.samples) for series in ring._series.values())
    assert estimated_bytes < 50 * 1024 * 1024


def test_replay_cli_smoke_uses_golden_fixture() -> None:
    env = os.environ.copy()
    env["PYTHONPATH"] = f"/tmp/groop-pytest:{fixture_root().parents[1] / 'src'}"
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "groop.cli",
            "--replay",
            str(fixture_root() / "frames" / "gstammtisch-once.jsonl"),
            "--step",
            "--ui-smoke",
        ],
        check=True,
        cwd=fixture_root().parents[1],
        env=env,
        text=True,
        stdout=subprocess.PIPE,
    )
    assert proc.stdout.strip() == "ui smoke ok frames=1 view=tree profile=auto"


def test_replay_cli_uses_config_path_defaults(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        "\n".join(
            (
                "[general]",
                'default_view = "container"',
                'default_column_profile = "minimal"',
            )
        )
    )
    env = os.environ.copy()
    env["PYTHONPATH"] = f"/tmp/groop-pytest:{fixture_root().parents[1] / 'src'}"
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "groop.cli",
            "--config",
            str(config_path),
            "--replay",
            str(fixture_root() / "frames" / "gstammtisch-once.jsonl"),
            "--step",
            "--ui-smoke",
        ],
        check=True,
        cwd=fixture_root().parents[1],
        env=env,
        text=True,
        stdout=subprocess.PIPE,
    )
    assert proc.stdout.strip() == "ui smoke ok frames=1 view=container profile=minimal"


def test_replay_cli_profile_override_supports_custom_profile(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        "\n".join(
            (
                "[general]",
                'default_column_profile = "minimal"',
                "",
                "[columns.profiles.forensics]",
                'list = ["name", "ram", "cpu_pct"]',
            )
        )
    )
    env = os.environ.copy()
    env["PYTHONPATH"] = f"/tmp/groop-pytest:{fixture_root().parents[1] / 'src'}"
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "groop.cli",
            "--config",
            str(config_path),
            "--profile",
            "forensics",
            "--replay",
            str(fixture_root() / "frames" / "gstammtisch-once.jsonl"),
            "--step",
            "--ui-smoke",
        ],
        check=True,
        cwd=fixture_root().parents[1],
        env=env,
        text=True,
        stdout=subprocess.PIPE,
    )
    assert proc.stdout.strip() == "ui smoke ok frames=1 view=tree profile=forensics"


def test_record_cli_once_writes_header_and_frame(tmp_path: Path) -> None:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(fixture_root().parents[1] / "src")
    record_path = tmp_path / "once.jsonl"
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "groop.cli",
            "--record",
            str(record_path),
            "--once",
            "--json",
            "--cgroup-root",
            str(fixture_root() / "cgroupfs" / "gstammtisch"),
        ],
        check=True,
        cwd=fixture_root().parents[1],
        env=env,
        text=True,
        stdout=subprocess.PIPE,
    )
    payload = json.loads(proc.stdout)
    assert payload["schema_version"] == 1
    lines = record_path.read_text().splitlines()
    assert json.loads(lines[0])["type"] == "header"
    assert json.loads(lines[1])["type"] == "frame"


def test_record_cli_runs_ui_and_writes_frames(tmp_path: Path) -> None:
    env = os.environ.copy()
    env["PYTHONPATH"] = f"/tmp/groop-pytest:{fixture_root().parents[1] / 'src'}"
    record_path = tmp_path / "live-ui.jsonl"
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "groop.cli",
            "--record",
            str(record_path),
            "--cgroup-root",
            str(fixture_root() / "cgroupfs" / "gstammtisch"),
            "--ui-smoke",
        ],
        check=True,
        cwd=fixture_root().parents[1],
        env=env,
        text=True,
        stdout=subprocess.PIPE,
    )
    assert proc.stdout.strip() == "ui smoke ok frames=1 view=tree profile=auto"
    lines = record_path.read_text().splitlines()
    assert json.loads(lines[0])["type"] == "header"
    assert json.loads(lines[1])["type"] == "frame"


def test_cli_version_reports_package_version() -> None:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(fixture_root().parents[1] / "src")
    proc = subprocess.run(
        [sys.executable, "-m", "groop.cli", "--version"],
        check=True,
        cwd=fixture_root().parents[1],
        env=env,
        text=True,
        stdout=subprocess.PIPE,
    )
    assert proc.stdout.strip() == "groop 0.1.0"
