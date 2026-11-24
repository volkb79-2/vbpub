# Usage: python3 -I stats.py <extracted-root> > stats.md   (<extracted-root> = the unpacked accept2-files.tgz)
# This is the script that ../ACCEPT2-STAGE-2026-11-19.md cites; stats.md beside it is its exact output.
"""Per-phase p50/p95/p99 for the iogovmon acceptance-2 fill stage (r1002, 2026-11-18/19).

usage: python3 -I stats.py <extracted-root> > stats.md
Sources: var/lib/iogovmon/journal/journal-2026-11.jsonl (probe + bench records),
root/iogovmon-accept2/stage-phases.json (phase windows), root/iogovmon-accept2/refprobe/*.csv.
"""
import csv, glob, json, os, sys
from collections import defaultdict

root = sys.argv[1]
acc = os.path.join(root, "root/iogovmon-accept2")
phases = json.load(open(os.path.join(acc, "stage-phases.json")))
STAGE_END = max(p.get("end") or p["start"] for p in phases)
phases.append({"name": "post-stage (recommend-only, to 11-24)", "start": STAGE_END + 1, "end": 1e12})


def phase_of(t):
    for p in phases:
        if p["start"] <= t < (p.get("end") or 1e12):
            return p["name"]
    return None


def pct(xs, q):
    if not xs:
        return None
    xs = sorted(xs)
    k = (len(xs) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def fmt(v, nd=0):
    return "-" if v is None else (f"{v:.{nd}f}")


lat = defaultdict(lambda: defaultdict(list))      # phase -> lane -> samples (us)
rate = defaultdict(lambda: defaultdict(list))     # phase -> lane -> MiB/s
nz = defaultdict(lambda: [0, 0])                  # phase -> [nonzero n, attempts] (r_cold)
for line in open(os.path.join(root, "var/lib/iogovmon/journal/journal-2026-11.jsonl")):
    r = json.loads(line)
    ph = phase_of(r.get("t", 0))
    if ph is None:
        continue
    if r.get("type") == "probe" and r.get("lane") in ("r_cold", "r_witness"):
        lat[ph][r["lane"]].extend(r.get("lat_us") or [])
        if r["lane"] == "r_cold":
            nz[ph][0] += r.get("n") or 0
            nz[ph][1] += r.get("attempts") or 0
    lanes = r.get("lanes")
    if isinstance(lanes, dict):
        for name, d in lanes.items():
            if not isinstance(d, dict) or not d.get("valid"):
                continue
            if "rate_bps" in d:
                rate[ph][name].append(d["rate_bps"] / 2**20)
            if "rate_write_bps" in d:
                rate[ph][name].append(d["rate_write_bps"] / 2**20)
            if isinstance(d.get("lat"), dict):
                lat[ph][name].extend(d["lat"].get("lat_us") or [])

ref = defaultdict(lambda: defaultdict(list))
for f in sorted(glob.glob(os.path.join(acc, "refprobe/*.csv"))):
    with open(f) as fh:
        for row in csv.DictReader(fh):
            if row["errno"] != "0":
                continue
            ph = phase_of(int(row["ts_utc_ns"]) / 1e9)
            if ph:
                ref[ph][f"{row['fs']}/{row['op']}"].append(int(row["latency_us"]))

order = [p["name"] for p in phases]
print("## Latency per phase (us): pooled per-sample values; n = samples\n")
lanes = sorted({ln for ph in lat for ln in lat[ph]})
for ln in lanes:
    print(f"### {ln}\n\n| phase | n | p50 | p95 | p99 |\n|---|---|---|---|---|")
    for ph in order:
        xs = lat[ph].get(ln)
        if xs:
            print(f"| {ph} | {len(xs)} | {fmt(pct(xs,.5))} | {fmt(pct(xs,.95))} | {fmt(pct(xs,.99))} |")
    print()
print("## Bench throughput per phase (MiB/s, one value per valid bench run)\n")
for ln in sorted({ln for ph in rate for ln in rate[ph]}):
    print(f"### {ln}\n\n| phase | runs | p50 | p95 | p99 |\n|---|---|---|---|---|")
    for ph in order:
        xs = rate[ph].get(ln)
        if xs:
            print(f"| {ph} | {len(xs)} | {fmt(pct(xs,.5),1)} | {fmt(pct(xs,.95),1)} | {fmt(pct(xs,.99),1)} |")
    print()
print("## r_cold non-zero block yield per phase\n\n| phase | non-zero | attempts | fraction |\n|---|---|---|---|")
for ph in order:
    n, a = nz[ph]
    if a:
        print(f"| {ph} | {n} | {a} | {100*n/a:.1f}% |")
print("\n## Reference probe (refprobe CSV, us)\n")
for k in sorted({k for ph in ref for k in ref[ph]}):
    print(f"### {k}\n\n| phase | n | p50 | p95 | p99 |\n|---|---|---|---|---|")
    for ph in order:
        xs = ref[ph].get(k)
        if xs:
            print(f"| {ph} | {len(xs)} | {fmt(pct(xs,.5))} | {fmt(pct(xs,.95))} | {fmt(pct(xs,.99))} |")
    print()
