#!/usr/bin/env python3
"""Replay recorded native-R2 candidates under different dispatch policies (offline, stdlib only).

Input: an assay progress stream (JSON lines; `candidate` events with candidate_id, candidate_index, path,
elapsed_seconds, peak_rss_bytes). The last event per candidate id is used. Each candidate is modelled as
occupying its recorded peak RSS for its whole recorded elapsed time (a conservative step model).

Policies: file (canonical order), lpt (longest predicted first), tem (two-ended matching), tem-lpt (two-ended
matching with longest-first among equals). Predictions: `oracle` = the candidate's own recorded values (stands for
"the previous profile of the same candidate id"), `file` = per-file medians (the fallback when the id is new).

Metrics: makespan, max / time-weighted p95 / p99 of the running RSS sum, tail idle worker-seconds
(sum over workers of makespan - that worker's last finish), and the admission bound H + (N-1)*normal_p95.

Usage: sched-sim.py PROGRESS.jsonl [--workers 2-6] [--budget-mib X]
"""
import argparse
import collections
import heapq
import json
import statistics


def load(path):
    last = {}
    with open(path, encoding="utf-8") as stream:
        for line in stream:
            if not line.strip():
                continue
            event = json.loads(line)
            if event.get("event") != "candidate":
                continue
            if not isinstance(event.get("elapsed_seconds"), (int, float)):
                continue
            if not isinstance(event.get("peak_rss_bytes"), int):
                continue
            last[event["candidate_id"]] = event
    cands = sorted(last.values(), key=lambda e: e["candidate_index"])
    return [
        {"id": e["candidate_id"], "idx": e["candidate_index"], "file": e["path"],
         "d": float(e["elapsed_seconds"]), "r": e["peak_rss_bytes"] / 2**20}
        for e in cands
    ]


def pct(values, q):
    values = sorted(values)
    return values[min(len(values) - 1, int(q * len(values)))]


def classes(rss_values):
    """Data-derived thresholds: the mode is the most populated 50 MiB bucket +- one bucket."""
    buckets = collections.Counter(int(r // 50) for r in rss_values)
    mode_bucket = buckets.most_common(1)[0][0]
    mode = [r for r in rss_values if mode_bucket - 1 <= int(r // 50) <= mode_bucket + 1]
    mode_median = statistics.median(mode)
    mode_p95 = pct(mode, 0.95)
    return 1.5 * mode_p95, 0.5 * mode_median, mode_median, mode_p95


def predictions(cands, mode):
    if mode == "oracle":
        return {c["id"]: (c["d"], c["r"]) for c in cands}
    by_file = collections.defaultdict(list)
    for c in cands:
        by_file[c["file"]].append(c)
    med = {f: (statistics.median(x["d"] for x in v), statistics.median(x["r"] for x in v))
           for f, v in by_file.items()}
    return {c["id"]: med[c["file"]] for c in cands}


def simulate(cands, n, policy, pred, hi_t, lo_t, budget=None):
    """Event-driven list scheduling; returns metrics computed from ACTUAL d and r."""
    by_id = {c["id"]: c for c in cands}
    cls = {}
    for c in cands:
        pr = pred[c["id"]][1]
        cls[c["id"]] = "H" if pr > hi_t else ("L" if pr < lo_t else "N")
    if policy == "file":
        queue = [c["id"] for c in cands]
    elif policy == "lpt":
        queue = [c["id"] for c in sorted(cands, key=lambda c: (-pred[c["id"]][0], c["idx"]))]
    else:  # tem / tem-lpt: per-class queues; highs first, at most one high running
        key = (lambda c: (-pred[c["id"]][0], c["idx"])) if policy == "tem-lpt" else (lambda c: c["idx"])
        q = {k: [c["id"] for c in sorted(cands, key=key) if cls[c["id"]] == k] for k in "HNL"}

    t = 0.0
    running = []  # heap (end, id)
    free = n
    worker_last = []  # finish times
    timeline = []  # (time, delta_rss)
    starts = 0

    def running_pred_sum():
        return sum(pred[i][1] for _, i in running)

    def pick():
        if policy in ("file", "lpt"):
            if not queue:
                return None
            nxt = queue[0]
            if budget and running and running_pred_sum() + pred[nxt][1] > budget:
                return None
            return queue.pop(0)
        high_running = any(cls[i] == "H" for _, i in running)
        if high_running:
            order = ["L", "N"]            # a slot freed while a high runs: a low first
        else:
            order = ["H", "N", "L"]       # a queued high starts as soon as none runs; lows fill the tail
        for k in order:
            if q[k]:
                nxt = q[k][0]
                if budget and running and running_pred_sum() + pred[nxt][1] > budget:
                    if k == "H":
                        return None   # the high slot: drain until the queued high fits (no starvation)
                    continue
                return q[k].pop(0)
        # tail: only highs left while one runs -> wait (the at-most-one-high rule)
        return None

    def remaining():
        if policy in ("file", "lpt"):
            return bool(queue)
        return any(q[k] for k in "HNL")

    while remaining() or running:
        while free > 0 and remaining():
            nxt = pick()
            if nxt is None:
                break
            c = by_id[nxt]
            heapq.heappush(running, (t + c["d"], nxt))
            timeline.append((t, c["r"]))
            free -= 1
            starts += 1
        if not running:
            break
        end, done = heapq.heappop(running)
        t = end
        timeline.append((t, -by_id[done]["r"]))
        worker_last.append(t)
        free += 1
    makespan = t
    # time-weighted distribution of the running RSS sum
    timeline.sort()
    level = 0.0
    prev = 0.0
    weighted = []
    peak = 0.0
    for time, delta in timeline:
        if time > prev:
            weighted.append((level, time - prev))
        level += delta
        peak = max(peak, level)
        prev = time
    weighted.sort()
    total = sum(w for _, w in weighted) or 1.0

    def tw(qv):
        acc = 0.0
        for value, w in weighted:
            acc += w
            if acc >= qv * total:
                return value
        return weighted[-1][0]

    last_per_worker = sorted(worker_last)[-n:]
    idle = sum(makespan - x for x in last_per_worker)
    return makespan, peak, tw(0.95), tw(0.99), idle


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("progress")
    ap.add_argument("--workers", default="2-6")
    ap.add_argument("--budget-mib", type=float, default=None)
    args = ap.parse_args()
    lo_n, hi_n = (int(x) for x in args.workers.split("-"))
    cands = load(args.progress)
    rss = [c["r"] for c in cands]
    dur = [c["d"] for c in cands]
    hi_t, lo_t, mode_med, mode_p95 = classes(rss)
    n_h = sum(r > hi_t for r in rss)
    n_l = sum(r < lo_t for r in rss)
    low_d = [c["d"] for c in cands if c["r"] < lo_t]
    norm = [c for c in cands if lo_t <= c["r"] <= hi_t]
    high = [c for c in cands if c["r"] > hi_t]
    print(f"{args.progress}: {len(cands)} candidates, work {sum(dur)/3600:.2f} h, "
          f"RSS p50 {statistics.median(rss):.0f} p95 {pct(rss,.95):.0f} max {max(rss):.0f} MiB")
    print(f"mode median {mode_med:.0f} MiB, mode p95 {mode_p95:.0f} MiB -> high > {hi_t:.0f} ({n_h}), "
          f"low < {lo_t:.0f} ({n_l})")
    print(f"elapsed median: low {statistics.median(low_d) if low_d else float('nan'):.0f} s, "
          f"normal {statistics.median(c['d'] for c in norm):.0f} s, "
          f"high {statistics.median(c['d'] for c in high) if high else float('nan'):.0f} s")
    normal_p95 = pct([c["r"] for c in norm], 0.95)
    h_max = max([c["r"] for c in high], default=normal_p95)
    print("| pred | N | policy | makespan s | lower bound s | RSS sum max | p99 | p95 | tail idle w-s | "
          "bound H+(N-1)*normal_p95 | N*max |")
    print("|---|---|---|---|---|---|---|---|---|---|---|")
    for pmode in ("oracle", "file"):
        pred = predictions(cands, pmode)
        for n in range(lo_n, hi_n + 1):
            lb = max(sum(dur) / n, max(dur))
            budget = args.budget_mib
            for policy in ("file", "lpt", "tem", "tem-lpt"):
                mk, peak, p95, p99, idle = simulate(cands, n, policy, pred, hi_t, lo_t, budget)
                print(f"| {pmode} | {n} | {policy} | {mk:.0f} | {lb:.0f} | {peak:.0f} | {p99:.0f} | {p95:.0f} | "
                      f"{idle:.0f} | {h_max + (n-1)*normal_p95:.0f} | {n*max(rss):.0f} |")


if __name__ == "__main__":
    main()
