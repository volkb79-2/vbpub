#!/usr/bin/env python3
"""Per-phase table for the A/B runner: probe latency, game D-state stalls, fps, device queue."""
import json, re, subprocess, datetime

D = "/var/log/mdt-stall"
phases = {}
for line in open(f"{D}/ab-phases.log"):
    m = re.match(r"\S+ (\d+) (?:PHASE (\S+) (START|END)|(BASELINE) (START|END))", line)
    if m:
        name = m.group(2) or m.group(4); edge = m.group(3) or m.group(5)
        phases.setdefault(name, {})[edge] = int(m.group(1))


def pct(v, p):
    v = sorted(v)
    return v[min(len(v) - 1, int(len(v) * p))] if v else float("nan")


probe = [json.loads(l) for l in open(f"{D}/ab-probe.jsonl")]
fps = []
for l in open(f"{D}/fps-20261020.jsonl"):
    if l.startswith("{"):
        d = json.loads(l)
        fps += [(d["epoch"], g["fps"]) for g in d["games"] if g.get("fps") is not None]
# D-state log: "=== %Y-%m-%dT%H:%M:%S" (local time) then "@maxms: N"
dwin = []
cur = None
for l in open(f"{D}/dstate-20261020.log"):
    if l.startswith("=== "):
        cur = datetime.datetime.strptime(l[4:].strip(), "%Y-%m-%dT%H:%M:%S").timestamp()
    elif l.startswith("@maxms:") and cur:
        dwin.append((cur, int(l.split()[1])))

print(f"{'phase':9} {'probe w p99/max ms':>20} {'fdatasync p50/p99/max ms':>27} {'game D max ms':>13} {'D>1s win':>8} {'fps min/p5':>11}")
for name in ("BASELINE", "A0", "A1", "A2", "A3", "A4"):
    p = phases.get(name, {})
    if "START" not in p:
        continue
    s, e = p["START"], p.get("END", 10**12)
    pr = [x for x in probe if s <= x["t"] <= e]
    w = [x["w_ms"] for x in pr]; sy = [x["s_ms"] for x in pr]
    dm = [m for t, m in dwin if s <= t <= e + 10]
    f = [v for t, v in fps if s <= t <= e]
    print(f"{name:9} {pct(w,.99):9.1f}/{max(w or [0]):8.1f}  {pct(sy,.5):8.1f}/{pct(sy,.99):8.1f}/{max(sy or [0]):8.1f} "
          f"{max(dm or [0]):13d} {sum(1 for m in dm if m >= 1000):8d} {min(f or [0]):5.1f}/{pct(f,.05):5.1f}")
    if "END" in p:
        out = subprocess.run(["python3", f"{D}/stall_summary.py", str(e - s), str(e)], capture_output=True, text=True).stdout
        for line in out.splitlines():
            if line.startswith(("vda", "dev_io", "host PSI")):
                print("          " + line)
