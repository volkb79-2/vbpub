"""IOB-R2-01: calibration oracles for the cold region, the idle QD1 latency, ladder densification and file-target planning."""
import pytest

from iobench import guard, ladder, plan, plausibility
from iobench.calibrate import RUNG_S, Calibrator
from iobench.devio import FioJob, FioResult, FioStats
from iobench.errors import Aborted
from iobench.testing import DirScenario

from helpers import INVERSION, calibrate, config, make_host
from test_r2_calibrate import file_host

GIB = 1 << 30
MIB = 1 << 20

TWO_TIER = dict(capacity_gib=100, hit_iops=92000, write_allocate=True, tier2_capacity_gib=200, tier2_iops=20000)


def test_r2_cal_a_cold_region_of_exactly_one_mib_per_half_is_measured_not_refused():
    host = make_host(cache=None)
    run = Calibrator(host, config())
    run.s0(need_workdir=False)
    run.ws = 20 * MIB  # 10 % of it is 2 MiB: two halves of exactly 1 MiB
    host.devices["/dev/vda7"].written.add(0, run.ws)
    run.read_extent.add(0, run.ws)
    run.e_cold()
    assert run.cold_raw["region_bytes"] == 2 * MIB
    run.safety.release()


def test_r2_cal_the_idle_qd1_p99_is_the_latency_of_the_qd1_run_not_of_a_deeper_one():
    seen = []

    class Spy(Calibrator):
        def rung(self, ws, direction, qd, runtime=RUNG_S, tag=""):
            rec = super().rung(ws, direction, qd, runtime, tag)
            seen.append((direction, qd, rec["lat_us"]["p99"]))
            return rec

    run = Spy(make_host(cache=None), config())
    assert run.execute().status == "complete"
    depth = {(d, qd): p99 for d, qd, p99 in seen if (d, qd) in {(d, q) for d in ("read", "write") for q in (1, 8)}}
    for direction in ("read", "write"):
        assert run.qd1_p99[direction] == depth[(direction, 1)]
        assert depth[(direction, 1)] != depth[(direction, 8)]


def _file_calibration_with_write(write, *, little_factor_by_qd=None):
    from test_r2_calibrate import file_host

    host = file_host()
    host.scenario.write = write
    host.scenario.noise_cv = 0.0
    if little_factor_by_qd is not None:
        host.scenario.little_factor_by_qd = little_factor_by_qd
    run = Calibrator(host, config(target="/bench/target.img", work_dir="/bench/w"))
    out = run.execute()
    assert out.status == "complete", out.message
    return run, out


@pytest.mark.parametrize(
    ("depth_failure", "qd32_failure", "caps_fail"),
    [(True, True, True), (True, False, False), (False, True, True)],
    ids=("depth-and-main", "depth-only", "main-only"),
)
def test_r2_cal_pla_06_merges_degradation_from_every_failing_rung(monkeypatch, depth_failure, qd32_failure, caps_fail):
    """A later hard QD32 failure keeps its cap effect even when a depth failure was recorded first."""
    original = plausibility.pla_06
    run = Calibrator(make_host(cache=INVERSION), config())

    def controlled(iops, latency_us, qd, *, job_name=None, degrade_caps=True, **kwargs):
        if depth_failure and qd == 1 and job_name and "rung-w1-" in job_name and job_name.endswith("-d"):
            return original(1600.0, 3.5, qd, job_name=job_name, degrade_caps=False)
        if qd32_failure and qd == 32 and job_name and f"rung-w32-{run.ws // MIB}" in job_name:
            return original(1600.0, 40_000.0, qd, job_name=job_name, degrade_caps=True)
        return original(iops, latency_us, qd, job_name=job_name, degrade_caps=degrade_caps, **kwargs)

    monkeypatch.setattr(plausibility, "pla_06", controlled)
    out = run.execute()
    assert out.status == "complete", out.message
    check = next(c for c in out.result["quality"]["checks"] if c["id"] == "IOB-PLA-06")
    assert check["status"] == "fail"
    assert ("caps" in check["degrades"]) is caps_fail
    if depth_failure:
        assert out.calibration["calibration"]["ladder"]["qd90"]["write"] is None
    caps = out.result["proposals"]["caps"]["dev_aggregate"]
    assert all((v.get("reason") == "stage_failed") is caps_fail for v in caps.values())


def test_r2_cal_a_v1001_qd1_write_replay_uses_total_latency_and_produces_a_depth_verdict():
    # The retained v1001 record supplies 1.6k IOPS, 3.5 us mean clat and 3.888 us p99.
    # Its slat/lat fields were not retained; about 620 us slat is a replay assumption, not
    # measured evidence. The end-to-end oracle asserts the resulting calibration verdict.
    write = DirScenario(iops_max=2600, qd_iops={1: 1600, 8: 2600, 32: 2600}, lat_us=3.5, slat_us=620.0)
    run, out = _file_calibration_with_write(write)
    job_suffix = f"rung-w1-{run.ws // MIB}-d"
    replay = next(row for row in out.result["quality"]["little_law_rungs"] if row["job"].endswith(job_suffix))
    assert replay["qd"] == 1 and replay["iops"] == pytest.approx(1600.0, rel=0.01)
    assert replay["clat_mean_us"] == pytest.approx(3.5)
    assert replay["slat_mean_us"] == pytest.approx(620.0)
    assert replay["lat_mean_us"] == pytest.approx(623.5)
    assert replay["status"] == "pass"
    assert out.calibration["calibration"]["ladder"]["qd90"]["write"] == 8
    assert not any(row.get("job", "").endswith(job_suffix) for row in out.calibration["calibration"]["ladder"]["excluded_rungs"])
    assert out.calibration["quality"]["little_law_rungs"] == out.result["quality"]["little_law_rungs"]
    depth_rows = [row for row in out.result["quality"]["little_law_rungs"] if row["job"].endswith("-d")]
    assert out.calibration["calibration"]["ladder"]["depth_rungs"] == depth_rows


def test_r2_cal_a_real_write_depth_failure_is_excluded_from_qd90_and_does_not_publish_its_p99():
    write = DirScenario(iops_max=2600, qd_iops={1: 1600, 8: 2600, 32: 2600}, lat_us=3.5)
    run, out = _file_calibration_with_write(write)
    job_suffix = f"rung-w1-{run.ws // MIB}-d"
    replay = next(row for row in out.result["quality"]["little_law_rungs"] if row["job"].endswith(job_suffix))
    assert replay["status"] == "fail" and replay["qd"] == 1
    assert replay["iops"] == pytest.approx(1600.0, rel=0.01)
    assert replay["clat_mean_us"] == pytest.approx(3.5) and replay["slat_mean_us"] == pytest.approx(0.0)
    assert replay["lat_mean_us"] == pytest.approx(3.5)
    assert 1 not in run.depth["write"] and 8 in run.depth["write"] and 32 in run.depth["write"]
    ladder_doc = out.calibration["calibration"]["ladder"]
    assert ladder_doc["qd90"]["write"] is None and ladder_doc["qd90"]["read"] is not None
    assert ladder_doc["idle_qd1_p99_us"]["write"] is None
    excluded = [row for row in ladder_doc["excluded_rungs"] if row.get("job", "").endswith(job_suffix)]
    assert len(excluded) == 1 and excluded[0]["qd"] == 1 and excluded[0]["check"] == "IOB-PLA-06"
    check = next(c for c in out.result["quality"]["checks"] if c["id"] == "IOB-PLA-06")
    for expected in (job_suffix, "QD1", "iops=", "mean clat=", "mean slat=", "mean lat="):
        assert expected in check["detail"]
    assert "caps" not in check["degrades"]
    caps = out.result["proposals"]["caps"]["dev_aggregate"]
    assert all(v.get("reason") != "stage_failed" for v in caps.values())


def test_r2_cal_missing_latency_from_supported_fio_is_a_measurement_defect():
    run = Calibrator(make_host(cache=None), config())
    job = FioJob("rung-w1-1-d", "/dev/vda7", "randwrite", 4096, 1, 10, 0, GIB)
    stats = FioStats("write", 1600.0, 6_553_600.0, 4096 * 16000, 10.0, 3.5,
                     {"99.000000": 3.888}, slat_mean_us=None, lat_mean_us=None, fio_version="fio-3.36")
    result = FioResult(job, 0, stats, [], None, 0.0)
    rec = run.judged(result, {"dir": "write", "ws_bytes": GIB}, 32)
    check = run.little[-1]
    assert rec["little_failed"] and check.status == "fail"
    assert "not_applicable: fio did not report lat/slat" in check.detail
    assert "measurement defect" in check.detail and "fio-3.36" in check.detail
    assert check.degrades == ("caps",)


def test_r2_cal_failed_run_keeps_little_law_and_depth_rung_partial_records():
    class AbortAfterAnalysis(Calibrator):
        def analyze(self):
            super().analyze()
            raise Aborted("test after ladder analysis")

    run = AbortAfterAnalysis(make_host(cache=INVERSION), config())
    out = run.execute()

    assert out.status == "aborted"
    partial = out.calibration["partial"]
    rows = partial["little_law_rungs"]
    depth_rows = [row for row in rows if row["job"].endswith("-d")]
    assert depth_rows
    assert partial["ladder"]["depth_rungs"] == depth_rows


def test_r2_cal_a_ladder_that_falls_below_the_cliff_ratio_is_densified_once_between_the_last_good_rung_and_the_largest():
    host = make_host(cache=TWO_TIER)
    run = Calibrator(host, config())
    assert run.execute().status == "complete"
    sizes = [r["ws_bytes"] for r in run.reads]
    planned = plan.rung_plan(run.fill_info["bytes"])
    # the 64 GiB rung is the first below 75 % of the median of the first three; 32 GiB is the last rung at or above that threshold
    median3 = sorted(r["steady_iops"] for r in run.reads[:3])[1]
    by_size = {r["ws_bytes"]: r["steady_iops"] for r in run.reads}
    assert by_size[32 * GIB] >= ladder.CLIFF_RATIO * median3 > by_size[64 * GIB]
    extra = plan.densification_rungs(32 * GIB, max(planned))
    assert sorted(sizes) == sorted(set(planned) | set(extra)) and len(sizes) == len(planned) + 2


def _planned_file_run(first_bytes, *, profile="standard"):
    """A file-target calibrator planned so that the first extent (what the fill may write) is ``first_bytes``."""
    host = file_host()
    run = Calibrator(host, config(target="/bench/target.img", work_dir="/bench/w", profile=profile))
    run.s0(need_workdir=False)
    run.target = guard.load_target(host, "/bench/target.img")
    fill_s = 60 if run.quick else plan.FILL_BUDGET_FRACTION * run.cfg.budget_s(plan.PROFILE_BUDGET_S[profile])
    run.r_est = first_bytes / fill_s
    run.make_plan()
    run.safety.release()
    return run


def test_r2_cal_a_file_target_keeps_a_fresh_extent_of_exactly_one_mib_and_drops_a_smaller_one():
    exact = _planned_file_run(10 * MIB + 5)  # 10 % of it is 1 MiB and a few bytes
    assert exact.fresh is not None and exact.fresh[1] - exact.fresh[0] == MIB
    smaller = _planned_file_run(10 * MIB - 10)
    assert smaller.fresh is None


def test_r2_cal_a_quick_profile_never_plans_a_fresh_extent_even_for_a_file_with_room_for_one():
    assert _planned_file_run(100 * MIB, profile="quick").fresh is None
    assert _planned_file_run(100 * MIB).fresh is not None


def test_r2_cal_a_shape_grid_cut_by_the_budget_marks_the_run_limited_by_budget():
    out = calibrate(make_host(cache=INVERSION), budget_minutes=48)
    cells = out.calibration["calibration"]["shape"]["cells"]
    assert 0 < len(cells) < 26
    assert out.result["quality"]["cache_proof"] == "limited-by-budget" and "limited-by-budget" in out.result["quality"]["limits"]


def test_r2_cal_shape_cells_of_thirty_seconds_are_logged_every_two_seconds_and_shorter_ones_every_second():
    from helpers import fio_jobs
    host = make_host(cache=INVERSION)
    calibrate(host)
    s6 = [j for j in fio_jobs(host) if "-s6-" in j.name]
    assert {j.runtime_s for j in s6} == {15, 30}
    assert all(j.log_avg_ms == (1000 if j.runtime_s < RUNG_S else 2000) for j in s6)


def test_r2_cal_a_budget_that_allows_exactly_three_rungs_ends_the_ladder_there_instead_of_failing_the_run():
    run = Calibrator(make_host(cache=INVERSION), config(budget_minutes=9))
    out = run.execute()
    assert out.status == "complete" and len(run.reads) == 3 and run.budget_limited
    assert out.result["quality"]["cache_proof"] == "limited-by-budget"


def test_r2_cal_without_a_closing_shape_reference_the_fresh_write_check_still_names_the_missing_cell():
    import copy
    base = Calibrator(make_host(cache=INVERSION), config())
    base.execute()
    run = copy.copy(base)
    run.cells = [c for c in base.cells if c["block_state"] != "fresh"]
    run.moved_dirs = {k: v for k, v in base.moved_dirs.items() if k != "shape"}
    check = {c.id: c for c in run.bytes_checks({})}["IOB-PLA-18"]
    assert check.status == "not_applicable" and check.detail == "write: no fresh sequential 1 MiB cell"
