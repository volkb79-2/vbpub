"""Tests for topos.daemon.persist — P91 recoverable age-and-byte-capped
persistent daemon history.

Numbered acceptance oracles (nyxloom-trove/handoffs/topos-P91-persistent-capped-history.md):
  O1  age-cap eviction (negative: an over-age frame remains queryable)
  O2  byte-cap eviction, least-recent-eligible-first
  O3  age and byte caps enforced simultaneously
  O4  restart recovery reconstructs the store without exceeding either cap
  O5  a torn/partial write does not corrupt subsequent segments
  O6  a corrupt middle segment is quarantined with an explicit gap;
      last-good history remains queryable
  O7  disk-full/read-only degrades visibly to the RAM tier without crashing
  O8  query results report gaps/evictions truthfully
  O9  recovered frames are byte-deterministic against what was persisted
  O10 24h synthetic workload measurement — see MEASUREMENTS.md /
      nyxloom-trove/reports/P91-REPORT.md (not exercised as a per-test oracle here;
      it is a one-shot recorded measurement, not a regression gate).
"""

from __future__ import annotations

import dataclasses
import json
import time
from pathlib import Path
from unittest import mock

import pytest

from topos.config import PersistConfig
from topos.daemon.persist import PersistentHistoryStore, PersistStoreError, read_segment_frames
from topos.model import frame_to_jsonable
from topos.query import PersistentHistoryFrameSource, Query, run_query

from conftest import fixture_frame


BASE_TS = 1_800_000_000.0


def _cfg(tmp_path: Path, **overrides) -> PersistConfig:
    defaults = dict(
        enabled=True,
        dir=tmp_path,
        max_age_days=365.0,
        max_size_mb=256.0,
        segment_frames=1,
        checkpoint_frames=1,
        compression="none",
    )
    defaults.update(overrides)
    return PersistConfig(**defaults)


def _frame_at(ts: float):
    return dataclasses.replace(fixture_frame(), ts=ts)


class _Clock:
    def __init__(self, start: float) -> None:
        self.value = start

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


# ---------------------------------------------------------------------------
# PersistConfig validation
# ---------------------------------------------------------------------------


def test_persist_config_rejects_non_positive_age_cap():
    with pytest.raises(ValueError):
        PersistConfig(max_age_days=0)


def test_persist_config_rejects_non_positive_byte_cap():
    with pytest.raises(ValueError):
        PersistConfig(max_size_mb=-1)


def test_persist_config_rejects_unknown_compression():
    with pytest.raises(ValueError):
        PersistConfig(compression="lz4")


def test_persist_config_defaults_are_disabled_pending_measurement():
    # Required contract 6: ship configured off until the resource budget is
    # recorded as met.
    assert PersistConfig().enabled is False


def test_history_config_ram_default_reconciled_to_five_minutes():
    # Required contract 1: the old 4h default is replaced explicitly, not
    # silently reinterpreted (D-005: five minutes at five-second resolution).
    from topos.config import HistoryConfig

    cfg = HistoryConfig()
    assert cfg.full_resolution_seconds == 300
    assert cfg.daemon == PersistConfig()


def test_load_parses_history_daemon_section(tmp_path):
    from topos.config import load

    config_file = tmp_path / "config.toml"
    config_file.write_text(
        "[history]\n"
        "full_resolution_seconds = 300\n"
        "\n"
        "[history.daemon]\n"
        "enabled = true\n"
        'dir = "/var/lib/topos/history"\n'
        "max_age_days = 1.0\n"
        "max_size_mb = 256.0\n"
        'compression = "zstd"\n'
        "segment_frames = 360\n"
        "checkpoint_frames = 30\n"
        "fsync = true\n"
    )
    config = load(config_file)
    assert config.history.full_resolution_seconds == 300
    assert config.history.daemon.enabled is True
    assert str(config.history.daemon.dir) == "/var/lib/topos/history"
    assert config.history.daemon.max_age_days == 1.0
    assert config.history.daemon.max_size_mb == 256.0
    assert config.history.daemon.compression == "zstd"
    assert config.history.daemon.segment_frames == 360


def test_load_defaults_history_daemon_section_to_disabled(tmp_path):
    from topos.config import load

    config_file = tmp_path / "config.toml"
    config_file.write_text("[history]\n")
    config = load(config_file)
    assert config.history.daemon.enabled is False
    assert config.history.daemon == PersistConfig()


def test_load_rejects_unknown_compression_by_falling_back_to_default(tmp_path):
    from topos.config import load

    config_file = tmp_path / "config.toml"
    config_file.write_text('[history.daemon]\ncompression = "lz4"\n')
    config = load(config_file)
    assert config.history.daemon.compression == "zstd"


def test_to_primitive_and_digest_cover_the_daemon_section():
    from topos.config import ToposConfig

    a = ToposConfig()
    b = ToposConfig(history=type(a.history)(daemon=PersistConfig(enabled=True)))
    assert a.digest() != b.digest()
    assert "daemon" in a.to_primitive()["history"]


# ---------------------------------------------------------------------------
# Disabled store is fully inert
# ---------------------------------------------------------------------------


def test_disabled_store_touches_no_disk(tmp_path):
    cfg = _cfg(tmp_path, enabled=False)
    store = PersistentHistoryStore(cfg)
    store.append(_frame_at(BASE_TS))
    store.flush()
    assert not tmp_path.exists() or list(tmp_path.iterdir()) == []
    stats = store.stats()
    assert stats.enabled is False
    assert stats.recovery_state == "disabled"
    assert stats.segment_count == 0


# ---------------------------------------------------------------------------
# O1 — age-cap eviction
# ---------------------------------------------------------------------------


def test_o1_frames_older_than_age_cap_are_evicted(tmp_path):
    clock = _Clock(BASE_TS)
    cfg = _cfg(tmp_path, max_age_days=10.0 / 86400.0, segment_frames=1)  # 10s cap
    store = PersistentHistoryStore(cfg, now=clock)
    store.append(_frame_at(BASE_TS))
    store.flush()
    assert store.stats().segment_count == 1

    clock.advance(11.0)
    store.append(_frame_at(clock.value))
    store.flush()

    stats = store.stats()
    remaining_ts = _all_frame_ts(store)
    assert BASE_TS not in remaining_ts, "a frame older than the age cap must not remain queryable"
    assert clock.value in remaining_ts
    assert stats.evicted_segments >= 1


def test_o1_negative_frame_within_age_cap_is_retained(tmp_path):
    clock = _Clock(BASE_TS)
    cfg = _cfg(tmp_path, max_age_days=100.0 / 86400.0, segment_frames=1)  # 100s cap
    store = PersistentHistoryStore(cfg, now=clock)
    store.append(_frame_at(BASE_TS))
    store.flush()
    clock.advance(5.0)
    store.append(_frame_at(clock.value))
    store.flush()
    assert BASE_TS in _all_frame_ts(store)


def _all_frame_ts(store: PersistentHistoryStore) -> list:
    return [frame.ts for _, frame, _ in store.read_frames()]


# ---------------------------------------------------------------------------
# O2 — byte-cap eviction, least-recent-eligible-first
# ---------------------------------------------------------------------------


def test_o2_byte_cap_evicts_oldest_segment_first(tmp_path):
    frame = fixture_frame()
    segment_bytes = len(json.dumps(frame_to_jsonable(frame), sort_keys=True, separators=(",", ":")))
    # Budget for ~2.5 segments so eviction must happen but some survive.
    cap_mb = (segment_bytes * 2.5) / (1024 * 1024)
    cfg = _cfg(tmp_path, max_age_days=365.0, max_size_mb=cap_mb, segment_frames=1)
    store = PersistentHistoryStore(cfg)
    now = time.time()
    for i in range(6):
        store.append(_frame_at(now + i))
        store.flush()

    stats = store.stats()
    assert stats.byte_size <= stats.byte_cap_bytes
    assert stats.evicted_segments > 0

    remaining = sorted(ts - now for ts in _all_frame_ts(store))
    # The newest frames must be the ones retained — eviction removed the
    # oldest-first, never a newer frame while an older one survives.
    assert remaining == sorted(remaining)
    assert remaining[-1] == pytest.approx(5.0)
    assert 0.0 not in remaining, "the oldest frame must be evicted before newer ones"


# ---------------------------------------------------------------------------
# O3 — both caps enforced simultaneously
# ---------------------------------------------------------------------------


def test_o3_byte_cap_alone_does_not_let_an_over_age_segment_survive(tmp_path):
    clock = _Clock(BASE_TS)
    # Byte cap generous (nothing evicted on bytes), age cap tiny.
    cfg = _cfg(tmp_path, max_age_days=10.0 / 86400.0, max_size_mb=256.0, segment_frames=1)
    store = PersistentHistoryStore(cfg, now=clock)
    store.append(_frame_at(BASE_TS))
    store.flush()
    clock.advance(20.0)
    store.append(_frame_at(clock.value))
    store.flush()
    assert BASE_TS not in _all_frame_ts(store)


def test_o3_age_cap_alone_does_not_let_an_over_byte_segment_survive(tmp_path):
    frame = fixture_frame()
    segment_bytes = len(json.dumps(frame_to_jsonable(frame), sort_keys=True, separators=(",", ":")))
    cap_mb = (segment_bytes * 1.5) / (1024 * 1024)
    # Age cap generous (nothing evicted on age), byte cap tiny.
    cfg = _cfg(tmp_path, max_age_days=365.0, max_size_mb=cap_mb, segment_frames=1)
    store = PersistentHistoryStore(cfg)
    now = time.time()
    for i in range(4):
        store.append(_frame_at(now + i))
        store.flush()
    stats = store.stats()
    assert stats.byte_size <= stats.byte_cap_bytes
    assert stats.evicted_segments > 0


# ---------------------------------------------------------------------------
# O4 — restart recovery never exceeds either cap
# ---------------------------------------------------------------------------


def test_o4_restart_recovery_applies_current_caps(tmp_path):
    clock = _Clock(BASE_TS)
    cfg = _cfg(tmp_path, max_age_days=1000.0 / 86400.0, segment_frames=1)  # generous while writing
    store = PersistentHistoryStore(cfg, now=clock)
    for i in range(5):
        store.append(_frame_at(clock.value + i))
    store.flush()
    assert store.stats().segment_count == 5

    # Reopen with time advanced far past the age cap and a tighter cap —
    # recovery itself must evict, not just future appends.
    clock2 = _Clock(clock.value + 999.0)
    tight_cfg = _cfg(tmp_path, max_age_days=5.0 / 86400.0, segment_frames=1)
    store2 = PersistentHistoryStore(tight_cfg, now=clock2)
    stats2 = store2.stats()
    assert stats2.segment_count == 0
    assert (clock2.value - stats2.age_cap_seconds) > BASE_TS
    assert stats2.evicted_segments == 5


def test_o4_recovery_is_bounded_by_directory_listing_not_full_history(tmp_path):
    cfg = _cfg(tmp_path, segment_frames=1)
    store = PersistentHistoryStore(cfg)
    now = time.time()
    for i in range(8):
        store.append(_frame_at(now + i))
    store.flush()
    store2 = PersistentHistoryStore(cfg)
    # Bounded scanning: recovery only ever inspects files actually present
    # under segments_dir, never an unbounded external log.
    assert store2.stats().segment_count == len(list(store.segments_dir.glob("seg-*")))


# ---------------------------------------------------------------------------
# O5 — torn/partial write does not corrupt subsequent segments
# ---------------------------------------------------------------------------


def test_o5_torn_segment_write_is_ignored_and_neighbors_survive(tmp_path):
    cfg = _cfg(tmp_path)
    store = PersistentHistoryStore(cfg)
    now = time.time()
    store.append(_frame_at(now))
    store.flush()

    # Simulate a crash mid-write of the next segment: a temp file exists but
    # was never renamed into place.
    torn = store.segments_dir / f".seg-{1:08d}.jsonl.99999.tmp"
    torn.write_bytes(b'{"type": "segment_header"')  # deliberately truncated

    store2 = PersistentHistoryStore(cfg)
    assert not torn.exists(), "leftover torn-write temp files must be cleaned up"
    stats = store2.stats()
    assert stats.segment_count == 1
    assert stats.recovery_state == "clean"
    assert not stats.gaps


def test_o5_torn_index_write_does_not_lose_prior_segments(tmp_path):
    cfg = _cfg(tmp_path)
    store = PersistentHistoryStore(cfg)
    now = time.time()
    store.append(_frame_at(now))
    store.flush()
    good_index = store.index_path.read_text(encoding="utf-8")

    # Simulate a crash mid-write of the index file: a torn tmp exists next to
    # a still-intact prior index.json.
    (store.index_path.with_suffix(".json.tmp")).write_text('{"schema_versio', encoding="utf-8")

    store2 = PersistentHistoryStore(cfg)
    assert not store2.index_path.with_suffix(".json.tmp").exists()
    assert store2.stats().segment_count == 1
    assert store2.index_path.read_text(encoding="utf-8").startswith("{")


# ---------------------------------------------------------------------------
# O6 — corrupt middle segment: quarantined, explicit gap, neighbors queryable
# ---------------------------------------------------------------------------


def test_o6_corrupt_middle_segment_is_quarantined_with_explicit_gap(tmp_path):
    cfg = _cfg(tmp_path)
    store = PersistentHistoryStore(cfg)
    now = time.time()
    for i in range(3):
        store.append(_frame_at(now + i))
        store.flush()

    segments = sorted(store.segments_dir.glob("seg-*"))
    assert len(segments) == 3
    middle = segments[1]
    corrupted = bytearray(middle.read_bytes())
    corrupted[5:15] = b"CORRUPTBYT"  # mangles the header's declared schema key
    middle.write_bytes(bytes(corrupted))

    store2 = PersistentHistoryStore(cfg)
    stats = store2.stats()
    assert stats.segment_count == 2
    assert stats.quarantined_segments == 1
    assert len(stats.gaps) == 1
    assert stats.recovery_state == "recovered"

    # The corrupt file itself is preserved (quarantined), not deleted.
    assert any(p.name.startswith("seg-") for p in store2.quarantine_dir.iterdir())

    remaining = sorted(ts - now for ts in _all_frame_ts(store2))
    assert remaining == [0.0, 2.0]


def test_o6_query_reports_gap_and_stays_queryable_not_crashed(tmp_path):
    cfg = _cfg(tmp_path)
    store = PersistentHistoryStore(cfg)
    now = time.time()
    for i in range(3):
        store.append(_frame_at(now + i))
        store.flush()
    segments = sorted(store.segments_dir.glob("seg-*"))
    corrupted = bytearray(segments[1].read_bytes())
    corrupted[5:15] = b"CORRUPTBYT"
    segments[1].write_bytes(bytes(corrupted))

    store2 = PersistentHistoryStore(cfg)
    source = PersistentHistoryFrameSource.from_store(store2)
    query = Query.from_dict({"shape": "raw", "metrics": ["ram"]})
    result = run_query(source, query)  # must not raise
    assert result.meta["coverage"]["gap_count"] == 1
    assert result.meta["coverage"]["complete"] is False
    assert result.meta["eviction"]["occurred"] is True
    assert result.meta["sample_count"] == 2


# ---------------------------------------------------------------------------
# O7 — disk-full / read-only degrades visibly without crashing collection
# ---------------------------------------------------------------------------


def test_o7_disk_full_on_publish_degrades_without_raising(tmp_path):
    cfg = _cfg(tmp_path, segment_frames=1)
    store = PersistentHistoryStore(cfg)
    with mock.patch("os.replace", side_effect=OSError(28, "No space left on device")):
        store.append(_frame_at(time.time()))  # must not raise
    stats = store.stats()
    assert stats.degraded is True
    assert stats.degraded_reason is not None
    assert stats.write_errors == 1
    assert stats.segment_count == 0


def test_o7_disk_full_recovers_once_space_returns(tmp_path):
    cfg = _cfg(tmp_path, segment_frames=1)
    store = PersistentHistoryStore(cfg)
    now = time.time()
    with mock.patch("os.replace", side_effect=OSError(28, "No space left on device")):
        store.append(_frame_at(now))
    assert store.stats().degraded
    store._degraded_retry_after = 0.0  # bypass the cooldown for a deterministic test
    store.append(_frame_at(now + 1))
    stats = store.stats()
    assert stats.degraded is False
    assert stats.segment_count == 1


def test_o7_readonly_directory_degrades_without_raising(tmp_path):
    cfg = _cfg(tmp_path, segment_frames=1)
    with mock.patch(
        "pathlib.Path.mkdir", side_effect=PermissionError(13, "Permission denied")
    ):
        store = PersistentHistoryStore(cfg)  # construction alone must not raise
    assert store.stats().degraded is True
    store.append(_frame_at(time.time()))  # append after a degraded recovery must not raise either


# ---------------------------------------------------------------------------
# O8 — query results report gaps/evictions truthfully
# ---------------------------------------------------------------------------


def test_o8_clean_store_reports_complete_continuous_series(tmp_path):
    cfg = _cfg(tmp_path, segment_frames=2)
    store = PersistentHistoryStore(cfg)
    now = time.time()
    for i in range(4):
        store.append(_frame_at(now + i))
    store.flush()
    source = PersistentHistoryFrameSource.from_store(store)
    result = run_query(source, Query.from_dict({"shape": "raw", "metrics": ["ram"]}))
    assert result.meta["coverage"]["complete"] is True
    assert result.meta["coverage"]["gap_count"] == 0
    assert result.meta["eviction"]["occurred"] is False


def test_o8_eviction_never_reported_as_continuous(tmp_path):
    frame = fixture_frame()
    segment_bytes = len(json.dumps(frame_to_jsonable(frame), sort_keys=True, separators=(",", ":")))
    cap_mb = (segment_bytes * 1.5) / (1024 * 1024)
    cfg = _cfg(tmp_path, max_size_mb=cap_mb, segment_frames=1)
    store = PersistentHistoryStore(cfg)
    now = time.time()
    for i in range(4):
        store.append(_frame_at(now + i))
        store.flush()
    source = PersistentHistoryFrameSource.from_store(store)
    result = run_query(source, Query.from_dict({"shape": "raw", "metrics": ["ram"]}))
    assert result.meta["eviction"]["occurred"] is True


# ---------------------------------------------------------------------------
# O9 — byte-deterministic recovery
# ---------------------------------------------------------------------------


def test_o9_recovered_frames_are_byte_identical_to_originals(tmp_path):
    cfg = _cfg(tmp_path, segment_frames=3)
    store = PersistentHistoryStore(cfg)
    now = time.time()
    originals = [_frame_at(now + i) for i in range(7)]
    for frame in originals:
        store.append(frame)
    store.flush()

    store2 = PersistentHistoryStore(cfg)
    recovered = [frame for _, frame, _ in store2.read_frames()]
    assert len(recovered) == len(originals)
    for original, back in zip(originals, recovered):
        assert frame_to_jsonable(original) == frame_to_jsonable(back)


def test_o9_compressed_round_trip_is_byte_deterministic(tmp_path):
    zstd = pytest.importorskip("zstandard")
    cfg = _cfg(tmp_path, segment_frames=2, compression="zstd")
    store = PersistentHistoryStore(cfg)
    now = time.time()
    originals = [_frame_at(now + i) for i in range(5)]
    for frame in originals:
        store.append(frame)
    store.flush()
    assert any(p.suffix == ".zst" for p in store.segments_dir.glob("seg-*"))

    store2 = PersistentHistoryStore(cfg)
    recovered = [frame for _, frame, _ in store2.read_frames()]
    for original, back in zip(originals, recovered):
        assert frame_to_jsonable(original) == frame_to_jsonable(back)


# ---------------------------------------------------------------------------
# Contract 4 — daemon-owned files, explicit permissions
# ---------------------------------------------------------------------------


def test_segment_and_index_files_use_configured_permissions(tmp_path):
    cfg = _cfg(tmp_path, segment_frames=1, file_mode=0o640, dir_mode=0o750)
    store = PersistentHistoryStore(cfg)
    store.append(_frame_at(time.time()))
    store.flush()
    segment = next(store.segments_dir.glob("seg-*"))
    assert (segment.stat().st_mode & 0o777) == 0o640
    assert (store.index_path.stat().st_mode & 0o777) == 0o640
    assert (store.segments_dir.stat().st_mode & 0o777) == 0o750


# ---------------------------------------------------------------------------
# read_segment_frames raises typed errors on corrupt input
# ---------------------------------------------------------------------------


def test_read_segment_frames_raises_typed_error_on_corrupt_input(tmp_path):
    cfg = _cfg(tmp_path, segment_frames=1)
    store = PersistentHistoryStore(cfg)
    store.append(_frame_at(time.time()))
    store.flush()
    segment = next(store.segments_dir.glob("seg-*"))
    segment.write_bytes(b"not json at all\n")
    with pytest.raises(PersistStoreError):
        read_segment_frames(segment)


# ---------------------------------------------------------------------------
# Daemon lifecycle wiring: the RAM tier (FrameBroker) and the disk tier see
# the same canonical frame stream exactly once (Contract 2).
# ---------------------------------------------------------------------------


def test_persisting_frame_stream_feeds_both_ram_and_disk_tiers(tmp_path):
    from topos.cli import _persisting_frame_stream
    from topos.daemon import FrameBroker

    cfg = _cfg(tmp_path, segment_frames=2)
    store = PersistentHistoryStore(cfg)
    now = time.time()
    raw_frames = [_frame_at(now + i) for i in range(5)]

    broker = FrameBroker(_persisting_frame_stream(iter(raw_frames), store), history_size=10)
    broker.start()
    broker.join(timeout=5.0)
    store.flush()

    ram_frames = [frame for _, frame in broker.stream(limit=10).entries]
    disk_frames = [frame for _, frame, _ in store.read_frames()]
    assert [f.ts for f in ram_frames] == [f.ts for f in raw_frames]
    assert [f.ts for f in disk_frames] == [f.ts for f in raw_frames]
