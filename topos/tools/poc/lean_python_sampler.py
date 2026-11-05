#!/usr/bin/env python3
"""Lean Python equivalent of the Rust hot sampler, for a like-for-like CPU/RSS comparison.

Same reads as the Rust PoC (cgroup tree files, slow-cadence limit files, /proc/stat,
meminfo, vmstat, loadavg, diskstats, per-PID stat/io/schedstat, bounded enriched union),
same rate and reset handling, same bottom-level device rule. Stdlib only, no pathlib,
os.open/os.read/os.close instead of open(): the cheapest idiomatic form. No tiers or
files: results go to an in-RAM ring of the last --ring ticks. Events, tiers, the socket
and zstd are NOT implemented here (that is the point of "lean").

usage: lean_python_sampler.py --duration 300 --stats out.json [--tick-ms 1000]
"""
import argparse
import heapq
import json
import os
import time
from collections import deque

CG_FILES = ("cpu.stat", "memory.current", "memory.stat", "memory.events", "io.stat",
            "cpu.pressure", "memory.pressure", "io.pressure", "pids.current")
SLOW_FILES = ("memory.min", "memory.low", "memory.high", "memory.max", "memory.swap.max",
              "cpu.max", "cpu.weight", "io.max", "io.weight")
MEMINFO = frozenset(b"MemTotal MemFree MemAvailable Buffers Cached SwapTotal SwapFree Dirty Writeback "
                    b"AnonPages Mapped Shmem Slab SReclaimable SUnreclaim Zswap Zswapped".split())
VMKEYS = frozenset(b"pgfault pgmajfault pswpin pswpout pgscan_direct pgscan_kswapd pgsteal_direct "
                   b"pgsteal_kswapd oom_kill zswpin zswpout zswpwb allocstall_normal pgpgin".split())
CPU_NAMES = ("user", "nice", "system", "idle", "iowait", "irq", "softirq", "steal")
IO_KEYS = (b"rbytes", b"wbytes", b"rios", b"wios", b"dbytes", b"dios")
RES = ("cpu", "memory", "io")


def slurp(path):
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return None
    try:
        return os.read(fd, 262144)
    except OSError:
        return None
    finally:
        os.close(fd)


class Sampler:
    def __init__(self, a):
        self.a = a
        self.cgs = {}      # rel -> [abs, prev, missing, serial, fresh, limits]
        self.hosts = {}    # name -> prev dict
        self.stacked = {}
        self.pmap = {}
        self.serial = 0
        self.resets = 0
        self.ring = deque(maxlen=a.ring)
        self.nameok = {}
        self.procs_out = []
        self.pids = 0
        self.denied = 0
        self.t_cg = self.t_host = self.t_proc = 0.0

    # ---- helpers
    def ctr(self, prev, out, name, cur, base_zero):
        p = prev.get(name)
        prev[name] = cur
        if p is None:
            if not base_zero:
                return
            p = 0
        if cur < p:
            self.resets += 1
            d = cur
        else:
            d = cur - p
        out[name] = d

    def is_bottom(self, dev):
        b = self.stacked.get(dev)
        if b is not None:
            return b
        try:
            names = os.listdir("%s/dev/block/%s/slaves" % (self.a.sys_root, dev.decode()))
        except OSError:
            return True
        b = not names
        self.stacked[dev] = b
        return b

    # ---- cgroups
    def cgroups(self, tick, slow_ticks, frame):
        root = self.a.cgroup_root
        stack = [(root, "")]
        seen = set()
        while stack:
            path, rel = stack.pop()
            try:
                with os.scandir(path) as it:
                    for e in it:
                        if e.is_dir(follow_symlinks=False):
                            stack.append((e.path, rel + "/" + e.name))
            except OSError:
                pass
            seen.add(rel)
            st = self.cgs.get(rel)
            if st is None:
                self.serial += 1
                st = self.cgs[rel] = [path, {}, 0, self.serial, True, {}, tick > 1]
            _, prev, missing, serial, fresh, limits, bz = st
            slow = fresh or (tick + serial) % slow_ticks == 0
            st[4] = False
            if slow:
                missing = 0
            out = {}
            for i in range(9):
                if (missing >> i) & 1:
                    continue
                b = slurp(path + "/" + CG_FILES[i])
                if b is None:
                    missing |= 1 << i
                    continue
                if i == 0:
                    for l in b.split(b"\n"):
                        k, _, v = l.partition(b" ")
                        if v:
                            self.ctr(prev, out, "cpu." + k.decode(), int(v), bz)
                elif i == 2:
                    for l in b.split(b"\n"):
                        k, _, v = l.partition(b" ")
                        if not v:
                            continue
                        ks = k.decode()
                        if ks.startswith(("pg", "thp_", "zswp", "workingset_")):
                            self.ctr(prev, out, "mem." + ks, int(v), bz)
                        else:
                            out["mem." + ks] = int(v)
                elif i == 3:
                    for l in b.split(b"\n"):
                        k, _, v = l.partition(b" ")
                        if v:
                            self.ctr(prev, out, "memev." + k.decode(), int(v), bz)
                elif i == 1:
                    out["mem.current"] = int(b)
                elif i == 8:
                    out["pids.current"] = int(b)
                elif i == 4:
                    tot = [0] * 6
                    vrate = None
                    for l in b.split(b"\n"):
                        parts = l.split()
                        if len(parts) < 2:
                            continue
                        bottom = self.is_bottom(parts[0])
                        for kv in parts[1:]:
                            k, _, v = kv.partition(b"=")
                            if k == b"cost.vrate":
                                x = float(v)
                                vrate = x if vrate is None or x > vrate else vrate
                            elif bottom and k in IO_KEYS:
                                tot[IO_KEYS.index(k)] += int(v)
                    for j in range(6):
                        self.ctr(prev, out, "io." + IO_KEYS[j].decode(), tot[j], bz)
                    if vrate is not None:
                        out["io.cost.vrate"] = vrate
                else:
                    r = RES[i - 5]
                    for l in b.split(b"\n"):
                        parts = l.split()
                        if not parts:
                            continue
                        kind = parts[0].decode()
                        for kv in parts[1:]:
                            k, _, v = kv.partition(b"=")
                            if k == b"avg10":
                                out["psi.%s.%s.avg10" % (r, kind)] = float(v)
                            elif k == b"total":
                                self.ctr(prev, out, "psi.%s.%s.total" % (r, kind), int(v), bz)
            st[2] = missing
            if slow:
                for j in range(9):
                    b = slurp(path + "/" + SLOW_FILES[j])
                    if b is None:
                        continue
                    t = b.strip()
                    if limits.get(j) != t:
                        limits[j] = t     # a limit change would raise an event in the Rust PoC
                    f = t.split()
                    if j == 5 and len(f) == 2:
                        out["limit.cpu.max.quota"] = -1 if f[0] == b"max" else int(f[0])
                        out["limit.cpu.max.period"] = int(f[1])
                    elif j == 7:
                        out["limit.io.max.capped"] = 1 if any(
                            kv.partition(b"=")[2] not in (b"max", b"") for l in t.split(b"\n") for kv in l.split()[1:]) else 0
                    elif j == 8:
                        if len(f) >= 2 and f[0] == b"default":
                            out["limit.io.weight"] = int(f[1])
                    elif f:
                        out["limit." + SLOW_FILES[j]] = -1 if f[0] == b"max" else int(f[0])
            if out:
                frame["cg:" + (rel or "/")] = out
        for rel in [r for r in self.cgs if r not in seen]:
            del self.cgs[rel]
        self.ncg = len(seen)

    # ---- host
    def host(self, tick, dt, frame):
        root = self.a.proc_root
        b = slurp(root + "/stat")
        host = frame.setdefault("host", {})
        hp = self.hosts.setdefault("host", {})
        if b:
            for l in b.split(b"\n"):
                f = l.split()
                if not f:
                    continue
                k = f[0]
                if k.startswith(b"cpu"):
                    name = "cpu:" + ("all" if k == b"cpu" else k[3:].decode())
                    prev = self.hosts.setdefault(name, {})
                    out = {}
                    for j in range(8):
                        self.ctr(prev, out, "cpu." + CPU_NAMES[j], int(f[1 + j]), False)
                    if len(out) == 8:
                        tot = sum(out.values())
                        out["cpu.busy_pct"] = (tot - out["cpu.idle"] - out["cpu.iowait"]) / tot * 100.0 if tot else 0.0
                    frame[name] = out
                elif k == b"ctxt":
                    self.ctr(hp, host, "host.ctxt", int(f[1]), False)
                elif k == b"processes":
                    self.ctr(hp, host, "host.forks", int(f[1]), False)
                elif k == b"procs_running":
                    host["host.procs_running"] = int(f[1])
                elif k == b"procs_blocked":
                    host["host.procs_blocked"] = int(f[1])
        b = slurp(root + "/meminfo")
        if b:
            for l in b.split(b"\n"):
                f = l.split()
                if len(f) >= 2 and f[0][:-1] in MEMINFO:
                    host["meminfo." + f[0][:-1].decode()] = int(f[1]) * 1024
        b = slurp(root + "/vmstat")
        if b:
            for l in b.split(b"\n"):
                k, _, v = l.partition(b" ")
                if k in VMKEYS:
                    self.ctr(hp, host, "vm." + k.decode(), int(v), False)
        b = slurp(root + "/loadavg")
        if b:
            f = b.split()
            host["host.load1"], host["host.load5"], host["host.load15"] = float(f[0]), float(f[1]), float(f[2])
        b = slurp(root + "/diskstats")
        if b:
            for l in b.split(b"\n"):
                f = l.split()
                if len(f) < 14:
                    continue
                dn = f[2]
                if dn.startswith((b"loop", b"ram")):
                    continue
                if int(f[3]) == 0 and int(f[7]) == 0:
                    continue
                name = "dev:" + dn.decode()
                prev = self.hosts.setdefault(name, {})
                out = {}
                for nm, fi in (("reads", 3), ("rd_sectors", 5), ("rd_ms", 6), ("writes", 7),
                               ("wr_sectors", 9), ("wr_ms", 10), ("io_ticks_ms", 12), ("time_in_queue_ms", 13)):
                    self.ctr(prev, out, "disk." + nm, int(f[fi]), False)
                out["disk.inflight"] = int(f[11])
                if "disk.reads" in out and len(out) == 9:
                    ops = out["disk.reads"] + out["disk.writes"]
                    out["disk.await_ms"] = (out["disk.rd_ms"] + out["disk.wr_ms"]) / ops if ops else 0.0
                    out["disk.util_pct"] = out["disk.io_ticks_ms"] / (dt * 1000.0) * 100.0
                frame[name] = out

    # ---- processes
    def procs(self, tick, dt):
        root = self.a.proc_root
        recs = []
        pmap = self.pmap
        seen = set()
        denied = 0
        try:
            it = os.scandir(root)
        except OSError:
            return
        with it:
            for e in it:
                n = e.name
                if not n.isdigit():
                    continue
                b = slurp("%s/%s/stat" % (root, n))
                if b is None:
                    continue
                cp = b.rfind(b")")
                f = b[cp + 2:].split()
                if len(f) < 22:
                    continue
                state = f[0]
                cpu = int(f[11]) + int(f[12])
                start = int(f[19])
                io = 0
                bi = slurp("%s/%s/io" % (root, n))
                if bi is None:
                    denied += 1
                else:
                    for l in bi.split(b"\n"):
                        if l.startswith((b"read_bytes:", b"write_bytes:")):
                            io += int(l.split()[1])
                slurp("%s/%s/schedstat" % (root, n))
                key = (int(n), start)
                p = pmap.get(key)
                if p is None:
                    cd = idl = 0
                else:
                    cd = cpu - p[0] if cpu >= p[0] else 0
                    idl = io - p[1] if io >= p[1] else 0
                pmap[key] = (cpu, io)
                seen.add(key)
                recs.append((key, state, cd, idl, int(f[21]), int(f[17]), b[b.find(b"(") + 1:cp]))
        if len(seen) != len(pmap):
            for k in [k for k in pmap if k not in seen]:
                del pmap[k]
        self.pids = len(recs)
        self.denied = denied
        cap = self.a.cap
        chosen = []
        mark = set()
        for r in recs:
            if r[1] == b"D" and len(chosen) < cap:
                mark.add(r[0])
                chosen.append((r, "D"))
        topc = heapq.nlargest(self.a.top_cpu, (r for r in recs if r[2] > 0), key=lambda r: r[2])
        topi = heapq.nlargest(self.a.top_io, (r for r in recs if r[3] > 0), key=lambda r: r[3])
        for k in range(max(len(topc), len(topi))):
            for lst, why in ((topc, "cpu"), (topi, "io")):
                if k < len(lst) and lst[k][0] not in mark and len(chosen) < cap:
                    mark.add(lst[k][0])
                    chosen.append((lst[k], why))
        out = []
        for r, why in chosen:
            cg = ""
            b = slurp("%s/%d/cgroup" % (root, r[0][0]))
            if b:
                for l in b.split(b"\n"):
                    if l.startswith(b"0::"):
                        cg = l[3:].decode()
                        break
            out.append((r[0][0], r[0][1], r[6].decode(errors="replace"), r[1].decode(), cg,
                        r[2] / dt, r[3] / dt, r[4] * 4096, r[5], why))
        self.procs_out = out

    def run(self):
        a = self.a
        slow_ticks = max(1, a.slow_every_s * 1000 // a.tick_ms)
        tick_s = a.tick_ms / 1000.0
        start = time.monotonic()
        nxt = start
        last = None
        tick = 0
        works = []
        pc = time.perf_counter
        while True:
            tick += 1
            t0 = time.monotonic()
            dt = (t0 - last) if last is not None else tick_s
            last = t0
            if tick % slow_ticks == 0:
                self.stacked.clear()
            w0 = pc()
            frame = {}
            self.cgroups(tick, slow_ticks, frame)
            w1 = pc()
            self.host(tick, dt, frame)
            w2 = pc()
            if a.scan:
                self.procs(tick, dt)
            w3 = pc()
            self.ring.append((time.time(), dt, frame, self.procs_out))
            self.t_cg += w1 - w0
            self.t_host += w2 - w1
            self.t_proc += w3 - w2
            works.append(w3 - w0)
            if a.duration and time.monotonic() - start >= a.duration:
                break
            nxt += tick_s
            d = nxt - time.monotonic()
            if d > 0:
                time.sleep(d)
            else:
                nxt = time.monotonic()
        wall = time.monotonic() - start
        self.finish(tick, wall, works)

    def finish(self, tick, wall, works):
        with open("/proc/self/stat") as f:
            fs = f.read().rsplit(")", 1)[1].split()
        ut, st = int(fs[11]), int(fs[12])
        status = open("/proc/self/status").read()
        kv = lambda k: int([l for l in status.splitlines() if l.startswith(k)][0].split()[1])
        io = open("/proc/self/io").read()
        iv = lambda k: int([l for l in io.splitlines() if l.startswith(k)][0].split()[1])
        ws = sorted(works)
        n = len(works)
        out = {
            "impl": "python", "ticks": tick, "wall_s": round(wall, 2), "cpu_ticks_user": ut, "cpu_ticks_sys": st,
            "cpu_pct_of_core": round((ut + st) / 100.0 / wall * 100.0, 3),
            "cpu_ms_per_tick": round((ut + st) * 10.0 / tick, 3),
            "work_ms_avg": round(sum(works) / n * 1000, 3), "work_ms_p50": round(ws[n // 2] * 1000, 3),
            "work_ms_p95": round(ws[min(n - 1, int(n * 0.95))] * 1000, 3), "work_ms_max": round(ws[-1] * 1000, 3),
            "ms_cgroups": round(self.t_cg / n * 1000, 3), "ms_host": round(self.t_host / n * 1000, 3),
            "ms_procs": round(self.t_proc / n * 1000, 3), "ms_store": 0,
            "vm_hwm_kb": kv("VmHWM:"), "vm_rss_kb": kv("VmRSS:"), "wchar": iv("wchar:"),
            "write_bytes_disk": iv("write_bytes:"), "syscw": iv("syscw:"),
            "cgroups": self.ncg, "pids": self.pids, "counter_resets": self.resets,
        }
        with open(self.a.stats, "w") as f:
            json.dump(out, f)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--duration", type=int, required=True)
    p.add_argument("--stats", required=True)
    p.add_argument("--tick-ms", type=int, default=1000)
    p.add_argument("--slow-every-s", type=int, default=60)
    p.add_argument("--ring", type=int, default=60)
    p.add_argument("--cgroup-root", default="/sys/fs/cgroup")
    p.add_argument("--proc-root", default="/proc")
    p.add_argument("--sys-root", default="/sys")
    p.add_argument("--top-cpu", type=int, default=20)
    p.add_argument("--top-io", type=int, default=20)
    p.add_argument("--cap", type=int, default=64)
    p.add_argument("--no-scan", dest="scan", action="store_false")
    Sampler(p.parse_args()).run()


if __name__ == "__main__":
    main()
