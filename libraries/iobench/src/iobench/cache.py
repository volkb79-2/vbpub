"""Cache-state estimators (IOB-CCH-01..08): E-cold, E2, E1, classification, write time proof.

E-cold, E1 and E2 are READ-ONLY estimators.  Writes are proven by the time axis only.
"""
from __future__ import annotations

import bisect
import math
from dataclasses import dataclass
from itertools import accumulate
from typing import Sequence

from .devio import HIST_BINS, bin_value_us
from .series import Reading

E1_RULES_VERSION = 2  # bump when the E1 acceptance rules change: fixtures that sit near a limit must be regenerated
MASS_MIN = 0.05
BIMODAL_GAP = 2.5
OVERLAP_GAP = 1.5
VALLEY_MAX = 0.5
HIT_LIKE = 1.5
MISS_LIKE = 2.5
E2_GAP_MAX_S = 60.0
E2_TOLERANCE = (0.7, 1.43)
MARGIN = 1.2
PROOF_MIN_AFTER_S = 120.0
PROOF_BURST_FACTOR = 2.0


class HistError(ValueError):
    """A histogram that cannot be analysed (empty)."""


def aggregate_hist(lines: Sequence[tuple[float, Sequence[int]]], skip: float = 0.2) -> list[int]:
    """Sum the bins of the run after the first ``skip`` of its lines."""
    kept = lines[int(len(lines) * skip):]
    out = [0] * HIST_BINS
    for _, bins in kept:
        for i, c in enumerate(bins):
            out[i] += c
    return out


def hist_percentile_us(counts: Sequence[float], q: float) -> float:
    cum = list(accumulate(counts))
    if cum[-1] <= 0:
        raise HistError("empty histogram")
    return bin_value_us(min(bisect.bisect_left(cum, q / 100.0 * cum[-1]), len(cum) - 1))


@dataclass(frozen=True)
class Modes:
    shape: str  # unimodal | bimodal | overlapping
    p50_us: float
    modes_us: tuple[float, ...]
    masses: tuple[float, ...]
    valley_ratio: float | None
    h2: float | None
    t_hit_us: float | None
    t_miss_us: float | None

    def as_dict(self) -> dict:
        return {"shape": self.shape, "p50_us": self.p50_us, "modes_us": list(self.modes_us), "masses": list(self.masses),
                "valley_ratio": self.valley_ratio, "h2": self.h2, "t_hit_us": self.t_hit_us, "t_miss_us": self.t_miss_us}


_LOG_WIDTH = [0.0] * 64 + [math.log(bin_value_us(i + 1) / bin_value_us(i)) for i in range(64, HIST_BINS - 1)]
_LOG_WIDTH.append(_LOG_WIDTH[-1])


def _smooth(counts: Sequence[float]) -> list[float]:
    """3-bin moving average of the density per unit log-latency (bins are not equally wide)."""
    n = len(counts)
    dens = [c / w if w else 0.0 for c, w in zip(counts, _LOG_WIDTH)]
    return [sum(dens[max(0, i - 1):min(n, i + 2)]) / len(dens[max(0, i - 1):min(n, i + 2)]) for i in range(n)]


def _peaks(smooth: Sequence[float]) -> list[int]:
    n = len(smooth)
    return [i for i in range(n) if smooth[i] > 0 and (i == 0 or smooth[i] >= smooth[i - 1]) and (i == n - 1 or smooth[i] > smooth[i + 1])]


def _valley(smooth: Sequence[float], a: int, b: int) -> int:
    return min(range(a, b + 1), key=lambda i: (smooth[i], i))


def modes(counts: Sequence[float]) -> Modes:
    """Modes of a full latency histogram (IOB-CCH-02; thresholds are [I] until P4)."""
    total = float(sum(counts))
    if total <= 0:
        raise HistError("empty histogram")
    smooth = _smooth(counts)
    peaks = _peaks(smooth)
    while True:
        bounds = [0] + [_valley(smooth, a, b) for a, b in zip(peaks, peaks[1:])] + [len(counts)]
        masses = [sum(counts[bounds[k]:bounds[k + 1]]) / total for k in range(len(peaks))]
        weak = [k for k, m in enumerate(masses) if m < MASS_MIN]
        near = [k for k in range(len(peaks) - 1) if bin_value_us(peaks[k + 1]) < OVERLAP_GAP * bin_value_us(peaks[k])]
        if weak and len(peaks) > 1:
            del peaks[min(weak, key=lambda k: masses[k])]
        elif near:
            k = near[0]
            del peaks[k if smooth[peaks[k]] < smooth[peaks[k + 1]] else k + 1]
        else:
            break
    p50 = hist_percentile_us(counts, 50)
    order = sorted(range(len(peaks)), key=lambda k: -masses[k])[:2]
    chosen = sorted(order, key=lambda k: peaks[k])
    lats = tuple(bin_value_us(peaks[k]) for k in chosen)
    mass = tuple(masses[k] for k in chosen)
    if len(chosen) == 1:
        return Modes("unimodal", p50, lats, mass, None, None, None, None)
    lo, hi = peaks[chosen[0]], peaks[chosen[1]]
    gap = lats[1] / lats[0]
    v = _valley(smooth, lo, hi)
    ratio = smooth[v] / min(smooth[lo], smooth[hi])
    if gap >= BIMODAL_GAP and ratio <= VALLEY_MAX:
        return Modes("bimodal", p50, lats, mass, ratio, sum(counts[:v]) / total, lats[0], lats[1])
    return Modes("overlapping", p50, lats, mass, ratio, None, None, None)


def classify_unimodal(p50_us: float, hit_ref_us: float | None, rate_limited: object) -> str:
    """``hit-like`` | ``miss-like`` | ``indeterminate`` (needs ``hit_ref_us``)."""
    if hit_ref_us is None:
        return "indeterminate"
    if p50_us <= HIT_LIKE * hit_ref_us:
        return "hit-like"
    if p50_us >= MISS_LIKE * hit_ref_us or rate_limited is True:
        return "miss-like"
    return "indeterminate"


@dataclass(frozen=True)
class ColdProbe:
    r_cold: float
    rate_limited: object
    shape: str  # unimodal | bimodal | overlapping
    cls: str  # hit-like | miss-like | indeterminate | n/a (non-unimodal)
    p50_us: float
    region_bytes: int

    def as_dict(self) -> dict:
        return {"region_bytes": self.region_bytes, "R_cold_iops": self.r_cold, "shape": self.shape, "class": self.cls,
                "p50_us": self.p50_us, "rate_limited": self.rate_limited}


def cold_probe(reading: Reading, qd1: Modes, hit_ref_us: float | None, region_bytes: int) -> ColdProbe:
    cls = classify_unimodal(qd1.p50_us, hit_ref_us, reading.rate_limited) if qd1.shape == "unimodal" else "n/a"
    return ColdProbe(reading.steady, reading.rate_limited, qd1.shape, cls, qd1.p50_us, region_bytes)


@dataclass(frozen=True)
class E2:
    shape: str
    gap_ratio: float | None
    r_e2: float | None
    voided: bool

    def as_dict(self) -> dict:
        return {"shape": self.shape, "gap_ratio": self.gap_ratio, "R_E2_iops": self.r_e2, "voided": self.voided}


def e2_estimate(qd32_steady: float, qd1: Modes, qd32_start_s: float, qd1_start_s: float) -> E2:
    """R_E2 = steady(QD32) x (1 - h2) when the QD1 run is bimodal and adjacent (gap <= 60 s)."""
    voided = qd1_start_s - qd32_start_s > E2_GAP_MAX_S
    if voided or qd1.shape != "bimodal":
        return E2(qd1.shape, None, None, voided)
    return E2("bimodal", qd1.t_miss_us / qd1.t_hit_us, qd32_steady * (1 - qd1.h2), False)


# -- E1 (informational) ------------------------------------------------------------
@dataclass(frozen=True)
class E1:
    fit: str  # good | poor | insufficient | plateau
    points: int
    r2: float | None = None
    loo_max: float | None = None
    r_e1: float | None = None
    c_bytes: float | None = None
    margin_ok: bool | None = None

    def as_dict(self) -> dict:
        return {"fit": self.fit, "points": self.points, "r2": self.r2, "loo_max": self.loo_max, "R_E1_iops": self.r_e1,
                "C_bytes": self.c_bytes, "margin_ok": self.margin_ok}


def _line(xs: Sequence[float], ys: Sequence[float]) -> tuple[float, float] | None:
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx == 0:
        return None
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
    return my - slope * mx, slope


def e1_fit(post_cliff: Sequence[tuple[int, float]], f_cached: float | None) -> E1:
    """``1/iops = A + B/ws`` over the post-cliff rungs.  Never raises on degenerate input."""
    pts = sorted(post_cliff)
    n = len(pts)
    ys = [p[1] for p in pts]
    if n >= 3 and max(ys) <= 1.10 * min(ys):
        return E1("plateau", n)
    if n < 4 or not f_cached:
        return E1("insufficient", n)
    xs = [1.0 / (p[0] / (1 << 30)) for p in pts]
    inv = [1.0 / y for y in ys]
    line = _line(xs, inv)
    if line is None:
        return E1("poor", n)
    a, b = line
    mean = sum(inv) / n
    ss_tot = sum((v - mean) ** 2 for v in inv)
    ss_res = sum((v - (a + b * x)) ** 2 for x, v in zip(xs, inv))
    r2 = 1.0 if ss_tot == 0 else 1 - ss_res / ss_tot
    loo = 0.0
    for i in range(n):
        sub = _line(xs[:i] + xs[i + 1:], inv[:i] + inv[i + 1:])
        if sub is None or sub[0] + sub[1] * xs[i] <= 0:
            loo = math.inf
            break
        loo = max(loo, abs(1.0 / (sub[0] + sub[1] * xs[i]) - ys[i]) / ys[i])
    if a <= 0 or b >= 0:
        return E1("poor", n, r2, loo)
    r = 1.0 / a
    denom = 1.0 / f_cached - 1.0 / r
    c_gib = b / denom if denom != 0 else math.nan
    c_bytes = c_gib * (1 << 30)
    monotone = all(ys[i + 1] <= 1.05 * ys[i] for i in range(n - 1))
    good = (monotone and 0 < c_bytes < pts[0][0] and r <= min(ys) and r2 >= 0.97 and loo <= 0.15)
    margin = pts[-1][0] >= MARGIN * c_bytes if c_bytes > 0 else None
    return E1("good" if good else "poor", n, r2, loo, r, c_bytes, margin)


# -- classification (IOB-CCH-05) --------------------------------------------------
@dataclass(frozen=True)
class CacheState:
    state: str  # cold_measured | rate_cap | no_cache_observed | cold_hit_like | undetermined
    status: str  # proven | unproven
    reasons: tuple[str, ...]
    r: float | None  # the cache-free read rate this state vouches for
    r_cold: float | None
    r_e2: float | None

    def as_dict(self) -> dict:
        return {"cache_state": self.state, "status": self.status, "reasons": list(self.reasons), "R_iops": self.r,
                "R_cold_iops": self.r_cold, "R_E2_iops": self.r_e2}


def _within(ratio: float) -> bool:
    return E2_TOLERANCE[0] <= ratio <= E2_TOLERANCE[1]


NO_CACHE_RATIO = 0.9


def classify_cache(cold: ColdProbe | None, e2: E2 | None, *, cliff_bytes: float | None, largest_rung: int,
                   quick: bool = False, hit_ref_us: float | None = None, cached_ref: float | None = None,
                   cache_excludable: bool = True) -> CacheState:
    """The table of IOB-CCH-05 (``cache_excludable`` is False for a file target, whose size bounds what can be shown)."""
    r_cold = cold.r_cold if cold else None
    r_e2 = e2.r_e2 if e2 else None
    if cold is None:
        reasons = ["quick_profile"] if quick else ["cache_residual_possible"]
        state, r = "undetermined", None
    elif cold.rate_limited is True:
        state, r, reasons = "rate_cap", cold.r_cold, []
    elif cold.shape == "unimodal" and cold.cls == "miss-like":
        if r_e2 is not None and not _within(cold.r_cold / r_e2):
            state, r, reasons = "undetermined", None, ["cache_model_undetermined"]
        else:
            state, r, reasons = "cold_measured", min(cold.r_cold, r_e2) if r_e2 is not None else cold.r_cold, []
    elif (cold.shape == "unimodal" and cold.cls in ("hit-like", "indeterminate") and cliff_bytes is None and cache_excludable
          and cached_ref is not None and cold.r_cold >= NO_CACHE_RATIO * cached_ref):
        # the cold region runs as fast as the warm one and there is no cliff: caching does not change the sustained rate
        state, r, reasons = "no_cache_observed", cold.r_cold, []
    elif cold.shape == "unimodal" and cold.cls == "hit-like":
        state, r = "cold_hit_like", None
        reasons = ["device_smaller_than_cache_margin" if cliff_bytes is not None else "no_cliff_hit_like_mode"]
    else:
        state, r, reasons = "undetermined", None, ["cache_model_undetermined"]
    if cliff_bytes is not None and largest_rung < MARGIN * cliff_bytes and "device_smaller_than_cache_margin" not in reasons:
        reasons.append("device_smaller_than_cache_margin")
    status = "proven" if not reasons else "unproven"
    return CacheState(state, status, tuple(reasons), r if status == "proven" else None, r_cold, r_e2)


@dataclass(frozen=True)
class ReadValue:
    value: float
    status: str
    reasons: tuple[str, ...]


def read_value(state: CacheState, measured_largest: float, e1: E1 | None) -> ReadValue:
    """The read cap basis of IOB-CCH-06: never above the measured largest-rung rate."""
    if state.status == "proven":
        return ReadValue(min(measured_largest, state.r), "proven", ())
    if state.r_cold is not None:
        value = min(measured_largest, state.r_cold)
    elif e1 is not None and e1.fit == "good":
        value = min(measured_largest, e1.r_e1)
    else:
        value = measured_largest
    return ReadValue(value, "unproven", state.reasons)


# -- write time proof (IOB-CCH-07) --------------------------------------------------
@dataclass(frozen=True)
class TimeProof:
    proven: bool
    reason: str | None
    observation_s: float
    knee_s: float | None
    burst_bytes: float
    after_knee_bytes: float

    def as_dict(self) -> dict:
        return {"observation_s": self.observation_s, "knee_s": self.knee_s, "burst_bytes": self.burst_bytes,
                "after_knee_bytes": self.after_knee_bytes, "proven": self.proven, "reason": self.reason}


def write_time_proof(reading: Reading, bs: int) -> TimeProof:
    """Steady, >= 120 s after the knee, bytes after the knee >= 2 x the burst bytes."""
    knee = reading.knee_s
    burst = reading.burst_units * bs
    if not reading.steady_certified:
        return TimeProof(False, "short_observation", reading.observation_s, None, burst, 0.0)
    after_s = reading.observation_s - knee
    after = reading.steady * after_s * bs
    ok = after_s >= PROOF_MIN_AFTER_S and after >= PROOF_BURST_FACTOR * burst
    return TimeProof(ok, None if ok else "short_observation", reading.observation_s, knee, burst, after)
