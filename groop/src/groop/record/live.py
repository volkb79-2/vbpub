from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass

from groop.collect.collector import Collector
from groop.model import Frame
from groop.record.writer import RecordWriter


@dataclass(frozen=True)
class SampleTiming:
    sample_s: float
    sleep_s: float
    overrun_s: float

    @property
    def skipped_sleep(self) -> bool:
        return self.sleep_s == 0.0


def sample_timing(interval_s: float, sample_s: float) -> SampleTiming:
    bounded_interval = max(0.0, interval_s)
    bounded_sample = max(0.0, sample_s)
    if bounded_sample >= bounded_interval:
        return SampleTiming(
            sample_s=bounded_sample,
            sleep_s=0.0,
            overrun_s=max(0.0, bounded_sample - bounded_interval),
        )
    return SampleTiming(
        sample_s=bounded_sample,
        sleep_s=bounded_interval - bounded_sample,
        overrun_s=0.0,
    )


def live_frame_stream(
    collector: Collector,
    *,
    writer: RecordWriter | None = None,
    monotonic: Callable[[], float] = time.monotonic,
    sleeper: Callable[[float], None] = time.sleep,
) -> Iterator[Frame]:
    """Yield the canonical annotated live frame stream.

    The collector already produces frames after network, governance drift, and
    diagnostics annotation. When a sweep overruns the configured interval, the
    next sweep starts immediately after the current one finishes; we skip sleep
    instead of trying to "catch up" with negative or stacked delays.
    """

    while True:
        started = monotonic()
        frame = collector.collect_once()
        if writer is not None:
            writer.write_frame(frame)
        finished = monotonic()
        timing = sample_timing(collector.config.interval, finished - started)
        yield frame
        if timing.sleep_s > 0:
            sleeper(timing.sleep_s)
