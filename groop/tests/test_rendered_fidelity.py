from __future__ import annotations

from pathlib import Path

import pytest

from groop.config import GroopConfig
from groop.diag import annotate
from groop.model import DockerMeta, Entity, EntityFrame, Frame, MetricValue
from groop.record.replay import ReplayDriver
from groop.record.writer import RecordWriter
from groop.ui.table import FormattedTableSnapshot, snapshot_container_table


CONFIG = GroopConfig()
RENDER_ARGS = {
    "width": 140,
    "profile": "triage",
    "sort_by": "name",
    "filter_text": "",
    "selected_key": "web.scope",
}


def _entity(key: str, name: str) -> Entity:
    return Entity(
        key=key,
        kind="scope",
        parent="",
        docker=DockerMeta(cid=name, full_id=f"full-{name}", name=name, image="test:latest"),
        tier="prod",
    )


def _metrics(tick: int, *, database: bool) -> dict[str, MetricValue]:
    unavailable = MetricValue(None, "unavail_kernel")
    unlimited = MetricValue(None, "unlimited")
    return {
        "ram": unlimited if database and tick == 2 else MetricValue((tick + 1) * 128 * 1024**2, "exact"),
        "cpu_pct": MetricValue(10.0 + tick + (5.0 if database else 0.0), "exact"),
        "psi_mem_full_avg10": MetricValue(0.5 + tick, "exact"),
        "psi_io_some_avg10": unavailable if tick == 0 else MetricValue(0.2 * tick, "exact"),
        "rf_d_per_s": unavailable if database and tick == 0 else MetricValue(12.5 + tick, "exact"),
        "io_r_bps": MetricValue((tick + 1) * 1024**2, "exact"),
        "io_w_bps": MetricValue(tick * 512 * 1024, "exact"),
        "net_rx_bps": unavailable if database else MetricValue((tick + 1) * 10_000_000, "exact"),
        "net_tx_bps": MetricValue((tick + 1) * 5_000_000, "exact"),
    }


def _frames() -> list[Frame]:
    web = _entity("web.scope", "web")
    database = _entity("db.scope", "db")
    frames: list[Frame] = []
    for tick in range(3):
        frame = Frame(
            schema_version=1,
            ts=100.0 + tick * 5.0,
            interval_s=5.0,
            host={},
            entities={
                web.key: EntityFrame(
                    entity=web,
                    metrics=_metrics(tick, database=False),
                    network={"source_label": "eth0", "aggregation": "exact"},
                ),
                database.key: EntityFrame(
                    entity=database,
                    metrics=_metrics(tick, database=True),
                    network={"source_label": "net:NS", "aggregation": "netns"},
                ),
            },
        )
        # Collector.collect_once() performs this annotation before live recording.
        frames.append(annotate(frame, CONFIG))
    return frames


def _snapshot(frame: Frame) -> FormattedTableSnapshot:
    return snapshot_container_table(frame, CONFIG, **RENDER_ARGS)


def _zstandard_available() -> bool:
    try:
        import zstandard  # noqa: F401
    except ImportError:
        return False
    return True


@pytest.mark.parametrize("filename", ["fidelity.jsonl", "fidelity.jsonl.zst"])
def test_recorded_ticks_replay_with_identical_formatted_cells(tmp_path: Path, filename: str) -> None:
    if filename.endswith(".zst") and not _zstandard_available():
        pytest.skip("zstandard not installed")

    original = _frames()
    path = tmp_path / filename
    with RecordWriter(path, config=CONFIG, started_at=original[0].ts) as writer:
        for frame in original:
            writer.write_frame(frame)

    replayed = [item.frame for item in ReplayDriver.from_path(path, config=CONFIG).play(step=True)]
    original_snapshots = tuple(_snapshot(frame) for frame in original)
    replayed_snapshots = tuple(_snapshot(frame) for frame in replayed)

    assert replayed_snapshots == original_snapshots
    assert len(set(original_snapshots)) == len(original_snapshots)

    first = original_snapshots[0]
    assert first.row_keys == ("db.scope", "web.scope")
    assert first.columns == (
        "name",
        "pressure",
        "ram",
        "cpu_pct",
        "psi_mem_full_avg10",
        "psi_io_some_avg10",
        "rf_d_per_s",
        "io_r_bps",
        "io_w_bps",
        "net_rx_bps",
        "net_tx_bps",
        "net_source",
    )
    flattened = {cell for row in first.cells for cell in row}
    assert {"-", "0B/s", "net:NS:netns", "> web"} <= flattened
    final_flattened = {cell for row in original_snapshots[-1].cells for cell in row}
    assert "max" in final_flattened
