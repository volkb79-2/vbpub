#!/usr/bin/env python3
"""1 Hz metrics + 10 Hz stack sampler for the zswap writeback-governor synthetic.

Runs INSIDE the VM-harness guest as root. Reads only /proc, /sys and debugfs.

Outputs in --out:
  samples.jsonl  one row per second (see ROW below)
  stacks.jsonl   every sampled game thread in uninterruptible sleep, with its
                 FULL kernel stack from /proc/<pid>/task/<tid>/stack (no depth
                 cut: the host's kstack(10) hid the reclaim caller; this does not)
  settled.json   written when the settle criterion holds (see README)
  burst.json     written when the burst criterion holds
  done           written POST_S seconds after the burst or at the cap

Criteria are implemented here ONLY to decide when to stop; analyze.py recomputes
them offline from samples.jsonl, and that recomputation is the authority.
"""
import argparse
import collections
import json
import os
import threading
import time

VMSTAT_KEYS = ("zswpout", "zswpin", "zswpwb", "pswpin", "pswpout", "pgscan_kswapd",
               "pgscan_direct", "pgsteal_kswapd", "pgsteal_direct", "allocstall_normal",
               "allocstall_dma32", "allocstall_movable", "workingset_refault_anon",
               "pgmajfault")
DEBUGFS_KEYS = ("pool_total_size", "stored_pages", "written_back_pages", "reject_reclaim_fail",
                "pool_limit_hit")
MEMSTAT_KEYS = ("anon", "zswap", "zswapped", "zswpout", "zswpwb", "workingset_refault_anon")


def read_kv(path):
    out = {}
    try:
        with open(path) as f:
            for line in f:
                p = line.split()
                if len(p) >= 2:
                    try:
                        out[p[0]] = int(p[1])
                    except ValueError:
                        pass
    except OSError:
        pass
    return out


def read_int(path, default=None):
    try:
        with open(path) as f:
            v = f.read().strip()
        return int(v) if v != "max" else -1
    except (OSError, ValueError):
        return default


def read_text(path):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return None


def read_psi(path):
    res = {}
    try:
        with open(path) as f:
            for line in f:
                p = line.split()
                kv = dict(x.split("=") for x in p[1:])
                res[p[0]] = float(kv["avg10"])
    except (OSError, KeyError, ValueError):
        pass
    return res


def disk_written_sectors(dev):
    try:
        with open("/proc/diskstats") as f:
            for line in f:
                p = line.split()
                if p[2] == dev:
                    return int(p[9])
    except (OSError, IndexError, ValueError):
        pass
    return None


def swap_used_kb():
    used = 0
    try:
        with open("/proc/swaps") as f:
            next(f)
            for line in f:
                p = line.split()
                used += int(p[3])
    except (OSError, StopIteration, ValueError, IndexError):
        pass
    return used


def row(cg, bg, dbg, disk):
    vm = read_kv("/proc/vmstat")
    d = {k: read_int(os.path.join(dbg, k)) for k in DEBUGFS_KEYS}
    ms = read_kv(os.path.join(cg, "memory.stat"))
    ev = read_kv(os.path.join(cg, "memory.events"))
    zswapped_b = ms.get("zswapped")
    bms = read_kv(os.path.join(bg, "memory.stat")) if bg else {}
    return {
        "ts": time.time(),
        # The game cgroup's own counters are what settle/burst use: the global debugfs counters
        # also count any other cgroup's pages (the ballast, if it were ever swapped).
        "game_stored_pages": None if zswapped_b is None else zswapped_b // 4096,
        "game_wb_pages": ms.get("zswpwb"),
        "bg_zswapped_bytes": bms.get("zswapped"),
        "bg_swap_current": read_int(os.path.join(bg, "memory.swap.current")) if bg else None,
        "mono": time.monotonic(),
        "vm": {k: vm.get(k) for k in VMSTAT_KEYS},
        "zswap": d,
        "game_current": read_int(os.path.join(cg, "memory.current")),
        "game_stat": {k: ms.get(k) for k in MEMSTAT_KEYS},
        "game_events": {k: ev.get(k) for k in ("low", "high", "max", "oom")},
        "game_psi": read_psi(os.path.join(cg, "memory.pressure")),
        "game_cfg": {k: read_text(os.path.join(cg, k)) for k in
                     ("memory.min", "memory.low", "memory.high")},
        "global_psi": read_psi("/proc/pressure/memory"),
        "memfree_kb": read_kv("/proc/meminfo").get("MemFree:"),
        "swap_used_kb": swap_used_kb(),
        "disk_wr_sectors": disk_written_sectors(disk),
    }


def kswapd_tids():
    out = []
    for d in os.listdir("/proc"):
        if d.isdigit():
            try:
                with open(f"/proc/{d}/comm") as f:
                    if f.read().startswith("kswapd"):
                        out.append(int(d))
            except OSError:
                pass
    return out


def dump_stack(tid, kind, out):
    try:
        with open(f"/proc/{tid}/status") as f:
            st = f.read()
        state = [l for l in st.splitlines() if l.startswith("State:")][0].split()[1]
        if state != "D":
            return
        pid = int([l for l in st.splitlines() if l.startswith("Tgid:")][0].split()[1])
        with open(f"/proc/{pid}/task/{tid}/stack") as f:
            stack = [x.strip() for x in f.read().splitlines()]
        with open(f"/proc/{tid}/comm") as f:
            comm = f.read().strip()
        out.write(json.dumps({"ts": time.time(), "kind": kind, "tid": tid, "comm": comm,
                              "stack": stack}) + "\n")
    except (OSError, IndexError, ValueError):
        return


def stack_sampler(cg, out_path, stop):
    # /proc/<pid>/task/<tid>/stack needs root; CONFIG_STACKTRACE is assumed (checked by setup-guest.sh).
    kswapds = kswapd_tids()   # kernel threads are stable for the run; H3 needs to see writeback move here
    with open(out_path, "a", buffering=1) as out:
        while not stop.is_set():
            t0 = time.monotonic()
            try:
                with open(os.path.join(cg, "cgroup.threads")) as f:
                    tids = [int(x) for x in f.read().split()]
            except OSError:
                tids = []
            for tid in tids:
                dump_stack(tid, "game", out)
            for tid in kswapds:
                dump_stack(tid, "kswapd", out)
            stop.wait(max(0.0, 0.1 - (time.monotonic() - t0)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--cg", required=True, help="the game cgroup directory")
    ap.add_argument("--bg-cg", default=None, help="the ballast cgroup (recorded; analyze.py invalidates a run whose ballast was swapped)")
    ap.add_argument("--debugfs-zswap", default="/sys/kernel/debug/zswap")
    ap.add_argument("--disk", default="vda")
    ap.add_argument("--fill-done-file", required=True,
                    help="settle detection starts only after this file exists")
    ap.add_argument("--burst-frac", type=float, default=0.25)
    ap.add_argument("--burst-window-s", type=int, default=60)
    ap.add_argument("--settle-window-s", type=int, default=60)
    ap.add_argument("--settle-drift-frac", type=float, default=0.01)
    ap.add_argument("--settle-max-wb-frac", type=float, default=0.02,
                    help="max written-back pages in the settle window, as a fraction of stored pages")
    ap.add_argument("--post-s", type=int, default=300,
                    help="seconds of data kept after the burst window; analyze.py reads [T_burst, T_burst+360]")
    ap.add_argument("--cap-s", type=int, required=True, help="max seconds after settle")
    ap.add_argument("--max-fill-wait-s", type=int, required=True,
                    help="give up (exit 4) if never settled this long after fill-done")
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    stop = threading.Event()
    th = threading.Thread(target=stack_sampler,
                          args=(args.cg, os.path.join(args.out, "stacks.jsonl"), stop), daemon=True)
    th.start()

    hist = collections.deque(maxlen=7200)  # (mono, stored, wb)
    t_fill_done = None
    settled_at = None
    settled_stored = None
    burst_at = None
    with open(os.path.join(args.out, "samples.jsonl"), "a", buffering=1) as sf:
        while True:
            t0 = time.monotonic()
            r = row(args.cg, args.bg_cg, args.debugfs_zswap, args.disk)
            sf.write(json.dumps(r) + "\n")
            stored = r["game_stored_pages"]
            wb = r["game_wb_pages"]
            if stored is None or wb is None or r["zswap"]["stored_pages"] is None:
                raise SystemExit("sampler: game memory.stat zswapped/zswpwb or the zswap debugfs counters "
                                 "are unreadable; is debugfs mounted and zswap enabled?")
            hist.append((r["mono"], stored, wb))
            if t_fill_done is None and os.path.exists(args.fill_done_file):
                t_fill_done = r["mono"]
            now = r["mono"]
            if t_fill_done is not None and settled_at is None:
                win = [h for h in hist if h[0] >= now - args.settle_window_s]
                if win and win[0][0] <= now - args.settle_window_s + 2:
                    s0, s1 = win[0][1], win[-1][1]
                    drift = abs(s1 - s0) / max(s1, 1)
                    dwb = win[-1][2] - win[0][2]
                    if stored > 0 and drift <= args.settle_drift_frac and dwb <= args.settle_max_wb_frac * max(stored, 1):
                        settled_at, settled_stored = now, stored
                        with open(os.path.join(args.out, "settled.json"), "w") as f:
                            json.dump({"mono": now, "ts": r["ts"], "stored_pages": stored}, f)
                if settled_at is None and now - t_fill_done > args.max_fill_wait_s:
                    open(os.path.join(args.out, "never-settled"), "w").close()
                    stop.set()
                    return 4
            if settled_at is not None and burst_at is None:
                w = [h for h in hist if h[0] >= now - args.burst_window_s]
                if w and w[0][0] <= now - args.burst_window_s + 2:
                    if w[-1][2] - w[0][2] >= args.burst_frac * settled_stored:
                        burst_at = w[0][0]
                        with open(os.path.join(args.out, "burst.json"), "w") as f:
                            json.dump({"window_start_mono": w[0][0],
                                       "t_burst_s": w[0][0] - settled_at}, f)
            done = False
            if burst_at is not None and now - burst_at >= args.burst_window_s + args.post_s:
                done = True
            if settled_at is not None and now - settled_at >= args.cap_s:
                done = True
            if done:
                open(os.path.join(args.out, "done"), "w").close()
                stop.set()
                return 0
            time.sleep(max(0.0, 1.0 - (time.monotonic() - t0)))


if __name__ == "__main__":
    raise SystemExit(main())
