"""IOB-CAL: calibration building blocks (pure parts)."""
import pytest

from iobench import devio, ladder
from iobench.extents import Extents
from iobench.series import SeriesError
from iobench.testing import DirScenario, Scenario, standard_host

from helpers import job, run_job

GIB = 1 << 30


def rung(ws_gib, iops, **kw):
    return {"ws_bytes": int(ws_gib * GIB), "steady_iops": float(iops), "state": "steady", **kw}


R1002 = [rung(1, 97850), rung(16, 96000), rung(100, 92000), rung(150, 90000), rung(200, 88000), rung(250, 85900),
         rung(300, 46315), rung(350, 31000), rung(400, 24000)]


def test_cal_03_rung_record_and_extension():
    host = standard_host(Scenario(read=DirScenario(iops_max=21600), write=DirScenario(iops_max=3596)))
    host.devices["/dev/vda7"].written.add(0, 4 * GIB)
    res = run_job(host, job("randread", size=GIB, rt=30))
    rec = ladder.rung_record(res, GIB)
    assert rec["state"] in ("steady", "flat") and rec["steady_iops"] == pytest.approx(res.stats.iops, rel=0.01)
    assert rec["dir"] == "read" and rec["qd"] == 32 and set(rec["lat_us"]) == {"p50", "p90", "p95", "p99", "p999"}
    assert len(rec["series"]) <= 120 and rec["series"][0][0] == 6.0  # the first 5 s are discarded
    assert rec["class"] == "unclassified" and rec["observation_s"] == 30.0
    # a 12 s job has too few points after the warm-up discard
    short = run_job(host, job("randread", size=GIB, rt=12, name="short"))
    with pytest.raises(SeriesError):
        ladder.rung_record(short, GIB)


def test_cal_04_cliff_detection():
    cliff, ref, classes = ladder.classify_rungs(R1002)
    assert cliff == 300 * GIB and ref == 96000
    assert [c["class"] for c in classes] == ["cached_tier"] * 6 + ["post_cliff"] * 3
    # r1002 recorded replay: the cliff lies between 250 and 300 GiB
    assert 250 * GIB < cliff <= 300 * GIB
    # cliff at 270 GiB
    sc = [rung(g, i) for g, i in ((1, 100), (16, 100), (64, 98), (128, 97), (256, 96), (270, 50), (345, 30))]
    assert ladder.classify_rungs(sc)[0] == 270 * GIB
    # no cliff
    flat = [rung(g, 100 - g / 100) for g in (1, 16, 64, 128, 256)]
    c, _, classes = ladder.classify_rungs(flat)
    assert c is None and {x["class"] for x in classes} == {"no_cliff"}
    # cliff only at the last rung
    last = [rung(g, 100) for g in (1, 16, 64, 128)] + [rung(345, 40)]
    assert ladder.classify_rungs(last)[0] == 345 * GIB
    # noisy small rungs: a dip that recovers is not a cliff
    noisy = [rung(1, 100), rung(2, 40), rung(4, 100), rung(8, 98), rung(16, 99)]
    assert ladder.classify_rungs(noisy)[0] is None
    # an inverted ladder (rising with ws): no cliff, cached_ref is the miss rate
    rising = [rung(g, 9800 + 60 * g) for g in (1, 16, 64, 128, 256, 345)]
    assert ladder.classify_rungs(rising)[0] is None
    # fewer than three rungs still works
    assert ladder.classify_rungs([rung(1, 100), rung(2, 10)])[0] == 2 * GIB


def test_cal_08_qd90():
    assert ladder.qd90({1: 3000, 8: 23700, 32: 23900}) == 8  # r1002: 23.7k at QD8, 23.9k at QD32
    assert ladder.qd90({1: 3000, 8: 15000, 32: 23900, 64: 24000}) == 32
    assert ladder.qd90({1: 1000}) == 1


def test_pla_03_cached_tier_factor_and_best_rung():
    assert ladder.cached_tier_factor(R1002[:7]) == pytest.approx(97850 / 46315)
    assert ladder.cached_tier_factor(R1002) == pytest.approx(97850 / 24000)
    assert ladder.best_read_rung(R1002) == 97850


def test_cal_07_fresh_penalty():
    fvr = ladder.fresh_vs_rewrite("device", 71584.0, 3596.0, None)
    assert fvr["penalty"] == pytest.approx(19.9, rel=0.01) and fvr["label"] == "fresh"
    assert ladder.fresh_vs_rewrite("file", 100.0, 50.0, "false")["label"] == "fresh_fs_alloc"


def test_cal_07_write_cap_uses_min():
    dev = ladder.fresh_vs_rewrite("device", 71584.0, 3596.0, None)
    assert ladder.write_iops_basis(71584.0, dev) == 3596.0
    assert ladder.write_iops_basis(71584.0, None) == 71584.0
    # a file target counts fresh_fs_alloc only on a discard mount
    off = ladder.fresh_vs_rewrite("file", 71584.0, 3596.0, "false")
    on = ladder.fresh_vs_rewrite("file", 71584.0, 3596.0, "true")
    assert not ladder.fresh_in_basis(off) and ladder.fresh_in_basis(on)
    assert ladder.write_iops_basis(71584.0, off) == 71584.0 and ladder.write_iops_basis(71584.0, on) == 3596.0
    assert not ladder.fresh_in_basis({"measured": False})
    # fresh faster than rewrite never raises the basis
    assert ladder.write_iops_basis(1000.0, ladder.fresh_vs_rewrite("device", 1000.0, 5000.0, None)) == 1000.0


def test_cal_07_fresh_extent_is_disjoint():
    size = 435853320192
    usable = size - 1048576
    ws = int(0.85 * usable)
    ext = ladder.fresh_extent(usable, ws)
    assert ext[0] >= ws and ext[1] <= size - 1048576 and ext[1] - ext[0] == 8 * GIB
    assert ext[0] % 1048576 == 0
    small = ladder.fresh_extent(10 * GIB, 8 * GIB)
    assert small == (8 * GIB, 8 * GIB + (10 * GIB - 8 * GIB - 1048576) // 1048576 * 1048576)
    assert ladder.fresh_extent(10 * GIB, 10 * GIB) is None


def test_cal_10_rearm_cell():
    assert ladder.rearm_verdict(71584.0, 3596.0, False)["verdict"] == "rearmed"
    assert ladder.rearm_verdict(71584.0, 60000.0, False)["verdict"] == "not_rearmed"
    moved = ladder.rearm_verdict(71584.0, 3596.0, True)
    assert moved["verdict"] == "ceiling_moved" and moved["rearm_ratio"] == pytest.approx(19.9, rel=0.01)


def test_cal_05_observation_limits():
    assert ladder.observation_plan("standard") == {"write": 240, "read": 120, "fresh": 120}
    assert ladder.observation_plan("quick") == ladder.observation_plan("standard")
    assert ladder.observation_plan("thorough") == {"write": 900, "read": 240, "fresh": 120}
    assert ladder.observation_limit(240.0) == "bucket_exclusion_s<=240"


def test_cal_06_bps_basis_rules():
    cell = {"steady_bps": 4096 * 1024 * 1024.0, "rate_limited": True}
    # proven read cache state: scaled by cache_ratio (<= 1)
    b = ladder.bps_basis("read", cell, cache_ratio=0.25)
    assert (b.value, b.status) == (1024 * 1024 * 1024.0, "proven")
    assert ladder.bps_basis("read", cell, cache_ratio=3.0).value == cell["steady_bps"]  # never above the cell
    # unproven: not scaled, cache_residual_possible
    u = ladder.bps_basis("read", cell, cache_ratio=None)
    assert (u.value, u.status, u.reasons) == (cell["steady_bps"], "unproven", ("cache_residual_possible",))
    flat_free = ladder.bps_basis("read", {"steady_bps": 5e8, "rate_limited": False}, cache_ratio=None)
    assert flat_free.reasons == ("cache_residual_possible",)
    # missing cell
    m = ladder.bps_basis("read", None, cache_ratio=0.5)
    assert (m.value, m.status, m.reason) == (None, "absent", "bps_basis_missing")
    assert m.as_dict() == {"value": None, "status": "absent", "reason": "bps_basis_missing"}
    # writes: status of the time proof, never scaled
    assert ladder.bps_basis("write", {"steady_bps": 14e6}, cache_ratio=0.1).status == "proven"
    w = ladder.bps_basis("write", {"steady_bps": 14e6}, cache_ratio=None, write_proven=False)
    assert (w.status, w.reasons, w.value) == ("unproven", ("short_observation",), 14e6)
    assert u.as_dict()["reasons"] == ["cache_residual_possible"]


def test_cal_02_confinement_argv():
    ext = Extents([(0, 10 * GIB)])
    assert ladder.confined(devio.parse_job(job("randread", off=GIB, size=GIB).argv()), ext)
    assert not ladder.confined(devio.parse_job(job("randread", off=9 * GIB + 1, size=GIB).argv()), ext)
    assert not ladder.confined(job("randread", off=20 * GIB, size=GIB), ext)
