#!/usr/bin/env python3
"""OFFLINE synthetic-shift check for the iogovmon recognizer (stdlib + the installed iogovmon package; no live state is touched).

Question: with the acceptance-2 condensed config (`.cond-v2`: window_hours = 1.0), does the recognizer (a) leave LEARNING after 5 valid
windows, (b) confirm a shift after 3 consecutive bad windows, (c) clear it after 2 good windows, for a median shift of 3x, of 1.5x, and
for a TAIL-ONLY change (p99 up, median flat: what the load calibration saw)? A no-change control and a run with the briefed
read_max_attempts=36 (finding 2: too few cold samples per window) are included.

How it works (read-only on the live state dir):
  1. COPY /var/lib/iogovmon/{state.json,agg.json,journal} to <synth>/live-copy/ (the only access to the live dir is these reads).
  2. The newest real quiet r_cold / r_witness probe records of the copy are the TEMPLATE: their lat_us values are the value pool and
     the non-zero fraction (n / attempts) sets how many samples a tick yields at read_max_attempts.
  3. Per scenario a fresh state dir <synth>/<scenario>/ gets a temp config (state_dir pointing there) and a synthetic journal of
     6 baseline windows, 4 changed windows, 3 recovery windows (+ one trailing tick so the last window closes), 12 ticks of 5 min per
     window, every window aligned to the UTC hour, real record layout (copied from a template record).
  4. The same Engine code path that the daemon uses (iogovmon.recognizer.recognize) and `iogovmonctl analyze` run on that copy.
Usage: python3 synth_shift_check.py [--synth-dir /root/iogovmon-accept2/synth] [--attempts 128] [--seed 7] [--live-dir DIR] [--config FILE]
Not stdlib-only: it imports the installed iogovmon package (config + recognizer), found at $IOGOVMON_SRC (default /opt/iogovmon) unless importable.
"""
import argparse, copy, json, os, random, shutil, statistics, subprocess, sys, time
from pathlib import Path

sys.path.append(os.environ.get("IOGOVMON_SRC", "/opt/iogovmon"))  # the installed package; an importable iogovmon (a source tree on PYTHONPATH) wins
from iogovmon.config import load as load_cfg  # noqa: E402
from iogovmon.recognizer import recognize  # noqa: E402

LIVE = Path("/var/lib/iogovmon")
CONFIG_V2 = Path("/root/iogovmon-accept2/iogovmon.toml.cond-v2")  # defaults of --live-dir / --config; the on-host acceptance paths
LANES = ("r_cold", "r_witness")
N_BASE, N_BAD, N_REC, TICKS = 6, 4, 3, 12


def pct(values, p):
    s = sorted(values)
    return s[min(len(s) - 1, int(round(p / 100.0 * (len(s) - 1))))]


def copy_live(dest: Path):
    dest.mkdir(parents=True, exist_ok=True)
    for name in ("state.json", "agg.json"):
        if (LIVE / name).exists():
            shutil.copy2(LIVE / name, dest / name)  # read from live, write into the copy
    jd = dest / "journal"
    jd.mkdir(exist_ok=True)
    for f in sorted((LIVE / "journal").glob("journal-*.jsonl")):
        shutil.copy2(f, jd / f.name)


def template(copy_dir: Path, newest=400):
    recs = []
    for f in sorted((copy_dir / "journal").glob("journal-*.jsonl")):
        for line in f.read_text(errors="replace").splitlines():
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("type") == "probe" and r.get("quiet") and r.get("lane") in LANES and r.get("lat_us"):
                recs.append(r)
    out = {}
    for lane in LANES:
        rs = [r for r in recs if r["lane"] == lane][-newest:]
        out[lane] = {"rec": rs[-1], "pool": [v for r in rs for v in r["lat_us"]],
                     "nonzero": sum(r["n"] for r in rs) / max(1, sum(r["attempts"] for r in rs)), "last_t": recs[-1]["t"]}
    return out


def transform(kind, factor, pool_p90):
    if kind == "median":
        return lambda v: v * factor
    if kind == "tail":  # only the top decile of the baseline distribution is stretched: median and p75 stay put, p95/p99 move
        return lambda v: v * factor if v >= pool_p90 else v
    return lambda v: v


def build_journal(scn, tmpl, attempts, rng, t_first):
    """Return (records, per_window_rows). Window k = t_first + k * 3600 (UTC hour aligned)."""
    pool_p90 = pct(tmpl["r_cold"]["pool"], 90)
    xf = transform(scn["kind"], scn["factor"], pool_p90)
    ident = transform("none", 1, pool_p90)
    recs, rows = [], []
    for k in range(N_BASE + N_BAD + N_REC):
        phase = "baseline" if k < N_BASE else ("changed" if k < N_BASE + N_BAD else "recovery")
        f = xf if phase == "changed" else ident
        cold_all = []
        for i in range(TICKS):
            t = t_first + k * 3600 + 120 + i * 300 + rng.uniform(-20, 20)
            for lane in LANES:
                tp = tmpl[lane]
                if lane == "r_cold":
                    n = sum(1 for _ in range(attempts) if rng.random() < tp["nonzero"])
                    vals = [max(1, int(f(rng.choice(tp["pool"])))) for _ in range(n)]
                    cold_all += vals
                    att, zero = attempts, attempts - n
                else:  # the witness lane is left unchanged: a control lane inside the same run
                    n = 2
                    vals = [rng.choice(tp["pool"]) for _ in range(n)]
                    att, zero = 2, 0
                r = copy.deepcopy(tp["rec"])
                r.update({"t": round(t, 3), "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(t)) + ".000Z", "lat_us": vals, "n": n,
                          "attempts": att, "zero": zero, "min": min(vals) if vals else 0, "max": max(vals) if vals else 0,
                          "p50": pct(vals, 50) if vals else 0, "p90": pct(vals, 90) if vals else 0, "p99": pct(vals, 99) if vals else 0,
                          "quiet": True, "direct": True, "run": f"synth{k:02d}{i:02d}"})
                if not vals:
                    r["lat_us"], r["n"] = [], 0
                recs.append(r)
        rows.append({"k": k, "phase": phase, "n": len(cold_all), "p50": pct(cold_all, 50) if cold_all else None,
                     "p95": pct(cold_all, 95) if cold_all else None, "p99": pct(cold_all, 99) if cold_all else None})
    t = t_first + (N_BASE + N_BAD + N_REC) * 3600 + 120  # trailing tick: closes the last window, itself too thin to count
    for lane in LANES:
        r = copy.deepcopy(tmpl[lane]["rec"])
        r.update({"t": round(t, 3), "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(t)) + ".000Z", "lat_us": [500], "n": 1,
                  "min": 500, "max": 500, "p50": 500, "p90": 500, "p99": 500, "run": "synthtail"})
        recs.append(r)
    return recs, rows


def write_scenario(root: Path, name, recs):
    d = root / name
    if d.exists():
        shutil.rmtree(d)
    (d / "journal").mkdir(parents=True)
    (d / "scratch").mkdir()
    for p in (d, d / "journal", d / "scratch"):
        os.chmod(p, 0o700)
    text = CONFIG_V2.read_text().splitlines()
    out = []
    for line in text:
        if line.startswith("state_dir"):
            line = f'state_dir = "{d}"'
        elif line.startswith("scratch_dir"):
            line = f'scratch_dir = "{d}/scratch"'
        out.append(line)
    cfg = d / "cfg.toml"
    cfg.write_text("\n".join(out) + "\n")
    os.chmod(cfg, 0o644)
    j = d / "journal" / "journal-synth.jsonl"
    j.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in recs))
    os.chmod(j, 0o600)
    return cfg


def evaluate(res, lane, t_first):
    st = res.get(lane)
    if st is None:
        return {"lane": lane, "valid_windows": 0, "note": "lane never produced a window"}
    rel = lambda w: w - int(t_first // 3600)  # noqa: E731
    ev = {e["kind"]: rel(e["evidence"]["window_start"]) for e in st.events}
    rows = [(rel(r.start), r.n, r.phase, None if r.x is None else round(r.x, 2), round(r.s_down, 2)) for r in st.rows]
    return {"lane": lane, "valid_windows": st.windows_seen - st.insufficient_windows, "insufficient": st.insufficient_windows,
            "baseline_established_after_window": ev.get("baseline_established"), "shift_detected_at_window": ev.get("shift_detected"),
            "shift_cleared_at_window": ev.get("shift_cleared"), "final_phase": st.phase, "rows": rows}


def main():
    global LIVE, CONFIG_V2
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--synth-dir", default="/root/iogovmon-accept2/synth")
    ap.add_argument("--attempts", type=int, default=128, help="read_max_attempts of the config under test (v2: 128, briefed: 36)")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--live-dir", default=str(LIVE), help="the iogovmon state dir to COPY the template records from (read only)")
    ap.add_argument("--config", default=str(CONFIG_V2), help="the condensed config under test (window_hours = 1.0 for acceptance-2)")
    a = ap.parse_args()
    LIVE, CONFIG_V2 = Path(a.live_dir), Path(a.config)
    root = Path(a.synth_dir)
    root.mkdir(parents=True, exist_ok=True)
    os.chmod(root, 0o700)
    live_copy = root / "live-copy"
    copy_live(live_copy)
    tmpl = template(live_copy)
    t_first = (int(tmpl["r_cold"]["last_t"] // 3600) + 1) * 3600
    print(f"template: r_cold pool {len(tmpl['r_cold']['pool'])} values (median {statistics.median(tmpl['r_cold']['pool'])} us), "
          f"non-zero fraction {tmpl['r_cold']['nonzero']:.3f}; r_witness pool {len(tmpl['r_witness']['pool'])}; attempts per tick {a.attempts}")
    scenarios = [{"name": "control-no-change", "kind": "none", "factor": 1.0}, {"name": "median-x3", "kind": "median", "factor": 3.0},
                 {"name": "median-x1.5", "kind": "median", "factor": 1.5}, {"name": "tail-only-x8", "kind": "tail", "factor": 8.0}]
    summary = []
    for scn in scenarios:
        rng = random.Random(a.seed)
        recs, wrows = build_journal(scn, tmpl, a.attempts, rng, t_first)
        cfg_path = write_scenario(root, f"{scn['name']}-att{a.attempts}", recs)
        cfg = load_cfg(str(cfg_path))
        res = recognize(recs, cfg, keep_events=True)
        ev = evaluate(res, "r_cold@vda", t_first)
        evw = evaluate(res, "r_witness@vda", t_first)
        an = subprocess.run(["iogovmonctl", "analyze", "--no-plots"], capture_output=True, text=True, cwd="/",
                            env=dict(os.environ, IOGOVMON_CONFIG=str(cfg_path)), timeout=300)
        (cfg_path.parent / "analyze.txt").write_text(an.stdout + an.stderr)
        print(f"\n=== {scn['name']} (attempts {a.attempts}) window_hours={cfg.window_hours}")
        print("  window phase     n      p50    p95    p99   | recognizer r_cold row (n, phase, x_log2, S_down)")
        rowmap = {r[0]: r for r in ev.get("rows", [])}
        for w in wrows:
            r = rowmap.get(w["k"])
            print(f"  {w['k']:>6} {w['phase']:<9} {w['n']:>4} {w['p50']!s:>7} {w['p95']!s:>6} {w['p99']!s:>6}   | {r if r else '(window not valid / no row)'}")
        print(f"  r_cold: valid windows {ev['valid_windows']} (insufficient {ev.get('insufficient')}); LEARNING left after window "
              f"{ev['baseline_established_after_window']}; shift detected at window {ev['shift_detected_at_window']} "
              f"(first changed window is {N_BASE}); cleared at window {ev['shift_cleared_at_window']} (first recovery window is {N_BASE + N_BAD}); "
              f"final phase {ev['final_phase']}")
        print(f"  r_witness (unchanged control lane): detected {evw.get('shift_detected_at_window')}, final {evw.get('final_phase')}")
        lines = [ln for ln in (an.stdout or "").splitlines() if "r_cold" in ln or "shift" in ln.lower()][:6]
        print("  iogovmonctl analyze (rc %d) lines: %s" % (an.returncode, " | ".join(l.strip()[:110] for l in lines) or "(none)"))
        summary.append((scn["name"], ev["baseline_established_after_window"], ev["shift_detected_at_window"], ev["shift_cleared_at_window"]))
    print("\nSUMMARY (window indexes are relative to the first synthetic window 0; changed windows are 6-9, recovery 10-12)")
    for name, a_, b_, c_ in summary:
        print(f"  {name:<20} leaves LEARNING after {a_} | shift confirmed at {b_} | cleared at {c_}")
    print(f"scenario dirs and analyze outputs: {root}/<scenario>-att{a.attempts}/ ; live dir was only read (copy in {live_copy})")


if __name__ == "__main__":
    main()
