"""IOB-STA: the status model, its vocabularies and the status collapse audit (every status reachable only from its row)."""
import json

import pytest

from iobench import cache, ceilings, guard, plan, proposals as P, result as res, shape, testing
from iobench import calibrate as cal_mod
from iobench.proposals import DEFAULT_TARGETS
from iobench.testing import Bucket

from helpers import INVERSION, READ_MISS, WRITE_14, calibrate, make_host, matrix, read_json

GIB = 1 << 30


def test_sta_01_every_value_carries_its_own_status():
    v = P.value(5, "R1", {"a": 1}, "measured")
    assert v["status"] == "proven" and "reasons" not in v and v["flags"] == []
    u = P.value(5, "R1", {}, "formula-only", reasons=["formula_only", "formula_only", "cross_disk"], flags=["x"])
    assert u["status"] == "unproven" and u["reasons"] == ["formula_only", "cross_disk"] and u["flags"] == ["x"]
    a = P.absent("stage_skipped", "R6-model", {"key": "wbps"})
    assert a["status"] == "absent" and a["reason"] == "stage_skipped" and a["value"] == 0
    # a value is never stronger than its weakest basis
    assert P.worsen(v, ["ceiling_moved"])["status"] == "unproven" and P.worsen(v, [])["status"] == "proven"
    assert P.worsen(a, ["ceiling_moved"]) is a
    assert P.worsen(u, ["formula_only"])["reasons"] == ["formula_only", "cross_disk"]


def test_sta_02_closed_vocabularies():
    assert len(P.UNPROVEN_REASONS) == 18 and len(P.ABSENT_REASONS) == 6
    with pytest.raises(ValueError):
        P.value(1, "R1", {}, "measured", reasons=["because"])
    with pytest.raises(ValueError):
        P.value(1, "R1", {}, "guesswork")
    with pytest.raises(ValueError):
        P.absent("lazy", "R1")
    with pytest.raises(ValueError):
        P.worsen(P.value(1, "R1", {}, "measured"), ["nope"])
    assert P.CACHE_STATES == ("cold_measured", "rate_cap", "cold_hit_like", "undetermined")
    assert P.METERING == ("byte", "iops", "mixed", "undetermined") and P.FITS == ("good", "poor", "insufficient", "plateau")
    for limit in ("quick-profile", "limited-by-budget", "short-observation", "single-run", "stage_skipped:S6", "bucket_exclusion_s<=240",
                  "filesystem_layer", "fresh_semantics_filesystem"):
        assert P.limit_ok(limit)
    assert not P.limit_ok("stage_skipped:E-cold") and not P.limit_ok("whatever")


def test_sta_03_cache_dependent_never_proven_without_direct_evidence():
    # controlled wrong: a perfect E1 fit and no cold probe -> unproven
    good = cache.E1("good", 8, 0.999, 0.01, 9000.0, 4e10, True)
    state = cache.classify_cache(None, None, cliff_bytes=3e11, largest_rung=345 * GIB)
    value = cache.read_value(state, 24000.0, good)
    assert state.status == "unproven" and value.status == "unproven" and value.value == 9000.0 and value.value <= 24000.0
    # E1 can never confer proven, whatever the fit
    for fit in ("good", "plateau"):
        assert cache.read_value(state, 24000.0, cache.E1(fit, 5, 1.0, 0.0, 9000.0, 1.0, True)).status == "unproven"


def test_sta_04_show_marks_apply_requirements():
    group = {"a": P.value(1, "R1", {}, "measured"), "b": P.value(1, "R1", {}, "measured", reasons=["formula_only"]),
             "c": P.absent("stage_skipped", "R1"), "nested": {"dev_aggregate": {"x": P.value(1, "R5", {}, "measured", reasons=["cross_disk"])}}}
    out = P.annotate_apply(group)
    assert (out["a"]["apply"], out["b"]["apply"], out["c"]["apply"]) == ("ok", "requires_accept", "not_possible")
    assert out["nested"]["dev_aggregate"]["x"]["apply"] == "requires_accept" and "apply" not in group["a"]
    assert P.section_summary(group, identity_weak=False)["proven"] == 1
    assert P.section_summary({"a": group["a"]}, identity_weak=True)["unproven"] == [{"key": "a", "reasons": ["identity_weak"]}]


# -- the audit ---------------------------------------------------------------------------------------------------
START = 1_792_550_400.0
PLAIN_READ = dict(iops_max=21600, lat_us=300)
PLAIN_WRITE = dict(iops_max=71584, lat_us=440, seq_io_ratio=1.0)


def reasons_of(result, sections=("caps", "iocost_model", "iocost_qos", "writeback")):
    out = {}
    for section in sections:
        for key, v in P.iter_values(result["proposals"][section]):
            out[f"{section}.{key}"] = (v["status"], tuple(v.get("reasons", ())), v.get("reason"))
    return out


def all_reasons(result):
    return {r for _, rs, _ in reasons_of(result).values() for r in rs}


def all_absent(result):
    return {a for _, _, a in reasons_of(result).values() if a}


def seed_history(host, path="/dev/vda7"):
    identity = res.s0_identity(host, guard.load_target(host, path), "/bench/w")
    digest = ceilings.identity_digest(identity)
    host.makedirs("/var/lib/iobench")
    for d in ("read", "write"):
        for w, vals in (("seq1m", (1.0e9, 1.1e9, 1.2e9)), ("rand4k", (4.0e7, 4.1e7, 4.2e7))):
            for i, v in enumerate(vals):
                ceilings.append(host, "/var/lib/iobench/ceilings.jsonl", {"ts": ceilings.iso(host.now() - 3600 * (i + 1)), "dir": d, "workload": w,
                                "plateau_bps": v, "duration_s": 600, "source": "governor"}, digest)


def seeded(seed=True, **host_kw):
    host = make_host(**{"cache": INVERSION, "write": PLAIN_WRITE, **host_kw})
    if seed:
        seed_history(host)
    return host


def run_cal(seed=True, **kw):
    host = kw.pop("host", None) or seeded(seed, **kw.pop("host_kw", {}))
    return host, calibrate(host, **kw)


def run_matrix(model, host_kw=None, **kw):
    host = make_host(**{"cache": INVERSION, **(host_kw or {})})
    calibrate(host)
    host.sweep_model = model
    return host, matrix(host, **kw)


def default_model(p99=2e5, drain=10.5e6):
    return lambda k: testing.SweepOutcome(probe_p99_us=p99, load_iops=800.0, load_fsync_p99_us=9e4, drain_bps=drain)


def matrix_baseline_model(host):
    cal = read_json(host, "/var/lib/iobench/calibration.json")["calibration"]
    return default_model(drain=cal["cap_basis"]["bps"]["write"]["value"])


def key_reasons(result, key):
    section, name = key.split(".", 1)
    for k, v in P.iter_values(result["proposals"][section]):
        if k == name:
            return v["status"], set(v.get("reasons", ())), v.get("reason")
    raise KeyError(key)


def test_sta_05_status_collapse_audit():
    base_host = seeded()
    baseline = calibrate(base_host).result
    # the proven row: a healthy, history-backed, cache-proven run
    for key in ("caps.RIOPS", "caps.WIOPS", "iocost_qos.wpct", "iocost_qos.wlat", "iocost_qos.rlat", "iocost_qos.min"):
        assert key_reasons(baseline, key)[:2] == ("proven", set()), (key, key_reasons(baseline, key))
    mbase_host = seeded()
    calibrate(mbase_host)
    mbase_host.sweep_model = matrix_baseline_model(mbase_host)
    mbase = matrix(mbase_host).result
    for key in ("writeback.dirty_bytes", "writeback.dirty_background_bytes", "writeback.max_sectors_kb"):
        assert key_reasons(mbase, key)[:2] == ("proven", set()), (key, key_reasons(mbase, key))

    def cal(**kw):
        return lambda: run_cal(**kw)[1].result

    def residual():
        orig = cal_mod.Calibrator.e_cold
        cal_mod.Calibrator.e_cold = lambda self: (_ for _ in ()).throw(cal_mod.S.TooFewPoints("x"))
        try:
            return run_cal()[1].result
        finally:
            cal_mod.Calibrator.e_cold = orig

    def margin():
        h = seeded(cache=dict(capacity_gib=330, hit_iops=92000, write_allocate=False))
        h.devices["/dev/vda7"].cache.order.update({i: 1.0 for i in range(330)})
        return calibrate(h).result

    def moved():
        flag = {"on": False}
        h = seeded(ceiling=lambda t: 0.5 if flag["on"] else 1.0)
        real = h.write_range
        h.write_range = lambda a, t, o, n: (flag.update(on=flag["on"] or any("rung-w32-4096" in x for x in a)), real(a, t, o, n))[1]
        return calibrate(h).result

    def cut():
        h = seeded()
        h.scenario.cut_after_bytes = {"-fill-": int(0.4 * 0.85 * 435853320192)}
        return calibrate(h).result

    def mat(model, host_kw=None, **kw):
        return lambda: run_matrix(model, host_kw, **kw)[1].result

    def cross():
        h = make_host(cache=INVERSION)
        seed_history(h)
        calibrate(h)
        h.add_disk("vdb", 253, 100 * GIB)
        h.add_partition("vdb", "vdb1", 253, 1, 50 * GIB, "00000000-0000-0000-0000-00000000bbbb")
        h.devices["/dev/vdb1"].fs_uuid = "44444444-4444-4444-4444-444444444444"
        h.add_mount("253:1", "/other", "/dev/vdb1")
        h.set_fs("/other", 50 * GIB, 40 * GIB)
        h.sweep_model = matrix_baseline_model(h)
        return matrix(h, work_dir="/other/w").result

    def mat_base(model_fn, **kw):
        def run():
            h = make_host(cache=INVERSION)
            seed_history(h)
            calibrate(h)
            h.sweep_model = model_fn(h)
            return matrix(h, **kw).result
        return run

    base_cal = read_json(base_host, "/var/lib/iobench/calibration.json")["calibration"]
    history = ceilings.summary(ceilings.load(base_host, "/var/lib/iobench/ceilings.jsonl",
                                              ceilings.identity_digest(read_json(base_host, "/var/lib/iobench/calibration.json")["identity"]))[0], base_host.now())

    def with_shape(cells, override=None):
        """A scenario at builder level: the S6 cells decide the model; everything else is the healthy baseline."""
        import copy
        c = copy.deepcopy(base_cal)
        c["shape"] = shape.shape_summary(cells, override=override)
        c["cap_basis"]["bps"] = {d: {"value": 1e9, "status": "proven", "reasons": []} for d in ("read", "write")}
        c["cap_basis"]["read"] = {"value": 9800.0, "status": "proven", "reasons": []}
        c["cache"]["read"]["read_value"] = {"value": 9800.0, "status": "proven", "reasons": []}
        return {"proposals": P.build_calibration_proposals(c, DEFAULT_TARGETS, history)}

    def cells_of(fn):
        out = []
        for d in ("read", "write"):
            for p in ("seq", "rand"):
                for bs in shape.SIZES:
                    iops = fn(d, p, bs)
                    out.append({"id": "x", "dir": d, "pattern": p, "bs": bs, "block_state": "rewrite", "steady_iops": iops, "steady_bps": iops * bs, "repeat": False})
        return out

    clean = cells_of(lambda d, p, bs: 1 / (80e-6 + bs * 0.25e-9))
    erratic = {4096: 9000.0, 16384: 2000.0, 65536: 9500.0, 262144: 1500.0, 1048576: 8000.0}
    assert key_reasons(with_shape(clean), "iocost_model.wbps")[:2] == ("proven", set())          # the proven row of the model
    def burst_throttle():
        t_obs = {}
        h = seeded(ceiling=lambda t: 1.0 if t < t_obs.get("t", 1e18) + 100 else 0.05)  # the rate collapses 100 s into the write observation
        real = h.write_range

        def wr(a, t, o, n):
            if any("-obs-write" in x for x in a):
                t_obs.setdefault("t", h.now())
            return real(a, t, o, n)

        h.write_range = wr
        return calibrate(h).result

    rows = {
        # name: (owner key, scenario)
        "cache_model_undetermined": ("caps.RIOPS", cal(host_kw=dict(read=dict(iops_max=9800, lat_us=200, lat_hit_us=110)))),
        "no_cliff_hit_like_mode": ("caps.RIOPS", cal(host_kw=dict(cache=dict(capacity_gib=500, hit_iops=92000, write_allocate=True)))),
        "cache_residual_possible": ("caps.RIOPS", residual),
        "device_smaller_than_cache_margin": ("caps.RIOPS", margin),
        "ceiling_moved": ("caps.RIOPS", moved),
        "budget_limited": ("caps.RIOPS", cut),
        "quick_profile": ("caps.RIOPS", cal(profile="quick")),
        "short_observation": ("caps.WIOPS", burst_throttle),
        "worst_from_single_run": ("caps.RIOPS", cal(seed=False)),
        "metering_undetermined": ("iocost_model.wbps", lambda: with_shape(cells_of(lambda d, p, bs: 1 / ((80e-6 if p == "rand" else 20e-6) + bs * 0.25e-9)))),
        "operator_override": ("iocost_model.wbps", lambda: with_shape(clean, override="byte")),
        "shape_fit_poor": ("iocost_qos.max", lambda: with_shape(cells_of(lambda d, p, bs: erratic[bs]))),
        "noise_floor_high": ("writeback.dirty_bytes", mat_base(noisy_model)),
        "target_missed": ("writeback.dirty_bytes", mat_base(lambda h: matrix_baseline_model_p99(h, 9e5))),
        "drain_below_ladder": ("writeback.dirty_bytes", mat(default_model())),
        "formula_only": ("writeback.dirty_bytes", mat_base(matrix_baseline_model, budget_minutes=6)),
        "cross_disk": ("writeback.dirty_bytes", cross),
    }
    for name, (key, scenario) in rows.items():
        result = scenario()
        status, got, _ = key_reasons(result, key)
        base = key_reasons(mbase if key.startswith("writeback") else (with_shape(clean) if key.startswith("iocost") else baseline), key)[1]
        assert name in got, (name, key, status, got)
        assert name not in base, (name, base)
        leaked = got - {name} - base
        assert leaked <= CONSEQUENCES.get(name, set()), (name, key, leaked)

    def failed_s6():
        orig = cal_mod.Calibrator.s6
        cal_mod.Calibrator.s6 = lambda self: (_ for _ in ()).throw(cal_mod.S.TooFewPoints("x"))
        try:
            return run_cal()[1].result
        finally:
            cal_mod.Calibrator.s6 = orig

    def skip_s7():
        for minutes in (20, 25, 30, 35, 40, 45):  # the smallest budget at which S7 no longer fits (the priority order skips it first)
            result = run_cal(budget_minutes=minutes)[1].result
            if result["run"]["stages"].get("S7") == "skipped: budget":
                return result
        raise AssertionError("no budget skipped S7")

    absent_rows = {"stage_skipped": ("iocost_qos.wlat", skip_s7), "stage_failed": ("iocost_model.wbps", failed_s6),
                   "basis_unsteady": ("caps.RIOPS", cal(host_kw=dict(cache=None, noise=0.6))),
                   "bps_basis_missing": ("caps.RBPS", failed_s6)}
    for name, (key, scenario) in absent_rows.items():
        status, _, reason = key_reasons(scenario(), key)
        assert (status, reason) == ("absent", name), (name, key, status, reason)
        assert key_reasons(baseline, key)[0] != "absent"
    # basis_missing: a quick calibration measures no QD1/QD8, so R2 has no basis in the matrix
    qh = make_host(cache=INVERSION)
    calibrate(qh, profile="quick")
    qh.sweep_model = default_model()
    qres = matrix(qh).result
    assert key_reasons(qres, "writeback.nr_requests")[::2] == ("absent", "basis_missing")


def noisy_model(host):
    """REF and REF2 differ 5x: the last cell of the sweep (late in the run) sees a 1 s p99 instead of 200 ms."""
    base = matrix_baseline_model(host)
    t0 = host.now()
    return lambda k: testing.SweepOutcome(probe_p99_us=1e6 if host.now() > t0 + 430 else 2e5, load_iops=800.0, load_fsync_p99_us=9e4,
                                          drain_bps=base(k).drain_bps)


def matrix_baseline_model_p99(host, p99):
    base = matrix_baseline_model(host)
    return lambda k: testing.SweepOutcome(probe_p99_us=p99, load_iops=800.0, load_fsync_p99_us=9e4, drain_bps=base(k).drain_bps)


# reasons that legitimately accompany another row's scenario (each is a CONSEQUENCE of the scenario, not a collapse of two rows):
CONSEQUENCES = {
    "budget_limited": {"cache_model_undetermined", "ceiling_moved", "no_cliff_hit_like_mode", "cache_residual_possible"},
    "ceiling_moved": {"cache_model_undetermined", "no_cliff_hit_like_mode"},
    "quick_profile": {"budget_limited"},  # the quick fill is time-bounded to R_est x 60 s, so the working set is budget-limited too
}
