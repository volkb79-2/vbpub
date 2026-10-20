#!/usr/bin/env python3
"""Constant low background global memory pressure (models the overcommitted host).

Runs INSIDE the VM-harness guest, in cgroup /sys/fs/cgroup/zwg/bg.

Two parts:
  ballast: allocates, touches and mlock()s exactly --ballast-mb of anonymous
           memory and holds it. The caller sizes it so that the guest cannot hold the
           game's whole working set next to it, so global reclaim has to push
           the game's cold pages into zswap. The caller gives the bg cgroup a
           memory.min equal to the ballast so global reclaim does not take it
           back: the only reclaimable anon left in the guest is the game's.
  churn:   every --period-s, allocates --churn-mb, touches it, frees it. The
           dip crosses the low watermark, which wakes kswapd (and, if deep
           enough, sends the allocating thread into direct reclaim). The
           depth is sized well below the host's real swing; it is a constant
           low pressure, not a load test.

Exit by SIGTERM. Never run on the live host.
"""
import argparse
import os
import ctypes
import mmap
import signal
import sys
import threading
import time

PAGE = 4096


def require_harness_guest() -> None:
    """Refuse unless run-synthetic.sh marked this guest (same check as guest/lib-guard.sh)."""
    run, nonce = os.environ.get("ZWG_RUN", ""), os.environ.get("ZWG_NONCE", "")
    want = f"zwg-harness-guest run={run} nonce={nonce}"
    try:
        st = os.stat("/etc/zwg-harness-guest")
        with open("/etc/zwg-harness-guest") as f:
            got = f.read().strip()
    except OSError:
        got, st = None, None
    if not run or not nonce or st is None or st.st_uid != 0 or got != want:
        print("zwg-guard: REFUSING: not a guest marked by run-synthetic.sh", file=sys.stderr)
        raise SystemExit(97)


def memfree_mb() -> int:
    with open("/proc/meminfo") as f:
        for line in f:
            if line.startswith("MemFree:"):
                return int(line.split()[1]) // 1024
    raise SystemExit("bgpressure: MemFree not found in /proc/meminfo")


def main() -> int:
    require_harness_guest()
    ap = argparse.ArgumentParser()
    ap.add_argument("--ballast-mb", type=int, required=True)
    ap.add_argument("--churn-mb", type=int, required=True)
    ap.add_argument("--period-s", type=float, default=5.0)
    ap.add_argument("--ready-file", required=True)
    args = ap.parse_args()

    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())

    libc = ctypes.CDLL(None, use_errno=True)
    chunks = []
    step = 16 * 1024 * 1024
    for _ in range(args.ballast_mb // 16):
        if stop.is_set():
            return 0
        c = mmap.mmap(-1, step)
        for off in range(0, step, PAGE):
            c[off] = 1
        # Non-reclaimable: mlock (unevictable) so the ballast can never be pushed into zswap or swap,
        # whatever the memory.min protection chain does. Keep the ctypes view alive with the mmap.
        view = (ctypes.c_char * step).from_buffer(c)
        if libc.mlock(ctypes.c_void_p(ctypes.addressof(view)), ctypes.c_size_t(step)) != 0:
            print(f"bgpressure: mlock failed (errno {ctypes.get_errno()}); ballast would be reclaimable",
                  file=sys.stderr)
            return 3
        chunks.append((c, view))
        time.sleep(0.05)
    with open(args.ready_file, "w") as f:
        f.write(f"{time.time()} ballast_mb={len(chunks) * 16} memfree_mb={memfree_mb()}\n")

    size = args.churn_mb * 1024 * 1024
    while not stop.is_set():
        a = mmap.mmap(-1, size)
        for off in range(0, size, PAGE):
            a[off] = 1
        a.close()
        stop.wait(args.period_s)
    return 0


if __name__ == "__main__":
    sys.exit(main())
