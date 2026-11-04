"""Two-ended-matching candidate scheduling for parallel native R2 (B207).

Pure, deterministic and clock-free: every time value is handed in by the
caller, so the scheduler replays identically under a fake executor.

Only the DISPATCH ORDER changes. A candidate's judgment is a function of its
own isolated clone, so results never depend on this module (design
``RESOURCE-PROFILES.md`` section 5.8). Predictions never feed classification,
buckets or budgets of the verdict.

The policy (design 5.4):

* classes (high, normal, low) come from the PREDICTED peak RSS relative to the
  lane's own mode, never from fixed MiB values;
* at most one high candidate runs at a time, and while it runs freed slots
  prefer lows; a queued high starts as soon as none runs;
* each class queue is ordered longest-predicted-first, ties by canonical index;
* admission: ``sum(predicted RSS of running) + predicted RSS(next) <= budget``,
  except that one candidate always runs; a queued high that does not fit stops
  further filling so highs cannot starve.
"""

from __future__ import annotations

import bisect
import collections
import heapq
import json
import statistics
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from .records import positional_record

HIGH = "high"
NORMAL = "normal"
LOW = "low"

DEFAULT_HIGH_FACTOR = 1.5
DEFAULT_LOW_FACTOR = 0.5
#: Width of the histogram bucket used to find the mode of predicted RSS.
MODE_BUCKET_BYTES = 50 * 2**20
#: Completions of one file in this run before its live median is trusted.
LIVE_MEDIAN_MIN_SAMPLES = 5
#: Admission estimate for a candidate with no RSS prediction at all. It is the
#: same conservative default run-gate's ``jobs=auto`` falls back to.
DEFAULT_CANDIDATE_RSS_BYTES = 2**30

SOURCE_PROFILE = "profile"
SOURCE_LIVE_FILE = "live-file-median"
SOURCE_HISTORY_FILE = "history-file-median"
SOURCE_HISTORY_LANE = "history-lane-median"
SOURCE_OPERATOR = "operator"
SOURCE_NONE = "none"
#: Lower number means a better source; the live file median (2) outranks the
#: history medians (3, 4), the operator medians (5) and no prediction (6).
SOURCE_RANK = {
    SOURCE_PROFILE: 1,
    SOURCE_LIVE_FILE: 2,
    SOURCE_HISTORY_FILE: 3,
    SOURCE_HISTORY_LANE: 4,
    SOURCE_OPERATOR: 5,
    SOURCE_NONE: 6,
}

#: assay 12 writes history/2: each entry carries a ``profile`` digest (the
#: resource profile's status, reason and ``rss_peak_bytes``). history/1, written
#: by assay 11.x with bare numeric ``rss_peak_bytes``, is still READ (old
#: history is evidence for predictions) and never written.
HISTORY_SCHEMA = "assay-schedule-history/2"
LEGACY_HISTORY_SCHEMA = "assay-schedule-history/1"
SCHEDULE_SCHEMA = "assay-schedule/1"
#: Deliberately not ``*.json`` (this one and the summary below): tools and
#: tests glob the state directory for candidate records (``<id>.json``), and
#: neither of these is one.
HISTORY_FILE_NAME = ".schedule-history.v1"
SCHEDULE_FILE_NAME = "schedule-summary.v1"
#: Bound on a history file this module will read back.
HISTORY_LIMIT_BYTES = 64 * 2**20


@positional_record
class Prediction:
    wall_seconds: float | None
    rss_peak_bytes: int | None
    source: str


@positional_record
class Thresholds:
    high_bytes: float
    low_bytes: float
    mode_median_bytes: float
    mode_p95_bytes: float


@positional_record
class HistoryEntry:
    """What one candidate measured: its own wall clock and, from its
    resource profile, the peak RSS (``None`` when the profile has no number).

    ``profile_status`` and ``profile_reason`` are the profile's status and
    reason code (``NO_MEASUREMENT`` plus a reason explains a missing RSS);
    both are ``None`` for history read from assay <= 11.
    """

    path: str
    wall_seconds: float | None
    rss_peak_bytes: int | None
    profile_status: str | None = None
    profile_reason: str | None = None


def _percentile(values: Sequence[float], q: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(q * len(ordered)))]


def derive_thresholds(
    rss_values: Iterable[float],
    *,
    high_factor: float = DEFAULT_HIGH_FACTOR,
    low_factor: float = DEFAULT_LOW_FACTOR,
) -> Thresholds | None:
    """Data-derived class thresholds (design 5.3), or ``None`` without data.

    The mode is the most populated 50 MiB bucket plus or minus one bucket;
    ``high > high_factor * p95(mode)`` and ``low < low_factor * median(mode)``.
    """
    values = [float(v) for v in rss_values]
    if not values:
        return None
    buckets = collections.Counter(int(v // MODE_BUCKET_BYTES) for v in values)
    # Deterministic tie-break: the most populated bucket, lowest index first.
    top = max(buckets.values())
    mode_bucket = min(b for b, count in buckets.items() if count == top)
    mode = [v for v in values if mode_bucket - 1 <= int(v // MODE_BUCKET_BYTES) <= mode_bucket + 1]
    median = statistics.median(mode)
    return Thresholds(
        high_bytes=high_factor * _percentile(mode, 0.95),
        low_bytes=low_factor * median,
        mode_median_bytes=median,
        mode_p95_bytes=_percentile(mode, 0.95),
    )


def classify(rss_peak_bytes: float | None, thresholds: Thresholds | None) -> str:
    if rss_peak_bytes is None or thresholds is None:
        return NORMAL
    if rss_peak_bytes > thresholds.high_bytes:
        return HIGH
    if rss_peak_bytes < thresholds.low_bytes:
        return LOW
    return NORMAL


# --------------------------------------------------------------------------
# History (source 1, 3 and 4): what the previous campaign measured.
# --------------------------------------------------------------------------


def parse_history(raw: bytes | str | None) -> tuple[dict[str, HistoryEntry], str | None]:
    """Parse a history document. A bad document yields an empty history and
    the reason, never an error: predictions only order work, so a corrupt
    side file must not stop a campaign or change a result."""
    if raw is None:
        return {}, None
    try:
        document = json.loads(raw)
    except (ValueError, UnicodeDecodeError, RecursionError) as exc:
        return {}, f"history is not valid JSON: {exc}"
    if (
        not isinstance(document, dict)
        or document.get("schema") not in (HISTORY_SCHEMA, LEGACY_HISTORY_SCHEMA)
        or not isinstance(document.get("candidates"), dict)
    ):
        return {}, "history has an unexpected schema"
    legacy = document["schema"] == LEGACY_HISTORY_SCHEMA
    entries: dict[str, HistoryEntry] = {}
    for identity, item in document["candidates"].items():
        if not isinstance(identity, str) or not isinstance(item, dict):
            continue
        path = item.get("path")
        wall = item.get("wall_seconds")
        status: str | None = None
        reason: str | None = None
        if legacy:
            # assay <= 11: the peak RSS is a bare numeric key.
            rss = item.get("rss_peak_bytes")
        else:
            profile = item.get("profile")
            if not isinstance(profile, dict):
                continue
            metrics = profile.get("metrics")
            if isinstance(metrics, dict):
                # A full `resource-profile/1` object (as a state record or a
                # profile line carries it): its wall clock and peak RSS.
                rss = metrics.get("rss_peak_bytes")
                if wall is None:
                    wall = profile.get("wall_seconds")
            else:
                rss = profile.get("rss_peak_bytes")
            raw_status, raw_reason = profile.get("status"), profile.get("reason")
            status = raw_status if isinstance(raw_status, str) else None
            reason = raw_reason if isinstance(raw_reason, str) else None
        if not isinstance(path, str):
            continue
        if isinstance(wall, bool) or not isinstance(wall, (int, float)) or wall < 0:
            wall = None
        if isinstance(rss, bool) or not isinstance(rss, int) or rss < 0:
            rss = None
        if wall is None and rss is None:
            continue
        entries[identity] = HistoryEntry(
            path, None if wall is None else float(wall), rss, status, reason
        )
    return entries, None


def render_history(entries: Mapping[str, HistoryEntry]) -> bytes:
    """history/2: the old numeric keys are never written."""
    document = {
        "schema": HISTORY_SCHEMA,
        "candidates": {
            identity: {
                "path": entry.path,
                "wall_seconds": entry.wall_seconds,
                "profile": {
                    "status": entry.profile_status,
                    "reason": entry.profile_reason,
                    "rss_peak_bytes": entry.rss_peak_bytes,
                },
            }
            for identity, entry in sorted(entries.items())
        },
    }
    return (json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _median(values: list[float]) -> float | None:
    return statistics.median(values) if values else None


def resolve_predictions(
    candidates: Sequence[tuple[str, str]],
    history: Mapping[str, HistoryEntry],
    *,
    operator_wall_seconds: float | None = None,
    operator_rss_bytes: int | None = None,
) -> list[Prediction]:
    """Initial predictions for ``(candidate_id, path)`` pairs, by source order.

    Wall time and RSS resolve independently inside a source: a profile that
    lacks an RSS falls through to the next source for that value only, and the
    reported source is the best one that supplied any value.
    """
    by_file_wall: dict[str, list[float]] = collections.defaultdict(list)
    by_file_rss: dict[str, list[float]] = collections.defaultdict(list)
    lane_wall: list[float] = []
    lane_rss: list[float] = []
    for entry in history.values():
        if entry.wall_seconds is not None:
            by_file_wall[entry.path].append(entry.wall_seconds)
            lane_wall.append(entry.wall_seconds)
        if entry.rss_peak_bytes is not None:
            by_file_rss[entry.path].append(float(entry.rss_peak_bytes))
            lane_rss.append(float(entry.rss_peak_bytes))
    lane_wall_median = _median(lane_wall)
    lane_rss_median = _median(lane_rss)
    file_wall_median = {p: _median(v) for p, v in by_file_wall.items()}
    file_rss_median = {p: _median(v) for p, v in by_file_rss.items()}

    predictions: list[Prediction] = []
    for identity, path in candidates:
        own = history.get(identity)
        wall: float | None = None
        rss: float | None = None
        rank = SOURCE_RANK[SOURCE_NONE]

        def take(source: str, new_wall: float | None, new_rss: float | None) -> None:
            nonlocal wall, rss, rank
            used = False
            if wall is None and new_wall is not None:
                wall, used = new_wall, True
            if rss is None and new_rss is not None:
                rss, used = new_rss, True
            if used:
                rank = min(rank, SOURCE_RANK[source])

        if own is not None:
            take(SOURCE_PROFILE, own.wall_seconds, own.rss_peak_bytes)
        take(SOURCE_HISTORY_FILE, file_wall_median.get(path), file_rss_median.get(path))
        take(SOURCE_HISTORY_LANE, lane_wall_median, lane_rss_median)
        take(SOURCE_OPERATOR, operator_wall_seconds, operator_rss_bytes)
        source = next(name for name, value in SOURCE_RANK.items() if value == rank)
        predictions.append(
            Prediction(wall, None if rss is None else int(rss), source)
        )
    return predictions


# --------------------------------------------------------------------------
# The scheduler.
# --------------------------------------------------------------------------


class TemScheduler:
    """Two-ended matching with longest-first among equals (design 5.4).

    ``next_candidate(now)`` returns the next candidate index to start, or
    ``None`` when the caller must wait for a completion. It never returns
    ``None`` while nothing is running and work remains (one candidate always
    runs). ``complete(...)`` releases a candidate and feeds the live medians.
    """

    def __init__(
        self,
        candidates: Sequence[tuple[str, str]],
        predictions: Sequence[Prediction],
        *,
        budget_bytes: int | None,
        workers: int,
        high_factor: float = DEFAULT_HIGH_FACTOR,
        low_factor: float = DEFAULT_LOW_FACTOR,
        default_rss_bytes: int = DEFAULT_CANDIDATE_RSS_BYTES,
    ) -> None:
        if len(candidates) != len(predictions):
            raise ValueError("one prediction per candidate is required")
        if budget_bytes is not None and budget_bytes < 1:
            raise ValueError("the admission budget must be a positive byte count")
        if workers < 1:
            raise ValueError("workers must be at least 1")
        self._ids = [identity for identity, _ in candidates]
        self._paths = [path for _, path in candidates]
        self._pred = list(predictions)
        self.budget_bytes = budget_bytes
        self.workers = workers
        self.high_factor = high_factor
        self.low_factor = low_factor
        self._default_rss = default_rss_bytes
        self.thresholds = derive_thresholds(
            (p.rss_peak_bytes for p in self._pred if p.rss_peak_bytes is not None),
            high_factor=high_factor,
            low_factor=low_factor,
        )
        self._class = [classify(p.rss_peak_bytes, self.thresholds) for p in self._pred]
        self._initial_pred = list(self._pred)
        self._initial_class = list(self._class)
        # Each queue is ascending by (wall, -index): the LAST entry is the
        # longest predicted, lowest canonical index, so ``pop()`` is O(1).
        self._queues: dict[str, list[tuple[float, int]]] = {HIGH: [], NORMAL: [], LOW: []}
        for index in range(len(self._ids)):
            self._queues[self._class[index]].append(self._key(index))
        for queue in self._queues.values():
            queue.sort()
        self._queued: set[int] = set(range(len(self._ids)))
        self._running: dict[int, float] = {}
        self._start_time: dict[int, float] = {}
        self.dispatch_order: list[int] = []
        self._live: dict[str, list[tuple[float | None, int | None]]] = collections.defaultdict(list)
        self._live_applied: set[str] = set()
        self._actual: dict[int, tuple[float, float, float | None]] = {}

    # -- queue helpers -----------------------------------------------------

    def _key(self, index: int) -> tuple[float, int]:
        wall = self._pred[index].wall_seconds
        return (0.0 if wall is None else float(wall), -index)

    def prediction(self, index: int) -> Prediction:
        return self._pred[index]

    def initial_prediction(self, index: int) -> Prediction:
        return self._initial_pred[index]

    def predicted_class(self, index: int) -> str:
        return self._class[index]

    def _rss_estimate(self, index: int) -> int:
        rss = self._pred[index].rss_peak_bytes
        return self._default_rss if rss is None else rss

    @property
    def remaining(self) -> int:
        return len(self._queued)

    def remaining_indices(self) -> list[int]:
        return sorted(self._queued)

    @property
    def running_predicted_bytes(self) -> int:
        return sum(self._rss_estimate(i) for i in self._running)

    @property
    def running_indices(self) -> list[int]:
        return sorted(self._running)

    # -- decisions ---------------------------------------------------------

    def next_candidate(self, now: float) -> int | None:
        if not self._queued:
            return None
        high_running = any(self._class[i] == HIGH for i in self._running)
        order = (LOW, NORMAL) if high_running else (HIGH, NORMAL, LOW)
        running_sum = self.running_predicted_bytes
        for name in order:
            queue = self._queues[name]
            if not queue:
                continue
            _, negative_index = queue[-1]
            index = -negative_index
            fits = (
                self.budget_bytes is None
                or not self._running
                or running_sum + self._rss_estimate(index) <= self.budget_bytes
            )
            if fits:
                queue.pop()
                self._queued.discard(index)
                self._running[index] = now
                self._start_time[index] = now
                self.dispatch_order.append(index)
                return index
            if name == HIGH:
                # The high slot: stop filling until the queued high fits.
                return None
        return None

    def complete(
        self,
        index: int,
        now: float,
        *,
        wall_seconds: float | None,
        rss_peak_bytes: int | None,
    ) -> None:
        started = self._running.pop(index)
        self._actual[index] = (
            started,
            now,
            None if rss_peak_bytes is None else float(rss_peak_bytes),
        )
        path = self._paths[index]
        self._live[path].append((wall_seconds, rss_peak_bytes))
        if len(self._live[path]) >= LIVE_MEDIAN_MIN_SAMPLES:
            self._apply_live_median(path)

    def _apply_live_median(self, path: str) -> None:
        samples = self._live[path]
        wall = _median([w for w, _ in samples if w is not None])
        rss = _median([float(r) for _, r in samples if r is not None])
        changed = False
        for index in sorted(self._queued):
            if self._paths[index] != path:
                continue
            current = self._pred[index]
            # Only sources worse than the live median are replaced; a profile
            # of the same id (source 1) stays.
            if SOURCE_RANK[current.source] <= SOURCE_RANK[SOURCE_LIVE_FILE]:
                continue
            new_wall = wall if wall is not None else current.wall_seconds
            new_rss = int(rss) if rss is not None else current.rss_peak_bytes
            if (new_wall, new_rss) == (current.wall_seconds, current.rss_peak_bytes):
                continue
            self._requeue(index, Prediction(new_wall, new_rss, SOURCE_LIVE_FILE))
            changed = True
        if changed:
            self._live_applied.add(path)

    def _requeue(self, index: int, prediction: Prediction) -> None:
        old_queue = self._queues[self._class[index]]
        old_queue.remove(self._key(index))
        self._pred[index] = prediction
        # Thresholds stay fixed for the run; a live median can move the class.
        self._class[index] = classify(prediction.rss_peak_bytes, self.thresholds)
        bisect.insort(self._queues[self._class[index]], self._key(index))

    # -- predicted versus actual (design 5.7) ------------------------------

    def error(
        self,
        index: int,
        *,
        wall_seconds: float | None,
        rss_peak_bytes: int | None,
        profile_reason: str | None = None,
    ) -> dict[str, Any]:
        """Actual over predicted for one finished candidate, judged against
        the prediction made at dispatch. Ratios are ``None`` with a reason
        when either side has no measurement; a candidate whose profile is
        ``NO_MEASUREMENT`` carries that profile's reason code."""
        prediction = self._pred[index]
        reasons: list[str] = []
        rss_ratio: float | None = None
        wall_ratio: float | None = None
        if rss_peak_bytes is None:
            reasons.append(
                "no peak RSS was recorded for the candidate"
                if profile_reason is None
                else f"no peak RSS was recorded for the candidate ({profile_reason})"
            )
        elif prediction.rss_peak_bytes in (None, 0):
            reasons.append("no RSS prediction was available")
        else:
            rss_ratio = round(rss_peak_bytes / prediction.rss_peak_bytes, 4)
        if wall_seconds is None:
            reasons.append("no elapsed time was recorded for the candidate")
        elif not prediction.wall_seconds:
            reasons.append("no wall-time prediction was available")
        else:
            wall_ratio = round(wall_seconds / prediction.wall_seconds, 4)
        return {
            "rss_ratio": rss_ratio,
            "wall_ratio": wall_ratio,
            "class_actual": classify(rss_peak_bytes, self.thresholds),
            **({"reason": "; ".join(reasons)} if reasons else {}),
            **({"profile_reason": profile_reason} if profile_reason is not None else {}),
        }

    def summary(self, *, errors: Mapping[int, Mapping[str, Any]]) -> dict[str, Any]:
        """The run summary written to ``schedule-summary.v1`` (design 5.4 and 5.7)."""
        sources = collections.Counter(self._initial_pred[i].source for i in range(len(self._ids)))
        by_source: dict[str, dict[str, list[float]]] = {}
        confusion: dict[str, dict[str, int]] = {
            name: {HIGH: 0, NORMAL: 0, LOW: 0} for name in (HIGH, NORMAL, LOW)
        }
        for index, error in errors.items():
            bucket = by_source.setdefault(
                self._pred[index].source, {"rss_ratio": [], "wall_ratio": []}
            )
            for key in ("rss_ratio", "wall_ratio"):
                if error.get(key) is not None:
                    bucket[key].append(float(error[key]))
            confusion[self._class[index]][error["class_actual"]] += 1

        def distribution(values: list[float]) -> dict[str, float | None]:
            if not values:
                return {"count": 0, "p50": None, "p95": None}
            return {
                "count": len(values),
                "p50": round(_percentile(values, 0.5), 4),
                "p95": round(_percentile(values, 0.95), 4),
            }

        return {
            "schema": SCHEDULE_SCHEMA,
            "policy": "tem-lpt",
            "workers": self.workers,
            "memory_budget_bytes": self.budget_bytes,
            "default_candidate_rss_bytes": self._default_rss,
            "high_factor": self.high_factor,
            "low_factor": self.low_factor,
            "thresholds": (
                None
                if self.thresholds is None
                else {
                    "high_bytes": round(self.thresholds.high_bytes),
                    "low_bytes": round(self.thresholds.low_bytes),
                    "mode_median_bytes": round(self.thresholds.mode_median_bytes),
                    "mode_p95_bytes": round(self.thresholds.mode_p95_bytes),
                }
            ),
            "candidates": len(self._ids),
            "class_counts": {
                name: sum(1 for c in self._initial_class if c == name)
                for name in (HIGH, NORMAL, LOW)
            },
            "source_counts": {name: sources.get(name, 0) for name in SOURCE_RANK},
            "live_median_files": len(self._live_applied),
            "error": {
                source: {
                    "rss_ratio": distribution(values["rss_ratio"]),
                    "wall_ratio": distribution(values["wall_ratio"]),
                }
                for source, values in sorted(by_source.items())
            },
            "class_confusion": confusion,
            "admission_overshoots": self.admission_overshoots(),
        }

    def verdict_summary(self, *, errors: Mapping[int, Mapping[str, Any]]) -> dict[str, Any]:
        """``judgment.r2.schedule`` (verdict v18, design 4.2): the policy, the
        class thresholds and factors, the admission budget, the worker count,
        how many candidates each prediction source served, and the error
        summary over all sources."""
        sources = collections.Counter(
            self._initial_pred[i].source for i in range(len(self._ids))
        )
        rss_ratios: list[float] = []
        wall_ratios: list[float] = []
        confusion: dict[str, dict[str, int]] = {
            name: {HIGH: 0, NORMAL: 0, LOW: 0} for name in (HIGH, NORMAL, LOW)
        }
        for index, error in errors.items():
            if error.get("rss_ratio") is not None:
                rss_ratios.append(float(error["rss_ratio"]))
            if error.get("wall_ratio") is not None:
                wall_ratios.append(float(error["wall_ratio"]))
            confusion[self._class[index]][error["class_actual"]] += 1

        def quantiles(values: list[float]) -> dict[str, float | None]:
            if not values:
                return {"p50": None, "p95": None}
            return {
                "p50": round(_percentile(values, 0.5), 4),
                "p95": round(_percentile(values, 0.95), 4),
            }

        return {
            "policy": "tem-lpt",
            "thresholds": {
                "high_bytes": (
                    None if self.thresholds is None else round(self.thresholds.high_bytes)
                ),
                "low_bytes": (
                    None if self.thresholds is None else round(self.thresholds.low_bytes)
                ),
                "high_factor": self.high_factor,
                "low_factor": self.low_factor,
            },
            "memory_budget_bytes": self.budget_bytes,
            "jobs": self.workers,
            "prediction_sources": {name: sources.get(name, 0) for name in SOURCE_RANK},
            "error": {
                "rss_ratio": quantiles(rss_ratios),
                "wall_ratio": quantiles(wall_ratios),
                "class_confusion": confusion,
                "admission_overshoots": self.admission_overshoots(),
            },
        }

    def admission_overshoots(self) -> int:
        """Moments (candidate starts) at which the sum of the ACTUAL peaks of
        the running candidates exceeded the budget: an upper envelope computed
        after the run. Candidates without a measured peak count as zero."""
        if self.budget_bytes is None:
            return 0
        events: list[tuple[float, int, float]] = []
        for started, ended, rss in self._actual.values():
            peak = 0.0 if rss is None else rss
            events.append((started, 1, peak))
            events.append((ended, 0, -peak))
        # Releases sort before starts at equal times.
        events.sort(key=lambda item: (item[0], item[1]))
        level = 0.0
        overshoots = 0
        for _, kind, delta in events:
            level += delta
            if kind == 1 and level > self.budget_bytes:
                overshoots += 1
        return overshoots


def canonical_verdict_summary(*, jobs: int, memory_budget_bytes: int | None) -> dict[str, Any]:
    """``judgment.r2.schedule`` for a run in canonical order (a serial run):
    no classes, no predictions, no error summary."""
    return {
        "policy": "canonical",
        "thresholds": None,
        "memory_budget_bytes": memory_budget_bytes,
        "jobs": jobs,
        "prediction_sources": {},
        "error": None,
    }


def plan_dispatch_order(
    candidates: Sequence[tuple[str, str]],
    predictions: Sequence[Prediction],
    durations: Sequence[float],
    *,
    workers: int,
    budget_bytes: int | None = None,
    high_factor: float = DEFAULT_HIGH_FACTOR,
    low_factor: float = DEFAULT_LOW_FACTOR,
    actual_rss_bytes: Sequence[int | None] | None = None,
) -> tuple[list[int], TemScheduler]:
    """Replay the dispatch loop with fake durations (an event-driven
    simulation using the real scheduler). Returns the dispatch order and the
    finished scheduler. Used by the replay tests and for offline planning."""
    scheduler = TemScheduler(
        candidates,
        predictions,
        budget_bytes=budget_bytes,
        workers=workers,
        high_factor=high_factor,
        low_factor=low_factor,
    )
    now = 0.0
    heap: list[tuple[float, int]] = []
    while scheduler.remaining or heap:
        while len(heap) < workers:
            index = scheduler.next_candidate(now)
            if index is None:
                break
            heapq.heappush(heap, (now + durations[index], index))
        if not heap:
            raise RuntimeError("scheduler stalled with work remaining")
        now, finished = heapq.heappop(heap)
        scheduler.complete(
            finished,
            now,
            wall_seconds=durations[finished],
            rss_peak_bytes=None if actual_rss_bytes is None else actual_rss_bytes[finished],
        )
    return scheduler.dispatch_order, scheduler


@positional_record
class ScheduleOptions:
    """Runtime scheduling inputs for one native R2 sweep (B207)."""

    #: Admission budget B in bytes (``--memory-budget-bytes``); ``None`` turns
    #: admission off and leaves only ordering.
    memory_budget_bytes: int | None = None
    high_factor: float = DEFAULT_HIGH_FACTOR
    low_factor: float = DEFAULT_LOW_FACTOR
    #: Operator medians (``mutation.prediction_wall_seconds`` and
    #: ``mutation.prediction_rss_bytes``), prediction source 5.
    prediction_wall_seconds: float | None = None
    prediction_rss_bytes: int | None = None
