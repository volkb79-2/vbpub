"""IOB-CCH: cache-state estimators and the CCH-08 fixtures."""
import json
import re

import pytest

from iobench import cache, ladder
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
    return ColdProbe(r, rl, shape, cls, 760.0, 32 * GIB, "steady")


def classify(cold, e2=None, cliff=None, largest=345 * GIB, quick=False, ref=None, excludable=True):
    return cache.classify_cache(cold, e2, cliff_bytes=cliff, largest_rung=largest, quick=quick, cached_ref=ref, cache_excludable=excludable)


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
    assert (s.state, s.status, s.r) == ("rate_cap", "proven", 9800.0)       # a flat cold probe stays rate_cap, whatever its latency class
    # hit-like with / without a cliff
    assert classify(probe(cls="hit-like"), cliff=300 * GIB).reasons == ("device_smaller_than_cache_margin",)
    assert classify(probe(cls="hit-like")).reasons == ("no_cliff_hit_like_mode",)      # no reference rate: nothing to compare with
    assert classify(probe(cls="hit-like"), ref=20000.0).reasons == ("no_cliff_hit_like_mode",)   # cold 9.8k < 0.9 x 20k: not "as fast as warm"
    # no cache observed: the cold region is as fast as the warm one, unimodal, no cliff
    n = classify(probe(cls="hit-like"), ref=9800.0)
    assert (n.state, n.status, n.reasons, n.r, n.r_cold) == ("no_cache_observed", "unproven", ("cache_not_excluded",), None, 9800.0)
    assert classify(probe(cls="indeterminate"), ref=10000.0).state == "no_cache_observed"
    assert classify(probe(cls="hit-like"), ref=10888.0).state == "no_cache_observed" and classify(probe(cls="hit-like"), ref=10890.0).state == "cold_hit_like"
    # ... but never with a cliff, on a file target, or for a non-unimodal probe
    assert classify(probe(cls="hit-like"), cliff=300 * GIB, ref=9800.0).state == "cold_hit_like"
    assert classify(probe(cls="hit-like"), ref=9800.0, excludable=False).state == "cold_hit_like"
    assert classify(probe(shape="bimodal", cls="n/a"), ref=9800.0).state == "undetermined"
    # the margin row still applies on top of the proven state
    assert classify(probe(cls="hit-like"), cliff=None, ref=9800.0, largest=1 * GIB).status == "unproven"
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


# ---------------------------------------------------------------------------------------------------------
# stage level
# ---------------------------------------------------------------------------------------------------------
import json  # noqa: E402

from helpers import INVERSION, calibrate, fio_jobs, make_host, read_json  # noqa: E402
from iobench import calibrate as cal_mod  # noqa: E402


def assert_cold_probe_first(jobs, extent_end):
    names = [j.name.split("-", 3)[-1] for j in jobs]
    first_cold = names.index("ecold")
    fill_last = max(i for i, n in enumerate(names) if n.startswith("fill-"))
    # right after the fill, before any rung, reference cell or other job touches [0, cold_len)
    assert first_cold == fill_last + 1
    after = jobs[first_cold + 2:]
    assert all(not (j.offset == 0 and "ecold" in j.name) for j in after)
    touchers = [j for j in jobs[:first_cold] if not j.writes]
    assert touchers == []  # nothing read the cold region before the probe
    cold = [j for j in jobs if j.name.split("-", 3)[-1].startswith("ecold")]
    assert all(j.end <= extent_end and not j.writes for j in cold)


def test_cch_01_cold_probe_runs_first_and_reads_written_data_only():
    host = make_host(cache=INVERSION)
    out = calibrate(host)
    cal = out.calibration["calibration"]
    jobs = fio_jobs(host)
    assert_cold_probe_first(jobs, cal["extent_written"][0][1])
    cold = [j for j in jobs if "-ecold" in j.name]
    assert [(j.iodepth, j.runtime_s, j.hist) for j in cold] == [(32, 70, False), (1, 60, True)]
    cold_len = cal["cache"]["read"]["e_cold"]["region_bytes"]
    assert cold_len == min(32 << 30, int(0.1 * cal["extent_written"][0][1])) // (1 << 20) * (1 << 20)
    assert cold[0].offset == 0 and cold[0].end == cold_len // 2 and cold[1].offset == cold_len // 2 and cold[1].end == cold_len  # disjoint halves
    # every rung and probe stores its touch map: bytes per 1/8 of the extent since the fill
    tm = cal["touch_map"]
    assert len(tm["fill"]) == 8 and sum(tm["fill"]) == pytest.approx(cal["extent_written"][0][1], rel=0.001)
    assert tm["e_cold"][0] > 0 and sum(tm["e_cold"][1:]) < tm["e_cold"][0] and "ladder" in tm
    # controlled wrong: a plan that reorders the cold probe behind the ladder fails the order check
    wrong = make_host(cache=INVERSION)
    orig = cal_mod.Calibrator.body

    def reordered(self):
        self.s0(need_workdir=False)
        self.stage("S1", self.s1, required=True)
        alloc = self.make_plan()
        self.stage("fill", lambda: self.fill(alloc), required=True)
        self.stage("ladder", self.run_ladder, required=True)       # ladder FIRST ...
        self.stage("E-cold", self.e_cold, min_s=1)                # ... cold probe behind it
        self.analyze()

    cal_mod.Calibrator.body = reordered
    try:
        calibrate(wrong)
    finally:
        cal_mod.Calibrator.body = orig
    with pytest.raises(AssertionError):
        assert_cold_probe_first(fio_jobs(wrong), 345 << 30)


def test_cch_03_e2_adjacency_and_crosscheck_in_a_run():
    host = make_host(cache=INVERSION)
    out = calibrate(host)
    e2 = out.calibration["calibration"]["cache"]["read"]["E2"]
    assert e2["shape"] in ("bimodal", "unimodal", "overlapping") and e2["voided"] is False
    jobs = fio_jobs(host)
    names = [j.name.split("-", 3)[-1] for j in jobs]
    largest = names.index("e2-qd1")
    assert names[largest - 1].startswith("rung-r32-")           # the QD1 histogram run immediately follows the QD32 read run
    qd32, qd1 = jobs[largest - 1], jobs[largest]
    assert (qd32.rw, qd32.iodepth, qd1.rw, qd1.iodepth, qd1.runtime_s, qd1.hist, qd1.end) == ("randread", 32, "randread", 1, 60, True, qd32.end)
    # a gap larger than 60 s voids E2
    slow = make_host(cache=INVERSION)
    orig = cal_mod.Calibrator.e2

    def late(self, ws, started):
        self.host.sleep(70)
        orig(self, ws, started)

    cal_mod.Calibrator.e2 = late
    try:
        o2 = calibrate(slow)
    finally:
        cal_mod.Calibrator.e2 = orig
    assert o2.calibration["calibration"]["cache"]["read"]["E2"]["voided"] is True
    assert o2.calibration["calibration"]["cache"]["read"]["E2"]["R_E2_iops"] is None


def test_cch_05_cold_probe_missing_is_residual_possible():
    host = make_host(cache=INVERSION)
    orig = cal_mod.Calibrator.e_cold

    def broken(self):
        raise cal_mod.S.TooFewPoints("no cold probe")

    cal_mod.Calibrator.e_cold = broken
    try:
        out = calibrate(host)
    finally:
        cal_mod.Calibrator.e_cold = orig
    c = out.calibration["calibration"]["cache"]["read"]
    assert out.calibration["run"]["stages"]["E-cold"].startswith("failed")
    assert c["cache_state"] == "undetermined" and c["reasons"] == ["cache_residual_possible"]
    caps = out.result["proposals"]["caps"]["dev_aggregate"]
    assert "cache_residual_possible" in caps["RIOPS"]["reasons"] and caps["RIOPS"]["status"] == "unproven"
    # a cap value never exceeds the measured largest-rung rate
    assert caps["RIOPS"]["inputs"]["basis_iops"] <= out.calibration["calibration"]["ladder"]["measured_largest"]["read"]


# ---------------------------------------------------------------------------------------------------------
# IOB-CCH-08 fixtures
# ---------------------------------------------------------------------------------------------------------
import pathlib  # noqa: E402

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


def fixture(name):
    return json.loads((FIXTURES / name).read_text())


def read_cache(out):
    return out.calibration["calibration"]["cache"]["read"]


def test_cch_08_fixture_header_matches_e1_rules_version():
    for name in ("two_tier_e1_good_but_wrong.json", "r1002_model.json", "r1002_recorded.json", "gstammtisch_tables.json", "latency_curve_synthetic.json"):
        header = fixture(name)["_provenance"]
        assert header.get("synthetic") is True or re.fullmatch(r"[0-9a-f]{64}", header["recorded"]), name
    gen = fixture("two_tier_e1_good_but_wrong.json")["_provenance"]["generator"]
    assert gen["e1_rules_version"] == cache.E1_RULES_VERSION   # a change of the E1 rules forces regenerating this fixture
    assert gen["kind"] == "two_tier" and gen["tier1_gib"] == 40 and gen["tier2_total_gib"] == 300 and gen["fill_gib"] == 345


def two_tier_ladder(gen):
    def rate(ws):
        t1, t2 = gen["tier1_gib"], gen["tier2_total_gib"]
        f1, f2, fb = min(ws, t1) / ws, (min(ws, t2) - min(ws, t1)) / ws, max(0, ws - t2) / ws
        return 1 / (f1 / gen["tier1_iops"] + f2 / gen["tier2_iops"] + fb / gen["backing_iops"])
    return [(g * GIB, rate(g)) for g in gen["rungs_gib"]]


def test_cch_08_1_two_tier_e1_good_but_wrong():
    doc = fixture("two_tier_e1_good_but_wrong.json")
    pts = two_tier_ladder(doc["_provenance"]["generator"])
    assert [round(v, 1) for _, v in pts] == [r["steady_iops"] for r in doc["ladder"]]       # the stored ladder IS the generator's
    post_cliff = [p for p in pts if p[0] >= 64 * GIB]
    e1 = cache.e1_fit(post_cliff, 92000.0)
    assert e1.fit == "good" and e1.r2 == pytest.approx(0.977, abs=0.003) and e1.loo_max <= 0.15
    assert e1.r_e1 >= 1.3 * 9800.0 and e1.r_e1 == pytest.approx(14.6e3, rel=0.03) and e1.c_bytes / GIB == pytest.approx(41, abs=4)
    cold = doc["cold_probe"]
    state = classify(ColdProbe(cold["r_cold"] * 1.03, False, cold["shape"], cold["cls"], 760.0, 32 * GIB, "steady"))
    assert state.state == "cold_measured" and state.status == "proven" and state.r == pytest.approx(9800, rel=0.05)
    value = cache.read_value(state, 15429.0, e1)
    assert value.status == "proven" and value.value == pytest.approx(9800, rel=0.05) and value.value < e1.r_e1 / 1.3   # the cap comes from R_cold, never R_E1
    # controlled wrong: a derivation that trusted E1 over the cold probe would be ~49 % too high
    assert e1.r_e1 / 9800.0 - 1 >= 0.30


def test_cch_08_2_fill_history_inversion():
    host = make_host(cache=INVERSION)
    out = calibrate(host)
    ladder = out.calibration["calibration"]["ladder"]
    rungs = sorted(ladder["rungs"], key=lambda r: r["ws_bytes"])

    assert ladder["cliff_bytes"] is None and 9000 < ladder["cached_ref_iops"] < 20000
    c = read_cache(out)
    assert c["cache_state"] == "cold_measured" and c["status"] == "proven" and 0.7 * 9800 <= c["R_iops"] <= 1.06 * 9800   # min(R_cold, R_E2); E2 = S x (1 - h2) is conservative here (IMPLEMENTATION-NOTES)
    cap = out.result["proposals"]["caps"]["dev_aggregate"]["RIOPS"]
    assert 0.7 * 9800 <= cap["inputs"]["basis_iops"] <= 1.06 * 9800


def test_cch_08_3_read_alloc_cold_ladder():
    host = make_host(cache=dict(capacity_gib=265, hit_iops=41800, write_allocate=False, read_allocate=True, read_alloc_share=1.0))
    out = calibrate(host)
    rungs = sorted((r for r in out.calibration["calibration"]["ladder"]["rungs"] if r["dir"] == "read"), key=lambda r: r["ws_bytes"])
    assert rungs[0]["steady_iops"] > 3 * rungs[-1]["steady_iops"] and rungs[0]["steady_iops"] > 30000
    c = read_cache(out)
    assert c["E1"]["fit"] in ("poor", "insufficient") and c["cache_state"] == "cold_measured" and c["status"] == "proven"


def test_cch_08_4_cache_larger_than_fill():
    # The cold region is cache-resident and as fast as the warm region; with no cliff that is what "no cache observed" means
    # (IOB-CCH-05). A cache at least as large as the fill is physically indistinguishable from no cache at all by rate or latency,
    # so this scenario and the no-cache one classify identically: no_cache_observed, unproven: cache_not_excluded.
    c = read_cache(calibrate(make_host(cache=dict(capacity_gib=500, hit_iops=92000, write_allocate=True))))
    assert (c["cache_state"], c["status"], c["reasons"]) == ("no_cache_observed", "unproven", ["cache_not_excluded"])
    none = read_cache(calibrate(make_host(cache=None, read=dict(iops_max=21600, lat_us=300))))
    assert (none["cache_state"], none["status"], none["reasons"]) == (c["cache_state"], c["status"], c["reasons"])     # indistinguishable, honestly
    assert c["R_cold_iops"] > 80000 and none["R_cold_iops"] < 30000 and c["R_iops"] is None
    # cold_hit_like needs a hit-like probe that cannot be called "as fast as warm": a cliff exists, or the rate is below the reference
    table = classify(probe(cls="hit-like"), cliff=300 * GIB, ref=9800.0)
    assert (table.state, table.status) == ("cold_hit_like", "unproven")


def test_cch_08_5_flat_rate_cap():
    # a bare flat provider cap on a disk with no cache: the cold probe is flat -> rate_cap, proven
    capped = dict(iops_max=3600, lat_us=8000, lat_hit_us=110)
    c = read_cache(calibrate(make_host(cache=None, read=capped, noise=0.001)))
    assert (c["cache_state"], c["status"], c["reasons"]) == ("rate_cap", "proven", [])
    assert c["R_iops"] == pytest.approx(3600, rel=0.02)
    # no cache and not flat -> no_cache_observed, unproven: cache_not_excluded; the value is min(measured_largest, R_cold)
    plain = read_cache(calibrate(make_host(cache=None, read=dict(iops_max=21600, lat_us=300))))
    assert (plain["cache_state"], plain["status"], plain["reasons"]) == ("no_cache_observed", "unproven", ["cache_not_excluded"])
    assert plain["read_value"]["value"] == pytest.approx(plain["R_cold_iops"], rel=0.05)
    for cls in ("miss-like", "indeterminate"):
        s = classify(probe(rl=True, cls=cls))
        assert (s.state, s.status) == ("rate_cap", "proven")
    s = classify(probe(rl=True, cls="hit-like"))
    assert (s.state, s.status) == ("rate_cap", "proven")


def test_cch_08_6_e2_disagrees():
    s = classify(probe(), E2("bimodal", 6.9, 9800.0 * 0.5, False))
    assert (s.state, s.reasons) == ("undetermined", ("cache_model_undetermined",))
    assert classify(probe(), E2("bimodal", 6.9, 9800.0 / 1.44, False)).state == "undetermined"
    assert classify(probe(), E2("bimodal", 6.9, 9800.0 / 1.42, False)).state == "cold_measured"


def test_cch_08_7_overlapping_modes_e2():
    s = classify(probe(), E2("overlapping", 1.8, None, False))
    assert (s.state, s.status) == ("cold_measured", "proven")      # E2 is a cross-check, not a gate


def test_cch_08_8_e1_degenerate_inputs():
    f = 92000.0
    full = model_points(100, f, 9800.0, (150, 200, 250, 300))
    cases = {"non_monotone": full[:2] + [(full[2][0], full[2][1] * 1.5)] + full[3:], "C_ge_smallest_rung": model_points(500, f, 9800.0, (150, 200, 250, 300)),
             "two_rungs": full[:2], "three_rungs_noise": [(300 * GIB, 9000.0), (350 * GIB, 12000.0), (400 * GIB, 8000.0)]}
    for name, pts in cases.items():
        e = cache.e1_fit(pts, f)
        assert e.fit in ("poor", "insufficient"), name
        assert classify(probe()).state == "cold_measured", name      # the cold probe decides, whatever E1 says
    assert cache.e1_fit(cases["two_rungs"], f).fit == "insufficient" and cache.e1_fit(cases["non_monotone"], f).fit == "poor"


def test_cch_08_9_true_plateau():
    pts = [(g * GIB, 9800.0 * x) for g, x in ((250, 1.0), (300, 1.03), (345, 0.98), (400, 1.01))]
    assert cache.e1_fit(pts, 92000.0).fit == "plateau"
    assert cache.e1_fit([(g * GIB, 9800.0 * x) for g, x in ((250, 1.0), (300, 1.2), (345, 0.98))], 92000.0).fit != "plateau"


def test_cch_08_10_recorded_r1002():
    doc = fixture("r1002_recorded.json")
    rungs = [{"ws_bytes": r["ws_gib"] * GIB, "steady_iops": float(r["steady_iops"])} for r in doc["rungs_le_0_85_usable"]]
    assert [r["ws_gib"] for r in doc["rungs_le_0_85_usable"]] == [1, 16, 100, 150, 200, 250, 300]
    cliff, ref, classed = ladder.classify_rungs(rungs)
    assert 250 * GIB <= cliff <= 300 * GIB and ref == pytest.approx(93083, rel=0.01)
    post = [(r["ws_bytes"], r["steady_iops"]) for r in classed if r["class"] == "post_cliff"]
    assert cache.e1_fit(post, ref).fit == "insufficient"
    s = cache.classify_cache(None, None, cliff_bytes=cliff, largest_rung=300 * GIB)
    assert s.state == "undetermined" and "cache_residual_possible" in s.reasons and s.status == "unproven"
    assert cache.read_value(s, 46315.0, None).reasons[0] == "cache_residual_possible"


def test_cch_08_11_r_wrong_3x():
    # a cache-free rate 3x too high is bounded by the measurement and flagged by the cold probe
    state = classify(probe(r=9800.0))
    assert cache.read_value(state, 9800.0 * 3, E1("good", 6, 0.99, 0.01, 29400.0, 1.0)).value == 9800.0
    undetermined = classify(None)
    assert cache.read_value(undetermined, 24000.0, E1("good", 6, 0.99, 0.01, 72000.0, 1.0)).value == 24000.0


def test_cch_08_12_largest_rung_flat_cold_slower():
    s = classify(probe(r=9800.0), cliff=None, largest=345 * GIB)
    flat_largest = 24000.0
    v = cache.read_value(s, flat_largest, None)
    assert s.state == "cold_measured" and v.value == 9800.0 and v.value != flat_largest
    none = cache.read_value(classify(None), flat_largest, None)
    assert none.status == "unproven" and none.reasons == ("cache_residual_possible",)
