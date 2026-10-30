"""IOB-CEI: ceiling history."""
import json

import pytest

from iobench import ceilings
from iobench.errors import Refusal
from iobench.testing import standard_host

IDENT = {"target": {"kind": "device"}, "disk": {"devno": "254:7", "partuuid": "p", "size_bytes": 1, "model": None, "serial": None, "wwn": None}}
DIGEST = ceilings.identity_digest(IDENT)
PATH = "/var/lib/iobench/ceilings.jsonl"
T0 = 1_792_550_400.0


def obs(ts=T0, d="write", w="seq1m", bps=14_680_064, dur=600, src="governor", **kw):
    return {"ts": ceilings.iso(ts), "dir": d, "workload": w, "plateau_bps": bps, "duration_s": dur, "source": src, **kw}


def test_cei_01_append_only_atomic():
    host = standard_host()
    host.makedirs("/var/lib/iobench")
    ceilings.append(host, PATH, obs(), DIGEST)
    ceilings.append(host, PATH, obs(ts=T0 + 60, bps=6_291_456), DIGEST)
    lines = host.read_text(PATH).splitlines()
    assert json.loads(lines[0]) == {"header": True, "schema": 1, "identity_digest": DIGEST}
    assert [json.loads(x)["plateau_bps"] for x in lines[1:]] == [14_680_064, 6_291_456]
    # the file is only ever appended to after the header (single write per observation)
    assert host.files[PATH].startswith(lines[0] + "\n") and host.files[PATH].endswith("\n")
    # a history of another identity is never appended to
    other = ceilings.identity_digest({**IDENT, "target": {"kind": "file"}})
    assert other != DIGEST  # TGT-09: the target kind is part of the digest
    with pytest.raises(Refusal) as err:
        ceilings.append(host, PATH, obs(), other)
    assert err.value.condition == "history_identity_mismatch"
    assert len(host.read_text(PATH).splitlines()) == 3


def test_cei_02_refusals():
    for bad, cond in ((obs(bps=0), "ceiling_plateau"), (obs(bps=-5), "ceiling_plateau"), (obs(bps=float("nan")), "ceiling_plateau"),
                      (obs(bps=float("inf")), "ceiling_plateau"), (obs(bps="x"), "ceiling_plateau"), (obs(bps=True), "ceiling_plateau"),
                      (obs(dur=59), "ceiling_duration"), (obs(dur=float("nan")), "ceiling_duration"), (obs(dur="x"), "ceiling_duration"),
                      (obs(src="oracle"), "ceiling_source"),
                      (obs(w="rand8k"), "ceiling_workload"), (obs(d="both"), "ceiling_dir")):
        with pytest.raises(Refusal) as err:
            ceilings.validate(bad)
        assert err.value.condition == cond, bad
    ceilings.validate(obs(dur=60))
    # the reference-shift note is exempt from the 60 s rule (15 s)
    ceilings.validate(ceilings.reference_shift(ceilings.iso(T0), "write", 86_000_000.0))
    with pytest.raises(Refusal):
        ceilings.validate(obs(dur=14, note="reference_shift"))
    with pytest.raises(Refusal):
        ceilings.validate(obs(dur=15))


def test_cei_03_summary_and_identity():
    host = standard_host()
    host.makedirs("/var/lib/iobench")
    for i, bps in enumerate((18_874_368, 14_680_064, 6_291_456)):
        ceilings.append(host, PATH, obs(ts=T0 - i * 3600, bps=bps), DIGEST)
    ceilings.append(host, PATH, ceilings.reference_shift(ceilings.iso(T0), "write", 1.0), DIGEST)
    ceilings.append(host, PATH, obs(ts=T0 - 40 * 86400, bps=1), DIGEST)  # outside the 30 d window
    ceilings.append(host, PATH, obs(ts=T0, d="read", bps=1_400_000_000), DIGEST)
    rows, warns = ceilings.load(host, PATH, DIGEST)
    assert warns == [] and len(rows) == 6
    s = ceilings.summary(rows, T0, 30)
    assert s["history"] == "recorded" and s["window_days"] == 30
    assert s["write"]["seq1m"] == {"worst": 6_291_456, "median": 14_680_064, "best": 18_874_368, "n": 3}
    assert s["read"]["seq1m"]["n"] == 1 and "rand4k" not in s["write"]
    # no records -> single-run; absent file -> single-run
    assert ceilings.summary([], T0) == {"history": "single-run", "window_days": 30}
    assert ceilings.load(host, "/nope.jsonl", DIGEST) == ([], [])
    # a history of another identity is ignored with a warning
    rows, warns = ceilings.load(host, PATH, "sha256:other")
    assert rows == [] and warns == ["history_ignored_identity_differs"]
    assert ceilings.parse_iso(ceilings.iso(T0)) == T0


def test_cei_04_recalibrate_rule():
    cal = {"write/seq1m": 14_680_064.0}

    def series(*pairs):
        return [obs(ts=T0 - h * 3600, bps=bps) for h, bps in pairs]

    # median of the last 12 h differs by > 25 % in two consecutive windows -> recommended
    low = series((1, 6e6), (3, 6e6), (14, 6.5e6), (20, 6e6))
    assert ceilings.recalibrate_recommended(low, cal, T0)
    # only one window differs
    assert not ceilings.recalibrate_recommended(series((1, 6e6), (14, 14.5e6)), cal, T0)
    # within 25 %
    assert not ceilings.recalibrate_recommended(series((1, 12e6), (14, 13e6)), cal, T0)
    # a window without observations is not a shift; reference shifts are ignored; other keys are ignored
    assert not ceilings.recalibrate_recommended(series((1, 6e6)), cal, T0)
    shifts = low + [ceilings.reference_shift(ceilings.iso(T0), "write", 1.0)]
    assert ceilings.recalibrate_recommended(shifts, cal, T0)
    assert not ceilings.recalibrate_recommended(low, {"read/seq1m": 1e9}, T0)
    assert not ceilings.recalibrate_recommended([], cal, T0)


def test_cei_05_run_appends_plateaus():
    plateaus = [{"dir": "write", "workload": "rand4k", "plateau_bps": 14.7e6, "observation_s": 240, "state": "steady"},
                {"dir": "read", "workload": "rand4k", "plateau_bps": 40e6, "observation_s": 120, "state": "flat"},
                {"dir": "write", "workload": "seq1m", "plateau_bps": 9e6, "observation_s": 30, "state": "steady"},
                {"dir": "read", "workload": "rand4k", "plateau_bps": 1e6, "observation_s": 120, "state": "unsteady"}]
    rows = ceilings.run_observations(plateaus, ceilings.iso(T0))
    assert [(r["dir"], r["workload"], r["duration_s"], r["source"]) for r in rows] == [("write", "rand4k", 240, "benchmark"), ("read", "rand4k", 120, "benchmark")]
    for r in rows:
        ceilings.validate(r)


def test_cei_05_run_appends_plateaus_in_a_run():
    from helpers import INVERSION, calibrate, make_host
    host = make_host(cache=INVERSION)
    out = calibrate(host)
    rows = [json.loads(x) for x in host.read_text("/var/lib/iobench/ceilings.jsonl").splitlines()]
    assert rows[0]["header"] is True and rows[0]["identity_digest"] == ceilings.identity_digest(out.result["identity"])
    obs = [r for r in rows[1:] if r.get("note") is None]
    assert {(r["dir"], r["workload"], r["source"]) for r in obs} == {("write", "rand4k", "benchmark"), ("read", "rand4k", "benchmark")}
    assert {r["duration_s"] for r in obs} == {240, 120}   # only the >= 60 s sustained windows at the cap-basis rung
    assert all(r["plateau_bps"] > 0 for r in obs)
    # a quick run measured no >= 60 s window and appends nothing
    q = make_host(cache=INVERSION)
    calibrate(q, profile="quick")
    assert not q.exists("/var/lib/iobench/ceilings.jsonl") or len(q.read_text("/var/lib/iobench/ceilings.jsonl").splitlines()) == 1
    # a moved reference is appended as a reference_shift finding (15 s, exempt from the 60 s rule)
    flag = {"on": False}
    moved = make_host(cache=None, ceiling=lambda t: 0.5 if flag["on"] else 1.0, read=dict(iops_max=21600, lat_us=300))
    real_wr = moved.write_range

    def wr(argv, target, offset, length):
        flag["on"] = flag["on"] or any("rung-w32-4096" in a for a in argv)  # the ceiling halves during the ladder
        return real_wr(argv, target, offset, length)

    moved.write_range = wr
    calibrate(moved)
    shifts = [json.loads(x) for x in moved.read_text("/var/lib/iobench/ceilings.jsonl").splitlines()[1:] if "reference_shift" in x]
    assert shifts and all(s["duration_s"] == 15 and s["workload"] == "rand4k" for s in shifts)
