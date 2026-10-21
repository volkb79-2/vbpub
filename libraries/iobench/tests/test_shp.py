"""IOB-SHP: cost shape grid, fit and metering."""
import pytest

from iobench import shape
from iobench.shape import SIZES

GIB = 1 << 30


def make_cells(fn, *, dirs=("read", "write"), fresh=False):
    """fn(dir, pattern, bs) -> steady_iops."""
    cells = []
    for d in dirs:
        for p in ("seq", "rand"):
            for bs in SIZES:
                iops = fn(d, p, bs)
                cells.append({"id": f"{d}-{p}-{bs}", "dir": d, "pattern": p, "bs": bs, "block_state": "rewrite",
                              "steady_iops": iops, "steady_bps": iops * bs, "repeat": False})
    return cells


def test_shp_01_grid_plan():
    plan = shape.grid_plan(seed=1)
    body = [c for c in plan if not c.repeat]
    assert len(body) == 22
    assert not [c for c in body if c.dir == "read" and c.block_state == "fresh"]
    assert sorted((c.dir, c.pattern, c.bs) for c in body if c.block_state == "fresh") == [("write", "rand", 4096), ("write", "seq", 1048576)]
    anchors = [c for c in body if c.anchor]
    assert {(c.dir, c.pattern, c.bs) for c in anchors} == {(d, p, b) for d in ("read", "write") for p, b in (("rand", 4096), ("seq", 1048576))}
    assert [c.anchor for c in plan[:4]] == [True] * 4  # anchors at the start
    repeats = [c for c in plan if c.repeat]
    assert len(repeats) == 4 and sum(c.seconds for c in repeats) == 60  # the 60 s of anchor repeats in the S6 allotment
    mid = len(plan) // 2
    assert any(c.repeat for c in plan[mid - 4:mid + 4]) and plan[-1].repeat
    assert sum(c.seconds for c in body) == 22 * 30
    # fixed order for a fixed seed; a different seed reorders; every id is unique among the body
    assert [c.id for c in shape.grid_plan(seed=1)] == [c.id for c in plan]
    assert [c.id for c in shape.grid_plan(seed=2)] != [c.id for c in plan]
    assert len({c.id for c in body}) == 22
    assert [c.id for c in plan[:9]] == [
        "read-seq-1048576-rewrite", "read-rand-4096-rewrite", "write-seq-1048576-rewrite", "write-rand-4096-rewrite",
        "read-rand-65536-rewrite", "read-seq-262144-rewrite", "write-rand-65536-rewrite", "read-seq-4096-rewrite",
        "write-seq-16384-rewrite"]
    assert shape.Cell("read", "rand", 4096, "rewrite").rw == "randread" and plan[0].qd == 32


def test_shp_01_quick_grid():
    quick = shape.grid_plan(quick=True)
    assert len(quick) == 8 and {c.bs for c in quick} == {4096, 65536} and all(c.seconds == 20 for c in quick)
    assert not any(c.anchor or c.repeat or c.block_state == "fresh" for c in quick)


def test_shp_01_anchor_drift():
    recs = [{"dir": "write", "pattern": "rand", "bs": 4096, "block_state": "rewrite", "steady_iops": v} for v in (100.0, 110.0, 105.0)]
    recs.append({"dir": "write", "pattern": "seq", "bs": 65536, "block_state": "rewrite", "steady_iops": 5.0})
    recs.append({"dir": "write", "pattern": "rand", "bs": 4096, "block_state": "fresh", "steady_iops": 1.0})
    d = shape.anchor_drift(recs)
    assert not d["moved"] and d["spread"] == {"write-rand-4096": pytest.approx(0.10)}
    recs.append({**recs[0], "steady_iops": 50.0})
    assert shape.anchor_drift(recs)["moved"]


def test_shp_02_fit():
    # synthetic c_io = 80 us, c_byte = 0.25 ns recovered within 2 %
    cells = make_cells(lambda d, p, bs: 1 / (80e-6 + bs * 0.25e-9), dirs=("read",))
    f = shape.fit_cells([c for c in cells if c["pattern"] == "rand"])
    assert f["fit"] == "good" and f["c_io_us"] == pytest.approx(80, rel=0.02) and f["c_byte_ns"] == pytest.approx(0.25, rel=0.02)
    # noise only -> poor
    noise = [{"bs": bs, "steady_iops": v} for bs, v in zip(SIZES, (9000.0, 2000.0, 9500.0, 1500.0, 8000.0))]
    assert shape.fit_cells(noise)["fit"] == "poor"
    # a flat-bytes disk (c_io = 0) is a perfect line
    flat = [{"bs": bs, "steady_iops": 14e6 / bs} for bs in SIZES]
    assert shape.fit_cells(flat)["c_io_us"] == pytest.approx(0, abs=1e-6)
    assert shape.fit_cells([{"bs": 4096, "steady_iops": 1.0}] * 2)["fit"] == "insufficient"
    same = [{"bs": bs, "steady_iops": 100.0} for bs in SIZES]
    assert shape.fit_cells(same)["fit"] == "good"  # zero variance in y: r2 defined as 1, constant cost per IO


def metering(fn, **kw):
    cells = make_cells(fn)
    return shape.shape_summary(cells, **kw)


def test_shp_03_metering():
    byte = metering(lambda d, p, bs: 14e6 / bs)
    assert byte["metering_by_dir"] == {"read": "byte", "write": "byte"} and byte["metering"] == "byte"
    iops = metering(lambda d, p, bs: 3000.0)
    assert iops["metering"] == "iops"
    mixed = metering(lambda d, p, bs: 1 / (80e-6 + bs * 0.25e-9))
    assert mixed["metering"] == "mixed" and mixed["linear_model_fit"] == "good"
    # CONTROLLED WRONG: flat bytes across sizes but seq != rand -> undetermined, not byte
    wrong = metering(lambda d, p, bs: (14e6 if p == "rand" else 30e6) / bs)
    assert wrong["metering"] == "undetermined" and wrong["metering_detail"]["write"]["tests"]["t_pattern_byte"] is False
    # seq == rand but not flat (and no linear fit) -> undetermined
    erratic = {4096: 9000.0, 16384: 2000.0, 65536: 9500.0, 262144: 1500.0, 1048576: 8000.0}
    assert metering(lambda d, p, bs: erratic[bs])["metering"] == "undetermined"
    # a byte-metered disk with a fresh penalty stays byte (fresh cells are a separate field)
    cells = make_cells(lambda d, p, bs: 14e6 / bs)
    cells.append({"id": "f", "dir": "write", "pattern": "rand", "bs": 4096, "block_state": "fresh", "steady_iops": 180.0,
                  "steady_bps": 180.0 * 4096, "repeat": False})
    assert shape.shape_summary(cells)["metering"] == "byte"
    # a moved reference makes it undetermined with ceiling_moved
    drifting = make_cells(lambda d, p, bs: 14e6 / bs)
    drifting += [{**c, "repeat": True, "steady_iops": c["steady_iops"] * 0.5} for c in drifting
                 if c["dir"] == "write" and shape.is_anchor(c["pattern"], c["bs"])]
    # drift is judged on rewrite anchors that are NOT flagged repeat, so mark them as plain rewrites
    for c in drifting:
        c["repeat"] = False
    out = shape.shape_summary(drifting)
    assert out["drift"]["moved"] and out["metering"] == "undetermined"
    assert out["metering_detail"]["write"]["reasons"] == ["ceiling_moved"]
    # override is stored and flagged
    ov = metering(lambda d, p, bs: 14e6 / bs, override="iops")
    assert ov["override"] == "iops" and ov["metering"] == "iops" and ov["metering_detail"]["read"]["override"] == "iops"
    # too few sizes (quick) cannot be assessed
    quick = [c for c in make_cells(lambda d, p, bs: 14e6 / bs) if c["bs"] in (4096, 65536)]
    q = shape.shape_summary(quick)
    assert q["metering"] == "undetermined" and q["linear_model_fit"] == "insufficient"
    only_read = shape.shape_summary([c for c in make_cells(lambda d, p, bs: 14e6 / bs) if c["dir"] == "read"])
    assert only_read["metering_by_dir"]["write"] == "undetermined" and only_read["linear_model_fit_by_dir"]["write"] == "insufficient"


def test_shp_04_model_keys():
    import copy
    from iobench import proposals as P
    from helpers import INVERSION, calibrate, make_host, read_json
    host = make_host(cache=INVERSION)
    calibrate(host)
    base = read_json(host, "/var/lib/iobench/calibration.json")["calibration"]
    history = {"history": "single-run"}

    def proposals_for(cells):
        c = copy.deepcopy(base)
        c["shape"] = shape.shape_summary(cells)
        return P.build_calibration_proposals(c, P.DEFAULT_TARGETS, history)

    erratic = {4096: 9000.0, 16384: 2000.0, 65536: 9500.0, 262144: 1500.0, 1048576: 8000.0}
    poor = proposals_for(make_cells(lambda d, p, bs: erratic[bs]))
    assert all(v["status"] == "absent" and v["reason"] == "stage_failed" for v in poor["iocost_model"].values())
    assert "shape_fit_poor" in poor["iocost_qos"]["max"].get("reasons", ())          # the dependent QoS names the cause
    # a good fit whose metering is undetermined: computed from the fit, unproven: metering_undetermined
    und = proposals_for(make_cells(lambda d, p, bs: 1 / ((80e-6 if p == "rand" else 20e-6) + bs * 0.25e-9)))
    for key in ("wbps", "wseqiops", "wrandiops"):
        assert und["iocost_model"][key]["status"] == "unproven" and "metering_undetermined" in und["iocost_model"][key]["reasons"]
        assert und["iocost_model"][key]["value"] > 0
    # a good, byte-metered fit has no such reason
    good = proposals_for(make_cells(lambda d, p, bs: 14e6 / bs))
    assert "metering_undetermined" not in good["iocost_model"]["wbps"].get("reasons", ())
