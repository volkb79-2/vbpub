"""groop report — steady-state profile from a P2 recording.

Reads a JSONL or JSONL.zst recording in the P2 header+frame format and computes
per-entity or per-slice percentiles (p50/p95/max) for a fixed set of gauges,
plus derived rates from embedded raw counters when the recorded live rate is
``None``.

Threshold gating (P61): assertions consume the already-computed
``GroupProfile`` list — they NEVER recompute or re-read frames.  Pass
``--assert GROUP:METRIC:STAT<=VALUE`` (or ``>=``) to get exit code 1 when a
bound is breached.

Module-level exports (the public API):
    compute_profile — main computation entry point
    report_to_jsonable — deterministic JSON serialization
    parse_window_spec — manual window string → (start_ts, end_ts) or None
    detect_steady_window — pure trailing-window stability detector
    evaluate_assertions — threshold gating helper (pure, no argparse dependency)
    Assertion — assertion spec dataclass
    AssertionResult — single assertion evaluation result
"""

from __future__ import annotations

import json
import math
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from groop.model import Frame
from groop.record.reader import RecordReader

# ---------------------------------------------------------------------------
# Fixed gauge set — see handoff §Requirements
# ---------------------------------------------------------------------------
REPORT_GAUGES: tuple[str, ...] = (
    "ram",
    "anon",
    "z_pool",
    "z_eq",
    "swap_disk",
    "psi_mem_some_avg10",
    "psi_mem_full_avg10",
    "psi_io_some_avg10",
    "psi_io_full_avg10",
    "psi_cpu_some_avg10",
    "psi_cpu_full_avg10",
)
"""Fixed set of gauge metrics included in every report."""


_RATE_SUFFIXES = ("_per_s", "_bps", "_pps", "_iops")
"""Suffixes identifying rate metrics that may need raw-counter derivation."""


def _is_rate_metric(name: str) -> bool:
    """Return True if *name* is a rate metric that should be derived.

    Matches ``_per_s`` metrics (refault, mem_events), IO rate metrics
    (``_bps``, ``_iops``), and network rate metrics (``_bps``, ``_pps``)
    as described in the P54 handoff.
    """
    return name.endswith(_RATE_SUFFIXES)


# ---------------------------------------------------------------------------
# Assertion types (P61 — threshold gating)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Assertion:
    """One parsed ``--assert GROUP:METRIC:STAT<=VALUE`` specification.

    Attributes:
        group: Profile key to match (entity key or slice key).
        metric: Gauge or rate metric name present in the report.
        stat: Percentile/statistic — ``"p50"``, ``"p95"``, or ``"max"``.
        op: Comparison operator — ``"<="`` or ``">="``.
        value: Threshold value (float).
    """
    group: str
    metric: str
    stat: str
    op: str
    value: float


_ASSERT_PATTERN = re.compile(
    r"^"
    r"(?P<group>[^:]*)"
    r":(?P<metric>[^:]+)"
    r":(?P<stat>p50|p95|max)"
    r"(?P<op><=|>=)"
    r"(?P<value>[+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)"
    r"$"
)


def parse_assert_spec(spec: str) -> Assertion:
    """Parse a single ``--assert`` string into an ``Assertion``.

    Raises ``ValueError`` with a descriptive message on malformed input,
    unknown stat, or non-finite/NaN-like value.
    """
    m = _ASSERT_PATTERN.match(spec.strip())
    if m is None:
        raise ValueError(
            f"invalid --assert spec {spec!r} — expected "
            f"GROUP:METRIC:STAT<=VALUE or GROUP:METRIC:STAT>=VALUE"
        )
    group = m.group("group")
    metric = m.group("metric")
    stat = m.group("stat")
    op = m.group("op")
    value_str = m.group("value")

    try:
        value = float(value_str)
    except ValueError:
        raise ValueError(
            f"invalid --assert value {value_str!r} — not a number"
        )

    if math.isnan(value) or math.isinf(value):
        raise ValueError(
            f"invalid --assert value {value_str!r} — must be finite"
        )

    return Assertion(group=group, metric=metric, stat=stat, op=op, value=value)


@dataclass
class AssertionResult:
    """Outcome of evaluating one assertion against the report.

    Serialized under the ``"assertions"`` top-level key in the JSON output.
    """
    group: str
    metric: str
    stat: str
    op: str
    threshold: float
    actual: float | None
    passed: bool
    reason: str | None = None


def _find_profile_metric(
    profile: GroupProfile,
    metric: str,
) -> dict[str, float | None] | None:
    """Look up *metric* (gauge or rate) in *profile*.

    Returns the ``{p50, p95, max}`` dict, or ``None`` if the metric is absent.
    """
    if metric in profile.gauges:
        return profile.gauges[metric]
    if metric in profile.rates:
        return profile.rates[metric]
    return None


def evaluate_assertions(
    profiles: list[GroupProfile],
    assertions: list[Assertion],
) -> list[AssertionResult]:
    """Evaluate threshold assertions against the already-computed profiles.

    This is a pure function with no file or argparse I/O — independently
    unit-testable.

    Args:
        profiles: The ``GroupProfile`` list from ``compute_profile``.
        assertions: Parsed ``Assertion`` specs.

    Returns:
        Sorted list of ``AssertionResult`` (sorted by group, metric, stat, op
        for deterministic output).
    """
    results: list[AssertionResult] = []

    # Build a lookup from group key -> GroupProfile
    profile_map: dict[str, GroupProfile] = {}
    for p in profiles:
        profile_map[p.key] = p

    for a in assertions:
        profile = profile_map.get(a.group)

        # Absent group -> breach
        if profile is None:
            results.append(AssertionResult(
                group=a.group,
                metric=a.metric,
                stat=a.stat,
                op=a.op,
                threshold=a.value,
                actual=None,
                passed=False,
                reason="group not present in report",
            ))
            continue

        metric_result = _find_profile_metric(profile, a.metric)

        # Absent metric -> breach
        if metric_result is None:
            results.append(AssertionResult(
                group=a.group,
                metric=a.metric,
                stat=a.stat,
                op=a.op,
                threshold=a.value,
                actual=None,
                passed=False,
                reason="metric not present in report",
            ))
            continue

        actual = metric_result.get(a.stat)

        # Null stat -> breach  (single-frame rate, degenerate window)
        if actual is None:
            results.append(AssertionResult(
                group=a.group,
                metric=a.metric,
                stat=a.stat,
                op=a.op,
                threshold=a.value,
                actual=None,
                passed=False,
                reason="stat is null (e.g. single-frame rate, degenerate window)",
            ))
            continue

        if a.op == "<=":
            passed = actual <= a.value
        else:
            passed = actual >= a.value

        results.append(AssertionResult(
            group=a.group,
            metric=a.metric,
            stat=a.stat,
            op=a.op,
            threshold=a.value,
            actual=actual,
            passed=passed,
            reason=None if passed else f"breached: {actual} {a.op} {a.value}",
        ))

    # Deterministic sort: group, metric, stat, op
    results.sort(key=lambda r: (r.group, r.metric, r.stat, r.op))
    return results


def assertion_result_to_jsonable(r: AssertionResult) -> dict[str, object]:
    """Convert one ``AssertionResult`` to a JSON-compatible dict.

    Floats are rounded to 6 decimal places for byte-determinism.
    """
    d: dict[str, object] = {
        "group": r.group,
        "metric": r.metric,
        "stat": r.stat,
        "op": r.op,
        "threshold": _round_float(r.threshold),
        "actual": _round_float(r.actual) if r.actual is not None else None,
        "passed": r.passed,
    }
    if r.reason is not None:
        d["reason"] = r.reason
    return d


# ---------------------------------------------------------------------------
# Percentile computation (nearest-rank per 2026-07-12 amendment)
# ---------------------------------------------------------------------------

def _nearest_rank_percentile(sorted_samples: list[float], p: int) -> float:
    """Nearest-rank percentile of *sorted_samples* (ascending, non-``None``).

    Index = ceil(p/100 * N) - 1  (0-based).  Returns the sample at that
    position.  ``p=100`` always returns the last element.
    """
    if not sorted_samples:
        raise ValueError("cannot compute percentile from empty sample set")
    n = len(sorted_samples)
    index = max(0, math.ceil(p / 100.0 * n) - 1)
    return sorted_samples[index]


def _max_value(sorted_samples: list[float]) -> float:
    """Return the maximum of *sorted_samples* (assumed ascending)."""
    return sorted_samples[-1]


# ---------------------------------------------------------------------------
# Window spec parsing
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class WindowRange:
    start_ts: float
    end_ts: float  # inclusive

_WINDOW_PATTERN = re.compile(r"^last:(\d+)s$|^all$")

DEFAULT_STABILITY_GAUGE = "ram"
DEFAULT_STABILITY_COV = 0.05
DEFAULT_MIN_FRAMES = 3


def parse_window_spec(spec: str, last_frame_ts: float) -> WindowRange | None:
    """Parse a ``--window`` flag value.

    Returns a ``WindowRange`` or ``None`` (for ``all``).  Raises
    ``ValueError`` on malformed specs.
    """
    if not isinstance(spec, str):
        raise ValueError("invalid window spec — expected 'all' or 'last:Ns'")
    spec = spec.strip()
    if spec == "all":
        return None
    m = _WINDOW_PATTERN.match(spec)
    if m is None:
        raise ValueError(f"invalid window spec {spec!r} — expected 'all' or 'last:Ns'")
    if not m.group(1):
        raise ValueError(f"invalid window spec {spec!r} — expected 'all' or 'last:Ns'")
    seconds = int(m.group(1))
    if seconds <= 0:
        raise ValueError(f"invalid window spec {spec!r} — duration must be positive")
    return WindowRange(start_ts=last_frame_ts - seconds, end_ts=last_frame_ts)


def _validate_stability_options(
    stability_gauge: str,
    stability_cov: float,
    min_frames: int,
) -> None:
    """Validate the fixed-domain auto-window detector inputs."""
    if not isinstance(stability_gauge, str) or stability_gauge not in REPORT_GAUGES:
        raise ValueError(
            f"invalid --stability-gauge {stability_gauge!r} — expected one of: "
            + ", ".join(REPORT_GAUGES)
        )
    if (
        isinstance(stability_cov, bool)
        or not isinstance(stability_cov, (int, float))
        or not math.isfinite(stability_cov)
        or stability_cov < 0
    ):
        raise ValueError("invalid --stability-cov — must be a finite non-negative number")
    if not isinstance(min_frames, int) or isinstance(min_frames, bool) or min_frames < 1:
        raise ValueError("invalid --min-frames — must be a positive integer")


def _finite_gauge_value(frame: Frame, entity_key: str, gauge: str) -> float | None:
    """Return one finite gauge value, rejecting absent/non-numeric values."""
    entity_frame = frame.entities.get(entity_key)
    if entity_frame is None:
        return None
    metric = entity_frame.metrics.get(gauge)
    if metric is None or metric.v is None or isinstance(metric.v, bool):
        return None
    if not isinstance(metric.v, (int, float)):
        return None
    value = float(metric.v)
    return value if math.isfinite(value) else None


@dataclass
class _ReverseRunningStats:
    """Numerically stable statistics for one suffix, extended backwards."""

    count: int = 0
    total: float = 0.0
    mean: float = 0.0
    m2: float = 0.0

    def add(self, value: float) -> None:
        """Add one value using Welford's stable online variance update."""
        self.count += 1
        self.total += value
        delta = value - self.mean
        self.mean += delta / self.count
        self.m2 += delta * (value - self.mean)


def detect_steady_window(
    frames: list[Frame],
    *,
    stability_gauge: str = DEFAULT_STABILITY_GAUGE,
    stability_cov: float = DEFAULT_STABILITY_COV,
    min_frames: int = DEFAULT_MIN_FRAMES,
) -> WindowRange | None:
    """Return the longest stable trailing frame window, if one exists.

    Every candidate is a suffix of *frames* with at least ``min_frames``.
    An entity is eligible only when it has a finite ``stability_gauge`` value
    in every frame of that suffix.  The busiest eligible entity is the one
    with the largest arithmetic mean (ties use lexical ``EntityKey`` order).
    Its population coefficient of variation (stddev / mean) must be at most
    ``stability_cov``.  A constant all-zero series has CoV 0; a zero-mean
    series with non-zero spread is rejected to avoid division by zero.

    Candidate suffixes are scanned from shortest to longest.  The last
    accepted candidate is therefore the longest accepted trailing window.
    ``None`` means no candidate satisfies the criterion.
    """
    _validate_stability_options(stability_gauge, stability_cov, min_frames)
    if len(frames) < min_frames:
        return None

    selected: WindowRange | None = None
    stats_by_entity: dict[str, _ReverseRunningStats] = {}
    last_index = len(frames) - 1

    # An entity absent or non-finite in the final frame can never be eligible
    # for a trailing suffix.  Thereafter extending a suffix by one frame either
    # adds one value to its running statistics or permanently disqualifies it.
    for entity_key in frames[last_index].entities:
        value = _finite_gauge_value(frames[last_index], entity_key, stability_gauge)
        if value is not None:
            stats = _ReverseRunningStats()
            stats.add(value)
            stats_by_entity[entity_key] = stats

    for start_index in range(last_index, -1, -1):
        frame = frames[start_index]
        if start_index != last_index:
            for entity_key, stats in list(stats_by_entity.items()):
                value = _finite_gauge_value(frame, entity_key, stability_gauge)
                if value is None:
                    del stats_by_entity[entity_key]
                else:
                    stats.add(value)

        candidate_len = last_index - start_index + 1
        if candidate_len < min_frames or not stats_by_entity:
            continue

        busiest_key = min(
            stats_by_entity,
            key=lambda key: (-stats_by_entity[key].total / stats_by_entity[key].count, key),
        )
        stats = stats_by_entity[busiest_key]
        mean = stats.total / stats.count
        variance = stats.m2 / stats.count
        stddev = math.sqrt(variance)
        if mean == 0:
            cov = 0.0 if stddev == 0 else math.inf
        else:
            cov = stddev / mean
        if cov <= stability_cov:
            selected = WindowRange(
                start_ts=frame.ts,
                end_ts=frames[last_index].ts,
            )
    return selected


@dataclass(frozen=True)
class WindowSelection:
    """Resolved report window and whether automatic detection succeeded."""
    mode: str
    window: WindowRange | None
    detected: bool = False


def select_report_window(
    frames: list[Frame],
    *,
    window_spec: str,
    stability_gauge: str = DEFAULT_STABILITY_GAUGE,
    stability_cov: float = DEFAULT_STABILITY_COV,
    min_frames: int = DEFAULT_MIN_FRAMES,
) -> WindowSelection:
    """Resolve a report window without changing profile computation."""
    _validate_stability_options(stability_gauge, stability_cov, min_frames)
    if not isinstance(window_spec, str):
        raise ValueError("invalid window spec — expected 'all', 'auto', or 'last:Ns'")
    if window_spec == "auto":
        detected = detect_steady_window(
            frames,
            stability_gauge=stability_gauge,
            stability_cov=stability_cov,
            min_frames=min_frames,
        )
        return WindowSelection(mode="auto", window=detected, detected=detected is not None)
    if window_spec == "all":
        return WindowSelection(mode="all", window=None)
    if not frames:
        # Validate the manual spelling even though no timestamp exists.
        parse_window_spec(window_spec, 0.0)
        return WindowSelection(mode="last", window=None)
    return WindowSelection(
        mode="last",
        window=parse_window_spec(window_spec, frames[-1].ts),
    )


# ---------------------------------------------------------------------------
# Filter frames by window
# ---------------------------------------------------------------------------

def _filter_frames_by_window(
    frames: list[Frame], window: WindowRange | None,
) -> list[Frame]:
    """Return frames whose ``ts`` falls within *window*.

    If *window* is ``None`` (``all``), return all frames unchanged.
    """
    if window is None:
        return frames
    return [f for f in frames if window.start_ts <= f.ts <= window.end_ts]


# ---------------------------------------------------------------------------
# Entity → slice ancestry
# ---------------------------------------------------------------------------

def _find_slice_ancestor(entity_key: str, frames: list[Frame]) -> str:
    """Return the nearest ``*.slice`` ancestor key for *entity_key*.

    Walks the parent chain across all available frames.  If the entity itself
    ends with ``.slice`` it is returned directly.  If no slice ancestor can
    be found, returns the entity's direct parent or the root (``""``).
    """
    # Build a parent map from the first frame that has this entity
    parent_map: dict[str, str] = {}
    for frame in frames:
        for ek, ef in frame.entities.items():
            if ek not in parent_map and ef.entity.parent is not None:
                parent_map[ek] = ef.entity.parent
            if ek not in parent_map:
                parent_map[ek] = ""

    # If the entity itself is a slice, return it
    if entity_key.endswith(".slice"):
        return entity_key

    # Walk the parent chain
    current = entity_key
    visited: set[str] = set()
    while current and current not in visited:
        visited.add(current)
        if current.endswith(".slice"):
            return current
        current = parent_map.get(current, "")
    # Fallback: direct parent or root
    return parent_map.get(entity_key, "")


# ---------------------------------------------------------------------------
# Rate derivation
# ---------------------------------------------------------------------------

def _derive_rate(
    metric_name: str,
    entity_key: str,
    frames: list[Frame],
    current_idx: int,
) -> float | None:
    """Derive a rate from raw counters when ``v is None and src == 'derived'``.

    Looks backward through *frames* (not requiring consecutive indices) for
    the nearest earlier frame that has the same entity/metric with a raw
    counter, then computes ``delta(raw) / delta(ts)``.
    """
    current_frame = frames[current_idx]
    current_mv = current_frame.entities.get(entity_key, None)
    if current_mv is None:
        return None
    mv = current_mv.metrics.get(metric_name)
    if mv is None or mv.raw is None:
        return None

    for idx in range(current_idx - 1, -1, -1):
        earlier = frames[idx]
        earlier_ef = earlier.entities.get(entity_key)
        if earlier_ef is None:
            continue
        earlier_mv = earlier_ef.metrics.get(metric_name)
        if earlier_mv is None or earlier_mv.raw is None:
            continue
        raw_delta = mv.raw - earlier_mv.raw
        if raw_delta < 0:
            # Counter reset — treat as unavailable
            return None
        ts_delta = current_frame.ts - earlier.ts
        if ts_delta <= 0:
            return None
        return raw_delta / ts_delta
    return None


# ---------------------------------------------------------------------------
# Group → samples accumulator
# ---------------------------------------------------------------------------

@dataclass
class _GaugeSamples:
    """Accumulated non-None samples for one gauge metric in one group."""
    values: list[float] = field(default_factory=list)


@dataclass
class _RateSamples:
    """Accumulated rate values for one rate metric in one group."""
    values: list[float] = field(default_factory=list)


def _group_frames(
    frames: list[Frame],
    group_by: str,
) -> dict[str, dict[str, object]]:
    """Accumulate per-group, per-metric samples from *frames*.

    Returns a dict mapping each group key to a sub-dict mapping metric names
    to ``_GaugeSamples`` or ``_RateSamples``.
    """
    groups: dict[str, dict[str, object]] = defaultdict(dict)

    for idx, frame in enumerate(frames):
        for entity_key, ef in frame.entities.items():
            # Determine group key
            if group_by == "slice":
                group_key = _find_slice_ancestor(entity_key, frames)
            else:
                group_key = entity_key

            for metric_name, mv in ef.metrics.items():
                if metric_name in REPORT_GAUGES:
                    if mv.v is not None:
                        samples = groups[group_key].get(metric_name)
                        if samples is None:
                            samples = _GaugeSamples()
                            groups[group_key][metric_name] = samples
                        samples.values.append(float(mv.v))

                elif _is_rate_metric(metric_name):
                    # Only derive if the metric spec kind is "derived" (checked by name pattern + src)
                    rate_v: float | None = None
                    if mv.v is not None:
                        rate_v = float(mv.v)
                    elif mv.src == "derived" and mv.raw is not None:
                        rate_v = _derive_rate(metric_name, entity_key, frames, idx)

                    if rate_v is not None:
                        samples = groups[group_key].get(metric_name)
                        if samples is None:
                            samples = _RateSamples()
                            groups[group_key][metric_name] = samples
                        samples.values.append(rate_v)

    return groups


# ---------------------------------------------------------------------------
# Main computation
# ---------------------------------------------------------------------------

@dataclass
class GroupProfile:
    """Profile for one group (entity or slice)."""
    key: str
    sample_count: int
    window_start_ts: float | None  # None if empty
    window_end_ts: float | None
    gauges: dict[str, dict[str, float | None]]  # metric → {p50, p95, max}
    rates: dict[str, dict[str, float | None]]    # metric → {p50, p95, max}


def _empty_metric_result() -> dict[str, float | None]:
    return {"p50": None, "p95": None, "max": None}


def _compute_metric_result(samples: list[float]) -> dict[str, float | None]:
    if not samples:
        return _empty_metric_result()
    s = sorted(samples)
    return {
        "p50": _nearest_rank_percentile(s, 50),
        "p95": _nearest_rank_percentile(s, 95),
        "max": _max_value(s),
    }


def compute_profile(
    frames: list[Frame],
    *,
    window: WindowRange | None = None,
    group_by: str = "entity",
) -> list[GroupProfile]:
    """Compute a steady-state profile from *frames*.

    Args:
        frames: All frames from a recording.
        window: Optional window range (``None`` = all frames).
        group_by: ``"entity"`` (per-EntityKey) or ``"slice"`` (per-*.slice ancestor).

    Returns:
        Sorted list of ``GroupProfile``, one per group that had any samples.
    """
    if not frames:
        return []

    windowed = _filter_frames_by_window(frames, window)
    if not windowed:
        return []

    # Determine the actual window bounds from frames
    window_start = min(f.ts for f in windowed)
    window_end = max(f.ts for f in windowed)

    groups = _group_frames(windowed, group_by)

    profiles: list[GroupProfile] = []
    for group_key in sorted(groups):
        metric_sets = groups[group_key]
        gauges: dict[str, dict[str, float | None]] = {}
        rates: dict[str, dict[str, float | None]] = {}
        total_samples = 0

        # Find the max sample count across metrics for this group
        for metric_name, samples_obj in metric_sets.items():
            if isinstance(samples_obj, _GaugeSamples):
                gauges[metric_name] = _compute_metric_result(samples_obj.values)
                total_samples = max(total_samples, len(samples_obj.values))
            elif isinstance(samples_obj, _RateSamples):
                rates[metric_name] = _compute_metric_result(samples_obj.values)
                total_samples = max(total_samples, len(samples_obj.values))

        profiles.append(GroupProfile(
            key=group_key,
            sample_count=total_samples,
            window_start_ts=window_start,
            window_end_ts=window_end,
            gauges=gauges,
            rates=rates,
        ))

    return profiles


# ---------------------------------------------------------------------------
# Deterministic JSON serialization
# ---------------------------------------------------------------------------

_ROUND_DIGITS = 6


def _round_float(v: float | None) -> float | None:
    if v is None:
        return None
    return round(v, _ROUND_DIGITS)


def _metric_jsonable(
    result: dict[str, float | None],
) -> dict[str, float | None]:
    return {k: _round_float(v) for k, v in result.items()}


def profile_to_jsonable(profile: GroupProfile) -> dict[str, Any]:
    """Convert one ``GroupProfile`` to a JSON-compatible dict."""
    d: dict[str, Any] = {
        "key": profile.key,
        "sample_count": profile.sample_count,
    }
    if profile.window_start_ts is not None:
        d["window_start_ts"] = profile.window_start_ts
    if profile.window_end_ts is not None:
        d["window_end_ts"] = profile.window_end_ts
    if profile.gauges:
        d["gauges"] = {
            k: _metric_jsonable(v)
            for k, v in sorted(profile.gauges.items())
        }
    if profile.rates:
        d["rates"] = {
            k: _metric_jsonable(v)
            for k, v in sorted(profile.rates.items())
        }
    return d


def report_to_jsonable(
    profiles: list[GroupProfile],
    assertions: list[AssertionResult] | None = None,
    window_selection: WindowSelection | None = None,
) -> dict[str, Any]:
    """Convert the full report to a deterministic JSON dict.

    Keys are sorted, floats are rounded to 6 decimal places, and output is
    suitable for ``json.dumps(..., sort_keys=True)``.

    If *assertions* is provided, an ``"assertions"`` block is included.
    """
    d: dict[str, Any] = {
        "profiles": [profile_to_jsonable(p) for p in profiles],
        "metrics_version": 1,
    }
    if assertions is not None:
        d["assertions"] = [
            assertion_result_to_jsonable(r) for r in assertions
        ]
    if window_selection is not None and window_selection.mode == "auto":
        d["window_mode"] = "auto"
        d["window_detected"] = window_selection.detected
        if window_selection.window is not None:
            d["window_start_ts"] = _round_float(window_selection.window.start_ts)
            d["window_end_ts"] = _round_float(window_selection.window.end_ts)
    return d


def format_report(
    profiles: list[GroupProfile],
    assertions: list[AssertionResult] | None = None,
    window_selection: WindowSelection | None = None,
) -> str:
    """Return a deterministic JSON string for the full report."""
    return json.dumps(
        report_to_jsonable(
            profiles,
            assertions=assertions,
            window_selection=window_selection,
        ),
        sort_keys=True,
        separators=(",", ":"),
    )


# ---------------------------------------------------------------------------
# Convenience: load + compute in one call
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ReportComputation:
    """Profiles plus the resolved window used to produce them."""
    profiles: list[GroupProfile]
    window_selection: WindowSelection


def compute_report_with_selection(
    path: Path,
    *,
    window_spec: str = "all",
    group_by: str = "entity",
    stability_gauge: str = DEFAULT_STABILITY_GAUGE,
    stability_cov: float = DEFAULT_STABILITY_COV,
    min_frames: int = DEFAULT_MIN_FRAMES,
) -> ReportComputation:
    """Load a recording and compute a steady-state profile.

    Args:
        path: Path to a ``.jsonl`` or ``.jsonl.zst`` recording.
        window_spec: ``"all"``, ``"last:Ns"``, or ``"auto"``.
        group_by: ``"entity"`` or ``"slice"``.

    Returns:
        Profiles plus the selected window metadata.

    Raises:
        FileNotFoundError: File does not exist.
        RuntimeError: ``.zst`` file without ``zstandard`` installed.
        ValueError: Malformed window spec or invalid group_by.
    """
    reader = RecordReader(path)
    frames = list(reader.iter_frames())
    selection = select_report_window(
        frames,
        window_spec=window_spec,
        stability_gauge=stability_gauge,
        stability_cov=stability_cov,
        min_frames=min_frames,
    )
    return ReportComputation(
        profiles=compute_profile(frames, window=selection.window, group_by=group_by),
        window_selection=selection,
    )


def compute_report(
    path: Path,
    *,
    window_spec: str = "all",
    group_by: str = "entity",
    stability_gauge: str = DEFAULT_STABILITY_GAUGE,
    stability_cov: float = DEFAULT_STABILITY_COV,
    min_frames: int = DEFAULT_MIN_FRAMES,
) -> list[GroupProfile]:
    """Load a recording and return its profiles (legacy convenience API)."""
    return compute_report_with_selection(
        path,
        window_spec=window_spec,
        group_by=group_by,
        stability_gauge=stability_gauge,
        stability_cov=stability_cov,
        min_frames=min_frames,
    ).profiles
