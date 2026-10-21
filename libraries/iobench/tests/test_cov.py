"""Behavioural corner cases of the run envelope, stages and builders (each asserts an observable refusal, mark or value)."""
import pytest

from iobench import calibrate as cal_mod, devio, extents, stages
from iobench import series as S
from iobench.errors import Refusal, RunFailed
from iobench.host import RunResult
from iobench.stages import IntegrityViolation

from helpers import INVERSION, calibrate, config, make_host, matrix, read_json

GIB = 1 << 30


def test_extents_algebra():
    e = extents.Extents()
    e.add(10, 10)
    e.add(20, 10)                       # empty and inverted ranges add nothing
    assert e.total() == 0 and e.as_list() == []
    e.add(0, 100)
    e.remove(40, 60)
    assert e.as_list() == [[0, 40], [60, 100]] and e.total() == 80
    e.remove(0, 10)
    e.remove(90, 120)                   # trims one end only
    assert e.as_list() == [[10, 40], [60, 90]]
    c = e.copy()
    assert c == e and c is not e and c != extents.Extents([(0, 1)]) and (c == 3) is False
    assert "Extents(" in repr(c)
    c.add(40, 60)
    assert c.covers(10, 90) and not e.covers(10, 90)


def test_fio_version_floor_and_parse():
    assert stages.parse_fio_version("fio-3.36\n") == (3, 36)
    with pytest.raises(Refusal) as err:
        stages.parse_fio_version("something else")
    assert err.value.condition == "fio_version"
    host = make_host(cache=None)
    real = host.run
    host.run = lambda argv, timeout=None: RunResult(0, "fio-3.28\n", "") if list(argv[1:]) == ["--version"] else real(argv, timeout)
    with pytest.raises(Refusal, match="older than 3.30"):
        calibrate(host)
    assert not host.write_log


def test_workdir_required_for_matrix():
    host = make_host(cache=INVERSION)
    assert calibrate(host).status == "complete"
    with pytest.raises(Refusal) as err:
        matrix(host, work_dir=None)
    assert err.value.condition == "workdir_required"


def test_read_outside_the_written_extent_is_refused_before_spawn():
    host = make_host(cache=None)
    r = cal_mod.Calibrator(host, config())
    r.s0(need_workdir=False)
    r.read_extent.add(0, GIB)
    with pytest.raises(IntegrityViolation, match="IOB-PLA-08"):
        r.spawn(r.job("x", "randread", 4096, 1, 1, 2 * GIB, GIB))
    r.safety.release()


def test_target_too_small_refused():
    host = make_host(size=GIB // 2, cache=None)
    with pytest.raises(Refusal) as err:
        calibrate(host)
    assert err.value.condition == "target_too_small" and host.devices["/dev/vda7"].write_bytes == 0


def test_s1_measures_no_throughput_fails_the_run():
    host = make_host(cache=None)
    r = cal_mod.Calibrator(host, config())
    r.s0(need_workdir=False)
    real = r.fio
    r.fio = lambda job: type("R", (), {"series": [(float(i), 0.0) for i in range(1, 21)], "stats": None, "job": job})()
    with pytest.raises(RunFailed, match="no write throughput"):
        r.s1()
    r.fio = real
    r.safety.release()


def test_fill_that_writes_nothing_fails():
    host = make_host(cache=INVERSION)
    host.scenario.cut_after_bytes = {"-fill-": 0}
    out = calibrate(host)
    assert out.status == "failed" and "fill wrote no bytes" in out.message


def test_cold_region_smaller_than_1mib():
    host = make_host(cache=None)
    r = cal_mod.Calibrator(host, config())
    r.s0(need_workdir=False)
    r.ws = 4 * (1 << 20)
    with pytest.raises(S.SeriesError, match="smaller than 1 MiB"):
        r.e_cold()
    r.safety.release()


def test_identity_failures_before_the_marker_has_a_run():
    host = make_host(cache=None)
    host.scenario.cut_after_bytes = {}
    real = host.run
    host.run = lambda argv, timeout=None: (_ for _ in ()).throw(RuntimeError("boom")) if list(argv[1:]) == ["--version"] else real(argv, timeout)
    with pytest.raises(RuntimeError):
        calibrate(host)
    assert not host.exists("/run/iobench/lock") and not host.write_log


def test_failing_fio_job_fails_the_run_and_restores():
    host = make_host(cache=None)
    r = cal_mod.Calibrator(host, config())
    real_collect = devio.collect
    devio.collect = lambda h, j, run, started: real_collect(h, j, RunResult(1, "", "boom"), started)
    try:
        res = r.execute()
    finally:
        devio.collect = real_collect
    assert res.status == "failed" and res.exit_code == 1 and "failed (1): boom" in res.message
    assert not host.exists("/run/iobench/restore.json")


def test_budget_too_small_for_three_rungs_fails_not_guesses():
    out = calibrate(make_host(cache=INVERSION), budget_minutes=8)
    assert out.status == "failed" and "fewer than three ladder rungs" in out.message
    assert out.result["run"]["status"] == "failed"


def test_e2_skipped_when_the_histogram_run_does_not_fit(monkeypatch):
    real = cal_mod.Calibrator.fits
    monkeypatch.setattr(cal_mod.Calibrator, "fits", lambda self, seconds: False if seconds == cal_mod.HIST_S else real(self, seconds))
    out = calibrate(make_host(cache=INVERSION))
    stages_ = out.calibration["run"]["stages"]
    assert stages_["E2"] == "skipped: budget" and out.calibration["calibration"]["cache"]["read"]["E2"] is None


def test_noise_leaves_no_basis_and_s7_skips_that_direction():
    out = calibrate(make_host(cache=None, noise=0.6), profile="thorough", budget_minutes=200)
    assert out.calibration["run"]["stages"]["S7"] == "ok"
    assert out.calibration["calibration"]["cap_basis"]["read"]["reason"] == "basis_unsteady"
    assert not [p for p in out.calibration["calibration"]["latency_curve"] if p["dir"] == "read"]


def test_fresh_stages_without_an_extent_or_discard():
    host = make_host(cache=None)
    r = cal_mod.Calibrator(host, config())
    r.s0(need_workdir=False)
    r.fresh = None
    with pytest.raises(S.SeriesError, match="no fresh extent"):
        r.fresh_vs_rewrite()
    assert r.fresh_note == "no_fresh_extent"
    with pytest.raises(S.SeriesError, match="no fresh extent to re-arm"):
        r.rearm()
    r.safety.release()


def test_run_job_records_touch_only_with_a_written_extent():
    host = make_host(cache=None)
    r = cal_mod.Calibrator(host, config())
    r.s0(need_workdir=False)
    r.read_extent.add(0, GIB)
    r.ws = 0
    r.run_job("t", "randread", 4096, 1, 5, 0, GIB, key="x")
    assert "x" not in r.touch.maps
    r.ws = GIB
    r.run_job("t2", "randread", 4096, 1, 5, 0, GIB, key="x")
    assert sum(r.touch.maps["x"]) > 0
    r.safety.release()


# ---------------------------------------------------------------------------------------------------------
# matrix corner cases
# ---------------------------------------------------------------------------------------------------------
from iobench import matrix as mx_mod, proposals as P, testing  # noqa: E402

SDA = "/sys/class/block/vda/queue"


def base_model(p99=2e5):
    return lambda k: testing.SweepOutcome(probe_p99_us=p99, load_iops=800.0, load_fsync_p99_us=9e4, drain_bps=10.5e6)


def calibrated():
    host = make_host(cache=INVERSION)
    assert calibrate(host).status == "complete"
    host.sweep_model = base_model()
    return host


def test_a_rejected_knob_skips_that_cell_only():
    host = calibrated()
    host.sysfs_reject.add(f"{SDA}/nr_requests")
    out = matrix(host)
    cells = {c["name"]: c for c in out.result["evidence"]["sweep"]["cells"]}
    assert cells["nr_requests*2"]["skipped"] == "knob_rejected" and "skipped" not in cells["REF"]
    assert out.status == "complete" and host.read_text(f"{SDA}/nr_requests") == "64\n"


def test_a_lost_reference_cell_fails_the_sweep_and_dependents_are_absent():
    host = calibrated()
    host.sysfs_reject.add("/proc/sys/vm/dirty_bytes")
    out = matrix(host)
    assert out.result["run"]["stages"]["S4"] == "failed: unusable_series"
    wb = out.result["proposals"]["writeback"]
    assert wb["dirty_bytes"]["status"] != "absent" and "formula_only" in wb["dirty_bytes"]["reasons"]


def test_unknown_knob_refused_and_a_repeat_with_a_rejected_candidate():
    host = calibrated()
    with pytest.raises(Refusal) as err:
        matrix(host, knob="vm_swappiness", candidates=(1, 2))
    assert err.value.condition == "knob_unknown" and not host.write_log
    host.sysfs_reject.add(f"{SDA}/max_sectors_kb")
    out = matrix(host, knob="max_sectors_kb", candidates=(32, 128), repeat=True)
    assert out.status == "complete" and out.result["evidence"]["sweep"]["decision"] is None


def test_failed_stages_make_their_dependents_absent_with_the_right_reason(monkeypatch):
    host = calibrated()
    monkeypatch.setattr(mx_mod.Matrixer, "s3", lambda self: (_ for _ in ()).throw(S.TooFewPoints("x")))
    out = matrix(host)
    wb = out.result["proposals"]["writeback"]
    assert out.result["run"]["stages"]["S4"] == "skipped: S3" and out.result["run"]["stages"]["S5"] == "skipped: S3"
    assert {wb[k]["reason"] for k in ("dirty_bytes", "dirty_background_bytes", "max_sectors_kb")} == {"stage_failed"}
    host2 = calibrated()
    monkeypatch.undo()
    monkeypatch.setattr(mx_mod.Matrixer, "s5", lambda self: (_ for _ in ()).throw(S.TooFewPoints("x")))
    wb2 = matrix(host2).result["proposals"]["writeback"]
    assert wb2["max_sectors_kb"]["status"] == "absent" and wb2["max_sectors_kb"]["reason"] == "stage_failed"


def winner_model(k):
    good = k["max_sectors_kb"] == 128 or k["dirty_bytes"] == 8 * 1024 * 1024
    return testing.SweepOutcome(probe_p99_us=4e4 if good else 2e5, load_iops=800.0, load_fsync_p99_us=9e4, drain_bps=10.5e6)


def test_sweep_winners_replace_the_formula_and_say_so():
    host = calibrated()
    host.sweep_model = winner_model
    out = matrix(host, targets={**P.DEFAULT_TARGETS, "F_read_ratio": 1.1})
    wb = out.result["proposals"]["writeback"]
    assert wb["max_sectors_kb"]["value"] == 128 and "sweep_winner" in wb["max_sectors_kb"]["flags"] and "read_floor_violated" in wb["max_sectors_kb"]["flags"]
    assert wb["max_sectors_kb"]["basis"] == "measured"
    assert wb["dirty_bytes"]["value"] == 8 * 1024 * 1024 and wb["dirty_bytes"]["basis"] == "measured" and "sweep_winner" in wb["dirty_bytes"]["flags"]


def test_a_run_whose_identity_cannot_be_read_returns_no_documents(monkeypatch):
    from iobench import result as res_mod
    from iobench.host import HostError
    for runner in (calibrate, matrix):
        host = calibrated()
        monkeypatch.setattr(res_mod, "s0_identity", lambda *a, **k: (_ for _ in ()).throw(HostError("device vanished")))
        out = runner(host)
        monkeypatch.undo()
        assert out.status == "failed" and out.result is None and out.calibration is None and "device vanished" in out.message
        assert not host.exists("/run/iobench/restore.json") and not host.locks


def test_stage_wrapper_error_classes():
    host = make_host(cache=None)
    r = cal_mod.Calibrator(host, config())
    r.start_budget(3600)

    def raiser(exc):
        def fn():
            raise exc
        return fn

    assert r.stage("a", raiser(S.TooFewPoints("x"))) is False and r.stages["a"] == "failed: insufficient_points"
    assert r.stage("b", raiser(S.SeriesError("x"))) is False and r.stages["b"] == "failed: unusable_series"
    assert r.stage("c", lambda: None) is True and r.stages["c"] == "ok"
    with pytest.raises(S.TooFewPoints):
        r.stage("d", raiser(S.TooFewPoints("x")), required=True)
    with pytest.raises(S.SeriesError):
        r.stage("e", raiser(S.SeriesError("x")), required=True)
    with pytest.raises(S.IntegrityError):
        r.stage("f", raiser(S.IntegrityError("x")))
    assert r.stage("g", lambda: None, min_s=1e9) is False and r.stages["g"] == "skipped: budget"


# ---------------------------------------------------------------------------------------------------------
# proposals builders on constructed calibrations
# ---------------------------------------------------------------------------------------------------------
import copy  # noqa: E402

from iobench import shape  # noqa: E402


def cells_of(fn):
    out = []
    for d in ("read", "write"):
        for p in ("seq", "rand"):
            for bs in shape.SIZES:
                iops = fn(d, p, bs)
                out.append({"id": "x", "dir": d, "pattern": p, "bs": bs, "block_state": "rewrite", "steady_iops": iops, "steady_bps": iops * bs, "repeat": False, "state": "steady"})
    return out


def model_for(cells, *, bps_status="proven", history=None, iops_status="proven"):
    host = make_host(cache=INVERSION)
    calibrate(host)
    cal = copy.deepcopy(read_json(host, "/var/lib/iobench/calibration.json")["calibration"])
    cal["shape"] = shape.shape_summary(cells)
    value = lambda status, v: {"value": v, "status": status, "reasons": []} if status != "absent" else {"value": None, "status": "absent", "reason": "bps_basis_missing"}  # noqa: E731
    cal["cap_basis"]["bps"] = {d: value(bps_status, 1e9) for d in ("read", "write")}
    cal["cap_basis"]["read"] = value(iops_status, 9800.0) if iops_status != "absent" else {"value": None, "status": "absent", "reason": "basis_unsteady"}
    cal["cap_basis"]["write"] = cal["cap_basis"]["read"]
    return P.build_model(cal, history if history is not None else {"history": "single-run"})


def test_model_keys_absent_when_the_bps_basis_is_absent():
    clean = cells_of(lambda d, p, bs: 1 / (80e-6 + bs * 0.25e-9))
    out = model_for(clean, bps_status="absent")
    assert {v["status"] for v in out.values()} == {"absent"} and {v["reason"] for v in out.values()} == {"bps_basis_missing"}


def test_iops_metered_model_uses_the_worst_iops_and_history():
    flat_iops = cells_of(lambda d, p, bs: 3000.0)
    single = model_for(flat_iops)
    assert single["rrandiops"]["value"] == 9800 and single["rbps"]["value"] == 9800 * 1048576
    assert "worst_from_single_run" in single["rbps"]["reasons"]
    hist = {"history": "recorded", "read": {"rand4k": {"worst": 2.0e6 / 1}, "seq1m": {"worst": 5e8}}, "write": {"rand4k": {"worst": 1e9}, "seq1m": {"worst": 5e8}}}
    with_hist = model_for(flat_iops, history=hist)
    assert with_hist["rseqiops"]["value"] == int(min(2.0e6 / 4096.0, 9800.0)) and "worst_from_single_run" not in with_hist["rbps"].get("reasons", [])
    gone = model_for(flat_iops, iops_status="absent")
    assert {v["reason"] for v in gone.values()} == {"basis_unsteady"}


def test_identity_comparison_on_every_strict_field():
    from iobench import result as res_mod
    base = {"target": {"kind": "device", "path": "/dev/vda7", "fs_uuid": None, "inode": None},
            "disk": {"devno": "254:7", "partuuid": "p", "size_bytes": 10, "model": "m", "serial": None, "wwn": None}}
    assert res_mod.identity_differs(base, None) is True
    assert res_mod.identity_differs(base, copy.deepcopy(base)) is False
    for section, key, value in (("target", "inode", 5), ("target", "kind", "file"), ("disk", "devno", "254:8"), ("disk", "partuuid", "q"),
                                ("disk", "size_bytes", 11), ("disk", "model", "other")):
        live = copy.deepcopy(base)
        live[section][key] = value
        assert res_mod.identity_differs(base, live) is True, (section, key)
    # a field the stored record never had does not make it stale
    live = copy.deepcopy(base)
    live["disk"]["serial"] = "S1"
    assert res_mod.identity_differs(base, live) is False


def test_run_without_a_work_dir_records_no_workdir_identity():
    out = calibrate(make_host(cache=None), work_dir=None)
    assert out.status == "complete" and out.result["identity"]["workdir_fs"] is None


def test_dry_run_plans_without_a_fresh_extent(monkeypatch):
    from iobench import dryrun, ladder
    monkeypatch.setattr(ladder, "fresh_extent", lambda usable, ws: None)
    host = make_host(cache=None)
    doc = dryrun.calibrate_plan(host, config(work_dir="/bench/w", assume_r_est=1.36e9))
    jobs = {st["name"]: st["jobs"] for st in doc["stages"]}
    assert jobs["ladder"] and jobs["fresh-vs-rewrite"] == [] and not host.write_log
    monkeypatch.undo()
    big = dryrun.calibrate_plan(make_host(cache=None), config(work_dir="/bench/w", assume_r_est=1.36e9))
    assert {st["name"]: st["jobs"] for st in big["stages"]}["fresh-vs-rewrite"]


def test_a_repeat_that_does_not_fit_is_not_run(monkeypatch):
    host = calibrated()
    host.sweep_model = winner_model
    real = mx_mod.Matrixer.fits
    monkeypatch.setattr(mx_mod.Matrixer, "fits", lambda self, s: False if s == mx_mod.sweep.SWEEP_CELL_S + 60 else real(self, s))
    out = matrix(host)
    assert out.result["evidence"]["sweep"]["repeats"] == {} and out.result["evidence"]["sweep"]["winners"] == {}


def test_a_sweep_winner_with_an_adequate_read_ratio_has_no_floor_flag():
    host = calibrated()
    host.sweep_model = winner_model
    wb = matrix(host).result["proposals"]["writeback"]
    assert wb["max_sectors_kb"]["value"] == 128 and "read_floor_violated" not in wb["max_sectors_kb"]["flags"]


def test_model_keys_absent_when_the_fit_gives_no_positive_cost():
    # seq and rand cost differently per byte but the per-IO term is non-positive: metering is undetermined and nothing can be derived
    odd = cells_of(lambda d, p, bs: (14e6 if p == "rand" else 30e6) / bs)
    out = model_for(odd)
    assert {v["status"] for v in out.values()} == {"absent"} and {v["reason"] for v in out.values()} == {"stage_failed"}
