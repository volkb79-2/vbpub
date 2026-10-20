#!/usr/bin/env python3
"""Synthetic "game" workload for the zswap writeback-governor reproduction.

Runs INSIDE the VM-harness guest, inside the cgroup the caller put it in
(run-variant.sh moves it into /sys/fs/cgroup/zwg/game before exec).
Never run this on the live host: it allocates and churns memory on purpose.

What it models (see ../README.md):
  * a settled working set of anonymous, compressible, non-same-filled pages
    (about 2.5x with zstd), most of it never touched again ("cold");
  * a small hot subset touched at a fixed rate ("the game loop");
  * a small steady allocation churn in the workload's own threads (the game's
    own heap traffic), which is what makes the workload's threads enter
    reclaim themselves when free memory or memory.high is short;
  * a latency probe in a separate process of the same cgroup, which allocates
    a small region itself on every iteration (so that it stalls when reclaim
    or throttling hits an allocating thread of this cgroup) and reads a few
    hot pages. One JSON line per second: n, p99_ms, max_ms, over_200ms.
"""
import argparse
import json
import mmap
import multiprocessing
import os
import random
import signal
import sys
import threading
import time

PAGE = 4096
COMPRESSIBLE_PREFIX = 1600  # random bytes per page; rest zeros -> ~2.5x


def page_block() -> bytes:
    # Not same-filled (zswap special-cases those), compresses ~2.5x.
    return os.urandom(COMPRESSIBLE_PREFIX) + bytes(PAGE - COMPRESSIBLE_PREFIX)


def touch_all(m: mmap.mmap, size: int) -> None:
    for off in range(0, size, PAGE):
        m[off] = 1


def probe_main(path: str, period_ms: int, alloc_kib: int, stop_after: float) -> None:
    hot = mmap.mmap(-1, 4 * 1024 * 1024)
    touch_all(hot, len(hot))
    hot_pages = len(hot) // PAGE
    rng = random.Random(1)
    samples = []
    next_flush = time.monotonic() + 1.0
    end = time.monotonic() + stop_after
    with open(path, "a", buffering=1) as out:
        while time.monotonic() < end:
            t0 = time.monotonic()
            time.sleep(period_ms / 1000.0)
            a = mmap.mmap(-1, alloc_kib * 1024)
            touch_all(a, len(a))
            for _ in range(16):
                _ = hot[rng.randrange(hot_pages) * PAGE]
            a.close()
            dt_ms = (time.monotonic() - t0) * 1000.0 - period_ms
            samples.append(max(dt_ms, 0.0))
            now = time.monotonic()
            if now >= next_flush:
                samples.sort()
                n = len(samples)
                out.write(json.dumps({
                    "ts": time.time(), "n": n,
                    "p99_ms": samples[min(n - 1, int(n * 0.99))],
                    "max_ms": samples[-1],
                    "over_200ms": sum(1 for s in samples if s >= 200.0),
                }) + "\n")
                samples = []
                next_flush = now + 1.0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ws-mb", type=int, required=True, help="total working set")
    ap.add_argument("--hot-pct", type=int, default=10, help="percent of the WS touched regularly")
    ap.add_argument("--hot-hz", type=float, default=1.0)
    ap.add_argument("--churn-mb", type=int, default=8, help="own-thread allocation churn size")
    ap.add_argument("--churn-period-ms", type=int, default=500)
    ap.add_argument("--probe-file", required=True)
    ap.add_argument("--probe-period-ms", type=int, default=20)
    ap.add_argument("--probe-alloc-kib", type=int, default=256)
    ap.add_argument("--ready-file", required=True, help="created after the WS is filled")
    ap.add_argument("--max-seconds", type=float, default=86400.0)
    args = ap.parse_args()

    ws = args.ws_mb * 1024 * 1024
    m = mmap.mmap(-1, ws)
    try:
        m.madvise(mmap.MADV_NOHUGEPAGE)
    except (AttributeError, OSError) as e:  # recorded, not fatal: THP is also set off in setup-guest.sh
        print(f"workload: madvise(NOHUGEPAGE) failed: {e}", file=sys.stderr)
    block = page_block() * 256  # 1 MiB chunk
    for off in range(0, ws, len(block)):
        n = min(len(block), ws - off)
        m[off:off + n] = block[:n]

    probe = multiprocessing.Process(
        target=probe_main,
        args=(args.probe_file, args.probe_period_ms, args.probe_alloc_kib, args.max_seconds),
        daemon=True)
    probe.start()

    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())

    hot_bytes = ws * args.hot_pct // 100
    hot_pages = hot_bytes // PAGE

    def hot_loop() -> None:
        period = 1.0 / args.hot_hz
        while not stop.is_set():
            t = time.monotonic()
            for p in range(hot_pages):
                _ = m[p * PAGE]
            stop.wait(max(0.0, period - (time.monotonic() - t)))

    def churn_loop() -> None:
        size = args.churn_mb * 1024 * 1024
        while not stop.is_set():
            a = mmap.mmap(-1, size)
            touch_all(a, size)
            a.close()
            stop.wait(args.churn_period_ms / 1000.0)

    threads = [threading.Thread(target=hot_loop, name="hot", daemon=True),
               threading.Thread(target=churn_loop, name="churn", daemon=True)]
    for t in threads:
        t.start()

    with open(args.ready_file, "w") as f:
        f.write(f"{time.time()}\n")

    deadline = time.monotonic() + args.max_seconds
    while not stop.is_set() and time.monotonic() < deadline:
        stop.wait(1.0)
    probe.terminate()
    return 0


if __name__ == "__main__":
    sys.exit(main())
