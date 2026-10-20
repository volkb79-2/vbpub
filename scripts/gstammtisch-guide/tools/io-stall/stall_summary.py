#!/usr/bin/env python3
"""Summarise the last N seconds of the stall recorder + fps log. Usage: stall_summary.py SECONDS [END_EPOCH]"""
import json, sys, time

N = int(sys.argv[1]); END = float(sys.argv[2]) if len(sys.argv) > 2 else time.time()
START = END - N
rec = [json.loads(l) for l in open("/var/log/mdt-stall/rec-20261020.jsonl") if l.startswith("{")]
rec = [r for r in rec if START <= r["t"] <= END]
fps = []
for l in open("/var/log/mdt-stall/fps-20261020.jsonl"):
    if l.startswith("{"):
        d = json.loads(l)
        if START <= d["epoch"] <= END:
            fps += [g["fps"] for g in d["games"] if g.get("fps") is not None]
if len(rec) < 2:
    print("no data"); sys.exit()
a, b = rec[0], rec[-1]; dt = b["t"] - a["t"]
def d(path):
    x, y = a, b
    for k in path:
        x, y = x[k], y[k]
    return (y - x) if (x is not None and y is not None) else None
# vda: reads0 rmerge1 rsect2 rticks3 writes4 wmerge5 wsect6 wticks7 inflight8 io_ticks9 tiq10
v = [b["vda"][i] - a["vda"][i] for i in range(11)]
print(f"window {dt:.0f}s  samples {len(rec)}")
print(f"vda r/s {v[0]/dt:.0f} w/s {v[4]/dt:.0f} rMB/s {v[2]*512/dt/1e6:.1f} wMB/s {v[6]*512/dt/1e6:.1f} "
      f"r_await {v[3]/max(v[0],1):.1f}ms w_await {v[7]/max(v[4],1):.1f}ms aqu {v[10]/dt/1000:.1f} max_inflight {max(sum(r['vda_inflight']) for r in rec)}")
for name in ("dev_io", "sys_io", "wings_io"):
    if a[name] and b[name]:
        print(f"{name}: rMB/s {(b[name]['rbytes']-a[name]['rbytes'])/dt/1e6:.1f} wMB/s {(b[name]['wbytes']-a[name]['wbytes'])/dt/1e6:.1f} "
              f"r/s {(b[name]['rios']-a[name]['rios'])/dt:.0f} w/s {(b[name]['wios']-a[name]['wios'])/dt:.0f}")
print("host PSI io full %.1f%%  mem full %.1f%%  | game PSI io some %.2f%% mem some %.2f%%" % (
    d(["psi_io", "full"]) / dt / 1e4, d(["psi_mem", "full"]) / dt / 1e4,
    d(["game_psi_io", "some"]) / dt / 1e4, d(["game_psi_mem", "some"]) / dt / 1e4))
print("game: majflt/s %.1f refault_anon/s %.1f refault_file/s %.1f zswpin/s %.1f pswpin/s %.1f | vm: allocstall %s pgscan_direct/s %.0f nr_dirty %s nr_writeback %s zswpwb/s %.1f" % (
    d(["game_mem", "pgmajfault"]) / dt, d(["game_mem", "workingset_refault_anon"]) / dt,
    d(["game_mem", "workingset_refault_file"]) / dt, (d(["game_mem", "zswpin"]) or 0) / dt,
    (d(["game_mem", "pswpin"]) or 0) / dt,
    (d(["vm", "allocstall_normal"]) or 0) + (d(["vm", "allocstall_movable"]) or 0),
    (d(["vm", "pgscan_direct"]) or 0) / dt, b["vm"]["nr_dirty"], b["vm"]["nr_writeback"], (d(["vm", "zswpwb"]) or 0) / dt))
for k in b["tmpfs"]:
    print(f"tmpfs {k}: swap {b['tmpfs'][k]['swap']/1e6:.0f}MB pswpin/s {((b['tmpfs'][k]['pswpin'] or 0)-(a['tmpfs'][k]['pswpin'] or 0))/dt:.1f}")
txp = [rec[i+1]["net"][3] - rec[i]["net"][3] for i in range(len(rec)-1)]
gaps = sum(1 for x in txp if x == 0)
print(f"game tx pkts/s median {sorted(txp)[len(txp)//2]} min {min(txp)}  zero-tx seconds {gaps}")
dmax = max((len(r["dthreads"]) for r in rec), default=0)
print(f"max game threads in D at a sample: {dmax}")
if fps:
    s = sorted(fps)
    print(f"fps n={len(s)} min {s[0]:.1f} p5 {s[len(s)//20]:.1f} median {s[len(s)//2]:.1f}  <25: {sum(1 for x in s if x < 25)}  <15: {sum(1 for x in s if x < 15)}")
