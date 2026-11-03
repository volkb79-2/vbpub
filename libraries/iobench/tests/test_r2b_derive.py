"""IOB-R2-01 (group B): exact-boundary oracles for the derivation rules of ``iobench.derive``."""
from iobench import derive
from iobench.derive import DIRTY_FLOOR, MIB


def test_pow2_floor_of_exactly_one_is_one_and_of_less_than_one_is_zero():
    assert derive.pow2_floor(1) == 1 and derive.pow2_floor(1.0) == 1
    assert derive.pow2_floor(0.99) == 0
    assert derive.pow2_floor(2) == 2 and derive.pow2_floor(3.9) == 2


def test_r1_an_inflight_volume_equal_to_the_whole_budget_leaves_nothing_and_takes_the_floor():
    assert derive.r1(W_s=2.0, R_drain_bps=10 * MIB, inflight_bytes=20 * MIB, T_eff_bytes=1e12) == (DIRTY_FLOOR, ["inflight_exceeds_budget"])
    value, flags = derive.r1(W_s=2.0, R_drain_bps=10 * MIB, inflight_bytes=19 * MIB, T_eff_bytes=1e12)
    assert flags == [] and value == DIRTY_FLOOR  # 1 MiB of budget clamps up, without the flag


def test_r1_a_budget_exactly_at_the_kernel_threshold_defers_to_the_kernel():
    assert derive.r1(W_s=2.0, R_drain_bps=10 * MIB, inflight_bytes=0.0, T_eff_bytes=20 * MIB) == ("kernel", [])
    value, _ = derive.r1(W_s=2.0, R_drain_bps=10 * MIB, inflight_bytes=0.0, T_eff_bytes=20 * MIB + 1)
    assert value == 5 * MIB  # just below the threshold: the 25 % ceiling of T_eff caps the 16 MiB power of two


def test_r3_a_candidate_whose_read_ratio_equals_the_floor_is_kept_without_flags():
    assert derive.r3(R_drain_bps=1.4e7, max_hw_kb=1280, F=0.25, P_us=250_000.0, read_ratio={"128": 0.25}, probe_p99_us={}) == (128, [])


def test_r3_a_larger_size_whose_read_ratio_equals_the_floor_meets_it():
    ratios = {"128": 0.1, "256": 0.25, "1280": 0.25}
    value, flags = derive.r3(R_drain_bps=1.4e7, max_hw_kb=1280, F=0.25, P_us=250_000.0, read_ratio=ratios, probe_p99_us={})
    assert (value, flags) == (256, [])


def test_r3_a_raised_size_whose_measured_p99_equals_the_target_is_not_a_violation():
    ratios = {"128": 0.1, "256": 0.9}
    at_target = derive.r3(R_drain_bps=1.4e7, max_hw_kb=1280, F=0.25, P_us=250_000.0, read_ratio=ratios, probe_p99_us={"256": 250_000.0})
    assert at_target == (256, [])
    over = derive.r3(R_drain_bps=1.4e7, max_hw_kb=1280, F=0.25, P_us=250_000.0, read_ratio=ratios, probe_p99_us={"256": 250_000.5})
    assert over == (128, ["read_floor_violated"])


def test_r4_a_closest_cell_exactly_at_the_target_latency_meets_it():
    cells = [{"name": "REF", "knobs": {"dirty_bytes": 8, "nr_requests": 64, "max_sectors_kb": 128}, "probe_p99_us": 250_000.0}]
    chosen = {"dirty_bytes": 8, "nr_requests": 64, "max_sectors_kb": 128}
    assert derive.r4(chosen, cells, 250_000.0)["ok"] is True
    assert derive.r4(chosen, cells, 249_999.0)["ok"] is False


def test_the_latency_knee_is_the_first_load_point_at_exactly_twice_the_light_load_p99():
    def point(load, p99):
        return {"dir": "read", "load_pct": load, "lat_us": {"p99": p99}}

    curve = [point(10, 100.0), point(30, 150.0), point(50, 200.0), point(70, 900.0)]
    assert derive.latency_knee(curve, "read") == 50
