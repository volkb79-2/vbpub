"""Adaptive-interval sampling loop.

A fixed 250 ms cadence for an entire run is wasteful (a quiet container costs
the same as a busy one) and a fixed 2 s cadence misses the transient that
answers the question this tool exists to answer. So the loop backs off toward
`idle_interval` while nothing is happening and snaps straight back to
`hot_interval` the instant something is — an event, a topology change, a
phase mark, or the activity score itself crossing `hot_threshold`. Backing
off multiplicatively (rather than resetting to `idle_interval` outright)
means a workload that idles for a few seconds and resumes doesn't first pay
for a `2.0` s blind spot.

`clock` and `sleep` are injected (default `time.monotonic` / `time.sleep`) so
this whole loop is testable with a fake clock and zero real waiting — a test
that sleeps out `idle_interval` seconds per tick is a test nobody runs.
"""

from __future__ import annotations

import math
import os
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

# `Membership` is only used for a type hint; sampler.run() never calls
# anything on it beyond `.refresh()`, so any duck-typed object works and the
# import cannot become a hard dependency on how Membership is built.
from .targets import Membership

# Divisors from DESIGN.md §4.4 — the byte-rate ones are deliberately generous
# (an idling devcontainer's background churn is nowhere near 8 MiB/s of
# memory growth) so that `hot_threshold` triggers on real ramps, not noise.
_MEM_NORM_BYTES_S = 8 * 1024 * 1024     # 8 MiB/s
_IO_NORM_BYTES_S = 50 * 1024 * 1024     # 50 MiB/s
_PSI_NORM = 20.0                        # matches DetectorConfig.psi_some_warn


@dataclass
class SamplerConfig:
    hot_interval: float = 0.25
    idle_interval: float = 2.0
    backoff: float = 1.5          # multiply toward idle when quiet
    hot_threshold: float = 0.15   # activity score above this => hot
    discovery_interval: float = 2.0
    max_duration: Optional[float] = None


def _clamp01(value: float) -> float:
    """Clamp to `[0, 1]`.

    A NaN reading — e.g. a PSI file whose `some_avg10` field got corrupted
    into the literal text `"nan"`, which `float()` parses without raising —
    compares unequal to everything, including itself, so neither bound check
    below would catch it and it would fall through unclamped. Treated as 0.0
    (unmeasurable, same as an absent signal) rather than let it propagate:
    `activity_score`'s contract is "0.0 … 1.0", and a NaN loose in a `max()`
    or a `>=` comparison downstream fails silently instead of loudly.
    """
    if math.isnan(value):
        return 0.0
    if value < 0.0:
        return 0.0
    if value > 1.0:
        return 1.0
    return value


def _delta_rate_abs(prev: Optional[float], cur: Optional[float], dt: float) -> Optional[float]:
    """|Δ|/dt for a *gauge* (memory.current), where a decrease is real activity.

    Unlike `util.rate`, a drop is not a counter reset here — memory can be
    freed — so the signal is the magnitude of movement in either direction.
    """
    if prev is None or cur is None or dt <= 0:
        return None
    return abs(cur - prev) / dt


def _counter_rate(prev: Optional[float], cur: Optional[float], dt: float) -> Optional[float]:
    """Per-second rate for a monotonic counter; None on reset, never negative."""
    if prev is None or cur is None or dt <= 0:
        return None
    if cur < prev:
        return None  # cgroup recreated / counter reset — unmeasurable, not a spike
    return (cur - prev) / dt


def _io_bytes_rate(prev_io: Dict[str, Any], cur_io: Dict[str, Any], dt: float) -> Optional[float]:
    """Sum of Δrbytes+Δwbytes across devices present in both snapshots.

    A device that only appears in one of the two samples (a new disk showing
    up mid-run, or the more common case of the whole group being absent) is
    skipped rather than guessed at.
    """
    total = 0.0
    seen = False
    for dev, cur_dev in (cur_io or {}).items():
        prev_dev = (prev_io or {}).get(dev)
        if not prev_dev:
            continue
        r = _counter_rate(prev_dev.get("rbytes"), cur_dev.get("rbytes"), dt)
        w = _counter_rate(prev_dev.get("wbytes"), cur_dev.get("wbytes"), dt)
        if r is not None:
            total += r
            seen = True
        if w is not None:
            total += w
            seen = True
    return total if seen else None


def _psi_signal(cur_metrics: Dict[str, Any]) -> Optional[float]:
    """Highest `some_avg10` across whichever PSI groups this cgroup carries.

    DESIGN.md §4.4 says "psi some_avg10" without naming mem/cpu/io
    specifically; any one of the three stalling is activity worth reacting
    to, so the max across whichever are present is used.
    """
    best: Optional[float] = None
    for group in ("psi_mem", "psi_cpu", "psi_io"):
        g = cur_metrics.get(group)
        if not g or "some_avg10" not in g:
            continue
        value = g["some_avg10"]
        if best is None or value > best:
            best = value
    return best


def _cgroup_activity(prev_metrics: Dict[str, Any], cur_metrics: Dict[str, Any], dt: float) -> float:
    signals: List[float] = []

    mem_rate = _delta_rate_abs(
        (prev_metrics.get("mem") or {}).get("current"),
        (cur_metrics.get("mem") or {}).get("current"),
        dt,
    )
    if mem_rate is not None:
        signals.append(_clamp01(mem_rate / _MEM_NORM_BYTES_S))

    cpu_rate = _counter_rate(
        (prev_metrics.get("cpu") or {}).get("usage_usec"),
        (cur_metrics.get("cpu") or {}).get("usage_usec"),
        dt,
    )
    if cpu_rate is not None:
        cores_used = cpu_rate / 1_000_000.0  # usec/s -> cores
        cores_available = max(1, os.cpu_count() or 1)
        signals.append(_clamp01(cores_used / cores_available))

    io_rate = _io_bytes_rate(prev_metrics.get("io") or {}, cur_metrics.get("io") or {}, dt)
    if io_rate is not None:
        signals.append(_clamp01(io_rate / _IO_NORM_BYTES_S))

    psi_value = _psi_signal(cur_metrics)
    if psi_value is not None:
        signals.append(_clamp01(psi_value / _PSI_NORM))

    return max(signals) if signals else 0.0


def activity_score(prev: Dict[str, Any], cur: Dict[str, Any], dt: float) -> float:
    """Max, across sampled cgroups and their four normalised signals, of
    activity in `[0, 1]`.

    A cgroup present in `cur` but missing from `prev` (just appeared) is
    skipped here — `Sampler.run` already snaps to hot on any topology
    change, so this function never needs to guess at a rate with no
    baseline. A cgroup, or any one metric group within it, that is simply
    absent contributes nothing rather than raising or producing NaN — that
    is the same "absent is not zero" rule the collector follows everywhere
    else, applied to a derived score instead of a raw counter.
    """
    if dt <= 0:
        return 0.0
    prev_cg = prev.get("cg") or {}
    cur_cg = cur.get("cg") or {}
    best = 0.0
    for path, cur_metrics in cur_cg.items():
        prev_metrics = prev_cg.get(path)
        if prev_metrics is None:
            continue
        best = max(best, _cgroup_activity(prev_metrics, cur_metrics, dt))
    return best


class Sampler:
    """Drives one adaptive sampling loop over a `Membership`.

    `tick()` only knows about producing one correctly-numbered record; all
    scheduling state (the current interval, the forced-hot latch) lives in
    `run()`/the instance so that a caller can also call `tick()` in
    isolation — e.g. `cgprofile attach --duration 0` taking a single sample
    — without dragging the loop's bookkeeping along.
    """

    def __init__(
        self,
        membership: Membership,
        config: SamplerConfig,
        sample_fn: Callable[[Membership], Dict[str, Any]],
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.membership = membership
        self.config = config
        self.sample_fn = sample_fn
        self.clock = clock
        self.sleep = sleep
        self._seq = 0
        self._t0_mono: Optional[float] = None
        self._interval = config.hot_interval
        self._forced_hot = False

    def force_hot(self) -> None:
        """Fold an out-of-band signal into the same snap-to-hot rule.

        A phase mark can be written by a *different* process or container
        (DESIGN.md §3), so this sampler has no way to notice one on its own
        — there is no run-directory path in this class's constructor to poll
        `marks.jsonl` against. Whatever does watch for marks (the `attach`
        loop, or an in-process caller of `emit_mark`) calls this so a mark
        gets exactly the same treatment as an event or a topology change:
        the *next* tick's interval, not this one, is snapped to
        `hot_interval`.
        """
        self._forced_hot = True

    def tick(self) -> Dict[str, Any]:
        """Take one sample and return it in the §3.1 record shape.

        `seq` and `mono` are this sampler's own bookkeeping; `cg`/`proc`/
        `host` come from `sample_fn`, which is expected to be a thin wrapper
        around `metrics.py`'s per-target readers keyed by
        `self.membership.paths()`. `t` is wall-clock and is not used by any
        scheduling decision, so it is read from the real clock even under a
        fake `clock`/`sleep` in tests.
        """
        now = self.clock()
        if self._t0_mono is None:
            self._t0_mono = now
        mono = now - self._t0_mono
        try:
            raw = self.sample_fn(self.membership) or {}
        except Exception:
            # sample_fn wraps metrics.py, whose own contract is "never raise
            # — return None for what can't be read" (DESIGN.md §1). Getting
            # here means that contract was broken, by a bug or a truly
            # unanticipated OS condition, not an expected "cgroup vanished".
            # A profiler that dies on it throws away every sample already
            # taken this run; recording one blank tick and continuing is the
            # same "absent is not zero, and neither is it fatal" rule
            # applied to the whole sample instead of one counter.
            raw = {}
        record: Dict[str, Any] = {
            "seq": self._seq,
            "t": time.time(),
            "mono": mono,
            "cg": raw.get("cg", {}),
        }
        if "proc" in raw:
            record["proc"] = raw["proc"]
        if "host" in raw:
            record["host"] = raw["host"]
        self._seq += 1
        return record

    def run(
        self,
        should_stop: Callable[[], bool],
        on_sample: Callable[[Dict[str, Any]], Optional[Any]],
        on_topology: Callable[[List[str], List[str]], Any],
    ) -> None:
        """Tick, react, sleep, repeat — until `should_stop()` or `max_duration`.

        The adaptive rule, exactly as DESIGN.md §4.4 specifies it:

        * no previous sample (the very first tick) => next interval is
          `hot_interval` — there is nothing yet to compute a score from, and
          erring hot at the start means a workload that is already busy at
          t=0 is not missed for a whole `idle_interval`.
        * `on_sample` returning anything truthy (an events list), or
          `on_topology` being invoked because membership changed, or
          `force_hot()` having been called since the last tick, snaps the
          *next* interval to `hot_interval` regardless of the score.
        * otherwise: `activity_score(prev, cur, dt) >= hot_threshold` =>
          `hot_interval`; else `min(idle_interval, current_interval *
          backoff)`, where `current_interval` is whatever interval this
          method most recently decided on (`hot_interval` before the first
          decision).
        """
        prev: Optional[Dict[str, Any]] = None
        last_discovery: Optional[float] = None
        while not should_stop():
            record = self.tick()
            mono = record["mono"]

            events = on_sample(record)

            appeared: List[str] = []
            disappeared: List[str] = []
            if last_discovery is None or (mono - last_discovery) >= self.config.discovery_interval:
                appeared, disappeared = self.membership.refresh()
                last_discovery = mono
                if appeared or disappeared:
                    on_topology(appeared, disappeared)

            forced = bool(events) or bool(appeared) or bool(disappeared) or self._forced_hot
            self._forced_hot = False

            score: Optional[float] = None
            if prev is not None:
                dt = mono - prev["mono"]
                score = activity_score(prev, record, dt)
            prev = record

            if forced or score is None or score >= self.config.hot_threshold:
                next_interval = self.config.hot_interval
            else:
                next_interval = min(self.config.idle_interval, self._interval * self.config.backoff)
            self._interval = next_interval

            if self.config.max_duration is not None and mono >= self.config.max_duration:
                break
            self.sleep(next_interval)
