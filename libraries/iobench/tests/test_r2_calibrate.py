"""IOB-R2-01: calibration oracles (fill, ladder densification, analysis, document assembly) for the survivors of the native R2 run."""
import math

import pytest

from iobench import calibrate as cal_mod
from iobench import guard, ladder
from iobench.calibrate import Calibrator, TouchMap
from iobench.errors import Refusal
from iobench.series import SeriesError
from iobench.testing import CacheModel, DirScenario, Scenario, standard_host

from helpers import CREATED, INVERSION, calibrate, config, fio_jobs, make_host

GIB = 1 << 30
MIB = 1 << 20
FILE_FS = "22222222-2222-2222-2222-222222222222"


def fills(host):
    return [j for j in fio_jobs(host) if "-fill-" in j.name]


def file_host(free=40 * GIB, size=6 * GIB):
    scn = Scenario(read=DirScenario(iops_max=21600, lat_us=300), write=DirScenario(iops_max=3600, lat_us=440, bps_cap_seq=14e6, bps_cap_rand=14e6), noise_cv=0.02)
    host = standard_host(scn, workdir_free=free)
    host.dynamic["/sys/fs/cgroup/game-server.slice/io.pressure"] = lambda: "some avg10=0.00 avg60=0.00 avg300=0.00 total=0\n"
    guard.init_file(host, "/bench/target.img", size=size, own=True, expect_fs_uuid=FILE_FS, confirm_quiet_window=False, allow_large_root_target=False, created=CREATED)
    return host


# -- the touch map -----------------------------------------------------------------------------------------------------------------------
def test_r2_cal_an_empty_range_touches_nothing_and_is_not_a_division_by_zero():
    touch = TouchMap()
    touch.add("x", 0, 0, 100.0, 800)
    assert touch.as_dict() == {"x": [0] * 8}


# -- S1 and a calibrator that has not run -------------------------------------------------------------------------------------------
def test_r2_cal_rearm_without_a_fresh_extent_is_a_series_error():
    host = make_host(cache=None)
    run = Calibrator(host, config())
    with pytest.raises(SeriesError, match="no fresh extent"):
        run.rearm()


def test_r2_cal_a_target_with_exactly_one_gib_usable_is_calibrated_and_one_block_less_is_refused():
    assert calibrate(make_host(size=GIB + MIB, cache=None)).status == "complete"
    with pytest.raises(Refusal) as err:
        calibrate(make_host(size=GIB + MIB - 4096, cache=None))
    assert err.value.condition == "target_too_small"


# -- the fill ------------------------------------------------------------------------------------------------------------------------------
def test_r2_cal_the_fill_stops_when_the_planned_extent_is_reached_without_a_zero_byte_job():
    host = make_host(cache=INVERSION)
    out = calibrate(host)
    jobs = fills(host)
    cal = out.calibration["calibration"]
    assert all(j.size > 0 and j.io_size > 0 for j in jobs)
    assert sum(j.size for j in jobs) == cal["fill"]["planned_bytes"] == cal["fill"]["bytes"]
    assert cal["fill"]["fill_aborted"] is None and cal["fill"]["fill_replanned"] is False


def test_r2_cal_a_fill_chunk_that_fio_cut_short_ends_the_fill_at_once():
    host = make_host(cache=INVERSION)
    chunk = 13_600_000_000
    host.scenario.cut_after_bytes = {"-fill-": 2 * chunk + chunk // 2}
    out = calibrate(host)
    jobs = fills(host)
    cal = out.calibration["calibration"]
    assert len(jobs) == 3 and cal["fill"]["bytes"] == cal["extent_written"][0][1]
    assert cal["fill"]["bytes"] == pytest.approx(2.5 * chunk, rel=0.01) and cal["fill"]["bytes"] < cal["fill"]["planned_bytes"]
    assert "limited-by-budget" not in out.result["quality"]["limits"]


def test_r2_cal_an_untouched_fill_that_wrote_everything_is_not_limited_by_budget():
    out = calibrate(make_host(cache=INVERSION))
    assert "limited-by-budget" not in out.result["quality"]["limits"] and out.result["quality"]["cache_proof"] != "limited-by-budget"


def test_r2_cal_a_burst_capped_disk_replans_the_fill_and_that_alone_marks_the_run_limited_by_budget():
    from iobench.testing import Bucket
    write = dict(iops_max=1e6, lat_us=440, bucket=Bucket(2e9, 14 * 1048576, 20.0))
    host = make_host(size=60 * GIB, write=write, read=dict(iops_max=3000, lat_us=760), noise=0.02)
    out = calibrate(host)
    cal = out.calibration["calibration"]
    assert cal["fill"]["fill_replanned"] is True and "limited-by-budget" in out.result["quality"]["limits"]
    assert cal["fill"]["bytes"] < cal["fill"]["planned_bytes"] and not cal["fill"]["fill_aborted"]


# -- a finished run, analysed again with the records the test wants -------------------------------------------------------------------------
@pytest.fixture(scope="module")
def finished():
    """One complete standard calibration (about a second); tests copy what they change."""
    run = Calibrator(make_host(cache=INVERSION), config())
    out = run.execute()
    assert out.status == "complete"
    return run, out


def reanalysed(run):
    run.checks = []
    run.analyze()
    return {c.id: c for c in run.checks}


def test_r2_cal_an_empty_direction_is_a_failed_run_whichever_direction_is_empty(finished):
    import copy
    run = copy.copy(finished[0])
    for reads, writes in (([], list(finished[0].writes)), (list(finished[0].reads), [])):
        run.reads, run.writes = reads, writes
        with pytest.raises(cal_mod.RunFailed, match="no usable ladder rung"):
            run.analyze()


def test_r2_cal_a_rung_whose_mean_overstates_the_steady_rate_by_more_than_ten_per_cent_is_listed(finished):
    import copy
    from iobench import plausibility
    run = copy.copy(finished[0])
    run.reads = [dict(r) for r in finished[0].reads]
    base = run.reads[0]["steady_iops"]
    run.reads[0]["mean_iops"] = plausibility.MEAN_REPORT_RATIO * base
    assert reanalysed(run)["IOB-PLA-04"].value == []
    run.reads[0]["mean_iops"] = math.nextafter(plausibility.MEAN_REPORT_RATIO * base, math.inf)
    assert reanalysed(run)["IOB-PLA-04"].value == ["read-1024M"]


def test_r2_cal_only_a_series_flagged_rate_limited_is_listed_as_flat_line(finished):
    import copy
    run = copy.copy(finished[0])
    run.reads = [dict(r) for r in finished[0].reads]
    run.reads[0]["rate_limited"] = True
    run.reads[1]["rate_limited"] = False
    assert reanalysed(run)["IOB-PLA-05"].value == ["read-1024M"]


def test_r2_cal_the_write_basis_is_proven_only_when_the_time_proof_ran(finished):
    import copy
    run = copy.copy(finished[0])
    run.reads, run.writes = [dict(r) for r in finished[0].reads], [dict(r) for r in finished[0].writes]
    run.fvr = finished[0].fvr
    run.analyze()
    assert run.write_proven is True and run.bases["write"]["status"] == "proven"
    run.obs = {k: v for k, v in finished[0].obs.items() if k != "write"}  # no observation: the cap cannot be time-proven
    run.analyze()
    assert run.write_proven is False and "budget_limited" in run.bases["write"]["reasons"]


def _decoys(cell):
    """Copies of a sequential 1 MiB rewrite write cell that each fail exactly one of the selection rules, carrying a recognisable rate."""
    bad = {"dir": "read", "pattern": "rand", "bs": 4096, "block_state": "fresh", "repeat": True, "state": "unsteady"}
    return [{**cell, "steady_bps": 1.0, key: value} for key, value in bad.items()]


def test_r2_cal_the_write_bytes_basis_comes_from_the_one_unrepeated_steady_sequential_1mib_rewrite_cell(finished):
    import copy
    run = copy.copy(finished[0])
    wanted = next(c for c in finished[0].cells if (c["dir"], c["pattern"], c["bs"], c["block_state"], c["repeat"]) == ("write", "seq", MIB, "rewrite", False))
    base = finished[0].bps_bases()
    run.cells = _decoys(wanted) + [dict(wanted)] + list(finished[0].cells)
    assert run.bps_bases()["write"] == base["write"]
    assert base["write"]["value"] == wanted["steady_bps"]


def test_r2_cal_the_read_bytes_basis_comes_from_the_one_unrepeated_steady_sequential_1mib_rewrite_read_cell(finished):
    import copy
    run = copy.copy(finished[0])
    wanted = next(c for c in finished[0].cells if (c["dir"], c["pattern"], c["bs"], c["block_state"], c["repeat"]) == ("read", "seq", MIB, "rewrite", False))
    decoys = [{**wanted, "steady_bps": 1.0, **change} for change in ({"dir": "write"}, {"pattern": "rand"}, {"bs": 4096}, {"block_state": "fresh"}, {"repeat": True}, {"state": "unsteady"})]
    run.cells = decoys + [dict(wanted)]
    got = run.bps_bases()["read"]
    run.cells = [dict(wanted)]
    assert got == run.bps_bases()["read"]


def test_r2_cal_the_write_windows_are_the_steady_sequential_1mib_rewrite_write_cells(finished):
    import copy
    run = copy.copy(finished[0])
    wanted = [c for c in finished[0].cells if (c["dir"], c["pattern"], c["bs"], c["block_state"]) == ("write", "seq", MIB, "rewrite") and c["state"] in ("steady", "flat")]
    assert len(wanted) >= 2
    decoy = {**wanted[0], "steady_bps": 1.0}
    run.cells = [{**decoy, "dir": "read"}, {**decoy, "pattern": "rand"}, {**decoy, "bs": 4096}, {**decoy, "block_state": "fresh"}, {**decoy, "state": "unsteady"}] + list(finished[0].cells)
    calibration, _ = run.assemble(finished[0].safety.report)
    assert calibration["calibration"]["windows"]["write_seq1m_bps"] == [c["steady_bps"] for c in wanted]


def test_r2_cal_without_a_fresh_sequential_cell_the_fresh_write_check_is_not_applicable_naming_what_is_missing(finished):
    import copy
    run = copy.copy(finished[0])
    run.cells = [c for c in finished[0].cells if c["block_state"] != "fresh"]
    history = {}
    checks = {c.id: c for c in run.bytes_checks(history)}
    assert checks["IOB-PLA-18"].status == "not_applicable" and "write: " in checks["IOB-PLA-18"].detail
    run.cells = [c for c in finished[0].cells if not (c["dir"] == "write" and c["pattern"] == "seq" and c["bs"] == MIB and c["block_state"] == "rewrite")]
    assert {c.id: c for c in run.bytes_checks(history)}["IOB-PLA-18"].status == "not_applicable"
    run.moved_dirs = {**finished[0].moved_dirs, "shape": {"read": True, "write": True}}
    run.cells = [c for c in finished[0].cells if c["block_state"] != "fresh"]
    assert {c.id: c for c in run.bytes_checks(history)}["IOB-PLA-18"].status == "not_applicable"


def test_r2_cal_the_document_of_a_run_without_cells_says_the_shape_stage_was_skipped_and_nothing_moved(finished):
    import copy
    run = copy.copy(finished[0])
    run.cells = []
    shape = run.shape_doc()
    assert shape["skipped"] is True and shape["drift"] == {"spread": {}, "moved": False} and shape["cells"] == []
    assert shape["metering"] == "undetermined" and shape["linear_model_fit"] == "insufficient"


def test_r2_cal_iocost_keys_carry_the_proposed_value_or_none_when_the_proposal_is_absent():
    for kw in ({}, {"profile": "quick"}):
        cal = calibrate(make_host(cache=INVERSION), **kw).calibration["calibration"]
        model = cal["proposals"]["iocost_model"]
        assert set(cal["iocost_keys"]) == set(model)
        for key, proposal in model.items():
            assert cal["iocost_keys"][key] == (None if proposal["status"] == "absent" else proposal["value"]), key
    standard = calibrate(make_host(cache=INVERSION)).calibration["calibration"]
    assert any(p["status"] == "absent" and standard["iocost_keys"][k] is None for k, p in standard["proposals"]["iocost_model"].items())


def test_r2_cal_the_targets_are_recorded_as_default_or_operator():
    assert calibrate(make_host(cache=INVERSION)).result["targets"]["source"] == "default"
    from iobench import proposals
    custom = {**proposals.DEFAULT_TARGETS, "P_ms": 100}
    out = calibrate(make_host(cache=INVERSION), targets=custom)
    assert out.result["targets"]["source"] == "operator" and out.result["targets"]["P_ms"] == 100


def test_r2_cal_a_first_run_is_a_single_run_and_a_repeat_on_the_same_disk_is_not():
    host = make_host(cache=INVERSION)
    assert "single-run" in calibrate(host).result["quality"]["limits"]
    assert "single-run" not in calibrate(host).result["quality"]["limits"]


def test_r2_cal_the_cache_proof_is_a_working_set_only_when_the_cache_state_is_proven():
    run = Calibrator(make_host(cache=INVERSION), config())
    run.execute()
    assert run.a["state"].status == "proven" and run.cache_proof() == "working-set"
    plain = Calibrator(make_host(cache=None), config())
    plain.execute()
    assert plain.a["state"].status == "unproven" and not plain.budget_limited and plain.cache_proof() == "none"
    quick = Calibrator(make_host(cache=INVERSION), config(profile="quick"))
    quick.execute()
    assert quick.budget_limited and quick.cache_proof() == "limited-by-budget"


def test_r2_cal_a_stage_series_error_inside_the_fill_or_the_ladder_fails_the_run_at_once():
    class FailingFill(Calibrator):
        def fill(self, alloc):
            raise SeriesError("fill series unusable")

    class FailingLadder(Calibrator):
        def run_ladder(self):
            raise SeriesError("ladder series unusable")

    out = FailingFill(make_host(cache=INVERSION), config()).execute()
    assert out.status == "failed" and out.message == "fill series unusable"
    run = FailingLadder(make_host(cache=INVERSION), config())
    out = run.execute()
    assert out.status == "failed" and out.message == "ladder series unusable" and "reference" not in run.stages


def test_r2_cal_a_file_target_that_cannot_discard_says_why_the_rearm_was_skipped():
    host = file_host()
    host.devices["/bench/target.img"].discard_supported = False
    run = Calibrator(host, config(target="/bench/target.img", work_dir="/bench/w"))
    assert run.execute().status == "complete"
    assert run.stages["rearm"] == "skipped: discard_unsupported"


def test_r2_cal_a_fill_that_leaves_exactly_the_free_space_margin_is_not_aborted():
    host = file_host()
    total, _ = host.statfs("/bench")
    host.at(host.now() + 10, lambda: host.set_fs("/bench", total, guard.file_margin(total)))
    out = calibrate(host, target="/bench/target.img", work_dir="/bench/w")
    assert out.status == "complete" and out.calibration["calibration"]["fill"]["fill_aborted"] is None


def _ladder_of(template, points):
    return [{**template, "ws_bytes": int(ws * GIB), "steady_iops": float(iops), "mean_iops": float(iops), "state": state, "class": "unclassified"} for ws, iops, state in points]


CLIFF_LADDER = [(1, 97850, "steady"), (16, 96000, "steady"), (100, 92000, "steady"), (150, 90000, "steady"), (200, 88000, "steady"), (250, 85900, "steady"),
                (300, 46315, "steady"), (350, 31000, "steady"), (400, 24000, "steady"), (450, 21000, "steady"), (500, 19500, "steady")]


def test_r2_cal_the_e1_fit_uses_the_steady_post_cliff_rungs_and_the_median_of_the_cached_tier(finished):
    import copy
    import statistics
    from iobench import cache
    run = copy.copy(finished[0])
    run.writes = [dict(r) for r in finished[0].writes]
    run.reads = _ladder_of(finished[0].reads[0], CLIFF_LADDER)
    reanalysed(run)
    cached = [iops for ws, iops, _ in CLIFF_LADDER[:6]]
    post = [(int(ws * GIB), float(iops)) for ws, iops, _ in CLIFF_LADDER[6:]]
    assert run.a["cliff"] == 300 * GIB
    assert run.a["e1"] == cache.e1_fit(post, statistics.median(cached)) and run.a["e1"].points == 5
    # an unsteady post-cliff rung is not a fit point, and a cached-tier rung that is unsteady does not matter to it
    unsteady = [(ws, iops, "unsteady" if ws == 400 else state) for ws, iops, state in CLIFF_LADDER]
    run.reads = _ladder_of(finished[0].reads[0], unsteady)
    reanalysed(run)
    assert run.a["e1"].points == 4 and run.a["e1"] == cache.e1_fit([p for p in post if p[0] != 400 * GIB], statistics.median(cached))
