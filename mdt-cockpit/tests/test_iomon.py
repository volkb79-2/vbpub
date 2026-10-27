"""Unit tests for scripts/iomon.py delta / latency / episode computations."""
from __future__ import annotations

import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "iomon", Path(__file__).resolve().parents[1] / "scripts" / "iomon.py")
iomon = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(iomon)

LINE = ("254 0 vda 100 5 2000 300 50 6 1000 700 3 800 1200 0 0 0 0 10 40\n"
        "253 0 dm-0 1 0 8 1 0 0 0 0 0 1 1\n")


class DiskTests(unittest.TestCase):
    def test_parse_filters_devices(self):
        d = iomon.parse_diskstats(LINE, {"vda"})
        self.assertEqual(list(d), ["vda"])
        self.assertEqual(d["vda"][0], 100)

    def test_delta_await_util_throughput(self):
        prev = [100, 0, 2000, 300, 50, 0, 1000, 700, 3, 800, 1200, 0, 0, 0, 0, 10, 40]
        cur = [200, 0, 4000, 500, 150, 0, 3000, 1700, 7, 1800, 3200, 0, 0, 0, 0, 20, 140]
        r = iomon.disk_delta(prev, cur, 5.0)
        self.assertEqual(r["ri"], 20.0)
        self.assertEqual(r["wi"], 20.0)
        self.assertAlmostEqual(r["ra"], 2.0)       # 200 ms / 100 reads
        self.assertAlmostEqual(r["wa"], 10.0)      # 1000 ms / 100 writes
        self.assertAlmostEqual(r["util"], 20.0)    # 1000 ms busy / 5000 ms
        self.assertAlmostEqual(r["q"], 0.4)        # 2000 ms-in-queue / 5000 ms
        self.assertAlmostEqual(r["rmb"], 2000 * 512 / 5 / 1e6, places=3)
        self.assertEqual(r["inf"], 7)
        self.assertAlmostEqual(r["fl"], 10.0)      # 100 ms / 10 flushes

    def test_idle_interval_has_no_await(self):
        v = [10, 0, 80, 5, 10, 0, 80, 5, 0, 3, 3]
        r = iomon.disk_delta(v, v, 5.0)
        self.assertIsNone(r["ra"])
        self.assertIsNone(r["wa"])
        self.assertEqual(r["util"], 0.0)

    def test_counter_reset_and_bad_dt_rejected(self):
        hi = [100, 0, 0, 0, 100, 0, 0, 0, 0, 100, 0]
        lo = [1, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0]
        self.assertIsNone(iomon.disk_delta(hi, lo, 5.0))
        self.assertIsNone(iomon.disk_delta(lo, hi, 0.0))

    def test_util_capped_at_100(self):
        a = [0] * 11
        b = [1, 0, 0, 0, 1, 0, 0, 0, 0, 99999, 0]
        self.assertEqual(iomon.disk_delta(a, b, 5.0)["util"], 100.0)


class PsiTests(unittest.TestCase):
    P1 = "some avg10=1.00 avg60=2.00 avg300=3.00 total=1000000\nfull avg10=0.50 avg60=0.10 avg300=0.00 total=500000\n"
    P2 = "some avg10=9.00 avg60=2.00 avg300=3.00 total=2000000\nfull avg10=4.00 avg60=0.10 avg300=0.00 total=1000000\n"

    def test_delta_percent(self):
        r = iomon.psi_delta(iomon.parse_psi(self.P1), iomon.parse_psi(self.P2), 5.0)
        self.assertAlmostEqual(r["sp"], 20.0)   # 1 s stalled in 5 s
        self.assertAlmostEqual(r["fp"], 10.0)
        self.assertEqual(r["sa10"], 9.0)

    def test_cpu_without_full_line(self):
        r = iomon.psi_delta({}, iomon.parse_psi("some avg10=1 avg60=1 avg300=1 total=5\n"), 5.0)
        self.assertIsNone(r["sp"])
        self.assertIsNone(r["fa10"])


class StatTests(unittest.TestCase):
    def test_percentile(self):
        v = list(range(1, 101))
        self.assertAlmostEqual(iomon.percentile(v, 50), 50.5)
        self.assertAlmostEqual(iomon.percentile(v, 99), 99.01)
        self.assertIsNone(iomon.percentile([None], 50))
        self.assertEqual(iomon.percentile([7], 95), 7)

    def test_probe_stats(self):
        s = iomon.probe_stats([{"w": 1.0, "r": 0.5}, {"w": 3.0, "r": 0.7}, {"e": "x"}])
        self.assertEqual(s["w"]["n"], 2)
        self.assertEqual(s["w"]["max"], 3.0)
        self.assertEqual(s["err"], 1)

    def test_container_delta(self):
        prev = {"cpu_ns": 0, "thr_periods": 1, "thr_ns": 0, "pgmaj": 10, "refault": 0,
                "pgscan": 0, "pgsteal": 0, "blk": {"254:0": {"read": 0, "write": 0}}, "blk_key": "254:0"}
        cur = dict(prev, cpu_ns=10_000_000_000, thr_periods=4, thr_ns=2_000_000, pgmaj=60,
                   blk={"254:0": {"read": 5_000_000, "write": 50_000_000}},
                   mem=1, anon=1, file=1, dirty=0, wb=0, pids=3)
        r = iomon.container_delta(prev, cur, 5.0)
        self.assertEqual(r["cpu"], 2.0)
        self.assertEqual(r["thr_p"], 3)
        self.assertEqual(r["thr_ms"], 2.0)
        self.assertEqual(r["majf"], 10.0)
        self.assertEqual(r["blk_wmb"], 10.0)
        self.assertEqual(iomon.container_delta(prev, None, 5.0), {"up": False})


def _s(t, wa, fp=0.0, thr=0, cid="c1", wmb=1.0, majf=0.0):
    return {"k": "s", "t": t,
            "disk": {"vda": {"ri": 1, "wi": 10, "rmb": 0, "wmb": 1, "ra": 1.0, "wa": wa,
                             "util": 5, "q": 0.1, "inf": 1}},
            "psi": {"io": {"sp": fp, "fp": fp, "fa10": 0}},
            "ct": {cid: {"up": True, "thr_p": thr, "majf": majf, "blk_wmb": wmb}}}


class EpisodeTests(unittest.TestCase):
    def test_merge(self):
        e = iomon.merge_episodes([(0, "a", 1), (5, "a", 3), (6, "b", 2), (100, "a", 1)], 10)
        self.assertEqual(len(e), 2)
        self.assertEqual(e[0]["end"], 6)
        self.assertEqual(e[0]["kinds"]["a"]["peak"], 3)

    def test_detect_latency_psi_throttle(self):
        s = [_s(1000 + 5 * i, 2.0) for i in range(30)]
        s[10] = _s(1050, 400.0, fp=20.0)
        s[11] = _s(1055, 350.0, thr=2)
        host, ct = iomon.detect_points(s, "vda", ["c1"])
        kinds = {k.split(">")[0] for _, k, _ in host}
        self.assertIn("wlat", kinds)
        self.assertIn("psi_io_full", kinds)
        self.assertEqual([k for _, k, _ in ct["c1"]], ["throttle"])
        eps = iomon.merge_episodes(host, 10)
        self.assertEqual(len(eps), 1)
        self.assertTrue(iomon.overlaps(eps[0], iomon.merge_episodes(ct["c1"], 10)[0], 10))

    def test_quiet_series_has_no_episode(self):
        s = [_s(1000 + 5 * i, 2.0 + (i % 3) * 0.1) for i in range(30)]
        host, ct = iomon.detect_points(s, "vda", ["c1"])
        self.assertEqual(host, [])
        self.assertEqual(ct["c1"], [])

    def test_summarize_minute(self):
        s = [_s(1020 + 5 * i, 2.0 + i) for i in range(12)]
        m = iomon.summarize_minute(s, "vda")
        self.assertEqual(m["n"], 12)
        self.assertEqual(m["disk"]["vda"]["wa_max"], 13.0)
        self.assertEqual(m["ct"]["c1"]["up"], True)


class WriterTests(unittest.TestCase):
    def test_rotation_cap_keeps_newest(self):
        with tempfile.TemporaryDirectory() as d:
            w = iomon.Writer(d, cap_bytes=2000, segment_bytes=500)
            names = set()
            for i in range(200):
                w.write({"k": "s", "t": i, "pad": "x" * 40})
                names.add(os.listdir(d)[0] if os.listdir(d) else None)
            w.close()
            files = [f for f in os.listdir(d) if f.startswith("samples-")]
            total = sum(os.path.getsize(os.path.join(d, f)) for f in files)
            self.assertLessEqual(total, 2000 + 500)
            self.assertGreaterEqual(len(files), 1)
            last = sorted(files)[-1]
            rec = json.loads(Path(d, last).read_text().splitlines()[-1])
            self.assertEqual(rec["t"], 199)


class BpfAnalysisTests(unittest.TestCase):
    H = [{"min": 64, "max": 127, "count": 90}, {"min": 128, "max": 255, "count": 9},
         {"min": 4096, "max": 8191, "count": 1}]

    def test_hist_percentiles(self):
        d = {}
        iomon.hist_merge(d, "k", self.H)
        iomon.hist_merge(d, "k", self.H)
        h = d["k"]
        self.assertEqual(iomon.hist_total(h), 200)
        self.assertEqual(iomon.hist_pct(h, 50), 127)
        self.assertEqual(iomon.hist_pct(h, 99), 255)
        self.assertEqual(iomon.hist_pct(h, 100), 8191)
        self.assertEqual(iomon.hist_max(h), 8191)
        self.assertIsNone(iomon.hist_pct({}, 50))

    def test_dev_name(self):
        self.assertEqual(iomon.dev_name(254 << 20), "vda")
        self.assertEqual(iomon.dev_name((253 << 20) | 2), "dm-2")

    def test_aggregate_sums_maps_maxes_gauges_and_merges_hists(self):
        def rec(o):
            return {"k": "bpf", "t": 1.0, "o": o}
        recs = [
            rec({"type": "map", "data": {"@ios": {"1,0,0": 5}}}),
            rec({"type": "map", "data": {"@ios": {"1,0,0": 7}}}),
            rec({"type": "map", "data": {"@rmiss_ppm": 10}}),
            rec({"type": "map", "data": {"@rmiss_ppm": 4}}),
            rec({"type": "hist", "data": {"@lat": {"a": self.H}}}),
            rec({"type": "hist", "data": {"@lat": {"a": self.H}}}),
            rec({"type": "stats", "data": {"@vrate": {"count": 2, "average": 5, "total": 10}}}),
            rec({"ev": "slow_write", "w": 1, "comm": "x", "tid": 1, "ms": 80}),
        ]
        agg = iomon.aggregate_bpf(recs)
        self.assertEqual(agg["maps"]["@ios"]["1,0,0"], 12)
        self.assertEqual(agg["maps"]["@rmiss_ppm"][""], 10)
        self.assertEqual(iomon.hist_total(agg["hists"]["@lat"]["a"]), 200)
        self.assertEqual(agg["stats"]["@vrate"], [2, 10])
        self.assertEqual(len(agg["events"]), 1)

    def test_counter_delta(self):
        d = iomon.counter_delta({"a": {"x": 1, "y": 5}, "z": 3}, {"a": {"x": 4, "y": 2}, "z": 9})
        self.assertEqual(d, {"a": {"x": 3, "y": None}, "z": 6})


if __name__ == "__main__":
    unittest.main()
