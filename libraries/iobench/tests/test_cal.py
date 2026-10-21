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
    assert ladder.observation_plan("standard") == {"write": 240, "read": 120, "fresh": 150}
    assert ladder.observation_plan("quick") == ladder.observation_plan("standard")
    assert ladder.observation_plan("thorough") == {"write": 900, "read": 240, "fresh": 150}
    assert ladder.observation_limit(240.0) == "bucket_exclusion_s<=240"


def test_cal_06_bps_basis_rules():
    cell = {"steady_bps": 4096 * 1024 * 1024.0, "rate_limited": True}
    CRP = ("cache_residual_possible",)
    kw = dict(read_reasons=CRP, write_reasons=())
    # proven read cache state: scaled by cache_ratio (<= 1)
    b = ladder.bps_basis("read", cell, cache_ratio=0.25, **kw)
    assert (b.value, b.status) == (1024 * 1024 * 1024.0, "proven")
    assert ladder.bps_basis("read", cell, cache_ratio=3.0, **kw).value == cell["steady_bps"]  # never above the cell
    # unproven: not scaled, with the REAL reasons of the cache state (quick profile, undetermined ...), none invented
    u = ladder.bps_basis("read", cell, cache_ratio=None, **kw)
    assert (u.value, u.status, u.reasons) == (cell["steady_bps"], "unproven", ("cache_residual_possible",))
    flat_free = ladder.bps_basis("read", {"steady_bps": 5e8, "rate_limited": False}, cache_ratio=None, **kw)
    assert flat_free.reasons == ("cache_residual_possible",)
    assert ladder.bps_basis("read", cell, cache_ratio=None, read_reasons=("quick_profile",), write_reasons=()).reasons == ("quick_profile",)
    # missing cell
    m = ladder.bps_basis("read", None, cache_ratio=0.5, **kw)
    assert (m.value, m.status, m.reason) == (None, "absent", "bps_basis_missing")
    assert m.as_dict() == {"value": None, "status": "absent", "reason": "bps_basis_missing"}
    # writes: the reasons of the write basis, never scaled
    assert ladder.bps_basis("write", {"steady_bps": 14e6}, cache_ratio=0.1, **kw).status == "proven"
    w = ladder.bps_basis("write", {"steady_bps": 14e6}, cache_ratio=None, read_reasons=CRP, write_reasons=("short_observation", "budget_limited"))
    assert (w.status, w.reasons, w.value) == ("unproven", ("short_observation", "budget_limited"), 14e6)
    assert u.as_dict()["reasons"] == ["cache_residual_possible"]


def test_cal_02_confinement_unit():
    ext = Extents([(0, 10 * GIB)])
    assert ladder.confined(devio.parse_job(job("randread", off=GIB, size=GIB).argv()), ext)
    assert not ladder.confined(devio.parse_job(job("randread", off=9 * GIB + 1, size=GIB).argv()), ext)
    assert not ladder.confined(job("randread", off=20 * GIB, size=GIB), ext)


# ---------------------------------------------------------------------------------------------------------
# stage-level (the full calibration on FakeHost)
# ---------------------------------------------------------------------------------------------------------
from helpers import (INVERSION, READ_MISS, WRITE_14, calibrate, config, fio_jobs, make_host, read_json)  # noqa: E402
from iobench import calibrate as cal_mod  # noqa: E402

CALIB = "/var/lib/iobench/calibration.json"
RESULT = "/var/lib/iobench/result.json"


@pytest.fixture(scope="module")
def healthy():
    host = make_host(cache=INVERSION)
    out = calibrate(host)
    return host, out


def test_cal_09_complete_definition(healthy):
    host, out = healthy
    assert out.status == "complete" and out.exit_code == 0
    cal = read_json(host, CALIB)
    assert cal["run"]["status"] == "complete" and cal["calibration"]["id"] == cal["run"]["calibration_id"]
    stages = cal["run"]["stages"]
    assert all(stages[s] == "ok" for s in ("S0", "S1", "fill", "ladder")) and stages["S7"] == "ok"
    assert read_json(host, RESULT)["run"]["calibration_id"] == cal["calibration"]["id"]
    # every key of the calibration example is present
    for key in ("target", "touch_map", "extent_written", "fresh_extent", "fill_bps", "R_est_bps", "ladder", "cache", "cap_basis",
                "fresh_vs_rewrite", "shape", "latency_curve", "latency_knee", "iocost_keys"):
        assert key in cal["calibration"], key


def test_cal_09_optional_stages_may_be_skipped_and_recorded():
    host = make_host(cache=INVERSION)
    out = calibrate(host, budget_minutes=20)
    stages = out.calibration["run"]["stages"]
    skipped = [k for k, v in stages.items() if v.startswith("skipped")]
    assert out.status == "complete" and skipped, stages
    assert "limited-by-budget" in out.result["quality"]["limits"]
    for s in ("S6", "S7"):
        if stages.get(s, "").startswith("skipped"):
            assert f"stage_skipped:{s}" in out.result["quality"]["limits"]


def test_cal_09_atomic_no_replace():
    host = make_host(cache=INVERSION)
    first = calibrate(host)
    good = host.read_text(CALIB)
    host.crash_after = host.fio_spawns + 5
    with pytest.raises(Exception):
        calibrate(host)
    # a crashed run restored its state and never replaced the good calibration
    assert host.read_text(CALIB) == good and first.status == "complete"
    host2 = make_host(cache=INVERSION)
    host2.makedirs("/run/iobench")
    host2.at(host2.now() + 400, lambda: host2.deliver_signal("SIGTERM"))
    out = calibrate(host2)
    assert out.status == "aborted" and out.exit_code == 8
    assert not host2.exists(CALIB) and read_json(host2, "/var/lib/iobench/calibration.last-failed.json")["run"]["status"] == "aborted"


def test_cal_02_confinement_argv(healthy):
    host, out = healthy
    extent_end = read_json(host, CALIB)["calibration"]["extent_written"][0][1]
    fresh = read_json(host, CALIB)["calibration"]["fresh_extent"]
    jobs = fio_jobs(host)
    reads = [j for j in jobs if j.filename == "/dev/vda7" and not j.writes]
    assert reads and all(0 <= j.offset and j.end <= extent_end for j in reads)
    assert not host.devices["/dev/vda7"].unwritten_reads
    writes = [j for j in jobs if j.filename == "/dev/vda7" and j.writes]
    assert all(j.end <= extent_end or (fresh and fresh[0] <= j.offset and j.end <= fresh[1]) or j.end <= 1 << 30 for j in writes)
    assert all(j.end <= host.devices["/dev/vda7"].size - 1048576 for j in jobs if j.filename == "/dev/vda7")


def test_cal_02_interrupted_fill_records_actual_bytes():
    host = make_host(cache=INVERSION)
    ws = int(0.85 * (435853320192 - 1048576))
    host.scenario.cut_after_bytes = {"-fill-": int(0.4 * ws)}
    out = calibrate(host)
    cal = out.calibration["calibration"]
    end = cal["extent_written"][0][1]
    assert out.status == "complete" and end == pytest.approx(0.4 * ws, rel=0.01) and cal["extent_written"][0][0] == 0
    sizes = {r["ws_bytes"] for r in cal["ladder"]["rungs"]}
    assert max(sizes) <= end and cal["fill"]["bytes"] == end
    props = out.result["proposals"]["caps"]["dev_aggregate"]
    assert "budget_limited" in props["RIOPS"]["reasons"] and props["RIOPS"]["status"] == "unproven"
    assert "limited-by-budget" in out.result["quality"]["limits"] and out.result["quality"]["cache_proof"] == "limited-by-budget"
    assert not host.devices["/dev/vda7"].unwritten_reads


def test_cal_02_fill_replans_on_burst_capped_disk():
    from iobench.testing import Bucket
    write = dict(iops_max=1e6, lat_us=440, bucket=Bucket(2e9, 14 * 1048576, 20.0))
    host = make_host(size=60 * GIB, write=write, read=dict(iops_max=3000, lat_us=760), noise=0.02)
    out = calibrate(host)
    cal = out.calibration["calibration"]
    assert out.status == "complete" and cal["fill"]["fill_replanned"] is True
    assert cal["R_est_bps"] < 0.2 * 2e9 and cal["R_est_bps"] == pytest.approx(14 * 1048576, rel=0.2)
    ws = cal["extent_written"][0][1]
    assert ws < 0.85 * (60 * GIB) and "limited-by-budget" in out.result["quality"]["limits"]
    # every later write job obeys 1.2 x the measured rate x its seconds
    for j in fio_jobs(host):
        if j.writes and j.io_size is not None and "-fill-" not in j.name:
            assert j.io_size <= 1.2 * cal["R_est_bps"] * j.runtime_s * 1.001, j.name


def test_cal_03_unsteady_rung_is_extended_then_kept_unsteady():
    host = make_host(cache=None, noise=0.6)
    out = calibrate(host)
    assert out.status == "complete"
    ext = [j for j in fio_jobs(host) if j.name.endswith("-ext")]
    assert ext and all(j.runtime_s == 300 for j in ext)
    rungs = out.calibration["calibration"]["ladder"]["rungs"]
    assert any(r["state"] == "unsteady" and r.get("extended") for r in rungs)
    caps = out.result["proposals"]["caps"]["dev_aggregate"]
    assert all(v["status"] == "absent" and v["reason"] == "basis_unsteady" for v in caps.values())  # no fallback to a smaller rung
    assert any(c["id"] == "IOB-PLA-01" and c["status"] == "fail" for c in out.result["quality"]["checks"])


def test_cal_05_observation_limits(healthy):
    host, out = healthy
    assert "bucket_exclusion_s<=240" in out.result["quality"]["limits"]
    obs = {"obs-" + j.name.split("-obs-")[1]: j for j in fio_jobs(host) if "-obs-" in j.name}
    assert {k: v.runtime_s for k, v in obs.items()} == {"obs-write": 240, "obs-read": 120, "obs-fresh": 150}
    assert all(j.iodepth == 32 and j.log_avg_ms == 1000 for j in obs.values())  # QD32: the depth of the cap it time-proves (review B-B3)
    thorough = make_host(size=40 * GIB, cache=None, read=dict(iops_max=21600, lat_us=300), write=dict(iops_max=21600, lat_us=300))
    out2 = calibrate(thorough, profile="thorough")
    assert "bucket_exclusion_s<=900" in out2.result["quality"]["limits"]
    assert 64 in {j.iodepth for j in fio_jobs(thorough)}


def test_cal_06_cap_basis(healthy):
    host, out = healthy
    cal = out.calibration["calibration"]
    basis = cal["cap_basis"]
    largest = cal["ladder"]["measured_largest"]["read"]
    assert basis["read"]["value"] <= largest
    fvr = cal["fresh_vs_rewrite"]
    assert basis["write"]["value"] == min(fvr["rewrite_iops"], fvr["fresh_iops"])
    assert set(basis["bps"]) == {"read", "write"} and basis["bps"]["read"]["status"] in ("proven", "unproven")


def test_cal_07_fresh_penalty():
    write = dict(WRITE_14, fresh_penalty=19.9, iops_max=71584)
    host = make_host(cache=None, write=write, read=dict(iops_max=21600, lat_us=300), size=40 * GIB)
    out = calibrate(host)
    fvr = out.calibration["calibration"]["fresh_vs_rewrite"]
    assert fvr["measured"] and fvr["penalty"] == pytest.approx(19.9, rel=0.15) and fvr["label"] == "fresh"
    notes = {n["note"] for n in out.result["proposals"]["notes"]}
    assert "fresh_write_penalty" in notes
    wb = out.calibration["calibration"]["cap_basis"]["write"]
    # the cap is the minimum of its recorded inputs (the 30 s QD32 runs and the time-proven observation series)
    assert wb["value"] == min(v for v in wb["inputs"].values() if v is not None) and wb["value"] == pytest.approx(fvr["fresh_iops"], rel=0.02)
    # discard unsupported -> not_measured with the reason
    host2 = make_host(cache=None, write=write, read=dict(iops_max=21600, lat_us=300), size=40 * GIB)
    host2.devices["/dev/vda7"].discard_supported = False
    out2 = calibrate(host2)
    fvr2 = out2.calibration["calibration"]["fresh_vs_rewrite"]
    assert fvr2 == {"measured": False, "reason": "discard_unsupported"}
    assert out2.calibration["run"]["stages"]["fresh-vs-rewrite"] == "skipped: discard_unsupported"  # not "failed": the target simply has no discard
    assert "fresh_not_measured" in out2.calibration["calibration"]["cap_basis"]["write"]["reasons"]
    assert out2.calibration["calibration"]["rearm"] is None


def test_cal_07_fresh_extent_is_disjoint(healthy):
    host, out = healthy
    cal = out.calibration["calibration"]
    ws, fresh = cal["extent_written"][0][1], cal["fresh_extent"]
    assert fresh[0] >= ws and fresh[1] <= cal["target"]["usable_bytes"] and fresh[1] - fresh[0] == 8 * GIB
    discards = [a for a in host.argv_log if a[0] == "blkdiscard"]
    assert len(discards) >= 5 and all(int(a[2]) >= ws for a in discards)  # re-discarded before each fresh cell, never inside [0, WS_max)
    jobs = fio_jobs(host)
    for j in jobs:
        if j.filename == "/dev/vda7" and j.writes and j.end > ws:
            assert j.offset >= fresh[0] and j.end <= fresh[1]
    s6 = [j for j in jobs if "-s6-" in j.name]
    assert all(j.end <= ws or j.offset >= fresh[0] for j in s6)
    assert host.devices["/dev/vda7"].written.overlap(*fresh) == 0  # removed after use


def test_cal_10_rearm_cell():
    rearm_scn = dict(WRITE_14, fresh_penalty=19.9)
    host = make_host(cache=None, write=rearm_scn, read=dict(iops_max=21600, lat_us=300), size=40 * GIB)
    r = calibrate(host).calibration["calibration"]["rearm"]
    assert r["verdict"] == "rearmed" and r["rearm_ratio"] >= 3
    flat = make_host(cache=None, write=dict(WRITE_14), read=dict(iops_max=21600, lat_us=300), size=40 * GIB)
    r2 = calibrate(flat).calibration["calibration"]["rearm"]
    assert r2["verdict"] == "not_rearmed" and r2["rearm_ratio"] < 3
