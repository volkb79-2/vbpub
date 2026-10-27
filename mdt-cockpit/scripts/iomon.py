#!/usr/bin/env python3
"""iomon - lightweight, read-mostly IO/latency monitor for a shared Linux host.

Stdlib only.  Samples cheap kernel counters every few seconds and appends
JSONL (one "s" object per sample, one "m" object per wall-clock minute).

  iomon.py run     --out DIR --container ID [--container ID ...]   (sampler)
  iomon.py analyze --out DIR [--first 10m | --last 30m | --since T --until T]
  iomon.py stop    --out DIR                                       (touch STOP)

Sources (all read-only): /proc/diskstats, /proc/pressure/*, /proc/meminfo,
/proc/vmstat, /proc/loadavg, /proc/stat, /sys/block/<dev>/queue/*, the
caller's own cgroup PSI, and the Docker Engine stats endpoint for each
container given by exact ID or name (stats only; nothing is ever started,
stopped, exec'd or modified).  An optional active probe does one 4 KiB
O_DSYNC write plus one 4 KiB direct read every N seconds against a tiny
preallocated file (1 MiB) in --probe-dir; its IO is ~0.8 KiB/s.

Stop: create the file <out>/STOP (or send SIGTERM); the PID is in <out>/iomon.pid.
`iomon.py stop --out DIR` also stops the optional eBPF collector when DIR/bpf points at its
output directory.

Optional eBPF collector (scripts/iomon-bpf/): run.sh starts ONE fixed-name, --rm container
(root, dev background slice, host cgroup tree/tracefs read-only, one writable output bind)
running iomon_bpf.py, which runs blk.bt / stall.bt (bpftrace, in-kernel aggregation) and
samples the host cgroup files.  Its bpf-*.jsonl is merged into `analyze` automatically when
it sits in OUT or OUT/bpf.  Build the image with scripts/iomon-bpf/Dockerfile.
"""
from __future__ import annotations

import argparse
import ctypes
import datetime as dt
import http.client
import json
import logging
import logging.handlers
import mmap
import os
import queue
import re
import signal
import socket
import statistics
import sys
import threading
import time

SECTOR = 512
PSI_KINDS = ("io", "memory", "cpu")
DEFAULT_DEVICES = ("vda", "dm-0", "dm-1", "dm-2")
SEGMENT_BYTES = 25 * 1024 * 1024


# --------------------------------------------------------------------------
# Pure computations (unit tested)
# --------------------------------------------------------------------------
def parse_diskstats(text, wanted=None):
    """-> {name: [int fields after the name]} for the wanted devices."""
    out = {}
    for line in text.splitlines():
        p = line.split()
        if len(p) < 14:
            continue
        name = p[2]
        if wanted is not None and name not in wanted:
            continue
        out[name] = [int(x) for x in p[3:]]
    return out


def _f(v, i):
    return v[i] if i < len(v) else 0


def disk_delta(prev, cur, dt_s):
    """Per-interval device metrics from two diskstats field lists.

    Field order (after name): 0 reads, 1 rd_merged, 2 rd_sectors, 3 rd_ms,
    4 writes, 5 wr_merged, 6 wr_sectors, 7 wr_ms, 8 in_flight, 9 io_ticks_ms,
    10 time_in_queue_ms, 11.. discards(4), 15.. flush(2).
    await is None when no IO of that kind completed in the interval.
    """
    d = lambda i: _f(cur, i) - _f(prev, i)
    if dt_s <= 0 or d(0) < 0 or d(4) < 0 or d(9) < 0:
        return None  # counter reset / bad interval
    rio, wio = d(0), d(4)
    fio = d(15)
    return {
        "ri": round(rio / dt_s, 2),
        "wi": round(wio / dt_s, 2),
        "rmb": round(d(2) * SECTOR / dt_s / 1e6, 3),
        "wmb": round(d(6) * SECTOR / dt_s / 1e6, 3),
        "ra": round(d(3) / rio, 3) if rio > 0 else None,
        "wa": round(d(7) / wio, 3) if wio > 0 else None,
        "util": round(min(100.0, d(9) / (dt_s * 10.0)), 2),
        "q": round(d(10) / (dt_s * 1000.0), 3),  # average queue size (iostat aqu-sz)
        "inf": _f(cur, 8),
        "fl": round(d(16) / fio, 3) if fio > 0 else None,  # avg flush ms
    }


def parse_psi(text):
    """'some avg10=.. total=..' lines -> {'some': {'a10','a60','tot'}, 'full': ...}."""
    out = {}
    for line in text.splitlines():
        p = line.split()
        if not p:
            continue
        kv = dict(x.split("=", 1) for x in p[1:])
        out[p[0]] = {"a10": float(kv["avg10"]), "a60": float(kv["avg60"]),
                     "tot": int(kv["total"])}
    return out


def psi_delta(prev, cur, dt_s):
    """-> {'sa10','sp','fa10','fp'}: avg10 and stall percent of the interval."""
    r = {}
    for kind, a, b in (("s", "some", "sa10"), ("f", "full", "fa10")):
        c = cur.get(a)
        p = prev.get(a) if prev else None
        r[b] = c["a10"] if c else None
        if c and p and dt_s > 0 and c["tot"] >= p["tot"]:
            r[kind + "p"] = round((c["tot"] - p["tot"]) / (dt_s * 1e4), 2)
        else:
            r[kind + "p"] = None
    return r


def percentile(values, q):
    v = sorted(x for x in values if x is not None)
    if not v:
        return None
    if len(v) == 1:
        return v[0]
    k = (len(v) - 1) * q / 100.0
    lo = int(k)
    hi = min(lo + 1, len(v) - 1)
    return v[lo] + (v[hi] - v[lo]) * (k - lo)


def _delta(prev, cur, key):
    if prev is None or key not in prev or key not in cur:
        return None
    x = cur[key] - prev[key]
    return x if x >= 0 else None


def container_delta(prev, cur, dt_s):
    """Raw container counters (see read_container) -> per-interval metrics."""
    if cur is None:
        return {"up": False}
    r = {"up": True, "mem": cur["mem"], "anon": cur["anon"], "file": cur["file"],
         "dirty": cur["dirty"], "wb": cur["wb"], "pids": cur["pids"]}
    if prev is None or dt_s <= 0:
        return r
    cpu = _delta(prev, cur, "cpu_ns")
    r["cpu"] = round(cpu / dt_s / 1e9, 3) if cpu is not None else None
    r["thr_p"] = _delta(prev, cur, "thr_periods")
    thr_ns = _delta(prev, cur, "thr_ns")
    r["thr_ms"] = round(thr_ns / 1e6, 2) if thr_ns is not None else None
    for key, out in (("pgmaj", "majf"), ("refault", "refault"),
                     ("pgscan", "scan"), ("pgsteal", "steal")):
        x = _delta(prev, cur, key)
        r[out] = round(x / dt_s, 2) if x is not None else None
    for op in ("read", "write"):
        a, b = prev.get("blk", {}), cur.get("blk", {})
        # the container's bytes against the primary (first sorted) device key
        key = cur.get("blk_key")
        x = (b.get(key, {}).get(op, 0) - a.get(key, {}).get(op, 0)) if key else None
        r["blk_" + op[0] + "mb"] = round(x / dt_s / 1e6, 3) if x is not None and x >= 0 else None
    return r


def probe_stats(samples):
    """samples: list of {'w': ms|None, 'r': ms|None} -> percentiles."""
    out = {}
    for k in ("w", "r"):
        vals = [s[k] for s in samples if s.get(k) is not None]
        out[k] = ({"n": len(vals), "p50": round(percentile(vals, 50), 3),
                   "p95": round(percentile(vals, 95), 3),
                   "p99": round(percentile(vals, 99), 3), "max": round(max(vals), 3)}
                  if vals else None)
    out["err"] = sum(1 for s in samples if s.get("e"))
    return out


def _mx(vals):
    v = [x for x in vals if x is not None]
    return max(v) if v else None


def _avg(vals):
    v = [x for x in vals if x is not None]
    return round(sum(v) / len(v), 3) if v else None


def summarize_minute(samples, primary_dev):
    """Aggregate the 's' records of one minute into one row (also the 'm' record)."""
    if not samples:
        return None
    row = {"k": "m", "t": samples[0]["t"], "n": len(samples)}
    devs = {}
    for d in samples[0].get("disk", {}):
        ds = [s["disk"][d] for s in samples if s.get("disk", {}).get(d)]
        if not ds:
            continue
        devs[d] = {
            "ri": _avg([x["ri"] for x in ds]), "wi": _avg([x["wi"] for x in ds]),
            "rmb": _avg([x["rmb"] for x in ds]), "wmb": _avg([x["wmb"] for x in ds]),
            "wmb_max": _mx([x["wmb"] for x in ds]),
            "ra_p50": percentile([x["ra"] for x in ds], 50),
            "ra_max": _mx([x["ra"] for x in ds]),
            "wa_p50": percentile([x["wa"] for x in ds], 50),
            "wa_p95": percentile([x["wa"] for x in ds], 95),
            "wa_max": _mx([x["wa"] for x in ds]),
            "util_avg": _avg([x["util"] for x in ds]), "util_max": _mx([x["util"] for x in ds]),
            "inf_max": _mx([x["inf"] for x in ds]), "q_max": _mx([x["q"] for x in ds]),
        }
    row["disk"] = devs
    psi = {}
    for kind in PSI_KINDS:
        ps = [s["psi"][kind] for s in samples if s.get("psi", {}).get(kind)]
        if ps:
            psi[kind] = {"sp_avg": _avg([x["sp"] for x in ps]), "sp_max": _mx([x["sp"] for x in ps]),
                         "fp_avg": _avg([x["fp"] for x in ps]), "fp_max": _mx([x["fp"] for x in ps]),
                         "fa10_max": _mx([x["fa10"] for x in ps])}
    row["psi"] = psi
    mem = [s["mem"] for s in samples if s.get("mem")]
    if mem:
        row["mem"] = {"dirty_max_kb": _mx([m["dirty"] for m in mem]),
                      "wb_max_kb": _mx([m["wb"] for m in mem]),
                      "avail_min_kb": min(m["avail"] for m in mem),
                      "majf_avg": _avg([m.get("majf") for m in mem]),
                      "swapin_avg": _avg([m.get("swin") for m in mem]),
                      "swapout_avg": _avg([m.get("swout") for m in mem]),
                      "blocked_max": _mx([m.get("blocked") for m in mem])}
    cts = {}
    for cid in (samples[0].get("ct") or {}):
        cs = [s["ct"][cid] for s in samples if s.get("ct", {}).get(cid, {}).get("up")]
        if not cs:
            cts[cid] = {"up": False}
            continue
        cts[cid] = {
            "up": True, "cpu_avg": _avg([c.get("cpu") for c in cs]),
            "cpu_max": _mx([c.get("cpu") for c in cs]),
            "thr_p": sum(c.get("thr_p") or 0 for c in cs),
            "thr_ms": round(sum(c.get("thr_ms") or 0 for c in cs), 2),
            "mem_max": _mx([c.get("mem") for c in cs]),
            "dirty_max": _mx([c.get("dirty") for c in cs]),
            "majf_avg": _avg([c.get("majf") for c in cs]), "majf_max": _mx([c.get("majf") for c in cs]),
            "refault_avg": _avg([c.get("refault") for c in cs]),
            "scan_avg": _avg([c.get("scan") for c in cs]),
            "blk_rmb": _avg([c.get("blk_rmb") for c in cs]),
            "blk_wmb": _avg([c.get("blk_wmb") for c in cs]),
        }
    row["ct"] = cts
    pr = [p for s in samples for p in s.get("probe", [])]
    if pr:
        row["probe"] = probe_stats(pr)
    row["primary"] = primary_dev
    return row


def merge_episodes(points, gap_s):
    """points: sorted [(t, kind, value)] -> [{'start','end','kinds','peak'}]."""
    eps = []
    for t, kind, val in sorted(points, key=lambda x: x[0]):
        if eps and t - eps[-1]["end"] <= gap_s:
            e = eps[-1]
            e["end"] = max(e["end"], t)
        else:
            e = {"start": t, "end": t, "kinds": {}}
            eps.append(e)
        k = e["kinds"].setdefault(kind, {"n": 0, "peak": val})
        k["n"] += 1
        if val is not None and (k["peak"] is None or val > k["peak"]):
            k["peak"] = val
    return eps


def detect_points(samples, primary, ids, mult=5.0, floor_ms=10.0, psi_full=5.0,
                  probe_floor_ms=50.0):
    """-> (host_points, {cid: points}) of anomalous (t, kind, value)."""
    host, ct = [], {c: [] for c in ids}

    def med(vals):
        v = [x for x in vals if x is not None]
        return statistics.median(v) if v else None

    for key, kind in (("wa", "wlat"), ("ra", "rlat")):
        vals = [s["disk"][primary][key] for s in samples if s.get("disk", {}).get(primary)]
        m = med(vals)
        thr = max((m or 0) * mult, floor_ms)
        for s in samples:
            x = (s.get("disk", {}).get(primary) or {}).get(key)
            if x is not None and x > thr:
                host.append((s["t"], f"{kind}>{thr:.0f}ms", x))
    pw = [p["w"] for s in samples for p in s.get("probe", []) if p.get("w") is not None]
    pm = med(pw)
    pthr = max((pm or 0) * mult, probe_floor_ms)
    for s in samples:
        for p in s.get("probe", []):
            if p.get("w") is not None and p["w"] > pthr:
                host.append((s["t"], "probe_w", p["w"]))
            if p.get("r") is not None and p["r"] > pthr:
                host.append((s["t"], "probe_r", p["r"]))
    for s in samples:
        for kind in ("io", "memory"):
            fp = (s.get("psi", {}).get(kind) or {}).get("fp")
            if fp is not None and fp > psi_full:
                host.append((s["t"], f"psi_{kind}_full", fp))
    for cid in ids:
        majf = [s["ct"][cid].get("majf") for s in samples if s.get("ct", {}).get(cid, {}).get("up")]
        mm = med(majf)
        mthr = max((mm or 0) * mult, 50.0)
        wmb = [s["ct"][cid].get("blk_wmb") for s in samples if s.get("ct", {}).get(cid, {}).get("up")]
        wm = med(wmb)
        wthr = max((wm or 0) * mult, 20.0)
        for s in samples:
            c = s.get("ct", {}).get(cid)
            if not c or not c.get("up"):
                continue
            if c.get("thr_p"):
                ct[cid].append((s["t"], "throttle", c["thr_p"]))
            if c.get("majf") is not None and c["majf"] > mthr:
                ct[cid].append((s["t"], "majfault", c["majf"]))
            if c.get("blk_wmb") is not None and c["blk_wmb"] > wthr:
                ct[cid].append((s["t"], "write_burst", c["blk_wmb"]))
    return host, ct


def overlaps(a, b, slack):
    return a["start"] - slack <= b["end"] and b["start"] - slack <= a["end"]


# --------------------------------------------------------------------------
# Readers
# --------------------------------------------------------------------------
def read_text(path):
    with open(path) as f:
        return f.read()


class UnixHTTP(http.client.HTTPConnection):
    def __init__(self, path, timeout):
        super().__init__("localhost", timeout=timeout)
        self._path = path

    def connect(self):
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(self.timeout)
        s.connect(self._path)
        self.sock = s


def read_container(sock_path, ident, timeout=4.0):
    """Raw counters for one container by exact ID/name via the stats endpoint
    (one-shot, read-only).  Returns None when it is gone / unreadable."""
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", ident):
        raise ValueError("bad container ident")
    conn = UnixHTTP(sock_path, timeout)
    try:
        conn.request("GET", f"/containers/{ident}/stats?stream=false&one-shot=true")
        resp = conn.getresponse()
        body = resp.read()
        if resp.status != 200:
            return None
        d = json.loads(body)
    except (OSError, ValueError, http.client.HTTPException):
        return None
    finally:
        conn.close()
    cpu, ms = d.get("cpu_stats", {}), d.get("memory_stats", {})
    st = ms.get("stats", {})
    thr = cpu.get("throttling_data", {})
    blk = {}
    for e in (d.get("blkio_stats", {}).get("io_service_bytes_recursive") or []):
        blk.setdefault(f"{e['major']}:{e['minor']}", {})[e["op"].lower()] = e["value"]
    # primary block-device key: the one that saw the most bytes (physical disk
    # appears under the dm key and under its parent; we keep the largest)
    blk_key = max(blk, key=lambda k: sum(blk[k].values())) if blk else None
    return {
        "cpu_ns": cpu.get("cpu_usage", {}).get("total_usage", 0),
        "thr_periods": thr.get("throttled_periods", 0), "thr_ns": thr.get("throttled_time", 0),
        "mem": ms.get("usage", 0), "anon": st.get("anon", 0), "file": st.get("file", 0),
        "dirty": st.get("file_dirty", 0), "wb": st.get("file_writeback", 0),
        "pgmaj": st.get("pgmajfault", 0),
        "refault": sum(v for k, v in st.items() if k.startswith("workingset_refault")),
        "pgscan": st.get("pgscan", 0), "pgsteal": st.get("pgsteal", 0),
        "pids": d.get("pids_stats", {}).get("current", 0),
        "blk": blk, "blk_key": blk_key,
    }


def read_kv_file(path):
    out = {}
    for line in read_text(path).splitlines():
        p = line.split()
        if len(p) >= 2:
            out[p[0].rstrip(":")] = int(p[1])
    return out


def read_queue_params(dev):
    q = f"/sys/block/{dev}/queue/"
    out = {}
    for name in ("nr_requests", "scheduler", "max_sectors_kb", "read_ahead_kb", "wbt_lat_usec"):
        try:
            out[name] = read_text(q + name).strip()
        except OSError:
            pass
    return out


def ioprio_best_effort_self_thread():
    """Raise only the CALLING thread from idle to best-effort/7 so the probe
    reflects what an ordinary service sees (mq-deadline honours ioprio)."""
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        tid = libc.syscall(186)  # gettid, x86_64
        # IOPRIO_WHO_PROCESS=1; class BE=2 << 13 | level 7
        libc.syscall(251, 1, tid, (2 << 13) | 7)
    except Exception:  # noqa: BLE001 - best effort only
        pass


class Probe(threading.Thread):
    """4 KiB O_DSYNC write + 4 KiB O_DIRECT read every `period` s on a 1 MiB file."""

    def __init__(self, directory, period, out_q, stop, log):
        super().__init__(daemon=True, name="probe")
        self.dir, self.period, self.q, self.stop_ev, self.log = directory, period, out_q, stop, log
        self.path = os.path.join(directory, "iomon-probe.dat")

    def run(self):
        ioprio_best_effort_self_thread()
        fd = rfd = None
        try:
            os.makedirs(self.dir, exist_ok=True)
            blocks = 256
            buf = mmap.mmap(-1, 4096)
            buf.write(b"iomon" * 800)
            with open(self.path, "wb") as f:  # one-time 1 MiB preallocation
                f.write(b"\0" * (blocks * 4096))
                f.flush()
                os.fsync(f.fileno())
            fd = os.open(self.path, os.O_WRONLY | os.O_DSYNC)
            try:
                rfd = os.open(self.path, os.O_RDONLY | os.O_DIRECT)
            except OSError:
                rfd = os.open(self.path, os.O_RDONLY)
            rbuf = mmap.mmap(-1, 4096)
            i = 0
            while not self.stop_ev.wait(self.period):
                off = (i % blocks) * 4096
                i += 1
                rec = {}
                try:
                    t0 = time.perf_counter()
                    os.pwrite(fd, buf, off)
                    rec["w"] = round((time.perf_counter() - t0) * 1000, 3)
                    try:
                        os.posix_fadvise(rfd, 0, 0, os.POSIX_FADV_DONTNEED)
                    except OSError:
                        pass
                    t0 = time.perf_counter()
                    os.preadv(rfd, [rbuf], off)
                    rec["r"] = round((time.perf_counter() - t0) * 1000, 3)
                except OSError as e:
                    rec["e"] = str(e)
                self.q.put(rec)
        except Exception as e:  # noqa: BLE001
            self.log.error("probe thread died: %r", e)
        finally:
            for x in (fd, rfd):
                if x is not None:
                    os.close(x)
            try:
                os.unlink(self.path)
            except OSError:
                pass


# --------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------
class Writer:
    """Size-capped, segment-rotating JSONL writer.  Thread-safe: every public method takes one
    lock, so concurrent writers (the eBPF supervisor's reader threads plus its main loop) can
    never see a segment closed under them."""

    def __init__(self, out_dir, cap_bytes, segment_bytes=SEGMENT_BYTES, prefix="samples-"):
        self.dir, self.cap, self.seg_max, self.prefix = out_dir, cap_bytes, segment_bytes, prefix
        self.f, self.size = None, 0
        self._lock = threading.Lock()
        self._seq = 0

    def _open(self):
        if self.f:
            self.f.close()
        self._seq += 1
        name = (self.prefix + dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
                + f"-{self._seq:04d}.jsonl")
        self.f = open(os.path.join(self.dir, name), "a", buffering=1)
        self.size = 0
        self._enforce_cap()

    def _enforce_cap(self):
        files = sorted(x for x in os.listdir(self.dir) if x.startswith(self.prefix))
        sizes = {x: os.path.getsize(os.path.join(self.dir, x)) for x in files}
        total = sum(sizes.values())
        for x in files[:-1]:  # never delete the newest (current) segment
            if total <= self.cap:
                break
            os.unlink(os.path.join(self.dir, x))
            total -= sizes[x]

    def enforce_cap(self):
        with self._lock:
            self._enforce_cap()

    def write(self, obj):
        line = json.dumps(obj, separators=(",", ":")) + "\n"
        with self._lock:
            if self.f is None or self.size + len(line) > self.seg_max:
                self._open()
            self.f.write(line)
            self.size += len(line)

    def close(self):
        with self._lock:
            if self.f:
                self.f.close()
                self.f = None


# --------------------------------------------------------------------------
# Sampler
# --------------------------------------------------------------------------
def cmd_run(a):
    out = os.path.abspath(a.out)
    os.makedirs(out, exist_ok=True)
    log = logging.getLogger("iomon")
    log.setLevel(logging.INFO)
    h = logging.handlers.RotatingFileHandler(os.path.join(out, "iomon.log"), maxBytes=2_000_000, backupCount=2)
    h.setFormatter(logging.Formatter("%(asctime)sZ %(levelname)s %(message)s"))
    h.formatter.converter = time.gmtime
    log.addHandler(h)
    pidf, stopf = os.path.join(out, "iomon.pid"), os.path.join(out, "STOP")
    if os.path.exists(stopf):
        os.unlink(stopf)
    if os.path.exists(pidf):
        try:
            os.kill(int(read_text(pidf)), 0)
            print("already running", file=sys.stderr)
            return 1
        except (OSError, ValueError):
            pass
    with open(pidf, "w") as f:
        f.write(str(os.getpid()))
    stop = threading.Event()
    for s in (signal.SIGTERM, signal.SIGINT):
        signal.signal(s, lambda *_: stop.set())
    devices = tuple(a.devices.split(","))
    primary = a.primary or devices[0]
    ids = list(a.container)
    cap = int(a.cap_mb * 1024 * 1024)
    w = Writer(out, cap)
    pq = queue.Queue()
    probe = None
    if a.probe_dir:
        probe = Probe(a.probe_dir, a.probe_period, pq, stop, log)
        probe.start()
    log.info("start pid=%d interval=%s devices=%s containers=%s probe=%s max_hours=%s",
             os.getpid(), a.interval, devices, ids, a.probe_dir, a.max_hours)
    w.write({"k": "meta", "t": round(time.time(), 3), "interval": a.interval, "devices": devices,
             "primary": primary, "containers": ids, "probe_dir": a.probe_dir,
             "queue": {d: read_queue_params(d) for d in devices}})
    deadline_end = time.monotonic() + a.max_hours * 3600
    prev = {}
    cprev = {}
    minute_buf, cur_minute = [], None
    last_cpu = time.process_time()
    last_wall = time.monotonic()
    qsnap = {d: read_queue_params(d) for d in devices}
    up_state = {}
    next_t = time.monotonic()
    try:
        while not stop.is_set():
            if os.path.exists(stopf):
                log.info("STOP marker found")
                break
            if time.monotonic() >= deadline_end:
                log.info("max runtime reached")
                break
            try:
                now_m, now_w = time.monotonic(), time.time()
                s = {"k": "s", "t": round(now_w, 3)}
                try:
                    ds = parse_diskstats(read_text("/proc/diskstats"), set(devices))
                    psi = {k: parse_psi(read_text(f"/proc/pressure/{k}")) for k in PSI_KINDS}
                    mi = read_kv_file("/proc/meminfo")
                    vm = read_kv_file("/proc/vmstat")
                    stat_blocked = None
                    for line in read_text("/proc/stat").splitlines():
                        if line.startswith("procs_blocked"):
                            stat_blocked = int(line.split()[1])
                    load = read_text("/proc/loadavg").split()[0]
                    cg_psi = {}
                    for k in PSI_KINDS:
                        try:
                            cg_psi[k] = parse_psi(read_text(f"/sys/fs/cgroup/{k}.pressure"))
                        except OSError:
                            pass
                except OSError as e:
                    log.error("proc read failed: %r", e)
                    raise
                craw = {}
                for cid in ids:
                    try:
                        craw[cid] = read_container(a.docker_sock, cid)
                    except Exception as e:  # noqa: BLE001
                        log.error("container %s read error: %r", cid, e)
                        craw[cid] = None
                    st = craw[cid] is not None
                    if up_state.get(cid) != st:
                        log.info("container %s %s", cid, "reachable" if st else "NOT reachable")
                        up_state[cid] = st
                if prev:
                    dts = now_m - prev["m"]
                    s["dt"] = round(dts, 3)
                    s["disk"] = {d: disk_delta(prev["ds"][d], ds[d], dts)
                                 for d in devices if d in ds and d in prev["ds"]}
                    s["psi"] = {k: psi_delta(prev["psi"][k], psi[k], dts) for k in PSI_KINDS}
                    s["cgpsi"] = {k: psi_delta(prev["cg_psi"].get(k, {}), cg_psi[k], dts)
                                  for k in cg_psi}
                    rate = lambda key: round(max(0, vm.get(key, 0) - prev["vm"].get(key, 0)) / dts, 2)
                    s["mem"] = {"dirty": mi.get("Dirty", 0), "wb": mi.get("Writeback", 0),
                                "avail": mi.get("MemAvailable", 0), "swfree": mi.get("SwapFree", 0),
                                "majf": rate("pgmajfault"), "swin": rate("pswpin"),
                                "swout": rate("pswpout"),
                                "refault": round((max(0, vm.get("workingset_refault_anon", 0) - prev["vm"].get("workingset_refault_anon", 0))
                                                  + max(0, vm.get("workingset_refault_file", 0) - prev["vm"].get("workingset_refault_file", 0))) / dts, 2),
                                "blocked": stat_blocked, "load1": float(load)}
                    s["ct"] = {cid: container_delta(cprev.get(cid), craw[cid], dts) for cid in ids}
                    probes = []
                    while True:
                        try:
                            probes.append(pq.get_nowait())
                        except queue.Empty:
                            break
                    if probes:
                        s["probe"] = probes
                    w.write(s)
                    minute = int(now_w // 60)
                    if cur_minute is None:
                        cur_minute = minute
                    if minute != cur_minute and minute_buf:
                        m = summarize_minute(minute_buf, primary)
                        cpu_now, wall_now = time.process_time(), time.monotonic()
                        m["self_cpu_pct"] = round((cpu_now - last_cpu) / max(wall_now - last_wall, 1e-9) * 100, 3)
                        last_cpu, last_wall = cpu_now, wall_now
                        q2 = {d: read_queue_params(d) for d in devices}
                        if q2 != qsnap:
                            log.warning("queue params changed: %s -> %s", qsnap, q2)
                            qsnap = q2
                        m["queue"] = q2
                        w.write(m)
                        minute_buf, cur_minute = [], minute
                    minute_buf.append(s)
                prev = {"m": now_m, "ds": ds, "psi": psi, "cg_psi": cg_psi, "vm": vm}
                cprev = craw
            except Exception as e:  # noqa: BLE001 - keep sampling
                log.error("sample error: %r", e)
            next_t += a.interval
            delay = next_t - time.monotonic()
            if delay < -a.interval:  # fell behind (suspend / stall): resync
                next_t = time.monotonic()
                delay = 0
            stop.wait(max(0.0, delay))
    finally:
        stop.set()
        if probe:
            probe.join(timeout=15)
        if minute_buf:
            m = summarize_minute(minute_buf, primary)
            if m:
                w.write(m)
        w.close()
        for p in (pidf, stopf):
            try:
                os.unlink(p)
            except OSError:
                pass
        log.info("stopped")
    return 0


DEFAULT_BPF_NAME = "iomon-bpf-20261027"


def cmd_stop(a):
    p = os.path.join(a.out, "STOP")
    open(p, "w").close()
    print("STOP marker written:", p)
    bpf = os.path.join(a.out, "bpf")  # symlink to the eBPF collector's output dir, if present
    if os.path.isdir(bpf):
        open(os.path.join(bpf, "STOP-BPF"), "w").close()
        print("STOP-BPF marker written:", bpf)
        print(f"If the collector container is still up after ~60 s: docker stop -t 60 {a.bpf_name}")
    else:
        why = "dangling symlink" if os.path.islink(bpf) else "missing"
        print(f"WARNING: {bpf} is {why}: the eBPF collector (if one is running) was NOT told to stop.\n"
              f"WARNING: stop it by exact name: docker stop -t 60 {a.bpf_name}\n"
              f"WARNING: (the container name given to run.sh --name; default {DEFAULT_BPF_NAME}), or "
              f"touch STOP-BPF in its output directory.", file=sys.stderr)
    return 0


# --------------------------------------------------------------------------
# Analyze
# --------------------------------------------------------------------------
def load_records(out):
    recs = []
    for name in sorted(x for x in os.listdir(out) if x.startswith("samples-")):
        with open(os.path.join(out, name)) as f:
            for line in f:
                try:
                    recs.append(json.loads(line))
                except ValueError:
                    pass  # torn final line
    return recs


def parse_dur(s):
    m = re.fullmatch(r"(\d+)([smh])", s)
    if not m:
        raise SystemExit(f"bad duration {s!r}")
    return int(m.group(1)) * {"s": 1, "m": 60, "h": 3600}[m.group(2)]


def parse_when(s):
    d = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    return (d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)).timestamp()


def hms(t):
    return dt.datetime.fromtimestamp(t, dt.timezone.utc).strftime("%H:%M:%S")


def fmt(v, nd=1):
    return "-" if v is None else (f"{v:.{nd}f}" if isinstance(v, float) else str(v))


def cmd_analyze(a):
    recs = load_records(a.out)
    meta = next((r for r in recs if r.get("k") == "meta"), {})
    samples = [r for r in recs if r.get("k") == "s"]
    if not samples:
        print("no samples")
        return 1
    t0, t1 = samples[0]["t"], samples[-1]["t"]
    if a.first:
        t1 = t0 + parse_dur(a.first)
    elif a.last:
        t0 = t1 - parse_dur(a.last)
    if a.since:
        t0 = parse_when(a.since)
    if a.until:
        t1 = parse_when(a.until)
    samples = [s for s in samples if t0 <= s["t"] <= t1]
    if not samples:
        print("no samples in window")
        return 1
    primary = a.primary or meta.get("primary") or DEFAULT_DEVICES[0]
    ids = meta.get("containers") or sorted({c for s in samples for c in s.get("ct", {})})
    short = {c: c[:6] for c in ids}
    print(f"window {hms(samples[0]['t'])} .. {hms(samples[-1]['t'])} UTC  samples={len(samples)}"
          f"  primary dev={primary}  containers={[short[c] for c in ids]}")
    by_min = {}
    for s in samples:
        by_min.setdefault(int(s["t"] // 60), []).append(s)
    rows = [summarize_minute(v, primary) for _, v in sorted(by_min.items())]
    print("\nPer-minute (device %s; w/r lat = per-interval await ms p50/max; PSI = stall%% avg/max;"
          " MB/s r/w; dirty/wb MB max)" % primary)
    print("min(UTC) | wlat p50/p95/max | rlat p50/max | util avg/max | IOPS r/w | MB/s r/w | inflt | "
          "probe w p50/max | io some/full | mem some/full | cpu some | dirty/wb | load")
    for r in rows:
        d = r["disk"].get(primary) or {}
        p = (r.get("probe") or {}).get("w") or {}
        pi, pm, pc = r["psi"].get("io", {}), r["psi"].get("memory", {}), r["psi"].get("cpu", {})
        m = r.get("mem", {})
        print(f"{hms(r['t'])[:5]}    | {fmt(d.get('wa_p50'))}/{fmt(d.get('wa_p95'))}/{fmt(d.get('wa_max'))}"
              f" | {fmt(d.get('ra_p50'))}/{fmt(d.get('ra_max'))}"
              f" | {fmt(d.get('util_avg'))}/{fmt(d.get('util_max'))}"
              f" | {fmt(d.get('ri'), 0)}/{fmt(d.get('wi'), 0)} | {fmt(d.get('rmb'))}/{fmt(d.get('wmb'))}"
              f" | {fmt(d.get('inf_max'), 0)} | {fmt(p.get('p50'))}/{fmt(p.get('max'))}"
              f" | {fmt(pi.get('sp_avg'))}/{fmt(pi.get('fp_avg'))} (max {fmt(pi.get('fp_max'))})"
              f" | {fmt(pm.get('sp_avg'))}/{fmt(pm.get('fp_avg'))}"
              f" | {fmt(pc.get('sp_avg'))}"
              f" | {fmt((m.get('dirty_max_kb') or 0) / 1024)}/{fmt((m.get('wb_max_kb') or 0) / 1024)}"
              f" | {fmt(max((s.get('mem', {}).get('load1') or 0) for s in by_min[int(r['t'] // 60)]))}")
    print("\nContainers per-minute (cpu cores avg/max; thr = throttled periods; majf/s avg/max; "
          "blk MB/s r/w; mem GiB; dirty MB)")
    for cid in ids:
        print(f"-- {short[cid]}")
        for r in rows:
            c = r["ct"].get(cid, {})
            if not c.get("up"):
                print(f"{hms(r['t'])[:5]}    | DOWN")
                continue
            print(f"{hms(r['t'])[:5]}    | cpu {fmt(c['cpu_avg'], 2)}/{fmt(c['cpu_max'], 2)}"
                  f" | thr {c['thr_p']} ({c['thr_ms']} ms) | majf {fmt(c['majf_avg'])}/{fmt(c['majf_max'])}"
                  f" | refault {fmt(c['refault_avg'])} | blk {fmt(c['blk_rmb'], 2)}/{fmt(c['blk_wmb'], 2)}"
                  f" | mem {fmt((c['mem_max'] or 0) / 2**30, 2)} | dirty {fmt((c['dirty_max'] or 0) / 1e6)}")
    host, ct = detect_points(samples, primary, ids, mult=a.mult, floor_ms=a.floor_ms, psi_full=a.psi_full)
    gap = a.gap
    hep = merge_episodes(host, gap)
    cep = {c: merge_episodes(ct[c], gap) for c in ids}
    print(f"\nStall episodes (latency > {a.mult}x median and > {a.floor_ms} ms; PSI full > {a.psi_full}%; "
          f"probe > max({a.mult}x median, 50 ms)); merged within {gap}s")
    print("HOST/DEVICE:" if hep else "HOST/DEVICE: none")
    for e in hep:
        kinds = ", ".join(f"{k}(n={v['n']},peak={fmt(v['peak'])})" for k, v in e["kinds"].items())
        print(f"  {hms(e['start'])}-{hms(e['end'])}  {kinds}")
    for c in ids:
        print(f"CONTAINER {short[c]}:" + ("" if cep[c] else " none"))
        for e in cep[c]:
            kinds = ", ".join(f"{k}(n={v['n']},peak={fmt(v['peak'])})" for k, v in e["kinds"].items())
            print(f"  {hms(e['start'])}-{hms(e['end'])}  {kinds}")
    print("\nCorrelation (containers' anomalies within +/-%ds of a host episode, and across containers):" % gap)
    any_c = False
    for e in hep:
        hits = [f"{short[c]}:{'+'.join(x['kinds'])}" for c in ids for x in cep[c] if overlaps(e, x, gap)]
        if hits:
            any_c = True
            print(f"  host {hms(e['start'])}-{hms(e['end'])} <-> {'; '.join(hits)}")
    for i, c1 in enumerate(ids):
        for c2 in ids[i + 1:]:
            for x in cep[c1]:
                for y in cep[c2]:
                    if overlaps(x, y, gap):
                        any_c = True
                        print(f"  {short[c1]} {hms(x['start'])}-{hms(x['end'])} ({'+'.join(x['kinds'])}) <-> "
                              f"{short[c2]} {hms(y['start'])}-{hms(y['end'])} ({'+'.join(y['kinds'])})")
    if not any_c:
        print("  no overlapping episodes")
    print("\nWindow totals: samples=%d, device wlat p50=%s ms max=%s ms, probe w p50/p99/max=%s" % (
        len(samples),
        fmt(percentile([s["disk"][primary]["wa"] for s in samples if s.get("disk", {}).get(primary)], 50), 2),
        fmt(_mx([s["disk"][primary]["wa"] for s in samples if s.get("disk", {}).get(primary)]), 1),
        (lambda ps: "/".join(fmt(x, 2) for x in (percentile(ps, 50), percentile(ps, 99), _mx(ps))))(
            [p["w"] for s in samples for p in s.get("probe", []) if p.get("w") is not None])))
    brecs = load_bpf_records(a.out)
    if any(r.get("k") == "bpf" for r in brecs):
        analyze_bpf(brecs, t0, t1, [short[c] for c in ids])
    return 0


# --------------------------------------------------------------------------
# eBPF collector output (bpf-*.jsonl written by iomon_bpf.py) - analysis
# --------------------------------------------------------------------------
KIND = {0: "R", 1: "W", 2: "F", 3: "O"}


def hist_merge(acc, key, buckets):
    d = acc.setdefault(key, {})
    for b in buckets:
        k = (b["min"], b["max"])
        d[k] = d.get(k, 0) + b["count"]


def hist_total(d):
    return sum(d.values()) if d else 0


def hist_pct(d, q):
    """Upper bound of the log2 bucket holding quantile q (0..100); None if empty."""
    tot = hist_total(d)
    if not tot:
        return None
    need, cum = tot * q / 100.0, 0
    for (lo, hi), c in sorted(d.items()):
        cum += c
        if cum >= need and c:
            return hi
    return max(hi for (_, hi) in d)


def hist_max(d):
    live = [hi for (_, hi), c in d.items() if c]
    return max(live) if live else None


def dev_name(code):
    major, minor = code >> 20, code & 0xFFFFF
    return {254: "vda", 11: "sr0"}.get(major) or (f"dm-{minor}" if major == 253 else f"{major}:{minor}")


def aggregate_bpf(recs):
    """bpf records -> {'maps': {name: {key: n}}, 'hists': {name: {key: {(lo,hi): n}}},
    'stats': {name: [count, total]}, 'events': [...]}; keys stay strings ('' for unkeyed)."""
    maps, hists, stats, events = {}, {}, {}, []
    for r in recs:
        o = r.get("o") or {}
        typ, data = o.get("type"), o.get("data")
        if typ in ("map", "hist", "stats") and isinstance(data, dict):
            for name, val in data.items():
                if typ == "map":
                    if isinstance(val, dict):
                        for k, v in val.items():
                            if not isinstance(v, (int, float)):
                                continue
                            m = maps.setdefault(name, {})
                            m[k] = m.get(k, 0) + v if not name.endswith(("_ppm", "lagging", "shortages")) else max(m.get(k, 0), v)
                    elif isinstance(val, (int, float)):
                        m = maps.setdefault(name, {})
                        m[""] = max(m.get("", 0), val) if name.endswith(("_ppm", "lagging", "shortages")) else m.get("", 0) + val
                elif typ == "hist":
                    if isinstance(val, dict):
                        for k, b in val.items():
                            hist_merge(hists.setdefault(name, {}), k, b)
                    else:
                        hist_merge(hists.setdefault(name, {}), "", val)
                else:
                    st = stats.setdefault(name, [0, 0])
                    st[0] += val.get("count", 0)
                    st[1] += val.get("total", 0)
        elif o.get("ev"):
            events.append(dict(o, t=r["t"]))
    return {"maps": maps, "hists": hists, "stats": stats, "events": events}


def counter_delta(first, last):
    """Cumulative counters (nested dict of numbers) -> last - first, same shape."""
    if isinstance(last, dict):
        return {k: counter_delta((first or {}).get(k), v) for k, v in last.items()}
    if isinstance(last, (int, float)) and isinstance(first, (int, float)):
        return last - first if last >= first else None
    return None


def load_bpf_records(out):
    recs = []
    for d in (out, os.path.join(out, "bpf")):
        if os.path.isdir(d):
            for name in sorted(x for x in os.listdir(d) if x.startswith("bpf-")):
                with open(os.path.join(d, name)) as f:
                    for line in f:
                        try:
                            recs.append(json.loads(line))
                        except ValueError:
                            pass
    recs.sort(key=lambda r: r.get("t", 0))
    return recs


def _lat_line(h):
    if not h:
        return "-"
    return f"n={hist_total(h)} p50<={hist_pct(h, 50)} p99<={hist_pct(h, 99)} max<={hist_max(h)}us"


def analyze_bpf(recs, t0, t1, shorts, out=print):
    """Print the eBPF-derived sections for the window.  shorts = names of watched containers."""
    win = [r for r in recs if t0 <= r.get("t", 0) <= t1]
    metas = [r for r in recs if r.get("k") == "bpf_meta" and "containers" in r]
    ids = (metas[-1]["cgroup_ids"] if metas else [0, 0])
    if metas:
        shorts = metas[-1]["containers"]
    who = {"0": "other", "1": shorts[0] if shorts else "w1", "2": shorts[1] if len(shorts) > 1 else "w2"}
    cgm = {}
    for r in recs:
        if r.get("k") == "cgmap":
            cgm.update({v: k for k, v in r["ids"].items()})
    for name, i in zip(shorts, ids):
        cgm[i] = f"scope:{name}"
    cgname = lambda i: cgm.get(int(i), str(i)) if str(i).lstrip("-").isdigit() else str(i)
    agg = aggregate_bpf([r for r in win if r.get("k") == "bpf"])
    span = max(1.0, (win[-1]["t"] - win[0]["t"])) if win else 1.0
    out(f"\n=== eBPF collector window {hms(win[0]['t'])}..{hms(win[-1]['t'])} UTC "
        f"({len(win)} records, ~{span:.0f}s) watched cgroup ids {ids}")
    H, M, S = agg["hists"], agg["maps"], agg["stats"]

    out("\n(1) Block service latency (blk_mq_start->end, us, log2-bucket upper bounds), "
        "by device / op / sync / owner cgroup")
    rows = {}
    for key, h in H.get("@lat", {}).items():
        dev, kind, sy, w = key.split(",")
        rows.setdefault((dev_name(int(dev)), KIND[int(kind)], who[w]), {}).setdefault(sy, {})
        for b, c in h.items():
            rows[(dev_name(int(dev)), KIND[int(kind)], who[w])][sy][b] = rows[(dev_name(int(dev)), KIND[int(kind)], who[w])][sy].get(b, 0) + c
    for (dev, kind, wn), bysy in sorted(rows.items()):
        allh = {}
        for h in bysy.values():
            for b, c in h.items():
                allh[b] = allh.get(b, 0) + c
        n, ns = hist_total(allh), hist_total(bysy.get("1", {}))
        if n < 5 and wn == "other":
            continue
        out(f"  {dev:6} {kind} {wn:8} {n / span:8.1f} IOPS  sync={100 * ns / max(n, 1):3.0f}%  {_lat_line(allh)}")
    slow = M.get("@slow", {})
    if slow:
        out("  >100 ms requests: " + ", ".join(
            f"{dev_name(int(k.split(',')[0]))}/{KIND[int(k.split(',')[1])]}/{who[k.split(',')[2]]}={v}"
            for k, v in sorted(slow.items())))
    else:
        out("  >100 ms requests: none")
    tw = H.get("@tagwait", {})
    out("  request-slot (tag) wait, us: " + "; ".join(f"{who[k]}: {_lat_line(h)}" for k, h in sorted(tw.items())))

    out("\n(6) Who writes (bytes submitted by owner cgroup; top 8) and by submitting comm")
    byc = {}
    for k, v in M.get("@bytes_by_cg", {}).items():
        cid, kind = k.split(",")
        if kind == "1":
            byc[cgname(cid)] = byc.get(cgname(cid), 0) + v
    for name, v in sorted(byc.items(), key=lambda x: -x[1])[:8]:
        out(f"  {v / 1e6:10.1f} MB  {v / span / 1e6:7.2f} MB/s  {name}")
    bycomm = {}
    for k, v in M.get("@wbytes_comm", {}).items():
        comm, cid = k.rsplit(",", 1)
        bycomm[(comm, cgname(cid))] = bycomm.get((comm, cgname(cid)), 0) + v
    for (comm, cg), v in sorted(bycomm.items(), key=lambda x: -x[1])[:8]:
        out(f"  {v / 1e6:10.1f} MB  comm={comm:18} owner={cg}")

    out("\n(2) D-state (uninterruptible sleep) of watched tasks")
    for k, h in sorted(H.get("@dstate_us", {}).items()):
        out(f"  {who[k]:8} wakeups={hist_total(h)} {_lat_line(h)}")
    longs = M.get("@dlong", {})
    out(f"  episodes > 100 ms: {sum(M.get('@dlong_n', {}).values())}" if longs or M.get("@dlong_n") else "  episodes > 100 ms: none")
    for k, ms in sorted(longs.items(), key=lambda x: -x[1])[:6]:
        comm, w, stack = k.split(",", 2)
        out(f"  {ms} ms total  comm={comm} {who.get(w, w)}\n    " + " <- ".join(x.strip() for x in stack.strip().splitlines()[:7]))

    out("\n(3) fsync / write() of watched tasks (us)")
    for name in ("@fsync_us", "@write_us"):
        for k, h in sorted(H.get(name, {}).items()):
            out(f"  {name[1:]:9} {who[k]:8} {_lat_line(h)}")
    evs = [e for e in agg["events"] if e.get("ev") in ("slow_fsync", "slow_write")]
    out(f"  slow (>50 ms) events: {len(evs)}")
    for e in evs[:12]:
        out(f"    {hms(e['t'])} {e['ev']} {who.get(str(e['w']))} comm={e['comm']} tid={e['tid']} {e['ms']} ms")

    out("\n(4) iocost / queueing")
    adj = M.get("@adj", {})
    nadj = sum(adj.values())
    vr = S.get("@vrate", [0, 0])
    out(f"  vrate adjustments: {nadj} (up={adj.get('1', 0)} down={adj.get('2', 0)} same={adj.get('0', 0)}); "
        f"avg new vrate (raw)={vr[1] / vr[0]:.0f}" if vr[0] else "  vrate adjustments: none")
    bz = S.get("@busy", [0, 0])
    if bz[0]:
        out(f"  busy_level avg={bz[1] / bz[0]:.1f}  max read_missed_ppm={max(M.get('@rmiss_ppm', {'': 0}).values())} "
            f"max write_missed_ppm={max(M.get('@wmiss_ppm', {'': 0}).values())} "
            f"max lagging={max(M.get('@lagging', {'': 0}).values())} max shortages={max(M.get('@shortages', {'': 0}).values())}")
    rq = H.get("@rqwait_pct", {}).get("")
    if rq:
        out(f"  rq_wait_pct per period: p50<={hist_pct(rq, 50)} p90<={hist_pct(rq, 90)} max<={hist_max(rq)}  "
            f"(>5% in {100 * sum(c for (lo, hi), c in rq.items() if lo > 5) / max(hist_total(rq), 1):.0f}% of periods)")
    out(f"  iocg activations={sum(M.get('@iocg_activate', {}).values())} idles={sum(M.get('@iocg_idle', {}).values())} "
        f"inuse shortages={sum(M.get('@inuse_shortage', {}).values())} debt forgiven={sum(M.get('@forgive_debt', {}).values())}")
    cgs = [r for r in win if r.get("k") == "cg"]
    if len(cgs) >= 2:
        f0, f1 = cgs[0]["cg"], cgs[-1]["cg"]
        dtw = max(cgs[-1]["t"] - cgs[0]["t"], 1)
        root = f1.get("root", {})
        out(f"  qos: {root.get('cost_qos')}\n  model: {root.get('cost_model')}")
        vrs = [r["cg"]["root"]["io"].get("254:0", {}).get("cost.vrate") for r in cgs if r["cg"].get("root")]
        vrs = [x for x in vrs if x is not None]
        if vrs:
            out(f"  root cost.vrate samples: min={min(vrs)} median={statistics.median(vrs)} max={max(vrs)}")
        for name in shorts:
            a, b = f0.get(name[:12]) or f0.get(name), f1.get(name[:12]) or f1.get(name)
            if not (a and b):
                continue
            dv = counter_delta(a, b)
            io = dv.get("io", {}).get("254:0", {})
            out(f"  {name}: io.max={b.get('io_max', '(none)')} weight={b.get('io_weight')}  "
                f"rd {io.get('rios', 0) / dtw:.1f} IOPS {io.get('rbytes', 0) / dtw / 1e6:.2f} MB/s  "
                f"wr {io.get('wios', 0) / dtw:.1f} IOPS {io.get('wbytes', 0) / dtw / 1e6:.2f} MB/s  "
                f"cost.usage d={io.get('cost.usage')}  "
                f"io-psi total d(some/full)={dv['io_psi'].get('some', {}).get('tot', 0) / 1e3:.0f}/"
                f"{dv['io_psi'].get('full', {}).get('tot', 0) / 1e3:.0f} ms  "
                f"mem.high events d={dv['mem_events'].get('high')}  mem-psi some d={dv['memory_psi'].get('some', {}).get('tot', 0) / 1e3:.0f} ms")
        for name in sorted(k for k in f1 if k.endswith(".slice")):  # slices the collector was told to watch
            b = f1.get(name)
            if b:
                dv = counter_delta(f0.get(name), b)
                io = dv.get("io", {}).get("254:0", {})
                out(f"  {name}: io.max={b.get('io_max', '(none)')}  wr {io.get('wbytes', 0) / dtw / 1e6:.2f} MB/s "
                    f"rd {io.get('rbytes', 0) / dtw / 1e6:.2f} MB/s cost.usage d={io.get('cost.usage')}")

    out("\n(5) Writeback throttling (balance_dirty_pages pauses > 0) by cgroup")
    bm = M.get("@bdp_ms", {})
    for k, ms in sorted(bm.items(), key=lambda x: -x[1])[:6]:
        out(f"  {cgname(k)}: {ms} ms total over {M.get('@bdp_n', {}).get(k, 0)} pauses")
    if not bm:
        out("  none")
    out("\n(7) Swap: swap-in pages by faulting cgroup (top 6); swap-out by reclaiming comm (top 4)")
    for k, v in sorted(M.get("@swapin", {}).items(), key=lambda x: -x[1])[:6]:
        out(f"  in  {v / span:8.1f} pages/s  {cgname(k)}")
    for k, v in sorted(M.get("@swapout", {}).items(), key=lambda x: -x[1])[:4]:
        out(f"  out {v / span:8.1f} pages/s  comm={k}")
    if len(cgs) >= 2:
        for name in shorts:
            a, b = f0.get(name[:12]), f1.get(name[:12])
            if a and b:
                dv = counter_delta(a["mem_stat"], b["mem_stat"])
                out(f"  {name}: swap.current {a.get('m_swap.current', 0) / 2**20:.0f}->{b.get('m_swap.current', 0) / 2**20:.0f} MiB; "
                    f"majfault/s={((dv.get('pgmajfault') or 0) / dtw):.1f} refault_anon/s={((dv.get('workingset_refault_anon') or 0) / dtw):.1f} "
                    f"zswpout/s={((dv.get('zswpout') or 0) / dtw):.1f} zswpin/s={((dv.get('zswpin') or 0) / dtw):.1f}")
    out("\n(8) Durability syscalls (fsync/fdatasync/sync_file_range/msync/syncfs) and O_SYNC/O_DSYNC opens")
    sn = M.get("@sync_n", {})
    if sn:
        for k, v in sorted(sn.items(), key=lambda x: (x[0].split(",")[0] == "0", x[0].split(",")[0], -x[1]))[:24]:
            wn, comm, probe = k.split(",", 2)
            out(f"  {who.get(wn, wn):8} {v:7d}  {comm:18} {probe.rsplit(':', 1)[-1].replace('sys_enter_', '')}")
    else:
        out("  none observed")
    for k, h in sorted(H.get("@sync_us", {}).items()):
        out(f"  latency {who[k]:8} {_lat_line(h)}")
    for k, v in sorted(M.get("@osync_open", {}).items(), key=lambda x: -x[1])[:8]:
        wn, comm = k.split(",", 1)
        out(f"  O_SYNC/O_DSYNC openat: {who.get(wn, wn):8} {v:6d} {comm}")
    pre = [r for r in recs if r.get("k") == "preload"]
    if pre:
        out("  preload mappings (name match only, latest check):")
        seen = {}
        for r in pre:
            seen[r["ct"]] = r
        for ct, r in seen.items():
            for p in r["procs"]:
                flags = (f"eatmydata={p.get('eatmydata')} ksm-optin={p.get('ksm-optin')}"
                         if p.get("readable") else "unreadable")
                out(f"    {ct} pid={p['pid']} comm={p['comm']} {flags}")
    cpu = [r["bpftrace_cpu_pct"] for r in win if "bpftrace_cpu_pct" in r]
    if cpu:
        out(f"\ncollector overhead: bpftrace CPU avg={sum(cpu) / len(cpu):.2f}% max={max(cpu):.2f}% of one core; "
            f"helper errors={max(r.get('helper_errors', 0) for r in win)}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--out", required=True)
    r.add_argument("--container", action="append", default=[], help="exact container ID or name (repeatable)")
    r.add_argument("--interval", type=float, default=5.0)
    r.add_argument("--max-hours", type=float, default=8.0)
    r.add_argument("--cap-mb", type=float, default=200.0)
    r.add_argument("--devices", default=",".join(DEFAULT_DEVICES))
    r.add_argument("--primary", default=None)
    r.add_argument("--probe-dir", default=None, help="directory on the device to probe (omit: no active probe)")
    r.add_argument("--probe-period", type=float, default=10.0)
    r.add_argument("--docker-sock", default="/var/run/docker.sock")
    an = sub.add_parser("analyze")
    an.add_argument("--out", required=True)
    an.add_argument("--first")
    an.add_argument("--last")
    an.add_argument("--since")
    an.add_argument("--until")
    an.add_argument("--primary")
    an.add_argument("--mult", type=float, default=5.0)
    an.add_argument("--floor-ms", type=float, default=10.0)
    an.add_argument("--psi-full", type=float, default=5.0)
    an.add_argument("--gap", type=float, default=10.0)
    s = sub.add_parser("stop")
    s.add_argument("--out", required=True)
    s.add_argument("--bpf-name", default=DEFAULT_BPF_NAME, help="collector container name (for the docker stop hint)")
    a = ap.parse_args(argv)
    return {"run": cmd_run, "analyze": cmd_analyze, "stop": cmd_stop}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
