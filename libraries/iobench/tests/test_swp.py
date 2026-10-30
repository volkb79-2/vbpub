"""IOB-DRN / IOB-SWP / IOB-RDC: drain rate, interference sweep, read cost."""
import io
import os
import types

import pytest

from iobench import plan, sweep
from iobench.sweep import SweepCell


def test_drn_01_drain_rate_and_consistency():
    # 10.5 MB/s steady after a 3 s ramp: the last 25 % decides
    cum = [0.0]
    for rate in [0] * 3 + [10_500_000] * 57:
        cum.append(cum[-1] + rate)
    assert sweep.drain_rate(cum) == 10_500_000
    # a noisy tail keeps its median
    cum = [0.0]
    for i in range(80):
        cum.append(cum[-1] + (9_000_000 if i % 2 else 12_000_000))
    assert sweep.drain_rate(cum) in (9_000_000, 12_000_000, 10_500_000)
    ok = sweep.rates_consistent(10_500_000, 12_000_000)
    assert ok["consistent"] and ok["reason"] is None
    below = sweep.rates_consistent(3_000_000, 12_000_000)
    assert (below["verdict"], below["reason"]) == ("drain_below_ladder", "drain_below_ladder")
    above = sweep.rates_consistent(40_000_000, 12_000_000)
    assert (above["verdict"], above["reason"]) == ("drain_above_ladder", "cache_residual_possible")
    # the 1.3x edges
    assert sweep.rates_consistent(12_000_000 / 1.3, 12_000_000)["consistent"]
    assert sweep.rates_consistent(12_000_000 * 1.3, 12_000_000)["consistent"]


def test_drn_01_quiet_gap_rule():
    assert not sweep.quiet_gap_ended([1e9] * 10, 10)
    assert sweep.quiet_gap_ended([1e9] * 10, 60)
    assert not sweep.quiet_gap_ended([1000.0] * 4, 4)  # under 5 s of quiet
    assert sweep.quiet_gap_ended([1e9, 1000.0, 2000.0, 3000.0, 4.0, 5.0], 6)
    assert not sweep.quiet_gap_ended([1000.0, 1000.0, 5e6, 1000.0, 1000.0], 6)


BASE = {"dirty_bytes": 16 * 1024 * 1024, "nr_requests": 16, "max_sectors_kb": 64}


def test_swp_01_plan_counts_and_times():
    cells = sweep.sweep_plan(BASE, 1280)
    assert [c.name for c in cells] == ["REF", "dirty_bytes/2", "nr_requests*2", "max_sectors_kb+", "max_sectors_kb-", "REF2"]
    assert len(cells) == plan.sweep_cells() == 6
    assert cells[0].knobs == BASE and cells[-1].knobs == BASE and cells[0].varies is None
    assert cells[1].knobs["dirty_bytes"] == 8 * 1024 * 1024 and cells[2].knobs["nr_requests"] == 32
    assert cells[3].knobs["max_sectors_kb"] == 128 and cells[4].knobs["max_sectors_kb"] == 32
    full = sweep.sweep_plan(BASE, 1280, full=True)
    assert len(full) == plan.sweep_cells(True) == 8 and full[-1].name == "REF2"
    assert {c.name for c in full} >= {"dirty_bytes*2", "nr_requests/2"}
    assert sweep.sweep_seconds(6) == 480 and sweep.sweep_seconds(6, worst=True) == 720
    # r1002: the base is already at max_hw; "one step up" cannot exceed it
    r = sweep.sweep_plan({"dirty_bytes": 3 * 1024 * 1024, "nr_requests": 2, "max_sectors_kb": 1280}, 1280)
    assert r[3].knobs["max_sectors_kb"] == 1280 and r[4].knobs["max_sectors_kb"] == 1024
    assert r[1].knobs["dirty_bytes"] == 1572864
    low = sweep.sweep_plan({**BASE, "nr_requests": 4}, 1280, full=True)
    assert [c for c in low if c.name == "nr_requests/2"][0].knobs["nr_requests"] == 4
    assert sweep.next_pow2_up(100, 1280) == 128 and sweep.next_pow2_down(100) == 64 and sweep.next_pow2_down(4) == 4


def test_swp_02_probe_unit_properties():
    from iobench import devio
    argv = devio.probe_unit_argv("iobench-probe-a1b2", "iobench-probe.slice", 130, sweep.PROBE_SOURCE,
                                 ["/bench/w", "/run/iobench/probe.out", "60", "0.5"])
    text = " ".join(argv)
    assert "RuntimeMaxSec=130" in text and "--slice=iobench-probe.slice" in text
    assert "IOReadBandwidthMax" not in text and "IOWeight" not in text and "Max=" not in text.replace("RuntimeMaxSec=", "")
    # run the probe source against a fake clock: the interval is 0.5 +- 0.05 s
    ns: dict = {"__name__": "probe"}
    exec(compile(sweep.PROBE_SOURCE, "probe", "exec"), ns)
    clock = [0.0]
    stamps = []
    unlinked = []

    class FakeFile(io.BytesIO):
        def write(self, b):
            stamps.append(clock[0])
            return super().write(b)

    files = {}

    def opener(path, mode="r", buffering=-1):
        fh = io.StringIO() if "b" not in mode else FakeFile()
        fh.fileno = lambda: 99  # type: ignore[method-assign]
        orig_close = fh.close
        if "b" not in mode:
            fh.close = lambda: files.__setitem__(path, fh.getvalue()) or orig_close()  # type: ignore[method-assign]
        return fh

    def fdatasync(fd):
        clock[0] += 0.002

    ns["os"] = types.SimpleNamespace(path=os.path, fdatasync=fdatasync, unlink=unlinked.append)
    ns["run"]("/bench/w", "/out", 10.0, 0.5, now=lambda: clock[0], sleep=lambda s: clock.__setitem__(0, clock[0] + s), opener=opener)
    gaps = [b - a for a, b in zip(stamps, stamps[1:])]
    assert len(stamps) >= 19 and all(abs(g - 0.5) <= 0.05 for g in gaps)
    assert unlinked == ["/bench/w/.iobench-probe"]
    lat = [int(x) for x in files["/out"].split()]
    assert len(lat) == len(stamps) and all(v == 2000 for v in lat)


def cell(name, p99, iops=800.0, fsync=90000.0, varies=None):
    return {"name": name, "knobs": {}, "varies": varies, "probe_p99_us": p99, "probe_samples": 120, "load_iops": iops,
            "load_fsync_p99_us": fsync}


def test_swp_03_decision_rule():
    ref_a, ref_b = cell("REF", 1000.0), cell("REF2", 1900.0)  # noise ratio 1.9
    assert sweep.noise_ratio(ref_a, ref_b) == pytest.approx(1.9)
    lucky = cell("nr_requests*2", 300.0, varies="nr_requests")  # 3.3x better in ONE cell
    d = sweep.decide(ref_a, ref_b, lucky, None)
    assert d["wins_in_cell"] and d["needs_repeat"] and not d["replaces_ref"]
    d = sweep.decide(ref_a, ref_b, lucky, cell("nr_requests*2", 900.0, varies="nr_requests"))  # repeat does not confirm
    assert not d["replaces_ref"] and not d["needs_repeat"]
    winner = cell("max_sectors_kb-", 200.0, varies="max_sectors_kb")  # 5x better, consistently
    d = sweep.decide(ref_a, ref_b, winner, cell("max_sectors_kb-", 210.0, varies="max_sectors_kb"))
    assert d["replaces_ref"]
    # a winner that regresses the gate-like load is not a winner
    sick = cell("dirty_bytes/2", 200.0, iops=100.0, varies="dirty_bytes")
    assert not sweep.beats_ref(ref_a, sick, 1.9)
    slow_fsync = cell("dirty_bytes/2", 200.0, fsync=500000.0, varies="dirty_bytes")
    assert not sweep.beats_ref(ref_a, slow_fsync, 1.9)
    out = sweep.choose([ref_a, lucky, winner, ref_b], {"max_sectors_kb-": cell("max_sectors_kb-", 210.0, varies="max_sectors_kb")})
    assert out["winners"]["max_sectors_kb"]["name"] == "max_sectors_kb-" and "nr_requests" not in out["winners"]
    assert out["needs_repeat"] == ["nr_requests*2"] and not out["noise_high"]
    # two winners for one knob: the better p99 stands
    better = cell("max_sectors_kb+", 100.0, varies="max_sectors_kb")
    out = sweep.choose([ref_a, winner, better, ref_b], {"max_sectors_kb-": winner, "max_sectors_kb+": better})
    assert out["winners"]["max_sectors_kb"]["name"] == "max_sectors_kb+"
    out = sweep.choose([ref_a, better, winner, ref_b], {"max_sectors_kb-": winner, "max_sectors_kb+": better})
    assert out["winners"]["max_sectors_kb"]["name"] == "max_sectors_kb+"
    # noise 5 -> noise_floor_high (and no neighbour can beat a noise ratio of 5 by 5x here)
    noisy = sweep.choose([cell("REF", 1000.0), winner, cell("REF2", 5000.0)], {})
    assert noisy["noise_high"] and noisy["noise_floor_ratio"] == 5.0


def test_swp_04_only_allowed_knobs_constant():
    assert sweep.ALLOWED_KNOBS == ("vm.dirty_bytes", "vm.dirty_background_bytes", "queue/nr_requests", "queue/max_sectors_kb")


def test_swp_cell_record():
    rec = sweep.cell_record(SweepCell("REF", BASE, None), [100.0] * 117 + [900.0, 900.0, 1500.0], 800.0, 90000.0)
    assert rec["probe_p99_us"] == 900.0 and rec["probe_samples"] == 120 and rec["varies"] is None
    assert sweep.percentile([1.0], 99) == 1.0


def test_rdc_01_read_ratio():
    assert sweep.read_ratio(0.27e9, 1.3e9) == pytest.approx(0.2077, abs=0.001)
    cands = sweep.rdc_candidates([64, 128], 1280)
    assert cands == [64, 128, 1280] and sweep.rdc_seconds(len(cands)) == 50
    assert sweep.rdc_candidates([1280], 1280) == [1280]
