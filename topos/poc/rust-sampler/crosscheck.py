#!/usr/bin/env python3
"""Hand cross-check of three sampler values against kernel files read independently.

usage: crosscheck.py DIR COLD_DIR [CGROUP_ENTITY_FOR_IO]
Starts the sampler (DIR/topos-sampler-poc, DIR/sampler.example.toml) for ~35 s, burns exactly one
core inside its own cgroup for 12 s, and compares:
  A. cgroup CPU rate: sampler series (hot) vs cpu.stat usage_usec read twice from the shell side
  B. io.stat total on the bottom-level rule: sampler raw vs an independent sum, bracketed by
     two independent reads (counters are monotonic: before <= sampler raw <= after)
  C. host per-CPU value: sampler cpu:N raw jiffies bracketed the same way, and busy_pct vs
     two /proc/stat reads
"""
import json, os, socket, subprocess, sys, time

D, COLD = sys.argv[1], sys.argv[2]
IO_CG = sys.argv[3] if len(sys.argv) > 3 else "/"
R = "%s/xc-%d" % (D, os.getpid())
os.makedirs(R + "/tmpfs"); os.makedirs(COLD, exist_ok=True)
sock = "%s/s.sock" % R
cfg = open(D + "/sampler.example.toml").read()
for k, v in (("socket_path", sock), ("stats_path", R + "/stats.json"), ("events_path", COLD + "/xc-events.jsonl"),
             ("tmpfs_dir", R + "/tmpfs"), ("cold_dir", COLD)):
    cfg = "\n".join('%s = "%s"' % (k, v) if l.startswith(k + " =") else l for l in cfg.split("\n"))
cfg = cfg.replace("duration_s = 300", "duration_s = 36")
open(R + "/c.toml", "w").write(cfg)
sampler = subprocess.Popen([D + "/topos-sampler-poc", R + "/c.toml"])
for _ in range(50):
    if os.path.exists(sock):
        break
    time.sleep(0.1)
s = socket.socket(socket.AF_UNIX); s.connect(sock); f = s.makefile("rw")


def q(o):
    f.write(json.dumps(o) + "\n"); f.flush(); return json.loads(f.readline())


own = [l for l in open("/proc/self/cgroup").read().split("\n") if l.startswith("0::")][0][3:]
cpustat = "/sys/fs/cgroup%s/cpu.stat" % own
ent = "cg:" + own


def usage():
    for l in open(cpustat):
        if l.startswith("usage_usec"):
            return int(l.split()[1])


def stacked(dev):
    try:
        return bool(os.listdir("/sys/dev/block/%s/slaves" % dev))
    except OSError:
        return False


def io_total(cg):
    path = "/sys/fs/cgroup%s/io.stat" % (cg if cg != "/" else "")
    t = {"rbytes": 0, "wbytes": 0}; excl = []
    for l in open(path):
        p = l.split()
        if len(p) < 2:
            continue
        if stacked(p[0]):
            excl.append(p[0]); continue
        for kv in p[1:]:
            k, _, v = kv.partition("=")
            if k in t:
                t[k] += int(v)
    return t, excl


def cpu_line(n):
    for l in open("/proc/stat"):
        p = l.split()
        if p[0] == "cpu%d" % n:
            return [int(x) for x in p[1:9]]


time.sleep(4)
burner = subprocess.Popen([sys.executable, "-c", "while True: pass"])
time.sleep(1)
# ---- A and C-rate: 10 s window
w1 = time.time(); m1 = time.monotonic(); u1 = usage(); c1 = cpu_line(0); a1 = [cpu_line(i) for i in range(1)]
time.sleep(10)
w2 = time.time(); m2 = time.monotonic(); u2 = usage(); c2 = cpu_line(0)
burner.kill(); burner.wait()
shell_rate = (u2 - u1) / (m2 - m1)
ser = q({"req": "series", "entity": ent, "metric": "cpu.usage_usec", "tier": "hot", "window_s": 15})
pts = [p for p in ser["points"] if w1 * 1000 <= p[0] <= w2 * 1000]
sd, sdt = sum(p[1] for p in pts), sum(p[2] for p in pts)
print("A cgroup %s cpu.usage_usec: shell %.0f usec/s over %.3fs ; sampler %.0f usec/s over %d ticks (dt %.3fs) ; diff %.2f%%" %
      (own, shell_rate, m2 - m1, sd / (sdt / 1000.0), len(pts), sdt / 1000.0, (sd / (sdt / 1000.0) / shell_rate - 1) * 100))
# C busy pct
sh_tot = sum(b - a for a, b in zip(c1, c2)); sh_busy = (sh_tot - (c2[3] - c1[3]) - (c2[4] - c1[4])) / sh_tot * 100
bp = q({"req": "series", "entity": "cpu:0", "metric": "cpu.busy_pct", "tier": "hot", "window_s": 15})
bpts = [p[1] for p in bp["points"] if w1 * 1000 <= p[0] <= w2 * 1000]
print("C cpu:0 busy%%: shell %.2f%% (jiffies delta %d) ; sampler mean of %d ticks %.2f%%" % (sh_busy, sh_tot, len(bpts), sum(bpts) / len(bpts)))
# ---- bracket checks (need a tick strictly between the reads)
br = {}
b_cpu1 = cpu_line(0); b_io1, excl = io_total(IO_CG)
time.sleep(1.6)
cur = q({"req": "current"})
a_cpu = cpu_line(0); a_io, _ = io_total(IO_CG)
e = cur["entities"]
raw_user = e["cpu:0"]["cpu.user"]["raw"]; raw_sys = e["cpu:0"]["cpu.system"]["raw"]
print("C cpu:0 raw jiffies: user %d <= sampler %d <= %d ; system %d <= %d <= %d  -> %s" % (
    b_cpu1[0], raw_user, a_cpu[0], b_cpu1[2], raw_sys, a_cpu[2],
    "OK" if b_cpu1[0] <= raw_user <= a_cpu[0] and b_cpu1[2] <= raw_sys <= a_cpu[2] else "MISMATCH"))
key = "cg:" + (IO_CG if IO_CG != "/" else "/")
if key in e and "io.rbytes" in e[key]:
    sr, sw = e[key]["io.rbytes"]["raw"], e[key]["io.wbytes"]["raw"]
    print("B io.stat %s excluded-stacked-devs=%s: rbytes %d <= sampler %d <= %d ; wbytes %d <= %d <= %d -> %s" % (
        IO_CG, excl, b_io1["rbytes"], sr, a_io["rbytes"], b_io1["wbytes"], sw, a_io["wbytes"],
        "OK" if b_io1["rbytes"] <= sr <= a_io["rbytes"] and b_io1["wbytes"] <= sw <= a_io["wbytes"] else "MISMATCH"))
else:
    print("B no io.stat values for", key)
sampler.wait()
import shutil; shutil.rmtree(R, ignore_errors=True)
for fn in os.listdir(COLD):
    os.remove(os.path.join(COLD, fn))
