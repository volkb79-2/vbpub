"""IOB-R2-01 survivors (package iob-1): result, ceilings, devio, stages, sweep, ladder, proposals, plan, extents.

Every test pins one boundary or default that the R2 run on r1002 (2026-11-02) showed unobserved.
"""
import copy
import dataclasses
import hashlib
import json

import pytest

from iobench import ceilings, devio, guard, ladder, plan, proposals, shape, sweep
from iobench import result as res
from iobench.errors import Refusal
from iobench.extents import Extents
from iobench.host import RunResult

from helpers import INVERSION, calibrate, config, make_host, read_json
from test_shp import make_cells

STATE = "/var/lib/iobench"
DAY = 86400


# -- extents ----------------------------------------------------------------------------------------------------
def test_extents_remove_touching_the_end_leaves_no_empty_range():
    ext = Extents([(0, 10)])
    ext.remove(5, 10)
    assert ext.as_list() == [[0, 5]]
    ext = Extents([(0, 10)])
    ext.remove(0, 10)
    assert ext.as_list() == []


# -- ceilings ---------------------------------------------------------------------------------------------------
SCOPE = {"model": "m", "serial": "s", "wwn": "w", "size_bytes": 5, "strength": "strong", "machine_id_hash": None}
IDENT = {"disk": {"devno": "8:1", "partuuid": "p", "size_bytes": 5}, "target": {"kind": "device"}, "disk_scope": SCOPE}


def test_ceilings_identity_digest_is_the_hash_of_the_pipe_joined_disk_scope():
    expected = "sha256:" + hashlib.sha256(b"disk-scope-1|m|s|w|5").hexdigest()
    assert ceilings.identity_digest(IDENT) == expected
    # a serial-less (weak) disk also binds to its host; absent fields are the empty string
    weak = {"model": None, "serial": None, "wwn": None, "size_bytes": 9, "strength": "weak", "machine_id_hash": "h"}
    assert ceilings.scope_key(weak) == "sha256:" + hashlib.sha256(b"disk-scope-1|||" + b"|9|h").hexdigest()
    assert ceilings.scope_key({**weak, "machine_id_hash": None}) == "sha256:" + hashlib.sha256(b"disk-scope-1|||" + b"|9|").hexdigest()
    # a strong disk ignores the host hash; the partition, the kind and the device name are never part of it
    assert ceilings.scope_key({**SCOPE, "machine_id_hash": "x", "name": "vdb"}) == ceilings.identity_digest(IDENT)
    assert ceilings.identity_digest({**IDENT, "disk": {"devno": "9:9"}, "target": {"kind": "file"}}) == ceilings.identity_digest(IDENT)


def test_ceilings_append_writes_sorted_keys_and_rotate_returns_none_for_a_missing_file():
    host = make_host()
    obs = {"ts": "2026-10-01T00:00:00Z", "dir": "write", "workload": "seq1m", "plateau_bps": 5e8, "duration_s": 60, "source": "benchmark", "kind": "raw"}
    ceilings.append(host, "/var/lib/iobench/c.jsonl", dict(reversed(list(obs.items()))), "sha256:x")
    assert host.read_text("/var/lib/iobench/c.jsonl").splitlines()[1] == json.dumps(obs, sort_keys=True)
    assert ceilings.rotate_foreign(host, "/var/lib/iobench/none.jsonl", "sha256:x", "t") is None


def obs_at(ts, bps=100.0, d="write", w="seq1m", **kw):
    return {"ts": ts, "dir": d, "workload": w, "plateau_bps": bps, "duration_s": 60, "source": "benchmark", "kind": "raw", **kw}


def test_ceilings_summary_window_includes_the_cutoff_instant():
    now = ceilings.parse_iso("2026-10-31T00:00:00Z")
    on_cutoff = obs_at(ceilings.iso(now - 30 * DAY))
    before = obs_at(ceilings.iso(now - 30 * DAY - 1), bps=1.0)
    out = ceilings.summary([on_cutoff, before], now)
    assert out["write"]["seq1m"]["n"] == 1 and out["write"]["seq1m"]["worst"] == 100.0


def test_ceilings_recalibrate_windows_exclude_their_lower_bound_and_include_the_upper():
    now = ceilings.parse_iso("2026-10-31T12:00:00Z")
    h = ceilings.WINDOW_H * 3600
    cal = {"write/seq1m": 100.0}
    far = 200.0  # a plateau 100 % above the calibration's
    # both windows hold a far observation exactly on their upper bound: recommended
    on_upper = [obs_at(ceilings.iso(now), far), obs_at(ceilings.iso(now - h), far)]
    assert ceilings.recalibrate_recommended(on_upper, cal, now) is True
    # an observation on the lower bound of the older window belongs to neither window
    on_lower = [obs_at(ceilings.iso(now), far), obs_at(ceilings.iso(now - 2 * h), far)]
    assert ceilings.recalibrate_recommended(on_lower, cal, now) is False


def test_ceilings_a_shift_of_exactly_the_threshold_is_not_a_shift():
    now = ceilings.parse_iso("2026-10-31T12:00:00Z")
    h = ceilings.WINDOW_H * 3600
    rows = [obs_at(ceilings.iso(now - 10), 125.0), obs_at(ceilings.iso(now - h - 10), 125.0)]
    assert ceilings.recalibrate_recommended(rows, {"write/seq1m": 100.0}, now) is False
    rows = [obs_at(ceilings.iso(now - 10), 126.0), obs_at(ceilings.iso(now - h - 10), 126.0)]
    assert ceilings.recalibrate_recommended(rows, {"write/seq1m": 100.0}, now) is True


def test_ceilings_run_observations_keep_a_window_of_exactly_the_minimum_duration():
    plateaus = [{"dir": "write", "workload": "seq1m", "plateau_bps": 1.0, "state": "steady", "observation_s": ceilings.MIN_DURATION_S},
                {"dir": "write", "workload": "rand4k", "plateau_bps": 1.0, "state": "steady", "observation_s": ceilings.MIN_DURATION_S - 1}]
    assert [o["workload"] for o in ceilings.run_observations(plateaus, "t", "raw")] == ["seq1m"]


# -- ladder -----------------------------------------------------------------------------------------------------
def rung(ws, iops):
    return {"ws_bytes": ws, "steady_iops": iops, "state": "steady"}


def test_ladder_the_cliff_needs_every_later_rung_below_the_threshold_and_a_rung_on_it_is_not_below():
    # threshold = 0.75 * median(first three) = 75
    base = [rung(1, 100.0), rung(2, 100.0), rung(3, 100.0)]
    cliff, ref, out = ladder.classify_rungs([*base, rung(4, 74.0), rung(5, 74.0)])
    assert (cliff, ref) == (4, 100.0)
    assert [r["class"] for r in out] == ["cached_tier"] * 3 + ["post_cliff"] * 2
    # a rung exactly on the threshold is not below it: it is no cliff, and it ends the run of rungs below
    assert ladder.classify_rungs([*base, rung(4, 75.0), rung(5, 74.0)])[0] == 5
    assert ladder.classify_rungs([*base, rung(4, 74.0), rung(5, 75.0)])[0] is None


def test_ladder_qd90_takes_a_depth_exactly_at_ninety_percent():
    assert ladder.qd90({1: 90.0, 8: 100.0, 64: 100.0}) == 1
    assert ladder.qd90({1: 89.9, 8: 100.0}) == 8


def test_ladder_fresh_extent_needs_positive_room_and_aligns_the_rest():
    ws_max = 4 * ladder.GIB
    start = ladder.align_up(ws_max)
    assert ladder.fresh_extent(start + ladder.TAIL_BYTES, ws_max) is None
    got = ladder.fresh_extent(start + ladder.TAIL_BYTES + ladder.MIB, ws_max)
    assert got == (start, start + ladder.MIB)


def test_ladder_rearm_needs_the_ratio_itself():
    assert ladder.rearm_verdict(3.0, 1.0, False)["verdict"] == "rearmed"
    assert ladder.rearm_verdict(2.9, 1.0, False)["verdict"] == "not_rearmed"
    assert ladder.rearm_verdict(9.0, 1.0, True)["verdict"] == "ceiling_moved"


def test_ladder_basis_is_immutable():
    basis = ladder.Basis(1.0, "proven")
    with pytest.raises(dataclasses.FrozenInstanceError):
        basis.value = 2.0


# -- plan -------------------------------------------------------------------------------------------------------
def test_plan_quick_rungs_include_a_size_only_when_below_the_working_set():
    g = plan.GIB
    assert plan.rung_plan(16 * g, quick=True) == [g, 16 * g]
    assert plan.rung_plan(16 * g + 1, quick=True) == [g, 16 * g, 16 * g + 1]
    assert plan.rung_plan(g, quick=True) == [g]


def test_plan_stage_and_allotment_are_immutable_and_the_knob_seconds_default_is_typical():
    stage = plan.Stage("S0", 30)
    with pytest.raises(dataclasses.FrozenInstanceError):
        stage.name = "x"
    allot = plan.Allotment("standard", 1, (stage,), 2, (3,))
    with pytest.raises(dataclasses.FrozenInstanceError):
        allot.budget_s = 5
    assert plan.knob_seconds(1) == plan.KNOB_CELL_S + plan.KNOB_GAP_TYPICAL_S
    assert plan.knob_seconds(1, worst=True) == plan.KNOB_CELL_S + plan.KNOB_GAP_WORST_S


# -- sweep ------------------------------------------------------------------------------------------------------
def test_sweep_drain_rate_needs_two_rates_and_takes_the_median_of_the_tail():
    with pytest.raises(sweep.SeriesError):
        sweep.drain_rate([0.0, 1.0])
    assert sweep.drain_rate([0.0, 1.0, 3.0]) in (1.5, 2.0)  # two rates are enough; the tail slice of a short series is not pinned here
    assert sweep.drain_rate([0.0, 1.0, 2.0, 10.0, 18.0, 26.0]) == 8.0


def test_sweep_rates_consistent_at_exactly_the_factor_is_consistent_and_names_its_direction():
    assert sweep.rates_consistent(100.0, 130.0)["consistent"] is True
    assert sweep.rates_consistent(130.0, 100.0)["consistent"] is True
    below = sweep.rates_consistent(99.0, 130.0)
    above = sweep.rates_consistent(131.0, 100.0)
    assert below == {"consistent": False, "verdict": "drain_below_ladder", "reason": "drain_below_ladder"}
    assert above == {"consistent": False, "verdict": "drain_above_ladder", "reason": "cache_residual_possible"}


def test_sweep_quiet_gap_needs_a_dirty_level_strictly_below_the_limit():
    quiet = [float(sweep.QUIET_DIRTY_BYTES - 1)] * sweep.QUIET_FOR_S
    assert sweep.quiet_gap_ended(quiet, 0) is True
    assert sweep.quiet_gap_ended([float(sweep.QUIET_DIRTY_BYTES)] * sweep.QUIET_FOR_S, 0) is False
    assert sweep.quiet_gap_ended(quiet[:-1], 0) is False
    assert sweep.quiet_gap_ended([], sweep.QUIET_MAX_S) is True
    assert sweep.quiet_gap_ended([], sweep.QUIET_MAX_S - 1) is False


def sweep_cell(name, p99, varies="nr_requests"):
    return {"name": name, "varies": varies, "probe_p99_us": p99, "load_iops": 100.0, "load_fsync_p99_us": 10.0}


def test_sweep_choose_keeps_the_first_of_two_equally_good_neighbours_and_judges_noise_strictly():
    ref = sweep_cell("REF", 1000.0, "")
    first, second = sweep_cell("a", 500.0), sweep_cell("b", 500.0)
    repeats = {"a": sweep_cell("a", 500.0), "b": sweep_cell("b", 500.0)}
    out = sweep.choose([ref, first, second, ref], repeats)
    assert out["winners"]["nr_requests"]["name"] == "a" and out["noise_high"] is False
    assert sweep.choose([ref, first, sweep_cell("REF", 4000.0, ""), ], {})["noise_high"] is False  # noise 4.0 is the limit itself
    assert sweep.choose([ref, first, sweep_cell("REF", 4001.0, "")], {})["noise_high"] is True


def test_sweep_sweep_cell_is_immutable():
    cell = sweep.SweepCell("REF", {}, "")
    with pytest.raises(dataclasses.FrozenInstanceError):
        cell.name = "x"


# -- devio ------------------------------------------------------------------------------------------------------
def test_devio_records_are_immutable_and_a_job_is_direct_and_shared_by_default():
    job = devio.FioJob("n", "/f", "read", 4096, 1, 1)
    for obj, attr in ((job, "name"),
                      (devio.FioStats("read", 1.0, 1.0, 1, 1.0, 1.0, {}), "iops"),
                      (devio.FioResult(job, 0, None, [], None, 0.0), "returncode")):
        with pytest.raises(dataclasses.FrozenInstanceError):
            setattr(obj, attr, 1)
    assert job.direct is True and job.device_guard is False


class FakeProbeHost:
    def __init__(self, result=None, error=None):
        self.result, self.error = result, error

    def run(self, argv):
        if self.error:
            raise self.error
        return self.result


def test_devio_require_fio_option_matches_the_exact_help_name_and_colon():
    # a longer option name is not a prefix match
    mixed = RunResult(0, "allow_mounted_write_extra: x\n", "")
    with pytest.raises(Refusal):
        devio.require_fio_option(FakeProbeHost(mixed), "allow_mounted_write", (3, 39))
    # a clean answer that never names the option is not documented either
    with pytest.raises(Refusal):
        devio.require_fio_option(FakeProbeHost(RunResult(0, "other_option: x\n", "")), "allow_mounted_write", (3, 39))
    devio.require_fio_option(FakeProbeHost(RunResult(0, "   allow_mounted_write: x\n", "")), "allow_mounted_write", (3, 39))


def test_devio_probe_messages_name_the_oserror_text_and_the_stderr():
    err = OSError(2, "No such file")
    with pytest.raises(Refusal) as e1:
        devio.probe(FakeProbeHost(error=err), "blkid", ["blkid"])
    assert "could not run (No such file)" in str(e1.value)
    with pytest.raises(Refusal) as e2:
        devio.probe(FakeProbeHost(error=OSError("plain text")), "blkid", ["blkid"])
    assert "could not run (plain text)" in str(e2.value)
    with pytest.raises(Refusal) as e3:
        devio.probe(FakeProbeHost(RunResult(1, "", "  boom \n")), "blkid", ["blkid"])
    assert "boom" in str(e3.value) and "no message" not in str(e3.value)
    with pytest.raises(Refusal) as e4:
        devio.probe(FakeProbeHost(RunResult(1, "", "")), "blkid", ["blkid"])
    assert "no message" in str(e4.value)


# -- stages: Config defaults ------------------------------------------------------------------------------------
def test_stages_config_flags_default_off():
    cfg = config()
    base = dataclasses.replace(cfg)
    from iobench.stages import Config
    plain = Config(target="/dev/x", tool_version="0")
    assert (plain.confirm_quiet_window, plain.installer, plain.full_sweep, plain.repeat, plain.require_strong_identity) == (False,) * 5
    assert base.target == cfg.target


# -- proposals --------------------------------------------------------------------------------------------------
def test_proposals_value_names_what_is_outside_the_vocabulary():
    with pytest.raises(ValueError, match="bogus_reason"):
        proposals.value(1, "R5", {}, "measured", reasons=["bogus_reason"])
    with pytest.raises(ValueError, match="no_such_basis"):
        proposals.value(1, "R5", {}, "no_such_basis")
    with pytest.raises(ValueError, match="bogus_reason"):
        proposals.value(1, "R5", {}, "no_such_basis", reasons=["bogus_reason"])


def test_proposals_sections_trust_the_identity_unless_told_otherwise():
    group = {"a": proposals.value(1, "R5", {}, "measured")}
    assert proposals.section_summary(group)["proven"] == 1
    rep = proposals.section_report("caps", {"dev_aggregate": {}})
    assert rep["unproven"] == []


def test_proposals_the_fresh_penalty_note_starts_at_three():
    cal = {"cache": {"read": {}}, "fresh_vs_rewrite": {"measured": True, "penalty": 3, "label": "fresh"}}
    assert [n["note"] for n in proposals.build_notes(cal)] == ["read_cache_headroom", "fresh_write_penalty"]
    cal["fresh_vs_rewrite"]["penalty"] = 2.99
    assert [n["note"] for n in proposals.build_notes(cal)] == ["read_cache_headroom"]


@pytest.fixture(scope="module")
def base_cal():
    host = make_host(cache=INVERSION)
    calibrate(host)
    return read_json(host, f"{STATE}/calibration.json")["calibration"]


def model_for(base_cal, cells, edit=None):
    c = copy.deepcopy(base_cal)
    c["shape"] = shape.shape_summary(cells)
    if edit:
        edit(c)
    return proposals.build_model(c, {"history": "single-run"})


UNDETERMINED = staticmethod(lambda d, p, bs: 1 / ((80e-6 if p == "rand" else 20e-6) + bs * 0.25e-9))


def und_cells():
    return make_cells(lambda d, p, bs: 1 / ((80e-6 if p == "rand" else 20e-6) + bs * 0.25e-9))


@pytest.mark.parametrize("status,r_iops,flagged", [("proven", 5000.0, False), ("unproven", 5000.0, True), ("proven", None, True), ("proven", 0, True)])
def test_proposals_read_model_inherits_the_cache_state_only_from_a_proven_positive_rate(base_cal, status, r_iops, flagged):
    def edit(c):
        c["cache"]["read"]["status"], c["cache"]["read"]["R_iops"] = status, r_iops
    out = model_for(base_cal, und_cells(), edit)
    assert ("cache_residual_possible" in out["rbps"].get("reasons", ())) is flagged


def test_proposals_a_zero_fitted_cost_is_no_cost_to_derive_from(base_cal):
    def edit(c):
        c["shape"]["fit"]["read-seq-rewrite"]["c_io_us"] = 0.0
    out = model_for(base_cal, und_cells(), edit)
    assert all(v["status"] == "absent" and v["reason"] == "stage_failed" for k, v in out.items() if k[0] == "r")


# -- result -----------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("text", ['["schema", "schema_minor"]', '{"schema": 1}', '{"schema_minor": 0}'])
def test_result_load_rejects_a_non_object_or_a_document_missing_a_version_key(text):
    host = make_host()
    host.write_atomic("/x.json", text)
    with pytest.raises(res.LoadError) as err:
        res.load(host, "/x.json", ())
    assert err.value.reason == "corrupt"


def test_result_identity_strength_follows_serial_or_wwn():
    host = make_host(cache=INVERSION)
    base = "/sys/devices/pci0/block/vda/"
    for serial, wwn, strength in (("S", None, "strong"), (None, "W", "strong"), ("S", "W", "strong"), (None, None, "weak")):
        for path, value in (("/sys/block/vda/serial", serial), (base + "wwid", wwn)):
            if value is None:
                host.files.pop(host._resolve(path), None)
            else:
                host.put(path, value + "\n")
        ident = res.s0_identity(host, guard.load_target(host, "/dev/vda7"), None)
        assert ident["identity_strength"] == strength == ident["disk_scope"]["strength"]


def identity(workdir_uuid=None, **disk):
    return {"target": {"kind": "device", "path": "/dev/x"}, "disk": {"scheduler": "none", **disk}, "ram_bytes": 1, "kernel": "7.2", "root_source": "/dev/x",
            "workdir_fs": None if workdir_uuid is None else {"uuid": workdir_uuid}}


def test_result_advisory_warnings_compare_workdir_filesystems_with_either_side_absent():
    assert res.advisory_warnings(identity(), identity(), None, 0) == []
    assert res.advisory_warnings(identity(), identity("u"), None, 0) == ["workdir_fs_changed"]
    # a work dir that is gone after the run has nothing to compare
    assert res.advisory_warnings(identity("u"), identity(), None, 0) == []
    assert res.advisory_warnings(identity("u"), identity("u"), None, 0) == []
    assert res.advisory_warnings(identity("u"), identity("v"), None, 0) == ["workdir_fs_changed"]


def test_result_advisory_warnings_age_limit_is_strict():
    limit = res.AGE_WARN_DAYS * DAY
    assert res.advisory_warnings(identity(), identity(), 0.0, limit) == []
    assert res.advisory_warnings(identity(), identity(), 0.0, limit + 1) == ["older_than_180_days"]


@pytest.fixture
def stored_host():
    host = make_host(cache=INVERSION)
    calibrate(host)
    return host


def now_of(host):
    return host.now()


def test_result_check_of_a_result_without_a_calibration_id_does_not_look_for_one(stored_host):
    host = stored_host
    p = res.paths(STATE)
    doc = read_json(host, p["result"])
    doc["run"]["calibration_id"] = None
    host.write_atomic(p["result"], json.dumps(doc))
    chk = res.check(host, STATE, now=now_of(host))
    assert chk.state in ("current", "current_unproven") and chk.reasons == []


def test_result_check_of_an_explicit_calibration_copy_reads_that_copy_only(stored_host):
    host = stored_host
    p = res.paths(STATE)
    doc = read_json(host, p["calibration"])
    doc["calibration"]["id"] = "another-calibration-id"
    host.write_atomic("/copy.json", json.dumps(doc))
    chk = res.check(host, STATE, calibration=True, now=now_of(host), result_path="/copy.json")
    assert chk.reasons == [] and chk.calibration_id == "another-calibration-id"


def test_result_a_failed_record_of_the_same_start_is_not_newer(stored_host):
    host = stored_host
    p = res.paths(STATE)
    doc = read_json(host, p["calibration"])
    host.write_atomic(p["calibration_failed"], json.dumps(doc))
    assert "last_run_aborted" not in res.check(host, STATE, calibration=True, now=now_of(host)).warnings
    doc["run"]["started"] = doc["run"]["started"].replace("T02", "T03")
    host.write_atomic(p["calibration_failed"], json.dumps(doc))
    assert "last_run_aborted" in res.check(host, STATE, calibration=True, now=now_of(host)).warnings


# -- proposals: section_report ---------------------------------------------------------------------------------
def test_proposals_section_report_trusts_the_identity_by_default():
    group = {"dev_aggregate": {"rbps": proposals.value(1, "R5", {}, "measured")}}
    assert proposals.section_report("caps", group)["unproven"] == []
    assert proposals.section_report("caps", group, identity_weak=True)["unproven"] != []


# -- stages -----------------------------------------------------------------------------------------------------
def test_stages_the_fio_floor_itself_is_accepted():
    from iobench.calibrate import Calibrator
    host = make_host(cache=INVERSION)
    host.probe_failures["fio"] = RunResult(0, "fio-3.30\n", "")
    with pytest.raises(Refusal) as err:
        Calibrator(host, config()).execute()
    assert err.value.condition == "fio_option_unsupported"  # past the version check; the stub answers every fio probe with the version line


@pytest.mark.parametrize("kind,filename,device_guard", [("device", None, True), ("device", "/dev/vda7", True), ("device", "/bench/w/load", False),
                                                        ("file", None, False), ("file", "/bench/target.img", False)])
def test_stages_only_jobs_on_the_device_target_carry_the_guard(kind, filename, device_guard):
    from iobench.calibrate import Calibrator
    host = make_host(cache=INVERSION)
    run = Calibrator(host, config())
    run.target = dataclasses.replace(guard.load_target(host, "/dev/vda7"), kind=kind, path="/bench/target.img" if kind == "file" else "/dev/vda7")
    if filename == "/dev/vda7":
        filename = run.target.path
    assert run.job("n", "read", 4096, 1, 1, 0, 4096, filename=filename).device_guard is device_guard


def test_stages_a_device_target_never_asks_the_filesystem_for_its_disk(monkeypatch):
    from iobench.calibrate import Calibrator
    host = make_host(cache=INVERSION)
    monkeypatch.setattr(Calibrator, "file_target_disk", lambda self, target: pytest.fail("a device target has its own disk"))
    run = Calibrator(host, config())
    assert run.execute().status == "complete" and run.disk_name == "vda"


def test_stages_a_file_target_takes_the_disk_behind_its_filesystem():
    from test_tgt import file_host
    from iobench.calibrate import Calibrator
    host = file_host()
    run = Calibrator(host, config(target="/bench/target.img", work_dir="/bench/w"))
    assert run.execute().status == "complete" and run.disk_name
