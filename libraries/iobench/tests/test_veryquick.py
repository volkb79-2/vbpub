"""IOB-PRF-03 (the veryquick profile), IOB-PLA-19 (its cache-inflation test) and the plausibility verdict."""
import json
import re

import pytest

from iobench import calibrate as cal_mod, derive, dryrun, plan, plausibility, proposals as P, result as res, shape
from iobench.devio import parse_job
from iobench.errors import Refusal
from iobench.testing import GIB

from helpers import INVERSION, calibrate, config, fio_jobs, make_host
from test_tgt import file_host

STATE = "/var/lib/iobench"
RUN_ID = re.compile(r"run-\d{8}T\d{6}Z-[0-9a-f]+")
MDT016_READ = dict(iops_max=1_000_000, lat_us=60, bps_cap_seq=4.3e9, bps_cap_rand=4.3e9)  # a 4.3 GB/s read ...
MDT016_WRITE = dict(iops_max=4000, lat_us=440, bps_cap_seq=20e6, bps_cap_rand=20e6)  # ... from a 20 MB/s disk


def vq(host=None, **kw):
    host = host or make_host(cache=None)
    started = host.now()
    return host, calibrate(host, profile="veryquick", **kw), host.now() - started


@pytest.fixture(scope="module")
def clean():
    return vq()


def values(out, section):
    return dict(P.iter_values(out.result["proposals"][section]))


# -- the plan ---------------------------------------------------------------------------------------------------------
def test_prf_03_the_plan_fits_180_seconds_inside_a_210_second_budget():
    alloc = plan.plan_calibration("veryquick", 435853320192, 1.36e9)
    assert [s.name for s in alloc.stages] == ["S0", "S1", "fill", "E-cold", "series", "S6"]
    assert [s.allotted_s for s in alloc.stages] == [10, 20, 25, 40, 30, 48] and alloc.total == 173 <= 180
    assert alloc.budget_s == plan.PROFILE_BUDGET_S["veryquick"] == 210 >= alloc.total
    assert [s.name for s in alloc.stages if s.required] == ["S0", "S1", "fill", "series", "S6"]  # the cold probe is the one optional stage
    # the working set is what 20 s of fill writes at the measured rate, or the target's 85 %
    assert alloc.ws_max == int(1.36e9 * plan.VQ_FILL_S) and alloc.rungs == (alloc.ws_max,)
    assert plan.plan_calibration("veryquick", 4 * GIB, 1.36e9).ws_max == int(0.85 * (4 * GIB - (1 << 20)))
    assert plan.plan_calibration("veryquick", 435853320192, 1.36e9, 99).budget_s == 99
    # the budget numbers are the quick profile's order of magnitude down: 12 minutes -> 3 minutes
    assert plan.PROFILE_BUDGET_S["quick"] == 720 > 3 * plan.PROFILE_BUDGET_S["veryquick"]


def test_prf_03_veryquick_refuses_to_overwrite_a_full_calibration():
    host = make_host(cache=INVERSION)
    full = calibrate(host)
    assert full.status == "complete"
    before_calibration = host.read_text(f"{STATE}/calibration.json")
    before_result = host.read_text(f"{STATE}/result.json")
    jobs_before = len(fio_jobs(host))
    with pytest.raises(Refusal) as err:
        calibrate(host, profile="veryquick")
    assert err.value.condition == "veryquick_state_exists" and "--state-dir" in err.value.remedy
    assert host.read_text(f"{STATE}/calibration.json") == before_calibration
    assert host.read_text(f"{STATE}/result.json") == before_result and len(fio_jobs(host)) == jobs_before
    isolated_state = f"{STATE}/veryquick"
    isolated = calibrate(host, profile="veryquick", state_dir=isolated_state)
    assert isolated.status == "complete"
    assert calibrate(host, profile="veryquick", state_dir=isolated_state).status == "complete"
    assert host.read_text(f"{STATE}/calibration.json") == before_calibration
    assert host.read_text(f"{STATE}/result.json") == before_result


def test_prf_03_veryquick_refuses_to_replace_an_unreadable_state_file():
    host = make_host(cache=None)
    host.makedirs(STATE)
    host.put(f"{STATE}/calibration.json", "{truncated")
    with pytest.raises(Refusal) as err:
        calibrate(host, profile="veryquick")
    assert err.value.condition == "veryquick_state_exists" and "separate --state-dir" in err.value.remedy
    assert not fio_jobs(host)


def test_prf_03_a_run_takes_the_planned_time_and_ends_complete(clean):
    host, out, elapsed = clean
    assert (out.status, out.exit_code) == ("complete", 0)
    assert 150 <= elapsed <= 180  # simulated seconds: the fio runs add up to what the plan says (173) and stay inside the budget
    assert out.result["run"]["stages"] == {"S0": "ok", "S1": "ok", "fill": "ok", "E-cold": "ok", "series": "ok", "S6": "ok",
                                           "ladder": "skipped: very-quick-profile", "S7": "skipped: very-quick-profile"}
    assert out.result["run"]["profile"] == "veryquick" and out.result["run"]["budget_s"] == 210
    jobs = [j.name.split("-", 3)[-1] for j in fio_jobs(host)]
    assert jobs[:1] == ["s1"] and jobs[-4:] == ["s6-0", "s6-1", "s6-2", "s6-3"] and {"ecold", "ecold-qd1", "hitref", "rung-w32-%d-vq" % (out.calibration["calibration"]["extent_written"][0][1] // (1 << 20))} <= set(jobs)
    assert not any(n.startswith(("rung-r", "obs-", "fresh", "e2", "ref-", "s7")) for n in jobs)  # no ladder, no observation runs, no S7


def test_prf_03_every_value_a_veryquick_run_makes_is_unproven_and_says_why(clean):
    _, out, _ = clean
    assert out.result["quality"]["limits"] == ["limited-by-budget", "stage_skipped:S7", "very-quick-profile", "single-run"]
    assert all(P.limit_ok(x) for x in out.result["quality"]["limits"])
    for section in ("caps", "iocost_model"):
        for key, v in values(out, section).items():
            assert v["status"] in ("unproven", "absent"), (section, key)
            if v["status"] == "unproven":
                assert "veryquick_profile" in v["reasons"], (section, key)
    qos = values(out, "iocost_qos")
    assert {k: v["status"] for k, v in qos.items() if k in ("max", "wlat", "rlat")} == {"max": "absent", "wlat": "absent", "rlat": "absent"}
    assert {qos[k]["reason"] for k in ("wlat", "rlat")} == {"stage_skipped"}  # QoS needs the latency curve, which a veryquick run never takes
    assert {v["reason"] for v in values(out, "writeback").values()} == {"stage_skipped"}
    # the sustained plateau, the observation runs and the history are out of reach, so nothing proven can come from them
    assert out.calibration["calibration"]["plateau_bps"] == {} and out.calibration["calibration"]["latency_curve"] == []


def test_prf_03_the_model_is_a_coarse_formula_estimate_of_the_four_cells(clean):
    _, out, _ = clean
    cal = out.calibration["calibration"]
    coarse = cal["coarse_model"]
    assert set(coarse) == {"read", "write"} and all(set(v) == {"seq_bps", "rand_iops"} for v in coarse.values())
    cells = {(c["dir"], c["pattern"], c["bs"]): c for c in cal["shape"]["cells"]}
    assert set(cells) == {("read", "rand", 4096), ("write", "rand", 4096), ("read", "seq", 131072), ("write", "seq", 131072)}
    model = values(out, "iocost_model")
    for d, p in (("read", "r"), ("write", "w")):
        v = model[f"{p}bps"]
        assert v["value"] == int(cells[(d, "seq", 131072)]["steady_bps"]) == int(coarse[d]["seq_bps"])
        assert model[f"{p}randiops"]["value"] == int(cells[(d, "rand", 4096)]["steady_iops"])
        assert model[f"{p}seqiops"]["value"] == int(coarse[d]["seq_bps"] // 4096)
        for k in ("bps", "seqiops", "randiops"):
            assert model[f"{p}{k}"]["reasons"] == ["veryquick_profile", "formula_only"] and model[f"{p}{k}"]["basis"] == "formula-only"
            assert model[f"{p}{k}"]["inputs"]["metering"] == "coarse"
            assert derive.recompute("R6-model", model[f"{p}{k}"]["inputs"]) == model[f"{p}{k}"]["value"]  # IOB-DER-07: a pure function of its inputs


def test_prf_03_a_cell_that_is_not_certified_leaves_that_direction_without_a_model(monkeypatch):
    real = shape.certified
    monkeypatch.setattr(shape, "certified", lambda c: c["dir"] != "write" and real(c))  # no write cell is steady enough to enter a model
    _, out, _ = vq()
    assert out.status == "complete" and out.calibration["calibration"]["coarse_model"]["write"] is None
    assert out.calibration["calibration"]["coarse_model"]["read"] is not None
    model = values(out, "iocost_model")
    assert {model[k]["status"] for k in ("wbps", "wseqiops", "wrandiops")} == {"absent"} and {model[k]["reason"] for k in ("wbps", "wseqiops", "wrandiops")} == {"stage_failed"}
    assert model["wbps"]["inputs"]["coarse"] == "cell_missing"
    assert {model[k]["status"] for k in ("rbps", "rseqiops", "rrandiops")} == {"unproven"}


def test_prf_03_a_veryquick_result_is_current_but_unproven_and_adds_no_history(clean):
    host, out, _ = clean
    c = res.check(host, STATE, now=host.now())
    assert (c.state, c.exit_code) == ("current_unproven", 7) and "very-quick-profile" in c.limits
    assert c.sections["iocost_model"]["proven"] == 0 and c.sections["caps"]["proven"] == 0
    # the 15-30 s windows of a veryquick run are no sustained plateau (>= 60 s): the ceiling history is untouched
    assert not host.exists(f"{STATE}/ceilings.jsonl") and c.history == "single-run"
    cc = res.check(host, STATE, calibration=True, now=host.now())
    assert cc.state == "current_unproven"


def test_prf_03_matrix_may_follow_a_veryquick_calibration_like_a_quick_one(clean):
    from helpers import matrix
    import copy
    host, _, _ = clean
    out = matrix(copy.deepcopy(host))
    assert (out.status, out.exit_code) == ("complete", 0)


# -- the shortened stages ----------------------------------------------------------------------------------------------
def test_prf_03_the_cold_probe_reads_on_the_cold_tenth_and_its_reference_on_the_last_gib_written(clean):
    host, out, _ = clean
    cal = out.calibration["calibration"]
    ws = cal["extent_written"][0][1]
    by = {j.name.split("-", 3)[-1]: j for j in fio_jobs(host)}
    cold_len = (min(cal_mod.COLD_MAX, int(cal_mod.COLD_SHARE * ws)) // (1 << 20)) * (1 << 20)
    half = (cold_len // 2 // (1 << 20)) * (1 << 20)
    assert (by["ecold"].iodepth, by["ecold"].runtime_s, by["ecold"].offset, by["ecold"].size) == (32, 15, 0, half)
    assert (by["ecold-qd1"].iodepth, by["ecold-qd1"].runtime_s, by["ecold-qd1"].offset, by["ecold-qd1"].size) == (1, 15, half, cold_len - half)
    assert (by["hitref"].iodepth, by["hitref"].runtime_s, by["hitref"].offset, by["hitref"].size) == (1, 10, ws - GIB, GIB)
    sampled = [j for n, j in by.items() if n in ("ecold", "ecold-qd1", "hitref") or n.startswith(("s6-", "rung-w32"))]
    assert len(sampled) == 8 and all(j.log_avg_ms == 500 for j in sampled)  # 500 ms samples: a 12 s cell still has 8 points after its warm-up
    assert cal["cache"]["read"]["e_cold"]["region_bytes"] == cold_len


def test_prf_03_the_series_is_one_30_second_write_run_and_the_grid_is_four_cells(clean):
    host, out, _ = clean
    ws = out.calibration["calibration"]["extent_written"][0][1]
    jobs = fio_jobs(host)
    series = [j for j in jobs if j.name.endswith("-vq")]
    assert len(series) == 1 and (series[0].rw, series[0].iodepth, series[0].runtime_s, series[0].offset, series[0].size) == ("randwrite", 32, 30, 0, ws)
    cells = [j for j in jobs if re.search(r"-s6-\d$", j.name)]
    assert [(j.rw, j.bs, j.runtime_s, j.iodepth) for j in cells] == [("randread", 4096, 12, 32), ("randwrite", 4096, 12, 32), ("read", 131072, 12, 32), ("write", 131072, 12, 32)]
    assert all((j.offset, j.size) == (0, ws) for j in cells)
    assert [(c.dir, c.pattern, c.bs, c.seconds) for c in shape.vq_grid(12, 131072)] == [("read", "rand", 4096, 12), ("write", "rand", 4096, 12), ("read", "seq", 131072, 12),
                                                                                         ("write", "seq", 131072, 12)]
    # the S1 rate probe is the standard one; the fill is 20 s of data in two 10 s chunks
    assert [j.name.split("-", 3)[-1] for j in jobs if "-fill-" in j.name] == ["fill-0", "fill-1"]


def test_prf_03_the_write_series_shows_a_burst_that_ends_inside_it():
    t0 = {}
    host = make_host(cache=None, ceiling=lambda t: 1.0 if t < t0.get("t", 1e18) + 8 else 0.2)
    real = host.write_range

    def write_range(argv, target, offset, length):
        if any(a.endswith("-vq") or "-vq" in a for a in argv):
            t0.setdefault("t", host.now())
        return real(argv, target, offset, length)

    host.write_range = write_range
    _, out, _ = vq(host)
    seen = out.calibration["calibration"]["veryquick"]
    assert seen["write_burst_seen"] is True and seen["write_state"] == "steady" and seen["write_knee_s"] == 8.0 and seen["series_s"] == 30
    flat = vq()[1].calibration["calibration"]["veryquick"]
    assert flat["write_burst_seen"] is False and flat["write_state"] == "flat"
    # an unsteady series claims no burst either way
    noisy = make_host(cache=None)
    noisy.scenario.noise_cv = 0.5
    assert vq(noisy)[1].calibration["calibration"]["veryquick"]["write_burst_seen"] is False


# -- the plausibility verdict ------------------------------------------------------------------------------------------
def verdict_of(out):
    return out.result["quality"]["plausibility"]


def test_prf_03_a_cache_inflated_disk_fails_the_plausibility_verdict():
    """The 4.3 GB/s read from a 20 MB/s disk (MDT-016): the sequential read is 200x the sequential write."""
    _, out, _ = vq(make_host(cache=None, read=MDT016_READ, write=MDT016_WRITE))
    assert out.status == "complete" and verdict_of(out)["verdict"] == "fail"
    check = next(c for c in out.result["quality"]["checks"] if c["id"] == "IOB-PLA-19")
    assert check["status"] == "fail" and check["value"] > 150 and "a cache is serving the reads" in check["detail"]
    assert all(v["status"] != "proven" for s in ("caps", "iocost_model") for v in values(out, s).values())


def test_prf_03_a_measured_cold_region_passes_and_an_undecidable_one_is_inconclusive():
    small = dict(capacity_gib=5, hit_iops=92000, write_allocate=True)  # the last GiB written is cached, the cold tenth is not
    _, out, _ = vq(make_host(cache=small))
    assert verdict_of(out) == {"verdict": "pass", "cache_state": "cold_measured"}
    assert out.calibration["calibration"]["cache"]["read"]["e_cold"]["class"] == "miss-like"
    assert next(c for c in out.result["quality"]["checks"] if c["id"] == "IOB-PLA-19")["status"] == "pass"
    # no ladder: a cold region that reads like the reference cannot tell "no cache" from "a cache bigger than 20 s of writes"
    assert verdict_of(vq()[1]) == {"verdict": "inconclusive", "cache_state": "cold_hit_like"}
    assert verdict_of(vq(make_host(cache=INVERSION))[1])["verdict"] == "inconclusive"


def test_prf_03_without_the_cold_probe_the_verdict_is_inconclusive_and_the_run_still_completes():
    _, out, _ = vq(budget_minutes=70 / 60)  # 70 s: the 40 s cold probe does not fit after S1 and the fill (40 s)
    assert out.status == "complete" and out.result["run"]["stages"]["E-cold"] == "skipped: budget"
    assert {"series", "S6"} <= {k for k, v in out.result["run"]["stages"].items() if v == "ok"}  # the required stages still run
    assert verdict_of(out) == {"verdict": "inconclusive", "cache_state": "undetermined"}
    assert out.calibration["calibration"]["cache"]["read"]["e_cold"] is None
    read = values(out, "caps")["RIOPS"]
    assert read["status"] == "unproven" and "veryquick_profile" in read["reasons"]


def test_pla_19_boundaries():
    assert plausibility.pla_19(None, 1.0).status == "not_applicable" and plausibility.pla_19(1.0, None).status == "not_applicable"
    assert plausibility.pla_19(20.0, 1.0).status == "pass"  # exactly 20x is not above it
    assert plausibility.pla_19(15.0, 1.0).status == "pass"
    assert plausibility.pla_19(50.0, 1.0).status == "info"
    assert plausibility.pla_19(100.0, 1.0).status == "info"
    assert plausibility.pla_19(150.0, 1.0).status == "fail"
    assert plausibility.pla_19(1.0, 20.0).status == "pass"  # a write faster than the read is no sign of a read cache
    assert plausibility.pla_19(10.0, 1.0).value == 10.0
    assert plausibility.READ_OVER_WRITE_WARN == 20.0 and plausibility.READ_OVER_WRITE_FAIL == 100.0


def test_prf_03_the_verdict_table():
    expect = {"cold_measured": "pass", "rate_cap": "pass", "cold_hit_like": "fail", "no_cache_observed": "inconclusive", "undetermined": "inconclusive"}
    assert plausibility.VERDICTS == expect and set(expect) == set(P.CACHE_STATES)
    for state, verdict in expect.items():
        assert plausibility.verdict(state) == {"verdict": verdict, "cache_state": state}
    # veryquick: a hit-like cold region is inconclusive alone; a 20-100x ratio is corroborated by a hit-like cold probe.
    assert plausibility.verdict("cold_hit_like", vq=True)["verdict"] == "inconclusive"
    assert plausibility.verdict("cold_measured", vq=True)["verdict"] == "pass"
    assert plausibility.verdict("cold_measured", vq=True, rate_status="info")["verdict"] == "inconclusive"
    assert plausibility.verdict("cold_hit_like", vq=True, rate_status="info")["verdict"] == "fail"
    assert plausibility.verdict("undetermined", vq=True, rate_status="fail")["verdict"] == "fail"


def test_prf_03_every_calibration_carries_the_verdict_of_its_cache_state():
    host = make_host(cache=INVERSION)
    out = calibrate(host)
    assert out.result["quality"]["plausibility"] == plausibility.verdict(out.calibration["calibration"]["cache"]["read"]["cache_state"])
    assert out.calibration["quality"]["plausibility"] == out.result["quality"]["plausibility"]
    quick = calibrate(make_host(cache=INVERSION), profile="quick")
    assert quick.result["quality"]["plausibility"] == {"verdict": "inconclusive", "cache_state": "undetermined"}  # no cold probe in a quick run


# -- failure modes -----------------------------------------------------------------------------------------------------
def test_prf_03_a_faked_fio_leaves_no_read_rung_and_fails_the_run():
    host = make_host(cache=None, little_factor=3.0)
    out = calibrate(host, profile="veryquick")
    assert out.status == "failed" and "no usable ladder rung" in out.message
    assert out.result["quality"]["limits"] is not None and "plausibility" not in out.result["quality"]
    assert host.read_text(f"{STATE}/result.last-failed.json") and not host.exists(f"{STATE}/result.json")


def test_prf_03_a_file_target_uses_the_veryquick_fill_budget_and_no_fresh_extent():
    host = file_host()
    out = calibrate(host, target="/bench/target.img", work_dir="/bench/w", profile="veryquick")
    assert out.status == "complete" and out.result["identity"]["target"]["kind"] == "file"
    assert {"filesystem_layer", "very-quick-profile"} <= set(out.result["quality"]["limits"])
    cal = out.calibration["calibration"]
    assert cal["fresh_vs_rewrite"] == {"measured": False, "reason": "veryquick_profile"}
    ws = cal["extent_written"][0][1]
    assert ws <= int(1.2 * out.calibration["calibration"]["R_est_bps"] * plan.VQ_FILL_S) + (1 << 20)


def test_prf_03_the_budget_flag_overrides_the_profile_budget():
    host = make_host(cache=None)
    out = calibrate(host, profile="veryquick", budget_minutes=1)
    assert out.status == "complete" and out.result["run"]["budget_s"] == 60


# -- the dry run -------------------------------------------------------------------------------------------------------
def test_prf_03_the_dry_run_plans_the_jobs_the_real_run_runs(clean):
    host, out, _ = clean
    cal = out.calibration["calibration"]
    planned = dryrun.calibrate_plan(host, config(profile="veryquick", assume_r_est=cal["R_est_bps"]))
    assert planned["profile"] == "veryquick" and planned["budget_s"] == 210 and planned["total_s"] == 173 and planned["preflight"] == "not_run"
    assert [s["name"] for s in planned["stages"]] == ["S0", "S1", "fill", "E-cold", "series", "S6"]
    # the planned jobs are the real run's jobs (same names bar the run id, same offsets, sizes, depths, runtimes, sampling and byte bounds)
    stage_jobs = {s["name"]: [RUN_ID.sub("RUN", " ".join(e["argv"])) for e in s["jobs"]] for s in planned["stages"]}
    ran = [RUN_ID.sub("RUN", " ".join(j.argv())) for j in fio_jobs(host)]
    for stage in ("S1", "fill", "E-cold", "series", "S6"):
        assert stage_jobs[stage] and all(job in ran for job in stage_jobs[stage]), stage
    assert [len(stage_jobs[s]) for s in ("S1", "fill", "E-cold", "series", "S6")] == [1, 2, 3, 1, 4]
    # every planned writer has the written-byte bound a real run's job has, every reader none
    for stage in planned["stages"]:
        for entry in stage["jobs"]:
            assert (entry["written_bytes_bound"] is not None) == parse_job(entry["argv"]).writes, entry["argv"]


def test_prf_03_the_cli_takes_the_profile_and_prints_the_plan():
    import io
    from cli_extended import CliIdentity
    from iobench import cli
    host = make_host(cache=None)
    calibrate(host)
    out, err = io.StringIO(), io.StringIO()
    code = cli.build_cli(CliIdentity("iobench", "0.1.0", "x", "iobench"), host_factory=lambda: host).run(
        argv=["calibrate", "--target", "/dev/vda7", "--profile", "veryquick", "--dry-run", "--json"], stdout=out, stderr=err)
    doc = json.loads(out.getvalue())
    assert code == 0 and doc["profile"] == "veryquick" and doc["total_s"] == 173 and cli.PROFILES == ("veryquick", "quick", "standard", "thorough")
    code = cli.build_cli(CliIdentity("iobench", "0.1.0", "x", "iobench"), host_factory=lambda: host).run(
        argv=["calibrate", "--target", "/dev/vda7", "--profile", "ludicrous", "--dry-run"], stdout=io.StringIO(), stderr=io.StringIO())
    assert code != 0


def test_prf_03_the_vocabulary_and_limits_are_closed():
    assert "veryquick_profile" in P.UNPROVEN_REASONS and "very-quick-profile" in P.LIMIT_FIXED and "coarse" in P.METERING
    with pytest.raises(ValueError):
        P.value(1, "R6-model", {}, "formula-only", reasons=["very_quick"])
    assert derive.r6_keys(direction="read", metering="coarse", worst_bps=4096.0 * 10, worst_iops=7.9, c_io_seq_us=None, c_io_rand_us=None, c_byte_ns=None,
                          cache_ratio=1.0) == {"rbps": 40960, "rseqiops": 10, "rrandiops": 7}
    assert P.build_coarse_model({"read": None, "write": {"seq_bps": 8192.0, "rand_iops": 3.0}})["wbps"]["value"] == 8192
    assert P.build_coarse_model({"read": None, "write": None})["rrandiops"]["status"] == "absent"
