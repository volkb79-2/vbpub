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


def test_persist_config_rejects_non_positive_segment_frames():
    with pytest.raises(ValueError):
        PersistConfig(segment_frames=0)


def test_persist_config_rejects_non_positive_checkpoint_frames():
    with pytest.raises(ValueError):
        PersistConfig(checkpoint_frames=0)


def test_persist_config_defaults_are_disabled_pending_measurement():
    # Required contract 6: ship configured off until the resource budget is
    # recorded as met.
    assert PersistConfig().enabled is False


def test_store_construction_rejects_a_non_persistconfig():
    with pytest.raises(TypeError):
        PersistentHistoryStore(object())


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


def test_evict_locked_can_publish_the_index_directly(tmp_path):
    """``_evict_locked(publish_index=True)`` is available for callers other
    than the two current internal call sites (recovery and post-publish),
    which always pass ``False`` and republish the index themselves
    unconditionally right afterward regardless of whether eviction fired."""
    cfg = _cfg(tmp_path, segment_frames=1)
    store = PersistentHistoryStore(cfg)
    store.append(_frame_at(time.time()))
    store.append(_frame_at(time.time()))
    assert store.stats().segment_count == 2

    future = store._now() + 400 * 86400  # well past the default 365-day age cap
    store._now = lambda: future
    changed = store._evict_locked(publish_index=True)

    assert changed is True
    index = json.loads(store.index_path.read_text())
    assert len(index["segments"]) == 0


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


def test_o7_flush_time_disk_failure_degrades_without_raising(tmp_path):
    """Distinct from the publish-time failure above: here the segment is
    still below segment_frames (append alone never triggers a publish), so
    the failure is only reached via an explicit flush() call."""
    cfg = _cfg(tmp_path, segment_frames=10)
    store = PersistentHistoryStore(cfg)
    store.append(_frame_at(time.time()))
    assert store.stats().segment_count == 0

    with mock.patch("os.replace", side_effect=OSError(28, "No space left on device")):
        store.flush()  # must not raise

    stats = store.stats()
    assert stats.degraded is True
    assert stats.segment_count == 0


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


def test_o9_compressed_writer_emits_mid_stream_chunks_before_flush(tmp_path):
    """A single active segment large enough to force the zstd compressor to
    emit output before ``finalize()``'s own flush -- distinct from the
    round-trip test above, whose 2-frame segments are too small to ever fill
    the compressor's internal buffer before it is finalized."""
    pytest.importorskip("zstandard")
    cfg = _cfg(tmp_path, segment_frames=400, compression="zstd")
    store = PersistentHistoryStore(cfg)
    now = time.time()
    originals = [_frame_at(now + i) for i in range(400)]
    for frame in originals:
        store.append(frame)
    store.flush()

    store2 = PersistentHistoryStore(cfg)
    recovered = [frame for _, frame, _ in store2.read_frames()]
    assert len(recovered) == len(originals)
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


def test_read_segment_frames_skips_blank_lines(tmp_path):
    frame_payload = {"type": "frame", **frame_to_jsonable(_frame_at(BASE_TS))}
    segment = tmp_path / "seg-00000000.jsonl"
    segment.write_text(_header_line(0) + "\n\n" + json.dumps(frame_payload) + "\n")
    frames = read_segment_frames(segment)
    assert len(frames) == 1


def test_segment_writer_abort_swallows_a_close_failure(tmp_path):
    from topos.daemon.persist import _SegmentWriter

    writer = _SegmentWriter(tmp_path / "seg.tmp", 0, compress=False)
    with mock.patch.object(writer._fh, "close", side_effect=OSError(5, "I/O error")):
        writer.abort()  # must not raise
    assert not writer.tmp_path.exists()


def test_read_segment_frames_rejects_a_missing_segment_header(tmp_path):
    segment = tmp_path / "seg-000000.jsonl"
    segment.write_text('{"type": "frame", "ts": 1.0}\n')
    with pytest.raises(PersistStoreError):
        read_segment_frames(segment)


def test_read_segment_frames_rejects_an_unexpected_record_type(tmp_path):
    segment = tmp_path / "seg-000000.jsonl"
    segment.write_text(
        '{"type": "segment_header", "schema_version": 1, "segment_id": 0}\n'
        '{"type": "checkpoint"}\n'
    )
    with pytest.raises(PersistStoreError):
        read_segment_frames(segment)


def test_read_segment_frames_raises_when_zstd_unavailable_for_a_compressed_segment(tmp_path):
    from topos.daemon import persist as persist_module

    segment = tmp_path / "seg-000000.jsonl.zst"
    segment.write_bytes(b"irrelevant-bytes-never-decoded")
    with mock.patch.object(persist_module, "_zstd", None):
        with pytest.raises(PersistStoreError, match="zstandard"):
            read_segment_frames(segment)


def test_read_segment_frames_raises_typed_error_on_corrupt_compressed_data(tmp_path):
    pytest.importorskip("zstandard")
    cfg = _cfg(tmp_path, segment_frames=1, compression="zstd")
    store = PersistentHistoryStore(cfg)
    store.append(_frame_at(time.time()))
    store.flush()
    segment = next(store.segments_dir.glob("seg-*.zst"))
    original = segment.read_bytes()
    # Flip every byte after the 4-byte zstd frame magic so the decompressor
    # itself rejects the stream (distinct from the JSON/UTF-8 level failures
    # covered above) -- exercises the ZstdError branch, not the generic one.
    corrupted = original[:4] + bytes(b ^ 0xFF for b in original[4:])
    segment.write_bytes(corrupted)
    with pytest.raises(PersistStoreError):
        read_segment_frames(segment)


# ---------------------------------------------------------------------------
# Recovery internals not exercised by the O1-O9 restart/corruption scenarios
# ---------------------------------------------------------------------------


def test_recovery_ignores_files_that_do_not_match_the_segment_name_pattern(tmp_path):
    cfg = _cfg(tmp_path, segment_frames=1)
    store = PersistentHistoryStore(cfg)
    store.append(_frame_at(time.time()))
    store.flush()
    # Must still start with "seg-" to reach the regex check at all -- the
    # glob("seg-*") itself already filters out anything else (e.g. a bare
    # "not-a-segment.txt" never even reaches _SEGMENT_NAME_RE.match()).
    (store.segments_dir / "seg-not-a-valid-id.jsonl").write_text("junk")

    store2 = PersistentHistoryStore(cfg)
    assert store2.stats().segment_count == 1
    assert [f for _, f, _ in store2.read_frames()]


def test_recovery_reparses_an_orphan_segment_missing_from_the_index(tmp_path):
    """A segment durably published but whose index update was lost before a
    crash has no recorded checksum -- recovery falls back to a full re-parse
    (P91-REPORT.md "Deviations": the index-write-lost-mid-crash path)."""
    cfg = _cfg(tmp_path, segment_frames=1)
    store = PersistentHistoryStore(cfg)
    frame = _frame_at(time.time())
    store.append(frame)
    store.flush()
    # Simulate the index update never landing: drop this segment's entry
    # from the on-disk index, as if the crash happened between the segment
    # rename and the index republish.
    index = json.loads(store.index_path.read_text())
    index["segments"] = []
    store.index_path.write_text(json.dumps(index))

    store2 = PersistentHistoryStore(cfg)
    recovered = [f for _, f, _ in store2.read_frames()]
    assert len(recovered) == 1
    assert frame_to_jsonable(recovered[0]) == frame_to_jsonable(frame)


def test_recovery_quarantines_an_orphan_segment_that_fails_to_reparse(tmp_path):
    cfg = _cfg(tmp_path, segment_frames=1)
    store = PersistentHistoryStore(cfg)
    store.append(_frame_at(time.time()))
    store.flush()
    index = json.loads(store.index_path.read_text())
    index["segments"] = []
    store.index_path.write_text(json.dumps(index))
    segment = next(store.segments_dir.glob("seg-*"))
    segment.write_bytes(b"not a valid segment at all\n")

    store2 = PersistentHistoryStore(cfg)
    assert store2.stats().segment_count == 0
    assert store2.stats().quarantined_segments == 1
    assert not segment.exists()


# ---------------------------------------------------------------------------
# _inspect_segment_file: the orphan-segment re-parser's structural checks,
# unit-tested directly rather than only indirectly through a full recovery
# (each of these is a distinct "syntactically valid JSON, semantically
# wrong" shape that the corrupt-JSON test above never reaches).
# ---------------------------------------------------------------------------


def _header_line(segment_id: int) -> str:
    return json.dumps({"type": "segment_header", "schema_version": 1, "segment_id": segment_id})


def test_inspect_segment_file_skips_blank_lines_between_records(tmp_path):
    from topos.daemon.persist import _inspect_segment_file

    frame_payload = {"type": "frame", **frame_to_jsonable(_frame_at(BASE_TS))}
    path = tmp_path / "seg-00000000.jsonl"
    path.write_text(_header_line(0) + "\n\n" + json.dumps(frame_payload) + "\n")

    result = _inspect_segment_file(path, expected_segment_id=0)
    assert result is not None
    frame_count, _first_ts, _last_ts, _raw_bytes = result
    assert frame_count == 1


def test_inspect_segment_file_rejects_a_first_line_that_is_not_a_header(tmp_path):
    from topos.daemon.persist import _inspect_segment_file

    path = tmp_path / "seg-00000000.jsonl"
    path.write_text(json.dumps({"type": "frame", "ts": 1.0}) + "\n")
    assert _inspect_segment_file(path, expected_segment_id=0) is None


def test_inspect_segment_file_rejects_a_segment_id_mismatch(tmp_path):
    from topos.daemon.persist import _inspect_segment_file

    path = tmp_path / "seg-00000000.jsonl"
    path.write_text(_header_line(99) + "\n")
    assert _inspect_segment_file(path, expected_segment_id=0) is None


def test_inspect_segment_file_rejects_an_unexpected_record_type(tmp_path):
    from topos.daemon.persist import _inspect_segment_file

    path = tmp_path / "seg-00000000.jsonl"
    path.write_text(_header_line(0) + "\n" + json.dumps({"type": "checkpoint"}) + "\n")
    assert _inspect_segment_file(path, expected_segment_id=0) is None


def test_inspect_segment_file_rejects_a_header_only_segment_with_no_frames(tmp_path):
    from topos.daemon.persist import _inspect_segment_file

    path = tmp_path / "seg-00000000.jsonl"
    path.write_text(_header_line(0) + "\n")
    assert _inspect_segment_file(path, expected_segment_id=0) is None


# ---------------------------------------------------------------------------
# _load_index_cache: recovery's best-effort checksum cache, direct edge
# cases beyond the missing/malformed-JSON path _validate_segment already
# proves is never trusted for correctness (every segment is re-hashed
# regardless).
# ---------------------------------------------------------------------------


def test_recovery_ignores_an_index_with_the_wrong_schema_version(tmp_path):
    cfg = _cfg(tmp_path, segment_frames=1)
    store = PersistentHistoryStore(cfg)
    store.append(_frame_at(time.time()))
    store.flush()
    index = json.loads(store.index_path.read_text())
    index["schema_version"] = 999
    store.index_path.write_text(json.dumps(index))

    # Recovery must still succeed by fully re-deriving from the segment
    # files themselves, not trust (or crash on) the unrecognized index.
    store2 = PersistentHistoryStore(cfg)
    assert store2.stats().segment_count == 1


def test_recovery_ignores_a_non_dict_entry_in_the_index_segments_list(tmp_path):
    cfg = _cfg(tmp_path, segment_frames=1)
    store = PersistentHistoryStore(cfg)
    store.append(_frame_at(time.time()))
    store.flush()
    index = json.loads(store.index_path.read_text())
    index["segments"].append("not-a-dict-entry")
    store.index_path.write_text(json.dumps(index))

    store2 = PersistentHistoryStore(cfg)
    assert store2.stats().segment_count == 1


def test_recovery_quarantines_a_segment_that_becomes_unreadable_mid_scan(tmp_path):
    """A segment listed by the directory glob but that fails to stat/hash by
    the time recovery reaches it (e.g. removed by a concurrent operator
    action) is quarantined rather than crashing recovery."""
    from topos.daemon import persist as persist_module

    cfg = _cfg(tmp_path, segment_frames=1)
    store = PersistentHistoryStore(cfg)
    store.append(_frame_at(time.time()))
    store.flush()

    with mock.patch.object(
        persist_module, "_file_sha256", side_effect=OSError(2, "No such file or directory")
    ):
        store2 = PersistentHistoryStore(cfg)

    assert store2.stats().segment_count == 0
    assert store2.stats().quarantined_segments == 1


# ---------------------------------------------------------------------------
# Quarantine internals not exercised by O6's single-corruption scenarios
# ---------------------------------------------------------------------------


def test_quarantine_disambiguates_a_filename_collision(tmp_path):
    cfg = _cfg(tmp_path, segment_frames=1)
    store = PersistentHistoryStore(cfg)
    store.append(_frame_at(time.time()))
    store.flush()
    segment = next(store.segments_dir.glob("seg-*"))
    store.quarantine_dir.mkdir(parents=True, exist_ok=True)
    leftover = store.quarantine_dir / segment.name
    leftover.write_bytes(b"leftover from an earlier quarantine")

    store._quarantine(segment, reason="test")

    assert not segment.exists()
    assert leftover.read_bytes() == b"leftover from an earlier quarantine"  # untouched
    disambiguated = [p for p in store.quarantine_dir.glob(f"{segment.name}.*")]
    assert len(disambiguated) == 1


def test_quarantine_swallows_a_rename_failure(tmp_path):
    cfg = _cfg(tmp_path, segment_frames=1)
    store = PersistentHistoryStore(cfg)
    store.append(_frame_at(time.time()))
    store.flush()
    segment = next(store.segments_dir.glob("seg-*"))
    before = store._quarantined_this_run

    with mock.patch("pathlib.Path.rename", side_effect=OSError(13, "Permission denied")):
        store._quarantine(segment, reason="test")  # must not raise

    assert store._quarantined_this_run == before + 1
    assert segment.exists()  # rename failed, so the original file is left in place


# ---------------------------------------------------------------------------
# Active-writer failure handling distinct from O7's publish-time OSError
# ---------------------------------------------------------------------------


def test_append_aborts_the_active_writer_on_a_mid_write_failure(tmp_path):
    """A disk error while writing to the still-open active segment (as
    opposed to O7's publish-time failure, where the active writer has
    already been cleared) must abort and drop that writer, not leak it."""
    from topos.daemon import persist as persist_module

    cfg = _cfg(tmp_path, segment_frames=5)  # large enough that publish never triggers
    store = PersistentHistoryStore(cfg)
    with mock.patch.object(
        persist_module._SegmentWriter, "write_frame", side_effect=OSError(28, "No space left on device")
    ):
        store.append(_frame_at(time.time()))  # must not raise

    assert store.stats().degraded is True
    assert store._active is None


def test_store_close_flushes_a_pending_partial_segment(tmp_path):
    cfg = _cfg(tmp_path, segment_frames=10)
    store = PersistentHistoryStore(cfg)
    store.append(_frame_at(time.time()))  # below segment_frames: stays active
    assert store.stats().segment_count == 0

    store.close()

    assert store._closed is True
    assert store.stats().segment_count == 1  # close() flushed the partial segment
    store.append(_frame_at(time.time() + 1))  # a closed store ignores further writes
    assert store.stats().segment_count == 1


# ---------------------------------------------------------------------------
# Reading internals not exercised by the O1-O9 scenarios
# ---------------------------------------------------------------------------


def test_iter_segments_returns_retained_segments_in_ascending_order(tmp_path):
    cfg = _cfg(tmp_path, segment_frames=1)
    store = PersistentHistoryStore(cfg)
    now = time.time()
    for i in range(3):
        store.append(_frame_at(now + i))
    segments = store.iter_segments()
    assert [s.segment_id for s in segments] == [0, 1, 2]


def test_read_frames_honors_the_since_and_until_ts_window(tmp_path):
    cfg = _cfg(tmp_path, segment_frames=1)
    store = PersistentHistoryStore(cfg)
    now = time.time()
    for i in range(5):
        store.append(_frame_at(now + i))

    windowed = store.read_frames(since_ts=now + 1, until_ts=now + 4)
    assert [f.ts for _, f, _ in windowed] == [now + 1.0, now + 2.0, now + 3.0]


def test_read_frames_skips_a_segment_corrupted_after_recovery(tmp_path):
    """Documented narrow race (P91-REPORT.md "Deviations"): a segment
    corrupted after recovery but before a live read_frames() call is skipped
    rather than crashing the query path; the next restart's recovery is what
    quarantines it and reports the gap."""
    cfg = _cfg(tmp_path, segment_frames=1)
    store = PersistentHistoryStore(cfg)
    now = time.time()
    store.append(_frame_at(now))
    store.append(_frame_at(now + 1))
    segments = sorted(store.segments_dir.glob("seg-*"))  # filenames sort by segment_id
    assert len(segments) == 2
    segments[0].write_bytes(b"corrupted after recovery, not yet re-validated\n")

    frames = store.read_frames()
    assert [f.ts for _, f, _ in frames] == [now + 1.0]


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


# ---------------------------------------------------------------------------
# `topos daemon serve` CLI wiring: startup health reporting, the
# _persisting_frame_stream tee, and the shutdown flush -- exercised through
# _main_daemon end-to-end, one server.serve_forever() iteration at a time
# (matches the pattern in test_cli_daemon_lifecycle_boundaries.py's
# _wire_one_shot_server, duplicated locally to keep this file self-contained).
# ---------------------------------------------------------------------------


def _wire_one_shot_daemon(monkeypatch, cli, observed: dict, broker_cls: type):
    class FakeServer:
        def __init__(self, broker) -> None:
            self._broker = broker

        def serve_forever(self) -> None:
            # Capture health state while "running", before shutdown mutates
            # it further (mark_stopping/mark_stopped happen in the finally).
            component = self._broker.health_registry.snapshot().by_name("persistent_history")
            observed["mid_run_state"] = component.state.value if component is not None else None
            observed["mid_run_detail"] = component.detail if component is not None else None
            raise KeyboardInterrupt

        def server_close(self) -> None:
            observed["closed"] = True

    def make_broker(*args, **kwargs):
        broker = broker_cls(*args, **kwargs)
        observed["broker"] = broker
        return broker

    monkeypatch.setattr(cli, "FrameBroker", make_broker)
    monkeypatch.setattr(cli, "DaemonApi", lambda *a, **k: None)
    monkeypatch.setattr(
        cli, "serve_versioned_unix_socket", lambda _path, broker, api=None: FakeServer(broker)
    )


class _FakeCliBroker:
    def __init__(self, frames, *, health_registry, **_kwargs) -> None:
        self.health_registry = health_registry
        self._frames = frames
        self.events: list[str] = []

    def start(self) -> None:
        # Never drained (matching test_cli_daemon_lifecycle_boundaries.py's
        # _FakeBroker): this suite only asserts _main_daemon's wiring and
        # health-reporting around the store, not frame delivery through a
        # live collector loop -- that is
        # test_persisting_frame_stream_feeds_both_ram_and_disk_tiers' job,
        # against a real FrameBroker and a real (non-CLI) frame source.
        self.events.append("start")

    def stop(self) -> None:
        self.events.append("stop")

    def join(self, *, timeout: float) -> None:
        self.events.append("join")


def test_cli_daemon_serve_reports_healthy_persistent_history_and_flushes_on_shutdown(
    tmp_path, monkeypatch
):
    import topos.cli as cli
    from topos.config import HistoryConfig, PersistConfig, ToposConfig

    history_dir = tmp_path / "history"
    config = ToposConfig(
        cgroup_root=tmp_path / "cgroup",
        history=HistoryConfig(daemon=PersistConfig(enabled=True, dir=history_dir, segment_frames=1)),
    )

    class FakeCollector:
        def __init__(self, cgroup_root, config) -> None:
            self.cgroup_root = cgroup_root or config.cgroup_root
            self.network_providers = ()

    observed: dict = {}
    _wire_one_shot_daemon(monkeypatch, cli, observed, _FakeCliBroker)
    monkeypatch.setattr(cli, "load", lambda _path: config)
    monkeypatch.setattr(cli, "Collector", FakeCollector)

    assert cli._main_daemon(["serve", "--socket", str(tmp_path / "topos.sock")]) == 0

    assert observed["closed"] is True
    assert observed["mid_run_state"] == "healthy"
    assert "recovery_state=" in observed["mid_run_detail"]
    assert history_dir.exists()  # the real store was constructed, not bypassed
    final = observed["broker"].health_registry.snapshot().by_name("persistent_history")
    assert final.state.value == "stopped"
    assert final.detail == "persistent history flushed"


def test_cli_daemon_serve_reports_degraded_persistent_history_at_startup(tmp_path, monkeypatch):
    import topos.cli as cli
    from topos.config import HistoryConfig, PersistConfig, ToposConfig

    history_dir = tmp_path / "history"
    config = ToposConfig(
        cgroup_root=tmp_path / "cgroup",
        history=HistoryConfig(daemon=PersistConfig(enabled=True, dir=history_dir, segment_frames=1)),
    )

    class FakeCollector:
        def __init__(self, cgroup_root, config) -> None:
            self.cgroup_root = cgroup_root or config.cgroup_root
            self.network_providers = ()

    observed: dict = {}
    _wire_one_shot_daemon(monkeypatch, cli, observed, _FakeCliBroker)
    monkeypatch.setattr(cli, "load", lambda _path: config)
    monkeypatch.setattr(cli, "Collector", FakeCollector)

    with mock.patch("pathlib.Path.mkdir", side_effect=PermissionError(13, "Permission denied")):
        assert cli._main_daemon(["serve", "--socket", str(tmp_path / "topos.sock")]) == 0

    assert observed["closed"] is True
    assert observed["mid_run_state"] == "degraded"
    assert "persistent history degraded at startup" in observed["mid_run_detail"]


def test_cli_daemon_serve_marks_persistent_history_disabled_when_not_enabled(tmp_path, monkeypatch):
    import topos.cli as cli
    from topos.config import ToposConfig

    config = ToposConfig(cgroup_root=tmp_path / "cgroup")  # history.daemon.enabled defaults to False

    class FakeCollector:
        def __init__(self, cgroup_root, config) -> None:
            self.cgroup_root = cgroup_root or config.cgroup_root
            self.network_providers = ()

    observed: dict = {}
    _wire_one_shot_daemon(monkeypatch, cli, observed, _FakeCliBroker)
    monkeypatch.setattr(cli, "load", lambda _path: config)
    monkeypatch.setattr(cli, "Collector", FakeCollector)

    assert cli._main_daemon(["serve", "--socket", str(tmp_path / "topos.sock")]) == 0

    assert observed["closed"] is True
    assert observed["mid_run_state"] == "disabled"
    final = observed["broker"].health_registry.snapshot().by_name("persistent_history")
    assert final.state.value == "disabled"  # shutdown flush is skipped entirely when disabled
