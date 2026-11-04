"""Distribution object, nearest-rank percentiles and machine-speed normalisation (SPEC sections 5 and 6).

Everything here is integer or plain-float arithmetic over measured values. A percentile is always one of the
measured samples (nearest rank); it is never interpolated, and it is ``None`` when the sample is too small for
the percentile to differ from the maximum.
"""

from __future__ import annotations

import math
from typing import Iterable

#: Percentiles reported by a distribution, in the order they appear in the object.
PERCENTILES = (50, 90, 98, 99)

#: Smallest sample size at which each percentile is reported (SPEC 5.3). p50 is always reported when n >= 1.
MIN_N = {50: 1, 90: 10, 98: 50, 99: 100}

#: Baseline single-core score time metrics are scaled to, keyed by Geekbench MAJOR version (SPEC 6). Geekbench 6 is
#: calibrated to 2500 on a Core i7-12700; Geekbench 7 is calibrated to 2500 on a Ryzen 7 7700. The two constants
#: are equal by coincidence of calibration only: scores of different majors are not comparable, so every
#: function that takes a score also takes the major and refuses a major that is not a key here.
BASELINES = {6: 2500, 7: 2500}

#: Major used for new measurements (operator decision 2026-11-05).
DEFAULT_MAJOR = 7

UNITS = ("s", "s_ref", "bytes", "ops", "ratio")


def nearest_rank(sorted_values: list[float], percent: int) -> float:
    """Return the element at 1-based rank ``ceil(percent * n / 100)`` of an ascending sample.

    The rank is computed with integer arithmetic, so no floating-point rounding can move it.
    """
    if isinstance(percent, bool) or not isinstance(percent, int) or not 1 <= percent <= 100:
        raise ValueError(f"percent must be an integer in 1..100, got {percent!r}")
    n = len(sorted_values)
    if n == 0:
        raise ValueError("nearest rank of an empty sample is undefined")
    rank = (percent * n + 99) // 100
    return sorted_values[rank - 1]


def _check_sample(values: Iterable[float]) -> list[float]:
    out: list[float] = []
    for v in values:
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise ValueError(f"sample values must be numbers, got {v!r}")
        if not math.isfinite(v):
            raise ValueError(f"sample values must be finite, got {v!r}")
        out.append(v)
    return out


def distribution(
    values: Iterable[float],
    *,
    unit: str,
    source: str,
    contaminated: int = 0,
    no_measurement: int = 0,
) -> dict:
    """Build the SPEC 5 distribution object from the measured sample.

    ``contaminated`` and ``no_measurement`` count subjects that were excluded before the sample reached this
    function; they are reported in ``n_excluded`` and never enter ``n``.
    """
    if unit not in UNITS:
        raise ValueError(f"unit must be one of {UNITS}, got {unit!r}")
    for name, count in (("contaminated", contaminated), ("no_measurement", no_measurement)):
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise ValueError(f"{name} must be a non-negative integer, got {count!r}")
    sample = sorted(_check_sample(values))
    n = len(sample)
    result: dict = {
        "unit": unit,
        "n": n,
        "n_excluded": {"contaminated": contaminated, "no_measurement": no_measurement},
    }
    for p in PERCENTILES:
        result[f"p{p}"] = nearest_rank(sample, p) if n >= MIN_N[p] else None
    result["max"] = sample[-1] if n else None
    result["source"] = source
    return result


def baseline(major: int) -> int:
    """The baseline score of a Geekbench major version; an unknown major is refused."""
    if isinstance(major, bool) or major not in BASELINES:
        raise ValueError(f"unsupported Geekbench major {major!r}; known: {sorted(BASELINES)}")
    return BASELINES[major]


def common_major(majors: Iterable[int]) -> int:
    """The one major shared by all ``majors``. Mixing majors (or an empty set) is refused, never converted."""
    distinct = set(majors)
    if len(distinct) != 1:
        raise ValueError(f"Geekbench majors must not be mixed, got {sorted(distinct)}")
    (major,) = distinct
    baseline(major)
    return major


def normalise(value: float, single_core: int, major: int) -> float:
    """Scale a time (``cpu_seconds`` or ``wall_seconds``) to the baseline core of Geekbench ``major``.

    ``x_ref = x * single_core / BASELINES[major]``. A faster host (higher score) did the same work in fewer
    seconds, so its seconds are scaled up to what the baseline core would have needed. ``single_core`` must be a
    score measured with that same major.
    """
    divisor = baseline(major)
    if isinstance(single_core, bool) or not isinstance(single_core, int) or single_core <= 0:
        raise ValueError(f"single_core must be a positive integer, got {single_core!r}")
    (checked,) = _check_sample([value])
    return checked * single_core / divisor
