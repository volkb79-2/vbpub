"""IOB-R2-01: the dry-run plan states exactly what a real run would run (direction, histogram, bound, file path, profile fan-out)."""
import re

import pytest

from iobench import dryrun, guard, ladder, shape
from iobench.devio import parse_job
from iobench.matrix import RDC_QD, Matrixer

from helpers import INVERSION, calibrate, config, make_host

MIB = 1 << 20


def short(job):
    return re.sub(r"^run-[0-9TZ]+-[0-9a-f]+-", "", job.name)


def plan_jobs(**kw):
    out = dryrun.calibrate_plan(make_host(cache=INVERSION), config(assume_r_est=1.36e9, **kw))
    return {s["name"]: [(parse_job(j["argv"]), j["written_bytes_bound"]) for j in s["jobs"]] for s in out["stages"]}


def test_r2_dryrun_histogram_jobs_are_the_latency_probes_and_only_those():
    jobs = plan_jobs()
    hist = {short(j) for stage in jobs.values() for j, _ in stage if "--log_hist_msec=1000" in j.argv()}
    assert hist == {"ecold-qd1", "hitref", "e2-qd1"}
    for stage in ("E-cold", "E2"):
        for j, _ in jobs[stage]:
            assert ("--write_hist_log=" + j.prefix in j.argv()) == (short(j) != "ecold")


def test_r2_dryrun_ladder_qd_reads_read_and_writes_write_with_a_bound_only_on_writes():
    jobs = plan_jobs()["ladder-qd"]
    assert [(short(j), j.rw, j.iodepth) for j, _ in jobs] == [("rung-r1-d", "randread", 1), ("rung-r8-d", "randread", 8),
                                                            ("rung-w1-d", "randwrite", 1), ("rung-w8-d", "randwrite", 8)]
    assert [b is None for _, b in jobs] == [True, True, False, False]
    assert all(j.io_size == b for j, b in jobs if b is not None)


def test_r2_dryrun_thorough_profile_adds_the_qd64_rungs_and_standard_does_not():
    standard = [j.iodepth for j, _ in plan_jobs()["ladder-qd"]]
    thorough = [(j.rw, j.iodepth) for j, _ in plan_jobs(profile="thorough")["ladder-qd"]]
    assert standard == [1, 8, 1, 8]
    assert thorough == [("randread", 1), ("randread", 8), ("randread", 64), ("randwrite", 1), ("randwrite", 8), ("randwrite", 64)]


def test_r2_dryrun_reference_pair_reads_and_writes_in_that_order_and_bounds_only_the_write():
    (read, read_bound), (write, write_bound) = plan_jobs()["reference"]
    assert (short(read), read.rw, read_bound) == ("ref-read", "randread", None)
    assert (short(write), write.rw) == ("ref-write", "randwrite") and write_bound == write.io_size and write_bound > 0


@pytest.mark.parametrize("quick", [False, True])
def test_r2_dryrun_s6_plans_exactly_the_rewrite_cells_with_a_bound_on_the_writers(quick):
    cells = [c for c in shape.grid_plan(quick=quick) if c.block_state == "rewrite"]
    assert cells and (quick or len(cells) < len(shape.grid_plan(quick=quick)))
    jobs = plan_jobs(profile="quick" if quick else "standard")["S6"]
    assert [(j.rw, j.bs, j.iodepth, j.runtime_s) for j, _ in jobs] == [(c.rw, c.bs, c.qd, c.seconds) for c in cells]
    assert [b is not None for _, b in jobs] == [c.dir == "write" for c in cells]
    assert [j.io_size is not None for j, _ in jobs] == [c.dir == "write" for c in cells]  # the written-bytes cap is in the argv of writers only
    assert any(c.dir == "read" for c in cells) and any(c.dir == "write" for c in cells)


def test_r2_dryrun_a_quick_plan_has_no_extra_stages_but_the_same_reference():
    jobs = plan_jobs(profile="quick")
    assert "E-cold" not in jobs and "ladder-qd" not in jobs and jobs["reference"]


def test_r2_dryrun_matrix_plan_uses_the_work_dir_for_the_load_files_and_a_placeholder_without_one():
    host = make_host(cache=INVERSION)
    calibrate(host)
    named = dryrun.matrix_plan(host, config(work_dir="/bench/w"))
    s3 = [parse_job(j["argv"]) for j in named["stages"][1]["jobs"]]
    stamp = r"run-[0-9TZ]+-[0-9a-f]+"
    assert [re.fullmatch(rf"/bench/w/iobench-{stamp}-s3-{k}", j.filename) is not None for j, k in zip(s3, "wf")] == [True, True]
    bare = dryrun.matrix_plan(host, config(work_dir=None))
    assert all(re.fullmatch(rf"<work-dir>/iobench-{stamp}-s3-[wf]", parse_job(j["argv"]).filename) for j in bare["stages"][1]["jobs"])


def test_iob_bl_13_the_dry_run_jobs_are_the_real_runs_jobs():
    host = make_host(cache=INVERSION)
    calibrate(host)
    cfg = config(work_dir="/bench/w")
    run = Matrixer(host, cfg)
    run.r_est = 1.0e9
    run.target = guard.load_target(host, cfg.target)
    writer, fsyncj = run.load_pair("s3", 30)
    assert run.safety.files == []  # a plan journals nothing
    run.safety.acquire()
    run.safety.begin()
    real = run.load_jobs("s3", 30)
    assert real == (writer, fsyncj) and run.safety.files == [writer.filename, fsyncj.filename]
    planned = dryrun.matrix_plan(host, cfg)
    for stage in planned["stages"][1:]:
        for entry in stage["jobs"]:
            job = parse_job(entry["argv"])
            assert job.name.startswith("run-") and (job.prefix or "").startswith("/run/iobench/run-") and "iobench-load" not in job.filename
    assert [parse_job(e["argv"]).iodepth for e in planned["stages"][3]["jobs"]] == [RDC_QD] * 3


def test_r2_dryrun_matrix_load_jobs_are_buffered_not_direct():
    host = make_host(cache=INVERSION)
    calibrate(host)
    for stage in dryrun.matrix_plan(host, config())["stages"][1:3]:
        for entry in stage["jobs"]:
            assert "--direct=0" in entry["argv"], entry["argv"]


def test_r2_dryrun_a_stored_calibration_alone_is_checked_for_staleness():
    host = make_host(cache=INVERSION)
    calibrate(host)
    host.remove("/var/lib/iobench/result.json")  # a calibration exists, no run result does
    assert dryrun.rate_source(host, config()) > 0
    host.put("/sys/devices/pci0/block/vda/size", f"{(500 << 30) // 512 + 2048}\n")  # another disk size: another disk (a partition's PARTUUID is not identity)
    with pytest.raises(Exception) as err:
        dryrun.rate_source(host, config())
    assert err.value.condition == "dry_run_rate_unusable"
