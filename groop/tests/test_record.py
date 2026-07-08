from __future__ import annotations

import json
import os
import subprocess
import sys
from array import array
from pathlib import Path

from conftest import fixture_frame, fixture_root
from groop.model import Entity, EntityFrame, Frame, MetricValue, frame_from_jsonable
from groop.record.reader import RecordReader
from groop.record.ring import DEFAULT_HISTORY_METRICS, HistoryRing
from groop.record.writer import RecordWriter


def _fixture_frame() -> Frame:
    return fixture_frame()


def _entity_frame(key: str, value: float) -> EntityFrame:
    return EntityFrame(
        entity=Entity(key=key, kind="scope", parent=""),
        metrics={"ram": MetricValue(value, "exact")},
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
