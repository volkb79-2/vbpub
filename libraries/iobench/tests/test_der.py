"""IOB-DER: derivation rules R1..R6 (pure)."""
import pytest

from iobench import derive

MIB = 1 << 20
GIB = 1 << 30
T_EFF = 1717986918  # 20 % of 8 GiB


def test_der_01_r1():
    # gstammtisch replay: R_drain 10.5 MB/s, q16 x r64 -> 16 MiB / 4 MiB
    inflight = 16 * 64 * 1024
    value, flags = derive.r1(W_s=2.0, R_drain_bps=10_500_000, inflight_bytes=inflight, T_eff_bytes=T_EFF)
    assert value == 16 * MIB and flags == []
    assert derive.r1b(dirty_bytes=value) == 4 * MIB
    # r1002 replay: the drain is sequential ~1.3 GB/s, so the budget exceeds T_eff -> kernel
    value, _ = derive.r1(W_s=2.0, R_drain_bps=1_300_000_000, inflight_bytes=64 * 1280 * 1024, T_eff_bytes=T_EFF)
    assert value == "kernel" and derive.r1b(dirty_bytes=value) == "kernel"
    # the ratchet case: a vm.dirty_bytes of 16 MiB applied earlier is NOT an input; T_eff comes from the ratio
    assert derive.t_eff(20, 8 * GIB) == T_EFF
    again, _ = derive.r1(W_s=2.0, R_drain_bps=10_500_000, inflight_bytes=16 * 64 * 1024, T_eff_bytes=derive.t_eff(20, 8 * GIB))
    assert again == 16 * MIB
    # inflight_exceeds_budget: the 4 MiB floor with a flag
    floor, flags = derive.r1(W_s=2.0, R_drain_bps=500_000, inflight_bytes=4 * MIB, T_eff_bytes=T_EFF)
    assert floor == 4 * MIB and flags == ["inflight_exceeds_budget"]
    # clamps: a budget below 4 MiB is raised to the floor, a huge budget below T_eff is capped at 25 % of T_eff
    assert derive.r1(W_s=2.0, R_drain_bps=3_000_000, inflight_bytes=0, T_eff_bytes=T_EFF)[0] == 4 * MIB
    assert derive.r1(W_s=2.0, R_drain_bps=800_000_000, inflight_bytes=0, T_eff_bytes=T_EFF)[0] == int(0.25 * T_EFF)
    assert derive.pow2_floor(0.5) == 0 and derive.pow2_ceil(0) == 1 and derive.pow2_ceil(9) == 16


def test_der_02_r2():
    assert derive.r2(qd90_write=8, current_nr_requests=256) == 16
    assert derive.r2(qd90_write=1, current_nr_requests=256) == 8  # clamped up
    assert derive.r2(qd90_write=32, current_nr_requests=256) == 64
    assert derive.r2(qd90_write=64, current_nr_requests=256) == 64  # clamped down
    assert derive.r2(qd90_write=8, current_nr_requests=16) == "kernel"  # not below the current value
    assert derive.r2(qd90_write=8, current_nr_requests=8) == "kernel"


GST_RATIO = {"64": 0.21, "128": 0.35, "256": 0.55, "4096": 1.0}


def test_der_03_r3_r4():
    # gstammtisch: 64 with read_floor_violated at read_ratio 0.21 (128 breaks P at q16)
    kw = dict(R_drain_bps=10_500_000, max_hw_kb=4096, F=0.25, P_us=250_000.0, read_ratio=GST_RATIO)
    value, flags = derive.r3(probe_p99_us={"128": 466_000.0}, **kw)
    assert (value, flags) == (64, ["read_floor_violated"])
    # the 128 KB fallback when 128 stays within P
    assert derive.r3(probe_p99_us={"128": 221_000.0}, **kw) == (128, [])
    # an unmeasured raised candidate is not "shown" to break P
    assert derive.r3(probe_p99_us={}, **kw) == (128, [])
    # no candidate reaches the floor
    low = dict(kw, read_ratio={"64": 0.1, "128": 0.2})
    assert derive.r3(probe_p99_us={}, **low) == (64, ["read_floor_violated"])
    # the raised candidate is the hardware maximum: leave the kernel value
    top = dict(kw, max_hw_kb=128, read_ratio={"64": 0.1, "128": 0.9})
    assert derive.r3(probe_p99_us={}, **top) == ("kernel", [])
    # a candidate that already meets the floor stays
    assert derive.r3(probe_p99_us={}, **dict(kw, read_ratio={"64": 0.4})) == (64, [])
    # an unmeasured candidate ratio is an error, never a pass
    with pytest.raises(KeyError):
        derive.r3(probe_p99_us={}, **dict(kw, read_ratio={}))
    # r1002 -> kernel
    assert derive.r3(R_drain_bps=1.3e9, max_hw_kb=1280, F=0.25, P_us=250_000.0, read_ratio={}, probe_p99_us={}) == ("kernel", [])
    assert derive.r3_candidate(10_500_000, 4096) == 64 and derive.r3_candidate(10_000, 4096) == 32
    # R4: the closest cell to the chosen triple must be within P
    cells = [{"name": "REF", "knobs": {"dirty_bytes": 16 * MIB, "nr_requests": 16, "max_sectors_kb": 64}, "probe_p99_us": 104_000.0},
             {"name": "max_sectors_kb+", "knobs": {"dirty_bytes": 16 * MIB, "nr_requests": 16, "max_sectors_kb": 128}, "probe_p99_us": 466_000.0}]
    ok = derive.r4({"dirty_bytes": 16 * MIB, "nr_requests": 16, "max_sectors_kb": 64}, cells, 250_000.0)
    assert ok == {"ok": True, "closest_cell": "REF", "probe_p99_us": 104_000.0}
    bad = derive.r4({"dirty_bytes": 16 * MIB, "nr_requests": 16, "max_sectors_kb": 128}, cells, 250_000.0)
    assert not bad["ok"] and bad["closest_cell"] == "max_sectors_kb+"


def test_der_04_r5():
    assert derive.r5(U=0.70, basis_iops=9400, worst_over_median=0.43) == 2829
    assert derive.r5(U=0.70, basis_iops=9400, worst_over_median=1.7) == 6580  # min(1, worst/median)
    assert derive.r5(U=0.70, basis_bps=14_680_064, worst_over_median=1.0) == 10_276_045


def test_der_05_r6_model():
    # plateau history worst 6 MiB/s, byte-metered -> wbps = 6291456, wseqiops = 1536
    w = derive.r6_keys(direction="write", metering="byte", worst_bps=6 * MIB, worst_iops=None, c_io_seq_us=None,
                       c_io_rand_us=None, c_byte_ns=None)
    assert w == {"wbps": 6291456, "wseqiops": 1536, "wrandiops": 1536}
    # read not byte-metered: iops keys from the fit
    r = derive.r6_keys(direction="read", metering="mixed", worst_bps=4e9, worst_iops=None, c_io_seq_us=50.0, c_io_rand_us=100.0, c_byte_ns=0.25)
    assert r == {"rbps": 4_000_000_000, "rseqiops": 20000, "rrandiops": 10000}
    r2_ = derive.r6_keys(direction="read", metering="undetermined", worst_bps=1e9, worst_iops=None, c_io_seq_us=50.0, c_io_rand_us=100.0, c_byte_ns=0.25)
    assert r2_["rbps"] == 1_000_000_000  # min(1e9 / c_byte, worst)
    i = derive.r6_keys(direction="write", metering="iops", worst_bps=0, worst_iops=3000.0, c_io_seq_us=None, c_io_rand_us=None, c_byte_ns=None)
    assert i == {"wbps": 3000 * MIB, "wseqiops": 3000, "wrandiops": 3000}


def test_der_06_r6_qos():
    # synthetic r1002-model fixture: best read rung 92000, R 9800 -> 110 x 9.388 = 1032.7 -> 1035
    mx, parts = derive.qos_max(best_read_rung=92000, R=9800, best_w=3.2e9, worst_w=1.4e9)
    assert mx == 1035 and parts["ratio_r"] == pytest.approx(9.388, abs=0.001)
    # fill_history_inversion: cached_ref (the miss rate 9.8k) would give 110; the best rung gives >= 355
    assert derive.qos_max(best_read_rung=9800, R=9800, best_w=1, worst_w=1)[0] == 110
    assert derive.qos_max(best_read_rung=31240, R=9800, best_w=1, worst_w=1)[0] >= 355
    # gstammtisch write example (18 / 6 MiB/s) with a read ratio below 3 -> 330
    assert derive.qos_max(best_read_rung=2.9 * 9800, R=9800, best_w=18 * MIB, worst_w=6 * MIB)[0] == 330
    assert derive.wlat_us(4200.0) == 10_000 and derive.wlat_us(17_000.0) == 35_000
    assert derive.wlat_us(100_000.0) == 50_000 and derive.wlat_us(1.0) == 10_000
    assert derive.rlat_us(600.0) == 2000 and derive.rlat_us(3200.0) == 7000 and derive.rlat_us(50_000.0) == 20_000
    assert derive.ceil_to(10.0, 5) == 10 and derive.ceil_to(10.001, 5) == 15
    curve = [{"dir": "write", "load_pct": 10, "lat_us": {"p90": 1000.0, "p95": 1500.0, "p99": 2000.0}},
             {"dir": "write", "load_pct": 30, "lat_us": {"p90": 4200.0, "p95": 5000.0, "p99": 9000.0}},
             {"dir": "write", "load_pct": 60, "lat_us": {"p90": 9000.0, "p95": 9500.0, "p99": 20000.0}},
             {"dir": "read", "load_pct": 10, "lat_us": {"p90": 500.0, "p95": 600.0, "p99": 900.0}},
             {"dir": "read", "load_pct": 30, "lat_us": {"p90": 400.0, "p95": 550.0, "p99": 800.0}, "not_reached": True}]
    assert derive.light_point(curve, "write", "p90") == 4200.0
    assert derive.light_point(curve, "read", "p95") == 600.0  # the not-reached 30 % point is ignored
    assert derive.light_point(curve, "nothing", "p95") is None
    # LAT-02 knee: first load point whose p99 >= 2 x the 10 % point's
    assert derive.latency_knee(curve, "write") == 30
    assert derive.latency_knee(curve, "read") is None
    assert derive.latency_knee([c for c in curve if c["load_pct"] != 10], "write") is None


def test_der_07_recompute_rules():
    assert derive.recompute("R1", dict(W_s=2.0, R_drain_bps=10_500_000, inflight_bytes=1048576, T_eff_bytes=T_EFF)) == 16 * MIB
    assert derive.recompute("R1b", dict(dirty_bytes=16 * MIB)) == 4 * MIB
    assert derive.recompute("R2", dict(qd90_write=8, current_nr_requests=256)) == 16
    assert derive.recompute("R5", dict(U=0.7, basis_iops=9400, worst_over_median=0.43)) == 2829
    assert derive.recompute("R6-min", {}) == 100 and derive.recompute("R6-wpct", {}) == 90 and derive.recompute("R6-rpct", {}) == 95
    assert derive.recompute("R6-wlat", {"light_p90_us": 4200.0}) == 10_000
    assert derive.recompute("R6-rlat", {"light_p95_us": 600.0}) == 2000
    assert derive.recompute("R6-qos-max", dict(best_read_rung=92000, R=9800, best_w=3.2e9, worst_w=1.4e9)) == 1035
    assert derive.recompute("R6-model", dict(key="wbps", direction="write", metering="byte", worst_bps=6 * MIB, worst_iops=None,
                                              c_io_seq_us=None, c_io_rand_us=None, c_byte_ns=None)) == 6291456
    assert derive.recompute("R3", dict(R_drain_bps=10_500_000, max_hw_kb=4096, F=0.25, P_us=250_000.0, read_ratio=GST_RATIO,
                                       probe_p99_us={"128": 466_000.0})) == 64
