"""IOB-SLO / IOB-PRF / IOB-SWP-01 / IOB-MAT-02 / IOB-CAL-01 planning."""
import pytest

from iobench import devio

from iobench import plan
from iobench.plan import GIB

R1002_SIZE = 435853320192
R_EST = 1.36e9


def test_slo_05_standard_fits_on_r1002_geometry():
    std = plan.plan_calibration("standard", R1002_SIZE, R_EST)
    assert std.total == 3310 <= 3600
    table = {s.name: s.allotted_s for s in std.stages}
    assert table == {"S0": 30, "S1": 30, "fill": 420, "E-cold": 140, "ladder": 720, "ladder-qd": 120, "E2": 120,
                     "fresh-vs-rewrite": 90, "fresh-observation": 120, "rearm": 100, "reference": 180,
                     "observation": 360, "S6": 720, "S7": 160}
    thorough = plan.plan_calibration("thorough", R1002_SIZE, R_EST)
    assert thorough.total == 4150 <= 4500
    assert std.seconds("fill") == 420 and std.as_dict()["total_s"] == 3310
    assert [s.name for s in std.stages if s.required] == ["S0", "S1", "fill", "ladder"]


def test_slo_05_quick_fits_and_has_no_cache_stages():
    quick = plan.plan_calibration("quick", R1002_SIZE, R_EST)
    names = [s.name for s in quick.stages]
    assert "E-cold" not in names and "E2" not in names and "observation" not in names
    assert quick.total <= 720
    assert len(quick.rungs) == 3 and quick.rungs[:2] == (GIB, 16 * GIB)
    assert quick.ws_max == int(R_EST * 60)


def test_slo_05_matrix_worst_case_fits():
    assert plan.matrix_seconds() == 650 and plan.matrix_seconds(worst=True) == 890 <= 900
    assert plan.knob_plan_seconds(2, True) == 220 and plan.knob_plan_seconds(2, True, worst=True) == 420
    assert plan.knob_plan_seconds(2, True) <= 240 and plan.knob_plan_seconds(2, True, worst=True) <= 480
    assert plan.knob_plan_seconds(3, False) == 165


def test_cal_01_rung_plan():
    ws = plan.ws_max(R1002_SIZE, R_EST, 3600)
    assert ws / GIB == pytest.approx(345.0, abs=0.1)
    rungs = plan.rung_plan(ws)
    assert [r // GIB for r in rungs[:-1]] == [1, 2, 4, 8, 16, 32, 64, 128, 256] and rungs[-1] == ws
    assert max(rungs) <= 0.85 * plan.usable(R1002_SIZE)
    dens = plan.densification_rungs(256 * GIB, ws)
    assert [round(d / GIB, 1) for d in dens] == [282.8, 312.4]  # log-equispaced between 256 and 345 GiB
    assert all(256 * GIB < d < ws for d in dens)
    # a slow disk: R_est x fill_budget_s is the bound
    slow = plan.ws_max(R1002_SIZE, 14 * 1024 * 1024, 3600)
    assert slow == int(14 * 1024 * 1024 * 432) and slow < 0.85 * plan.usable(R1002_SIZE)
    assert plan.rung_plan(10 * GIB) == [GIB, 2 * GIB, 4 * GIB, 8 * GIB, 10 * GIB]
    assert plan.rung_plan(8 * GIB) == [GIB, 2 * GIB, 4 * GIB, 8 * GIB]


def test_slo_02_volume_bound():
    assert plan.volume_bound(14e6, 30) == int(1.2 * 14e6 * 30)


def test_swp_01_plan_counts_and_times():
    assert plan.sweep_cells() == 6 and plan.sweep_cells(True) == 8
    assert plan.sweep_seconds(6) == 480 and plan.sweep_seconds(6, worst=True) == 720
    assert plan.sweep_seconds(8) == 640


def test_mat_02_plan_budgets():
    assert plan.matrix_seconds() == 650
    assert plan.matrix_seconds(worst=True) == 890
    assert plan.knob_plan_seconds(2, True) == 220
    assert plan.knob_plan_seconds(2, True, worst=True) == 420
    assert plan.matrix_seconds(full_sweep=True) == 30 + 90 + 640 + 50


def test_slo_04_budget_fits():
    t = [1000.0]
    b = plan.Budget(lambda: t[0], 100)
    assert b.fits(100) and b.remaining() == 100
    t[0] += 60
    assert b.elapsed() == 60 and not b.fits(41) and b.fits(40)


# ---------------------------------------------------------------------------------------------------------
# stage-level
# ---------------------------------------------------------------------------------------------------------
from helpers import INVERSION, WRITE_14, calibrate, fio_jobs, make_host, read_json  # noqa: E402
from iobench.testing import Bucket, DirScenario  # noqa: E402

MIBS = 1 << 20


def test_slo_01_rate_probe():
    start = 1_792_550_400.0
    ramp = lambda t: (1.2 + 0.2 * min(max(t - start, 0.0), 20.0) / 20.0) / 1.4  # 1.2 .. 1.4 GB/s across the 20 s probe
    host = make_host(cache=None, noise=0.0, ceiling=ramp, write=dict(WRITE_14, bps_cap_seq=1.4e9), read=dict(iops_max=21600, lat_us=300))
    out = calibrate(host)
    samples = [1.2e9 + 0.2e9 * t / 20 for t in range(1, 21)]
    expected = sorted(samples[-10:])[4:6]
    assert out.calibration["calibration"]["R_est_bps"] != 0
    probe = out.result["evidence"]["rate_probe"]
    assert probe["r_est_bps"] == pytest.approx(sum(expected) / 2, rel=0.01) and probe["seconds"] == 20 and probe["samples"] == 20
    # S1 is a direct sequential 1 MiB write of the FIRST GiB of the target device (the one the fill uses)
    s1 = [j for j in fio_jobs(host) if j.name.endswith("-s1")][0]
    assert (s1.filename, s1.rw, s1.bs, s1.offset, s1.size, s1.runtime_s, s1.direct) == ("/dev/vda7", "write", MIBS, 0, 1 << 30, 20, True)


def test_slo_02_volume_bound():
    sizes = {}
    host = make_host(size=60 << 30, cache=None, noise=0.0, read=dict(iops_max=3000, lat_us=760),
                     write=dict(iops_max=1e6, lat_us=440, bps_cap_seq=14 * MIBS, bps_cap_rand=14 * MIBS))
    real = host.write_range

    def spy(argv, target, offset, length):
        handle = real(argv, target, offset, length)
        sizes[len(sizes)] = (devio.parse_job(argv), handle.sim.io_bytes)
        return handle

    host.write_range = spy
    out = calibrate(host)
    r_est = out.calibration["calibration"]["R_est_bps"]
    assert r_est == pytest.approx(14 * MIBS, rel=0.05)
    for job, written in sizes.values():
        if job.io_size is not None and "-fill-" not in job.name:
            assert written <= 1.2 * r_est * job.runtime_s * 1.001, job.name
    # the 3 x R_est scenario: the actual rate triples mid-run; bytes written stay bounded, the truncated stage is reported
    start = 1_792_550_400.0
    triple = make_host(size=60 << 30, cache=None, noise=0.0, ceiling=lambda t: 3.0 if t - start > 1500 else 1.0,
                       read=dict(iops_max=3000, lat_us=760), write=dict(iops_max=1e6, lat_us=440, bps_cap_seq=14 * MIBS, bps_cap_rand=14 * MIBS))
    seen = []
    real2 = triple.write_range

    def spy2(argv, target, offset, length):
        handle = real2(argv, target, offset, length)
        seen.append((devio.parse_job(argv), handle.sim.io_bytes))
        return handle

    triple.write_range = spy2
    out2 = calibrate(triple)
    r2 = out2.calibration["calibration"]["R_est_bps"]
    late = [(j, b) for j, b in seen if j.io_size is not None and "-fill-" not in j.name]
    assert late and all(b <= 1.2 * r2 * j.runtime_s * 1.001 for j, b in late)
    failed = {k: v for k, v in out2.result["run"]["stages"].items() if v.startswith("failed")}
    assert any(v == "failed: insufficient_points" for v in failed.values()), out2.result["run"]["stages"]


def test_slo_03_slow_disk_time_bounded():
    host = make_host(size=60 << 30, cache=None, noise=0.02, read=dict(iops_max=3000, lat_us=760),
                     write=dict(iops_max=1e6, lat_us=440, bps_cap_seq=3_000_000, bps_cap_rand=3_000_000))
    out = calibrate(host)
    cal = out.calibration["calibration"]
    assert cal["R_est_bps"] < 5_000_000 and out.status == "complete"
    assert cal["extent_written"][0][1] <= 1.05 * cal["R_est_bps"] * 0.12 * 3600
    assert "limited-by-budget" in out.result["quality"]["limits"] and out.result["quality"]["cache_proof"] == "limited-by-budget"
    caps = out.result["proposals"]["caps"]["dev_aggregate"]
    assert "budget_limited" in caps["RIOPS"]["reasons"] and "budget_limited" in caps["WIOPS"]["reasons"]
    assert out.calibration["run"]["budget_s"] == 3600


def test_slo_04_priority_and_skip_record():
    host = make_host(cache=INVERSION)
    out = calibrate(host, budget_minutes=40)
    stages = out.calibration["run"]["stages"]
    order = list(stages)
    required = ["S0", "S1", "fill", "ladder"]
    assert [s for s in order if s in required] == required and all(stages[s] == "ok" for s in required)
    skipped = [s for s, v in stages.items() if v == "skipped: budget"]
    assert skipped
    assert order.index("S0") < order.index("S1") < order.index("fill") < order.index("ladder") < order.index("reference")
    assert "limited-by-budget" in out.result["quality"]["limits"]
    # dependents of a skipped stage are absent: stage_skipped, never a guessed value
    if stages.get("S7") == "skipped: budget":
        qos = out.result["proposals"]["iocost_qos"]
        assert qos["wlat"]["status"] == "absent" and qos["wlat"]["reason"] == "stage_skipped"
        assert "stage_skipped:S7" in out.result["quality"]["limits"]
    if stages.get("S6") == "skipped: budget":
        assert out.result["proposals"]["iocost_model"]["wbps"]["reason"] == "stage_skipped"
        assert out.result["proposals"]["caps"]["dev_aggregate"]["RBPS"]["reason"] == "bps_basis_missing"
    assert out.status == "complete"
