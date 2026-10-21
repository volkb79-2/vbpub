"""IOB-SLO / IOB-PRF / IOB-SWP-01 / IOB-MAT-02 / IOB-CAL-01 planning."""
import pytest

from iobench import plan
from iobench.plan import GIB

R1002_SIZE = 435853320192
R_EST = 1.36e9


def test_slo_05_standard_fits_on_r1002_geometry():
    std = plan.plan_calibration("standard", R1002_SIZE, R_EST)
    assert std.total == 3280 <= 3600
    table = {s.name: s.allotted_s for s in std.stages}
    assert table == {"S0": 30, "S1": 30, "fill": 420, "E-cold": 140, "ladder": 720, "ladder-qd": 120, "E2": 120,
                     "fresh-vs-rewrite": 90, "fresh-observation": 120, "rearm": 100, "reference": 150,
                     "observation": 360, "S6": 720, "S7": 160}
    thorough = plan.plan_calibration("thorough", R1002_SIZE, R_EST)
    assert thorough.total == 4120 <= 4500
    assert std.seconds("fill") == 420 and std.as_dict()["total_s"] == 3280
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
