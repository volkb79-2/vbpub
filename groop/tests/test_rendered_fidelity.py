"""Rendered replay fidelity tests (P41).

Build a multi-tick frame sequence exercising all production formatting paths,
write via RecordWriter, read via RecordReader/ReplayDriver, and compare
byte-for-byte row keys, column identities, and plain formatted cell text.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from groop.config import GroopConfig
from groop.model import DockerMeta, Entity, EntityFrame, Frame, MetricValue
from groop.record.reader import RecordReader
from groop.record.replay import ReplayDriver
from groop.record.writer import RecordWriter
from groop.ui.table import (
    RenderedRows,
    format_metric_value,
    render_container_table,
    resolve_profile,
)

# ---------------------------------------------------------------------------
# Fixed rendering inputs — terminal layout is outside the comparison.
# ---------------------------------------------------------------------------
WIDTH = 140
PROFILE = "triage"
SORT = "name"
FILTER = ""
SELECTED = None

_CONFIG = GroopConfig()


def _host_stub() -> dict[str, MetricValue]:
    return {
        "host_mem_total": MetricValue(16_000_000_000, "host"),
        "host_mem_available": MetricValue(8_000_000_000, "host"),
        "host_swap_total": MetricValue(4_000_000_000, "host"),
        "host_swap_free": MetricValue(2_000_000_000, "host"),
        "host_swapcached": MetricValue(100_000_000, "host"),
        "host_zswap_pool": MetricValue(50_000_000, "host"),
        "host_zswap_stored": MetricValue(100_000_000, "host"),
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


def _entity(key: str, docker_name: str, tier: str) -> Entity:
    return Entity(
        key=key,
        kind="scope",
        parent="",
        docker=DockerMeta(
            cid=docker_name,
            full_id=f"full-{docker_name}",
            name=docker_name,
            image="test:latest",
        ),
        tier=tier,
    )


def _make_frame(
    ts: float,
    interval_s: float,
    entity_frames: dict[str, EntityFrame],
) -> Frame:
    return Frame(
        schema_version=1,
        ts=ts,
        interval_s=interval_s,
        host=_host_stub(),
        entities=entity_frames,
    )


# ---------------------------------------------------------------------------
# Multi-tick frame builder
# ---------------------------------------------------------------------------


def _build_fidelity_frames() -> list[Frame]:
    """Return a deterministic multi-tick frame sequence.

    Exercises: numeric rates/bytes/percentages, unavailable values, unlimited
    limits, network labels, and at least one value change per tick.
    """
    e_web = _entity("web.scope", "web", "prod")
    e_db = _entity("db.scope", "db", "prod")
    e_cache = _entity("cache.scope", "cache", "best_effort")

    # --- Tick 1 ---
    tick1 = _make_frame(100.0, 5.0, {
        e_web.key: EntityFrame(
            entity=e_web,
            metrics={
                "ram": MetricValue(256 * 1024 * 1024, "exact"),          # 256.0MiB
                "cpu_pct": MetricValue(45.2, "exact"),                   # 45.2%
                "rf_d_per_s": MetricValue(12.5, "exact"),                # 12.5/s
                "psi_mem_full_avg10": MetricValue(0.5, "exact"),         # 0.5%
                "psi_io_some_avg10": MetricValue(None, "unavail_perm"),  # - (dim)
                "io_r_bps": MetricValue(1 * 1024 * 1024, "exact"),       # 1.0MiB/s
                "io_w_bps": MetricValue(512 * 1024, "exact"),            # 512.0KiB/s
                "net_rx_bps": MetricValue(50_000_000, "exact"),          # 47.7MiB/s
                "net_tx_bps": MetricValue(10_000_000, "exact"),          # 9.5MiB/s
            },
            network={"source_label": "eth0"},
        ),
        e_db.key: EntityFrame(
            entity=e_db,
            metrics={
                "ram": MetricValue(1 * 1024 * 1024 * 1024, "exact"),    # 1.0GiB
                "cpu_pct": MetricValue(12.0, "exact"),                   # 12.0%
                "rf_d_per_s": MetricValue(None, "unavail_kernel"),       # - (dim)
                "psi_mem_full_avg10": MetricValue(3.2, "exact"),         # 3.2%
                "psi_io_some_avg10": MetricValue(1.1, "exact"),          # 1.1%
                "io_r_bps": MetricValue(500_000, "exact"),               # 488.3KiB/s
                "io_w_bps": MetricValue(2_000_000, "exact"),             # 1.9MiB/s
                "net_rx_bps": MetricValue(None, "unavail_kernel"),       # - (dim)
                "net_tx_bps": MetricValue(None, "unavail_kernel"),       # - (dim)
            },
            network={"source_label": "net:NS", "aggregation": "exact"},
        ),
        e_cache.key: EntityFrame(
            entity=e_cache,
            metrics={
                "ram": MetricValue(None, "unlimited"),                   # max (yellow)
                "cpu_pct": MetricValue(0.0, "exact"),                    # 0.0%
                "rf_d_per_s": MetricValue(999.9, "exact"),               # 999.9/s
                "psi_mem_full_avg10": MetricValue(15.0, "exact"),        # 15.0%
                "psi_io_some_avg10": MetricValue(0.0, "exact"),          # 0.0%
                "io_r_bps": MetricValue(0, "exact"),                     # 0B/s
                "io_w_bps": MetricValue(0, "exact"),                     # 0B/s
                "net_rx_bps": MetricValue(100_000_000, "exact"),         # 95.4MiB/s
                "net_tx_bps": MetricValue(50_000_000, "exact"),          # 47.7MiB/s
            },
            network={"source_label": "docker0", "aggregation": "netns"},
        ),
    })

    # --- Tick 2: every value changes ---
    tick2 = _make_frame(105.0, 5.0, {
        e_web.key: EntityFrame(
            entity=e_web,
            metrics={
                "ram": MetricValue(300 * 1024 * 1024, "exact"),          # 300.0MiB
                "cpu_pct": MetricValue(50.1, "exact"),                   # 50.1%
                "rf_d_per_s": MetricValue(10.0, "exact"),                # 10.0/s
                "psi_mem_full_avg10": MetricValue(0.8, "exact"),         # 0.8%
                "psi_io_some_avg10": MetricValue(None, "unavail_perm"),  # - (dim)
                "io_r_bps": MetricValue(2 * 1024 * 1024, "exact"),       # 2.0MiB/s
                "io_w_bps": MetricValue(256 * 1024, "exact"),            # 256.0KiB/s
                "net_rx_bps": MetricValue(60_000_000, "exact"),          # 57.2MiB/s
                "net_tx_bps": MetricValue(12_000_000, "exact"),          # 11.4MiB/s
            },
            network={"source_label": "eth0"},
        ),
        e_db.key: EntityFrame(
            entity=e_db,
            metrics={
                "ram": MetricValue(1 * 1024 * 1024 * 1024, "exact"),    # 1.0GiB (same)
                "cpu_pct": MetricValue(15.3, "exact"),                   # 15.3%
                "rf_d_per_s": MetricValue(5.5, "exact"),                 # 5.5/s
                "psi_mem_full_avg10": MetricValue(4.1, "exact"),         # 4.1%
                "psi_io_some_avg10": MetricValue(0.9, "exact"),          # 0.9%
                "io_r_bps": MetricValue(600_000, "exact"),               # 585.9KiB/s
                "io_w_bps": MetricValue(1_500_000, "exact"),             # 1.4MiB/s
                "net_rx_bps": MetricValue(None, "unavail_kernel"),       # - (dim)
                "net_tx_bps": MetricValue(None, "unavail_kernel"),       # - (dim)
            },
            network={"source_label": "net:NS", "aggregation": "exact"},
        ),
        e_cache.key: EntityFrame(
            entity=e_cache,
            metrics={
                "ram": MetricValue(100 * 1024 * 1024, "exact"),          # 100.0MiB
                "cpu_pct": MetricValue(1.2, "exact"),                    # 1.2%
                "rf_d_per_s": MetricValue(1200.0, "exact"),              # 1200.0/s
                "psi_mem_full_avg10": MetricValue(20.5, "exact"),        # 20.5%
                "psi_io_some_avg10": MetricValue(0.5, "exact"),          # 0.5%
                "io_r_bps": MetricValue(100, "exact"),                   # 100B/s
                "io_w_bps": MetricValue(200, "exact"),                   # 200B/s
                "net_rx_bps": MetricValue(200_000_000, "exact"),         # 190.7MiB/s
                "net_tx_bps": MetricValue(80_000_000, "exact"),          # 76.3MiB/s
            },
            network={"source_label": "docker0", "aggregation": "netns"},
        ),
    })

    # --- Tick 3: more changes, exercise edge cases ---
    tick3 = _make_frame(110.0, 5.0, {
        e_web.key: EntityFrame(
            entity=e_web,
            metrics={
                "ram": MetricValue(256 * 1024 * 1024, "exact"),          # 256.0MiB (back to tick1)
                "cpu_pct": MetricValue(47.8, "exact"),                   # 47.8%
                "rf_d_per_s": MetricValue(None, "unavail_kernel"),       # - (dim, changed from avail)
                "psi_mem_full_avg10": MetricValue(0.0, "exact"),         # 0.0%
                "psi_io_some_avg10": MetricValue(0.2, "exact"),          # 0.2%
                "io_r_bps": MetricValue(1 * 1024 * 1024, "exact"),       # 1.0MiB/s
                "io_w_bps": MetricValue(0, "exact"),                     # 0B/s
                "net_rx_bps": MetricValue(10_000_000, "exact"),          # 9.5MiB/s
                "net_tx_bps": MetricValue(5_000_000, "exact"),           # 4.8MiB/s
            },
            network={"source_label": "eth0"},
        ),
        e_db.key: EntityFrame(
            entity=e_db,
            metrics={
                "ram": MetricValue(None, "unlimited"),                   # max
                "cpu_pct": MetricValue(11.7, "exact"),                   # 11.7%
                "rf_d_per_s": MetricValue(3.3, "exact"),                 # 3.3/s
                "psi_mem_full_avg10": MetricValue(2.5, "exact"),         # 2.5%
                "psi_io_some_avg10": MetricValue(0.0, "exact"),          # 0.0%
                "io_r_bps": MetricValue(0, "exact"),                     # 0B/s
                "io_w_bps": MetricValue(0, "exact"),                     # 0B/s
                "net_rx_bps": MetricValue(10_000, "exact"),              # 9.8KiB/s
                "net_tx_bps": MetricValue(5_000, "exact"),               # 4.9KiB/s
            },
            network={"source_label": "net:NS", "aggregation": "exact"},
        ),
        e_cache.key: EntityFrame(
            entity=e_cache,
            metrics={
                "ram": MetricValue(50 * 1024 * 1024, "exact"),           # 50.0MiB
                "cpu_pct": MetricValue(0.5, "exact"),                    # 0.5%
                "rf_d_per_s": MetricValue(500.0, "exact"),               # 500.0/s
                "psi_mem_full_avg10": MetricValue(10.0, "exact"),        # 10.0%
                "psi_io_some_avg10": MetricValue(0.1, "exact"),          # 0.1%
                "io_r_bps": MetricValue(50_000_000, "exact"),            # 47.7MiB/s
                "io_w_bps": MetricValue(10_000_000, "exact"),            # 9.5MiB/s
                "net_rx_bps": MetricValue(1_000_000_000, "exact"),       # 953.7MiB/s
                "net_tx_bps": MetricValue(500_000_000, "exact"),         # 476.8MiB/s
            },
            network={"source_label": "docker0", "aggregation": "netns"},
        ),
    })

    return [tick1, tick2, tick3]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _render(frame: Frame) -> RenderedRows:
    return render_container_table(
        frame,
        _CONFIG,
        width=WIDTH,
        profile=PROFILE,
        sort_by=SORT,
        filter_text=FILTER,
        selected_key=SELECTED,
    )


def _columns() -> tuple[str, ...]:
    return resolve_profile(_CONFIG, width=WIDTH, profile=PROFILE).columns


def _cell_grid(
    frame: Frame,
    columns: tuple[str, ...],
) -> dict[str, dict[str, str]]:
    """Build {row_key: {column_name: plain_text}} using the production formatter."""
    rows = _render(frame)
    grid: dict[str, dict[str, str]] = {}
    for row_key in rows.row_keys:
        ef = frame.entities[row_key]
        grid[row_key] = {
            col: format_metric_value(col, ef).plain
            for col in columns
        }
    return grid


def _write_frames(path: Path, frames: list[Frame]) -> None:
    with RecordWriter(path, config=_CONFIG, started_at=frames[0].ts) as writer:
        for f in frames:
            writer.write_frame(f)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_fidelity_frame_sequence_round_trips(tmp_path: Path) -> None:
    """Multi-tick frames survive RecordWriter→RecordReader round-trip."""
    frames = _build_fidelity_frames()
    path = tmp_path / "fidelity.jsonl"
    _write_frames(path, frames)
    replayed = list(RecordReader(path))
    assert len(replayed) == len(frames)
    for i, (orig, rep) in enumerate(zip(frames, replayed)):
        assert orig.ts == rep.ts, f"Tick {i}: ts differs"
        assert set(orig.entities) == set(rep.entities), f"Tick {i}: entity keys differ"
        assert orig.host.keys() == rep.host.keys(), f"Tick {i}: host keys differ"


def test_fidelity_replay_driver_round_trips(tmp_path: Path) -> None:
    """ReplayDriver.from_path loads the same frames."""
    frames = _build_fidelity_frames()
    path = tmp_path / "fidelity.jsonl"
    _write_frames(path, frames)
    driver = ReplayDriver.from_path(path, config=_CONFIG)
    assert len(driver.frames) == len(frames)
    assert driver.total == len(frames)
    for i, (orig, rep) in enumerate(zip(frames, driver.frames)):
        assert orig.ts == rep.ts, f"Tick {i}: ts differs"


def test_fidelity_round_trip_compressed_zst(tmp_path: Path) -> None:
    """Same round-trip works for compressed JSONL when zstandard is available."""
    try:
        import zstandard  # noqa: F401
    except ImportError:
        pytest.skip("zstandard not installed")
        return

    frames = _build_fidelity_frames()
    path = tmp_path / "fidelity.jsonl.zst"
    _write_frames(path, frames)
    replayed = list(RecordReader(path))
    assert len(replayed) == len(frames)
    for i, (orig, rep) in enumerate(zip(frames, replayed)):
        assert orig.ts == rep.ts, f"Tick {i}: ts differs"


def test_fidelity_all_ticks_produce_identical_row_keys() -> None:
    """Original frames produce deterministic row-key tuples per tick."""
    frames = _build_fidelity_frames()
    for i, frame in enumerate(frames):
        rendered = _render(frame)
        assert len(rendered.row_keys) == 3, f"Tick {i}: expected 3 rows, got {len(rendered.row_keys)}"
        # Sorted by name (cache, db, web)
        assert rendered.row_keys == ("cache.scope", "db.scope", "web.scope"), (
            f"Tick {i}: expected deterministic sort order, got {rendered.row_keys}"
        )


def test_fidelity_all_ticks_produce_identical_columns() -> None:
    """Original and replayed frames use identical resolved columns."""
    columns = _columns()
    expected = (
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
    assert columns == expected, f"Expected triage columns at WIDTH=140, got {columns}"


def test_fidelity_cell_text_matches_after_record_replay(tmp_path: Path) -> None:
    """Every cell's plain text is identical for original vs replayed frames."""
    frames = _build_fidelity_frames()
    path = tmp_path / "fidelity.jsonl"
    _write_frames(path, frames)
    replayed = list(RecordReader(path))
    columns = _columns()

    for i, (orig, rep) in enumerate(zip(frames, replayed)):
        orig_grid = _cell_grid(orig, columns)
        rep_grid = _cell_grid(rep, columns)
        assert orig_grid == rep_grid, f"Tick {i}: cell grid mismatch"


def test_fidelity_driver_preserves_frame_metadata(tmp_path: Path) -> None:
    """ReplayDriver preserves ts, entities, host keys vs Reader-loaded frames."""
    frames = _build_fidelity_frames()
    path = tmp_path / "fidelity.jsonl"
    _write_frames(path, frames)
    reader_frames = list(RecordReader(path))
    driver = ReplayDriver.from_path(path, config=_CONFIG)
    driver_frames = list(driver.frames)

    assert len(reader_frames) == len(driver_frames)
    for i, (rf, df) in enumerate(zip(reader_frames, driver_frames)):
        assert rf.ts == df.ts, f"Tick {i}: ts differs"
        assert set(rf.entities) == set(df.entities), f"Tick {i}: entity keys differ"
        assert rf.host.keys() == df.host.keys(), f"Tick {i}: host keys differ"


def test_fidelity_rendered_containers_use_only_docker_entities() -> None:
    """render_container_table includes only entities with Docker metadata."""
    frames = _build_fidelity_frames()
    frame = frames[0]
    non_docker_key = "system.slice"
    frame.entities[non_docker_key] = EntityFrame(
        entity=Entity(key=non_docker_key, kind="slice", parent=""),
        metrics={
            "ram": MetricValue(500, "exact"),
            "cpu_pct": MetricValue(5.0, "exact"),
        },
    )
    rendered = _render(frame)
    assert non_docker_key not in rendered.row_keys, (
        "Non-Docker entity should be excluded from container view"
    )
    assert rendered.row_keys == ("cache.scope", "db.scope", "web.scope")


def test_fidelity_compressed_cell_text_matches(tmp_path: Path) -> None:
    """Cell text from compressed round-trip matches original."""
    try:
        import zstandard  # noqa: F401
    except ImportError:
        pytest.skip("zstandard not installed")
        return

    frames = _build_fidelity_frames()
    path = tmp_path / "fidelity.jsonl.zst"
    _write_frames(path, frames)
    replayed = list(RecordReader(path))
    columns = _columns()

    for i, (orig, rep) in enumerate(zip(frames, replayed)):
        orig_grid = _cell_grid(orig, columns)
        rep_grid = _cell_grid(rep, columns)
        assert orig_grid == rep_grid, f"Tick {i}: cell grid mismatch via compressed round-trip"


def test_fidelity_specific_cell_values_are_correct() -> None:
    """Spot-check specific cell formatting values."""
    frames = _build_fidelity_frames()
    columns = _columns()

    # Tick 1
    t1 = frames[0]
    t1_grid = _cell_grid(t1, columns)

    # web: ram=256.0MiB, cpu_pct=45.2%, rf_d_per_s=12.5/s, psi_mem_full_avg10=0.5%
    assert t1_grid["web.scope"]["ram"] == "256.0MiB"
    assert t1_grid["web.scope"]["cpu_pct"] == "45.2%"
    assert t1_grid["web.scope"]["rf_d_per_s"] == "12.5/s"
    assert t1_grid["web.scope"]["psi_mem_full_avg10"] == "0.5%"
    assert t1_grid["web.scope"]["io_r_bps"] == "1.0MiB/s"
    assert t1_grid["web.scope"]["io_w_bps"] == "512.0KiB/s"
    assert t1_grid["web.scope"]["psi_io_some_avg10"] == "-"  # unavail_perm → dim "-"
    assert t1_grid["web.scope"]["net_source"] == "eth0"

    # db: ram=1.0GiB, cpu_pct=12.0%, rf_d_per_s=- (unavail_kernel)
    assert t1_grid["db.scope"]["ram"] == "1.0GiB"
    assert t1_grid["db.scope"]["cpu_pct"] == "12.0%"
    assert t1_grid["db.scope"]["rf_d_per_s"] == "-"  # unavail_kernel → dim "-"
    assert t1_grid["db.scope"]["net_source"] == "net:NS"

    # cache: ram=max (unlimited), cpu_pct=0.0%, rf_d_per_s=999.9/s
    assert t1_grid["cache.scope"]["ram"] == "max"  # unlimited → "max" yellow
    assert t1_grid["cache.scope"]["cpu_pct"] == "0.0%"
    assert t1_grid["cache.scope"]["rf_d_per_s"] == "999.9/s"
    assert t1_grid["cache.scope"]["net_source"] == "docker0:netns"  # aggregation != exact/none

    # Tick 2: verify values changed
    t2 = frames[1]
    t2_grid = _cell_grid(t2, columns)
    assert t2_grid["web.scope"]["cpu_pct"] == "50.1%"
    assert t2_grid["web.scope"]["ram"] == "300.0MiB"
    assert t2_grid["db.scope"]["cpu_pct"] == "15.3%"
    assert t2_grid["cache.scope"]["ram"] == "100.0MiB"
    assert t2_grid["cache.scope"]["cpu_pct"] == "1.2%"

    # Tick 3: some values change
    t3 = frames[2]
    t3_grid = _cell_grid(t3, columns)
    assert t3_grid["web.scope"]["ram"] == "256.0MiB"
    assert t3_grid["web.scope"]["rf_d_per_s"] == "-"  # changed to unavail_kernel
    assert t3_grid["web.scope"]["psi_io_some_avg10"] == "0.2%"  # changed from unavail to actual
    assert t3_grid["db.scope"]["ram"] == "max"  # changed from 1.0GiB to unlimited
    assert t3_grid["db.scope"]["rf_d_per_s"] == "3.3/s"  # changed from unavail to actual
    assert t3_grid["cache.scope"]["io_r_bps"] == "47.7MiB/s"
    assert t3_grid["cache.scope"]["io_w_bps"] == "9.5MiB/s"


def test_fidelity_record_replay_row_key_identity(tmp_path: Path) -> None:
    """Row keys from rendered container table are identical after record and replay for every tick."""
    frames = _build_fidelity_frames()
    path = tmp_path / "fidelity.jsonl"
    _write_frames(path, frames)
    replayed = list(RecordReader(path))

    for i, (orig, rep) in enumerate(zip(frames, replayed)):
        orig_rows = _render(orig)
        rep_rows = _render(rep)
        assert orig_rows.row_keys == rep_rows.row_keys, (
            f"Tick {i}: row keys differ: {orig_rows.row_keys} vs {rep_rows.row_keys}"
        )


def test_fidelity_column_identities_are_constant() -> None:
    """Column identities from resolve_profile are deterministic for fixed inputs."""
    from groop.ui.table import header_label

    cols1 = _columns()
    cols2 = resolve_profile(_CONFIG, width=WIDTH, profile=PROFILE).columns
    assert cols1 == cols2
    assert all(isinstance(c, str) for c in cols1)
    # Column labels include branch-policy suffixes
    expected_headers = (
        "NAME",
        "PRESSURE",
        "RAM[subtree]",
        "CPU%[local]",
        "PSI_MEM[local]",
        "PSI_IO[local]",
        "RF_DEV/S[subtree]",
        "IO_R[subtree]",
        "IO_W[subtree]",
        "NET_RX[agg]",
        "NET_TX[agg]",
        "NET_SRC",
    )
    for col, exp in zip(cols1, expected_headers):
        label = header_label(col)
        assert label == exp, f"Column {col!r}: expected header {exp!r}, got {label!r}"
