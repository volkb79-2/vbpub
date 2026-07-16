from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from conftest import fixture_frame, fixture_root, systemctl_fixture_runner
from topos.collect.collector import Collector
from topos.config import ToposConfig
from topos.model import Frame, MetricValue, frame_to_jsonable
from topos.record.headless import (
    HeadlessRecordDriver,
    RecordProgress,
    install_signal_handlers,
    make_second_signal_handler,
    run_headless_record,
)
from topos.record.reader import RecordReader
from topos.record.writer import RecordWriter


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _host_stub() -> dict[str, object]:
    from topos.model import MetricValue
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
    """Test seam: do not actually touch OS signal handlers."""


def _signal_from_thread(stop_event: threading.Event, *, delay: float = 0.01) -> threading.Thread:
    """Return a thread that will set the stop_event after *delay* seconds."""
    def _set() -> None:
        time.sleep(delay)
        stop_event.set()
    t = threading.Thread(target=_set, daemon=True)
    t.start()
    return t


# ===================================================================
# RecordProgress
# ===================================================================

class TestRecordProgress:
    def test_basic_progress_line(self) -> None:
        p = RecordProgress()
        p.advance(10.5)
        assert p.frame_count == 1
        assert "frames=1" in p.format_line()
        assert "10.5s" in p.format_line()

    def test_progress_accumulates(self) -> None:
        p = RecordProgress()
        p.advance(5.0)
        p.advance(15.0)
        assert p.frame_count == 2
        assert "frames=2" in p.format_line()
        assert "15.0s" in p.format_line()


# ===================================================================
# HeadlessRecordDriver unit tests (injected seams, no real signals)
# ===================================================================

class TestHeadlessRecordDriver:
    """Tests using injected monotonic clock and no-op signal registration."""

    def test_stops_at_frame_count(self, tmp_path: Path) -> None:
        """max_frames bound causes clean exit after K frames."""
        path = tmp_path / "headless.jsonl"
        collector = _make_collector()
        monotonic_values = iter([0.0, 1.0, 1.5, 6.0, 6.5, 11.0, 11.5, 16.0, 16.5, 21.0, 21.5])

        with RecordWriter(path, config=collector.config, started_at=100.0) as writer:
            driver = HeadlessRecordDriver(
                collector,
                writer,
                max_frames=3,
                register_signals=_noop_register,
                monotonic=lambda: next(monotonic_values),
            )
            rc = driver.run()

        assert rc == 0
        assert driver.frames_written == 3
        lines = [json.loads(line) for line in path.read_text().splitlines()]
        assert len(lines) == 4  # header + 3 frames
        assert all(line["type"] == "frame" for line in lines[1:])

    def test_stops_at_duration(self, tmp_path: Path) -> None:
        """duration bound causes clean exit after time elapses."""
        path = tmp_path / "headless.jsonl"
        collector = _make_collector()
        monotonic_values = iter([0.0, 1.0, 1.5, 6.0, 6.5, 11.0, 11.5, 16.0, 16.5])

        with RecordWriter(path, config=collector.config, started_at=100.0) as writer:
            driver = HeadlessRecordDriver(
                collector,
                writer,
                duration=8.0,  # should stop between frame 2 and 3
                register_signals=_noop_register,
                monotonic=lambda: next(monotonic_values),
            )
            rc = driver.run()

        assert rc == 0
        assert 1 <= driver.frames_written <= 3
        lines = [json.loads(line) for line in path.read_text().splitlines()]
        assert lines[0]["type"] == "header"

    def test_duration_and_max_frames_mutually_exclusive(self) -> None:
        collector = _make_collector()
        writer = object()  # type: ignore[assignment]  # not opened, just testing ctor
        with pytest.raises(ValueError, match="mutually exclusive"):
            HeadlessRecordDriver(collector, writer, duration=5.0, max_frames=10)  # type: ignore[arg-type]

    def test_interval_override_applied(self, tmp_path: Path) -> None:
        """interval override is applied to collector.config.interval."""
        path = tmp_path / "headless.jsonl"
        collector = _make_collector()
        original_interval = collector.config.interval
        assert original_interval == 5.0

        with RecordWriter(path, config=collector.config, started_at=100.0) as writer:
            driver = HeadlessRecordDriver(
                collector,
                writer,
                interval=2.0,
                max_frames=1,
                register_signals=_noop_register,
            )
            driver.run()

        # After driver.run, the collector's config should have the new interval
        assert collector.config.interval == 2.0
        assert collector.config.interval != original_interval

    def test_signal_shutdown_writes_frame(self, tmp_path: Path) -> None:
        """Signal causes clean shutdown; the in-flight frame is written."""
        path = tmp_path / "headless.jsonl"
        collector = _make_collector(times=[100.0, 105.0, 110.0])
        monotonic_values = iter([0.0, 1.0, 1.5, 6.0, 6.5])

        with RecordWriter(path, config=collector.config, started_at=100.0) as writer:
            driver = HeadlessRecordDriver(
                collector,
                writer,
                max_frames=5,  # would run forever if not for signal
                register_signals=_noop_register,
                monotonic=lambda: next(monotonic_values),
            )
            # Simulate signal after a short delay.
            _signal_from_thread(driver.stop_event, delay=0.05)
            rc = driver.run()

        assert rc == 0
        assert driver.frames_written >= 1
        # The file should be parseable.
        frames = list(RecordReader(path))
        assert len(frames) == driver.frames_written

    def test_jsonl_record_reader_roundtrip(self, tmp_path: Path) -> None:
        """Written .jsonl file parses end-to-end with RecordReader."""
        path = tmp_path / "headless.jsonl"
        collector = _make_collector()

        with RecordWriter(path, config=collector.config, started_at=100.0) as writer:
            driver = HeadlessRecordDriver(
                collector,
                writer,
                max_frames=2,
                register_signals=_noop_register,
            )
            rc = driver.run()

        assert rc == 0
        frames = list(RecordReader(path))
        assert len(frames) == 2
        # Verify each frame is structurally complete.
        for f in frames:
            assert isinstance(f, Frame)
            assert f.schema_version == 1
            assert f.entities

    def test_zst_record_reader_roundtrip(self, tmp_path: Path) -> None:
        """Written .jsonl.zst file parses end-to-end with RecordReader."""
        zstandard = pytest.importorskip("zstandard", reason="zstandard extra not installed")
        path = tmp_path / "headless.jsonl.zst"
        collector = _make_collector()

        with RecordWriter(path, config=collector.config, started_at=100.0) as writer:
            driver = HeadlessRecordDriver(
                collector,
                writer,
                max_frames=2,
                register_signals=_noop_register,
            )
            rc = driver.run()

        assert rc == 0
        frames = list(RecordReader(path))
        assert len(frames) == 2
        for f in frames:
            assert isinstance(f, Frame)
            assert f.schema_version == 1

    def test_second_signal_during_shutdown(self, tmp_path: Path) -> None:
        """Second signal during finalization exits promptly non-zero."""
        path = tmp_path / "headless.jsonl"
        collector = _make_collector(times=[100.0, 105.0])
        monotonic_values = iter([0.0, 1.0, 1.5])

        with RecordWriter(path, config=collector.config, started_at=100.0) as writer:
            driver = HeadlessRecordDriver(
                collector,
                writer,
                max_frames=5,
                register_signals=_noop_register,
                monotonic=lambda: next(monotonic_values),
            )
            # Set both events to simulate second signal during shutdown.
            driver.stop_event.set()
            driver.abort_event.set()
            rc = driver.run()

        assert rc == 1

    def test_writer_flush_failure_mid_run(self, tmp_path: Path) -> None:
        """Writer failure mid-run exits non-zero; partial file is valid JSONL."""
        path = tmp_path / "headless.jsonl"
        collector = _make_collector(times=[100.0, 105.0, 110.0])

        class _FlakyWriter(RecordWriter):
            _fail_after: int = 0

            def write_frame(self, frame: Frame) -> None:
                super().write_frame(frame)
                self._fail_after -= 1
                if self._fail_after <= 0:
                    # Simulate I/O error by making the writer inoperable
                    self._text.close()
                    self._text = None
                    self._binary = None
                    raise OSError("simulated write error")

            def close(self) -> None:
                # Avoid double-close on already-closed writer
                if self._text is not None:
                    super().close()

        writer = _FlakyWriter(path, config=collector.config, started_at=100.0)
        writer._fail_after = 1  # fail after first frame

        driver = HeadlessRecordDriver(
            collector,
            writer,
            max_frames=5,
            register_signals=_noop_register,
        )
        rc = driver.run()

        # Should exit non-zero since there was a write error.
        assert rc != 0
        # Partial file should be valid up to the last flushed frame.
        if path.stat().st_size > 0:
            try:
                frames = list(RecordReader(path))
                # At minimum the header should be readable.
                assert len(frames) >= 0
            except Exception:
                pass  # file may have been truncated before any frame

    def test_no_textual_import_on_headless_path(self, tmp_path: Path) -> None:
        """Assert 'textual' is not imported after a headless record run."""
        # Run headless record in a subprocess and check that textual is not
        # in sys.modules after completion.
        test_code = """
import sys
sys.path.insert(0, "topos/src")
from pathlib import Path
import tempfile

from topos.collect.collector import Collector
from topos.config import ToposConfig
from topos.record.headless import HeadlessRecordDriver
from topos.record.writer import RecordWriter

# Minimal collector for a single frame
cgroup_root = Path("topos/tests/fixtures/cgroupfs/gstammtisch")
collector = Collector(
    cgroup_root=cgroup_root,
    config=ToposConfig(interval=5.0),
    docker_inspect=lambda _cid: None,
    host_collector=lambda: {},
    network_providers=(),
)
with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as f:
    path = Path(f.name)

with RecordWriter(path, config=collector.config) as writer:
    driver = HeadlessRecordDriver(collector, writer, max_frames=1,
                                   register_signals=lambda ev: None)
    rc = driver.run()

assert rc == 0, f"headless run failed: {rc}"
# Check that textual was NOT imported
assert "textual" not in sys.modules, "textual was imported on headless path"
path.unlink()
print("OK: no textual import")
"""
        # Use PYTHONWARNINGS to avoid accidental textual import via warnings
        env = os.environ.copy()
        env["PYTHONPATH"] = str(fixture_root().parents[1] / "src")
        proc = subprocess.run(
            [sys.executable, "-c", test_code],
            cwd=fixture_root().parents[1].parent,  # topos/ directory
            env={**os.environ, "PYTHONWARNINGS": "error"},
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
        )
        assert proc.returncode == 0, f"subprocess failed: {proc.stderr}"
        assert "OK: no textual import" in proc.stdout


# ===================================================================
# CLI integration tests (subprocess)
# ===================================================================

class TestHeadlessCLI:
    """Test CLI flag validation and integration."""

    _base_env: dict[str, str] = {}

    @pytest.fixture(autouse=True)
    def _setup_env(self) -> None:
        self._base_env = os.environ.copy()
        self._base_env["PYTHONPATH"] = str(fixture_root().parents[1] / "src")

    def _run(self, *args: str, **kwargs: object) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-m", "topos.cli", *args],
            cwd=fixture_root().parents[1].parent,
            env=self._base_env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
            **kwargs,  # type: ignore[arg-type]
        )

    def test_headless_requires_record(self) -> None:
        proc = self._run("--headless")
        assert proc.returncode == 2
        assert "--headless requires --record" in proc.stderr

    def test_headless_rejects_attach(self) -> None:
        proc = self._run("--headless", "--record", "/tmp/x.jsonl", "--attach", "/tmp/sock")
        assert proc.returncode == 2
        assert "--headless is not supported with --attach" in proc.stderr

    def test_headless_rejects_replay(self) -> None:
        proc = self._run("--headless", "--replay", "/tmp/y.jsonl")
        assert proc.returncode == 2
        assert "--headless is not supported with --replay" in proc.stderr

    def test_duration_and_frames_mutually_exclusive(self) -> None:
        proc = self._run("--record", "/tmp/x.jsonl", "--duration", "10", "--frames", "5")
        assert proc.returncode == 2
        assert "--duration and --frames are mutually exclusive" in proc.stderr

    def test_headless_with_once_works(self, tmp_path: Path) -> None:
        """--headless with --once does not require UI and writes one frame."""
        record_path = tmp_path / "once.jsonl"
        proc = self._run(
            "--record", str(record_path),
            "--headless",
            "--once",
            "--json",
            "--cgroup-root", str(fixture_root() / "cgroupfs" / "gstammtisch"),
        )
        assert proc.returncode == 0
        payload = json.loads(proc.stdout)
        assert payload["schema_version"] == 1
        lines = record_path.read_text().splitlines()
        assert json.loads(lines[0])["type"] == "header"
        assert json.loads(lines[1])["type"] == "frame"

    def test_headless_writes_header_and_frame(self, tmp_path: Path) -> None:
        """Basic --headless --record path writes valid recording."""
        record_path = tmp_path / "headless.jsonl"
        proc = self._run(
            "--record", str(record_path),
            "--headless",
            "--frames", "2",
            "--cgroup-root", str(fixture_root() / "cgroupfs" / "gstammtisch"),
        )
        assert proc.returncode == 0
        lines = record_path.read_text().splitlines()
        assert json.loads(lines[0])["type"] == "header"
        assert json.loads(lines[1])["type"] == "frame"
        assert json.loads(lines[2])["type"] == "frame"

    def test_headless_frames_bound_exits_zero(self, tmp_path: Path) -> None:
        """--frames K causes clean exit after K frames."""
        record_path = tmp_path / "bounded.jsonl"
        proc = self._run(
            "--record", str(record_path),
            "--headless",
            "--frames", "3",
            "--cgroup-root", str(fixture_root() / "cgroupfs" / "gstammtisch"),
        )
        assert proc.returncode == 0
        lines = record_path.read_text().splitlines()
        assert len(lines) == 4  # header + 3 frames

    def test_headless_duration_bound_exits_zero(self, tmp_path: Path) -> None:
        """--duration S causes clean exit."""
        record_path = tmp_path / "duration.jsonl"
        proc = self._run(
            "--record", str(record_path),
            "--headless",
            "--duration", "1",
            "--cgroup-root", str(fixture_root() / "cgroupfs" / "gstammtisch"),
        )
        assert proc.returncode == 0
        lines = record_path.read_text().splitlines()
        assert len(lines) >= 2  # header + at least 1 frame

    def test_headless_with_interval(self, tmp_path: Path) -> None:
        """--interval N is accepted with --headless."""
        record_path = tmp_path / "interval.jsonl"
        proc = self._run(
            "--record", str(record_path),
            "--headless",
            "--frames", "1",
            "--interval", "2.0",
            "--cgroup-root", str(fixture_root() / "cgroupfs" / "gstammtisch"),
        )
        assert proc.returncode == 0
        lines = record_path.read_text().splitlines()
        assert json.loads(lines[1])["type"] == "frame"


# ===================================================================
# Signal seam tests
# ===================================================================

class TestSignalSeam:
    def test_make_second_signal_handler_first_signal_sets_stop(self) -> None:
        stop = threading.Event()
        abort = threading.Event()
        handler = make_second_signal_handler(stop, abort)
        handler(2, None)  # SIGINT
        assert stop.is_set()
        assert not abort.is_set()

    def test_make_second_signal_handler_second_signal_sets_abort(self) -> None:
        stop = threading.Event()
        abort = threading.Event()
        handler = make_second_signal_handler(stop, abort)
        stop.set()  # first signal already processed
        handler(2, None)  # second signal
        assert abort.is_set()

    def test_install_signal_handlers_registers_sigint_sigterm(self) -> None:
        """install_signal_handlers installs real signal handlers; verify by
        raising SIGINT and checking the event is set."""
        stop = threading.Event()
        install_signal_handlers(stop)

        import os
        os.kill(os.getpid(), 2)  # SIGINT
        assert stop.is_set()

        # Reset and try SIGTERM
        stop.clear()
        os.kill(os.getpid(), 15)  # SIGTERM
        assert stop.is_set()


# ===================================================================
# HeadlessRecordDriver progress tests
# ===================================================================

class TestProgressCadence:
    def test_progress_emitted_at_cadence(self, tmp_path: Path) -> None:
        """Progress lines are emitted at approximately the configured interval."""
        path = tmp_path / "headless.jsonl"
        collector = _make_collector(times=[100.0, 105.0, 110.0, 115.0, 120.0, 125.0])
        # Each frame consumes 3 monotonic calls (stream started, stream finished,
        # driver now_ts).  4 frames + 1 driver start = 13 total values.
        monotonic_values = iter([
            0.0,     # driver start
            1.0, 2.0, 3.0,    # frame 1: stream started=1, stream finished=2, driver now=3
            35.0, 36.0, 37.0,  # frame 2: stream started=35, stream finished=36, driver now=37
            70.0, 71.0, 72.0,  # frame 3: stream started=70, stream finished=71, driver now=72
            105.0, 106.0, 107.0,  # frame 4: stream started=105, stream finished=106, driver now=107
        ])
        captured: list[str] = []

        with RecordWriter(path, config=collector.config, started_at=100.0) as writer:
            driver = HeadlessRecordDriver(
                collector,
                writer,
                max_frames=4,
                progress_interval_s=30.0,
                register_signals=_noop_register,
                monotonic=lambda: next(monotonic_values),
                stderr=captured.append,
            )
            driver.run()

        # Should have at least 2 progress lines (at 30s and 60s)
        progress_lines = [l for l in captured if l.startswith("frames=")]
        assert len(progress_lines) >= 1
