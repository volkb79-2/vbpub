#!/usr/bin/env python3
"""Offline analysis of zswap writeback-governor synthetic results.

    analyze.py <results-dir>

<results-dir> holds one directory per run, named <variant>-r<N>, each with the files
run-variant.sh produced (meta.json, samples.jsonl, stacks.jsonl, probe.jsonl,
settled.json). Pure stdlib; reads files only.

This script is the AUTHORITY for every definition in ../README.md. The sampler's
online copy of the burst/settle criteria only decides when a run may stop.
"""
import collections
import json
import os
import statistics
import sys

BURST_FRAC = 0.25
BURST_WINDOW_S = 60
MIN_T_BURST_S = 120.0          # a "burst after aging" must come this long after settle
STALL_WINDOW_S = 360           # [T_burst, T_burst+360]: the 60 s burst window plus the sampler's 300 s post window; also the caller window
STALL_BASE_WINDOW_S = 120
CALLER_MIN_SAMPLES = 5
CALLER_MODAL_FRAC = 0.5

VM_ALLOCSTALL = ("allocstall_normal", "allocstall_dma32", "allocstall_movable")


def jl(path):
    if not os.path.exists(path):
        return []
    with open(path) as f:
        return [json.loads(x) for x in f if x.strip()]


def frame_names(stack):
    names = set()
    for line in stack:
        tok = line.split("]")[-1].strip() if "]" in line else line.strip()
        names.add(tok.split("+")[0].split()[0] if tok else "")
        # "foo.cold+0x.." / "foo.isra.0+0x.." keep the bare symbol as well
        names.add(tok.split("+")[0].split(".")[0].split()[0] if tok else "")
    names.discard("")
    return names


def classify(stack):
    """Name the reclaim entry point from a FULL kernel stack (a /proc/<tid>/stack dump).
    Order matters: memory.high return-to-user reclaim also contains try_to_free_mem_cgroup_pages."""
    n = frame_names(stack)
    if "shrink_memcg_cb" not in n:
        return None
    if n & {"__mem_cgroup_handle_over_high", "mem_cgroup_handle_over_high", "reclaim_high"}:
        return "memcg-high"
    if "try_to_free_pages" in n:
        return "global-direct"
    if "try_to_free_mem_cgroup_pages" in n:
        return "memcg-charge-or-max"
    if "zswap_store" in n and "shrink_memcg" in n:
        return "zswap-max-store"
    if n & {"balance_pgdat", "kswapd"}:
        return "kswapd"
    if "shrink_worker" in n:
        return "zswap-shrink-worker"
    return "unknown"


def analyse_run(d):
    meta = json.load(open(os.path.join(d, "meta.json"))) if os.path.exists(os.path.join(d, "meta.json")) else {}
    samples = jl(os.path.join(d, "samples.jsonl"))
    res = {"dir": os.path.basename(d), "variant": meta.get("variant"), "meta": meta,
           "valid": False, "note": "", "did_not_settle": False}
    if not meta:
        res["note"] = "no meta.json (run died before finishing)"
        return res
    if meta.get("outcome") == "did-not-settle":
        res["did_not_settle"] = True
        res["note"] = "did not settle (pool kept moving after the fill); neither burst nor no-burst"
        return res
    if not os.path.exists(os.path.join(d, "settled.json")) or not samples:
        res["note"] = "no settle marker or no samples"
        return res
    if any((x.get("bg_zswapped_bytes") or 0) > 0 or (x.get("bg_swap_current") or 0) > 0 for x in samples):
        res["note"] = "ballast was swapped (bg zswapped/swap.current > 0); game counters are still used but the run's pressure model is broken"
        return res
    settled = json.load(open(os.path.join(d, "settled.json")))
    t_settle = settled["mono"]
    stored0 = settled["stored_pages"]
    series = [(s["mono"], s["game_wb_pages"], s) for s in samples
              if s.get("game_wb_pages") is not None]
    after = [x for x in series if x[0] >= t_settle]
    res["stored_at_settle"] = stored0
    res["observed_s_after_settle"] = (after[-1][0] - t_settle) if after else 0
    # burst: earliest t >= settle with wb(t+60) - wb(t) >= 25 % of stored at settle
    t_burst = None
    j = 0
    for i, (t, wb, _) in enumerate(after):
        while j < len(after) and after[j][0] < t + BURST_WINDOW_S:
            j += 1
        if j >= len(after):
            break
        if after[j][1] - wb >= BURST_FRAC * stored0:
            t_burst = t - t_settle
            break
    res["t_burst_s"] = t_burst
    res["burst"] = t_burst is not None

    probes = jl(os.path.join(d, "probe.jsonl"))
    stall_ms = meta.get("stall_ms", 200)

    def stall_stats(t0, t1):
        # probe rows carry wall-clock ts; map settle (mono) to ts via the settled marker
        a = settled["ts"] + t0
        b = settled["ts"] + t1
        rows = [p for p in probes if a <= p["ts"] < b]
        return (sum(1 for p in rows if p["max_ms"] >= stall_ms),
                max([p["max_ms"] for p in rows], default=0.0), len(rows))

    sb = stall_stats(0, STALL_BASE_WINDOW_S)
    res["baseline_stall_s"], res["baseline_stall_max_ms"] = sb[0], sb[1]
    if t_burst is not None:
        st = stall_stats(t_burst, t_burst + STALL_WINDOW_S)
        res["stall_s"], res["stall_max_ms"], res["stall_rows"] = st
    else:
        res["stall_s"] = res["stall_max_ms"] = None

    # global-pressure counters over [settle, burst or end]
    end_t = (t_settle + t_burst) if t_burst is not None else after[-1][0]
    win = [x for x in series if t_settle <= x[0] <= end_t]
    if win:
        a, b = win[0][2]["vm"], win[-1][2]["vm"]
        res["allocstall_delta"] = sum((b[k] or 0) - (a[k] or 0) for k in VM_ALLOCSTALL)
        res["pgscan_direct_delta"] = (b["pgscan_direct"] or 0) - (a["pgscan_direct"] or 0)
        res["pgscan_kswapd_delta"] = (b["pgscan_kswapd"] or 0) - (a["pgscan_kswapd"] or 0)
        res["game_high_events_delta"] = (b_ev(win[-1][2], "high") - b_ev(win[0][2], "high"))
    # caller of the writeback: from FULL stacks sampled during the burst window
    callers = collections.Counter()
    kswapd_n = 0
    if t_burst is not None:
        lo = settled["ts"] + t_burst
        hi = lo + STALL_WINDOW_S
        for st in jl(os.path.join(d, "stacks.jsonl")):
            if lo <= st["ts"] < hi:
                c = classify(st["stack"])
                if not c:
                    continue
                if st.get("kind") == "kswapd":
                    kswapd_n += 1
                else:
                    callers[c] += 1
    total = sum(callers.values())
    res["caller_counts"] = dict(callers)
    if total < CALLER_MIN_SAMPLES:
        res["caller"] = "unmeasured"
    else:
        name, n = callers.most_common(1)[0]
        res["caller"] = name if n / total >= CALLER_MODAL_FRAC else "mixed"
    res["shrink_memcg_cb_dstate_samples"] = total
    res["kswapd_shrink_samples"] = kswapd_n
    res["valid"] = True
    return res


def b_ev(sample, key):
    return (sample["game_events"].get(key) or 0)


def med(vals):
    return statistics.median(vals) if vals else None


def tburst_or_cap(r):
    # a run with no burst counts as "at least the observed time" for medians
    return r["t_burst_s"] if r["burst"] else r["observed_s_after_settle"]


def verdict_h1(runs):
    base = [r for r in runs.get("base", []) if r["valid"]]
    idle = [r for r in runs.get("idle", []) if r["valid"]]
    nosh = [r for r in runs.get("noshrink", []) if r["valid"]]
    if len(base) < 3 or not idle or not nosh:
        return "INCOMPLETE", "needs 3 valid base runs, 1 valid idle, 1 valid noshrink"
    bursts = [r for r in base if r["burst"]]
    early = [r["t_burst_s"] for r in bursts if r["t_burst_s"] < MIN_T_BURST_S]
    late = [r["t_burst_s"] for r in bursts if r["t_burst_s"] >= MIN_T_BURST_S]
    if idle[0]["burst"]:
        return "FAIL", "idle control (no pressure after fill) burst: time alone triggers it, which the source says cannot happen; investigate"
    if nosh[0]["burst"]:
        return "FAIL", "noshrink control burst: the shrinker is not the writer"
    if len(bursts) < 2:
        return "FAIL", f"only {len(bursts)} of 3 base runs burst within the cap"
    if early:
        return "INCONCLUSIVE", f"a base run burst earlier than {MIN_T_BURST_S:.0f}s after settle ({sorted(round(x) for x in early)}): settle detection suspect"
    # all bursts are late here, and there are >= 2 of them
    if max(late) / min(late) <= 2.0:
        return "PASS", f"{len(late)} of 3 bursts at {sorted(round(x) for x in late)} s, spread <= 2x; idle and noshrink controls quiet"
    return "INCONCLUSIVE", f"bursts happen but spread > 2x: {sorted(round(x) for x in late)}"


def verdict_h2(runs):
    base = [r for r in runs.get("base", []) if r["valid"]]
    out = {}
    base_bursts = sum(r["burst"] for r in base)
    for name in ("squeeze", "squeeze-minlow"):
        sq = [r for r in runs.get(name, []) if r["valid"]]
        if len(base) < 3 or len(sq) < 3:
            out[name] = ("INCOMPLETE", "needs 3 valid base runs and 3 valid runs of this variant")
            continue
        if base_bursts < 2:
            out[name] = ("INAPPLICABLE", f"base burst in only {base_bursts} of 3 runs, so there is no reference time to advance (H1 did not PASS)")
            continue
        mb, ms = med([tburst_or_cap(r) for r in base]), med([tburst_or_cap(r) for r in sq])
        nb = sum(r["burst"] for r in sq)
        if ms <= 0.5 * mb and nb >= 2:
            out[name] = ("PASS", f"median to burst {ms:.0f}s vs base {mb:.0f}s, {nb}/3 burst")
        elif ms >= 0.8 * mb:
            out[name] = ("FAIL", f"median to burst {ms:.0f}s vs base {mb:.0f}s: not earlier")
        else:
            out[name] = ("INCONCLUSIVE", f"median {ms:.0f}s vs base {mb:.0f}s, {nb}/3 burst")
    return out


def verdict_h3(runs):
    base = [r for r in runs.get("base", []) if r["valid"] and r["burst"]]
    w2 = [r for r in runs.get("wsf200", []) if r["valid"]]
    if len(base) < 2 or len(w2) < 3:
        return "INCOMPLETE", "needs >=2 bursting base runs and 3 valid wsf200 runs"
    glob = sum(1 for r in base if r["caller"] == "global-direct")
    if glob < 2:
        return "NOT-APPLICABLE", f"measured caller in base is {[r['caller'] for r in base]}, not global direct reclaim; watermark_scale_factor cannot help"
    sb = med([r["stall_s"] or 0 for r in base])
    sw = med([(r["stall_s"] or 0) for r in w2])
    gb = med([r["shrink_memcg_cb_dstate_samples"] for r in base])
    gw = med([r["shrink_memcg_cb_dstate_samples"] for r in w2])
    kb = med([r["kswapd_shrink_samples"] for r in base])
    kw = med([r["kswapd_shrink_samples"] for r in w2])
    mech = f"game-thread shrink_memcg_cb D samples {gw:.0f} vs base {gb:.0f}; kswapd {kw:.0f} vs base {kb:.0f}"
    if sb == 0:
        return "INCONCLUSIVE", "base runs show no stall seconds to reduce; " + mech
    if sw <= 0.5 * sb and gw <= 0.5 * gb:
        return "PASS", f"stall seconds {sw:.0f} vs base {sb:.0f}; {mech}"
    if sw >= 0.8 * sb:
        return "FAIL", f"stall seconds {sw:.0f} vs base {sb:.0f}; {mech}"
    return "INCONCLUSIVE", f"stall seconds {sw:.0f} vs base {sb:.0f}; {mech}"


def verdict_h4(runs):
    allho = runs.get("highonly", [])
    ho = [r for r in allho if r["valid"]]
    dns = [r for r in allho if r.get("did_not_settle")]
    ctl = [r for r in runs.get("highonly-noshrink", []) if r["valid"]]
    if len(allho) < 3 or not (ctl or runs.get("highonly-noshrink")):
        return "INCOMPLETE", "needs highonly x3 and highonly-noshrink x1"
    if len(dns) >= 2:
        return "DID-NOT-SETTLE", f"{len(dns)} of {len(allho)} highonly runs never settled within the settle timeout: memory.high reclaim kept the pool moving, so no aging window exists to test"
    if not ctl:
        return "INCOMPLETE", "highonly-noshrink control did not produce a valid run"
    ok = [r for r in ho if r.get("allocstall_delta") == 0 and r.get("pgscan_direct_delta") == 0
          and (r.get("pgscan_kswapd_delta") or 0) <= 1000]
    if len(ok) < 2:
        return "INVALID", "fewer than 2 runs were free of global reclaim (allocstall/pgscan_direct/kswapd), so memory.high-alone is not isolated"
    bursts = [r for r in ok if r["burst"] and r["t_burst_s"] >= MIN_T_BURST_S]
    if ctl[0]["burst"]:
        return "FAIL", "highonly-noshrink control burst: not the shrinker"
    if len(bursts) >= 2 and all(r["caller"] == "memcg-high" for r in bursts):
        return "PASS", f"{len(bursts)} bursts at {sorted(round(r['t_burst_s']) for r in bursts)} s, caller memcg-high"
    if len(bursts) >= 2:
        return "INCONCLUSIVE", f"bursts without a memcg-high caller: {[r['caller'] for r in bursts]}"
    if sum(1 for r in ok if not r["burst"]) >= 2:
        return "FAIL", "no burst within the cap in >=2 valid runs"
    return "INCONCLUSIVE", "mixed outcomes"


def main():
    if len(sys.argv) != 2 or not os.path.isdir(sys.argv[1]):
        print(__doc__)
        return 2
    root = sys.argv[1]
    runs = collections.defaultdict(list)
    allr = []
    for name in sorted(os.listdir(root)):
        d = os.path.join(root, name)
        if os.path.isdir(d) and "-r" in name:
            r = analyse_run(d)
            runs[name.rsplit("-r", 1)[0]].append(r)
            allr.append(r)
    print(f"{'run':24} {'valid':5} {'burst':5} {'t_burst_s':>9} {'stall_s':>7} {'maxms':>7} {'caller':>20} {'allocst':>8} {'direct':>9} {'kswapd':>9}")
    for r in allr:
        print(f"{r['dir']:24} {str(r['valid']):5} {str(r.get('burst')):5} "
              f"{str(None if r.get('t_burst_s') is None else round(r['t_burst_s'])):>9} "
              f"{str(r.get('stall_s')):>7} {str(r.get('stall_max_ms') if r.get('stall_max_ms') is None else round(r['stall_max_ms'])):>7} "
              f"{str(r.get('caller')):>20} {str(r.get('allocstall_delta')):>8} "
              f"{str(r.get('pgscan_direct_delta')):>9} {str(r.get('pgscan_kswapd_delta')):>9}  {r['note']}")
    print()
    v, why = verdict_h1(runs); print(f"H1 {v}: {why}")
    for n, (v, why) in verdict_h2(runs).items():
        print(f"H2 [{n}] {v}: {why}")
    v, why = verdict_h3(runs); print(f"H3 {v}: {why}")
    v, why = verdict_h4(runs); print(f"H4 {v}: {why}")
    with open(os.path.join(root, "analysis.json"), "w") as f:
        json.dump(allr, f, indent=1, default=str)
    return 0


if __name__ == "__main__":
    sys.exit(main())
