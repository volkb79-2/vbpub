#!/usr/bin/env python3
"""iomon_bpf - in-container supervisor for the eBPF part of iomon (stdlib only).

Runs inside the collector container (root, --pid=host, host cgroup tree at /hostcg:ro,
host tracefs/debugfs/btf read-only).  It
  * resolves each watched container ID to its cgroup scope and kernfs id,
  * runs blk.bt and stall.bt (bpftrace, in-kernel aggregation, JSON every 30 s),
  * samples cgroup files the devcontainer cannot see (io.stat incl. iocost cost.*,
    io.pressure, memory.pressure/events/stat, cpu.stat, io.max/io.weight, io.cost.*),
  * appends everything as JSONL to OUT/bpf-*.jsonl (size-capped) and stops on OUT/STOP.
Use scripts/iomon-bpf/run.sh to start it from the devcontainer.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import signal
import subprocess
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from iomon import Writer  # noqa: E402

HOSTCG = "/hostcg"
BT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "iomon-bpf")
MEM_STAT_KEYS = ("anon", "file", "file_dirty", "file_writeback", "pgmajfault", "pgscan", "pgsteal",
                 "workingset_refault_anon", "workingset_refault_file", "zswpin", "zswpout", "zswpwb",
                 "swapcached", "pswpin", "pswpout")


def resolve_scope(ident):
    """Container ID (>= 12 hex chars) -> (scope dir, kernfs id) or (None, 0)."""
    for pat in (f"{HOSTCG}/*/docker-{ident}*.scope", f"{HOSTCG}/*/*/docker-{ident}*.scope"):
        for p in glob.glob(pat):
            return p, os.stat(p).st_ino
    return None, 0


def read(path):
    try:
        with open(path) as f:
            return f.read()
    except OSError:
        return None


def kv_lines(text):
    out = {}
    for line in (text or "").splitlines():
        p = line.split()
        if len(p) >= 2 and "=" not in p[0]:
            try:
                out[p[0]] = int(p[1])
            except ValueError:
                pass
    return out


def parse_io_stat(text):
    """'254:0 rbytes=.. wios=.. cost.usage=..' lines -> {dev: {key: number}}."""
    out = {}
    for line in (text or "").splitlines():
        p = line.split()
        if len(p) < 2 or ":" not in p[0]:
            continue
        d = {}
        for kv in p[1:]:
            if "=" in kv:
                k, v = kv.split("=", 1)
                try:
                    d[k] = float(v) if "." in v else int(v)
                except ValueError:
                    d[k] = v
        out[p[0]] = d
    return out


def parse_psi(text):
    out = {}
    for line in (text or "").splitlines():
        p = line.split()
        if p:
            kv = dict(x.split("=", 1) for x in p[1:])
            out[p[0]] = {"a10": float(kv["avg10"]), "tot": int(kv["total"])}
    return out


def sample_cg(path):
    r = {"io": parse_io_stat(read(f"{path}/io.stat"))}
    for k in ("io", "memory", "cpu"):
        r[f"{k}_psi"] = parse_psi(read(f"{path}/{k}.pressure"))
    mem = kv_lines(read(f"{path}/memory.stat"))
    r["mem_stat"] = {k: mem[k] for k in MEM_STAT_KEYS if k in mem}
    r["mem_events"] = kv_lines(read(f"{path}/memory.events"))
    for f in ("memory.current", "memory.swap.current", "memory.high", "memory.max", "memory.low", "memory.min"):
        v = (read(f"{path}/{f}") or "").strip()
        if v:
            r[f.replace("memory.", "m_")] = v if v == "max" else int(v)
    r["cpu_stat"] = kv_lines(read(f"{path}/cpu.stat"))
    for f in ("io.max", "io.weight"):
        v = read(f"{path}/{f}")
        if v and v.strip():
            r[f.replace(".", "_")] = v.strip().replace("\n", " ; ")
    return r


def cg_snapshot(scopes):
    snap = {"root": {"io": parse_io_stat(read(f"{HOSTCG}/io.stat")),
                     "io_psi": parse_psi(read(f"{HOSTCG}/io.pressure")),
                     "mem_psi": parse_psi(read(f"{HOSTCG}/memory.pressure")),
                     "cost_qos": (read(f"{HOSTCG}/io.cost.qos") or "").strip(),
                     "cost_model": (read(f"{HOSTCG}/io.cost.model") or "").strip()}}
    for name, path in scopes.items():
        if path and os.path.isdir(path):
            snap[name] = sample_cg(path)
        else:
            snap[name] = None
    return snap


def cgmap(max_depth=4, max_dirs=3000):
    """kernfs id (= inode) -> cgroup path for the first max_dirs cgroups down to max_depth."""
    m, n = {1: "/"}, 0
    base_depth = HOSTCG.rstrip("/").count("/")
    for root, dirs, _ in os.walk(HOSTCG):
        depth = root.count("/") - base_depth
        if depth >= max_depth:
            dirs[:] = []
        for d in dirs:
            p = os.path.join(root, d)
            try:
                m[os.stat(p).st_ino] = os.path.relpath(p, HOSTCG)
            except OSError:
                continue
            n += 1
            if n >= max_dirs:
                return m
    return m


class Bpf:
    def __init__(self, name, script, ids, w, log):
        self.name, self.script, self.ids, self.w, self.log = name, script, ids, w, log
        self.p = subprocess.Popen(
            ["bpftrace", "-f", "json", os.path.join(BT_DIR, script), str(ids[0]), str(ids[1])],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)
        self.helper_errors = 0
        for fn in (self._out, self._err):
            threading.Thread(target=fn, daemon=True).start()

    def _out(self):
        for line in self.p.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                o = json.loads(line)
            except ValueError:
                o = {"type": "raw", "line": line[:500]}
            if o.get("type") == "helper_error":
                self.helper_errors += 1
                continue
            if o.get("type") == "printf":
                try:
                    o = json.loads(o["data"])
                except ValueError:
                    pass
            self.w.write({"k": "bpf", "t": round(time.time(), 3), "src": self.name, "o": o})

    def _err(self):
        for line in self.p.stderr:
            self.log(f"bpftrace[{self.name}] {line.rstrip()[:300]}")

    def cpu_ticks(self):
        try:
            f = open(f"/proc/{self.p.pid}/stat").read().rsplit(")", 1)[1].split()
            return int(f[11]) + int(f[12])
        except (OSError, IndexError, ValueError):
            return 0

    def stop(self):
        if self.p.poll() is None:
            self.p.send_signal(signal.SIGINT)
            try:
                self.p.wait(10)
            except subprocess.TimeoutExpired:
                self.p.kill()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="/out")
    ap.add_argument("--container", action="append", required=True, help="container ID (>=12 hex)")
    ap.add_argument("--max-hours", type=float, default=8.0)
    ap.add_argument("--cap-mb", type=float, default=80.0)
    ap.add_argument("--interval", type=float, default=5.0)
    a = ap.parse_args()
    if len(a.container) > 2:
        sys.exit("at most two watched containers (bpftrace params $1/$2)")
    stopf = os.path.join(a.out, "STOP-BPF")
    legacy_stop = os.path.join(a.out, "STOP")
    w = Writer(a.out, int(a.cap_mb * 2**20), prefix="bpf-")
    logf = open(os.path.join(a.out, "iomon-bpf.log"), "a", buffering=1)

    def log(msg):
        logf.write(time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()) + "Z " + msg + "\n")

    stop = threading.Event()
    for s in (signal.SIGTERM, signal.SIGINT):
        signal.signal(s, lambda *_: stop.set())
    idents = list(a.container)

    def resolve():
        scopes, ids = {}, []
        for i in idents:
            p, ino = resolve_scope(i)
            scopes[i[:12]] = p
            ids.append(ino)
        while len(ids) < 2:
            ids.append(0)
        for extra in ("wings.slice", "dev.slice", "dev-background.slice", "dev-interactive.slice"):
            scopes[extra] = f"{HOSTCG}/{extra}" if os.path.isdir(f"{HOSTCG}/{extra}") else None
        return scopes, ids

    scopes, ids = resolve()
    log(f"start containers={idents} cgroup_ids={ids} scopes={scopes}")
    w.write({"k": "bpf_meta", "t": round(time.time(), 3), "containers": [i[:12] for i in idents],
             "cgroup_ids": ids, "scopes": scopes})
    procs = [Bpf("blk", "blk.bt", ids, w, log), Bpf("stall", "stall.bt", ids, w, log)]
    end = time.monotonic() + a.max_hours * 3600
    last_map = last_res = 0.0
    last_cpu, last_wall = sum(p.cpu_ticks() for p in procs), time.monotonic()
    tick = os.sysconf("SC_CLK_TCK")
    try:
        while not stop.is_set():
            if os.path.exists(stopf) or os.path.exists(legacy_stop):
                log("STOP marker")
                break
            if time.monotonic() > end:
                log("max runtime")
                break
            now = time.monotonic()
            if now - last_map > 300:
                w.write({"k": "cgmap", "t": round(time.time(), 3), "ids": {v: k for k, v in cgmap().items()}})
                last_map = now
            if now - last_res > 30:
                last_res = now
                ns, nids = resolve()
                if nids != ids or any(p.p.poll() is not None for p in procs):
                    log(f"restart bpftrace: ids {ids}->{nids} alive={[p.p.poll() is None for p in procs]}")
                    for p in procs:
                        p.stop()
                    scopes, ids = ns, nids
                    w.write({"k": "bpf_meta", "t": round(time.time(), 3), "cgroup_ids": ids, "scopes": scopes})
                    procs = [Bpf("blk", "blk.bt", ids, w, log), Bpf("stall", "stall.bt", ids, w, log)]
            rec = {"k": "cg", "t": round(time.time(), 3), "cg": cg_snapshot(scopes)}
            if int(rec["t"]) % 60 < a.interval:
                cpu = sum(p.cpu_ticks() for p in procs)
                wall = time.monotonic()
                rec["bpftrace_cpu_pct"] = round((cpu - last_cpu) / tick / max(wall - last_wall, 1e-9) * 100, 3)
                rec["helper_errors"] = sum(p.helper_errors for p in procs)
                last_cpu, last_wall = cpu, wall
            w.write(rec)
            stop.wait(a.interval)
    finally:
        for p in procs:
            p.stop()
        w.close()
        for f in (stopf, legacy_stop):
            try:
                os.unlink(f)
            except OSError:
                pass
        log("stopped")


if __name__ == "__main__":
    main()
