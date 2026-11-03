"""IOB-R2-01 (group B): boundary and state oracles for ``iobench.series``."""
import dataclasses

import pytest

from iobench import series
from iobench.series import KNEE_BAND, MIN_POINTS, Reading, reduce_series


def pts(values, dt=1.0, t0=0.0):
    return [(t0 + i * dt, v) for i, v in enumerate(values)]


def test_a_reading_is_immutable():
    reading = reduce_series(pts([100.0] * 10))
    with pytest.raises(dataclasses.FrozenInstanceError):
        reading.steady = 1.0


def test_exactly_the_minimum_number_of_points_is_enough():
    assert reduce_series(pts([100.0] * MIN_POINTS)).state == "flat"
    with pytest.raises(series.TooFewPoints):
        reduce_series(pts([100.0] * (MIN_POINTS - 1)))


def test_a_sample_exactly_at_the_warmup_boundary_is_discarded():
    kept = series.discard_warmup([(5.0, 1.0), (5.5, 2.0), (6.0, 3.0)], 5.0)
    assert kept == [(5.5, 2.0), (6.0, 3.0)]


def test_a_smoothed_sample_exactly_at_the_knee_band_edge_is_inside_the_band():
    edge = 100.0 + KNEE_BAND * 100.0
    assert abs(edge - 100.0) == KNEE_BAND * 100.0  # the constructed edge is exact
    assert series._knee_index([edge] * 10, 100.0) == 0


def test_a_band_that_covers_only_the_last_tenth_of_the_series_has_no_knee():
    smooth = [500.0] * 9 + [100.0]  # k = 9 = 0.9 x 10
    assert series._knee_index(smooth, 100.0) is None
    assert series._knee_index([500.0] * 8 + [100.0, 100.0], 100.0) == 8


def test_a_series_exactly_sixty_seconds_long_is_assessed_for_rate_limiting():
    assert series.rate_limited(pts([100.0] * 13, dt=5.0)) is True  # t = 0 .. 60
    assert series.rate_limited(pts([100.0] * 12, dt=5.0)) == "not_assessed"  # 55 s


def test_a_sample_exactly_sixty_seconds_before_the_last_is_part_of_the_window():
    values = [1000.0] + [100.0] * 8
    points = [(0.0, values[0])] + [(53.0 + i, 100.0) for i in range(7)] + [(60.0, 100.0)]
    assert series.rate_limited(points) is False  # the wild first sample is inside the final window


def test_a_final_window_of_exactly_the_minimum_samples_is_assessed():
    points = [(-1.0, 7.0)] + [(53.0 + i, 100.0) for i in range(MIN_POINTS)]
    assert series.rate_limited(points) is True
    points = [(-1.0, 7.0)] + [(54.0 + i, 100.0) for i in range(MIN_POINTS - 1)]  # one sample fewer in the final window
    assert series.rate_limited(points) == "not_assessed"


def test_an_all_zero_window_is_not_rate_limited():
    assert series.rate_limited(pts([0.0] * 13, dt=5.0)) is False


def test_a_leading_raw_sample_exactly_on_the_band_edge_does_not_move_the_knee():
    values = [100.0 * (1 + KNEE_BAND)] + [100.0] * 19
    assert reduce_series(pts(values)).state == "flat"
    values = [100.0 * (1 + KNEE_BAND) + 1.0] + [100.0] * 19
    assert reduce_series(pts(values)).state == "steady"  # one above the edge: a (tiny) leading burst


def test_a_tail_cv_of_exactly_the_unsteady_limit_is_still_steady():
    tail = [9.0] * 10
    tail[2] = tail[7] = 14.0  # mean 10, pstdev 2: cv = 0.2 exactly; isolated spikes, symmetric: no trend
    reading = reduce_series(pts([9.0] * 30 + tail))
    assert reading.tail_cv == series.UNSTEADY_CV and reading.state == "flat"
    tail[2] = tail[7] = 20.0
    assert reduce_series(pts([9.0] * 30 + tail)).state == "unsteady"


def test_a_tail_trend_of_exactly_the_unsteady_limit_is_still_certified():
    assert reduce_series(pts([100.0] * 5 + [90.0, 100.0, 110.0])).state == "flat"  # |slope x span| / steady = 20 / 100 = 0.2
    assert reduce_series(pts([100.0] * 5 + [89.0, 100.0, 111.0])).state == "unsteady"


def test_an_unsteady_series_reports_no_knee_and_no_burst_even_when_a_band_exists():
    tail = [9.0] * 10
    tail[2] = tail[7] = 20.0
    reading = reduce_series(pts([30.0] * 3 + [9.0] * 27 + tail))
    assert reading.state == "unsteady" and reading.knee_s is None and reading.burst_units == 0.0


def test_the_mean_overstates_only_beyond_the_ratio():
    reading = Reading("steady", 1.0, 2.0, 0.0, 0.0, 10.0, False, 0.0, 10)
    assert series.mean_overstates(reading, 2.0) is False
    assert series.mean_overstates(reading, 1.99) is True


def test_downsample_keeps_a_series_within_the_limit_whole_and_samples_a_longer_one_evenly():
    five = pts([1.0, 2.0, 3.0, 4.0, 5.0])
    assert series.downsample(five, 5) == [[t, v] for t, v in five]
    assert series.downsample(five, 6) == [[t, v] for t, v in five]
    assert series.downsample(five, 2) == [[0.0, 1.0], [2.0, 3.0]]
    assert series.downsample([], 3) == []
