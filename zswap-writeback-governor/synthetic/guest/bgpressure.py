#!/usr/bin/env python3
"""Constant low background global memory pressure (models the overcommitted host).

Runs INSIDE the VM-harness guest, in cgroup /sys/fs/cgroup/zwg/bg.

Two parts:
  ballast: allocates and touches exactly --ballast-mb of anonymous memory and
           holds it. The caller sizes it so that the guest cannot hold the
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
import mmap
import signal
import sys
import threading
import time

PAGE = 4096


def memfree_mb() -> int:
    with open("/proc/meminfo") as f:
        for line in f:
            if line.startswith("MemFree:"):
                return int(line.split()[1]) // 1024
    raise SystemExit("bgpressure: MemFree not found in /proc/meminfo")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ballast-mb", type=int, required=True)
    ap.add_argument("--churn-mb", type=int, required=True)
    ap.add_argument("--period-s", type=float, default=5.0)
    ap.add_argument("--ready-file", required=True)
    args = ap.parse_args()

    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())

    chunks = []
    step = 16 * 1024 * 1024
    for _ in range(args.ballast_mb // 16):
        if stop.is_set():
            return 0
        c = mmap.mmap(-1, step)
        for off in range(0, step, PAGE):
            c[off] = 1
        chunks.append(c)
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
