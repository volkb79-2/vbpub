"""Shift recognizer: per-lane window medians -> robust frozen baseline -> two-sided CUSUM with hysteresis -> RECOMMENDATIONS.

It is a pure function of the journal records (replayable, deterministic, no hidden state): `recognize(records, cfg)`.
Its only outputs are recommendation events; nothing in this module can touch a cap, a model, a queue or a cgroup.

Metric. Every lane is turned into a CAPABILITY (higher is better) per window:
    latency lanes     capability = 1e6 / median latency in us      (= operations per second at queue depth 1)
    throughput lanes  capability = median durable (or read) rate in B/s
and x = log2(capability / reference). x = -1 means "2x worse", x = -4.3 means "~20x worse".

CUSUM (log2 units). S_down = max(0, S_down - x - k_eff), S_up = max(0, S_up + x - k_eff), both capped at cap_mult * h_eff so
a long collapse does not need an equally long time to unwind. k_eff = max(k, sigma), h_eff = max(h, 3 sigma), where sigma is
the robust (MAD) noise of the baseline windows with a floor. An alarm needs S >= h_eff. Detection delay for a sustained
step of s log2 units is ceil(h_eff / (s - k_eff)) windows (k=0.5, h=1.5: 20x -> 1 window, 4x -> 1, 2x -> 3).
Hysteresis: an alarm clears only after `clear_windows` consecutive windows with |x| < k_eff (back at the reference), or flips
direction explicitly; it never clears just because S decayed.
The reference is FROZEN while alarmed and is only replaced by an operator `baseline_reset` note (the recognizer never
silently adopts a degraded state as the new normal).
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field

LATENCY_LANES = ("r_cold", "r_witness", "w_over_4k")
THROUGHPUT_LANES = ("w_fresh_qd1", "w_fresh_qd4", "r_seq")


def lane_base(lane: str) -> str:
    return lane.split("@", 1)[0]


def lane_kind(lane: str) -> str:
    return "latency" if lane_base(lane) in LATENCY_LANES else "throughput"


@dataclass
class Window:
    start: int  # window index (floor(t / window_s))
    n: int = 0  # true sample count (latency) or number of records (throughput)
    values: list = field(default_factory=list)
    epochs: set = field(default_factory=set)

    def median(self):
        return statistics.median(self.values) if self.values else None


def window_index(t: float, cfg) -> int:
    return int(t // (cfg.window_hours * 3600.0))


def extract(records, cfg) -> tuple[dict, dict]:
    """records -> ({lane: {window_idx: Window}}, {'resets': [t...], 'passive_peak': {window: bps}})."""

    lanes: dict[str, dict[int, Window]] = {}
    side = {"resets": [], "passive_peak": {}, "records": 0}

    def add(lane: str, t: float, vals: list, n: int, epoch):
        w = lanes.setdefault(lane, {}).setdefault(window_index(t, cfg), Window(window_index(t, cfg)))
        w.values.extend(vals)
        w.n += n
        if epoch:
            w.epochs.add(epoch)

    for rec in records:
        side["records"] += 1
        typ, t = rec["type"], rec["t"]
        if typ == "probe":
            if rec.get("quiet") and rec["lane"] in LATENCY_LANES and rec["lat_us"]:
                add(f"{rec['lane']}@{rec['device']}", t, [v for v in rec["lat_us"] if v > 0] or [1], rec["n"], rec.get("epoch"))
        elif typ == "bench":
            for name, lane in rec["lanes"].items():
                if not lane.get("valid"):
                    continue
                base = lane_base(name)
                if base == "w_over_4k":
                    vals = [v for v in lane["lat"]["lat_us"] if v > 0] or [1]
                    add(name, t, vals, lane.get("n", len(vals)), rec.get("settings_epoch"))
                elif base in ("w_fresh_qd1", "w_fresh_qd4"):
                    add(name, t, [lane["rate_durable_bps"]], 1, rec.get("settings_epoch"))
                elif base == "r_seq":
                    add(name, t, [lane["rate_bps"]], 1, rec.get("settings_epoch"))
        elif typ == "passive":
            peak = 0.0
            for d in rec["devices"].values():
                if d and d.get("w_bps"):
                    peak = max(peak, d["w_bps"])
            w = window_index(t, cfg)
            side["passive_peak"][w] = max(side["passive_peak"].get(w, 0.0), peak)
        elif typ == "note" and rec.get("kind") == "baseline_reset":
            side["resets"].append(t)
    return lanes, side


def _capability(lane: str, w: Window):
    med = w.median()
    if med is None or med <= 0:
        return None
    return 1e6 / med if lane_kind(lane) == "latency" else med


@dataclass
class Row:
    start: int
    n: int
    capability: float
    x: float | None
    s_down: float
    s_up: float
    phase: str
    confounded: bool = False


@dataclass
class LaneResult:
    lane: str
    rows: list = field(default_factory=list)
    events: list = field(default_factory=list)
    phase: str = "NO_DATA"
    reference: float | None = None  # capability (linear units)
    latest: float | None = None
    sigma: float = 0.0
    last_valid_window: int | None = None
    insufficient_windows: int = 0

    def ratio(self):
        if self.reference and self.latest:
            return self.latest / self.reference
        return None


def mad_sigma(values: list[float]) -> float:
    med = statistics.median(values)
    return 1.4826 * statistics.median([abs(v - med) for v in values])


def analyze_lane(lane: str, windows: dict, resets: list, cfg, window_s: float) -> LaneResult:
    res = LaneResult(lane)
    need = cfg.min_samples.get(lane_base(lane), 1)
    learn: list[float] = []
    ref = None
    sigma = cfg.noise_floor_log2
    s_dn = s_up = 0.0
    phase = "LEARNING"
    xs: list[float] = []
    alarm_n = 0
    clear_run = 0
    ref_epochs: set = set()
    history: list[tuple[int, float]] = []  # (window, log2 capability) of valid windows, for operator rebaselines
    pending = sorted(resets)

    def event(kind: str, w: Window, text: str, **evidence):
        evidence.update({"category": "capability", "window_start": w.start, "key": f"{lane}|{kind}|{w.start}", "reference": res.reference})
        res.events.append({"lane": lane, "kind": kind, "text": text, "evidence": evidence})

    for idx in sorted(windows):
        w = windows[idx]
        cap = _capability(lane, w)
        if cap is None or w.n < need:
            res.insufficient_windows += 1
            continue
        # operator baseline resets that happened before this window starts
        while pending and pending[0] < idx * window_s:
            pending.pop(0)
            recent = [v for _, v in history[-cfg.rebaseline_windows:]]
            if recent:
                ref = statistics.median(recent)
                res.reference = 2 ** ref
                s_dn = s_up = 0.0
                phase, xs, alarm_n, clear_run = "OK", [], 0, 0
                ref_epochs = set()
                event("baseline_reset", w, "baseline reset by operator note; reference re-established from the latest windows",
                      from_windows=len(recent))
        lv = math.log2(cap)
        history.append((idx, lv))
        res.latest, res.last_valid_window = cap, idx
        if phase == "LEARNING":
            learn.append(lv)
            ref_epochs |= w.epochs
            res.rows.append(Row(idx, w.n, cap, None, 0.0, 0.0, phase))
            if len(learn) >= cfg.baseline_windows:
                ref = statistics.median(learn)
                sigma = max(cfg.noise_floor_log2, mad_sigma(learn))
                res.reference, res.sigma = 2 ** ref, sigma
                phase = "OK"
                event("baseline_established", w, f"baseline established from {len(learn)} windows", sigma_log2=round(sigma, 3))
            continue
        x = lv - ref
        k_eff = max(cfg.cusum_k, sigma)
        h_eff = max(cfg.cusum_h, 3.0 * sigma)
        cap_s = cfg.cusum_cap_mult * h_eff
        s_dn = min(cap_s, max(0.0, s_dn - x - k_eff))
        s_up = min(cap_s, max(0.0, s_up + x - k_eff))
        confounded = bool(ref_epochs and w.epochs and not (w.epochs & ref_epochs))
        ctx = {"x_log2": round(x, 3), "s_down": round(s_dn, 3), "s_up": round(s_up, 3), "n": w.n, "confounded": confounded}
        note = " Settings changed since the baseline, the shift may be self-inflicted." if confounded else ""
        if phase in ("OK", "SUSPECT_DOWN", "SUSPECT_UP"):
            # a window only counts toward confirmation if it is itself beyond the slack: a big one-off outlier leaves S high
            # for several windows and must not be "confirmed" by perfectly normal windows that follow it
            dirn = "down" if (s_dn >= h_eff and x <= -k_eff) else ("up" if (s_up >= h_eff and x >= k_eff) else None)
            if dirn is None:
                phase, xs = "OK", []
            else:
                want = "SUSPECT_" + dirn.upper()
                xs = xs + [x] if phase == want else [x]
                phase = want
                if len(xs) >= cfg.confirm_windows:
                    phase, alarm_n, clear_run = "ALARM_" + dirn.upper(), len(xs), 0
                    factor = 2 ** abs(statistics.median(xs))
                    event("shift_detected", w,
                          f"capability shifted {dirn} ~{factor:.1f}x on {lane}, baseline reset suggested "
                          f"(verify the cause, then run `iogov annotate --baseline-reset`)." + note,
                          direction=dirn, factor=round(factor, 2), baseline_reset_suggested=True, **ctx)
        else:
            direction_down = phase == "ALARM_DOWN"
            xs.append(x)
            alarm_n += 1
            flipped = (s_up >= h_eff) if direction_down else (s_dn >= h_eff)
            clear_run = clear_run + 1 if abs(x) < k_eff else 0
            if flipped:
                event("shift_cleared", w, f"{lane} moved through the reference to the opposite side; previous alarm closed.", reason="flip", **ctx)
                phase = "SUSPECT_UP" if direction_down else "SUSPECT_DOWN"
                xs, alarm_n, clear_run = [x], 1, 0
            elif clear_run >= cfg.clear_windows:
                event("shift_cleared", w, f"{lane} is back at the reference for {clear_run} windows; alarm cleared.", reason="recovered", **ctx)
                phase, s_dn, s_up, xs, alarm_n, clear_run = "OK", 0.0, 0.0, [], 0, 0
        res.rows.append(Row(idx, w.n, cap, x, s_dn, s_up, phase, confounded))
    res.phase = phase if res.rows else "NO_DATA"
    if res.phase == "LEARNING":
        res.phase = f"LEARNING({len(learn)}/{cfg.baseline_windows})"
    return res


def recognize(records, cfg) -> dict:
    lanes, side = extract(records, cfg)
    window_s = cfg.window_hours * 3600.0
    results = {lane: analyze_lane(lane, wins, side["resets"], cfg, window_s) for lane, wins in sorted(lanes.items())}
    side["demand"] = classify_demand(results, side)
    results["__side__"] = side
    return results


def classify_demand(results: dict, side: dict, idle_fraction: float = 0.25) -> list[dict]:
    """Per window with passive data: is a low delivered rate demand-limited (idle capacity) or evidence-free?

    Low delivered throughput is NEVER a capacity signal by itself (it can be low demand, our own caps, request-slot limits or a slow
    backend). Only the probe lanes drive shift recommendations; this labels what the passive numbers can and cannot say.
    """

    lane = next((k for k in ("w_fresh_qd4", "w_fresh_qd1") if k in results), None)
    rows = {r.start: r for r in results[lane].rows} if lane else {}
    out = []
    for w, peak in sorted(side["passive_peak"].items()):
        row = rows.get(w)
        if row is None:
            out.append({"window": w, "label": "no_probe_evidence", "peak_w_bps": peak,
                        "text": "no qualifying probe window: a low delivered rate here says nothing about capacity"})
        elif row.x is not None and row.x > -0.5 and peak < idle_fraction * row.capability:
            out.append({"window": w, "label": "demand_limited", "peak_w_bps": peak, "probe_capability_bps": row.capability,
                        "text": "probes show idle capacity; the low delivered rate is demand-limited, not a capacity drop"})
        elif row.x is not None and row.x <= -0.5 and peak < idle_fraction * (results[lane].reference or row.capability):
            out.append({"window": w, "label": "consistent_not_evidence", "peak_w_bps": peak, "probe_capability_bps": row.capability,
                        "text": "probes are degraded and the delivered rate is low: consistent, but the passive rate alone would not prove it"})
        else:
            out.append({"window": w, "label": "load_present", "peak_w_bps": peak, "probe_capability_bps": row.capability,
                        "text": "delivered rate is a sizeable fraction of probe capability (a lower bound on capability)"})
    return out


def pattern_hint(results: dict) -> str | None:
    """A cautious cross-lane reading (a hypothesis, never a classification or an action)."""

    def alarmed(prefix: str):
        return [r for k, r in results.items() if k != "__side__" and lane_base(k) == prefix and r.phase.startswith("ALARM")]

    thr = [r for p in THROUGHPUT_LANES for r in alarmed(p)]
    lat = [r for p in LATENCY_LANES for r in alarmed(p)]
    if thr and not lat:
        return ("throughput lanes shifted while single-request latency lanes did not: pattern fits a bandwidth/drain-bound change "
                "(hypothesis; the probes cannot see the provider side)")
    if lat and not thr:
        return "latency lanes shifted while throughput lanes did not: pattern fits added per-request latency (hypothesis)"
    if lat and thr:
        return "latency and throughput lanes both shifted: a broad backend change (hypothesis)"
    return None


def new_events(results: dict, known_keys: set) -> list[dict]:
    out = []
    for lane, res in results.items():
        if lane == "__side__":
            continue
        for ev in res.events:
            if ev["evidence"]["key"] not in known_keys:
                out.append(ev)
    return out
