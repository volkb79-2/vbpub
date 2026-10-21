"""IOB-03 P3 review B (measurement, estimators, derivations, proof model): one run-level test per finding, each red without its fix."""
import copy
import json

import pytest

from helpers import CREATED, INVERSION, READ_MISS, WRITE_14, calibrate, config, make_host, matrix, read_json
from iobench import cache, calibrate as cal_mod, ceilings, derive, guard, ladder, plausibility, proposals as P, result as res, shape
from iobench.errors import Refusal
from iobench.series import IntegrityError, SeriesError, reduce_series, rate_limited
from iobench.stages import StageSkipped
from iobench.testing import GIB, Scenario, DirScenario, FakeHost
from test_sta import PLAIN_READ, PLAIN_WRITE, key_reasons, run_cal, seed_history, seeded

STATE = "/var/lib/iobench"


def cal_of(out):
    return out.calibration["calibration"]


def check_of(out, check_id):
    return next(c for c in out.result["quality"]["checks"] if c["id"] == check_id)


# -- B-B1: the burst is integrated over the RAW series ---------------------------------------------------------------
def gst(seconds, burst_s=2, burst=2e9, steady=14.68e6):
    return reduce_series([(float(t), burst if t <= burst_s else steady) for t in range(1, seconds + 1)])


def test_rev_b_b1_burst_bytes_are_the_raw_integral():
    r = gst(240)
    assert r.burst_units == pytest.approx(3.97e9, rel=0.01)  # the true burst, not the smoothed 2.98e9
    proof = cache.write_time_proof(r, 1)
    assert proof.burst_bytes == pytest.approx(3.97e9, rel=0.01) and not proof.proven


@pytest.mark.parametrize("seconds,proven", [(240, False), (500, False), (540, False), (560, True), (600, True)])
def test_rev_b_b1_gstammtisch_proof_boundaries(seconds, proven):
    # after-knee bytes must reach 2 x the true burst (7.94e9): ~543 s, not the ~405 s the smoothed integral allowed
    assert cache.write_time_proof(gst(seconds), 1).proven is proven


def test_rev_b_b1_a_one_sample_burst_is_a_burst():
    r = reduce_series([(float(t), 5e9 if t == 1 else 14.68e6) for t in range(1, 241)])
    assert r.state == "steady" and r.knee_s > 0 and r.burst_units == pytest.approx(5e9 - 14.68e6, rel=0.01)
    proof = cache.write_time_proof(r, 1)
    assert not proof.proven and proof.after_knee_bytes < 2 * proof.burst_bytes
    # noise inside the band never makes a burst out of a flat series
    flat = reduce_series([(float(t), 1000.0 * (1.05 if t % 2 else 0.95)) for t in range(1, 241)])
    assert flat.state == "flat" and flat.burst_units <= 0.06 * 1000.0 * 2


def test_rev_b_b1_run_level_one_sample_burst_in_the_write_observation():
    """A burst of one 1 s sample at the start of the 240 s write observation: its median-smoothed integral is zero, so the old code
    called the observation flat and proven; the raw integral makes it unproven."""
    t_obs = {}
    sc = dict(iops_max=500_000, lat_us=440, seq_io_ratio=1.0)
    host = seeded(write=sc, ceiling=lambda t: 1.0 if t < t_obs.get("t", 1e18) + 1.5 else 0.00717)
    real = host.write_range

    def wr(argv, target, offset, length):
        if any("-obs-write" in a for a in argv):
            t_obs.setdefault("t", host.now())
        return real(argv, target, offset, length)

    host.write_range = wr
    out = calibrate(host)
    proof = cal_of(out)["cache"]["write"]["time_proof"]
    assert proof["burst_bytes"] > 1.5e9 and proof["proven"] is False
    status, reasons, _ = key_reasons(out.result, "caps.WIOPS")
    assert status == "unproven" and "short_observation" in reasons


# -- B-B2: an unsteady cold probe is not a rate ------------------------------------------------------------------------
def probe(state="steady", rate_limited=True, shape_="unimodal", cls="miss-like", r=9800.0):
    return cache.ColdProbe(r, rate_limited, shape_, cls, 760.0, 32 * GIB, state)


def test_rev_b_b2_classify_needs_a_certified_cold_state():
    assert cache.classify_cache(probe(), None, cliff_bytes=None, largest_rung=GIB).state == "rate_cap"
    for state in ("unsteady",):
        for rl in (True, False):
            s = cache.classify_cache(probe(state, rl), None, cliff_bytes=None, largest_rung=GIB)
            assert (s.state, s.status, s.reasons) == ("undetermined", "unproven", ("cache_model_undetermined",)) and s.r is None
    assert cache.classify_cache(probe("flat", False), None, cliff_bytes=None, largest_rung=GIB).state == "cold_measured"


def test_rev_b_b2_rate_limited_is_judged_on_the_final_window():
    flat_then_moves = [(float(t), 100.0 if t <= 58 else 150.0) for t in range(1, 66)]
    assert rate_limited(flat_then_moves) is False  # flat for the first 60 s, but the reported tail is not
    assert rate_limited([(float(t), 100.0) for t in range(1, 66)]) is True
    drift = [(float(t), 100.0 + 0.4 * t) for t in range(1, 66)]
    assert rate_limited(drift) is False
    assert rate_limited([(float(t), 100.0) for t in range(1, 30)]) == "not_assessed"
    # a sparse final window (fewer than 8 points) is not assessed, never a limiter (N10)
    assert rate_limited([(0.0, 100.0), (60.0, 100.0), (70.0, 100.0), (80.0, 100.0)]) == "not_assessed"


def test_rev_b_b2_run_level_a_drifting_cold_probe_is_not_proven():
    t_cold = {}
    host = seeded(ceiling=lambda t: max(0.1, 1 - 0.9 * (t - t_cold["t"]) / 70) if t_cold.get("t") and t_cold["t"] <= t <= t_cold["t"] + 70 else 1.0)
    real = host.spawn

    def sp(argv):
        if any(a.endswith("-ecold") or "-ecold " in a or a.startswith("--name=") and a.endswith("-ecold") for a in argv):
            t_cold.setdefault("t", host.now())
        return real(argv)

    host.spawn = sp
    out = calibrate(host)
    cold = cal_of(out)["cache"]["read"]["e_cold"]
    assert t_cold and cold["state"] == "unsteady"
    c = cal_of(out)["cache"]["read"]
    assert (c["cache_state"], c["status"]) == ("undetermined", "unproven") and "cache_model_undetermined" in c["reasons"]
    status, reasons, _ = key_reasons(out.result, "caps.RIOPS")
    assert status == "unproven" and "cache_model_undetermined" in reasons


# -- B-B3: the cap and its proof are one series ---------------------------------------------------------------------------
def test_rev_b_b3_a_burst_that_outlives_the_30s_runs_does_not_set_the_cap():
    """Writes run at twice the sustained rate for 60 s after the observation starts: the 30 s fresh/rewrite runs before it sit in the burst
    and report ~2L, the 240 s observation reports L and is time-proven.  The cap must be L, not 2L."""
    t_obs = {}
    host = seeded(write=dict(PLAIN_WRITE), ceiling=lambda t: 2.0 if t < t_obs.get("t", 1e18) + 60 else 1.0)
    real = host.write_range

    def wr(argv, target, offset, length):
        if any("-obs-write" in a for a in argv):
            t_obs.setdefault("t", host.now())
        return real(argv, target, offset, length)

    host.write_range = wr
    out = calibrate(host)
    wb = cal_of(out)["cap_basis"]["write"]
    inputs = wb["inputs"]
    assert inputs["rewrite_iops"] > 1.6 * inputs["obs_write_steady"]  # the 30 s runs did sit inside the burst
    assert wb["value"] == min(v for v in inputs.values() if v is not None) and wb["value"] == pytest.approx(inputs["obs_write_steady"], rel=0.02)
    assert wb["value"] < 1.2 * 71584


def test_rev_b_b3_observation_runs_at_the_depth_of_the_cap():
    host = seeded()
    out = calibrate(host)
    depths = {j.name.split("-obs-")[1]: j.iodepth for j in cal_mod_jobs(host) if "-obs-" in j.name}
    assert depths == {"write": 32, "read": 32, "fresh": 32}
    assert cal_of(out)["cap_basis"]["write"]["inputs"]["obs_fresh_steady"] > 0


def cal_mod_jobs(host):
    from helpers import fio_jobs
    return fio_jobs(host)


# -- B-B4: the write basis is proven only with every input present and unmoved ------------------------------------------------
def write_basis(out):
    return cal_of(out)["cap_basis"]["write"]


def test_rev_b_b4_1_a_missing_fresh_observation_is_not_proven(monkeypatch):
    def broken(self, seconds):
        raise SeriesError("the fresh observation failed")

    monkeypatch.setattr(cal_mod.Calibrator, "fresh_cell_obs", broken)
    out = run_cal()[1]
    wb = write_basis(out)
    assert cal_of(out)["fresh_vs_rewrite"]["measured"] and cal_of(out)["cache"]["write"]["fresh_proof"] is None
    assert wb["status"] == "unproven" and "short_observation" in wb["reasons"]
    assert key_reasons(out.result, "caps.WIOPS")[0] == "unproven"


def test_rev_b_b4_2_an_unmeasured_fresh_penalty_is_unproven_with_the_real_reason(monkeypatch):
    # (a) the target has no discard: the stage is skipped (never "failed"), the cap says why it lacks the fresh half
    host = seeded()
    host.devices["/dev/vda7"].discard_supported = False
    out = calibrate(host)
    assert out.calibration["run"]["stages"]["fresh-vs-rewrite"] == "skipped: discard_unsupported"
    wb = write_basis(out)
    assert wb["status"] == "unproven" and "fresh_not_measured" in wb["reasons"]
    assert cal_of(out)["fresh_vs_rewrite"] == {"measured": False, "reason": "discard_unsupported"}
    # (b) skipped for budget -> budget_limited, and the recorded reason is the budget, not "quick_profile"
    monkeypatch.setattr(cal_mod.Calibrator, "fresh_vs_rewrite", lambda self: (_ for _ in ()).throw(StageSkipped("budget")))
    out = run_cal()[1]
    assert out.calibration["run"]["stages"]["fresh-vs-rewrite"] == "skipped: budget"
    assert "budget_limited" in write_basis(out)["reasons"] and cal_of(out)["fresh_vs_rewrite"]["reason"] == "budget"
    # (c) the stage failed: recorded as stage_failed, not quick_profile
    monkeypatch.setattr(cal_mod.Calibrator, "fresh_vs_rewrite", lambda self: (_ for _ in ()).throw(SeriesError("boom")))
    out = run_cal()[1]
    assert cal_of(out)["fresh_vs_rewrite"]["reason"] == "stage_failed" and "fresh_not_measured" in write_basis(out)["reasons"]
    # (d) anything else that left the stage unrun is recorded as skipped
    monkeypatch.setattr(cal_mod.Calibrator, "fresh_vs_rewrite", lambda self: None)
    monkeypatch.setattr(cal_mod.Calibrator, "stage", lambda self, name, fn, **kw: False if name == "fresh-vs-rewrite" else real_stage(self, name, fn, **kw))
    out = run_cal()[1]
    assert cal_of(out)["fresh_vs_rewrite"]["reason"] == "stage_skipped"
    # (e) quick profile
    assert cal_of(run_cal(profile="quick")[1])["fresh_vs_rewrite"]["reason"] == "quick_profile"


real_stage = cal_mod.Runner.stage


def test_rev_b_b4_3_a_moved_fresh_vs_rewrite_write_reference_is_unproven():
    flag = {"on": False}
    host = seeded(ceiling=lambda t: 0.5 if flag["on"] else 1.0)
    real = host.write_range
    host.write_range = lambda a, t, o, n: (flag.update(on=flag["on"] or any("-rewrite" in x and "rung" not in x for x in a)), real(a, t, o, n))[1]
    out = calibrate(host)
    moved = cal_of(out)["reference"]["moved"]
    assert moved["fresh_vs_rewrite"] and not moved["ladder"]
    status, reasons, _ = key_reasons(out.result, "caps.WIOPS")
    assert status == "unproven" and "ceiling_moved" in reasons


def test_rev_b_b4_a_read_reference_that_follows_the_read_cache_does_not_move_a_write_cap():
    # the healthy cache scenario: the rewrite evicts the read cache, so the READ reference differs; the write cap is judged by the WRITE reference
    out = run_cal()[1]
    assert key_reasons(out.result, "caps.WIOPS")[:2] == ("proven", set())


# -- B-B5: check ------------------------------------------------------------------------------------------------------------------
@pytest.fixture(scope="module")
def world():
    host = make_host(cache=INVERSION)
    return host, calibrate(host)


def do_check(host, **kw):
    return res.check(host, STATE, now=host.now(), **kw)


def test_rev_b_b5_absent_and_unknown_sections_are_not_certified(world):
    host, _ = world
    with pytest.raises(Refusal) as err:
        do_check(host, calibration=True, sections=("writeback",))
    assert err.value.condition == "section_not_in_document" and err.value.exit_code == 2
    with pytest.raises(Refusal) as err:
        do_check(host, sections=("bogus",))
    assert err.value.condition == "unknown_section" and err.value.exit_code == 2
    # an emptied section is "nothing measured", never "nothing wrong"
    h = copy.deepcopy(host)
    doc = read_json(h, f"{STATE}/result.json")
    doc["proposals"]["caps"] = {}
    h.put(f"{STATE}/result.json", json.dumps(doc))
    c = do_check(h, sections=("caps",))
    assert c.exit_code == 7 and c.sections["caps"]["absent"] == [{"key": "caps", "reason": "stage_skipped"}] and c.sections["caps"]["proven"] == 0


def test_rev_b_b5_an_unknown_status_is_never_proven(world):
    host, _ = world
    with pytest.raises(ValueError, match="unknown proposal status"):
        P.section_summary({"x": {"status": "provisional"}})
    h = copy.deepcopy(host)
    doc = read_json(h, f"{STATE}/result.json")
    doc["proposals"]["caps"]["dev_aggregate"]["RIOPS"]["status"] = "provisional"
    h.put(f"{STATE}/result.json", json.dumps(doc))
    c = do_check(h)
    assert (c.state, c.reasons, c.exit_code) == ("corrupt", ["damaged_proposals"], 5)
    for damage in (lambda d: d["proposals"].__setitem__("caps", [1]), lambda d: d["proposals"]["caps"].__setitem__("dev_aggregate", {"x": 3})):
        doc = read_json(host, f"{STATE}/result.json")
        damage(doc)
        h.put(f"{STATE}/result.json", json.dumps(doc))
        assert do_check(h, sections=("caps",)).exit_code == 5


def test_rev_b_b5_a_newer_minor_is_refused_through_the_cli(world):
    from test_cli import run
    host, _ = world
    h = copy.deepcopy(host)
    doc = read_json(h, f"{STATE}/result.json")
    h.put(f"{STATE}/result.json", json.dumps({**doc, "schema_minor": res.SCHEMA_MINOR + 1}))
    code, out, _ = run(h, "check", "--json")
    assert code == 5 and "schema_unsupported" in out
    code, _, err = run(copy.deepcopy(host), "check", "--calibration", "--section", "writeback")
    assert code == 2 and "writeback" in err


# -- B-B6: read model keys from the S6 cells inherit the read cache state --------------------------------------------------
def mixed_cells(c_io_us=80.0):
    out = []
    for d in ("read", "write"):
        for p in ("seq", "rand"):
            for bs in shape.SIZES:
                iops = 1 / (c_io_us * 1e-6 + bs * 0.25e-9)
                out.append({"id": "x", "dir": d, "pattern": p, "bs": bs, "block_state": "rewrite", "steady_iops": iops, "steady_bps": iops * bs,
                            "repeat": False, "state": "steady"})
    return out


def model_for(base_cal, *, cache_status, r_iops, largest, cells=None):
    c = copy.deepcopy(base_cal)
    c["shape"] = shape.shape_summary(cells or mixed_cells())
    c["cap_basis"]["bps"] = {d: {"value": 1e9, "status": "proven", "reasons": []} for d in ("read", "write")}
    c["cache"]["read"].update(status=cache_status, R_iops=r_iops, reasons=[] if cache_status == "proven" else ["cache_residual_possible"])
    c["ladder"]["measured_largest"]["read"] = largest
    return P.build_model(c, {})


def test_rev_b_b6_mixed_read_keys_are_scaled_by_the_cache_ratio(world):
    _, out = world
    base = cal_of(out)
    assert base["shape"]["metering_by_dir"]["read"] in ("mixed", "undetermined", "byte", "iops")
    cells = mixed_cells()
    assert shape.shape_summary(cells)["metering_by_dir"]["read"] == "mixed"
    # a disk whose reads run 3x faster in cache than the cache-free rate R = 5000
    model = model_for(base, cache_status="proven", r_iops=5000.0, largest=15000.0)
    assert model["rrandiops"]["value"] <= 5000 and model["rrandiops"]["status"] == "unproven"  # worst_from_single_run only
    assert model["rrandiops"]["inputs"]["cache_ratio"] == pytest.approx(1 / 3)
    assert model["rseqiops"]["value"] == pytest.approx(1e6 / 80.0 / 3, rel=0.02)
    unscaled = derive.r6_keys(direction="read", metering="mixed", worst_bps=1e9, worst_iops=None, c_io_seq_us=80.0, c_io_rand_us=80.0,
                              c_byte_ns=0.25, cache_ratio=1.0)
    assert unscaled["rrandiops"] > 5000 >= model["rrandiops"]["value"]
    # writes are never scaled
    assert model["wrandiops"]["inputs"]["cache_ratio"] == 1.0
    # an unproven read cache state: the keys carry cache_residual_possible instead
    unproven = model_for(base, cache_status="unproven", r_iops=None, largest=15000.0)
    assert "cache_residual_possible" in unproven["rrandiops"]["reasons"] and unproven["rrandiops"]["inputs"]["cache_ratio"] == 1.0


def test_rev_b_b6_unsteady_cells_leave_the_fit_and_metering():
    cells = mixed_cells()
    for c in cells:
        if c["dir"] == "read" and c["pattern"] == "rand" and c["bs"] in (4096, 16384):
            c["state"] = "unsteady"
            c["steady_iops"] *= 10
    summary = shape.shape_summary(cells)
    assert summary["fit"]["read-rand-rewrite"]["n"] == 3  # the unsteady cells are not fit points
    assert summary["linear_model_fit_by_dir"]["read"] in ("good", "poor")
    assert summary["metering_by_dir"]["read"] == "undetermined"  # < 4 certified random sizes: no metering claim
    assert shape.shape_summary(mixed_cells())["metering_by_dir"]["read"] == "mixed"
    assert shape.certified({"state": "flat"}) and not shape.certified({"state": "unsteady"})


# -- B-B7: PLA-07/09/17/18 are evaluated by the run ------------------------------------------------------------------------------
def test_rev_b_b7_pla_17_and_18_are_recorded_by_a_run():
    out = run_cal()[1]  # seeded history: the observation plateau differs from the history median
    c17 = check_of(out, "IOB-PLA-17")
    assert c17["status"] == "info" and "differs from the ceiling median" in c17["detail"] and c17["detail"].startswith("read:")
    c18 = check_of(out, "IOB-PLA-18")
    assert c18["status"] == "info" and c18["value"]["fresh_write_bps"] > 0 and c18["value"]["rewrite_bps"] > 0
    assert c18["value"]["rearm_ratio"] == cal_of(out)["rearm"]["rearm_ratio"]
    unseeded = run_cal(seed=False)[1]
    assert check_of(unseeded, "IOB-PLA-17")["status"] == "not_applicable"
    # history close to the observed plateau: pass
    host = seeded(seed=False)
    identity = res.s0_identity(host, guard.load_target(host, "/dev/vda7"), "/bench/w")
    digest = ceilings.identity_digest(identity)
    host.makedirs(STATE)
    for d, v in (("read", 21600 * 4096.0), ("write", 71584 * 4096.0)):
        ceilings.append(host, f"{STATE}/ceilings.jsonl", {"ts": ceilings.iso(host.now() - 3600), "dir": d, "workload": "rand4k", "plateau_bps": v,
                                                           "duration_s": 600, "source": "governor"}, digest)
    assert check_of(calibrate(host), "IOB-PLA-17")["status"] in ("pass", "info")


def test_rev_b_b7_pla_17_info_is_a_warning_not_a_degradation():
    out = run_cal()[1]
    assert check_of(out, "IOB-PLA-17")["degrades"] == []


def test_rev_b_b7_pla_18_not_applicable_without_a_fresh_cell():
    host = seeded()
    host.devices["/dev/vda7"].discard_supported = False
    c18 = check_of(calibrate(host), "IOB-PLA-18")
    assert c18["status"] == "not_applicable"


def sequential_collapse(write):
    """A host whose sequential 1 MiB write rate collapses to 100 MB/s when S6 starts (the fill ran at full speed)."""
    host = seeded(write=write)
    real = host.write_range

    def wr(argv, target, offset, length):
        if any("-s6-" in a for a in argv):
            host.scenario.write.bps_cap_seq = 1e8
        return real(argv, target, offset, length)

    host.write_range = wr
    return host


def test_rev_b_b7_pla_07_random_bytes_above_sequential_degrades_the_bps_caps():
    # random-4K writes move 293 MB/s while the sequential 1 MiB cells run at 100 MB/s, the write reference unmoved
    out = calibrate(sequential_collapse(dict(PLAIN_WRITE)))
    c07 = check_of(out, "IOB-PLA-07")
    assert c07["status"] == "fail" and "write:" in c07["detail"] and "caps" in c07["degrades"]
    status, _, reason = key_reasons(out.result, "caps.WBPS")
    assert status == "absent" and reason == "stage_failed"
    assert key_reasons(out.result, "caps.WIOPS")[0] == "proven"  # only the BPS caps are degraded


def test_rev_b_b7_pla_09_fill_far_from_sequential_rewrite_degrades_the_bps_caps():
    # a slow random path (82 MB/s) keeps PLA-07 passing; the fill ran 31x faster than the sequential rewrite
    out = calibrate(sequential_collapse(dict(PLAIN_WRITE, iops_max=20000)))
    assert check_of(out, "IOB-PLA-07")["status"] == "pass"
    c09 = check_of(out, "IOB-PLA-09")
    assert c09["status"] == "fail" and "caps" in c09["degrades"] and "unmoved" in c09["detail"]
    assert key_reasons(out.result, "caps.WBPS")[2] == "stage_failed"


def test_rev_b_b7_healthy_run_passes_pla_07_and_09():
    out = run_cal()[1]
    assert check_of(out, "IOB-PLA-07")["status"] == "pass" and check_of(out, "IOB-PLA-09")["status"] == "pass"


def test_rev_b_b7_missing_cells_are_not_applicable():
    out = run_cal(profile="quick")[1]  # no S6 anchors with the full grid
    assert check_of(out, "IOB-PLA-18")["status"] == "not_applicable"
    host = seeded()
    orig = cal_mod.Calibrator.s6
    cal_mod.Calibrator.s6 = lambda self: (_ for _ in ()).throw(SeriesError("x"))
    try:
        out = calibrate(host)
    finally:
        cal_mod.Calibrator.s6 = orig
    assert check_of(out, "IOB-PLA-07")["status"] == "not_applicable" and check_of(out, "IOB-PLA-09")["status"] == "not_applicable"


def test_rev_b_b7_pla_06_and_13_run_level_wiring():
    # PLA-06: a fio that breaks Little's law makes the rung unusable (caps absent) - run level, not only the pure function
    out = run_cal(host_kw=dict(little_factor=2.0))[1]
    c06 = check_of(out, "IOB-PLA-06")
    assert c06["status"] == "fail" and ";" in c06["detail"]  # every failing rung is listed
    assert key_reasons(out.result, "caps.RIOPS")[2] == "stage_failed"
    # PLA-13: fresh writes faster than rewrites with an unmoved reference (impossible without a cache effect)
    out = run_cal(host_kw=dict(write=dict(PLAIN_WRITE, fresh_penalty=0.5)))[1]
    assert check_of(out, "IOB-PLA-13")["status"] == "fail" and key_reasons(out.result, "caps.WIOPS")[2] == "stage_failed"


def test_rev_b_b7_worst_check_merges_directions():
    ok = plausibility.Check("IOB-PLA-07", "pass", "fine")
    bad = plausibility.Check("IOB-PLA-07", "fail", "bad", ("caps",))
    merged = cal_mod.worst_check("IOB-PLA-07", [("read", ok), ("write", bad)], "n/a")
    assert merged.status == "fail" and merged.detail == "read: fine; write: bad" and merged.degrades == ("caps",)
    assert cal_mod.worst_check("IOB-PLA-07", [], "no cells").status == "not_applicable"


# =============================================================================================================================
# SHOULD findings
# =============================================================================================================================
from iobench import devio, matrix as mx_mod, testing  # noqa: E402
from test_cov import SDA, base_model, calibrated  # noqa: E402


# -- S1 / S2: a value is not stronger than its inputs ---------------------------------------------------------------------
def test_rev_b_s1_qos_max_inherits_the_caps_budget_and_moved_reasons():
    out = run_cal()[1]
    cal = copy.deepcopy(cal_of(out))
    cal["cap_basis"]["read"]["reasons"] = ["budget_limited", "ceiling_moved", "cache_residual_possible"]
    qos = P.build_qos(cal, P.DEFAULT_TARGETS, {"history": "recorded", "write": {"seq1m": {"best": 3e9, "worst": 1e9, "median": 2e9, "n": 3}}})
    assert {"budget_limited", "ceiling_moved"} <= set(qos["max"]["reasons"]) and "cache_residual_possible" not in qos["max"]["reasons"]
    # no steady rung at all: the QoS max is absent, never computed from an unsteady figure
    cal["ladder"]["best_read_rung_iops"] = None
    assert P.build_qos(cal, P.DEFAULT_TARGETS, {"history": "recorded", "write": {"seq1m": {"best": 3e9, "worst": 1e9, "median": 2e9, "n": 3}}})["max"]["status"] == "absent"
    rungs = [{"steady_iops": 100.0, "state": "steady"}, {"steady_iops": 900.0, "state": "unsteady"}]
    assert ladder.best_read_rung(rungs) == 100.0 and ladder.best_read_rung(rungs[1:]) is None


def test_rev_b_s2_latency_values_inherit_the_ceiling_reasons():
    t_obs = {}
    host = seeded(ceiling=lambda t: 1.0 if t < t_obs.get("t", 1e18) + 100 else 0.05)  # the burst_throttle row: the write cap is unproven
    real = host.write_range
    host.write_range = lambda a, t, o, n: (t_obs.setdefault("t", host.now()) if any("-obs-write" in x for x in a) else None, real(a, t, o, n))[1]
    out = calibrate(host)
    assert key_reasons(out.result, "caps.WIOPS")[0] == "unproven"
    status, reasons, _ = key_reasons(out.result, "iocost_qos.wlat")
    assert status == "unproven" and "short_observation" in reasons
    assert key_reasons(out.result, "iocost_qos.rlat")[:2] == ("proven", set())  # the read ceiling is proven: its latency is too


# -- S3: a fresh reference immediately before S6 --------------------------------------------------------------------------
def test_rev_b_s3_shape_group_has_its_own_fresh_reference():
    from helpers import fio_jobs
    host = seeded()
    out = calibrate(host)
    cal = cal_of(out)
    assert set(cal["reference"]["cells"]) >= {"r0", "r1", "r2", "r3", "r3s", "r4"}
    names = [j.name.split("-ref-")[1] for j in fio_jobs(host) if "-ref-" in j.name]
    assert names.index("r3s-read") > names.index("r3-write") and names.index("r3s-write") < names.index("r4-read")
    assert [j.name for j in fio_jobs(host) if "-obs-" in j.name] and plan_reference_seconds() == 180
    stages_run = list(out.calibration["run"]["stages"])
    assert stages_run.index("S6") > stages_run.index("observation")


def plan_reference_seconds():
    from iobench import plan
    return plan.plan_calibration("standard", 435853320192, 1.36e9).seconds("reference")


def test_rev_b_s3_a_draining_bucket_between_rearm_and_s6_is_not_a_moved_shape_group():
    # the write path drains during the 240+120+120 s of heavy observation writes: the reference taken at the end of rearm would
    # differ from the one after S6 on a perfectly healthy bucket disk; the fresh reference before S6 does not
    flag = {"after_obs": False}
    host = seeded(cache=None, read=PLAIN_READ, write=dict(PLAIN_WRITE), ceiling=lambda t: 0.6 if flag["after_obs"] else 1.0)
    real = host.write_range

    def wr(argv, target, offset, length):
        if any("-obs-fresh" in a for a in argv):
            flag["after_obs"] = True  # the bucket has drained by the end of the observation and stays at its sustained rate
        return real(argv, target, offset, length)

    host.write_range = wr
    out = calibrate(host)
    ref = cal_of(out)["reference"]
    assert ref["moved"]["shape"] is False and ref["moved_by_dir"]["shape"] == {"read": False, "write": False}
    assert "ceiling_moved" not in key_reasons(out.result, "caps.WBPS")[1]


# -- S4: unsteady depth rungs ------------------------------------------------------------------------------------------------
def test_rev_b_s4_an_unsteady_depth_rung_voids_qd90():
    host = seeded()
    orig = cal_mod.Calibrator.rung

    def rung(self, ws, direction, qd, runtime=cal_mod.RUNG_S, tag=""):
        rec = orig(self, ws, direction, qd, runtime, tag)
        return {**rec, "state": "unsteady"} if (direction, qd, tag) == ("write", 8, "-d") else rec

    cal_mod.Calibrator.rung = rung
    try:
        out = calibrate(host)
    finally:
        cal_mod.Calibrator.rung = orig
    qd90 = cal_of(out)["ladder"]["qd90"]
    assert qd90["write"] is None and qd90["read"] is not None
    # the largest rung's QD32 figure counts as a depth rung too
    host2 = seeded()
    cal_mod.Calibrator.rung = lambda self, ws, direction, qd, runtime=cal_mod.RUNG_S, tag="": (
        lambda rec: {**rec, "state": "unsteady"} if (direction, qd, tag) in (("read", 32, ""), ("write", 32, "")) and ws == self.rung_sizes[-1] else rec)(
        orig(self, ws, direction, qd, runtime, tag))
    try:
        out2 = calibrate(host2)
    finally:
        cal_mod.Calibrator.rung = orig
    assert cal_of(out2)["ladder"]["qd90"] == {"read": None, "write": None}


# -- S5: optional estimators are not fatal ----------------------------------------------------------------------------------------
def test_rev_b_s5_an_empty_histogram_fails_the_estimator_not_the_run(monkeypatch):
    real = cache.aggregate_hist
    calls = {"n": 0}

    def empty(hists):
        calls["n"] += 1
        return [0] * devio.HIST_BINS

    monkeypatch.setattr(cal_mod.cache, "aggregate_hist", empty)
    out = run_cal()[1]
    assert out.status == "complete"
    stages_ = out.calibration["run"]["stages"]
    assert stages_["E2"] == "failed: unusable_series" and stages_["E-cold"] == "failed: unusable_series" and stages_["hit_ref"].startswith("failed")
    c = cal_of(out)["cache"]["read"]
    assert c["status"] == "unproven" and c["E2"] is None
    assert issubclass(cache.HistError, SeriesError)
    monkeypatch.setattr(cal_mod.cache, "aggregate_hist", real)
    with pytest.raises(cache.HistError, match="no latency mode"):
        cache.modes([5.0] + [0.0] * (devio.HIST_BINS - 1))


def test_rev_b_s5_a_required_stage_that_is_skipped_still_raises():
    host = make_host(cache=None)
    r = cal_mod.Calibrator(host, config())
    r.start_budget(3600)
    with pytest.raises(StageSkipped):
        r.stage("x", lambda: (_ for _ in ()).throw(StageSkipped("nothing_to_do")), required=True)
    assert r.stage("y", lambda: (_ for _ in ()).throw(StageSkipped("nothing_to_do"))) is False and r.stages["y"] == "skipped: nothing_to_do"


# -- S6 / S7: the model ------------------------------------------------------------------------------------------------------------------
def test_rev_b_s6_the_model_takes_the_worst_write_window_without_history(world):
    _, out = world
    base = copy.deepcopy(cal_of(out))
    base["shape"] = shape.shape_summary(mixed_cells())
    base["cap_basis"]["bps"] = {d: {"value": 3e9, "status": "proven", "reasons": []} for d in ("read", "write")}
    base["windows"]["write_seq1m_bps"] = [3e9, 1.2e9, 2e9]  # a draining bucket: the first cell is the highest
    model = P.build_model(base, {})
    assert model["wbps"]["inputs"]["worst_bps"] == 1.2e9
    with_history = P.build_model(base, {"write": {"seq1m": {"worst": 9e8, "median": 1e9, "best": 3e9, "n": 3}}})
    assert with_history["wbps"]["inputs"]["worst_bps"] == 9e8


def test_rev_b_s7_iops_metering_with_an_absent_iops_basis_and_history_does_not_raise(world):
    _, out = world
    base = copy.deepcopy(cal_of(out))
    iops_cells = [{**c, "steady_iops": 5000.0, "steady_bps": 5000.0 * c["bs"]} for c in mixed_cells() if c["dir"] == "write"] + \
                 [c for c in mixed_cells() if c["dir"] == "read"]
    base["shape"] = shape.shape_summary(iops_cells)
    assert base["shape"]["metering_by_dir"]["write"] == "iops"
    base["cap_basis"]["bps"] = {d: {"value": 1e9, "status": "proven", "reasons": []} for d in ("read", "write")}
    base["cap_basis"]["write"] = {"value": None, "status": "absent", "reason": "basis_unsteady"}
    history = {"write": {"rand4k": {"worst": 1e7, "median": 2e7, "best": 3e7, "n": 3}, "seq1m": {"worst": 1e8, "median": 1e8, "best": 1e8, "n": 3}}}
    model = P.build_model(base, history)
    assert model["wbps"]["status"] == "absent" and model["wbps"]["reason"] == "basis_unsteady"


# -- S8: matrix --------------------------------------------------------------------------------------------------------------------------
def test_rev_b_s8_a_rejected_buffered_read_size_fails_the_stage_not_the_comparison():
    host = calibrated()
    real = host.write_sysfs

    def reject(path, value):
        if path.endswith("max_sectors_kb") and value in ("64", "256"):
            raise OSError(22, "EINVAL")
        return real(path, value)

    host.write_sysfs = reject
    out = matrix(host)
    assert out.result["run"]["stages"]["S5"] == "failed: unusable_series"
    assert out.result["evidence"]["read_cost"] == {}


def test_rev_b_s8_the_sweep_never_decides_on_a_partial_set(monkeypatch):
    host = calibrated()
    real = mx_mod.Matrixer.fits
    need = mx_mod.sweep.cell_need_s(mx_mod.sweep.SWEEP_CELL_S)
    monkeypatch.setattr(mx_mod.Matrixer, "fits", lambda self, s: False if s == need else real(self, s))
    out = matrix(host)
    cells = out.result["evidence"]["sweep"]["cells"] if "cells" in out.result["evidence"]["sweep"] else []
    assert out.result["run"]["stages"]["S4"] == "failed: unusable_series" and "limited-by-budget" in out.result["quality"]["limits"]
    assert cells == [] and out.result["proposals"]["writeback"]["dirty_bytes"]["reasons"].count("formula_only") == 1


def test_rev_b_s8_the_knob_plan_honours_the_budget_cell_by_cell():
    host = calibrated()
    started = host.now()
    out = matrix(host, knob="nr_requests", candidates=(32, 64, 128), repeat=True, budget_minutes=3)
    assert host.now() - started < 3 * 60 + 90
    cells = out.result["evidence"]["sweep"]["cells"]
    assert any(c.get("skipped") == "budget" for c in cells) and out.result["evidence"]["sweep"]["decision"] is None
    assert "limited-by-budget" in out.result["quality"]["limits"]


def test_rev_b_s8_the_budget_counts_from_the_start_of_the_run():
    host = seeded()
    r = cal_mod.Calibrator(host, config())
    r.s0(need_workdir=False)
    host.sleep(100)
    r.start_budget(3600)
    assert r.budget.elapsed() >= 100 and r.budget.started == r.started
    r.safety.release()


def test_rev_b_s8_matrix_writeback_inherits_a_short_write_proof():
    host = calibrated()
    doc = read_json(host, f"{STATE}/calibration.json")
    doc["calibration"]["cap_basis"]["write"]["status"] = "unproven"
    doc["calibration"]["cap_basis"]["write"]["reasons"] = ["short_observation"]
    host.put(f"{STATE}/calibration.json", json.dumps(doc))
    out = matrix(host)
    assert "short_observation" in out.result["proposals"]["writeback"]["dirty_bytes"]["reasons"]


# -- S9 / S10 -----------------------------------------------------------------------------------------------------------------------------
def test_rev_b_s9_a_dry_run_never_plans_with_another_disks_rate(world):
    from iobench import dryrun
    host, _ = world
    cfg = config()
    assert dryrun.rate_source(host, cfg) == cal_of(world[1])["R_est_bps"]
    with pytest.raises(Refusal) as err:
        dryrun.rate_source(host, config(target="/dev/vdb9"))
    assert err.value.condition == "dry_run_rate_other_target" and err.value.exit_code == 2
    h = copy.deepcopy(host)
    h.devices["/dev/vda7"].partuuid = "00000000-0000-0000-0000-0000deadbeef"
    with pytest.raises(Refusal) as err:
        dryrun.rate_source(h, cfg)
    assert err.value.condition == "dry_run_rate_unusable"


def test_rev_b_s10_a_foreign_history_is_rotated_aside_before_the_run_not_refused_after_it():
    host = seeded(seed=False)
    host.makedirs(STATE)
    foreign = "sha256:" + "0" * 64
    ceilings.append(host, f"{STATE}/ceilings.jsonl", {"ts": CREATED, "dir": "read", "workload": "rand4k", "plateau_bps": 1e8, "duration_s": 600,
                                                       "source": "governor"}, foreign)
    out = calibrate(host)
    assert out.status == "complete" and out.exit_code == 0 and out.result["evidence"]["history_rotated"].startswith(f"{STATE}/ceilings.jsonl.foreign-")
    aside = out.result["evidence"]["history_rotated"]
    assert json.loads(host.read_text(aside).splitlines()[0])["identity_digest"] == foreign  # kept, not deleted
    assert json.loads(host.read_text(f"{STATE}/ceilings.jsonl").splitlines()[0])["identity_digest"] != foreign
    # a garbled header is rotated too; the matching history is left alone
    host2 = seeded(seed=False)
    host2.put(f"{STATE}/ceilings.jsonl", "not json\n")
    assert calibrate(host2).result["evidence"]["history_rotated"] is not None
    assert calibrate(host2).result["evidence"]["history_rotated"] is None


# -- S11 -------------------------------------------------------------------------------------------------------------------------------------
def test_rev_b_s11_a_flat_cold_probe_that_disagrees_with_e2_is_not_a_proven_rate_cap():
    flat = probe("steady", True)
    e2 = cache.E2("bimodal", 6.9, 9800.0 * 0.5, False, 100.0, 105.0)  # R_E2 half the flat cold rate: far outside the tolerance
    s = cache.classify_cache(flat, e2, cliff_bytes=None, largest_rung=GIB)
    assert (s.state, s.status) == ("undetermined", "unproven")
    agree = cache.E2("bimodal", 6.9, 9800.0, False, 100.0, 105.0)
    assert cache.classify_cache(flat, agree, cliff_bytes=None, largest_rung=GIB).state == "rate_cap"


# -- S12 / S13 / S14 -----------------------------------------------------------------------------------------------------------------------
def test_rev_b_s12_bps_caps_carry_the_real_reasons():
    # an unproven read cache state: the BPS cap carries the cache state's own reason, not a generic one
    out = run_cal(host_kw=dict(cache=None, read=dict(PLAIN_READ)))[1]
    assert "cache_not_excluded" in key_reasons(out.result, "caps.RBPS")[1]
    # a write cap without the fresh half: the BPS cap says so, instead of the generic short_observation
    host = seeded()
    host.devices["/dev/vda7"].discard_supported = False
    out = calibrate(host)
    wstatus, wreasons, _ = key_reasons(out.result, "caps.WBPS")
    assert wstatus == "unproven" and "fresh_not_measured" in wreasons and "short_observation" not in wreasons


def test_rev_b_s14_e2_start_timestamps_are_in_the_artefact():
    out = run_cal()[1]
    e2 = cal_of(out)["cache"]["read"]["E2"]
    assert e2["qd32_start"] > 0 and e2["qd1_start"] >= e2["qd32_start"] and cache.E2("overlapping", None, None, False, 1.0, 2.0).as_dict()["qd1_start"] == 2.0


# -- N5 ---------------------------------------------------------------------------------------------------------------------------------------
def test_rev_b_n5_a_non_finite_value_in_a_fio_log_is_a_data_integrity_failure():
    with pytest.raises(IntegrityError, match="non-finite"):
        devio.parse_rate_log("1000, 5000, 1, 4096, 0\n2000, nan, 1, 4096, 0\n", "iops")
    assert devio.parse_rate_log("1000, 5000, 1, 4096, 0\n", "iops") == [(1.0, 5000.0)]


# -- budget inside S6 / S7 ------------------------------------------------------------------------------------------------------------------
def test_rev_b_s8_s6_and_s7_stop_at_the_budget():
    host = seeded()
    real_fits = cal_mod.Calibrator.fits
    state = {"on": False}

    def fits(self, seconds):
        if state["on"] and seconds in (cal_mod.shape.CELL_S + 5, cal_mod.S7_S + 5):
            return False
        return real_fits(self, seconds)

    orig_s6, orig_s7 = cal_mod.Calibrator.s6, cal_mod.Calibrator.s7

    def s6(self):
        state["on"] = True
        try:
            return orig_s6(self)
        finally:
            state["on"] = False

    cal_mod.Calibrator.fits = fits
    cal_mod.Calibrator.s6 = s6
    try:
        out = calibrate(host)
    finally:
        cal_mod.Calibrator.fits, cal_mod.Calibrator.s6 = real_fits, orig_s6
    assert out.status == "complete" and "limited-by-budget" in out.result["quality"]["limits"]
    assert cal_of(out)["shape"]["cells"] == []


def test_rev_b_s8_s7_stops_at_the_budget():
    host = seeded()
    real_fits = cal_mod.Calibrator.fits
    cal_mod.Calibrator.fits = lambda self, seconds: False if seconds == cal_mod.S7_S + 5 else real_fits(self, seconds)
    try:
        out = calibrate(host)
    finally:
        cal_mod.Calibrator.fits = real_fits
    assert cal_of(out)["latency_curve"] == [] and "limited-by-budget" in out.result["quality"]["limits"]


def test_rev_b_b7_a_run_without_the_shape_reference_does_not_claim_an_unmoved_comparison(monkeypatch):
    real_close = cal_mod.Calibrator.close_group

    def close(self, group, tag):
        if group == "shape":
            raise SeriesError("the closing reference failed")
        return real_close(self, group, tag)

    monkeypatch.setattr(cal_mod.Calibrator, "close_group", close)
    out = run_cal()[1]
    assert cal_of(out)["shape"]["cells"] and out.calibration["run"]["stages"]["S6"].startswith("failed")
    for check_id in ("IOB-PLA-07", "IOB-PLA-09", "IOB-PLA-18"):
        assert check_of(out, check_id)["status"] == "not_applicable", check_id
