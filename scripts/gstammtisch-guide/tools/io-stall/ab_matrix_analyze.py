#!/usr/bin/env python3
"""Per-cell table for ab_matrix.sh: probe latency, game D-state stalls, fps, device throughput, fio (gate-like) cost.
Usage: ab_matrix_analyze.py [SINCE_EPOCH]  (default: the last 'MATRIX ORIG' line)"""
import datetime, json, re, sys

D = "/var/log/mdt-stall"
lines = open(f"{D}/ab-phases.log").read().splitlines()
since = int(sys.argv[1]) if len(sys.argv) > 1 else max(
    int(l.split()[1]) for l in lines if " MATRIX ORIG " in l)
cells, order = {}, []
for l in lines:
    m = re.match(r"\S+ (\d+) PHASE (\S+) (START|END)", l)
    if m and int(m.group(1)) >= since:
        if m.group(3) == "START":
            order.append(m.group(2))
        cells.setdefault(m.group(2), {})[m.group(3)] = int(m.group(1))


def pct(v, p):
    v = sorted(v)
    return v[min(len(v) - 1, int(len(v) * p))] if v else float("nan")


probe = [json.loads(l) for l in open(f"{D}/ab-probe.jsonl") if l.startswith("{")]
rec = [json.loads(l) for l in open(f"{D}/rec-20261020.jsonl") if l.startswith("{")]
fps = []
for l in open(f"{D}/fps-20261020.jsonl"):
    if l.startswith("{"):
        d = json.loads(l)
        fps += [(d["epoch"], g["fps"]) for g in d["games"] if g.get("fps") is not None]
dwin, cur = [], None
for l in open(f"{D}/dstate-20261020.log"):
    if l.startswith("=== "):
        cur = datetime.datetime.strptime(l[4:].strip(), "%Y-%m-%dT%H:%M:%S").timestamp()
    elif l.startswith("@maxms:") and cur:
        dwin.append((cur, int(l.split()[1])))


def fio(name):
    try:
        txt = open(f"{D}/ab-fio-{name}.json").read()
        j = json.loads(txt[txt.index("{"):])["jobs"][0]
    except (OSError, ValueError, KeyError, IndexError) as exc:
        return f"fio n/a ({type(exc).__name__})"
    w = j["write"]
    sync = j.get("sync", {}).get("lat_ns", {}).get("percentile", {})
    s99 = sync.get("99.000000")
    return f"{w['bw_bytes'] / 1e6:5.1f}MB/s {w['iops']:6.0f}iops fsync p99 " + (f"{s99 / 1e6:7.1f}ms" if s99 else "n/a")


print(f"{'cell':20} {'n':>4} {'write p99/max ms':>17} {'fdatasync p50/p99/max ms':>26} {'D max':>6} {'D>1s':>4} "
      f"{'fps min/med':>11} {'vda wMB/s':>9} {'w_await':>8} {'aqu':>5}  fio (gate-like load)")
for name in order:
    c = cells[name]
    if "END" not in c:
        print(f"{name:20} (no END)")
        continue
    s, e = c["START"], c["END"]
    pr = [x for x in probe if s <= x["t"] <= e]
    w = [x["w_ms"] for x in pr]; sy = [x["s_ms"] for x in pr]
    dm = [m for t, m in dwin if s < t <= e + 10]
    f = [v for t, v in fps if s <= t <= e]
    rr = [r for r in rec if s <= r["t"] <= e]
    if len(rr) >= 2:
        a, b = rr[0], rr[-1]; dt = b["t"] - a["t"]
        v = [b["vda"][i] - a["vda"][i] for i in range(11)]
        dev = f"{v[6] * 512 / dt / 1e6:9.1f} {v[7] / max(v[4], 1):7.0f}ms {v[10] / dt / 1000:5.1f}"
    else:
        dev = f"{'n/a':>9} {'n/a':>9} {'n/a':>5}"
    print(f"{name:20} {len(sy):4d} {pct(w, .99):7.1f}/{max(w or [0]):8.1f}  {pct(sy, .5):7.1f}/{pct(sy, .99):8.1f}/{max(sy or [0]):8.1f} "
          f"{max(dm or [0]):6d} {sum(1 for m in dm if m >= 1000):4d} {min(f or [0]):5.1f}/{pct(f, .5):5.1f} {dev}  {fio(name)}")
