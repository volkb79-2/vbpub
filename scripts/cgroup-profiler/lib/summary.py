"""The incremental Summary accumulator — RG55-INTERFACE-CONTRACT.md §3/§7.

``SummaryAccumulator`` is fed one tick at a time (``add_sample``) as the
daemon's session server samples the target cgroup, and produces the exact
Summary document ``ctl stop`` returns (``finalize``). It is "incremental" in
the sense the contract requires (§1.5: the daemon must answer ``stop`` within
30 s for a session of *any* length) — it never re-reads the on-disk series to
compute the answer. ``add_sample`` updates counters, extrema, adjacent-pair
rates, and exact order-statistic trees as each tick arrives. ``finalize``
assembles a fixed-size document from that state; its percentile lookups are
O(log n), and it never scans or retains the session's raw sample series.

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
:mod:`lib.util`'s readers already refuse to, and the streaming reducers
ignore unreadable values for extrema/percentiles while preserving null for
deltas whose endpoints are unreadable.

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
(measured live: a 3.3x CPU understatement on a real probe). For each such
counter the accumulator keeps the last successful reading in scope
``container`` and the first/current endpoint in scope ``container-shared``
(that cgroup is shared, so a delta against session start is still the only
lane-attributable number). ``memory.peak_over_baseline_bytes`` is always
``null`` in scope ``container``; ``limit_drift``, ``cores_max`` and
``host.*`` are not cumulative-counter deltas.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from math import ceil
from math import isfinite
from typing import Any, Dict, Iterable, List, Optional, cast

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


_SUMMARY_FIELD_PATHS = {
    "mem_current": ("mem", "current"),
    "mem_peak": ("mem", "peak"),
    "swap_current": ("mem", "swap_current"),
    "anon": ("memstat", "anon"),
    "file": ("memstat", "file"),
    "cpu_usage_usec": ("cpu", "usage_usec"),
    "cpu_throttled_usec": ("cpu", "throttled_usec"),
    "cpu_nr_throttled": ("cpu", "nr_throttled"),
    "psi_mem_some_total": ("psi_mem", "some_total"),
    "psi_mem_full_total": ("psi_mem", "full_total"),
    "psi_cpu_some_total": ("psi_cpu", "some_total"),
    "psi_io_some_total": ("psi_io", "some_total"),
    "psi_io_full_total": ("psi_io", "full_total"),
    "pgmajfault": ("memstat", "pgmajfault"),
    "workingset_refault_anon": ("memstat", "workingset_refault_anon"),
    "workingset_refault_file": ("memstat", "workingset_refault_file"),
    "oom_kill": ("memev", "oom_kill"),
    "memory_high_breach": ("memev", "high"),
    "mem_max": ("mem_max",),
    "mem_high": ("mem_high",),
    "pids_peak": ("pids", "peak"),
}

_RW21_FIELDS = (
    "cpu_usage_usec", "cpu_throttled_usec", "cpu_nr_throttled",
    "psi_mem_some_total", "psi_mem_full_total", "psi_cpu_some_total",
    "psi_io_some_total", "psi_io_full_total", "pgmajfault",
    "workingset_refault_anon", "workingset_refault_file", "oom_kill",
    "memory_high_breach",
)
_DAMON_CLASSES = ("hot", "warm", "cold", "idle")


def _summary_fields(cgroup: Dict[str, Any]) -> Dict[str, Any]:
    """Project one input sample to the scalar fields used by the summary."""
    return {
        name: _dig(cgroup, *path)
        for name, path in _SUMMARY_FIELD_PATHS.items()
    }


class _OrderNode:
    """AVL node with duplicate counts and subtree sizes for exact rank reads."""

    __slots__ = ("count", "height", "left", "right", "size", "value")

    def __init__(self, value: Any) -> None:
        self.value = value
        self.count = 1
        self.size = 1
        self.height = 1
        self.left = None
        self.right = None


def _tree_size(node: Optional[_OrderNode]) -> int:
    return 0 if node is None else node.size


def _tree_height(node: Optional[_OrderNode]) -> int:
    return 0 if node is None else node.height


def _tree_refresh(node: _OrderNode) -> _OrderNode:
    node.size = node.count + _tree_size(node.left) + _tree_size(node.right)
    node.height = 1 + max(_tree_height(node.left), _tree_height(node.right))
    return node


def _tree_rotate_left(node: _OrderNode) -> _OrderNode:
    pivot = node.right
    if pivot is None:
        raise AssertionError("left rotation without a right child")
    node.right = pivot.left
    pivot.left = _tree_refresh(node)
    return _tree_refresh(pivot)


def _tree_rotate_right(node: _OrderNode) -> _OrderNode:
    pivot = node.left
    if pivot is None:
        raise AssertionError("right rotation without a left child")
    node.left = pivot.right
    pivot.right = _tree_refresh(node)
    return _tree_refresh(pivot)


def _tree_balance(node: _OrderNode) -> _OrderNode:
    _tree_refresh(node)
    balance = _tree_height(node.left) - _tree_height(node.right)
    if balance > 1:
        left = cast(_OrderNode, node.left)
        if _tree_height(left.left) < _tree_height(left.right):
            node.left = _tree_rotate_left(left)
        return _tree_rotate_right(node)
    if balance < -1:
        right = cast(_OrderNode, node.right)
        if _tree_height(right.right) < _tree_height(right.left):
            node.right = _tree_rotate_right(right)
        return _tree_rotate_left(node)
    return node


def _tree_insert(node: Optional[_OrderNode], value: Any) -> _OrderNode:
    if node is None:
        return _OrderNode(value)
    if value < node.value:
        node.left = _tree_insert(node.left, value)
    elif value > node.value:
        node.right = _tree_insert(node.right, value)
    else:
        node.count += 1
        return _tree_refresh(node)
    return _tree_balance(node)


def _tree_select(node: Optional[_OrderNode], rank: int) -> Any:
    if node is None:
        raise AssertionError("rank selection escaped the order-statistic tree")
    left_size = _tree_size(node.left)
    if rank < left_size:
        return _tree_select(node.left, rank)
    if rank < left_size + node.count:
        return node.value
    return _tree_select(node.right, rank - left_size - node.count)


class _OrderStatisticMultiset:
    """Exact nearest-rank statistics with O(log n) insert and selection."""

    def __init__(self) -> None:
        self._root: Optional[_OrderNode] = None

    def add(self, value: Any) -> None:
        if value is not None:
            self._root = _tree_insert(self._root, value)

    def nearest_rank(self, percentile: float) -> Any:
        count = _tree_size(self._root)
        if count == 0:
            return None
        rank = ceil(percentile / 100.0 * count)
        rank = max(1, min(count, rank))
        return _tree_select(self._root, rank - 1)


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

        self._sample_count = 0
        self._first_fields: Dict[str, Any] = {}
        self._last_fields: Dict[str, Any] = {}
        self._last_successful: Dict[str, Any] = {}
        self._memory_current_ranks = _OrderStatisticMultiset()
        self._damon_ranks = {
            name: _OrderStatisticMultiset() for name in _DAMON_CLASSES
        }
        self._damon_count = 0
        self._damon_peaks = {name: None for name in _DAMON_CLASSES}
        self._maxima = {
            "memory_peak": None,
            "swap_peak": None,
            "anon_peak": None,
            "file_peak": None,
            "slice_memory_peak": None,
        }
        self._baseline_memory = None
        self._previous_limits: Optional[tuple] = None
        self._limit_drift_count = 0
        self._limit_read_seen = False
        self._previous_mono: Optional[float] = None
        self._cores_max: Optional[float] = None
        self._cores_max_invalid = False
        self._host_start: Optional[Dict[str, Any]] = None
        self._host_end: Optional[Dict[str, Any]] = None
        self._host_first_totals: Dict[str, Any] = {}
        self._host_last_totals: Dict[str, Any] = {}
        self._slice_seen = False
        self._slice_first_full_total = None
        self._slice_last_full_total = None
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
        fields = _summary_fields(cgroup)
        current_mono = (
            float(mono)
            if isinstance(mono, (int, float)) and not isinstance(mono, bool) and isfinite(mono)
            else None
        )
        if self._sample_count == 0:
            self._first_fields = fields.copy()
            self._baseline_memory = fields["mem_current"]
        else:
            step = _delta(self._last_fields["cpu_usage_usec"], fields["cpu_usage_usec"])
            if step is None or self._previous_mono is None or current_mono is None:
                self._cores_max_invalid = True
            else:
                delta_mono = current_mono - self._previous_mono
                if delta_mono <= 0:
                    self._cores_max_invalid = True
                else:
                    rate = (step / 1e6) / delta_mono
                    self._cores_max = rate if self._cores_max is None else max(self._cores_max, rate)

            limits = (fields["mem_max"], fields["mem_high"])
            previous_limits = self._previous_limits
            if (
                previous_limits is not None
                and all(value is not None for value in previous_limits + limits)
                and previous_limits != limits
            ):
                self._limit_drift_count += 1

        for name in (*_RW21_FIELDS, "mem_peak", "pids_peak"):
            value = fields[name]
            if value is not None:
                self._last_successful[name] = value
        self._memory_current_ranks.add(fields["mem_current"])
        if self.scope != "container":
            self._update_max("memory_peak", fields["mem_current"])
        self._update_max("swap_peak", fields["swap_current"])
        self._update_max("anon_peak", fields["anon"])
        self._update_max("file_peak", fields["file"])

        limits = (fields["mem_max"], fields["mem_high"])
        if limits != (None, None):
            self._limit_read_seen = True
        self._previous_limits = limits

        host_block = _host_pressure_block(host)
        host_totals = {
            "some": _dig(host, "psi", "memory", "some_total"),
            "full": _dig(host, "psi", "memory", "full_total"),
        }
        if self._sample_count == 0:
            self._host_start = host_block
            self._host_first_totals = host_totals
        self._host_end = host_block
        self._host_last_totals = host_totals

        if slice_cgroup is not None:
            slice_full_total = _dig(slice_cgroup, "psi_mem", "full_total")
            if not self._slice_seen:
                self._slice_first_full_total = slice_full_total
            self._slice_seen = True
            self._slice_last_full_total = slice_full_total
            self._update_max("slice_memory_peak", _dig(slice_cgroup, "mem", "current"))

        if damon is not None:
            self._damon_count += 1
            for name in _DAMON_CLASSES:
                value = damon.get(name)
                if value is not None:
                    current_peak = self._damon_peaks[name]
                    if current_peak is None or value > current_peak:
                        self._damon_peaks[name] = value
                self._damon_ranks[name].add(value)

        self._last_fields = fields
        self._previous_mono = current_mono
        self._sample_count += 1
        self._pids_seen.update(pids)

    def _update_max(self, name: str, value: Any) -> None:
        current = self._maxima[name]
        if value is not None and (current is None or value > current):
            self._maxima[name] = value

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
        return self._sample_count

    @property
    def targets_seen(self) -> int:
        """The running union size of every pid ever passed to
        :meth:`add_sample` (contract §3 ``target.targets_seen``) — exposed so
        a live ``ctl status`` can report it without the daemon keeping its
        own separate pid-union tracker."""
        return len(self._pids_seen)

    # ── finalize ─────────────────────────────────────────────────────────

    def finalize(self, *, ended_at: str) -> Dict[str, Any]:
        if self._sample_count == 0:
            raise ValueError("finalize() called with no samples recorded")
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
            "samples": self._sample_count,
            "target": {
                "container_id": self.container_id,
                "cgroup": self.cgroup,
                "token": self.token,
                "targets_seen": len(self._pids_seen),
            },
            "memory": self._memory_block(),
            "cpu": self._cpu_block(duration),
            "pressure": self._pressure_block(),
            "faults": self._faults_block(),
            "pids": {"peak": self._last_successful.get("pids_peak")},
            "damon": self._damon_block(),
            "host": self._host_block(),
            "events": self._events_block(),
        }

    def _reference(self, name: str) -> Any:
        if self.scope == "container":
            return self._last_successful.get(name)
        return _delta(self._first_fields[name], self._last_fields[name])

    def _memory_block(self) -> Dict[str, Any]:
        baseline = self._baseline_memory
        if self.scope == "container":
            peak = self._last_successful.get("mem_peak")
            source = "memory.peak"
        else:
            peak = self._maxima["memory_peak"]
            source = "sampled-max"
        if self.scope == "container":
            over_baseline = None
        else:
            over_baseline = None if peak is None or baseline is None else max(0, peak - baseline)
        return {
            "peak_bytes": peak,
            "source": source,
            "baseline_bytes": baseline,
            "peak_over_baseline_bytes": over_baseline,
            "p90_bytes": self._memory_current_ranks.nearest_rank(90),
            "median_bytes": self._memory_current_ranks.nearest_rank(50),
            "swap_peak_bytes": self._maxima["swap_peak"],
            "anon_peak_bytes": self._maxima["anon_peak"],
            "file_peak_bytes": self._maxima["file_peak"],
        }

    def _cpu_block(self, duration: Optional[float]) -> Dict[str, Any]:
        usage_ref = self._reference("cpu_usage_usec")
        seconds = None if usage_ref is None else usage_ref / 1e6
        cores_avg = None
        if seconds is not None and duration is not None and duration > 0:
            cores_avg = seconds / duration
        return {
            "seconds": _round(seconds),
            "cores_avg": _round(cores_avg),
            "cores_max": _round(None if self._cores_max_invalid else self._cores_max),
            "throttled_seconds": _round(
                None if self._reference("cpu_throttled_usec") is None
                else self._reference("cpu_throttled_usec") / 1e6
            ),
            "nr_throttled": self._reference("cpu_nr_throttled"),
        }

    def _pressure_block(self) -> Dict[str, Any]:
        def stall(name: str) -> Optional[float]:
            ref = self._reference(name)
            return _round(None if ref is None else ref / 1e6)

        return {
            "memory_some_stall_seconds": stall("psi_mem_some_total"),
            "memory_full_stall_seconds": stall("psi_mem_full_total"),
            "cpu_some_stall_seconds": stall("psi_cpu_some_total"),
            "io_some_stall_seconds": stall("psi_io_some_total"),
            "io_full_stall_seconds": stall("psi_io_full_total"),
        }

    def _faults_block(self) -> Dict[str, Any]:
        return {
            "pgmajfault": self._reference("pgmajfault"),
            "workingset_refault_anon": self._reference("workingset_refault_anon"),
            "workingset_refault_file": self._reference("workingset_refault_file"),
        }

    def _damon_block(self) -> Optional[Dict[str, Any]]:
        if not self._damon_enabled:
            return None
        if self._damon_unavailable_reason is not None:
            status, reason, kdamond = "unavailable", self._damon_unavailable_reason, None
        else:
            status, reason, kdamond = "on", None, self._damon_kdamond

        def class_stats(name: str) -> Optional[Dict[str, Optional[int]]]:
            if self._damon_count == 0:
                return None
            return {
                "peak": self._damon_peaks[name],
                "p90": self._damon_ranks[name].nearest_rank(90),
                "median": self._damon_ranks[name].nearest_rank(50),
            }

        return {
            "status": status,
            "reason": reason,
            "kdamond": kdamond,
            "targets_seen": len(self._pids_seen),
            "samples": self._damon_count,
            "hot_bytes": class_stats("hot"),
            "warm_bytes": class_stats("warm"),
            "cold_bytes": class_stats("cold"),
            "idle_bytes": class_stats("idle"),
            "thresholds": self._damon_thresholds,
        }

    def _host_block(self) -> Dict[str, Any]:
        if self._host_start is None or self._host_end is None:
            raise AssertionError("host endpoints missing after a recorded sample")

        def host_stall(kind: str) -> Optional[float]:
            delta = _delta(self._host_first_totals[kind], self._host_last_totals[kind])
            return _round(None if delta is None else delta / 1e6)

        slice_block = None
        if self._slice_seen:
            slice_delta = _delta(self._slice_first_full_total, self._slice_last_full_total)
            slice_block = {
                "name": self.slice_name,
                "memory_full_stall_seconds": _round(None if slice_delta is None else slice_delta / 1e6),
                "memory_peak_bytes": self._maxima["slice_memory_peak"],
            }

        return {
            "start": {
                "memory_pressure": dict(self._host_start["memory_pressure"]),
                "cpu_pressure": dict(self._host_start["cpu_pressure"]),
                "loadavg1": self._host_start["loadavg1"],
            },
            "end": {
                "memory_pressure": dict(self._host_end["memory_pressure"]),
                "cpu_pressure": dict(self._host_end["cpu_pressure"]),
                "loadavg1": self._host_end["loadavg1"],
            },
            "memory_full_stall_seconds": host_stall("full"),
            "memory_some_stall_seconds": host_stall("some"),
            "slice": slice_block,
        }

    def _events_block(self) -> Dict[str, Any]:
        return {
            "oom_kill": self._reference("oom_kill"),
            "limit_drift": self._limit_drift_count if self._limit_read_seen else None,
            "memory_high_breach": self._reference("memory_high_breach"),
        }
