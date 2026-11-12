"""Value semantics for the P88 query core (Contract 3).

Every value the engine summarizes DECLARES exactly one of six semantics —
``gauge``, ``rate``, ``counter_delta``, ``integral``, ``event_count`` or
``state_duration`` — and the reducer is chosen from that declaration, never
guessed from the number.

The percentile math is REUSED from P54/P70 (``report._nearest_rank_percentile``)
rather than re-implemented, per the handoff.  The reset-aware rate derivation
mirrors ``report._derive_rate`` exactly so a gauge/rate summary is byte-for-byte
differential-equal to ``topos report`` on the same recording and window; this
module only ADDS reset counting and the counter/integral/state reducers on top.

Canonical semantic per metric name:
    * names ending ``_per_s`` / ``_bps`` / ``_pps`` / ``_iops`` and ``cpu_pct``
      → ``rate`` (interval rates, derivable from embedded raw counters);
    * every other registry metric → ``gauge`` (point-in-time value, including
      bounded percentages such as PSI and headroom).

Compatible non-canonical readings a caller may request explicitly:
    * on a gauge: ``integral`` (∫ v dt) and ``state_duration`` (time per value);
    * on a rate: ``counter_delta`` and ``event_count`` (Σ positive raw deltas,
      requires embedded raw counters) and ``integral`` (∫ rate dt).
Anything else is an ``IncompatibleQueryError``.
"""

from __future__ import annotations

import bisect
import math
from dataclasses import dataclass
from enum import Enum

from topos.model import Frame
from topos.registry import REGISTRY
from topos.report import _nearest_rank_percentile

from .errors import IncompatibleQueryError, InvalidQueryError

# Reuse report's rounding grain so figures diff byte-identically with `topos
# report` and across two query invocations.
_ROUND_DIGITS = 6

# Cap distinct states so a state_duration reading cannot blow up cardinality.
MAX_STATES = 64


class ValueSemantic(str, Enum):
    GAUGE = "gauge"
    RATE = "rate"
    COUNTER_DELTA = "counter_delta"
    INTEGRAL = "integral"
    EVENT_COUNT = "event_count"
    STATE_DURATION = "state_duration"


_RATE_SUFFIXES = ("_per_s", "_bps", "_pps", "_iops")
_EXPLICIT_RATES = frozenset({"cpu_pct", "proc_cpu_pct", "proc_cpu_host_pct"})


def canonical_semantic(name: str) -> ValueSemantic:
    """Return the canonical value semantic for a registry metric name."""
    if name.endswith(_RATE_SUFFIXES) or name in _EXPLICIT_RATES:
        return ValueSemantic.RATE
    return ValueSemantic.GAUGE


_GAUGE_COMPATIBLE = frozenset(
    {ValueSemantic.GAUGE, ValueSemantic.INTEGRAL, ValueSemantic.STATE_DURATION}
)
_RATE_COMPATIBLE = frozenset(
    {
        ValueSemantic.RATE,
        ValueSemantic.COUNTER_DELTA,
        ValueSemantic.EVENT_COUNT,
        ValueSemantic.INTEGRAL,
    }
)


def resolve_semantic(name: str, requested: str | None) -> ValueSemantic:
    """Resolve the effective semantic for a metric ref.

    ``requested is None`` → the canonical semantic.  A requested semantic must be
    in the closed enum AND compatible with the metric's canonical family; both
    failures raise a typed error (unknown value → InvalidQueryError, incompatible
    combination → IncompatibleQueryError).
    """
    if name not in REGISTRY:
        raise InvalidQueryError(f"unknown metric: {name!r}")
    canon = canonical_semantic(name)
    if requested is None:
        return canon
    try:
        sem = ValueSemantic(requested)
    except ValueError:
        valid = ", ".join(s.value for s in ValueSemantic)
        raise InvalidQueryError(
            f"unknown value semantic {requested!r} for {name!r}; expected one of: {valid}"
        ) from None
    compatible = _GAUGE_COMPATIBLE if canon == ValueSemantic.GAUGE else _RATE_COMPATIBLE
    if sem not in compatible:
        allowed = ", ".join(s.value for s in sorted(compatible, key=lambda s: s.value))
        raise IncompatibleQueryError(
            f"value semantic {sem.value!r} is not compatible with {name!r} "
            f"(a {canon.value}); compatible: {allowed}"
        )
    return sem


def _round(v: float | None) -> float | None:
    if v is None:
        return None
    return round(v, _ROUND_DIGITS)


def _finite_number(value: object) -> float | None:
    """Return a finite float, rejecting bools/None/non-numeric/non-finite."""
    if value is None or isinstance(value, bool):
        return None
    if not isinstance(value, (int, float)):
        return None
    f = float(value)
    return f if math.isfinite(f) else None


# ---------------------------------------------------------------------------
# Per-(entity, metric) sample extraction over ordered windowed source frames.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class _Point:
    """One retained (timestamp, MetricValue) observation for a metric."""

    ts: float
    v: float | None
    raw: int | None
    src: str
    # For state_duration: the raw JSON-ish value (may be a string/int state).
    state: object
    # The sampling interval of the frame this point came from (0 = unknown).
    interval: float = 0.0


# An inter-sample distance larger than this many sampling intervals is a hole
# (entity absent, daemon outage, stalled collector), not a continuous span.
# Same factor the engine uses to report temporal gaps in ``meta.gaps``.
GAP_FACTOR = 1.5


def gap_limit(points: list[_Point]) -> float | None:
    """Largest bridgeable distance between two points, or None when unknown."""
    intervals = [p.interval for p in points if p.interval and p.interval > 0]
    return GAP_FACTOR * max(intervals) if intervals else None


def collect_points(
    frames: list[Frame], entity_key: str, metric: str
) -> list[_Point]:
    """Gather the ordered observations of *metric* for *entity_key*.

    *frames* must already be window-filtered and ascending in time.  Absent
    entities/metrics contribute no point (never a fabricated zero).
    """
    points: list[_Point] = []
    for frame in frames:
        ef = frame.entities.get(entity_key)
        if ef is None:
            continue
        mv = ef.metrics.get(metric)
        if mv is None:
            continue
        points.append(
            _Point(
                ts=frame.ts,
                v=_finite_number(mv.v),
                raw=mv.raw if isinstance(mv.raw, int) and not isinstance(mv.raw, bool) else None,
                src=mv.src,
                state=mv.v,
                interval=frame.interval_s if frame.interval_s and frame.interval_s > 0 else 0.0,
            )
        )
    return points


# ---------------------------------------------------------------------------
# Reducers.  Each returns a JSON-ready dict AND a reset count.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Summary:
    """A metric's reduced summary plus the resets observed producing it."""

    semantic: ValueSemantic
    stats: dict[str, object]
    sample_count: int
    resets: int


def _gauge_stats(values: list[float]) -> dict[str, object]:
    if not values:
        return {"count": 0, "min": None, "mean": None, "p50": None, "p95": None, "max": None}
    s = sorted(values)
    return {
        "count": len(s),
        "min": _round(s[0]),
        "mean": _round(sum(s) / len(s)),
        "p50": _round(_nearest_rank_percentile(s, 50)),
        "p95": _round(_nearest_rank_percentile(s, 95)),
        "max": _round(s[-1]),
    }


def _rate_pairs(
    points: list[_Point], max_dt: float | None = None
) -> tuple[list[tuple[float, float]], int]:
    """Reset-aware per-interval (ts, rate) pairs, mirroring report._derive_rate.

    A live ``v`` is used directly.  Otherwise a ``src=='derived'`` point with a
    raw counter is derived as ``delta(raw)/delta(ts)`` against the nearest
    earlier point that carries a raw counter; a negative raw delta is a counter
    reset — the interval yields no pair and increments the reset count.  Each
    emitted pair carries the timestamp of the point that produced it, so
    consumers that need positions (the integral reducer) never re-pair samples
    with points positionally.

    For a pure gauge series (every ``v`` present) this degenerates to the
    ``(ts, v)`` observations themselves with zero resets.
    """
    pairs: list[tuple[float, float]] = []
    resets = 0
    for idx, point in enumerate(points):
        if point.v is not None:
            pairs.append((point.ts, point.v))
            continue
        if point.src != "derived" or point.raw is None:
            continue
        for prior in range(idx - 1, -1, -1):
            earlier = points[prior]
            if earlier.raw is None:
                continue
            raw_delta = point.raw - earlier.raw
            ts_delta = point.ts - earlier.ts
            if raw_delta < 0:
                resets += 1
                break
            if ts_delta <= 0:
                break
            if max_dt is not None and ts_delta > max_dt:
                # The nearest earlier counter is across a hole: smearing the
                # delta over it would invent a rate for time never observed.
                break
            pairs.append((point.ts, raw_delta / ts_delta))
            break
    return pairs, resets


def _rate_samples(points: list[_Point]) -> tuple[list[float], int]:
    """Reset-aware per-interval rate samples (values of :func:`_rate_pairs`)."""
    pairs, resets = _rate_pairs(points, gap_limit(points))
    return [value for _, value in pairs], resets


def _counter_total(points: list[_Point]) -> tuple[float, int, int]:
    """Sum positive raw deltas over consecutive raw-bearing points, reset-aware.

    Returns ``(total, interval_count, resets)``.  A negative delta is a reset:
    the pre-reset interval is dropped from the total and counted as a reset.
    """
    total = 0.0
    intervals = 0
    resets = 0
    prev: int | None = None
    for point in points:
        if point.raw is None:
            continue
        if prev is not None:
            delta = point.raw - prev
            if delta < 0:
                resets += 1
            else:
                total += delta
                intervals += 1
        prev = point.raw
    return total, intervals, resets


def _integral_of_series(pairs: list[tuple[float, float]]) -> tuple[float, float]:
    """Trapezoidal ∫ over ordered (ts, value) pairs; returns (integral, span)."""
    integral, span, _covered = _integral_covered(pairs, None)
    return integral, span


def _integral_covered(
    pairs: list[tuple[float, float]],
    max_dt: float | None,
    point_ts: list[float] | None = None,
) -> tuple[float, float, float]:
    """Trapezoidal ∫ that does not bridge holes wider than *max_dt*.

    A hole is a distance wider than *max_dt* between consecutive *observations*
    (``point_ts``; the pair timestamps when not given).  Pairs dropped for a
    counter reset leave no hole: the frames around them were still sampled.

    Returns ``(integral, span, covered)``: ``span`` is first-to-last timestamp,
    ``covered`` the time actually integrated (``covered < span`` means the
    series had holes that were left out rather than interpolated across).
    """
    if len(pairs) < 2:
        return 0.0, 0.0, 0.0
    obs = point_ts if point_ts is not None else [t for t, _ in pairs]
    # holes[i] counts hole-sized steps among obs[0..i]; an interval (t0, t1)
    # contains a hole when the count differs between its end observations.
    holes = [0]
    for prev, cur in zip(obs, obs[1:]):
        holes.append(holes[-1] + (1 if max_dt is not None and cur - prev > max_dt else 0))
    integral = 0.0
    covered = 0.0
    for (t0, v0), (t1, v1) in zip(pairs, pairs[1:]):
        dt = t1 - t0
        if dt <= 0:
            continue
        i0 = bisect.bisect_left(obs, t0)
        i1 = bisect.bisect_right(obs, t1) - 1
        if holes[i1] - holes[i0] > 0:
            continue
        integral += (v0 + v1) / 2.0 * dt
        covered += dt
    span = pairs[-1][0] - pairs[0][0]
    return integral, span, covered


def summarize(points: list[_Point], semantic: ValueSemantic) -> Summary:
    """Reduce *points* under the declared *semantic*."""
    if semantic == ValueSemantic.GAUGE:
        values = [p.v for p in points if p.v is not None]
        return Summary(semantic, _gauge_stats(values), len(values), 0)

    if semantic == ValueSemantic.RATE:
        samples, resets = _rate_samples(points)
        stats = _gauge_stats(samples)
        stats["resets"] = resets
        return Summary(semantic, stats, len(samples), resets)

    if semantic in (ValueSemantic.COUNTER_DELTA, ValueSemantic.EVENT_COUNT):
        total, intervals, resets = _counter_total(points)
        if semantic == ValueSemantic.EVENT_COUNT:
            stats: dict[str, object] = {
                "events": int(total),
                "intervals": intervals,
                "resets": resets,
            }
        else:
            stats = {
                "total": _round(total),
                "intervals": intervals,
                "resets": resets,
            }
        return Summary(semantic, stats, intervals, resets)

    if semantic == ValueSemantic.INTEGRAL:
        # Integrate the natural per-frame magnitude: a gauge integrates its
        # value; a rate integrates its reset-aware interval rate. _rate_pairs
        # carries each sample's own timestamp (and degenerates to the (ts, v)
        # observations for a pure gauge series), so no positional re-pairing
        # can misalign a sample with another point's timestamp.
        limit = gap_limit(points)
        canon_values, resets = _rate_pairs(points, limit)
        integral, span, covered = _integral_covered(canon_values, limit, [p.ts for p in points])
        stats = {
            "integral": _round(integral),
            "span_s": _round(span),
            "covered_s": _round(covered),
            "count": len(canon_values),
            "resets": resets,
        }
        return Summary(semantic, stats, len(canon_values), resets)

    # state_duration
    durations: dict[str, float] = {}
    count = 0
    limit = gap_limit(points)
    for cur, nxt in zip(points, points[1:]):
        if cur.state is None:
            continue
        key = _state_key(cur.state)
        dt = nxt.ts - cur.ts
        if dt <= 0:
            continue
        if limit is not None and dt > limit:
            # Do not credit the last state with time nobody observed.
            continue
        durations[key] = durations.get(key, 0.0) + dt
        count += 1
        if len(durations) > MAX_STATES:
            raise IncompatibleQueryError(
                f"state_duration exceeded {MAX_STATES} distinct states"
            )
    span = points[-1].ts - points[0].ts if len(points) >= 2 else 0.0
    stats = {
        "states": {k: _round(durations[k]) for k in sorted(durations)},
        "intervals": count,
        "covered_s": _round(sum(durations.values())),
        "span_s": _round(span),
    }
    return Summary(semantic, stats, count, 0)


def _state_key(state: object) -> str:
    if isinstance(state, bool):
        return "true" if state else "false"
    if isinstance(state, float):
        return repr(round(state, _ROUND_DIGITS))
    return str(state)


def current_value(mv_v: object) -> object:
    """Return a JSON-safe current value (finite float rounded, else raw)."""
    num = _finite_number(mv_v)
    if num is not None:
        return _round(num)
    return mv_v
