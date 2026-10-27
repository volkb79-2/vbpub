"""IOB-PLA / IOB-REF: reference cells and plausibility checks (each row: a controlled-wrong case, plus the healthy ones)."""
import pytest

from iobench import plausibility as P
from iobench import proposals

GIB = 1 << 30


def test_ref_01_protocol():
    assert P.bracket(["A", "B"], "REF") == ["REF", "A", "B", "REF"]
    assert not P.ref_moved(24100.0, 24100.0)
    assert not P.ref_moved(24100.0, 24100.0 * 1.15)   # exactly 15 % does not move
    assert P.ref_moved(24100.0, 24100.0 * 1.16)
    assert P.ref_moved(24100.0, 24100.0 * 0.5)         # a rate multiplier of 0.5 mid-run
    assert P.ref_moved(24100.0, 24100.0 * 0.84)


def test_ref_02_moved_is_finding_not_failure():
    # the healthy "moving cap" case is built first: a moved reference is NOT a failed run
    ok = P.compare_group({"A": 100.0, "B": 90.0}, 1000.0, 1010.0)
    assert not ok["moved"] and ok["reasons"] == [] and ok["finding"] is None
    moved = P.compare_group({"A": 100.0, "B": 90.0}, 1000.0, 500.0)
    assert moved["moved"] and moved["reasons"] == ["ceiling_moved"] and moved["finding"] == "reference_shift"
    assert moved["values"] == {"A": 100.0, "B": 90.0}  # both values stay in the evidence
    obs = P.shift_observation("2026-10-21T04:10:00Z", "write", 21000.0)
    assert obs["note"] == "reference_shift" and obs["duration_s"] == 15 and obs["plateau_bps"] == 21000.0 * 4096


def test_pla_01_controlled_wrong():
    assert P.pla_01("steady").status == "pass" and P.pla_01("flat").status == "pass"
    c = P.pla_01("unsteady")
    assert c.status == "fail" and c.degrades == ("caps", "iocost_model", "iocost_qos")
    assert {e[2] for e in c.effects} == {"basis_unsteady"}


def test_pla_02_controlled_wrong():
    assert P.pla_02(345 * GIB, 270 * GIB, False).status == "pass"
    assert P.pla_02(300 * GIB, 270 * GIB, False).status == "fail"
    assert P.pla_02(345 * GIB, 270 * GIB, True).status == "info"  # limited-by-budget is never a pass
    assert P.pla_02(345 * GIB, None, False).status == "info"


def test_pla_03_to_05_info():
    assert P.pla_03(3.8).value == 3.8 and P.pla_03(3.8).status == "info"
    assert P.pla_04(["rung-64G"]).value == ["rung-64G"] and "none" in P.pla_04([]).detail
    assert P.pla_05(["read-seq-1M"]).status == "info" and "none" in P.pla_05([]).detail


def test_pla_06_controlled_wrong():
    assert P.pla_06(10000.0, 3200.0, 32).status == "pass"      # 10000 x 3.2 ms / 32 = 1.0
    bad = P.pla_06(10000.0, 9000.0, 32)                         # 2.8: broken or faked fio
    assert bad.status == "fail" and bad.effects == (("caps", "absent", "stage_failed"),)
    assert P.pla_06(10000.0, 100.0, 32).status == "fail"


def test_pla_07_controlled_wrong():
    assert P.pla_07(10.0, 12.0, False).status == "pass"
    assert P.pla_07(10.5, 10.0, False).status == "pass"        # 5 % tolerance
    assert P.pla_07(11.0, 10.0, False).status == "fail"
    moved = P.pla_07(11.0, 10.0, True)
    assert moved.status == "info" and moved.effects == (("iocost_model", "unproven", "ceiling_moved"),)


def test_pla_08_controlled_wrong():
    assert P.pla_08([]).status == "pass"
    with pytest.raises(AssertionError, match="outside the written extent"):
        P.pla_08(["read [10G,11G) outside"])


def test_pla_09_controlled_wrong():
    assert P.pla_09(1.3e9, 1.0e9, False).status == "pass"
    assert P.pla_09(1.0e9, 1.3e9, False).status == "pass"
    assert P.pla_09(1.0e9, 6.0e9, False).status == "fail"
    assert P.pla_09(7.0e9, 1.0e9, True).status == "info"


def test_pla_10_to_12():
    assert P.pla_10("cold_measured", []).status == "info" and "(" in P.pla_10("undetermined", ["cache_model_undetermined"]).detail
    assert P.pla_11({"read": "good", "write": "good"}).status == "pass"
    assert P.pla_11({"read": "good", "write": "poor"}).status == "fail"
    assert P.pla_12(1.9).status == "pass" and P.pla_12(4.0).status == "pass"
    bad = P.pla_12(5.0)
    assert bad.status == "fail" and bad.effects == (("writeback", "unproven", "noise_floor_high"),)


def test_pla_13_controlled_wrong():
    assert P.pla_13(3596.0, 71584.0, False).status == "pass"
    assert P.pla_13(100000.0, 71584.0, False).status == "pass"   # 1.4x is within the bound
    assert P.pla_13(120000.0, 71584.0, False).status == "fail"
    moved = P.pla_13(120000.0, 71584.0, True)
    assert moved.status == "info" and moved.effects == (("caps:WIOPS", "unproven", "ceiling_moved"),)


def test_pla_14_15_16_are_run_failures_not_stored_checks():
    from iobench.series import IntegrityError, SeriesError, reduce_series
    with pytest.raises(IntegrityError):
        reduce_series([(float(i), float("nan") if i == 3 else 1.0) for i in range(1, 20)])
    with pytest.raises(IntegrityError):
        reduce_series([(1.0, 1.0)] * 20)
    assert issubclass(IntegrityError, SeriesError)
    assert not hasattr(P, "pla_14") and not hasattr(P, "pla_15") and not hasattr(P, "pla_16")


def test_pla_17_controlled_wrong():
    assert P.pla_17(10e6, None).status == "not_applicable"
    assert P.pla_17(10e6, 12e6).status == "pass"
    out = P.pla_17(40e6, 10e6)
    assert out.status == "info" and out.value == 4.0 and P.pla_17(3e6, 10e6).status == "info"


def test_pla_18_controlled_wrong():
    assert P.pla_18(None, 1.0, False, None).status == "not_applicable"
    assert P.pla_18(1.0, None, False, None).status == "not_applicable"
    ok = P.pla_18(3.6e6, 71e6, False, 19.9)
    assert ok.status == "info" and ok.value["rearm_ratio"] == 19.9 and ok.as_dict()["value"]["fresh_write_bps"] == 3.6e6
    moved = P.pla_18(3.6e6, 71e6, True, 19.9)
    assert moved.status == "info" and "ceiling_moved" in moved.detail
    assert P.Check("IOB-PLA-99", "pass").as_dict() == {"id": "IOB-PLA-99", "status": "pass", "detail": "", "degrades": []}


def _caps():
    def v(x):
        return proposals.value(x, "R5", {"basis_iops": 1}, "measured")
    return {"dev_aggregate": {k: v(10) for k in ("RIOPS", "WIOPS", "RBPS", "WBPS")}}


def test_pla_healthy_and_moving_cap_pass():
    props = {"caps": _caps(), "iocost_model": {"wbps": proposals.value(1, "R6-model", {}, "measured")},
             "iocost_qos": {"max": proposals.value(1, "R6-qos-max", {}, "measured")},
             "writeback": {"dirty_bytes": proposals.value(1, "R1", {}, "formula-only")}, "notes": []}
    healthy = [P.pla_01("steady"), P.pla_02(345 * GIB, 270 * GIB, False), P.pla_06(10000.0, 3200.0, 32), P.pla_07(10.0, 12.0, False),
               P.pla_09(1.0, 1.0, False), P.pla_11({"read": "good", "write": "good"}), P.pla_12(1.0), P.pla_13(1.0, 1.0, False)]
    assert all(c.status == "pass" for c in healthy)
    assert P.apply_effects(props, healthy) == props
    # the moving cap: every comparison is moved, nothing FAILS, the values only become unproven
    moving = [P.pla_07(11.0, 10.0, True), P.pla_09(7.0, 1.0, True), P.pla_13(3.0, 1.0, True)]
    assert not any(c.status == "fail" for c in moving)
    out = P.apply_effects(props, moving)
    assert out["iocost_model"]["wbps"]["reasons"] == ["ceiling_moved"]
    assert out["caps"]["dev_aggregate"]["WIOPS"]["reasons"] == ["ceiling_moved"]
    assert out["caps"]["dev_aggregate"]["RBPS"]["reasons"] == ["ceiling_moved"] and "reasons" not in out["caps"]["dev_aggregate"]["RIOPS"]


def test_pla_effects_degrade_only_what_they_list():
    props = {"caps": _caps(), "iocost_model": {"wbps": proposals.value(1, "R6-model", {}, "measured")},
             "iocost_qos": {"max": proposals.value(1, "R6-qos-max", {}, "measured")},
             "writeback": {"dirty_bytes": proposals.value(1, "R1", {}, "formula-only")}, "notes": [{"note": "x"}]}
    out = P.apply_effects(props, [P.pla_09(1.0, 6.0, False)])
    caps = out["caps"]["dev_aggregate"]
    assert caps["RBPS"]["status"] == "absent" and caps["WBPS"]["reason"] == "stage_failed" and caps["RIOPS"]["status"] == "proven"
    assert out["iocost_model"]["wbps"]["status"] == "proven" and out["notes"] == [{"note": "x"}]
    out = P.apply_effects(props, [P.pla_13(9.0, 1.0, False)])
    assert out["caps"]["dev_aggregate"]["WIOPS"]["status"] == "absent" and out["caps"]["dev_aggregate"]["RIOPS"]["status"] == "proven"
    out = P.apply_effects(props, [P.pla_01("unsteady")])
    assert all(i["reason"] == "basis_unsteady" for i in out["caps"]["dev_aggregate"].values())
    assert out["iocost_model"]["wbps"]["reason"] == "basis_unsteady" and out["iocost_qos"]["max"]["status"] == "absent"
    assert out["writeback"]["dirty_bytes"]["status"] == "proven"
    out = P.apply_effects(props, [P.pla_12(9.0)])
    assert out["writeback"]["dirty_bytes"]["reasons"] == ["noise_floor_high"]
    # a target whose section is not present is ignored
    only = {"caps": _caps(), "notes": []}
    assert P.apply_effects(only, [P.pla_12(9.0)]) == only
    out = P.apply_effects(props, [P.pla_06(10000.0, 9000.0, 32)])
    assert out["caps"]["dev_aggregate"]["RIOPS"]["status"] == "absent"


def test_pla_04_mean_vs_steady():
    c = P.pla_04(["rung-64G"])
    assert c.status == "info" and c.value == ["rung-64G"] and c.degrades == () if hasattr(c, "degrades") else c.status == "info"
    assert P.pla_04([]).status == "info" and "none" in P.pla_04([]).detail


def test_pla_05_flat_line():
    c = P.pla_05(["read-seq-1M"])
    assert c.status == "info" and "read-seq-1M" in str(c.value) + c.detail
    assert "none" in P.pla_05([]).detail


def test_pla_11_shape_fit():
    assert P.pla_11({"read": "good", "write": "good"}).status == "pass"
    bad = P.pla_11({"read": "good", "write": "poor"})
    assert bad.status == "fail" and bad.degrades == ("iocost_model", "iocost_qos") and "shape_fit_poor" in bad.detail


def test_pla_12_noise_floor():
    assert P.pla_12(4.0).status == "pass" and P.pla_12(4.01).status == "fail"
    assert P.pla_12(5.0).effects == (("writeback", "unproven", "noise_floor_high"),)


def _identity_flip_after_body(monkeypatch, flip):
    from iobench import calibrate as cal_mod
    original = cal_mod.Calibrator.body

    def body(self):
        original(self)
        flip(self.host)

    monkeypatch.setattr(cal_mod.Calibrator, "body", body)


def test_pla_15_identity_stable(monkeypatch):
    from helpers import INVERSION, calibrate, make_host
    stable = calibrate(make_host(cache=INVERSION))
    assert stable.status == "complete"
    host = make_host(cache=INVERSION)
    _identity_flip_after_body(monkeypatch, lambda h: setattr(h.devices["/dev/vda7"], "partuuid", "00000000-0000-0000-0000-00000000dead"))
    out = calibrate(host)
    assert out.status == "failed" and out.exit_code == 1 and "IOB-PLA-15" in out.message
    assert not host.exists("/var/lib/iobench/calibration.json") or "IOB-PLA-15" not in host.read_text("/var/lib/iobench/calibration.json")


def test_pla_16_extent_integrity():
    from helpers import INVERSION, calibrate, make_host
    from iobench import devio
    from iobench.stages import IntegrityViolation
    host = make_host(cache=INVERSION)
    from iobench import calibrate as cal_mod
    from helpers import config
    r = cal_mod.Calibrator(host, config())
    r.s0(need_workdir=False)
    r.allowed_writes.add(0, 1 << 30)
    ok = devio.FioJob(name="x-ok", filename="/dev/vda7", rw="randwrite", bs=4096, iodepth=1, offset=0, size=1 << 20, runtime_s=1, exclusive=True)
    r.spawn(ok).kill()
    bad = devio.FioJob(name="x-bad", filename="/dev/vda7", rw="randwrite", bs=4096, iodepth=1, offset=2 << 30, size=1 << 20, runtime_s=1, exclusive=True)
    with pytest.raises(IntegrityViolation, match="IOB-PLA-16"):
        r.spawn(bad)
    tail = devio.FioJob(name="x-tail", filename="/dev/vda7", rw="randwrite", bs=4096, iodepth=1, offset=435853320192 - (1 << 20), size=1 << 20, runtime_s=1, exclusive=True)
    r.allowed_writes.add(0, 435853320192)
    with pytest.raises(IntegrityViolation, match="IOB-PLA-16"):
        r.spawn(tail)
    r.safety.release()
