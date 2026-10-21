"""IOB-CCH: cache-state estimators (pure parts; the FakeFio fixtures are in test_cch_fixtures.py)."""
import pytest

from iobench import cache
from iobench.cache import ColdProbe, E1, E2
from iobench.devio import HIST_BINS
from iobench.series import reduce_series
from iobench.testing import mixture

GIB = 1 << 30


def hist(*components, total=100_000, sigma=0.12):
    w = mixture(components, sigma)
    counts = [0.0] * HIST_BINS
    for i, v in w.items():
        counts[i] = v * total
    return counts


def test_cch_02_modes_unimodal():
    m = cache.modes(hist((1.0, 110.0)))
    assert m.shape == "unimodal" and m.p50_us == pytest.approx(110, rel=0.1) and m.h2 is None
    assert m.as_dict()["shape"] == "unimodal"
    # two modes closer than 1.5x merge into one
    assert cache.modes(hist((0.5, 110.0), (0.5, 140.0))).shape == "unimodal"
    # a 3 % bump is below the 5 % mass floor
    assert cache.modes(hist((0.97, 110.0), (0.03, 900.0))).shape == "unimodal"


def test_cch_02_modes_bimodal_and_h2():
    m = cache.modes(hist((0.6, 110.0), (0.4, 760.0)))
    assert m.shape == "bimodal" and m.h2 == pytest.approx(0.6, abs=0.02)
    assert m.t_hit_us == pytest.approx(110, rel=0.1) and m.t_miss_us == pytest.approx(760, rel=0.1)
    assert m.valley_ratio < 0.5
    # three modes: the two heaviest are compared
    three = cache.modes(hist((0.5, 110.0), (0.3, 760.0), (0.2, 6000.0)))
    assert three.shape == "bimodal" and three.t_miss_us == pytest.approx(760, rel=0.1)


def test_cch_02_modes_overlapping_and_valley():
    m = cache.modes(hist((0.5, 110.0), (0.5, 200.0)))
    assert m.shape == "overlapping" and m.h2 is None and m.valley_ratio is not None
    # modes 3x apart but wide enough that the valley stays above 0.5 x the lower peak
    wide = cache.modes(hist((0.5, 110.0), (0.5, 300.0), sigma=0.34))
    assert wide.shape == "overlapping" and wide.valley_ratio > 0.5


def test_cch_02_empty_histogram():
    with pytest.raises(cache.HistError):
        cache.modes([0.0] * HIST_BINS)
    with pytest.raises(cache.HistError):
        cache.hist_percentile_us([0] * HIST_BINS, 50)


def test_cch_02_unimodal_classes():
    assert cache.classify_unimodal(150, 110, False) == "hit-like"
    assert cache.classify_unimodal(300, 110, False) == "miss-like"
    assert cache.classify_unimodal(200, 110, False) == "indeterminate"
    assert cache.classify_unimodal(200, 110, True) == "miss-like"
    assert cache.classify_unimodal(100, 110, True) == "hit-like"
    assert cache.classify_unimodal(900, None, False) == "indeterminate"


def test_cch_02_aggregate_hist_skips_first_fifth():
    lines = [(float(i), [1] * HIST_BINS) for i in range(10)]
    agg = cache.aggregate_hist(lines)
    assert agg[0] == 8


def flat_series(value, n=40, dt=2.0):
    return reduce_series([(dt * (i + 1), value) for i in range(n)])


def test_cch_03_e2_adjacency_and_crosscheck():
    bi = cache.modes(hist((0.6, 110.0), (0.4, 760.0)))
    e = cache.e2_estimate(20000.0, bi, 0.0, 31.0)
    assert e.shape == "bimodal" and e.r_e2 == pytest.approx(20000 * 0.4, rel=0.05) and not e.voided
    assert cache.e2_estimate(20000.0, bi, 0.0, 61.0).voided
    assert cache.e2_estimate(20000.0, bi, 0.0, 61.0).r_e2 is None
    uni = cache.modes(hist((1.0, 110.0)))
    assert cache.e2_estimate(20000.0, uni, 0.0, 10.0).r_e2 is None
    assert e.as_dict()["voided"] is False


def probe(r=9800.0, shape="unimodal", cls="miss-like", rl=False):
    return ColdProbe(r, rl, shape, cls, 760.0, 32 * GIB)


def classify(cold, e2=None, cliff=None, largest=345 * GIB, quick=False):
    return cache.classify_cache(cold, e2, cliff_bytes=cliff, largest_rung=largest, quick=quick)


def test_cch_05_cache_state_table():
    s = classify(probe())
    assert (s.state, s.status, s.r) == ("cold_measured", "proven", 9800.0)
    # E2 agrees: R = min(R_cold, R_E2); E2 overlapping or voided is not a failure
    s = classify(probe(), E2("bimodal", 6.9, 9000.0, False))
    assert s.state == "cold_measured" and s.r == 9000.0
    assert classify(probe(), E2("overlapping", None, None, False)).state == "cold_measured"
    # E2 disagrees (ratio outside [0.7, 1.43])
    s = classify(probe(), E2("bimodal", 6.9, 5000.0, False))
    assert (s.state, s.reasons, s.r) == ("undetermined", ("cache_model_undetermined",), None)
    # rate cap needs the COLD PROBE itself flat and not hit-like
    s = classify(probe(rl=True, cls="indeterminate"))
    assert (s.state, s.status) == ("rate_cap", "proven")
    assert classify(probe(rl=True, shape="bimodal", cls="n/a")).state == "rate_cap"
    s = classify(probe(rl=True, cls="hit-like"))
    assert (s.state, s.reasons) == ("cold_hit_like", ("no_cliff_hit_like_mode",))
    # hit-like with / without a cliff
    assert classify(probe(cls="hit-like"), cliff=300 * GIB).reasons == ("device_smaller_than_cache_margin",)
    assert classify(probe(cls="hit-like")).reasons == ("no_cliff_hit_like_mode",)
    # bimodal / overlapping / indeterminate cold probe
    for shape, cls in (("bimodal", "n/a"), ("overlapping", "n/a"), ("unimodal", "indeterminate")):
        assert classify(probe(shape=shape, cls=cls)).reasons == ("cache_model_undetermined",)
    # no cold probe
    assert classify(None).reasons == ("cache_residual_possible",)
    assert classify(None, quick=True).reasons == ("quick_profile",)
    # margin row: the largest rung is below 1.2 x the cliff
    s = classify(probe(), cliff=300 * GIB, largest=345 * GIB)
    assert s.status == "unproven" and s.reasons == ("device_smaller_than_cache_margin",) and s.state == "cold_measured"
    assert classify(probe(), cliff=250 * GIB, largest=345 * GIB).status == "proven"
    assert classify(probe(cls="hit-like"), cliff=300 * GIB).reasons.count("device_smaller_than_cache_margin") == 1
    assert s.as_dict()["cache_state"] == "cold_measured"


def model_points(c_gib, f, r, sizes):
    return [(g * GIB, 1 / ((c_gib / g) / f + (1 - c_gib / g) / r)) for g in sizes]


def test_cch_04_e1_good_on_the_exact_model():
    pts = model_points(100, 92000.0, 9800.0, (150, 200, 250, 300, 350, 400))
    e = cache.e1_fit(pts, 92000.0)
    assert e.fit == "good" and e.r_e1 == pytest.approx(9800, rel=0.01)
    assert e.c_bytes == pytest.approx(100 * GIB, rel=0.01) and e.margin_ok
    assert e.as_dict()["fit"] == "good"


def test_cch_04_e1_degenerate_inputs_do_not_raise():
    assert cache.e1_fit([], 9e4).fit == "insufficient"
    two = model_points(100, 92000.0, 9800.0, (300, 400))
    assert cache.e1_fit(two, 9e4).fit == "insufficient"
    noisy3 = [(300 * GIB, 9000.0), (350 * GIB, 12000.0), (400 * GIB, 8000.0)]
    assert cache.e1_fit(noisy3, 9e4).fit == "insufficient"
    non_mono = model_points(100, 92000.0, 9800.0, (150, 200, 250, 300))
    non_mono[2] = (non_mono[2][0], non_mono[2][1] * 1.5)
    assert cache.e1_fit(non_mono, 92000.0).fit == "poor"
    big_cache = model_points(500, 92000.0, 9800.0, (150, 200, 250, 300))
    assert cache.e1_fit(big_cache, 92000.0).fit == "poor"
    assert cache.e1_fit(model_points(100, 92000.0, 9800.0, (150, 200, 250, 300)), None).fit == "insufficient"
    same_x = [(300 * GIB, 9000.0), (300 * GIB, 8000.0), (300 * GIB, 7000.0), (300 * GIB, 6000.0)]
    assert cache.e1_fit(same_x, 9e4).fit == "poor"
    rising = [(g * GIB, 5000.0 * (1 + i / 3)) for i, g in enumerate((150, 200, 250, 300))]
    assert cache.e1_fit(rising, 9e4).fit == "poor"
    f_equals_r = model_points(100, 9800.0, 9800.0, (150, 200, 250, 300))
    assert cache.e1_fit(f_equals_r, 9800.0).fit in ("plateau", "poor")
    zero_pred = [(150 * GIB, 100.0), (200 * GIB, 100000.0), (250 * GIB, 50.0), (300 * GIB, 100000.0)]
    assert cache.e1_fit(zero_pred, 9e4).fit == "poor"


def test_cch_04_e1_true_plateau_not_poor():
    pts = [(g * GIB, 9800.0 * f) for g, f in ((250, 1.0), (300, 1.02), (345, 0.99))]
    assert cache.e1_fit(pts, 92000.0).fit == "plateau"
    four = [(g * GIB, 9800.0 * f) for g, f in ((250, 1.0), (300, 1.02), (345, 0.99), (400, 1.01))]
    assert cache.e1_fit(four, 92000.0).fit == "plateau"


def test_cch_04_e1_never_decides_cache_state():
    e1 = cache.e1_fit(model_points(100, 92000.0, 9800.0, (150, 200, 250, 300, 350, 400)), 92000.0)
    assert e1.fit == "good"
    s = classify(probe(cls="indeterminate"))
    assert s.state == "undetermined"  # E1 good, but the cold probe decides


def test_cch_06_value_never_exceeds_measured():
    proven = classify(probe())
    v = cache.read_value(proven, 24000.0, None)
    assert (v.value, v.status) == (9800.0, "proven")
    # E1 reporting 3 x the measured rate: the value is bounded by the measurement
    good3 = E1("good", 6, 0.99, 0.01, 72000.0, 1.0)
    unproven_no_cold = classify(None)
    assert cache.read_value(unproven_no_cold, 24000.0, good3).value == 24000.0
    assert cache.read_value(unproven_no_cold, 24000.0, E1("good", 6, 0.99, 0.01, 14600.0, 1.0)).value == 14600.0
    assert cache.read_value(unproven_no_cold, 24000.0, E1("poor", 6)).value == 24000.0
    assert cache.read_value(unproven_no_cold, 24000.0, None).reasons == ("cache_residual_possible",)
    # an unclassifiable cold probe at 9.8k with a 24k largest rung gives 9.8k
    unclass = classify(probe(shape="bimodal", cls="n/a"))
    v = cache.read_value(unclass, 24000.0, good3)
    assert v.value == 9800.0 and v.status == "unproven" and v.reasons == ("cache_model_undetermined",)
    assert cache.read_value(proven, 5000.0, None).value == 5000.0


def test_cch_07_write_time_proof():
    # (a) burst longer than the observation
    burst_long = reduce_series([(float(t), 2e9 if t <= 100 else 14.68e6) for t in range(1, 241)])
    assert not cache.write_time_proof(burst_long, 1).proven
    # (b) gstammtisch: 2 GB/s for 2 s then 14 MiB/s
    def gst(seconds):
        return reduce_series([(float(t), 2e9 if t <= 2 else 14.68e6) for t in range(1, seconds + 1)])
    short = cache.write_time_proof(gst(240), 1)
    assert not short.proven and short.reason == "short_observation"
    assert short.after_knee_bytes < 2 * short.burst_bytes
    assert cache.write_time_proof(gst(600), 1).proven
    # (c) flat 21.6k IOPS for 240 s
    flat = reduce_series([(2.0 * i, 21600.0) for i in range(1, 121)])
    p = cache.write_time_proof(flat, 4096)
    assert p.proven and p.burst_bytes == 0 and p.as_dict()["proven"]
    # (d) 100 s flat series: below 120 s after the knee
    assert not cache.write_time_proof(reduce_series([(float(t), 21600.0) for t in range(1, 101)]), 4096).proven
    # an unsteady observation is never a proof
    wild = reduce_series([(float(t), 1000.0 + (t % 2) * 3000.0 + t * 20) for t in range(1, 241)])
    assert wild.state == "unsteady" and not cache.write_time_proof(wild, 4096).proven


def test_cch_01_cold_probe_record():
    reading = flat_series(9800.0, n=40)
    uni = cache.modes(hist((1.0, 760.0)))
    cp = cache.cold_probe(reading, uni, 110.0, 32 * GIB)
    assert cp.cls == "miss-like" and cp.r_cold == 9800.0 and cp.rate_limited is True
    assert cp.as_dict()["R_cold_iops"] == 9800.0
    bi = cache.modes(hist((0.6, 110.0), (0.4, 760.0)))
    assert cache.cold_probe(reading, bi, 110.0, 1).cls == "n/a"
