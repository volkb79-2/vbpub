"""Offline reporting over the journal: `analyze` (summary, shift detection, plots as text) and `status`."""

from __future__ import annotations

import collections
import datetime as dt
import json
import os

from . import covariates as cov
from .gating import kill_switch_reason
from .journal import ReadStats, load_state, read_records
from .recognizer import LaneResult, lane_kind, pattern_hint, recognize
from .scratch import PREFIX


def _day(idx: int, cfg) -> str:
    t = idx * cfg.window_hours * 3600.0
    return dt.datetime.fromtimestamp(t, dt.timezone.utc).strftime("%Y-%m-%d %H:%M")


def fmt_cap(lane: str, cap: float | None) -> str:
    if cap is None:
        return "-"
    if lane_kind(lane) == "latency":
        return f"{1e6 / cap / 1000.0:.3f} ms"
    return f"{cap / (1024 * 1024):.1f} MiB/s"


def bar(x: float | None, width: int = 41, span: float = 6.0) -> str:
    """Text bar centred on the reference: '#' left of '|' = worse (x<0), right = better. span = +-log2 units."""

    if x is None:
        return " " * width
    half = width // 2
    n = min(half, int(round(abs(x) / span * half)))
    if x < 0:
        return " " * (half - n) + "#" * n + "|" + " " * half
    return " " * half + "|" + "+" * n + " " * (half - n)


def summarize(records: list[dict]) -> dict:
    by_type = collections.Counter(r["type"] for r in records)
    skips = collections.Counter()
    for r in records:
        if r["type"] == "skip":
            for reason in r["reasons"]:
                skips[(r["kind"], reason.split(":")[0])] += 1
    benches = [r for r in records if r["type"] == "bench"]
    return {
        "records": len(records),
        "by_type": dict(by_type),
        "first": records[0]["ts"] if records else None,
        "last": records[-1]["ts"] if records else None,
        "bench_ok": sum(1 for b in benches if b["status"] == "ok"),
        "bench_aborted": sum(1 for b in benches if b["status"] != "ok"),
        "bench_abort_reasons": dict(collections.Counter(b.get("abort") for b in benches if b["status"] != "ok")),
        "skips": {f"{k}/{v}": n for (k, v), n in sorted(skips.items())},
        "write_region_modes": dict(collections.Counter(b["region"].get("mode") for b in benches)),
    }


def harm_summary(records: list[dict]) -> dict:
    """Protected-workload distress OBSERVATIONS (kept apart from capability recommendations) incl. stopping-distance data."""

    eps = [r for r in records if r["type"] == "harm" and r["kind"] == "episode"]
    probe = [r for r in records if r["type"] == "harm" and r["kind"] == "probe_latency"]
    durs = sorted(e["evidence"]["duration_s"] for e in eps)
    recs = sorted(e["evidence"]["recovery_s"] for e in eps)
    notes = [r for r in records if r["type"] == "note"]
    around = []
    for n in notes:
        if n.get("kind") not in ("action", "annotation"):
            continue
        before = [e for e in eps if n["t"] - 86400 <= e["t"] < n["t"]]
        after = [e for e in eps if n["t"] <= e["t"] < n["t"] + 86400]
        if before or after:
            around.append({"ts": n["ts"], "text": n["text"], "episodes_24h_before": len(before), "episodes_24h_after": len(after),
                           "max_duration_before_s": max((e["evidence"]["duration_s"] for e in before), default=None),
                           "max_duration_after_s": max((e["evidence"]["duration_s"] for e in after), default=None)})
    return {
        "episodes": len(eps), "probe_latency_events": len(probe),
        "duration_s_median": durs[len(durs) // 2] if durs else None, "duration_s_max": durs[-1] if durs else None,
        "recovery_s_max": recs[-1] if recs else None,
        "peak_some_pct_max": max((e["evidence"]["peak_some_pct"] for e in eps), default=None),
        "last": [{"ts": e["ts"], "duration_s": e["evidence"]["duration_s"], "recovery_s": e["evidence"]["recovery_s"],
                  "peak_some_pct": e["evidence"]["peak_some_pct"], "peak_dstate": e["evidence"]["peak_dstate"]} for e in eps[-5:]],
        "around_annotations": around,
    }


def latest_prevention(cfg, records: list[dict], wall) -> list[dict]:
    starts = [r for r in records if r["type"] == "run_start" and r.get("covariates")]
    return cov.prevention(cfg, starts[-1]["covariates"], wall) if starts else []


def analyze(cfg, path: str, as_json: bool = False, lane_filter: str | None = None, plots: bool = True) -> str:
    stats = ReadStats()
    records = sorted(read_records(path, stats), key=lambda r: r["t"])
    summary = summarize(records)
    results = recognize(records, cfg)
    side = results.pop("__side__")
    if as_json:
        out = {
            "summary": summary,
            "read": {"torn": stats.torn, "unknown_version": stats.unknown_version, "invalid": stats.invalid},
            "lanes": {
                lane: {
                    "phase": r.phase, "reference": r.reference, "latest": r.latest, "ratio": r.ratio(),
                    "events": r.events, "insufficient_windows": r.insufficient_windows,
                    "rows": [vars(row) for row in r.rows],
                }
                for lane, r in results.items() if not lane_filter or lane.startswith(lane_filter)
            },
            "hint": pattern_hint(results),
            "protected_harm": harm_summary(records),
            "demand_check": [dict(d, window=_day(d["window"], cfg)) for d in side["demand"]],
            "passive_peak_write_bps_by_window": {_day(k, cfg): v for k, v in sorted(side["passive_peak"].items())},
        }
        return json.dumps(out, indent=1, sort_keys=True)
    lines = [f"iogov analyze: {summary['records']} records, {summary['first']} .. {summary['last']}"]
    if stats.torn or stats.unknown_version or stats.invalid:
        lines.append(f"  skipped while reading: {stats.torn} torn, {stats.unknown_version} other-schema, {stats.invalid} invalid")
    lines.append(f"  types: {summary['by_type']}")
    lines.append(f"  bench: {summary['bench_ok']} ok, {summary['bench_aborted']} aborted {summary['bench_abort_reasons'] or ''}; "
                 f"write region modes {summary['write_region_modes']}")
    if summary["skips"]:
        lines.append(f"  skipped runs (evidence lost): {summary['skips']}")
    lines.append("")
    lines.append(f"{'lane':<24}{'state':<18}{'reference':>14}{'latest':>14}{'ratio':>9}  windows(valid/insufficient)")
    for lane, r in results.items():
        if lane_filter and not lane.startswith(lane_filter):
            continue
        ratio = r.ratio()
        lines.append(f"{lane:<24}{r.phase:<18}{fmt_cap(lane, r.reference):>14}{fmt_cap(lane, r.latest):>14}"
                     f"{(f'{ratio:.2f}x' if ratio else '-'):>9}  {len(r.rows)}/{r.insufficient_windows}")
    if not results:
        lines.append("(no qualifying windows yet: nothing to recognise; see skipped runs and sample counts)")
    hint = pattern_hint(results)
    if hint:
        lines += ["", f"pattern: {hint}"]
    events = [(ev["evidence"]["window_start"], ev) for r in results.values() for ev in r.events]
    if events:
        lines += ["", "recommendations (recommend-only; nothing was changed):"]
        for w, ev in sorted(events, key=lambda e: (e[0], e[1]["lane"])):
            lines.append(f"  {_day(w, cfg)}  [{ev['kind']}] {ev['text']}")
    h = harm_summary(records)
    lines += ["", "protected workload harmed (observations about the game side, NOT capacity claims):"]
    if not h["episodes"] and not h["probe_latency_events"]:
        lines.append("  none recorded")
    else:
        lines.append(f"  {h['episodes']} distress episodes (duration median {h['duration_s_median']} s, max {h['duration_s_max']} s, "
                     f"max recovery {h['recovery_s_max']} s, peak stall {h['peak_some_pct_max']}%), "
                     f"{h['probe_latency_events']} slow-probe events")
        for e in h["last"]:
            lines.append(f"  {e['ts']} lasted {e['duration_s']} s, recovery {e['recovery_s']} s, peak stall {e['peak_some_pct']}%, D-state {e['peak_dstate']}")
        for a in h["around_annotations"]:
            lines.append(f"  around annotation {a['ts']} '{a['text']}': episodes 24 h before {a['episodes_24h_before']} "
                         f"(max {a['max_duration_before_s']} s), after {a['episodes_24h_after']} (max {a['max_duration_after_s']} s)")
    if side["demand"]:
        lines += ["", "demand check (passive delivered rate vs probe capability; low throughput alone is not a capacity drop):"]
        for d in side["demand"][-10:]:
            lines.append(f"  {_day(d['window'], cfg)} {d['label']}: {d['text']}")
    if plots:
        for lane, r in results.items():
            if lane_filter and not lane.startswith(lane_filter):
                continue
            if not r.rows:
                continue
            lines += ["", f"{lane}: log2(capability / reference), '#' = worse, '+' = better, scale +-6 (64x)"]
            for row in r.rows[-40:]:
                x = "learning" if row.x is None else f"{row.x:+5.2f}"
                flag = " (settings changed)" if row.confounded else ""
                lines.append(f"  {_day(row.start, cfg)} {bar(row.x)} {x} {fmt_cap(lane, row.capability):>12} {row.phase}{flag}")
    if side["passive_peak"]:
        lines += ["", "passive peak write rate seen on the disks (a LOWER bound of capability, informational only):"]
        for w, v in sorted(side["passive_peak"].items())[-14:]:
            lines.append(f"  {_day(w, cfg)} {v / (1024 * 1024):8.1f} MiB/s")
    return "\n".join(lines)


def status(cfg, wall) -> str:
    state = load_state(cfg.state_file())
    now = wall()
    lines = ["iogov status"]
    ks = kill_switch_reason(cfg)
    lines.append(f"  kill switch: {'ACTIVE ' + ks if ks else 'not set'}")

    def age(key: str) -> str:
        t = state.get(key)
        return "never" if not t else f"{(now - t) / 3600:.1f} h ago"

    lines.append(f"  last tick: {age('last_tick')}; last bench: {age('last_bench')}; next bench not before: "
                 f"{'-' if not state.get('next_bench') else dt.datetime.fromtimestamp(state['next_bench'], dt.timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    b = state.get("budget")
    if b:
        lines.append(f"  today ({b['day']}): read {b['read_bytes']} B / {cfg.max_read_bytes_per_day}, write {b['write_bytes']} B / {cfg.max_write_bytes_per_day}, "
                     f"{b['sync_ops']} syncs")
    bo = {k: v for k, v in state.get("backoff", {}).items() if v.get("n")}
    if bo:
        lines.append(f"  backoff: {bo}")
    leftovers = []
    if os.path.isdir(cfg.scratch_dir):
        leftovers = [n for n in os.listdir(cfg.scratch_dir) if n.startswith(PREFIX)]
    lines.append(f"  scratch leftovers: {leftovers or 'none'}")
    records = list(read_records(cfg.journal_dir(), since=now - cfg.lookback_days * 86400))
    results = recognize(records, cfg)
    results.pop("__side__")
    if not results:
        lines.append("  recognizer: no qualifying windows yet")
    for lane, r in results.items():
        ratio = r.ratio()
        stale = ""
        if r.last_valid_window is not None:
            gap = int(now // (cfg.window_hours * 3600.0)) - r.last_valid_window
            if gap >= 2:
                stale = f"  STALE: no qualifying window for {gap} windows"
        lines.append(f"  {lane:<24}{r.phase:<18}{(f'{ratio:.2f}x' if ratio else '-'):>8}{stale}")
    h = harm_summary(records)
    lines.append(f"  protected harm: {h['episodes']} episodes, {h['probe_latency_events']} slow-probe events "
                 f"(max episode {h['duration_s_max']} s); separate from capability recommendations")
    findings = latest_prevention(cfg, records, wall)
    lines.append("  prevention checks (report only):" + (" all clear" if not findings else ""))
    for f in findings:
        lines.append(f"    [{f['level']}] {f['code']}: {f['text']}")
    recs = [r for r in records if r["type"] == "recommendation" and r["kind"] != "baseline_established"]
    for r in recs[-5:]:
        lines.append(f"  recommendation {r['ts']}: [{r['kind']}] {r['text']}")
    runs = [r for r in records if r["type"] == "run_end"][-3:]
    for r in runs:
        lines.append(f"  last run {r['ts']} {r['kind']}: {r['status']} {r.get('abort') or ''} in {r['duration_s']} s")
    lines.append("  This tool recommends only; it has changed no cap, model, queue setting or cgroup file.")
    return "\n".join(lines)
