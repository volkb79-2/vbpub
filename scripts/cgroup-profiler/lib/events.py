"""Detects the kinds of moments a report needs a marker for.

`Detector` is stateful across calls (one instance per run) because several
kinds are edge-triggered rather than level-triggered: a cgroup sitting at
`psi some_avg10 = 40` for thirty seconds is one `psi_spike` event, not one
per sample tick. Without that latch, a sustained spike would drown the
events list — and a report where "worth marking" and "worth mentioning
every 250ms" are the same thing is not readable.

`victim_pressure` is why this module exists: an *observer*-role target (a
production container we are not touching, watched only to see what a gate
running elsewhere does to it) whose `rfd/s` — anon refaults actually served
from disk, not from zswap — crosses `victim_rfd_warn`/`victim_rfd_serious`.
Everything else here answers "is the subject in trouble"; this one answers
"is something else paying for it", which is the question DESIGN.md §0 opens
with.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

from .model import Event, SEVERITIES

if TYPE_CHECKING:
    # Only for the type hints on limits_changed()/__init__ — limits.py is a
    # sibling agent's module and this file must not need it to import or run.
    from .limits import Effective

_PAGE_BYTES = 4096  # kernel page size assumed by the vmstat/memory.stat counters

_BAND_ORDER = {None: 0, "warn": 1, "serious": 2}


@dataclass
class DetectorConfig:
    psi_some_warn: float = 20.0
    psi_some_serious: float = 50.0
    swap_out_warn_mb_s: float = 20.0
    victim_rfd_warn: float = 10.0        # the host monitor's "low tens" line
    victim_rfd_serious: float = 50.0
    # Fraction of the warn threshold a metric must fall below before the same
    # kind can fire again for the same cgroup. 1.0 disables hysteresis and
    # restores bare edge-triggering.
    rearm_ratio: float = 0.5


def _nonneg_delta(prev: Optional[float], cur: Optional[float]) -> Optional[float]:
    """cur-prev, or None when either is missing or the counter went backwards.

    A cgroup event counter (memory.events, cpu.stat's nr_throttled, ...) only
    ever grows within one cgroup's lifetime; a decrease means the cgroup was
    recreated underneath us, which is "unmeasurable this tick", never "-N
    events", matching `util.rate`'s rule for the same situation.
    """
    if prev is None or cur is None:
        return None
    delta = cur - prev
    return delta if delta >= 0 else None


def _rate(prev: Optional[float], cur: Optional[float], dt: float) -> Optional[float]:
    if dt <= 0:
        return None
    delta = _nonneg_delta(prev, cur)
    return None if delta is None else delta / dt


def _rfd_per_s(prev_memstat: Dict[str, Any], cur_memstat: Dict[str, Any], dt: float) -> Optional[float]:
    """`max(0, Δworkingset_refault_anon - Δzswpin) / Δt` — reproduced exactly
    from the host's `soulmask-zswap-monitor.py` definition (DESIGN.md §4.1)
    so the two tools read the same number the same way.
    """
    if dt <= 0:
        return None
    wra_delta = _nonneg_delta(prev_memstat.get("workingset_refault_anon"), cur_memstat.get("workingset_refault_anon"))
    zin_delta = _nonneg_delta(prev_memstat.get("zswpin"), cur_memstat.get("zswpin"))
    if wra_delta is None or zin_delta is None:
        return None
    return max(0.0, wra_delta - zin_delta) / dt


def _severity_band(value: float, warn: Optional[float], serious: Optional[float]) -> Optional[str]:
    if serious is not None and value >= serious:
        return "serious"
    if warn is not None and value >= warn:
        return "warn"
    return None


def _event(t: float, mono: float, kind: str, severity: str, target: str, message: str, data: Dict[str, Any]) -> Event:
    assert severity in SEVERITIES, f"unknown severity {severity!r}"
    return Event(t=t, mono=mono, kind=kind, severity=severity, target=target, message=message, data=data)


class Detector:
    """Runs every `observe()`/`topology()`/`limits_changed()` call against
    one run's worth of running state.

    `limits` maps cgroup path to its resolved `Effective` (from `limits.py`)
    for the target(s) it is worth checking drift on; it is looked up by
    `limits_changed`'s caller, not read directly here.

    `roles` is an addition to the two constructor arguments DESIGN.md §4.6
    lists (`config`, `limits`): it is keyword-only with a default of `None`,
    so `Detector(config, limits)` still works exactly as specified. Without
    it there is no way for this class to know which cgroup is playing the
    "observer" role that `victim_pressure` is specifically about — that
    information lives on `Target.role` (`lib/targets.py`), and neither
    `DetectorConfig` nor `Effective` carries it. Omitting `roles` disables
    `victim_pressure` and `zswap_refault` stays role-agnostic, so a caller
    that does not pass it gets a strictly smaller, still-correct set of
    events rather than a crash or a false positive.
    """

    def __init__(
        self,
        config: DetectorConfig,
        limits: Dict[str, "Effective"],
        roles: Optional[Dict[str, str]] = None,
    ) -> None:
        self.config = config
        self.limits = limits
        self.roles = roles or {}
        # (cgroup, kind[, sub-channel]) -> the last severity band reached, so
        # threshold-based kinds fire only on an upward crossing.
        self._bands: Dict[Tuple[Any, ...], Optional[str]] = {}

    def _role(self, cgroup: str) -> str:
        return self.roles.get(cgroup, "subject")

    # ── per-tick metric events ──────────────────────────────────────────

    def observe(self, prev: Dict[str, Any], cur: Dict[str, Any], dt: float) -> List[Event]:
        if dt <= 0:
            return []
        t = cur.get("t", time.time())
        mono = cur.get("mono", 0.0)
        prev_cg = prev.get("cg") or {}
        cur_cg = cur.get("cg") or {}
        events: List[Event] = []
        for cgroup, cur_metrics in cur_cg.items():
            prev_metrics = prev_cg.get(cgroup)
            if prev_metrics is None:
                continue  # just appeared; topology() already covers that arrival
            events.extend(self._observe_cgroup(cgroup, prev_metrics, cur_metrics, dt, t, mono))
        return events

    def _observe_cgroup(
        self, cgroup: str, prev_m: Dict[str, Any], cur_m: Dict[str, Any], dt: float, t: float, mono: float
    ) -> List[Event]:
        out: List[Event] = []

        pmev = prev_m.get("memev") or {}
        cmev = cur_m.get("memev") or {}
        out.extend(self._counter_event(
            cgroup, "memory_high_breach", "warn", pmev.get("high"), cmev.get("high"), t, mono,
            lambda d, total: f"memory.high breached ({int(d)} more, {int(total)} total)",
        ))
        out.extend(self._counter_event(
            cgroup, "memory_max_breach", "serious", pmev.get("max"), cmev.get("max"), t, mono,
            lambda d, total: f"memory.max breached ({int(d)} more, {int(total)} total)",
        ))
        out.extend(self._counter_event(
            cgroup, "oom_kill", "critical", pmev.get("oom_kill"), cmev.get("oom_kill"), t, mono,
            lambda d, total: f"OOM killed {int(d)} process(es) ({int(total)} total)",
        ))

        for group, channel in (("psi_mem", "memory"), ("psi_cpu", "cpu"), ("psi_io", "io")):
            cur_psi = (cur_m.get(group) or {}).get("some_avg10")
            if cur_psi is None:
                continue
            out.extend(self._threshold_event(
                cgroup, "psi_spike", cur_psi, self.config.psi_some_warn, self.config.psi_some_serious,
                t, mono, key=(cgroup, "psi_spike", group),
                message=f"{channel} PSI some_avg10={cur_psi:.1f}",
                data_extra={"channel": channel, "some_avg10": cur_psi},
            ))

        pms = prev_m.get("memstat") or {}
        cms = cur_m.get("memstat") or {}

        swap_rate = _rate(pms.get("pswpout"), cms.get("pswpout"), dt)
        if swap_rate is not None:
            mb_s = swap_rate * _PAGE_BYTES / (1024 * 1024)
            out.extend(self._threshold_event(
                cgroup, "swap_burst", mb_s, self.config.swap_out_warn_mb_s, None, t, mono,
                key=(cgroup, "swap_burst"),
                message=f"swap-out {mb_s:.1f} MB/s",
                data_extra={"swap_out_mb_s": mb_s},
            ))

        # rfz/s: refaults served from zswap — fast, but still a pressure
        # signal, and unlike victim_pressure this applies to any role.
        rfz = _rate(pms.get("zswpin"), cms.get("zswpin"), dt)
        if rfz is not None:
            out.extend(self._threshold_event(
                cgroup, "zswap_refault", rfz, self.config.victim_rfd_warn, self.config.victim_rfd_serious,
                t, mono, key=(cgroup, "zswap_refault"),
                message=f"zswap refault {rfz:.1f}/s",
                data_extra={"rfz_per_s": rfz},
            ))

        if self._role(cgroup) == "observer":
            rfd = _rfd_per_s(pms, cms, dt)
            if rfd is not None:
                out.extend(self._threshold_event(
                    cgroup, "victim_pressure", rfd, self.config.victim_rfd_warn, self.config.victim_rfd_serious,
                    t, mono, key=(cgroup, "victim_pressure"),
                    message=f"observer disk-refault rate {rfd:.1f}/s",
                    data_extra={
                        "rfd_per_s": rfd,
                        "workingset_refault_anon_delta": _nonneg_delta(
                            pms.get("workingset_refault_anon"), cms.get("workingset_refault_anon")
                        ),
                        "zswpin_delta": _nonneg_delta(pms.get("zswpin"), cms.get("zswpin")),
                        "dt": dt,
                    },
                ))

        pcpu = prev_m.get("cpu") or {}
        ccpu = cur_m.get("cpu") or {}
        out.extend(self._counter_event(
            cgroup, "cpu_throttled", "warn", pcpu.get("nr_throttled"), ccpu.get("nr_throttled"), t, mono,
            lambda d, total: f"CPU throttled {int(d)} more period(s) ({int(total)} total)",
            data_extra={"throttled_usec_delta": _nonneg_delta(pcpu.get("throttled_usec"), ccpu.get("throttled_usec"))},
        ))

        return out

    def _counter_event(
        self, cgroup, kind, severity, prev_v, cur_v, t, mono, message_fn, data_extra=None,
    ) -> List[Event]:
        delta = _nonneg_delta(prev_v, cur_v)
        if not delta:  # None (missing/reset) or 0 (no new occurrences): nothing to report
            return []
        data: Dict[str, Any] = {"delta": delta, "total": cur_v}
        if data_extra:
            data.update(data_extra)
        return [_event(t, mono, kind, severity, cgroup, message_fn(delta, cur_v), data)]

    def _threshold_event(
        self, cgroup, kind, value, warn, serious, t, mono, key, message, data_extra=None,
    ) -> List[Event]:
        band = _severity_band(value, warn, serious)
        prev_band = self._bands.get(key)
        if _BAND_ORDER[band] < _BAND_ORDER.get(prev_band, 0):
            # De-escalate only once the value has fallen clearly below the
            # threshold, not the instant it dips under it. Measured on a real
            # host: a metric sitting *at* its threshold re-crosses it several
            # times a second, and across 29 sampled cgroups that produced 67
            # duplicate events in 20 idle seconds — an events list nobody can
            # read. Requiring a genuine retreat before re-arming turns that
            # back into one event per real excursion.
            rearm = self.config.rearm_ratio
            floor = (warn if warn is not None else 0.0) * rearm
            if value > floor:
                self._bands[key] = prev_band
                return []
        self._bands[key] = band
        if _BAND_ORDER[band] <= _BAND_ORDER.get(prev_band, 0):
            return []  # not an upward crossing: still quiet, or a de-escalation
        data: Dict[str, Any] = {"value": value, "warn_threshold": warn}
        if serious is not None:
            data["serious_threshold"] = serious
        if data_extra:
            data.update(data_extra)
        return [_event(t, mono, kind, band, cgroup, message, data)]

    # ── discovery / limits ──────────────────────────────────────────────

    def topology(
        self, appeared: List[str], disappeared: List[str], t: float, mono: float
    ) -> List[Event]:
        events: List[Event] = []
        for cgroup in appeared:
            events.append(_event(t, mono, "cgroup_appeared", "info", cgroup, f"{cgroup} appeared", {}))
        for cgroup in disappeared:
            events.append(_event(t, mono, "cgroup_disappeared", "warn", cgroup, f"{cgroup} disappeared", {}))
            # Drop latched state for this path so a later reappearance (a
            # container restarting under the same cgroup name) starts clean
            # instead of inheriting an "already serious" latch from the task
            # that just exited.
            for key in [k for k in self._bands if k[0] == cgroup]:
                del self._bands[key]
        return events

    def limits_changed(
        self, cgroup: str, old: "Effective", new: "Effective", t: float, mono: float
    ) -> List[Event]:
        """One `limit_drift` event when any cap this tool tracks moved.

        Compares by attribute (`getattr(..., default=None)`) rather than
        importing `Effective` for an `==` on the dataclass, so this keeps
        working against any object shaped like one — real or a test double —
        without a hard runtime dependency on `limits.py`.
        """
        fields = ("memory_max", "memory_high", "memory_swap_max", "cpu_cores", "pids_max")
        changed: List[str] = []
        old_vals: Dict[str, Any] = {}
        new_vals: Dict[str, Any] = {}
        for field in fields:
            ov = getattr(old, field, None)
            nv = getattr(new, field, None)
            if ov != nv:
                changed.append(field)
                old_vals[field] = ov
                new_vals[field] = nv
        old_io = getattr(old, "io_max", None)
        new_io = getattr(new, "io_max", None)
        if old_io != new_io:
            changed.append("io_max")
            old_vals["io_max"] = old_io
            new_vals["io_max"] = new_io
        if not changed:
            return []
        message = f"effective limits changed: {', '.join(changed)}"
        return [_event(t, mono, "limit_drift", "warn", cgroup, message, {
            "changed": changed, "old": old_vals, "new": new_vals,
        })]
