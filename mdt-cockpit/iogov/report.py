"""Reporting: `analyze` (summary, shift detection, plots as text) and `status`.

Both stream the journal once through an accumulator and the shared recognizer `Engine` (bounded memory, whatever the journal
size). `status` reads the persisted aggregates (`agg.json`) for the recognizer state; `analyze` replays the journal through the
same Engine code, so the two agree.
"""

from __future__ import annotations

import collections
import datetime as dt
import json
import os
import time

from . import covariates as cov
from . import safeio
from .gating import kill_switch_reason
from .journal import ReadStats, load_state, read_records
from .recognizer import Engine, lane_kind, pattern_hint
from .scratch import STALE_RE

MAX_EPISODES = 2000
MAX_NOTES = 500


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


class Acc:
    """Streaming accumulator over journal records: counters and small bounded lists, never the records themselves."""

    def __init__(self) -> None:
        self.records = 0
        self.by_type: collections.Counter = collections.Counter()
        self.skips: collections.Counter = collections.Counter()
        self.first = self.last = None
        self.bench_ok = self.bench_aborted = 0
        self.abort_reasons: collections.Counter = collections.Counter()
        self.region_modes: collections.Counter = collections.Counter()
        self.episodes: collections.deque = collections.deque(maxlen=MAX_EPISODES)  # (t, evidence summary)
        self.probe_latency_events = 0
        self.notes: collections.deque = collections.deque(maxlen=MAX_NOTES)
        self.last_covariates = None
        self.last_runs: collections.deque = collections.deque(maxlen=3)
        self.last_recs: collections.deque = collections.deque(maxlen=5)

    def add(self, r: dict) -> None:
        self.records += 1
        typ = r["type"]
        self.by_type[typ] += 1
        self.first = self.first or r["ts"]
        self.last = r["ts"]
        if typ == "skip":
            for reason in r["reasons"]:
                self.skips[(r["kind"], reason.split(":")[0])] += 1
        elif typ == "bench":
            if r["status"] == "ok":
                self.bench_ok += 1
            else:
                self.bench_aborted += 1
                self.abort_reasons[(r.get("abort") or "")[:60]] += 1
            self.region_modes[r["region"].get("mode")] += 1
        elif typ == "harm":
            if r["kind"] == "episode":
                e = r["evidence"]
                self.episodes.append({"t": r["t"], "ts": r["ts"],
                                      **{k: e.get(k) for k in ("duration_s", "recovery_s", "peak_some_pct", "peak_dstate", "peak_mem_some_pct", "driver")}})
            else:
                self.probe_latency_events += 1
        elif typ == "note":
            self.notes.append({"t": r["t"], "ts": r["ts"], "text": r["text"], "kind": r.get("kind")})
        elif typ == "run_start" and r.get("covariates"):
            self.last_covariates = r["covariates"]
        elif typ == "run_end":
            self.last_runs.append(r)
        elif typ == "recommendation" and r["kind"] != "baseline_established":
            self.last_recs.append(r)

    # -- summaries -----------------------------------------------------------------------------------------
    def summary(self) -> dict:
        return {
            "records": self.records, "by_type": dict(self.by_type), "first": self.first, "last": self.last,
            "bench_ok": self.bench_ok, "bench_aborted": self.bench_aborted,
            "bench_abort_reasons": dict(self.abort_reasons),
            "skips": {f"{k}/{v}": n for (k, v), n in sorted(self.skips.items())},
            "write_region_modes": dict(self.region_modes),
        }

    def harm(self) -> dict:
        eps = list(self.episodes)
        durs = sorted(e["duration_s"] for e in eps)
        recs = sorted(e["recovery_s"] for e in eps)
        around = []
        for n in self.notes:
            if n.get("kind") not in ("action", "annotation"):
                continue
            before = [e for e in eps if n["t"] - 86400 <= e["t"] < n["t"]]
            after = [e for e in eps if n["t"] <= e["t"] < n["t"] + 86400]
            if before or after:
                around.append({"ts": n["ts"], "text": n["text"], "episodes_24h_before": len(before), "episodes_24h_after": len(after),
                               "max_duration_before_s": max((e["duration_s"] for e in before), default=None),
                               "max_duration_after_s": max((e["duration_s"] for e in after), default=None)})
        return {
            "episodes": len(eps), "probe_latency_events": self.probe_latency_events,
            "duration_s_median": durs[len(durs) // 2] if durs else None, "duration_s_max": durs[-1] if durs else None,
            "recovery_s_max": recs[-1] if recs else None,
            "peak_some_pct_max": max((e["peak_some_pct"] for e in eps), default=None),
            "drivers": dict(collections.Counter(e.get("driver") or "unknown" for e in eps)),
            "last": [{k: e[k] for k in ("ts", "duration_s", "recovery_s", "peak_some_pct", "peak_dstate", "peak_mem_some_pct", "driver")} for e in eps[-5:]],
            "around_annotations": around,
        }


def summarize(records: list[dict]) -> dict:
    acc = Acc()
    for r in records:
        acc.add(r)
    return acc.summary()


def harm_summary(records: list[dict]) -> dict:
    """Protected-workload distress OBSERVATIONS (kept apart from capability recommendations) incl. stopping-distance data."""

    acc = Acc()
    for r in records:
        acc.add(r)
    return acc.harm()


def latest_prevention(cfg, records: list[dict], wall) -> list[dict]:
    starts = [r for r in records if r["type"] == "run_start" and r.get("covariates")]
    return cov.prevention(cfg, starts[-1]["covariates"], wall) if starts else []


def replay(cfg, path: str, now: float | None = None, stats: ReadStats | None = None, since: float | None = None):
    """Stream the journal through the Engine and an Acc. Returns (engine, acc). Only CLOSED windows are evaluated."""

    eng, acc = Engine(cfg, keep_events=True), Acc()
    for rec in read_records(path, stats, since):
        acc.add(rec)
        eng.feed(rec)
    eng.close_up_to(time.time() if now is None else now)
    return eng, acc


def analyze(cfg, path: str, as_json: bool = False, lane_filter: str | None = None, plots: bool = True,
            now: float | None = None) -> str:
    stats = ReadStats()
    eng, acc = replay(cfg, path, now, stats)
    summary = acc.summary()
    results = eng.results()
    side = results.pop("__side__")
    h = acc.harm()
    if as_json:
        out = {
            "summary": summary,
            "read": {"torn": stats.torn, "unknown_version": stats.unknown_version, "invalid": stats.invalid, "unreadable": stats.unreadable},
            "lanes": {
                lane: {
                    "phase": r.display_phase, "reference": r.reference, "latest": r.latest, "ratio": r.ratio(),
                    "events": r.events, "insufficient_windows": r.insufficient_windows,
                    "rows": [vars(row) for row in r.rows],
                }
                for lane, r in results.items() if not lane_filter or lane.startswith(lane_filter)
            },
            "hint": pattern_hint(results),
            "protected_harm": h,
            "demand_check": [dict(d, window=_day(d["window"], cfg)) for d in side["demand"]],
            "passive_peak_write_bps_by_window": {_day(k, cfg): v for k, v in sorted(side["passive_peak"].items())},
        }
        return json.dumps(out, indent=1, sort_keys=True)
    lines = [f"iogov analyze: {summary['records']} records, {summary['first']} .. {summary['last']}"]
    if stats.skipped():
        lines.append(f"  skipped while reading: {stats.torn} torn, {stats.unknown_version} other-schema, {stats.invalid} invalid, "
                     f"{len(stats.unreadable)} unreadable file(s)")
        for u in stats.unreadable:
            lines.append(f"    UNREADABLE {u}")
    lines.append(f"  types: {summary['by_type']}")
    lines.append(f"  bench: {summary['bench_ok']} ok, {summary['bench_aborted']} aborted {summary['bench_abort_reasons'] or ''}; "
                 f"write region modes {summary['write_region_modes']}")
    if summary["skips"]:
        lines.append(f"  skipped runs (evidence lost): {summary['skips']}")
    lines.append("  only closed 24 h windows are evaluated; the current partial day is not")
    lines.append("")
    lines.append(f"{'lane':<24}{'state':<18}{'reference':>14}{'latest':>14}{'ratio':>9}  windows(valid/insufficient)")
    for lane, r in results.items():
        if lane_filter and not lane.startswith(lane_filter):
            continue
        ratio = r.ratio()
        lines.append(f"{lane:<24}{r.display_phase:<18}{fmt_cap(lane, r.reference):>14}{fmt_cap(lane, r.latest):>14}"
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
    lines += ["", "protected workload harmed (observations about the game side, NOT capacity claims):"]
    if not h["episodes"] and not h["probe_latency_events"]:
        lines.append("  none recorded")
    else:
        lines.append(f"  {h['episodes']} distress episodes (duration median {h['duration_s_median']} s, max {h['duration_s_max']} s, "
                     f"max recovery {h['recovery_s_max']} s, peak io stall {h['peak_some_pct_max']}%; dominant stall share {h['drivers']}), "
                     f"{h['probe_latency_events']} slow-probe events")
        for e in h["last"]:
            lines.append(f"  {e['ts']} lasted {e['duration_s']} s, recovery {e['recovery_s']} s, peak io stall {e['peak_some_pct']}%, "
                         f"memory stall {e.get('peak_mem_some_pct')}%, D-state {e['peak_dstate']}, driver {e.get('driver') or 'unknown'}")
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
    fail = state.get("bench_fail", {})
    if fail.get("n"):
        lines.append(f"  bench failures in a row: {fail['n']}" + (" (BENCH LANE DISABLED until `iogov annotate --baseline-reset`)" if fail.get("disabled_at") else ""))
    b = state.get("budget")
    if b:
        lines.append(f"  today ({b['day']}): read {b['read_bytes']} B / {cfg.max_read_bytes_per_day}, write {b['write_bytes']} B / {cfg.max_write_bytes_per_day}, "
                     f"{b['sync_ops']} syncs")
    bo = {k: v for k, v in state.get("backoff", {}).items() if v.get("n")}
    if bo:
        lines.append(f"  backoff: {bo}")
    leftovers = []
    if os.path.isdir(cfg.scratch_dir):
        leftovers = [n for n in os.listdir(cfg.scratch_dir) if STALE_RE.fullmatch(n)]
    lines.append(f"  scratch leftovers: {leftovers or 'none'}")
    data = safeio.load_json(os.path.join(cfg.state_dir, "agg.json"), any_owner=True)  # read-only: an operator may run `status`
    acc = Acc()
    window = now - 30 * 86400
    stats = ReadStats()
    for rec in read_records(cfg.journal_dir(), stats, since=window):
        acc.add(rec)
    for u in stats.unreadable:
        lines.append(f"  UNREADABLE journal file: {u}")
    if data.get("v"):
        eng = Engine(cfg, data)
    else:  # no persisted aggregates yet: replay (bounded memory), closed windows only
        eng, _ = replay(cfg, cfg.journal_dir(), now)
    eng.close_up_to(now)
    results = eng.results()
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
        lines.append(f"  {lane:<24}{r.display_phase:<18}{(f'{ratio:.2f}x' if ratio else '-'):>8}{stale}")
    h = acc.harm()
    lines.append(f"  protected harm (30 d): {h['episodes']} episodes, {h['probe_latency_events']} slow-probe events "
                 f"(max episode {h['duration_s_max']} s); separate from capability recommendations")
    findings = cov.prevention(cfg, acc.last_covariates, wall) if acc.last_covariates else []
    lines.append("  prevention checks (report only):" + (" all clear" if not findings else ""))
    for f in findings:
        lines.append(f"    [{f['level']}] {f['code']}: {f['text']}")
    for lane, r in results.items():
        for ev in r.events[-2:]:
            if ev["kind"] != "baseline_established":
                lines.append(f"  recommendation [{ev['kind']}] {ev['text']}")
    for r in acc.last_runs:
        lines.append(f"  last run {r['ts']} {r['kind']}: {r['status']} {r.get('abort') or ''} in {r['duration_s']} s")
    lines.append("  This tool recommends only; it has changed no cap, model, queue setting or cgroup file.")
    return "\n".join(lines)
