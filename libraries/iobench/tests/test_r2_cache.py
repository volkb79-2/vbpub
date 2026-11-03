"""IOB-R2-01: boundary and single-failure oracles for the cache-state estimators (cache.py)."""
import dataclasses
import math

import pytest

from iobench import cache
from iobench.cache import ColdProbe
from iobench.devio import HIST_BINS, bin_value_us
from iobench.series import Reading

GIB = 1 << 30


def spikes(**by_bin):
    """A histogram with the given counts in single bins (keys are ``b<bin>``)."""
    counts = [0.0] * HIST_BINS
    for key, count in by_bin.items():
        counts[int(key[1:])] = float(count)
    return counts


@pytest.fixture
def equal_bin_width(monkeypatch):
    """Bins of equal log width, so equal counts mean equal density (the real widths differ by a few per cent per bin)."""
    monkeypatch.setattr(cache, "_LOG_WIDTH", [1.0] * HIST_BINS)


def reading(**kw):
    base = dict(state="steady", steady=1000.0, mean=1000.0, knee_s=30.0, burst_units=60000.0, observation_s=150.0, rate_limited=False, tail_cv=0.0, n=10)
    base.update(kw)
    return Reading(**base)


# -- the records are immutable evidence -----------------------------------------------------------------------------------------------
def _records():
    cold = ColdProbe(1000.0, False, "unimodal", "miss-like", 700.0, GIB, "steady")
    e2 = cache.e2_estimate(1000.0, cache.modes(spikes(b99=95, b199=5)), 0.0, 10.0)
    return {
        "Modes": cache.modes(spikes(b99=100)),
        "ColdProbe": cold,
        "E2": e2,
        "E1": cache.e1_fit([(GIB, 2.0), (2 * GIB, 1.5)], 2.0),
        "CacheState": cache.classify_cache(cold, None, cliff_bytes=None, largest_rung=GIB),
        "ReadValue": cache.read_value(cache.classify_cache(cold, None, cliff_bytes=None, largest_rung=GIB), 900.0, None),
        "TimeProof": cache.write_time_proof(reading(), 4096),
    }


@pytest.mark.parametrize("name", ["Modes", "ColdProbe", "E2", "E1", "CacheState", "ReadValue", "TimeProof"])
def test_r2_cache_estimator_records_cannot_be_modified_after_the_fact(name):
    record = _records()[name]
    field = dataclasses.fields(record)[0].name
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(record, field, None)


# -- peak picking -----------------------------------------------------------------------------------------------------------------------
def test_r2_cache_peaks_are_strict_local_maxima_with_the_last_bin_of_a_plateau_winning():
    assert cache._peaks([0.0, 1.0, 1.0, 0.0]) == [2]
    assert cache._peaks([0.0, 1.0, 3.0, 2.0, 0.0]) == [2]
    assert cache._peaks([0.0, 0.0]) == []


def test_r2_cache_peaks_include_both_ends_when_they_are_higher_than_their_one_neighbour():
    assert cache._peaks([1.0, 0.0, 5.0]) == [0, 2]
    assert cache._peaks([5.0, 0.0, 1.0]) == [0, 2]
    assert cache._peaks([2.0, 1.0, 2.0]) == [0, 2]
    assert cache._peaks([3.0, 1.0, 0.0]) == [0]
    assert cache._peaks([1.0, 1.0]) == [1]


# -- modes ------------------------------------------------------------------------------------------------------------------------------
def test_r2_cache_an_all_zero_histogram_is_refused_as_empty_not_as_modeless():
    with pytest.raises(cache.HistError, match="empty histogram"):
        cache.modes([0.0] * HIST_BINS)


def test_r2_cache_a_mode_with_exactly_the_mass_floor_is_kept():
    m = cache.modes(spikes(b99=95, b199=5))
    assert m.shape == "bimodal" and m.masses == (0.95, 0.05) and m.masses[1] == cache.MASS_MIN
    below = cache.modes(spikes(b99=96, b199=4))
    assert below.shape == "unimodal" and len(below.modes_us) == 1


def test_r2_cache_modes_closer_than_the_overlap_gap_merge_and_exactly_at_it_stay_two(monkeypatch):
    assert bin_value_us(120) == cache.OVERLAP_GAP * bin_value_us(80)  # peaks at bins 80 and 120 (the last bin of each spike's plateau)
    exactly = cache.modes(spikes(b79=50, b119=50))
    assert exactly.shape == "overlapping" and exactly.modes_us == (bin_value_us(80), bin_value_us(120))
    closer = cache.modes(spikes(b79=50, b115=50))
    assert closer.shape == "unimodal"


def test_r2_cache_two_merging_modes_keep_the_denser_one_and_the_faster_one_on_a_tie(equal_bin_width):
    m = cache.modes(spikes(b79=50, b89=50))
    assert m.shape == "unimodal" and m.modes_us == (bin_value_us(80),)
    lower = cache.modes(spikes(b79=60, b89=40))
    assert lower.modes_us == (bin_value_us(80),)
    higher = cache.modes(spikes(b79=40, b89=60))
    assert higher.modes_us == (bin_value_us(90),)


def test_r2_cache_a_gap_and_a_valley_exactly_at_their_limits_still_count_as_bimodal(monkeypatch, equal_bin_width):
    m = cache.modes(spikes(b99=95, b199=5))
    gap = m.modes_us[1] / m.modes_us[0]
    assert m.shape == "bimodal" and m.valley_ratio == 0.0
    monkeypatch.setattr(cache, "BIMODAL_GAP", gap)
    assert cache.modes(spikes(b99=95, b199=5)).shape == "bimodal"
    monkeypatch.setattr(cache, "BIMODAL_GAP", math.nextafter(gap, math.inf))
    assert cache.modes(spikes(b99=95, b199=5)).shape == "overlapping"
    monkeypatch.setattr(cache, "BIMODAL_GAP", 2.5)
    # a valley exactly at its limit: two spikes with a shallow dip, the limit set to the measured ratio
    shallow = [0.0] * HIST_BINS
    shallow[99] = shallow[199] = 50.0
    shallow[100:199] = [2.0] * 99  # a floor between the two peaks
    ratio = cache.modes(shallow).valley_ratio
    assert 0 < ratio < 1
    monkeypatch.setattr(cache, "VALLEY_MAX", ratio)
    assert cache.modes(shallow).shape == "bimodal"
    monkeypatch.setattr(cache, "VALLEY_MAX", math.nextafter(ratio, -math.inf))
    assert cache.modes(shallow).shape == "overlapping"


# -- classification of one mode ------------------------------------------------------------------------------------------------------
def test_r2_cache_unimodal_classes_are_inclusive_at_both_limits():
    assert cache.classify_unimodal(150.0, 100.0, False) == "hit-like"
    assert cache.classify_unimodal(math.nextafter(150.0, math.inf), 100.0, False) == "indeterminate"
    assert cache.classify_unimodal(250.0, 100.0, False) == "miss-like"
    assert cache.classify_unimodal(math.nextafter(250.0, 0.0), 100.0, False) == "indeterminate"


# -- E2 --------------------------------------------------------------------------------------------------------------------------------
def test_r2_cache_e2_adjacent_means_a_gap_of_up_to_and_including_sixty_seconds():
    qd1 = cache.modes(spikes(b99=95, b199=5))
    assert cache.e2_estimate(1000.0, qd1, 100.0, 160.0).voided is False
    assert cache.e2_estimate(1000.0, qd1, 100.0, 160.0).r_e2 is not None
    assert cache.e2_estimate(1000.0, qd1, 100.0, 160.5).voided is True


def test_r2_cache_the_e2_tolerance_band_is_closed_at_both_ends():
    assert cache._within(0.7) and cache._within(1.43)
    assert not cache._within(math.nextafter(0.7, 0.0)) and not cache._within(math.nextafter(1.43, 2.0))


# -- E1 --------------------------------------------------------------------------------------------------------------------------------
def model(a, b, sizes):
    """Post-cliff rungs on ``1/iops = a + b/ws`` with ws in GiB."""
    return [(w * GIB, 1.0 / (a + b / w)) for w in sizes]


def test_r2_cache_e1_a_flat_series_is_a_plateau_up_to_and_including_ten_per_cent_spread():
    top = 1.10 * 1.0
    assert cache.e1_fit([(GIB, 1.0), (2 * GIB, 1.0), (4 * GIB, top)], 2.0).fit == "plateau"
    assert cache.e1_fit([(GIB, 1.0), (2 * GIB, 1.0), (4 * GIB, math.nextafter(top, 2.0))], 2.0).fit == "insufficient"


def test_r2_cache_e1_a_perfect_fit_with_the_knee_below_the_smallest_rung_is_good():
    e1 = cache.e1_fit(model(1.0, -0.25, [1, 2, 4, 8]), 2.0)
    assert e1.fit == "good" and e1.c_bytes == 0.5 * GIB and e1.r_e1 == 1.0 and e1.margin_ok is True


def test_r2_cache_e1_a_knee_exactly_at_the_smallest_rung_is_not_below_it():
    e1 = cache.e1_fit(model(1.0, -0.5, [1, 2, 4, 8]), 2.0)
    assert e1.c_bytes == GIB and e1.r2 == 1.0 and e1.fit == "poor"


def test_r2_cache_e1_a_knee_above_the_smallest_rung_is_poor_although_the_fit_is_perfect():
    e1 = cache.e1_fit(model(1.0, -0.9, [1, 2, 4, 8]), 2.0)
    assert e1.r2 == pytest.approx(1.0) and e1.loo_max < 1e-9 and e1.c_bytes == pytest.approx(1.8 * GIB) and e1.fit == "poor"


def test_r2_cache_e1_a_negative_knee_is_poor_and_has_no_margin():
    e1 = cache.e1_fit(model(1.0, -0.25, [1, 2, 4, 8]), 0.5)  # a cached rate below the asymptote: the knee would be negative
    assert e1.c_bytes < 0 and e1.fit == "poor" and e1.margin_ok is None


def test_r2_cache_e1_margin_needs_the_largest_rung_to_reach_the_margin_times_the_knee_inclusive(monkeypatch):
    pts = model(1.0, -0.5, [1, 2, 4, 8])  # knee 1 GiB, largest rung 8 GiB
    monkeypatch.setattr(cache, "MARGIN", 8.0)
    assert cache.e1_fit(pts, 2.0).margin_ok is True
    monkeypatch.setattr(cache, "MARGIN", 8.5)
    assert cache.e1_fit(pts, 2.0).margin_ok is False


def test_r2_cache_e1_a_rung_series_may_rise_five_per_cent_per_step_and_no_more():
    sizes = [1, 2, 8, 12]
    flat = [1.947931, 1.484409, 1.184299]
    at_limit = [(w * GIB, y) for w, y in zip(sizes, flat + [1.05 * flat[-1]])]  # the last rung is exactly 5 % above its predecessor
    over = [(w * GIB, y) for w, y in zip(sizes, flat + [math.nextafter(1.05 * flat[-1], math.inf)])]
    assert cache.e1_fit(at_limit, 2.0).fit == "good"
    assert cache.e1_fit(over, 2.0).fit == "poor"


def test_r2_cache_e1_each_acceptance_rule_alone_downgrades_a_fit_to_poor():
    one_off = {
        # only the monotone rule fails (a rung rises by 5.07 %)
        "monotone": [(2.0, 2.6577), (3.0, 2.0537), (6.0, 1.6496), (8.0, 1.7332), (16.0, 1.5451), (24.0, 1.5298), (32.0, 1.5076)],
        # only the knee-below-smallest-rung rule fails (knee 2.3e9 bytes against a smallest rung of 1 GiB)
        "knee": [(1.0, 7.2089), (2.0, 1.2406), (4.0, 0.8774), (8.0, 0.7653), (12.0, 0.7341), (24.0, 0.7053)],
        # only the asymptote-below-every-rung rule fails
        "asymptote": [(1.0, 0.6753), (6.0, 0.5291), (8.0, 0.524), (12.0, 0.5166), (24.0, 0.5127), (32.0, 0.5027)],
        # only r2 >= 0.97 fails
        "r2": [(1.0, 1.1107), (2.0, 0.9675), (3.0, 0.8589), (12.0, 0.8352), (32.0, 0.8116)],
        # only leave-one-out <= 15 % fails
        "loo": [(1.0, 1.3672), (4.0, 0.8434), (6.0, 0.787), (8.0, 0.7848)],
    }
    cached = {"monotone": 5.326919130607403, "knee": 1.1604045819901907, "asymptote": 1.6731811945142852, "r2": 2.2284877001027197, "loo": 2.5428893002849393}
    got = {name: cache.e1_fit([(w * GIB, y) for w, y in pts], cached[name]) for name, pts in one_off.items()}
    for name, e1 in got.items():
        assert e1.fit == "poor" and e1.r_e1 is not None, name
    assert got["monotone"].r2 >= 0.97 and got["monotone"].loo_max <= 0.15 and 0 < got["monotone"].c_bytes < 2 * GIB
    assert got["knee"].c_bytes > GIB and got["knee"].r2 >= 0.97 and got["knee"].loo_max <= 0.15
    assert got["asymptote"].r_e1 > 0.5027 and got["asymptote"].r2 >= 0.97 and got["asymptote"].loo_max <= 0.15
    assert got["r2"].r2 < 0.97 and got["r2"].loo_max <= 0.15
    assert got["loo"].loo_max > 0.15 and got["loo"].r2 >= 0.97


def test_r2_cache_e1_a_series_that_gets_faster_with_a_smaller_rung_is_poor_without_a_rate():
    rising = cache.e1_fit([(GIB, 1.0), (2 * GIB, 1.2), (4 * GIB, 1.5), (8 * GIB, 2.0)], 2.0)  # slower with size: no cache saturation curve
    assert rising.fit == "poor" and rising.r_e1 is None and rising.c_bytes is None and rising.margin_ok is None


def test_r2_cache_e1_a_series_with_exactly_no_slope_is_poor_without_a_rate():
    flat_fit = cache.e1_fit([(GIB, 1.0), (2 * GIB, 0.125), (4 * GIB, 0.5), (8 * GIB, 1.0)], 2.0)  # not a plateau, yet the fitted slope is exactly 0
    assert flat_fit.r2 == 0.0 and flat_fit.fit == "poor" and flat_fit.r_e1 is None and flat_fit.c_bytes is None


def test_r2_cache_e1_a_leave_one_out_line_that_cannot_be_drawn_or_goes_negative_makes_the_error_infinite():
    # three rungs of the same size: leaving out the fourth leaves a vertical line
    duplicated = [(GIB, 3.0), (GIB, 2.0), (GIB, 2.5), (2 * GIB, 1.0)]
    assert cache.e1_fit(duplicated, 2.0).loo_max == math.inf
    # leaving out the smallest rung extrapolates the line of the others below zero
    steep = [(1.0, 10.0), (2.0, 2.0), (4.0, 1.0 / 0.9), (8.0, 1.0 / 0.95)]
    e1 = cache.e1_fit([(w * GIB, y) for w, y in steep], 2.0)
    assert e1.loo_max == math.inf and e1.fit == "poor"
    # leaving out the smallest rung predicts exactly zero (1/iops = 16 - 16/ws through the others): that too is infinite, not a division by zero
    on_zero = [(GIB, 1.0)] + [(w * GIB, 1.0 / v) for w, v in zip([2, 4, 8, 16], [8, 12, 14, 15])]
    assert cache.e1_fit(on_zero, 2.0).loo_max == math.inf


# -- classification --------------------------------------------------------------------------------------------------------------------
def cold(r_cold=1000.0, *, shape="unimodal", cls="hit-like", rate_limited=False, state="steady"):
    return ColdProbe(r_cold, rate_limited, shape, cls, 100.0, GIB, state)


def test_r2_cache_a_cold_region_as_fast_as_the_warm_one_without_a_cliff_is_no_cache_observed_by_default():
    state = cache.classify_cache(cold(900.0), None, cliff_bytes=None, largest_rung=GIB, cached_ref=1000.0)
    assert state.state == "no_cache_observed" and state.status == "unproven" and state.reasons == ("cache_not_excluded",)
    assert cache.NO_CACHE_RATIO * 1000.0 == 900.0  # exactly at the ratio still counts
    just_below = cache.classify_cache(cold(math.nextafter(900.0, 0.0)), None, cliff_bytes=None, largest_rung=GIB, cached_ref=1000.0)
    assert just_below.state == "cold_hit_like" and just_below.reasons == ("no_cliff_hit_like_mode",)


def test_r2_cache_a_file_target_cannot_exclude_a_cache_so_the_same_probe_stays_hit_like():
    state = cache.classify_cache(cold(900.0), None, cliff_bytes=None, largest_rung=GIB, cached_ref=1000.0, cache_excludable=False)
    assert state.state == "cold_hit_like"


def test_r2_cache_the_rung_must_reach_the_margin_times_the_cliff_inclusive():
    cliff = 5 * GIB
    assert cache.MARGIN * cliff == 6 * GIB
    probe = cold(500.0, cls="miss-like")
    at = cache.classify_cache(probe, None, cliff_bytes=cliff, largest_rung=6 * GIB)
    assert at.status == "proven" and at.reasons == ()
    under = cache.classify_cache(probe, None, cliff_bytes=cliff, largest_rung=6 * GIB - 1)
    assert under.status == "unproven" and under.reasons == ("device_smaller_than_cache_margin",)


# -- write time proof ----------------------------------------------------------------------------------------------------------------
def test_r2_cache_a_write_proof_needs_120_seconds_after_the_knee_inclusive():
    on = cache.write_time_proof(reading(knee_s=30.0, observation_s=150.0, burst_units=60000.0), 4096)
    assert on.proven is True and on.reason is None and on.after_knee_bytes == 2 * on.burst_bytes
    short = cache.write_time_proof(reading(knee_s=30.0, observation_s=149.0, burst_units=1.0), 4096)
    assert short.proven is False and short.reason == "short_observation"


def test_r2_cache_a_write_proof_needs_twice_the_burst_bytes_after_the_knee_inclusive():
    on = cache.write_time_proof(reading(knee_s=30.0, observation_s=230.0, burst_units=100000.0), 4096)
    assert on.proven is True and on.after_knee_bytes == 2 * on.burst_bytes
    under = cache.write_time_proof(reading(knee_s=30.0, observation_s=230.0, burst_units=100000.0 + 1), 4096)
    assert under.proven is False and under.reason == "short_observation"
