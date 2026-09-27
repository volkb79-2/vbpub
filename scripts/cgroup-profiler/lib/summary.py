"""The incremental Summary accumulator — RG55-INTERFACE-CONTRACT.md §3/§7.

``SummaryAccumulator`` is fed one tick at a time (``add_sample``) as the
daemon's session server samples the target cgroup, and produces the exact
Summary document ``ctl stop`` returns (``finalize``). It is "incremental" in
the sense the contract requires (§1.5: the daemon must answer ``stop`` within
30 s for a session of *any* length) — it never re-reads the on-disk series to
compute the answer; every number in the output is derived from state kept
across ``add_sample`` calls plus the first and last tick, so ``finalize`` is
O(1) in wall time regardless of session length (percentiles are the one
O(samples) piece, and that cost is paid incrementally, one append per tick,
never re-scanned).

**Sample shapes.** A cgroup sample is :func:`sample_target_cgroup`'s (or
:func:`sample_slice_cgroup`'s) output — a thin wrapper around
:mod:`lib.metrics`'s ``sample_cgroup`` that also reads ``memory.max`` /
``memory.high`` (needed for limit-drift detection, per contract §7, but not
part of ``metrics.sample_cgroup``'s own "mem" group — those two files already
have an owner, :mod:`lib.limits`'s ancestor-chain resolver, and summary.py
only ever needs the leaf cgroup's own raw value, not a resolved effective
limit, so duplicating the two reads here beat threading limits.py through the
sampler). A host sample is :func:`lib.metrics.sample_host`'s output, used
as-is. A DAMON sample is ``{"hot": int, "warm": int, "cold": int, "idle":
int}`` in bytes — one classified snapshot per DAMON aggregation interval,
already summed by class (:mod:`lib.damon`'s ``Classifier.summary`` output,
adapted by the caller); this module never talks to DAMON sysfs itself.

**Null discipline (contract §1.7 / §7).** Every computed field follows one
rule: a delta or percentile whose *inputs* were ever unreadable is ``null``,
never computed against a substituted zero. This module never invents a 0 —
:mod:`lib.util`'s readers already refuse to, and every helper here
(``_delta``, ``_nearest_rank``, ``_max_over``, ``_last_present``) preserves
that by propagating ``None`` rather than treating it as 0.

**Decision ask (recorded in the P1 REPORT, not re-litigated here):** the
contract does not say whether "the last read of memory.peak / pids.peak"
(§7) means literally sample index n, or the most recent sample at which that
one file was actually readable. This module takes the latter (skip back over
``None`` entries) — a container's cgroup files can flicker unreadable for one
tick without losing a real high-water mark that was read a moment earlier,
and "the last read" most naturally means the last *successful* read. The
golden fixtures never exercise a trailing gap (every field is present at
every frame), so this choice is untested by the contract's own goldens;
``tests/test_summary.py`` adds a case that pins it explicitly.

**RW-21 (contract §7 "Scope `container`: absolute counters", 2026-09-12).**
In scope ``container`` the cgroup was created for the lane itself, so its
cumulative counters (``cpu.stat usage_usec``/``throttled_usec``/
``nr_throttled``, the container's own PSI totals, ``memory.stat`` fault
counters, ``memory.events.local`` oom_kill/high) already measure the lane
alone from its first instruction — reading them as a delta from ``s_0``
drops everything the lane did between cgroup creation and the first sample
(measured live: a 3.3x CPU understatement on a real probe). ``_rw21_reference``
is the one helper every affected block routes through: scope ``container``
takes the last successful read (the same "skip back over unreadable ticks"
rule as ``memory.peak_bytes``/``pids.peak`` above); scope ``container-shared``
is unaffected, keeping the original delta-from-``s_0`` rule (that cgroup is
shared, so a delta against session start is still the only lane-attributable
number). ``memory.peak_over_baseline_bytes`` is always ``null`` in scope
``container`` (not computed at all — see ``_memory_block``); ``limit_drift``,
``cores_max`` and ``host.*`` are explicitly unaffected (none of them were
ever a cumulative-counter delta to begin with).
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from math import ceil
from math import isfinite
from typing import Any, Dict, Iterable, List, Optional, Sequence

from . import metrics, util

SCHEMA = 1

_ISO_FMT = "%Y-%m-%dT%H:%M:%SZ"

_TARGET_GROUPS = {"mem", "memstat", "memev", "psi", "cpu", "pids"}
_SLICE_GROUPS = {"mem", "psi"}


# ── sampling helpers (thin wrappers so a caller has one function per role) ──

def sample_target_cgroup(abs_path: str) -> Dict[str, Any]:
    """One tick of the target cgroup, in the shape :class:`SummaryAccumulator`
    expects: :func:`lib.metrics.sample_cgroup`'s groups plus ``mem_max`` /
    ``mem_high`` (``memory.max`` / ``memory.high``, ``None`` for ``"max"`` or
    an unreadable file, via :func:`lib.util.read_int`)."""
    sample = metrics.sample_cgroup(abs_path, groups=_TARGET_GROUPS)
    if not sample:
        return {}
    sample["mem_max"] = util.read_int(os.path.join(abs_path, "memory.max"))
    sample["mem_high"] = util.read_int(os.path.join(abs_path, "memory.high"))
    return sample


def sample_slice_cgroup(abs_path: str) -> Dict[str, Any]:
    """One tick of the slice the target lives under — only ``memory.current``
    and pressure are needed for ``host.slice``."""
    return metrics.sample_cgroup(abs_path, groups=_SLICE_GROUPS)


def read_cgroup_pids(abs_path: str) -> List[int]:
    """The pids directly in ``cgroup.procs`` right now (no token filtering —
    that is :mod:`lib.subtree`'s job; this is the "no --token" default source
    and also what a token-aware caller unions with the resolved subtree
    before calling :meth:`SummaryAccumulator.add_sample`). PID 0 is discarded:
    cgroupfs uses it as the translation for tasks outside a private PID
    namespace, and it is not a process identity that sampling can use."""
    text = util.read_text(os.path.join(abs_path, "cgroup.procs"))
    if not text:
        return []
    out: List[int] = []
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        try:
            pid = int(line)
        except ValueError:
            continue
        if pid > 0:
            out.append(pid)
    return out


# ── small numeric helpers, every one None-safe per the module docstring ────

def _dig(d: Optional[Dict[str, Any]], *keys: str) -> Any:
    cur: Any = d
    for key in keys:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur


def _delta(a: Optional[float], b: Optional[float]) -> Optional[float]:
    """``b - a``, or ``None`` if either end is unreadable."""
    if a is None or b is None:
        return None
    return b - a


def _round(value: Optional[float], places: int = 3) -> Optional[float]:
    return None if value is None else round(value, places)


def _max_over(values: Iterable[Optional[float]]) -> Optional[float]:
    present = [v for v in values if v is not None]
    return max(present) if present else None


def _last_present(values: Sequence[Optional[Any]]) -> Optional[Any]:
    """The last non-``None`` entry, or ``None`` if every entry was."""
    for value in reversed(values):
        if value is not None:
            return value
    return None


def _nearest_rank(values: Iterable[Optional[float]], pct: float) -> Optional[int]:
    """Nearest-rank percentile (contract §7): sort ascending, rank =
    ``ceil(pct/100 * N)`` (1-based), take that element. No interpolation, so
    two independent stdlib implementations always agree byte-for-byte."""
    present = sorted(v for v in values if v is not None)
    n = len(present)
    if n == 0:
        return None
    rank = ceil(pct / 100.0 * n)
    rank = max(1, min(n, rank))
    return present[rank - 1]


def _parse_iso(text: Optional[str]) -> Optional[datetime]:
    if not text:
        return None
    try:
        return datetime.strptime(text, _ISO_FMT).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _host_pressure_block(host: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    memory = _dig(host, "psi", "memory") or {}
    cpu = _dig(host, "psi", "cpu") or {}
    loadavg = _dig(host, "loadavg")
    return {
        "memory_pressure": {
            "some_avg10": memory.get("some_avg10"),
            "full_avg10": memory.get("full_avg10"),
            "some_avg60": memory.get("some_avg60"),
            "full_avg60": memory.get("full_avg60"),
        },
        "cpu_pressure": {
            "some_avg10": cpu.get("some_avg10"),
            "full_avg10": cpu.get("full_avg10"),
            "some_avg60": cpu.get("some_avg60"),
            "full_avg60": cpu.get("full_avg60"),
        },
        "loadavg1": loadavg[0] if isinstance(loadavg, list) and loadavg else None,
    }


def _count_limit_drift(samples: Sequence[Dict[str, Any]]) -> Optional[int]:
    """Number of consecutive sample-pairs where ``memory.max`` or
    ``memory.high`` changed (contract §7). A pair where either side of the
    comparison is unreadable is skipped (not counted as a change and not
    counted as "unchanged") rather than guessed at; the field is ``null``
    only when *no* sample ever carried a reading (fully unreadable), per
    contract §1.7 ("absent is never zero", but a real reading of "unchanged"
    is a real 0, not an absence)."""
    pairs = [(s.get("mem_max"), s.get("mem_high")) for s in samples]
    if all(p == (None, None) for p in pairs):
        return None
    drift = 0
    for prev, cur in zip(pairs, pairs[1:]):
        if prev[0] is None or cur[0] is None or prev[1] is None or cur[1] is None:
            continue
        if prev != cur:
            drift += 1
    return drift


def _rw21_reference(
    scope: str, samples: List[Dict[str, Any]], *keys: str,
) -> Optional[float]:
    """RW-21 (contract §7 "Scope `container`: absolute counters"): the one
    raw number that section redefines. In scope ``container`` the cgroup was
    created for the lane itself, so its cumulative counters already measure
    the lane alone from its first instruction — reading them as a delta from
    ``s_0`` drops everything the lane did between cgroup creation and the
    first sample (measured live: a 3.3x CPU understatement). So scope
    ``container`` takes the LAST SUCCESSFUL read (RW-7's "last read" =
    skip back over unreadable trailing ticks, the same rule
    ``memory.peak_bytes``/``pids.peak`` already use) instead of a delta.
    Scope ``container-shared`` is unaffected — it keeps the original
    delta-from-``s_0`` rule (that cgroup is shared, so its cumulative
    counters were never lane-scoped to begin with; a delta against the
    session's own start is still the only lane-attributable number).
    Every caller below divides by 1e6 or leaves the raw int as-is per its
    own field; this helper only decides which raw value — a last-read or a
    delta — feeds that math."""
    if scope == "container":
        return _last_present([_dig(s, *keys) for s in samples])
    first, last = samples[0], samples[-1]
    return _delta(_dig(first, *keys), _dig(last, *keys))


class SummaryAccumulator:
    """Feed successive samples; :meth:`finalize` returns the Summary object
    of contract §3. One instance per live session — the session server holds
    one alongside each :class:`lib.sampler.Sampler` thread."""

    def __init__(
        self,
        *,
        session: str,
        daemon_name: str,
        daemon_version: str,
        scope: str,
        started_at: str,
        interval_seconds: float,
        container_id: str,
        cgroup: str,
        token: Optional[str],
        slice_name: Optional[str] = None,
        damon_enabled: bool = False,
        damon_kdamond: Optional[int] = None,
        damon_thresholds: Optional[Dict[str, Any]] = None,
    ) -> None:
        if scope not in ("container", "container-shared"):
            raise ValueError(f"unknown scope {scope!r}")
        self.session = session
        self.daemon_name = daemon_name
        self.daemon_version = daemon_version
        self.scope = scope
        self.started_at = started_at
        self.interval_seconds = interval_seconds
        self.container_id = container_id
        self.cgroup = cgroup
        self.token = token
        self.slice_name = slice_name

        self._damon_enabled = damon_enabled
        self._damon_kdamond = damon_kdamond
        self._damon_thresholds = damon_thresholds or {}
        self._damon_unavailable_reason: Optional[str] = None

        self._samples: List[Dict[str, Any]] = []
        self._slice_samples: List[Dict[str, Any]] = []
        self._host_samples: List[Dict[str, Any]] = []
        self._damon_samples: List[Dict[str, int]] = []
        self._sample_monos: List[Optional[float]] = []
        self._pids_seen: set = set()

    def add_sample(
        self,
        *,
        cgroup: Dict[str, Any],
        host: Dict[str, Any],
        slice_cgroup: Optional[Dict[str, Any]] = None,
        damon: Optional[Dict[str, int]] = None,
        pids: Iterable[int] = (),
        mono: Optional[float] = None,
    ) -> None:
        """Record one tick. ``cgroup``/``slice_cgroup``/``host`` are the
        shapes documented on the module; ``damon`` is one classified-bytes
        snapshot or ``None`` when this tick had none (DAMON off, or not yet
        available); ``pids`` is every pid attributed to the target at this
        tick (the whole cgroup with no token, the resolved subtree with
        one) — unioned into ``target.targets_seen``."""
        self._samples.append(cgroup)
        self._host_samples.append(host)
        if slice_cgroup is not None:
            self._slice_samples.append(slice_cgroup)
        if damon is not None:
            self._damon_samples.append(damon)
        if isinstance(mono, (int, float)) and not isinstance(mono, bool) and isfinite(mono):
            self._sample_monos.append(float(mono))
        else:
            self._sample_monos.append(None)
        self._pids_seen.update(pids)

    def mark_damon_unavailable(self, reason: str) -> None:
        """DAMON was requested but sysfs is absent or read-only (contract
        C3 / DESIGN.md §4.7). The session keeps running; only the ``damon``
        block of the eventual summary reflects it."""
        self._damon_unavailable_reason = reason

    @property
    def sample_count(self) -> int:
        """How many ticks have been recorded so far — sample zero is present
        before a live session is published, so a stop always has at least one
        recorded tick to finalize."""
        return len(self._samples)

    @property
    def targets_seen(self) -> int:
        """The running union size of every pid ever passed to
        :meth:`add_sample` (contract §3 ``target.targets_seen``) — exposed so
        a live ``ctl status`` can report it without the daemon keeping its
        own separate pid-union tracker."""
        return len(self._pids_seen)

    # ── finalize ─────────────────────────────────────────────────────────

    def finalize(self, *, ended_at: str) -> Dict[str, Any]:
        if not self._samples:
            raise ValueError("finalize() called with no samples recorded")
        first, last = self._samples[0], self._samples[-1]
        start_dt, end_dt = _parse_iso(self.started_at), _parse_iso(ended_at)
        duration = (
            (end_dt - start_dt).total_seconds() if start_dt and end_dt else None
        )

        return {
            "schema": SCHEMA,
            "session": self.session,
            "daemon": {"name": self.daemon_name, "version": self.daemon_version},
            "scope": self.scope,
            "method": "daemon",
            "started_at": self.started_at,
            "ended_at": ended_at,
            "duration_seconds": _round(duration),
            "interval_seconds": self.interval_seconds,
            "samples": len(self._samples),
            "target": {
                "container_id": self.container_id,
                "cgroup": self.cgroup,
                "token": self.token,
                "targets_seen": len(self._pids_seen),
            },
            "memory": self._memory_block(first, last),
            "cpu": self._cpu_block(first, last, duration),
            "pressure": self._pressure_block(first, last),
            "faults": self._faults_block(first, last),
            "pids": {"peak": _last_present([_dig(s, "pids", "peak") for s in self._samples])},
            "damon": self._damon_block(),
            "host": self._host_block(),
            "events": self._events_block(first, last),
        }

    def _memory_block(self, first: Dict, last: Dict) -> Dict[str, Any]:
        currents = [_dig(s, "mem", "current") for s in self._samples]
        baseline = _dig(first, "mem", "current")
        if self.scope == "container":
            peak = _last_present([_dig(s, "mem", "peak") for s in self._samples])
            source = "memory.peak"
        else:
            peak = _max_over(currents)
            source = "sampled-max"
        # RW-21 / contract §3's nullability note: in scope "container" the
        # profiled cgroup IS the lane, so "peak over what it started at" is
        # not a meaningful number (baseline is kept, informational only) —
        # always null here, never computed, regardless of whether peak/
        # baseline themselves are present.
        if self.scope == "container":
            over_baseline = None
        else:
            over_baseline = None if peak is None or baseline is None else max(0, peak - baseline)
        return {
            "peak_bytes": peak,
            "source": source,
            "baseline_bytes": baseline,
            "peak_over_baseline_bytes": over_baseline,
            "p90_bytes": _nearest_rank(currents, 90),
            "median_bytes": _nearest_rank(currents, 50),
            "swap_peak_bytes": _max_over(_dig(s, "mem", "swap_current") for s in self._samples),
            "anon_peak_bytes": _max_over(_dig(s, "memstat", "anon") for s in self._samples),
            "file_peak_bytes": _max_over(_dig(s, "memstat", "file") for s in self._samples),
        }

    def _cpu_block(self, first: Dict, last: Dict, duration: Optional[float]) -> Dict[str, Any]:
        # RW-21: usage_usec/throttled_usec/nr_throttled are cumulative
        # counters — scope "container" reads the last successful value,
        # scope "container-shared" keeps the delta-from-s0 rule (see
        # `_rw21_reference`'s own docstring). `cores_max` is a per-interval
        # RATE, never itself a delta-from-s0 quantity, so it is unaffected
        # in either scope.
        usage_ref = _rw21_reference(self.scope, self._samples, "cpu", "usage_usec")
        seconds = None if usage_ref is None else usage_ref / 1e6
        cores_avg = None
        if seconds is not None and duration is not None and duration > 0:
            cores_avg = seconds / duration
        cores_max = None
        for index, (prev, cur) in enumerate(zip(self._samples, self._samples[1:])):
            step = _delta(_dig(prev, "cpu", "usage_usec"), _dig(cur, "cpu", "usage_usec"))
            prev_mono = self._sample_monos[index]
            cur_mono = self._sample_monos[index + 1]
            if step is None or prev_mono is None or cur_mono is None:
                cores_max = None
                break
            delta_mono = cur_mono - prev_mono
            if delta_mono <= 0:
                cores_max = None
                break
            rate = (step / 1e6) / delta_mono
            cores_max = rate if cores_max is None else max(cores_max, rate)
        throttled_ref = _rw21_reference(self.scope, self._samples, "cpu", "throttled_usec")
        nr_throttled = _rw21_reference(self.scope, self._samples, "cpu", "nr_throttled")
        return {
            "seconds": _round(seconds),
            "cores_avg": _round(cores_avg),
            "cores_max": _round(cores_max),
            "throttled_seconds": _round(None if throttled_ref is None else throttled_ref / 1e6),
            "nr_throttled": nr_throttled,
        }

    def _pressure_block(self, first: Dict, last: Dict) -> Dict[str, Any]:
        def stall(group: str, kind: str) -> Optional[float]:
            # RW-21: the container's own PSI totals are cumulative counters
            # too — same last-read-vs-delta branch as `_cpu_block`.
            ref = _rw21_reference(self.scope, self._samples, group, f"{kind}_total")
            return _round(None if ref is None else ref / 1e6)

        return {
            "memory_some_stall_seconds": stall("psi_mem", "some"),
            "memory_full_stall_seconds": stall("psi_mem", "full"),
            "cpu_some_stall_seconds": stall("psi_cpu", "some"),
            "io_some_stall_seconds": stall("psi_io", "some"),
            "io_full_stall_seconds": stall("psi_io", "full"),
        }

    def _faults_block(self, first: Dict, last: Dict) -> Dict[str, Any]:
        # RW-21: memory.stat's fault counters are cumulative too.
        def value(key: str) -> Optional[int]:
            return _rw21_reference(self.scope, self._samples, "memstat", key)

        return {
            "pgmajfault": value("pgmajfault"),
            "workingset_refault_anon": value("workingset_refault_anon"),
            "workingset_refault_file": value("workingset_refault_file"),
        }

    def _damon_block(self) -> Optional[Dict[str, Any]]:
        if not self._damon_enabled:
            return None
        if self._damon_unavailable_reason is not None:
            status, reason, kdamond = "unavailable", self._damon_unavailable_reason, None
        else:
            status, reason, kdamond = "on", None, self._damon_kdamond

        def class_stats(name: str) -> Optional[Dict[str, Optional[int]]]:
            values = [s.get(name) for s in self._damon_samples]
            if not self._damon_samples:
                return None
            return {
                "peak": _max_over(values),
                "p90": _nearest_rank(values, 90),
                "median": _nearest_rank(values, 50),
            }

        return {
            "status": status,
            "reason": reason,
            "kdamond": kdamond,
            "targets_seen": len(self._pids_seen),
            "samples": len(self._damon_samples),
            "hot_bytes": class_stats("hot"),
            "warm_bytes": class_stats("warm"),
            "cold_bytes": class_stats("cold"),
            "idle_bytes": class_stats("idle"),
            "thresholds": self._damon_thresholds,
        }

    def _host_block(self) -> Dict[str, Any]:
        host_first = self._host_samples[0] if self._host_samples else None
        host_last = self._host_samples[-1] if self._host_samples else None

        def host_stall(kind: str) -> Optional[float]:
            delta = _delta(
                _dig(host_first, "psi", "memory", f"{kind}_total"),
                _dig(host_last, "psi", "memory", f"{kind}_total"),
            )
            return _round(None if delta is None else delta / 1e6)

        slice_block = None
        if self._slice_samples:
            slice_first, slice_last = self._slice_samples[0], self._slice_samples[-1]
            slice_delta = _delta(
                _dig(slice_first, "psi_mem", "full_total"),
                _dig(slice_last, "psi_mem", "full_total"),
            )
            slice_block = {
                "name": self.slice_name,
                "memory_full_stall_seconds": _round(None if slice_delta is None else slice_delta / 1e6),
                "memory_peak_bytes": _max_over(_dig(s, "mem", "current") for s in self._slice_samples),
            }

        return {
            "start": _host_pressure_block(host_first),
            "end": _host_pressure_block(host_last),
            "memory_full_stall_seconds": host_stall("full"),
            "memory_some_stall_seconds": host_stall("some"),
            "slice": slice_block,
        }

    def _events_block(self, first: Dict, last: Dict) -> Dict[str, Any]:
        # RW-21: memory.events.local's oom_kill/high are cumulative counters
        # too. `limit_drift` is UNAFFECTED (contract §7's own note) — it was
        # never itself a cumulative counter read as a delta, just a count of
        # sample-pairs where memory.max/memory.high changed, so it stays
        # `_count_limit_drift`'s own logic in both scopes.
        def value(key: str) -> Optional[int]:
            return _rw21_reference(self.scope, self._samples, "memev", key)

        return {
            "oom_kill": value("oom_kill"),
            "limit_drift": _count_limit_drift(self._samples),
            "memory_high_breach": value("high"),
        }
