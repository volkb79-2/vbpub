"""IOB-R2-01 (group B): oracles for ``iobench.shape`` (grid, drift, fit boundaries, the metering rule)."""
import dataclasses

import pytest

from iobench import shape
from iobench.testing import GIB, DirScenario, Scenario, standard_host

from helpers import job, run_job

SIZES4 = (4096, 16384, 65536, 262144)


def cell(direction="write", pattern="rand", bs=4096, *, iops=100.0, bps=1000.0, state="steady", **kw):
    return {"id": f"{direction}-{pattern}-{bs}", "dir": direction, "pattern": pattern, "bs": bs, "block_state": "rewrite",
            "steady_iops": iops, "steady_bps": bps, "repeat": False, "state": state, **kw}


def cells(rand_bps, rand_iops, seq_bps=None, seq_iops=None, direction="write"):
    seq_bps = rand_bps if seq_bps is None else seq_bps
    seq_iops = rand_iops if seq_iops is None else seq_iops
    return ([cell(direction, "rand", bs, iops=i, bps=b) for bs, b, i in zip(SIZES4, rand_bps, rand_iops)]
            + [cell(direction, "seq", bs, iops=i, bps=b) for bs, b, i in zip(SIZES4, seq_bps, seq_iops)])


# -- the grid ------------------------------------------------------------------------------------------------------
def test_a_cell_is_immutable_and_is_neither_an_anchor_nor_a_repeat_by_default():
    plain = shape.Cell("read", "seq", 4096, "rewrite")
    assert plain.anchor is False and plain.repeat is False and plain.seconds == shape.CELL_S
    with pytest.raises(dataclasses.FrozenInstanceError):
        plain.bs = 1


def test_the_drift_repeats_are_the_write_anchors_run_again_and_marked_as_such():
    repeats = [c for c in shape.grid_plan(seed=1) if c.repeat]
    assert sorted((c.dir, c.pattern, c.bs) for c in repeats) == [("write", "rand", 4096), ("write", "rand", 4096),
                                                                  ("write", "seq", 1048576), ("write", "seq", 1048576)]
    assert all(c.anchor and c.seconds == shape.ANCHOR_REPEAT_S and c.block_state == "rewrite" for c in repeats)


# -- the cell record -------------------------------------------------------------------------------------------------
def _record(runtime, noise=0.0):
    host = standard_host(Scenario(write=DirScenario(iops_max=71584, lat_us=440), noise_cv=noise))
    result = run_job(host, job("write", size=GIB, bs=4096, qd=32, rt=runtime, io=None, name="rec"))
    return shape.cell_record(result, shape.Cell("write", "rand", 4096, "rewrite"))


def test_a_long_perfectly_flat_cell_is_a_flat_line():
    record = _record(100)
    assert record["flat_line"] is True and record["state"] == "flat"


def test_a_cell_too_short_to_assess_is_not_a_flat_line():
    record = _record(30)
    assert record["flat_line"] is False


# -- anchor drift ---------------------------------------------------------------------------------------------------
def _anchor(v):
    return {"dir": "write", "pattern": "rand", "bs": 4096, "block_state": "rewrite", "steady_iops": v}


def test_a_single_anchor_measurement_has_no_spread_entry():
    assert shape.anchor_drift([_anchor(100.0)]) == {"spread": {}, "moved": False}


def test_a_spread_just_over_the_limit_moves_the_ceiling_and_one_under_does_not():
    assert shape.anchor_drift([_anchor(100.0), _anchor(115.1)])["moved"] is True
    assert shape.anchor_drift([_anchor(100.0), _anchor(114.9)])["moved"] is False


# -- the fit -----------------------------------------------------------------------------------------------------------
def fit(ys):
    return shape.fit_cells([{"bs": bs, "steady_iops": 1.0 / y} for bs, y in enumerate(ys, start=1)])


def test_a_fit_with_a_relative_residual_of_exactly_the_limit_is_good():
    result = fit((2, 4, 9))
    assert result["residual_max"] == shape.FIT_RESIDUAL and result["r2"] >= shape.FIT_R2
    assert result["fit"] == "good"


def test_a_fit_with_an_r2_of_exactly_the_limit_is_good():
    result = fit((1, 2, 2, 3))
    assert result["r2"] == shape.FIT_R2 and result["residual_max"] <= shape.FIT_RESIDUAL
    assert result["fit"] == "good"


def test_a_fit_needs_both_a_good_r2_and_a_small_residual():
    noisy_flat = fit((1.0, 1.1, 0.9, 1.1, 0.9))  # tiny residuals, but the line explains nothing
    assert noisy_flat["r2"] < shape.FIT_R2 and noisy_flat["residual_max"] <= shape.FIT_RESIDUAL
    assert noisy_flat["fit"] == "poor"
    steep = fit((1, 4, 9, 16, 25))  # a curve the line follows well in r2 but badly at the small end
    assert steep["r2"] >= shape.FIT_R2 and steep["residual_max"] > shape.FIT_RESIDUAL
    assert steep["fit"] == "poor"


# -- pattern agreement ---------------------------------------------------------------------------------------------
def test_the_patterns_agree_with_exactly_four_matching_sizes_and_not_with_three():
    rand = {1: 100.0, 2: 100.0, 3: 100.0, 4: 100.0}
    assert shape._pattern_ok(rand, {1: 100.0, 2: 110.0, 3: 90.0, 4: 100.0}) is True
    assert shape._pattern_ok(rand, {1: 100.0, 2: 110.0, 3: 90.0, 4: 150.0}) is False  # three of four within the tolerance
    assert shape._pattern_ok(dict(list(rand.items())[:3]), {1: 100.0, 2: 100.0, 3: 100.0}) is False  # three sizes


# -- metering ---------------------------------------------------------------------------------------------------------
def test_metering_is_not_forced_undetermined_unless_the_ceiling_moved():
    flat_bps = [20.0] * 4
    varying_iops = [3.0, 7.0, 3.0, 7.0]
    result = shape.metering_for(cells(flat_bps, varying_iops), "write", {})
    assert result["metering"] == "byte" and result["reasons"] == []
    moved = shape.metering_for(cells(flat_bps, varying_iops), "write", {}, moved=True)
    assert moved == {"metering": "undetermined", "reasons": ["ceiling_moved"], "tests": {}}


def test_metering_needs_four_sizes_in_each_pattern_and_reports_the_random_count():
    all_cells = cells([20.0] * 4, [3.0, 7.0, 3.0, 7.0])
    no_rand_4096 = [c for c in all_cells if not (c["pattern"] == "rand" and c["bs"] == 4096)]
    assert shape.metering_for(no_rand_4096, "write", {}) == {"metering": "undetermined", "reasons": [], "tests": {"sizes": 3}}
    no_seq_4096 = [c for c in all_cells if not (c["pattern"] == "seq" and c["bs"] == 4096)]
    assert shape.metering_for(no_seq_4096, "write", {}) == {"metering": "undetermined", "reasons": [], "tests": {"sizes": 4}}
    assert shape.metering_for(all_cells, "write", {})["metering"] == "byte"


def test_the_size_tests_read_the_random_cells_not_the_sequential_ones():
    rand_flat = [20.0] * 4
    seq_wavy = [24.0, 16.0, 24.0, 16.0]  # within the pattern tolerance of the random cells, but cv 0.2 > the flat limit
    result = shape.metering_for(cells(rand_flat, [3.0, 7.0, 3.0, 7.0], seq_bps=seq_wavy, seq_iops=[3.0, 7.0, 3.0, 7.0]), "write", {})
    assert result["tests"]["t_size_byte"] is True and result["metering"] == "byte"


def test_a_cv_exactly_on_the_flat_and_vary_limits_counts_as_flat_and_as_varying():
    flat_at_limit = [17.0, 23.0, 17.0, 23.0]  # cv exactly 0.15
    vary_at_limit = [3.0, 7.0, 3.0, 7.0]  # cv exactly 0.40
    by_bytes = shape.metering_for(cells(flat_at_limit, vary_at_limit), "write", {})
    assert by_bytes["tests"]["t_size_byte"] is True and by_bytes["tests"]["t_size_iops"] is False
    by_iops = shape.metering_for(cells(vary_at_limit, flat_at_limit), "write", {})
    assert by_iops["tests"]["t_size_iops"] is True and by_iops["tests"]["t_size_byte"] is False


def _mixed_inputs(c_io, c_byte):
    fits = {"write-rand-rewrite": {"fit": "good", "c_io_us": c_io, "c_byte_ns": c_byte}}
    return shape.metering_for(cells([1000.0] * 4, [100.0] * 4), "write", fits)


def test_a_mixed_meter_needs_both_cost_terms_strictly_positive():
    assert _mixed_inputs(5.0, 5.0)["metering"] == "mixed"
    assert _mixed_inputs(0.0, 5.0)["metering"] == "undetermined"
    assert _mixed_inputs(5.0, 0.0)["metering"] == "undetermined"
