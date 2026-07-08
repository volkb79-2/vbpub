from __future__ import annotations

import math
from array import array
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Final

from groop.config import GroopConfig, load
from groop.model import Frame, MetricValue

DEFAULT_HISTORY_METRICS: Final[tuple[str, ...]] = (
    "ram",
    "anon",
    "file",
    "z_pool",
    "z_eq",
    "swap_disk",
    "rf_z_per_s",
    "rf_d_per_s",
    "rf_f_per_s",
    "cpu_pct",
    "psi_mem_some_avg10",
    "psi_mem_full_avg10",
    "psi_io_some_avg10",
    "psi_io_full_avg10",
    "psi_cpu_some_avg10",
    "io_r_bps",
    "io_w_bps",
    "io_r_iops",
    "io_w_iops",
    "net_tx_bps",
    "net_rx_bps",
    "pids_current",
    "headroom_max_pct",
    "hot_pct",
)

_NAN = float("nan")


def _coerce_numeric(value: MetricValue | float | int | None) -> float | None:
    if isinstance(value, MetricValue):
        value = value.v
    if value is None:
        return None
    return float(value)


@dataclass
class _Series:
    samples: array
    cursor: int = 0
    count: int = 0

    @classmethod
    def with_capacity(cls, capacity: int) -> _Series:
        return cls(samples=array("f", [_NAN]) * capacity)

    def append(self, value: float | None) -> None:
        self.samples[self.cursor] = _NAN if value is None else value
        self.cursor = (self.cursor + 1) % len(self.samples)
        if self.count < len(self.samples):
            self.count += 1

    def last(self, n: int) -> list[float | None]:
        take = min(max(0, n), self.count)
        if take == 0:
            return []
        start = (self.cursor - take) % len(self.samples)
        out: list[float | None] = []
        for offset in range(take):
            sample = self.samples[(start + offset) % len(self.samples)]
            out.append(None if math.isnan(sample) else float(sample))
        return out

    def minmax(self, n: int) -> tuple[float, float] | None:
        values = [sample for sample in self.last(n) if sample is not None]
        if not values:
            return None
        return min(values), max(values)

    @property
    def storage_bytes(self) -> int:
        return len(self.samples) * self.samples.itemsize


class HistoryRing:
    def __init__(
        self,
        capacity: int,
        *,
        tracked_metrics: Iterable[str] = DEFAULT_HISTORY_METRICS,
        entity_grace_frames: int = 0,
    ) -> None:
        self.capacity = max(1, capacity)
        self.tracked_metrics = tuple(tracked_metrics)
        self.entity_grace_frames = max(0, entity_grace_frames)
        self._tick = 0
        self._series: dict[tuple[str, str], _Series] = {}
        self._entity_last_seen: dict[str, int] = {}

    @classmethod
    def from_config(cls, config: GroopConfig | None = None) -> HistoryRing:
        cfg = config or load()
        return cls(
            cfg.history.capacity_for_interval(cfg.interval),
            tracked_metrics=DEFAULT_HISTORY_METRICS,
            entity_grace_frames=cfg.history.entity_grace_frames(cfg.interval),
        )

    def append_frame(self, frame: Frame) -> None:
        self._tick += 1
        current_entities = set(frame.entities)
        for entity_key, entity_frame in frame.entities.items():
            self._entity_last_seen[entity_key] = self._tick
            for metric_name in self.tracked_metrics:
                metric = entity_frame.metrics.get(metric_name)
                self._series_for(entity_key, metric_name).append(_coerce_numeric(metric))
        stale_entities = [entity_key for entity_key in self._entity_last_seen if entity_key not in current_entities]
        for entity_key in stale_entities:
            missed = self._tick - self._entity_last_seen[entity_key]
            if missed <= self.entity_grace_frames:
                for metric_name in self.tracked_metrics:
                    self._series_for(entity_key, metric_name).append(None)
                continue
            self._entity_last_seen.pop(entity_key, None)
            for metric_name in self.tracked_metrics:
                self._series.pop((entity_key, metric_name), None)

    def last(self, entity_key: str, metric_name: str, n: int) -> list[float | None]:
        series = self._series.get((entity_key, metric_name))
        return [] if series is None else series.last(n)

    def minmax(self, entity_key: str, metric_name: str, n: int) -> tuple[float, float] | None:
        series = self._series.get((entity_key, metric_name))
        return None if series is None else series.minmax(n)

    def has_series(self, entity_key: str, metric_name: str) -> bool:
        return (entity_key, metric_name) in self._series

    @property
    def storage_bytes(self) -> int:
        return sum(series.storage_bytes for series in self._series.values())

    @property
    def series_count(self) -> int:
        return len(self._series)

    def _series_for(self, entity_key: str, metric_name: str) -> _Series:
        key = (entity_key, metric_name)
        series = self._series.get(key)
        if series is None:
            series = _Series.with_capacity(self.capacity)
            self._series[key] = series
        return series
