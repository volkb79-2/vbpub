"""IOB-R2-01 (group B): oracles for ``iobench.matrix`` (drain basis, the REF-cell rule, read-cost jobs, knob comparison, targets source)."""
import json

import pytest

from iobench import matrix as matrix_mod
from iobench.errors import RunFailed
from iobench import proposals, testing

from helpers import INVERSION, calibrate, config, fio_jobs, make_host, matrix, read_json

STATE = "/var/lib/iobench"
CAL = f"{STATE}/calibration.json"


def sweep_model(p99=lambda k: 200_000.0, drain=10_500_000.0, iops=800.0):
    return lambda k: testing.SweepOutcome(probe_p99_us=p99(k), load_iops=iops, load_fsync_p99_us=90_000.0, drain_bps=drain)


def calibrated(**kw):
    host = make_host(cache=INVERSION, **kw)
    assert calibrate(host).status == "complete"
    host.sweep_model = sweep_model()
    return host


def edit_calibration(host, edit):
    doc = read_json(host, CAL)
    edit(doc["calibration"])
    host.put(CAL, json.dumps(doc))


# -- S3: the ladder basis of the drain check ----------------------------------------------------------------------
def test_the_drain_is_judged_against_the_proven_write_byte_rate_of_the_calibration():
    host = calibrated()
    ladder = read_json(host, CAL)["calibration"]["cap_basis"]["bps"]["write"]["value"]
    assert matrix(host).result["evidence"]["drain"]["ladder_write_bps"] == ladder


def test_without_a_byte_rate_the_drain_is_judged_against_the_write_iops_times_4_kib():
    host = calibrated()

    def edit(cal):
        cal["cap_basis"]["bps"]["write"]["value"] = None
        cal["cap_basis"]["write"]["value"] = 1000.0

    edit_calibration(host, edit)
    assert matrix(host).result["evidence"]["drain"]["ladder_write_bps"] == 4_096_000.0


def test_without_any_write_basis_the_drain_has_no_consistency_verdict():
    host = calibrated()

    def edit(cal):
        cal["cap_basis"]["bps"]["write"]["value"] = None
        cal["cap_basis"]["write"]["value"] = None

    edit_calibration(host, edit)
    drain = matrix(host).result["evidence"]["drain"]
    assert drain["ladder_write_bps"] == 0 and drain["consistency"] is None


# -- plan_base: the sector size when the formula candidate reaches the hardware limit ------------------------------
def test_a_formula_candidate_equal_to_the_hardware_limit_keeps_the_current_request_size():
    host = calibrated()
    host.put("/sys/class/block/vda/queue/max_hw_sectors_kb", "1024\n")
    host.put("/sys/class/block/vda/queue/max_sectors_kb", "512\n")
    host.sweep_model = sweep_model(drain=1.05e8)  # 1.05e8 B/s x 10 ms / 1 KiB = 1025 -> the power of two 1024 = max_hw
    out = matrix(host)
    assert out.result["evidence"]["sweep"]["base"]["max_sectors_kb"] == 512


# -- S4: the REF cells ------------------------------------------------------------------------------------------------------
def test_a_sweep_that_ran_out_of_budget_before_its_closing_ref_cell_is_a_failed_stage():
    host = calibrated()
    m = matrix_mod.Matrixer(host, config(budget_minutes=10))
    out = m.execute()
    measured = [c["name"] for c in m.sweep_cells if "skipped" not in c]
    assert 3 <= len(measured) < 6 and measured[0] == "REF" and "REF2" not in measured
    assert out.result["run"]["stages"]["S4"] == "failed: unusable_series"
    assert "formula_only" in out.result["proposals"]["writeback"]["dirty_bytes"]["reasons"]


def test_a_complete_sweep_records_its_r4_check():
    host = calibrated()
    out = matrix(host)
    assert out.result["run"]["stages"]["S4"] == "ok" and "r4" in out.result["evidence"]["sweep"]


@pytest.mark.parametrize(("stage", "method"), (("S3", "s3"), ("S4", "s4"), ("S5", "s5")))
def test_a_failed_matrix_stage_persists_the_partial_evidence(monkeypatch, stage, method):
    host = calibrated()
    m = matrix_mod.Matrixer(host, config())

    def fail():
        m.evidence["failure_probe"] = {"stage": stage}
        raise RunFailed(f"controlled {stage} failure")

    monkeypatch.setattr(m, method, fail)
    out = m.execute()
    failed = read_json(host, f"{STATE}/result.last-failed.json")
    partial = failed["partial"]
    assert out.status == "failed" and partial["status"] == "incomplete"
    assert partial["stages"][stage] == f"failed: controlled {stage} failure"
    assert partial["evidence"]["failure_probe"] == {"stage": stage}


# -- S5: read cost ------------------------------------------------------------------------------------------------------------
def test_the_buffered_read_cost_comparison_reads_through_the_page_cache_and_the_direct_one_does_not():
    host = calibrated()
    matrix(host)
    buffered = [j for j in fio_jobs(host) if "rdc-buf-" in j.name]
    direct = [j for j in fio_jobs(host) if "-rdc-" in j.name and "rdc-buf-" not in j.name]
    assert len(buffered) == 2 and not any(j.direct for j in buffered)
    assert direct and all(j.direct for j in direct)


# -- the read floor of a sweep winner ------------------------------------------------------------------------------------------
def winner_model(k):
    good = k["max_sectors_kb"] == 128 or k["dirty_bytes"] == 8 * 1024 * 1024
    return testing.SweepOutcome(probe_p99_us=4e4 if good else 2e5, load_iops=800.0, load_fsync_p99_us=9e4, drain_bps=10.5e6)


def test_a_sweep_winner_whose_read_ratio_equals_the_floor_exactly_does_not_violate_it():
    host = calibrated()
    # drain 2e7 B/s: the formula candidate is 128 KiB; the sweep prefers 64 KiB, which S5 never measured (its ratio defaults to 1.0)
    host.sweep_model = lambda k: testing.SweepOutcome(probe_p99_us=4e4 if k["max_sectors_kb"] == 64 else 2e5, load_iops=800.0,
                                                      load_fsync_p99_us=9e4, drain_bps=2e7)
    wb = matrix(host, targets={**proposals.DEFAULT_TARGETS, "F_read_ratio": 1.0}).result["proposals"]["writeback"]
    assert wb["max_sectors_kb"]["value"] == 64 and "sweep_winner" in wb["max_sectors_kb"]["flags"]
    assert "read_floor_violated" not in wb["max_sectors_kb"]["flags"]
    below = calibrated()
    below.sweep_model = host.sweep_model
    flags = matrix(below, targets={**proposals.DEFAULT_TARGETS, "F_read_ratio": 1.01}).result["proposals"]["writeback"]["max_sectors_kb"]["flags"]
    assert "read_floor_violated" in flags


# -- targets ----------------------------------------------------------------------------------------------------------------------
def test_the_targets_say_whether_they_are_the_defaults_or_the_operators():
    default = matrix(calibrated()).result["targets"]
    assert default["source"] == "default"
    custom = matrix(calibrated(), targets={**proposals.DEFAULT_TARGETS, "W_s": 3.0}).result["targets"]
    assert custom["source"] == "operator" and custom["W_s"] == 3.0


# -- knob comparison -----------------------------------------------------------------------------------------------------------------
def test_a_knob_comparison_runs_every_cell_at_the_effective_dirty_bytes_of_the_host():
    host = calibrated()
    out = matrix(host, knob="nr_requests", candidates=(32, 64))
    cells = out.result["evidence"]["sweep"]["cells"]
    assert cells and all(c["knobs"]["dirty_bytes"] > 0 for c in cells)  # a zero setting means "the kernel ratio": the cell names its effective value
    explicit = calibrated()
    explicit.put("/proc/sys/vm/dirty_bytes", "50000000\n")
    cells = matrix(explicit, knob="nr_requests", candidates=(32, 64)).result["evidence"]["sweep"]["cells"]
    assert {c["knobs"]["dirty_bytes"] for c in cells} == {50_000_000}


def test_a_knob_comparison_whose_budget_is_already_spent_still_runs_its_stage_and_skips_every_cell():
    host = calibrated()
    out = matrix(host, knob="nr_requests", candidates=(32, 64), budget_minutes=-1)  # the remaining budget is negative before the stage starts
    assert out.result["run"]["stages"]["S4"] == "ok"
    assert {c.get("skipped") for c in out.result["evidence"]["sweep"]["cells"]} == {"budget"}
    assert "limited-by-budget" in out.result["quality"]["limits"]
