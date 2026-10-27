"""Shift recognizer: per-lane CLOSED-window medians -> persisted robust baseline -> two-sided CUSUM with hysteresis -> RECOMMENDATIONS.

One code path serves the runner (incremental, state persisted in `agg.json`) and `analyze` (streams the journal through the same
`Engine`), so they cannot disagree. Memory is bounded by the number of lanes: only the currently open window of each lane and a
short ring of recent rows are kept; the journal is write-mostly. Its only outputs are recommendation events; nothing in this
module can touch a cap, a model, a queue or a cgroup file.

Metric. Every lane is turned into a CAPABILITY (higher is better) per window:
    latency lanes     capability = 1e6 / median latency in us      (= operations per second at queue depth 1)
    throughput lanes  capability = median durable (or read) rate in B/s
and x = log2(capability / reference). x = -1 means "2x worse", x = -4.3 means "~20x worse".

Windows are 24 h and only CLOSED windows are evaluated: a partial day never produces a verdict. A window closes when a record of a
later window arrives or when `close_up_to(now)` passes its end.

CUSUM (log2 units). S_down = max(0, S_down - x - k_eff), S_up = max(0, S_up + x - k_eff), both capped at cap_mult * h_eff so a long
collapse does not need an equally long time to unwind. sigma is the baseline noise: a MAD estimate shrunk toward a documented prior
(prior_sigma_log2, weight prior_weight) with a floor, so a lucky quiet baseline cannot produce an over-sensitive detector.
k_eff = max(k, sigma), h_eff = max(h, 3 sigma). A window counts toward confirmation only if S >= h_eff AND the window itself is
beyond the slack (a one-off outlier leaves S high for days and must not be confirmed by normal days after it), and
`confirm_windows` consecutive such windows are needed; windows missing in between (more than `max_gap_windows`) break the streak.
An alarm clears only after `clear_windows` consecutive windows back within the slack, or flips explicitly.
The reference/sigma/phase are PERSISTED and the reference is frozen: it changes only through an operator `baseline_reset` note,
which never expires.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import asdict, dataclass, field

LATENCY_LANES = ("r_cold", "r_witness", "w_over_4k")
THROUGHPUT_LANES = ("w_fresh_qd1", "w_fresh_qd4", "r_seq")
MAX_WINDOW_VALUES = 4096
MAX_ROWS = 60
AGG_VERSION = 1


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
    capped: int = 0  # records whose value sat at the probe's own rate ceiling (a lower bound only)
    records: int = 0

    def add(self, vals: list, n: int, epoch, capped: bool = False) -> None:
        self.values.extend(vals)
        if len(self.values) > MAX_WINDOW_VALUES:
            self.values = self.values[::2]  # decimate: memory per open window is bounded
        self.n += n
        self.records += 1
        self.capped += 1 if capped else 0
        if epoch:
            self.epochs.add(epoch)

    def median(self):
        return statistics.median(self.values) if self.values else None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["epochs"] = sorted(self.epochs)
        return d

    @staticmethod
    def from_dict(d: dict) -> "Window":
        return Window(d["start"], d["n"], list(d["values"]), set(d["epochs"]), d.get("capped", 0), d.get("records", 0))


def window_index(t: float, cfg) -> int:
    return int(t // (cfg.window_hours * 3600.0))


def points_of(rec: dict):
    """One journal record -> [(lane, values, n, epoch, capped)]. Only trustworthy evidence yields points."""

    typ = rec["type"]
    out = []
    if typ == "probe":
        # samples that did not bypass the page cache are not evidence of the device; non-quiet ones are contended
        if rec.get("quiet") and rec.get("direct") is not False and rec["lane"] in LATENCY_LANES and rec["lat_us"]:
            out.append((f"{rec['lane']}@{rec['device']}", [v for v in rec["lat_us"] if v > 0] or [1], rec["n"], rec.get("epoch"), False))
    elif typ == "bench":
        for name, lane in rec["lanes"].items():
            if not lane.get("valid"):
                continue
            base = lane_base(name)
            if base == "w_over_4k":
                vals = [v for v in lane["lat"]["lat_us"] if v > 0] or [1]
                out.append((name, vals, lane.get("n", len(vals)), rec.get("settings_epoch"), False))
            elif base in ("w_fresh_qd1", "w_fresh_qd4"):
                out.append((name, [lane["rate_durable_bps"]], 1, rec.get("settings_epoch"), bool(lane.get("at_cap"))))
            elif base == "r_seq":
                out.append((name, [lane["rate_bps"]], 1, rec.get("settings_epoch"), bool(lane.get("at_cap"))))
    return out


def extract(records, cfg) -> tuple[dict, dict]:
    """records -> ({lane: {window_idx: Window}}, {'resets': [t...], 'passive_peak': {window: bps}}). Test/inspection helper."""

    lanes: dict[str, dict[int, Window]] = {}
    side = {"resets": [], "passive_peak": {}, "records": 0}
    for rec in records:
        side["records"] += 1
        t = rec["t"]
        idx = window_index(t, cfg)
        for lane, vals, n, epoch, capped in points_of(rec):
            lanes.setdefault(lane, {}).setdefault(idx, Window(idx)).add(vals, n, epoch, capped)
        if rec["type"] == "passive":
            side["passive_peak"][idx] = max(side["passive_peak"].get(idx, 0.0), _passive_peak(rec))
        elif rec["type"] == "note" and rec.get("kind") == "baseline_reset":
            side["resets"].append(t)
    return lanes, side


def _passive_peak(rec: dict) -> float:
    peak = 0.0
    for d in rec["devices"].values():
        if d and d.get("w_bps"):
            peak = max(peak, d["w_bps"])
    return peak


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


def mad_sigma(values: list[float]) -> float:
    med = statistics.median(values)
    return 1.4826 * statistics.median([abs(v - med) for v in values])


class LaneState:
    """Everything the detector needs for one lane, JSON-serialisable (this is what is persisted)."""

    def __init__(self, lane: str, cfg, max_events: int | None = None):
        self.lane, self.cfg, self.max_events = lane, cfg, max_events
        self.rows: list[Row] = []
        self.events: list[dict] = []
        self.phase = "LEARNING"
        self.reference_log2: float | None = None
        self.sigma = cfg.noise_floor_log2
        self.s_dn = self.s_up = 0.0
        self.xs: list[float] = []
        self.alarm_n = 0
        self.clear_run = 0
        self.learn: list[float] = []
        self.learn_capped = 0
        self.ref_capped = False
        self.ref_epochs: set = set()
        self.history: list[list] = []  # [window, log2 capability, capped, epochs] of the latest valid windows
        self.latest: float | None = None
        self.last_valid_window: int | None = None
        self.last_idx: int | None = None
        self.insufficient_windows = 0
        self.reset_cursor: float | None = None
        self.windows_seen = 0

    # -- views -----------------------------------------------------------------------------------------------
    @property
    def reference(self):
        return None if self.reference_log2 is None else 2 ** self.reference_log2

    def ratio(self):
        return self.latest / self.reference if self.reference and self.latest else None

    @property
    def display_phase(self) -> str:
        if self.phase == "LEARNING":
            return f"LEARNING({len(self.learn)}/{self.cfg.baseline_windows})" if self.rows else "NO_DATA"
        return self.phase

    # -- persistence -----------------------------------------------------------------------------------------
    def to_dict(self) -> dict:
        d = {k: v for k, v in self.__dict__.items() if k not in ("cfg", "rows", "ref_epochs", "events")}
        d["rows"] = [asdict(r) for r in self.rows[-MAX_ROWS:]]
        d["ref_epochs"] = sorted(self.ref_epochs)
        d["events"] = self.events[-20:]
        return d

    @staticmethod
    def from_dict(d: dict, cfg, max_events: int | None = 20) -> "LaneState":
        s = LaneState(d["lane"], cfg, max_events)
        for k, v in d.items():
            if k == "rows":
                s.rows = [Row(**r) for r in v]
            elif k == "ref_epochs":
                s.ref_epochs = set(v)
            else:
                setattr(s, k, v)
        return s

    # -- the detector ----------------------------------------------------------------------------------------
    def _event(self, kind: str, w: Window, text: str, out: list, **evidence) -> None:
        evidence.update({"category": "capability", "window_start": w.start, "key": f"{self.lane}|{kind}|{w.start}",
                         "reference": self.reference})
        ev = {"lane": self.lane, "kind": kind, "text": text, "evidence": evidence}
        self.events.append(ev)
        if self.max_events is not None and len(self.events) > self.max_events:
            self.events = self.events[-self.max_events:]
        out.append(ev)

    def _apply_reset(self, w: Window, out: list) -> None:
        recent = self.history[-self.cfg.rebaseline_windows:]
        if not recent:
            return
        self.reference_log2 = statistics.median([h[1] for h in recent])
        self.ref_capped = sum(1 for h in recent if h[2]) * 2 > len(recent)
        self.ref_epochs = {e for h in recent for e in h[3]}
        self.s_dn = self.s_up = 0.0
        self.phase, self.xs, self.alarm_n, self.clear_run = "OK", [], 0, 0
        self._event("baseline_reset", w, "baseline reset by operator note; reference re-established from the latest windows", out,
                    from_windows=len(recent))

    def step(self, w: Window, window_s: float, resets: list[float]) -> list[dict]:
        """Process one CLOSED window. Returns the events it produced."""

        cfg, out = self.cfg, []
        if self.reset_cursor is None:
            self.reset_cursor = max([r for r in resets if r < w.start * window_s], default=-math.inf)
        for r in resets:
            if self.reset_cursor < r < w.start * window_s:
                self._apply_reset(w, out)
                self.reset_cursor = r
        self.windows_seen += 1
        cap = _capability(self.lane, w)
        need = cfg.min_samples.get(lane_base(self.lane), 1)
        if cap is None or w.n < need:
            self.insufficient_windows += 1
            return out
        idx = w.start
        capped = w.capped * 2 > max(1, w.records)
        lv = math.log2(cap)
        gap = 0 if self.last_idx is None else idx - self.last_idx - 1
        self.last_idx = idx
        self.history.append([idx, lv, capped, sorted(w.epochs)])
        self.history = self.history[-max(cfg.rebaseline_windows, 3):]
        self.latest, self.last_valid_window = cap, idx
        if self.phase == "LEARNING":
            self.learn.append(lv)
            self.learn_capped += 1 if capped else 0
            self.ref_epochs |= w.epochs
            self.rows.append(Row(idx, w.n, cap, None, 0.0, 0.0, "LEARNING"))
            if len(self.learn) >= cfg.baseline_windows:
                m = len(self.learn)
                self.reference_log2 = statistics.median(self.learn)
                prior = cfg.prior_sigma_log2
                est = math.sqrt((cfg.prior_weight * prior ** 2 + m * mad_sigma(self.learn) ** 2) / (cfg.prior_weight + m))
                self.sigma = max(cfg.noise_floor_log2, est)
                self.ref_capped = self.learn_capped * 2 > m
                self.phase = "OK"
                self._event("baseline_established", w, f"baseline established from {m} windows", out, sigma_log2=round(self.sigma, 3))
            self.rows = self.rows[-MAX_ROWS:]
            return out
        x = lv - self.reference_log2
        k_eff = max(cfg.cusum_k, self.sigma)
        h_eff = max(cfg.cusum_h, 3.0 * self.sigma)
        cap_s = cfg.cusum_cap_mult * h_eff
        self.s_dn = min(cap_s, max(0.0, self.s_dn - x - k_eff))
        self.s_up = min(cap_s, max(0.0, self.s_up + x - k_eff))
        confounded = bool(self.ref_epochs and w.epochs and not (w.epochs & self.ref_epochs))
        ctx = {"x_log2": round(x, 3), "s_down": round(self.s_dn, 3), "s_up": round(self.s_up, 3), "n": w.n, "confounded": confounded,
               "capped": capped}
        note = " Settings changed since the baseline, the shift may be self-inflicted." if confounded else ""
        if self.phase in ("OK", "SUSPECT_DOWN", "SUSPECT_UP"):
            if gap > cfg.max_gap_windows:  # windows are missing: the earlier bad windows are not adjacent evidence
                self.phase, self.xs = "OK", []
            # a window only counts toward confirmation if it is itself beyond the slack
            dirn = "down" if (self.s_dn >= h_eff and x <= -k_eff) else ("up" if (self.s_up >= h_eff and x >= k_eff) else None)
            if dirn is None:
                self.phase, self.xs = "OK", []
            else:
                want = "SUSPECT_" + dirn.upper()
                self.xs = self.xs + [x] if self.phase == want else [x]
                self.phase = want
                if len(self.xs) >= cfg.confirm_windows:
                    self.phase, self.alarm_n, self.clear_run = "ALARM_" + dirn.upper(), len(self.xs), 0
                    factor = 2 ** abs(statistics.median(self.xs))
                    lower_bound = (dirn == "down" and self.ref_capped) or (dirn == "up" and capped)
                    qual = "by at least " if lower_bound else ""
                    self._event("shift_detected", w,
                                f"capability shifted {dirn} {qual}~{factor:.1f}x on {self.lane}"
                                + (" (the probe sat at its own rate ceiling: a lower bound, capability >= the measured value)" if lower_bound else "")
                                + ", baseline reset suggested (verify the cause, then run `iogov annotate --baseline-reset`)." + note,
                                out, direction=dirn, factor=round(factor, 2), factor_is_lower_bound=lower_bound,
                                baseline_reset_suggested=True, **ctx)
        else:
            down = self.phase == "ALARM_DOWN"
            self.xs.append(x)
            self.alarm_n += 1
            flipped = (self.s_up >= h_eff) if down else (self.s_dn >= h_eff)
            self.clear_run = self.clear_run + 1 if abs(x) < k_eff else 0
            if flipped:
                self._event("shift_cleared", w, f"{self.lane} moved through the reference to the opposite side; previous alarm closed.",
                            out, reason="flip", **ctx)
                self.phase = "SUSPECT_UP" if down else "SUSPECT_DOWN"
                self.xs, self.alarm_n, self.clear_run = [x], 1, 0
            elif self.clear_run >= cfg.clear_windows:
                self._event("shift_cleared", w, f"{self.lane} is back at the reference for {self.clear_run} windows; alarm cleared.",
                            out, reason="recovered", **ctx)
                self.phase, self.s_dn, self.s_up, self.xs, self.alarm_n, self.clear_run = "OK", 0.0, 0.0, [], 0, 0
        self.rows.append(Row(idx, w.n, cap, x, self.s_dn, self.s_up, self.phase, confounded))
        self.rows = self.rows[-MAX_ROWS:]
        return out


def analyze_lane(lane: str, windows: dict, resets: list, cfg, window_s: float) -> LaneState:
    """Run a lane over already-built windows (all treated as closed). Same LaneState code as the streaming engine."""

    st = LaneState(lane, cfg)
    for idx in sorted(windows):
        st.step(windows[idx], window_s, sorted(resets))
    return st


class Engine:
    """Streaming recognizer. `feed` records in time order, `close_up_to(now)` to close elapsed windows, `drain()` the new events."""

    def __init__(self, cfg, data: dict | None = None, keep_events: bool = False):
        self.cfg = cfg
        self.window_s = cfg.window_hours * 3600.0
        self.keep_events = keep_events
        self.lanes: dict[str, LaneState] = {}
        self.open: dict[str, Window] = {}
        self.resets: list[float] = []
        self.passive_peak: dict[int, float] = {}
        self.horizon = -(10 ** 12)  # every window with a smaller index is closed
        self.records = 0
        self.late = 0
        self.first_t: float | None = None
        self._new: list[dict] = []
        if data:
            self._load(data)

    # -- persistence ---------------------------------------------------------------------------------------
    def to_dict(self) -> dict:
        return {
            "v": AGG_VERSION, "horizon": self.horizon, "records": self.records, "late": self.late, "first_t": self.first_t,
            "resets": self.resets[-50:], "passive_peak": {str(k): v for k, v in sorted(self.passive_peak.items())[-MAX_ROWS:]},
            "lanes": {k: v.to_dict() for k, v in self.lanes.items()},
            "open": {k: w.to_dict() for k, w in self.open.items()},
        }

    def _load(self, d: dict) -> None:
        if d.get("v") != AGG_VERSION:
            return
        self.horizon, self.records, self.late, self.first_t = d["horizon"], d["records"], d.get("late", 0), d.get("first_t")
        self.resets = list(d["resets"])
        self.passive_peak = {int(k): v for k, v in d["passive_peak"].items()}
        self.lanes = {k: LaneState.from_dict(v, self.cfg, None if self.keep_events else 20) for k, v in d["lanes"].items()}
        self.open = {k: Window.from_dict(w) for k, w in d["open"].items()}

    # -- feeding -------------------------------------------------------------------------------------------
    def add_reset(self, t: float) -> None:
        if t not in self.resets:
            self.resets.append(t)
            self.resets.sort()

    def feed(self, rec: dict) -> None:
        t = rec["t"]
        idx = window_index(t, self.cfg)
        self._advance(idx)
        if idx < self.horizon:
            self.late += 1
            return
        self.records += 1
        if self.first_t is None:
            self.first_t = t
        for lane, vals, n, epoch, capped in points_of(rec):
            self.open.setdefault(lane, Window(idx)).add(vals, n, epoch, capped)
        if rec["type"] == "passive":
            self.passive_peak[idx] = max(self.passive_peak.get(idx, 0.0), _passive_peak(rec))
        elif rec["type"] == "note" and rec.get("kind") == "baseline_reset":
            self.add_reset(t)

    def _advance(self, idx: int) -> None:
        if idx <= self.horizon:
            return
        for lane in sorted(self.open):
            w = self.open[lane]
            if w.start < idx:
                del self.open[lane]
                st = self.lanes.get(lane)
                if st is None:
                    st = self.lanes[lane] = LaneState(lane, self.cfg, None if self.keep_events else 20)
                self._new += st.step(w, self.window_s, self.resets)
        self.horizon = idx
        for k in [k for k in self.passive_peak if k < idx - MAX_ROWS]:
            del self.passive_peak[k]

    def close_up_to(self, now: float) -> None:
        """Close every window that has fully elapsed at `now` (the window containing `now` stays open)."""

        self._advance(window_index(now, self.cfg))

    def close_all(self) -> None:
        self._advance(max([w.start for w in self.open.values()], default=self.horizon) + 1)

    def drain(self) -> list[dict]:
        out, self._new = self._new, []
        return out

    # -- views ---------------------------------------------------------------------------------------------
    def results(self) -> dict:
        res: dict = dict(sorted(self.lanes.items()))
        side = {"resets": list(self.resets), "passive_peak": dict(self.passive_peak), "records": self.records, "late": self.late}
        res["__side__"] = side
        side["demand"] = classify_demand(res, side)
        return res


def recognize(records, cfg, keep_events: bool = True) -> dict:
    """All records treated as complete (every window closed). Records are sorted by time first (test/helper convenience)."""

    eng = Engine(cfg, keep_events=keep_events)
    for rec in sorted(records, key=lambda r: r["t"]):
        eng.feed(rec)
    eng.close_all()
    return eng.results()


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
