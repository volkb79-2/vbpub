#!/usr/bin/env python3
# mdt host-setup — systemd-cgtop-like table with memory.min/memory.low as
# columns.
#
# Neither `systemd-cgtop` nor `top` exposes memory.min/memory.low (the
# reclaim-protection floors) — only memory.current. Under a host running
# several tiers with overlapping protection claims, "how close is each
# slice to its own floor/ceiling right now" is exactly the question those
# tools can't answer. This walks the cgroup v2 tree directly and prints
# both, plus %FLOOR (current/max(min,low)) and %HIGH (current/high), so
# you can see at a glance which slices are near/over their protection or
# their throttle point.
#
# Companion to mdt-slice-audit.py (which flags no-op protection caused by an
# unprotected ancestor) — this is a live/one-shot viewer, not an auditor.
#
# Usage:
#   sudo python3 cgtop-protection.py [--root /sys/fs/cgroup] [--depth N] [--watch SECS]

import argparse
import os
import time
from pathlib import Path


def read(p: Path, default="0"):
    try:
        v = p.read_text().strip()
    except OSError:
        return default
    return v


def to_bytes(v: str):
    if v in ("", "max"):
        return None  # unlimited / unset
    try:
        return int(v)
    except ValueError:
        return None


def human(n):
    if n is None:
        return "max"
    for unit in ("B", "K", "M", "G", "T"):
        if abs(n) < 1024.0:
            return f"{n:.0f}{unit}" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024.0
    return f"{n:.1f}P"


def walk(root: Path, max_depth: int):
    rows = []

    def rec(d: Path, depth: int):
        cur_f = d / "memory.current"
        if cur_f.exists():
            cur = to_bytes(read(cur_f)) or 0
            mn = to_bytes(read(d / "memory.min"))
            low = to_bytes(read(d / "memory.low"))
            high = to_bytes(read(d / "memory.high"))
            mx = to_bytes(read(d / "memory.max"))
            swap = to_bytes(read(d / "memory.swap.current"))
            rel = "/" + str(d.relative_to(root)) if d != root else "/"
            # surface nodes that are either protected, near their high, or leaves with real usage
            interesting = (mn or low) or (high and cur and cur > 0.5 * high) or (cur > 20 * 1024 * 1024)
            if interesting:
                floor = max(mn or 0, low or 0)
                pct_floor = (100.0 * cur / floor) if floor else None
                pct_high = (100.0 * cur / high) if (high and cur is not None) else None
                rows.append((rel, cur, mn, low, high, mx, swap, pct_floor, pct_high))
        if depth < max_depth:
            try:
                for child in sorted(d.iterdir()):
                    if child.is_dir():
                        rec(child, depth + 1)
            except OSError:
                pass

    rec(root, 0)
    return rows


def render(rows):
    hdr = f"{'CGROUP':<70} {'CUR':>8} {'MIN':>8} {'LOW':>8} {'HIGH':>8} {'MAX':>8} {'SWAP':>8} {'%FLOOR':>7} {'%HIGH':>7}"
    print(hdr)
    print("-" * len(hdr))
    for rel, cur, mn, low, high, mx, swap, pf, ph in sorted(rows, key=lambda r: -r[1]):
        pf_s = f"{pf:5.0f}%" if pf is not None else "   -  "
        ph_s = f"{ph:5.0f}%" if ph is not None else "   -  "
        print(f"{rel:<70} {human(cur):>8} {human(mn):>8} {human(low):>8} {human(high):>8} "
              f"{human(mx):>8} {human(swap):>8} {pf_s:>7} {ph_s:>7}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="/sys/fs/cgroup")
    ap.add_argument("--depth", type=int, default=4)
    ap.add_argument("--watch", type=float, default=0)
    args = ap.parse_args()
    root = Path(args.root)
    if args.watch:
        try:
            while True:
                os.system("clear")
                print(f"cgtop-protection — {time.strftime('%H:%M:%S')} (Ctrl-C to stop)\n")
                render(walk(root, args.depth))
                time.sleep(args.watch)
        except KeyboardInterrupt:
            pass
    else:
        render(walk(root, args.depth))


if __name__ == "__main__":
    main()
