"""Remaining in-memory boundary contracts for :mod:`topos.report`."""

from __future__ import annotations

import math

import pytest

import topos.report as report
from topos.model import Entity, EntityFrame, Frame, MetricValue
from topos.report import (
    WindowRange,
    _derive_rate,
    _finite_gauge_value,
    _nearest_rank_percentile,
    _population_cov,
    compute_profile,
    compute_report_with_selection,
    select_report_window,
)


def _frame(ts: float, entities: dict[str, dict[str, MetricValue]]) -> Frame:
    return Frame(
        schema_version=1,
        ts=ts,
        interval_s=5.0,
        host={},
        entities={
            key: EntityFrame(Entity(key, "scope", ""), metrics)
            for key, metrics in entities.items()
        },
    )


def _derived_counter(raw: int) -> MetricValue:
    return MetricValue(v=None, src="derived", raw=raw)


def test_empty_percentile_has_a_typed_precondition_failure() -> None:
    """Callers cannot silently invent a percentile for a sample-free metric."""
    with pytest.raises(ValueError, match="cannot compute percentile from empty sample set"):
        _nearest_rank_percentile([], 95)


def test_non_numeric_gauge_value_is_not_accepted_as_a_measurement() -> None:
    """Malformed decoded input cannot become a numeric stability sample."""
    frame = _frame(
        100.0,
        {"worker.scope": {"ram": MetricValue(v="128", src="exact")}},  # type: ignore[arg-type]
    )

    assert _finite_gauge_value(frame, "worker.scope", "ram") is None


def test_zero_mean_with_spread_is_not_a_stable_series() -> None:
    """A nonconstant zero-mean series has infinite, not zero, CoV."""
    assert _population_cov([-1.0, 1.0]) == math.inf


def test_manual_window_resolves_against_the_last_frame_timestamp() -> None:
    """A valid explicit window on a recording retains its inclusive bounds."""
    frames = [_frame(80.0, {}), _frame(100.0, {})]

    selection = select_report_window(frames, window_spec="last:30s")

    assert selection.mode == "last"
    assert selection.window == WindowRange(start_ts=70.0, end_ts=100.0)


@pytest.mark.parametrize(
    ("frames", "current_idx"),
    [
        ([_frame(100.0, {})], 0),
        ([_frame(100.0, {"worker.scope": {}})], 0),
        (
            [
                _frame(100.0, {"worker.scope": {"io_r_bps": _derived_counter(10)}}),
                _frame(100.0, {"worker.scope": {"io_r_bps": _derived_counter(20)}}),
            ],
            1,
        ),
    ],
)
def test_rate_derivation_fails_closed_without_a_usable_counter_interval(
    frames: list[Frame], current_idx: int,
) -> None:
    """Missing entities/metrics and nonpositive elapsed time never yield a rate."""
    assert _derive_rate("io_r_bps", "worker.scope", frames, current_idx) is None


def test_raw_counter_without_derived_provenance_is_not_reported_as_a_rate() -> None:
    """Only explicitly derived counters are eligible for rate reconstruction."""
    frames = [
        _frame(100.0, {"worker.scope": {"io_r_bps": MetricValue(None, "exact", raw=10)}}),
        _frame(105.0, {"worker.scope": {"io_r_bps": MetricValue(None, "exact", raw=20)}}),
    ]

    assert compute_profile(frames) == []


def test_rate_only_group_profiles_derived_counter_samples() -> None:
    """A group with no gauges still exposes its derived rate statistics."""
    frames = [
        _frame(100.0, {"worker.scope": {"io_r_bps": _derived_counter(100)}}),
        _frame(105.0, {"worker.scope": {"io_r_bps": _derived_counter(160)}}),
    ]

    profiles = compute_profile(frames)

    assert len(profiles) == 1
    assert profiles[0].gauges == {}
    assert profiles[0].rates == {"io_r_bps": {"p50": 12.0, "p95": 12.0, "max": 12.0}}


def test_empty_reader_result_is_rejected_not_serialized_as_a_clean_report(monkeypatch: pytest.MonkeyPatch) -> None:
    """A completed report requires at least one decoded frame, independent of I/O."""
    class EmptyReader:
        def __init__(self, path: object) -> None:
            self.path = path

        def iter_frames(self):
            return iter(())

    monkeypatch.setattr(report, "RecordReader", EmptyReader)

    with pytest.raises(ValueError, match=r"recording contains no frames: memory-recording"):
        compute_report_with_selection("memory-recording")  # type: ignore[arg-type]
