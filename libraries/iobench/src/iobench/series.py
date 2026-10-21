"""Time-series reduction (IOB-SER-01..05).

A series is a list of ``(t_seconds, rate)`` pairs.  ``reduce_series`` turns it
into a :class:`Reading` that names its own state (``steady``, ``flat``,
``unsteady``) so no caller ever uses a mean or a burst as a capability.
"""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from typing import Any, Sequence

Point = tuple[float, float]

MIN_POINTS = 8
KNEE_BAND = 0.15
UNSTEADY_CV = 0.20
UNSTEADY_TREND = 0.20
RATE_LIMITED_CV = 0.01
RATE_LIMITED_WINDOW_S = 60.0
MAX_STORED_POINTS = 120
MEDIAN_WINDOW = 5
MEDIAN_HALF = MEDIAN_WINDOW // 2  # a rolling median of window 5 removes any burst of <= 2 samples


class SeriesError(ValueError):
    """The series cannot be reduced to a trustworthy reading."""


class TooFewPoints(SeriesError):
    """Fewer than 8 points: the stage fails with ``insufficient_points`` (IOB-SLO-02)."""


class IntegrityError(SeriesError):
    """NaN, infinity, negative values or non-monotonic timestamps: a data-integrity failure (IOB-PLA-14)."""


@dataclass(frozen=True)
class Reading:
    state: str
    steady: float
    mean: float
    knee_s: float | None
    burst_units: float
    observation_s: float
    rate_limited: Any  # True | False | "not_assessed"
    tail_cv: float
    n: int

    @property
    def steady_certified(self) -> bool:
        return self.state in ("steady", "flat")

    def as_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "steady": self.steady,
            "mean": self.mean,
            "knee_s": self.knee_s,
            "burst_units": self.burst_units,
            "observation_s": self.observation_s,
            "rate_limited": self.rate_limited,
            "tail_cv": self.tail_cv,
            "steady_certified": self.steady_certified,
        }


def validate(points: Sequence[Point]) -> None:
    if len(points) < MIN_POINTS:
        raise TooFewPoints(f"need at least {MIN_POINTS} points, got {len(points)}")
    last_t = None
    for i, (t, v) in enumerate(points):
        if not (math.isfinite(t) and math.isfinite(v)):
            raise IntegrityError(f"non-finite value at index {i}")
        if v < 0:
            raise IntegrityError(f"negative rate at index {i}")
        if last_t is not None and t <= last_t:
            raise IntegrityError(f"timestamps not strictly increasing at index {i}")
        last_t = t


def discard_warmup(points: Sequence[Point], warmup_s: float) -> list[Point]:
    """Drop the samples whose timestamp is within the warm-up window."""
    return [p for p in points if p[0] > warmup_s]


def rolling_median(values: Sequence[float], window: int = MEDIAN_WINDOW) -> list[float]:
    half = window // 2
    n = len(values)
    return [statistics.median(values[max(0, i - half):min(n, i + half + 1)]) for i in range(n)]


def _tail(n: int) -> int:
    return max(3, math.ceil(n * 0.25))


def _tail_trend(tail: Sequence[Point], steady: float) -> float:
    xs = [p[0] for p in tail]
    ys = [p[1] for p in tail]
    mx = sum(xs) / len(xs)
    my = sum(ys) / len(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
    return abs(slope * (xs[-1] - xs[0])) / steady


def _knee_index(smooth: Sequence[float], steady: float) -> int | None:
    n = len(smooth)
    k = n
    for j in range(n - 1, -1, -1):
        if abs(smooth[j] - steady) <= KNEE_BAND * steady:
            k = j
        else:
            break
    return k if k < 0.9 * n else None


def rate_limited(points: Sequence[Point]) -> Any:
    """True when the FINAL 60 s window (the one that defines ``steady``) has CV < 1 %; ``not_assessed`` for short series.

    An earlier flat window followed by a change is not a limiter: the figure that is reported is the tail's, so the
    flatness must hold there.  A window with fewer than ``MIN_POINTS`` samples is not assessed either (N10).
    """
    last = points[-1][0]
    if last - points[0][0] < RATE_LIMITED_WINDOW_S:
        return "not_assessed"
    window = [v for t, v in points if last - t <= RATE_LIMITED_WINDOW_S]
    if len(window) < MIN_POINTS:
        return "not_assessed"
    mean = sum(window) / len(window)
    return bool(mean > 0 and statistics.pstdev(window) / mean < RATE_LIMITED_CV)


def reduce_series(points: Sequence[Point]) -> Reading:
    validate(points)
    values = [p[1] for p in points]
    n = len(points)
    tail = list(points[n - _tail(n):])
    steady = statistics.median(p[1] for p in tail)
    if steady == 0:
        raise SeriesError("zero steady value")
    tail_mean = sum(p[1] for p in tail) / len(tail)
    tail_cv = statistics.pstdev(p[1] for p in tail) / tail_mean
    smooth = rolling_median(values)
    knee = _knee_index(smooth, steady)
    if knee is not None:
        # The median hides a leading burst of 1-2 samples (a one-sample 5 GB/s burst looks `flat`): the raw samples decide
        # where a leading burst ends, so the knee never precedes a raw sample that sits above the band.
        for i in range(MEDIAN_HALF):
            if values[i] > steady * (1 + KNEE_BAND):
                knee = max(knee, i + 1)
    trend = _tail_trend(tail, steady)
    if tail_cv > UNSTEADY_CV or trend > UNSTEADY_TREND or knee is None:
        state = "unsteady"
    elif knee == 0:
        state = "flat"
    else:
        state = "steady"
    burst = 0.0
    knee_s = None
    if knee is not None and state != "unsteady":
        knee_s = 0.0 if state == "flat" else points[knee][0]  # a flat series has no knee: it is flat from the start
        for i in range(knee):  # RAW samples: the smoothed series under-counts a short burst; noise cancels in the signed sum
            burst += (values[i] - steady) * (points[i + 1][0] - points[i][0])
        burst = max(0.0, burst)
    return Reading(
        state=state,
        steady=steady,
        mean=sum(values) / n,
        knee_s=knee_s,
        burst_units=burst,
        observation_s=points[-1][0],
        rate_limited=rate_limited(points),
        tail_cv=tail_cv,
        n=n,
    )


def mean_overstates(reading: Reading, ratio: float) -> bool:
    """True when the mean exceeds ``ratio`` x steady.  The ratio is required."""
    return reading.mean > ratio * reading.steady


def downsample(points: Sequence[Point], limit: int = MAX_STORED_POINTS) -> list[list[float]]:
    if len(points) <= limit:
        return [[t, v] for t, v in points]
    step = len(points) / limit
    return [[points[int(i * step)][0], points[int(i * step)][1]] for i in range(limit)]
