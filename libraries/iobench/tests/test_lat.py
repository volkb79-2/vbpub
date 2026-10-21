"""IOB-LAT: the latency-vs-load curve and the latency knee."""
import json
import pathlib

import pytest

from iobench import derive

from helpers import INVERSION, calibrate, fio_jobs, make_host

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "latency_curve_synthetic.json"


def test_lat_01_curve_and_light_points():
    host = make_host(cache=None, read=dict(iops_max=21600, lat_us=300), write=dict(iops_max=21600, lat_us=440, bps_cap_seq=1.36e9, bps_cap_rand=1.36e9))
    out = calibrate(host)
    curve = out.calibration["calibration"]["latency_curve"]
    assert sorted({(p["dir"], p["load_pct"]) for p in curve}) == sorted((d, pct) for d in ("read", "write") for pct in (10, 30, 60, 90))
    for p in curve:
        assert set(p["lat_us"]) == {"p50", "p90", "p95", "p99"} and p["offered_iops"] > 0
        assert p["not_reached"] is False and p["achieved_iops"] == pytest.approx(p["offered_iops"], rel=0.1)
    jobs = [j for j in fio_jobs(host) if "-s7-" in j.name]
    assert len(jobs) == 8 and all(j.runtime_s == 20 and j.rate_iops and j.bs == 4096 for j in jobs)
    # the QoS latencies come from the larger of the 10 % and 30 % points
    w = {p["load_pct"]: p["lat_us"]["p90"] for p in curve if p["dir"] == "write"}
    assert derive.light_point(curve, "write", "p90") == max(w[10], w[30])
    qos = out.result["proposals"]["iocost_qos"]
    assert qos["wlat"]["inputs"]["light_p90_us"] == max(w[10], w[30])
    # a point whose achieved rate is below 90 % of the offered rate is not_reached (the ceiling halves during S7)
    flag = {"s7": False}
    moving = make_host(cache=None, read=dict(iops_max=21600, lat_us=300), write=dict(iops_max=21600, lat_us=440, bps_cap_seq=1.36e9, bps_cap_rand=1.36e9),
                       ceiling=lambda t: 0.4 if flag["s7"] else 1.0)
    real_spawn = moving.spawn

    def spawn(argv):
        flag["s7"] = flag["s7"] or any("-s7-r90" in a for a in argv)  # the ceiling collapses at the 90 % read point
        return real_spawn(argv)

    moving.spawn = spawn
    o2 = calibrate(moving)
    c2 = o2.calibration["calibration"]["latency_curve"]
    assert o2.status == "complete"
    assert [p["not_reached"] for p in c2 if p["dir"] == "read" and p["load_pct"] == 90] == [True]
    assert not any(p["not_reached"] for p in c2 if p["load_pct"] == 10)
    quick = calibrate(make_host(cache=None), profile="quick")
    assert sorted({p["load_pct"] for p in quick.calibration["calibration"]["latency_curve"]}) == [10, 30, 90]


def test_lat_02_knee():
    doc = json.loads(FIXTURE.read_text())
    assert doc["_provenance"]["synthetic"] is True
    curve = doc["curve"]
    assert derive.latency_knee(curve, "write") == 60      # p99 4800 >= 2 x 2000 at the 60 % point
    assert derive.latency_knee(curve, "read") == 90       # p99 1900 >= 2 x 700 only at 90 %
    flat = [dict(p, lat_us={**p["lat_us"], "p99": 2000}) for p in curve if p["dir"] == "write"]
    assert derive.latency_knee(flat, "write") is None
