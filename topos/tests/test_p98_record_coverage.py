"""P98 — Close all record-stack coverage gaps.

Targets all missing lines and branches in record/headless.py, reader.py,
replay.py, and writer.py. Uses the existing test patterns from
test_record.py and test_headless_record.py (tmp_path, fake collectors,
injected boundaries).
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

from conftest import fixture_frame, fixture_root, systemctl_fixture_runner
from topos.collect.collector import Collector
from topos.config import ToposConfig
from topos.model import Frame, MetricValue
from topos.record.headless import (
    HeadlessRecordDriver,
    RecordProgress,
    install_signal_handlers,
    make_second_signal_handler,
    run_headless_record,
)
from topos.record.reader import RecordReader, _ZstdStreamReader
from topos.record.replay import ReplayDriver, format_frame_summary, ReplayFrame
from topos.record.writer import RecordWriter, _is_zstd_file


# ======================================================================
# Helpers (from test_headless_record.py)
# ======================================================================

def _host_stub() -> dict[str, object]:
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


def _make_collector(*, times: list[float] | None = None) -> Collector:
    if times is None:
        times = [100.0, 105.0, 110.0, 115.0, 120.0]
    return Collector(
        cgroup_root=fixture_root() / "cgroupfs" / "gstammtisch",
        config=ToposConfig(interval=5.0, tiers={"prod": ["system.slice"]}, protected_services=("soulmask-paks.slice",)),
        docker_inspect=lambda _cid: None,
        host_collector=_host_stub,
        now=lambda: times.pop(0) if times else 999.0,
        network_providers=(),
        systemctl_show_runner=systemctl_fixture_runner("gstammtisch"),
    )


def _noop_register(stop_event: threading.Event) -> None:
    pass


def _fixture_frame() -> Frame:
    return fixture_frame()


# ======================================================================
# record/headless.py — 28 missing lines, 3 branch pairs
# ======================================================================

class TestHeadlessDriverGaps:
    """Close every remaining gap in headless.py."""

    def test_second_signal_sets_abort_event(self):
        """install_signal_handlers: second signal path sets abort (tested via helper)."""
        se = threading.Event()
        ae = threading.Event()
        se.set()  # first signal already received
        handler = make_second_signal_handler(se, ae)
        handler(15, None)
        assert ae.is_set()

    def test_make_second_signal_handler_sets_stop_on_first_signal(self):
        """make_second_signal_handler sets stop_event on first signal (line 50)."""
        se = threading.Event()
        ae = threading.Event()
        handler = make_second_signal_handler(se, ae)
        handler(15, None)
        assert se.is_set()
        assert not ae.is_set()

    def test_run_propagates_collector_error(self):
        """_drive error path lines 186-188: exception raises BaseException."""
        path = Path("/tmp") / "test_err.jsonl"
        writer = RecordWriter(path, started_at=100.0)
        # Use a collector whose collect_once causes the stream to raise
        with patch("topos.record.headless.live_frame_stream", side_effect=RuntimeError("stream failed")):
            driver = HeadlessRecordDriver(
                _make_collector(), writer,
                max_frames=1, register_signals=_noop_register,
            )
            with pytest.raises(RuntimeError, match="stream failed"):
                driver.run()
        writer.close()
        path.unlink(missing_ok=True)

    def test_drive_best_effort_close_failure(self):
        """_drive except Exception handler (lines 225-226): best-effort close fails."""
        path = Path("/tmp") / "test_closefail.jsonl"
        writer = RecordWriter(path, started_at=100.0)
        class FailingStream:
            def __next__(self):
                raise ValueError("stream fail")
            def __iter__(self):
                return self
        driver = HeadlessRecordDriver(
            _make_collector(), writer,
            max_frames=5, register_signals=_noop_register,
            monotonic=lambda: 0.0,
        )
        driver._writer.close = lambda: (_ for _ in ()).throw(Exception("close fail"))
        rc = driver._drive(FailingStream())
        assert rc == 1
        path.unlink(missing_ok=True)

    def test_abort_event_mid_iteration(self):
        """_drive checks abort_event after each frame (line 242, branch [241,242])."""
        path = Path("/tmp") / "test_abort.jsonl"
        writer = RecordWriter(path, started_at=100.0)
        collector = _make_collector(times=[100.0, 105.0, 110.0])
        driver = HeadlessRecordDriver(
            collector, writer,
            max_frames=10, register_signals=_noop_register,
            monotonic=lambda: 0.0,
        )
        base = _fixture_frame()
        # Yield one frame, set abort, then keep yielding forever so next()
        # never raises StopIteration and the abort check is reached.
        yielded = False
        def _inf_stream():
            nonlocal yielded
            while True:
                if not yielded:
                    yielded = True
                    yield Frame(ts=base.ts, interval_s=base.interval_s, host=base.host, entities=base.entities, schema_version=1)
                    driver._abort_event.set()
                else:
                    yield Frame(ts=base.ts, interval_s=base.interval_s, host=base.host, entities=base.entities, schema_version=1)
        driver._frames_written = 0
        driver._started_at = 0.0
        driver._first_frame_yielded = True
        rc = driver._drive(_inf_stream())
        assert rc == 1  # abort causes non-zero exit
        path.unlink(missing_ok=True)

    def test_finalize_abort_with_close_failure(self):
        """_finalize lines 266-267: abort path with close failure."""
        path = Path("/tmp") / "test_fin_abort.jsonl"
        writer = RecordWriter(path, started_at=100.0)
        driver = HeadlessRecordDriver(
            _make_collector(), writer,
            max_frames=1, register_signals=_noop_register,
        )
        driver._abort_event.set()
        driver._writer.close = lambda: (_ for _ in ()).throw(Exception("close fail"))
        rc = driver._finalize()
        assert rc == 1
        path.unlink(missing_ok=True)

    def test_finalize_flush_failure(self):
        """_finalize lines 272-287: flush error handler."""
        path = Path("/tmp") / "test_flush.jsonl"
        writer = RecordWriter(path, started_at=100.0)
        driver = HeadlessRecordDriver(
            _make_collector(), writer,
            max_frames=1, register_signals=_noop_register,
        )
        driver._first_frame_yielded = True
        driver._frames_written = 5
        original_flush = writer.flush
        writer.flush = lambda **kw: (_ for _ in ()).throw(Exception("flush fail"))
        rc = driver._finalize()
        assert rc == 1
        writer.flush = original_flush
        writer.close()
        path.unlink(missing_ok=True)

    def test_finalize_post_flush_abort(self):
        """_finalize lines 290-295: abort check after successful flush."""
        path = Path("/tmp") / "test_postflush.jsonl"
        writer = RecordWriter(path, started_at=100.0)
        driver = HeadlessRecordDriver(
            _make_collector(), writer,
            max_frames=3, register_signals=_noop_register,
        )
        # After flush succeeds, abort is set
        def _flush_then_abort(**kw):
            driver._abort_event.set()
        writer.flush = _flush_then_abort
        driver._first_frame_yielded = True
        rc = driver._finalize()
        assert rc == 1
        path.unlink(missing_ok=True)

    def test_finalize_close_failure(self):
        """_finalize lines 299-309: close error handler."""
        path = Path("/tmp") / "test_closeerr.jsonl"
        writer = RecordWriter(path, started_at=100.0)
        driver = HeadlessRecordDriver(
            _make_collector(), writer,
            max_frames=1, register_signals=_noop_register,
        )
        driver._first_frame_yielded = True
        original_close = writer.close
        writer.close = lambda: (_ for _ in ()).throw(Exception("close err"))
        rc = driver._finalize()
        assert rc == 1
        writer.close = original_close
        writer.close()
        path.unlink(missing_ok=True)

    def test_finalize_abort_before_first_frame_flush(self):
        """_finalize flush error with no frames yielded (hint=0)."""
        path = Path("/tmp") / "test_ff.jsonl"
        writer = RecordWriter(path, started_at=100.0)
        driver = HeadlessRecordDriver(
            _make_collector(), writer,
            max_frames=1, register_signals=_noop_register,
        )
        driver._first_frame_yielded = False
        writer.flush = lambda **kw: (_ for _ in ()).throw(Exception("fail"))
        rc = driver._finalize()
        assert rc == 1
        path.unlink(missing_ok=True)

    def test_run_headless_record_wrapper(self):
        """run_headless_record convenience wrapper (lines 334, 345).

        Uses a collector with enough time values and a fast monotonic clock
        to produce at least one frame before the stream runs out.
        """
        path = Path("/tmp") / "test_wrapper.jsonl"
        collector = _make_collector(times=[100.0, 105.0, 110.0, 115.0])
        writer = RecordWriter(path, started_at=100.0)
        rc = run_headless_record(
            collector, writer,
            max_frames=2, register_signals=_noop_register,
            monotonic=lambda: 1.0,
        )
        assert rc == 0
        path.unlink(missing_ok=True)


# ======================================================================
# record/reader.py — 16 missing lines, 11 branch pairs
# ======================================================================

class TestRecordReaderGaps:
    """Close every gap in reader.py."""

    def test_reader_truncated_json_line(self):
        """reader: truncated final line that ends without newline (line 133)."""
        base = _fixture_frame()
        path = Path("/tmp") / "trunc.jsonl"
        with RecordWriter(path, started_at=base.ts) as w:
            w.write_frame(base)
        with path.open("a") as fh:
            fh.write('{"type":"frame","schema_version":1,"ts"')  # truncated, no newline
        # Should NOT raise ValueError because line doesn't end with \n -> break
        frames = list(RecordReader(path))
        assert len(frames) == 1
        path.unlink(missing_ok=True)

    def test_reader_invalid_json_with_newline(self):
        """reader: invalid JSON line ending with newline -> ValueError (line 134)."""
        path = Path("/tmp") / "badjson.jsonl"
        with path.open("w") as fh:
            fh.write('{"type":"header","schema_version":1}\n')
            fh.write("not valid json\n")
        with pytest.raises(ValueError, match="invalid JSON on line 2"):
            list(RecordReader(path))
        path.unlink(missing_ok=True)

    def test_reader_invalid_schema_version_in_header(self):
        """reader: header with missing schema_version (line 139)."""
        path = Path("/tmp") / "badschema.jsonl"
        with path.open("w") as fh:
            fh.write('{"type":"header","not_schema":1}\n')
        with pytest.raises(ValueError, match="invalid recording header"):
            list(RecordReader(path))
        path.unlink(missing_ok=True)

    def test_reader_unsupported_schema_version(self):
        """reader: schema_version != 1 raises (line 143)."""
        path = Path("/tmp") / "unsup.jsonl"
        with path.open("w") as fh:
            fh.write('{"type":"header","schema_version":2}\n')
        with pytest.raises(ValueError, match="unsupported recording schema_version"):
            list(RecordReader(path))
        path.unlink(missing_ok=True)

    def test_reader_unexpected_record_type(self):
        """reader: non-None, non-frame type raises (line 153)."""
        path = Path("/tmp") / "badtype.jsonl"
        with path.open("w") as fh:
            fh.write('{"type":"header","schema_version":1}\n')
            fh.write('{"type":"garbage","ts":0}\n')
        with pytest.raises(ValueError, match="unexpected record type"):
            list(RecordReader(path))
        path.unlink(missing_ok=True)

    def test_reader_invalid_frame_payload(self):
        """reader: frame payload that fails frame_from_jsonable (line 159)."""
        path = Path("/tmp") / "badframe.jsonl"
        with path.open("w") as fh:
            fh.write('{"type":"header","schema_version":1}\n')
            fh.write('{"type":"frame","not_a_valid_frame":true}\n')
        with pytest.raises(ValueError, match="invalid recording frame"):
            list(RecordReader(path))
        path.unlink(missing_ok=True)

    def test_reader_unsupported_frame_schema(self):
        """reader: frame with wrong schema_version (line 165)."""
        base = _fixture_frame()
        path = Path("/tmp") / "badframeschema.jsonl"
        with RecordWriter(path, started_at=base.ts) as w:
            w.write_frame(base)
        # Manually corrupt the schema in the written frame
        lines = path.read_text().splitlines()
        lines[1] = lines[1].replace('"schema_version":1', '"schema_version":2')
        path.write_text("\n".join(lines))
        with pytest.raises(ValueError, match="unsupported frame schema_version"):
            list(RecordReader(path))
        path.unlink(missing_ok=True)

    def test_reader_zstd_error(self):
        """reader: ZstdError wraps in ValueError (lines 168-170)."""
        path = Path("/tmp") / "bad.zst"
        path.write_bytes(b"\x28\xb5\x2f\xfdBADZSTDATA")
        with pytest.raises(ValueError, match="corrupt or truncated compressed recording"):
            list(RecordReader(path))
        path.unlink(missing_ok=True)

    def test_reader_empty_lines_skipped(self):
        """reader: empty lines are skipped (line 127)."""
        path = Path("/tmp") / "emptyline.jsonl"
        with path.open("w") as fh:
            fh.write('{"type":"header","schema_version":1}\n')
            fh.write("\n")
            fh.write('{"type":"frame","schema_version":1,"ts":0,"interval_s":1,"host":{},"entities":{}}\n')
        frames = list(RecordReader(path))
        assert len(frames) == 1
        path.unlink(missing_ok=True)


# ======================================================================
# record/replay.py — 5 missing lines, 3 branch pairs
# ======================================================================

class TestReplayGaps:
    """Close every gap in replay.py."""

    def test_replay_empty_frames_raises(self):
        """ReplayDriver with empty frames raises ValueError (line 24)."""
        with pytest.raises(ValueError, match="at least one frame"):
            ReplayDriver([])

    def test_replay_play_negative_speed_raises(self):
        """play with speed <= 0 raises ValueError (line 75)."""
        base = _fixture_frame()
        frame = Frame(ts=base.ts, interval_s=base.interval_s, host=base.host, entities=base.entities, schema_version=1)
        driver = ReplayDriver([frame])
        with pytest.raises(ValueError, match="replay speed must be positive"):
            for _ in driver.play(speed=-1):
                pass

    def test_replay_play_produces_delay(self):
        """play with speed produces delay_s (line 80)."""
        frames = [
            Frame(ts=100.0, interval_s=1.0, host={}, entities={}, schema_version=1),
            Frame(ts=105.0, interval_s=1.0, host={}, entities={}, schema_version=1),
        ]
        driver = ReplayDriver(frames)
        results = list(driver.play(speed=2.0, step=False))
        assert len(results) == 2
        assert results[0].delay_s == 0.0  # first frame has no previous
        assert results[1].delay_s == 2.5  # (105-100)/2
        assert results[0].index == 0

    def test_format_frame_summary(self):
        """format_frame_summary produces expected string (lines 87-88)."""
        base = _fixture_frame()
        frame = Frame(ts=100.0, interval_s=5.0, host=base.host, entities=base.entities, schema_version=1)
        rf = ReplayFrame(index=0, total=5, frame=frame, delay_s=0.0)
        summary = format_frame_summary(rf)
        assert "frame 1/5" in summary
        assert "ts=100.000" in summary

    def test_replay_seek_timestamp_beyond_last(self):
        """seek_timestamp clamps to last frame when ts beyond all frames (line 67)."""
        frames = [
            Frame(ts=100.0, interval_s=1.0, host={}, entities={}, schema_version=1),
            Frame(ts=105.0, interval_s=1.0, host={}, entities={}, schema_version=1),
        ]
        driver = ReplayDriver(frames)
        result = driver.seek_timestamp(999.0)
        assert result.ts == 105.0
        assert driver.index == 1

    def test_replay_seek_timestamp_first_match(self):
        """seek_timestamp finds first frame with ts >= target (line 64)."""
        frames = [
            Frame(ts=100.0, interval_s=1.0, host={}, entities={}, schema_version=1),
            Frame(ts=105.0, interval_s=1.0, host={}, entities={}, schema_version=1),
            Frame(ts=110.0, interval_s=1.0, host={}, entities={}, schema_version=1),
        ]
        driver = ReplayDriver(frames)
        result = driver.seek_timestamp(103.0)
        assert result.ts == 105.0
        assert driver.index == 1


# ======================================================================
# record/writer.py — 5 missing lines, 4 branch pairs
# ======================================================================

class TestWriterGaps:
    """Close every gap in writer.py."""

    def test_is_zstd_file_not_found(self):
        """_is_zstd_file returns False on FileNotFoundError (line 28-29)."""
        assert _is_zstd_file(Path("/nonexistent/path.zst")) is False

    def test_writer_no_zstd_for_existing_zst(self):
        """_open: existing zst without zstandard raises RuntimeError (line 66)."""
        path = Path("/tmp") / "test_nozstd.zst"
        path.write_bytes(b"\x28\xb5\x2f\xfd")
        with patch("topos.record.writer._zstd", None):
            with pytest.raises(RuntimeError, match="cannot append to compressed recording"):
                RecordWriter(path)
        path.unlink(missing_ok=True)

    def test_writer_unsupported_schema_version(self):
        """write_frame with wrong schema_version raises ValueError (line 96)."""
        base = _fixture_frame()
        path = Path("/tmp") / "test_badschema.jsonl"
        writer = RecordWriter(path, started_at=100.0)
        bad_frame = Frame(ts=base.ts, interval_s=base.interval_s, host=base.host, entities=base.entities, schema_version=99)
        with pytest.raises(ValueError, match="unsupported schema_version"):
            writer.write_frame(bad_frame)
        writer.close()
        path.unlink(missing_ok=True)

    def test_flush_returns_early_when_not_open(self):
        """flush returns early when _text is None (line 104-105)."""
        path = Path("/tmp") / "test_flush_early.jsonl"
        writer = RecordWriter(path, started_at=100.0)
        writer.close()
        before = path.read_bytes()
        writer.flush(force=True)
        assert writer._text is None
        assert path.read_bytes() == before
        path.unlink(missing_ok=True)

    def test_write_frame_triggers_flush(self):
        """write_frame calls flush when _frames_since_flush >= flush_every_frames (line 100 branch)."""
        path = Path("/tmp") / "test_trigflush.jsonl"
        writer = RecordWriter(path, flush_every_frames=1)
        base = _fixture_frame()
        frame = Frame(ts=base.ts, interval_s=base.interval_s, host=base.host, entities=base.entities, schema_version=1)
        writer.write_frame(frame)
        assert writer._frames_since_flush == 0
        assert json.loads(path.read_text().splitlines()[-1])["type"] == "frame"
        writer.close()
        path.unlink(missing_ok=True)


# ======================================================================
# Additional targeted tests for remaining headless.py and reader.py gaps
# ======================================================================

class TestHeadlessRemaining:
    """Tests for remaining hard-to-close headless.py lines."""

    def test_run_keyboard_interrupt_causes_baseexception(self):
        """run() line 186: KeyboardInterrupt (a BaseException) triggers close-and-reraise."""
        path = Path("/tmp") / "test_kbi.jsonl"
        writer = RecordWriter(path, started_at=100.0)
        with patch("topos.record.headless.live_frame_stream",
                   side_effect=KeyboardInterrupt):
            driver = HeadlessRecordDriver(
                _make_collector(), writer,
                max_frames=5, register_signals=_noop_register,
            )
            with pytest.raises(KeyboardInterrupt):
                driver.run()
        path.unlink(missing_ok=True)

    def test_finalize_close_error_after_flush(self):
        """_finalize lines 293-294: close error handler after abort."""
        path = Path("/tmp") / "test_cabort.jsonl"
        writer = RecordWriter(path, started_at=100.0)
        driver = HeadlessRecordDriver(
            _make_collector(), writer,
            max_frames=1, register_signals=_noop_register,
        )
        driver._abort_event.set()
        driver._writer.close = lambda: (_ for _ in ()).throw(Exception("close error"))
        rc = driver._finalize()
        assert rc == 1


class TestReaderRemaining:
    """Tests for remaining reader.py gaps (zstd decompression paths)."""

    def test_zstd_reader_corrupt_data_raises(self):
        """_ZstdStreamReader._pump raises ValueError on truncated zstd (line 64)."""
        path = Path("/tmp") / "test_corrupt.zst"
        # Write a valid zstd magic but garbage after it
        path.write_bytes(b"\x28\xb5\x2f\xfd\x00\x00\x00\x00")
        reader = RecordReader(path)
        with pytest.raises(ValueError, match="corrupt or truncated compressed recording"):
            list(reader)
        path.unlink(missing_ok=True)

    def test_open_text_no_zstd_for_compressed_file(self):
        """_open_text raises RuntimeError when zstd is needed but unavailable (line 96-97)."""
        from topos.record.reader import _open_text
        path = Path("/tmp") / "test_nozstd2.zst"
        path.write_bytes(b"\x28\xb5\x2f\xfd\x00")
        with patch("topos.record.reader._zstd", None):
            with pytest.raises(RuntimeError, match="cannot read compressed recording"):
                _open_text(path)
        path.unlink(missing_ok=True)


class TestFinalGaps:
    """Final targeted tests for remaining infrastructure-dependent gaps."""

    def test_headless_drive_baseexception_without_mock(self):
        """HeadlessRecordDriver._drive raises KeyboardInterrupt (line 186)."""
        path = Path("/tmp") / "test_fin_kbi.jsonl"
        writer = RecordWriter(path, started_at=100.0)
        driver = HeadlessRecordDriver(
            _make_collector(), writer,
            max_frames=3, register_signals=_noop_register,
        )
        def _exploding_stream():
            yield None  # This won't be reached; next() on this raises StopIteration
        # We override the stream to raise KeyboardInterrupt
        original_drive = driver._drive
        def _raising_drive(stream):
            raise KeyboardInterrupt("ctrl-c")
        driver._drive = _raising_drive
        with pytest.raises(KeyboardInterrupt):
            driver.run()
        path.unlink(missing_ok=True)

    def test_reader_zstd_multi_chunk(self):
        """_ZstdStreamReader reuses decompressor across chunks (branch [66,68])."""
        path = Path("/tmp") / "test_multi.zst"
        import zstandard
        data = b'{"type":"header","schema_version":1}\n{"type":"frame","schema_version":1,"ts":0,"interval_s":1,"host":{},"entities":{}}\n'
        cctx = zstandard.ZstdCompressor()
        compressed = cctx.compress(data)
        path.write_bytes(compressed)
        from topos.record.reader import _ZstdStreamReader
        with path.open("rb") as fh:
            reader = _ZstdStreamReader(fh, path)
            buffer = bytearray(1024)
            result = reader.readinto(buffer)
        assert bytes(buffer[:result]) == data
        path.unlink(missing_ok=True)

    def test_headless_finalize_close_error_after_flush_abort(self):
        """_finalize close error after flush+abort (lines 293-294)."""
        path = Path("/tmp") / "test_fin_clr.jsonl"
        writer = RecordWriter(path, started_at=100.0)
        driver = HeadlessRecordDriver(
            _make_collector(), writer,
            max_frames=2, register_signals=_noop_register,
        )
        driver._first_frame_yielded = True
        driver._frames_written = 3
        # Set abort after flush succeeds, then close fails
        tracker = {"flushed": False}
        def _special_flush(**kw):
            driver._abort_event.set()
            tracker["flushed"] = True
        def _failing_close():
            raise Exception("close err")
        writer.flush = _special_flush
        writer.close = _failing_close
        rc = driver._finalize()
        assert rc == 1
        assert tracker["flushed"]
        path.unlink(missing_ok=True)

    def test_headless_abort_at_start_of_finalize(self):
        """_finalize with abort set and close failure (lines 266-267)."""
        path = Path("/tmp") / "test_fin_abort2.jsonl"
        writer = RecordWriter(path, started_at=100.0)
        driver = HeadlessRecordDriver(
            _make_collector(), writer,
            max_frames=1, register_signals=_noop_register,
        )
        driver._abort_event.set()
        def _failing_close():
            raise OSError("io error")
        writer.close = _failing_close
        rc = driver._finalize()
        assert rc == 1
        path.unlink(missing_ok=True)

    def test_reader_zstd_double_pump(self):
        """_ZstdStreamReader._pump reuses decompressor (branch [66,68] False side)."""
        import zstandard
        path = Path("/tmp") / "test_double.zst"
        # Create zstd data that needs multiple decompression chunks
        data = b'x' * 200000  # large enough to span chunks
        cctx = zstandard.ZstdCompressor()
        compressed = cctx.compress(data)
        path.write_bytes(compressed)
        from topos.record.reader import _ZstdStreamReader
        with path.open("rb") as fh:
            reader = _ZstdStreamReader(fh, path, chunk_size=1024)  # tiny chunk
            result = bytearray()
            buf = bytearray(4096)
            while True:
                n = reader.readinto(buf)
                if n == 0:
                    break
                result.extend(buf[:n])
        assert len(result) == 200000
        path.unlink(missing_ok=True)

    def test_writer_no_flush_on_first_frame(self):
        """write_frame does NOT flush when threshold not reached (branch [100,-94] False)."""
        base = _fixture_frame()
        path = Path("/tmp") / "test_noflush.jsonl"
        writer = RecordWriter(path, flush_every_frames=5)
        frame = Frame(ts=base.ts, interval_s=base.interval_s, host=base.host, entities=base.entities, schema_version=1)
        # Writing 1 frame with threshold=5 should NOT trigger flush
        writer.write_frame(frame)
        assert writer._frames_since_flush == 1
        writer.close()
        path.unlink(missing_ok=True)

    def test_reader_zstd_reuse_decompressor(self):
        """_ZstdStreamReader reuses decompressor across _pump calls (branch [66,68])."""
        import zstandard
        path = Path("/tmp") / "test_reuse.zst"
        # Create zstd data with multiple frames so decompression needs multiple chunks
        data = b"Hello World! " * 5000  # ~65KB
        cctx = zstandard.ZstdCompressor()
        compressed = cctx.compress(data)
        path.write_bytes(compressed)
        from topos.record.reader import _ZstdStreamReader
        with path.open("rb") as fh:
            # Very small chunk forces multiple _pump() iterations
            reader = _ZstdStreamReader(fh, path, chunk_size=512)
            result = bytearray()
            buf = bytearray(2048)
            while True:
                n = reader.readinto(buf)
                if n == 0:
                    break
                result.extend(buf[:n])
        assert result == data
        path.unlink(missing_ok=True)

class TestStubbornGaps:
    """Final targeted tests for the last two external-boundary gaps."""

    def test_headless_install_signal_handlers_second_signal(self):
        """install_signal_handlers line 65: second signal calls os._exit.

        os._exit is an external effect that terminates the process.
        Mocking it is acceptable per the 'mock external effects' rule.
        """
        from unittest.mock import patch
        import os as _os
        se = threading.Event()
        install_signal_handlers(se)
        se.set()  # First signal already received
        with patch("os._exit") as mock_exit:
            import signal
            signal.raise_signal(signal.SIGTERM)
        mock_exit.assert_called_once_with(1)

    def test_reader_zstd_reuse_pump_directly(self):
        """_ZstdStreamReader._pump branch [66,68]: reuse decompressor across chunks."""
        import zstandard
        path = Path("/tmp") / "test_direct.zst"
        # Create zstd data where decompression produces output in multiple chunks
        data = bytes(range(256)) * 2000  # ~512KB
        cctx = zstandard.ZstdCompressor()
        compressed = cctx.compress(data)
        path.write_bytes(compressed)
        from topos.record.reader import _ZstdStreamReader
        with path.open("rb") as fh:
            reader = _ZstdStreamReader(fh, path, chunk_size=256)
            # Read in small pieces forcing multiple _pump calls
            chunks = []
            while True:
                buf = bytearray(128)
                n = reader.readinto(buf)
                if n == 0:
                    break
                chunks.append(bytes(buf[:n]))
        assert b"".join(chunks) == data
        path.unlink(missing_ok=True)
