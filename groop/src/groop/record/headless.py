from __future__ import annotations

import dataclasses
import sys
import threading
import time
from collections.abc import Callable, Iterator

from groop.collect.collector import Collector
from groop.record.live import live_frame_stream
from groop.record.writer import RecordWriter


__all__ = [
    "HeadlessRecordDriver",
    "SignalRegistration",
    "install_signal_handlers",
    "make_second_signal_handler",
    "run_headless_record",
]


# ---------------------------------------------------------------------------
# Injectable signal helpers
# ---------------------------------------------------------------------------

SignalRegistration = Callable[[threading.Event], None]
"""Signature for signal-registration callables.

The implementation receives a ``threading.Event`` that it should set when
SIGINT or SIGTERM is received (first signal).  When the event is *already*
set (second signal) the callable should instead set the abort event for
prompt non-zero exit.
"""


def make_second_signal_handler(
    stop_event: threading.Event,
    abort_event: threading.Event,
) -> Callable[[int, object], None]:
    """Return a signal handler with second-signal abort support.

    First signal -> set *stop_event* (clean in-flight completion).
    Second signal (stop_event already set) -> set *abort_event* (prompt exit).
    """
    def _handler(signum: int, _frame: object) -> None:
        if stop_event.is_set():
            abort_event.set()
        else:
            stop_event.set()
    return _handler


def install_signal_handlers(stop_event: threading.Event) -> None:
    """Default signal registration for production use.

    Installs SIGINT/SIGTERM handlers on the main thread.  A second signal
    causes an immediate ``os._exit(1)``.
    """
    import signal as _signal
    import os as _os

    def _handler(signum: int, _frame: object) -> None:
        if stop_event.is_set():
            _os._exit(1)
        stop_event.set()

    _signal.signal(_signal.SIGINT, _handler)
    _signal.signal(_signal.SIGTERM, _handler)


# ---------------------------------------------------------------------------
# Progress state
# ---------------------------------------------------------------------------

class RecordProgress:
    """Bounded progress state for --headless stderr output."""

    __slots__ = ("frame_count", "elapsed_s")

    def __init__(self) -> None:
        self.frame_count: int = 0
        self.elapsed_s: float = 0.0

    def advance(self, elapsed_s: float) -> None:
        self.frame_count += 1
        self.elapsed_s = elapsed_s

    def format_line(self) -> str:
        """Produce one human-readable progress line."""
        return f"frames={self.frame_count} elapsed={self.elapsed_s:.1f}s"


# ---------------------------------------------------------------------------
# Main driver
# ---------------------------------------------------------------------------

class HeadlessRecordDriver:
    """Drive the collector loop headlessly, writing frames to *writer*.

    Owns the ``live_frame_stream`` loop, progress reporting, signal
    handling, and bounded exit conditions (duration / frame count).
    """

    def __init__(
        self,
        collector: Collector,
        writer: RecordWriter,
        *,
        interval: float | None = None,
        duration: float | None = None,
        max_frames: int | None = None,
        progress_interval_s: float = 30.0,
        register_signals: SignalRegistration = install_signal_handlers,
        monotonic: Callable[[], float] = time.monotonic,
        stderr: Callable[[str], None] | None = None,
    ) -> None:
        if duration is not None and max_frames is not None:
            raise ValueError("duration and max_frames are mutually exclusive")
        self._collector = collector
        self._writer = writer
        self._interval = interval
        self._duration = duration
        self._max_frames = max_frames
        self._progress_interval_s = progress_interval_s
        self._register_signals = register_signals
        self._monotonic = monotonic
        self._stderr = stderr or (lambda msg: print(msg, file=sys.stderr))
        self._stop_event = threading.Event()
        self._abort_event = threading.Event()
        self._progress = RecordProgress()
        self._frames_written: int = 0
        self._started_at: float = 0.0
        # Track whether at least one frame was yielded so we can distinguish
        # "failed at frame 0" from "failed at frame N>0".
        self._first_frame_yielded: bool = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def frames_written(self) -> int:
        """Return the count of frames durably written so far."""
        return self._frames_written

    @property
    def stop_event(self) -> threading.Event:
        """The stop event (visible for seam injection in tests)."""
        return self._stop_event

    @property
    def abort_event(self) -> threading.Event:
        """The abort event for second-signal handling (visible for tests)."""
        return self._abort_event

    def run(self) -> int:
        """Run the headless record loop and return an exit code.

        Returns:
            0 on clean completion (duration/frame-count reached, or clean
              single-signal shutdown).
            Non-zero on startup failure (before first frame) or mid-run
              I/O error.
        """
        self._started_at = self._monotonic()

        # Apply interval override, if provided.
        if self._interval is not None:
            self._collector.config = dataclasses.replace(
                self._collector.config, interval=self._interval,
            )

        # Register signal handlers via the injectable seam.
        self._register_signals(self._stop_event)

        stream = live_frame_stream(
            self._collector,
            writer=self._writer,
            monotonic=self._monotonic,
            stop_event=self._stop_event,
        )

        try:
            return self._drive(stream)
        except BaseException:
            self._writer.close()
            raise

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _drive(self, stream: Iterator) -> int:
        """Consume the frame stream, applying bounds and progress."""
        deadline: float | None = None
        if self._duration is not None:
            deadline = self._started_at + self._duration

        last_progress_ts = self._started_at

        while not self._stop_event.is_set() and not self._abort_event.is_set():
            try:
                frame = next(stream)
            except StopIteration:
                # The stream itself stopped (e.g. because stop_event was set
                # inside live_frame_stream).  The current in-flight sweep
                # completed and its frame has already been written by the
                # stream -- we're done.
                break
            except Exception as exc:
                # Writer or collector failure mid-run.
                frame_hint = (
                    "0" if not self._first_frame_yielded
                    else f"{self._frames_written}"
                )
                print(
                    f"headless record failed at frame {frame_hint}: {exc}",
                    file=sys.stderr,
                )
                # Best-effort close; the writer may already be in an
                # inconsistent state.
                try:
                    self._writer.close()
                except Exception:  # noqa: BLE001
                    pass
                return 1

            self._first_frame_yielded = True
            self._frames_written += 1
            now_ts = self._monotonic()
            elapsed = now_ts - self._started_at

            # Bounded progress reporting to stderr.
            if now_ts - last_progress_ts >= self._progress_interval_s:
                self._progress.advance(elapsed)
                self._stderr(self._progress.format_line())
                last_progress_ts = now_ts

            # Check if a second signal arrived during this iteration.
            if self._abort_event.is_set():
                return self._finalize()

            # Frame-count bound.
            if self._max_frames is not None and self._frames_written >= self._max_frames:
                return self._finalize()

            # Duration bound.
            if deadline is not None and now_ts >= deadline:
                return self._finalize()

        # Reached because stop_event or abort_event was set, or the stream
        # ended naturally.
        return self._finalize()

    def _finalize(self) -> int:
        """Flush and close the writer.

        On a mid-run writer error, distinguishes failed-at-0 from
        failed-at-N>0 in the error message.
        """
        # If a second signal arrived before we got here, exit promptly.
        if self._abort_event.is_set():
            try:
                self._writer.close()
            except Exception:  # noqa: BLE001
                pass
            return 1

        try:
            self._writer.flush(force=True)
        except Exception as exc:
            # Writer flush failed -- attempt best-effort close and report.
            frame_hint = (
                "0" if not self._first_frame_yielded
                else f"{self._frames_written}"
            )
            try:
                self._writer.close()
            except Exception:  # noqa: BLE001
                pass
            print(
                f"headless record failed at frame {frame_hint}: "
                f"flush error: {exc}",
                file=sys.stderr,
            )
            return 1

        # Check abort again after flush (which may have been slow).
        if self._abort_event.is_set():
            try:
                self._writer.close()
            except Exception:  # noqa: BLE001
                pass
            return 1

        try:
            self._writer.close()
        except Exception as exc:
            frame_hint = (
                "0" if not self._first_frame_yielded
                else f"{self._frames_written}"
            )
            print(
                f"headless record failed at frame {frame_hint}: "
                f"close error: {exc}",
                file=sys.stderr,
            )
            return 1

        return 0


# ---------------------------------------------------------------------------
# Convenience wrapper
# ---------------------------------------------------------------------------

def run_headless_record(
    collector: Collector,
    writer: RecordWriter,
    *,
    interval: float | None = None,
    duration: float | None = None,
    max_frames: int | None = None,
    register_signals: SignalRegistration = install_signal_handlers,
    progress_interval_s: float = 30.0,
    monotonic: Callable[[], float] = time.monotonic,
    stderr: Callable[[str], None] | None = None,
) -> int:
    """Convenience wrapper around ``HeadlessRecordDriver``.

    Returns the same exit code as ``HeadlessRecordDriver.run()``.
    """
    driver = HeadlessRecordDriver(
        collector,
        writer,
        interval=interval,
        duration=duration,
        max_frames=max_frames,
        progress_interval_s=progress_interval_s,
        register_signals=register_signals,
        monotonic=monotonic,
        stderr=stderr,
    )
    return driver.run()
