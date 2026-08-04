"""Tests for event detection.

`victim_pressure` — an *observer*-role target's disk-backed refault rate
crossing the warn/serious line — is the reason this module exists, so it
gets the most scrutiny here. Everything else follows the same two shapes:
a counter that only ever increases (`memory_high_breach`, `oom_kill`, ...)
fires on any positive delta, and a rate/gauge (`psi_spike`, `swap_burst`,
`zswap_refault`, `victim_pressure`) is edge-triggered off a severity band so
a sustained spike is one event, not one per sample tick.

Metrics dicts here are built by hand rather than via `conftest.cg_metrics`,
which only parametrises the fields the sampler/analyzer tests need
(`memory.current`, `usage_usec`, io bytes, one `psi_mem` reading). Detection
also needs `memory.events.max`, `oom_kill`, `cpu.stat.nr_throttled` and
`pswpout`, none of which that helper exposes — this file's `metrics()`
covers exactly, and only, what `events.py` reads.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

import pytest

from lib.events import Detector, DetectorConfig
from tests.conftest import OBSERVER, SUBJECT


def metrics(
    *,
    mem_high: int = 0,
    mem_max: int = 0,
    oom_kill: int = 0,
    pswpout: int = 0,
    zswpin: int = 0,
    wra: int = 0,
    some10: float = 0.0,
    nr_throttled: int = 0,
    throttled_usec: int = 0,
) -> Dict[str, Any]:
    return {
        "memev": {"low": 0, "high": mem_high, "max": mem_max, "oom": 0, "oom_kill": oom_kill},
        "memstat": {"pswpout": pswpout, "zswpin": zswpin, "workingset_refault_anon": wra},
        "psi_mem": {"some_avg10": some10},
        "psi_cpu": {"some_avg10": 0.0},
        "psi_io": {"some_avg10": 0.0},
        "cpu": {"nr_throttled": nr_throttled, "throttled_usec": throttled_usec},
    }


def rec(cg: Dict[str, Any], t: float = 0.0, mono: float = 0.0) -> Dict[str, Any]:
    return {"t": t, "mono": mono, "cg": cg}


@dataclass
class _FakeEffective:
    """Duck-typed stand-in for `limits.Effective` — see the module docstring
    in `lib/events.py`: `limits_changed` compares by attribute, not by
    importing the real dataclass, precisely so this file doesn't need
    `limits.py` to exist."""

    memory_max: Optional[int] = None
    memory_high: Optional[int] = None
    memory_swap_max: Optional[int] = None
    cpu_cores: Optional[float] = None
    pids_max: Optional[int] = None
    io_max: Dict[str, Any] = None

    def __post_init__(self):
        if self.io_max is None:
            self.io_max = {}


class TestPrivateRateHelpers:
    """`observe()` already guards `dt <= 0` before anything downstream of it
    runs, so these two helpers' own guards are unreachable through the
    public API — they exist so the helpers stay correct on their own if
    something else ever calls them directly (`_rfd_per_s` in particular
    reproduces a formula from a standalone host monitor and is exactly the
    kind of thing worth being able to call in isolation). Tested directly
    rather than pragma'd, since the guard is real code with real behaviour,
    not dead weight."""

    def test_rate_returns_none_for_non_positive_dt(self):
        from lib.events import _rate
        assert _rate(1.0, 2.0, 0.0) is None
        assert _rate(1.0, 2.0, -1.0) is None

    def test_rfd_per_s_returns_none_for_non_positive_dt(self):
        from lib.events import _rfd_per_s
        memstat = {"workingset_refault_anon": 100, "zswpin": 10}
        assert _rfd_per_s(memstat, memstat, 0.0) is None
        assert _rfd_per_s(memstat, memstat, -1.0) is None


class TestCounterEvents:
    def test_memory_high_breach_fires_with_delta_and_total(self):
        prev = rec({SUBJECT: metrics(mem_high=0)})
        cur = rec({SUBJECT: metrics(mem_high=3)}, t=5.0, mono=5.0)
        events = Detector(DetectorConfig(), limits={}).observe(prev, cur, dt=1.0)
        assert len(events) == 1
        ev = events[0]
        assert ev.kind == "memory_high_breach"
        assert ev.severity == "warn"
        assert ev.target == SUBJECT
        assert ev.data["delta"] == 3
        assert ev.data["total"] == 3
        assert ev.t == 5.0 and ev.mono == 5.0

    def test_no_change_produces_no_event(self):
        prev = rec({SUBJECT: metrics()})
        cur = rec({SUBJECT: metrics()})
        assert Detector(DetectorConfig(), limits={}).observe(prev, cur, dt=1.0) == []

    def test_memory_max_breach_is_serious(self):
        prev = rec({SUBJECT: metrics(mem_max=0)})
        cur = rec({SUBJECT: metrics(mem_max=1)})
        events = Detector(DetectorConfig(), limits={}).observe(prev, cur, dt=1.0)
        assert [e.kind for e in events] == ["memory_max_breach"]
        assert events[0].severity == "serious"

    def test_oom_kill_is_critical_and_carries_the_count(self):
        prev = rec({SUBJECT: metrics(oom_kill=0)})
        cur = rec({SUBJECT: metrics(oom_kill=2)})
        events = Detector(DetectorConfig(), limits={}).observe(prev, cur, dt=1.0)
        assert events[0].kind == "oom_kill"
        assert events[0].severity == "critical"
        assert events[0].data["delta"] == 2

    def test_counter_reset_produces_no_negative_spike_not_an_error(self):
        prev = rec({SUBJECT: metrics(mem_high=5)})
        cur = rec({SUBJECT: metrics(mem_high=1)})  # cgroup recreated, counter went backwards
        assert Detector(DetectorConfig(), limits={}).observe(prev, cur, dt=1.0) == []

    def test_cpu_throttled_carries_period_and_usec_deltas(self):
        prev = rec({SUBJECT: metrics(nr_throttled=0, throttled_usec=0)})
        cur = rec({SUBJECT: metrics(nr_throttled=2, throttled_usec=150_000)})
        events = Detector(DetectorConfig(), limits={}).observe(prev, cur, dt=1.0)
        assert events[0].kind == "cpu_throttled"
        assert events[0].data["delta"] == 2
        assert events[0].data["throttled_usec_delta"] == 150_000

    def test_dt_zero_returns_no_events(self):
        prev = rec({SUBJECT: metrics(mem_high=0)})
        cur = rec({SUBJECT: metrics(mem_high=5)})
        assert Detector(DetectorConfig(), limits={}).observe(prev, cur, dt=0.0) == []

    def test_newly_appeared_cgroup_has_no_baseline_and_is_skipped(self):
        prev = rec({})
        cur = rec({SUBJECT: metrics(mem_high=5)})
        assert Detector(DetectorConfig(), limits={}).observe(prev, cur, dt=1.0) == []

    def test_vanished_between_ticks_is_simply_not_visited(self):
        # The mirror image of the appeared case: a cgroup that was in `prev`
        # but is gone from `cur` (the task it belonged to exited). observe()
        # only walks cur["cg"], so this must produce no events and no
        # KeyError, not a stale reading of the last thing it saw.
        prev = rec({SUBJECT: metrics(mem_high=5)})
        cur = rec({})
        assert Detector(DetectorConfig(), limits={}).observe(prev, cur, dt=1.0) == []

    def test_completely_empty_metrics_dicts_produce_no_events(self):
        # A target that asked for no metric groups at all (or a reader that
        # failed every single group this tick): every sub-lookup in
        # _observe_cgroup falls back to `{}`, so nothing should fire and
        # nothing should raise.
        prev = rec({SUBJECT: {}})
        cur = rec({SUBJECT: {}})
        assert Detector(DetectorConfig(), limits={}).observe(prev, cur, dt=1.0) == []


class TestThresholdEvents:
    def test_psi_spike_fires_once_on_crossing_then_stays_quiet_while_sustained(self):
        config = DetectorConfig(psi_some_warn=20.0, psi_some_serious=50.0)
        d = Detector(config, limits={})
        low = rec({SUBJECT: metrics(some10=5.0)})
        hot = rec({SUBJECT: metrics(some10=25.0)})
        still_hot = rec({SUBJECT: metrics(some10=26.0)})
        first = d.observe(low, hot, dt=1.0)
        second = d.observe(hot, still_hot, dt=1.0)
        assert [e.kind for e in first] == ["psi_spike"]
        assert first[0].severity == "warn"
        assert first[0].data["some_avg10"] == 25.0
        assert second == []  # still above warn but no new crossing: no repeat

    def test_psi_spike_escalates_to_serious_on_a_second_crossing(self):
        config = DetectorConfig(psi_some_warn=20.0, psi_some_serious=50.0)
        d = Detector(config, limits={})
        a = rec({SUBJECT: metrics(some10=5.0)})
        b = rec({SUBJECT: metrics(some10=25.0)})
        c = rec({SUBJECT: metrics(some10=60.0)})
        d.observe(a, b, dt=1.0)
        events = d.observe(b, c, dt=1.0)
        assert [e.kind for e in events] == ["psi_spike"]
        assert events[0].severity == "serious"

    def test_psi_spike_refires_after_recovering_and_crossing_again(self):
        config = DetectorConfig(psi_some_warn=20.0, psi_some_serious=50.0)
        d = Detector(config, limits={})
        low = rec({SUBJECT: metrics(some10=1.0)})
        hot = rec({SUBJECT: metrics(some10=25.0)})
        d.observe(low, hot, dt=1.0)
        recovered = d.observe(hot, low, dt=1.0)
        again = d.observe(low, hot, dt=1.0)
        assert recovered == []
        assert [e.kind for e in again] == ["psi_spike"]

    def test_swap_burst_normalises_pages_to_mb_per_s(self):
        config = DetectorConfig(swap_out_warn_mb_s=1.0)
        d = Detector(config, limits={})
        # 300 pages/s * 4096 bytes/page ~= 1.17 MB/s, over a 1.0 MB/s line.
        prev = rec({SUBJECT: metrics(pswpout=0)})
        cur = rec({SUBJECT: metrics(pswpout=300)})
        events = d.observe(prev, cur, dt=1.0)
        assert len(events) == 1
        assert events[0].kind == "swap_burst"
        assert events[0].data["swap_out_mb_s"] == pytest.approx(300 * 4096 / (1024 * 1024))

    def test_zswap_refault_fires_regardless_of_role(self):
        config = DetectorConfig(victim_rfd_warn=10.0)
        d = Detector(config, limits={})  # no roles at all: SUBJECT defaults to "subject"
        prev = rec({SUBJECT: metrics(zswpin=0)})
        cur = rec({SUBJECT: metrics(zswpin=20)})
        events = d.observe(prev, cur, dt=1.0)
        assert [e.kind for e in events] == ["zswap_refault"]
        assert events[0].data["rfz_per_s"] == pytest.approx(20.0)

    def test_threshold_event_without_extra_data_still_produces_a_clean_event(self):
        # Every call site inside _observe_cgroup happens to pass a non-empty
        # data_extra, but the parameter defaults to None because the method
        # is a general building block, not something wired one way only.
        # Called directly to exercise that default rather than contriving a
        # metric combination that would never actually omit data_extra.
        d = Detector(DetectorConfig(), limits={})
        events = d._threshold_event(
            SUBJECT, "custom_kind", 5.0, warn=1.0, serious=None,
            t=0.0, mono=0.0, key=(SUBJECT, "custom_kind"), message="hand-rolled",
        )
        assert len(events) == 1
        assert events[0].data == {"value": 5.0, "warn_threshold": 1.0}


class TestVictimPressure:
    def test_fires_only_for_observer_role_targets(self):
        config = DetectorConfig(victim_rfd_warn=10.0, victim_rfd_serious=50.0)
        prev = rec({OBSERVER: metrics(wra=0, zswpin=0), SUBJECT: metrics(wra=0, zswpin=0)})
        # Both cross the same raw refault rate; only OBSERVER is tagged.
        cur = rec({OBSERVER: metrics(wra=30, zswpin=0), SUBJECT: metrics(wra=30, zswpin=0)})
        d = Detector(config, limits={}, roles={OBSERVER: "observer"})
        events = d.observe(prev, cur, dt=1.0)
        by_target = {(e.target, e.kind) for e in events}
        assert (OBSERVER, "victim_pressure") in by_target
        assert (SUBJECT, "victim_pressure") not in by_target

    def test_default_roles_disables_victim_pressure(self):
        config = DetectorConfig(victim_rfd_warn=10.0)
        prev = rec({OBSERVER: metrics(wra=0, zswpin=0)})
        cur = rec({OBSERVER: metrics(wra=30, zswpin=0)})
        d = Detector(config, limits={})  # roles omitted entirely — the default
        events = d.observe(prev, cur, dt=1.0)
        assert all(e.kind != "victim_pressure" for e in events)

    def test_rfd_is_refaults_minus_zswapin_clamped_at_zero(self):
        config = DetectorConfig(victim_rfd_warn=1.0)
        prev = rec({OBSERVER: metrics(wra=1000, zswpin=400)})
        # wra grows by 50, zswpin grows by 60: zswap absorbed more than
        # refaulted, so rfd/s must clamp at 0, never go negative. (zswpin's
        # own rate of 60/s is high enough to trip the role-agnostic
        # zswap_refault kind separately — that is not what this asserts.)
        cur = rec({OBSERVER: metrics(wra=1050, zswpin=460)})
        d = Detector(config, limits={}, roles={OBSERVER: "observer"})
        events = d.observe(prev, cur, dt=1.0)
        assert all(e.kind != "victim_pressure" for e in events)

    def test_victim_pressure_data_carries_the_triggering_numbers(self):
        config = DetectorConfig(victim_rfd_warn=10.0, victim_rfd_serious=50.0)
        prev = rec({OBSERVER: metrics(wra=1000, zswpin=400)})
        # +100 wra, +20 zswpin over dt=1 => rfd/s = (100-20)/1 = 80. zswpin's
        # own 20/s rate also crosses the (role-agnostic) zswap_refault warn
        # line, so this filters for victim_pressure specifically.
        cur = rec({OBSERVER: metrics(wra=1100, zswpin=420)}, t=42.0, mono=12.0)
        d = Detector(config, limits={}, roles={OBSERVER: "observer"})
        events = [e for e in d.observe(prev, cur, dt=1.0) if e.kind == "victim_pressure"]
        assert len(events) == 1
        ev = events[0]
        assert ev.kind == "victim_pressure"
        assert ev.severity == "serious"  # 80 >= victim_rfd_serious(50)
        assert ev.target == OBSERVER
        assert ev.data["rfd_per_s"] == pytest.approx(80.0)
        assert ev.data["workingset_refault_anon_delta"] == 100
        assert ev.data["zswpin_delta"] == 20
        assert ev.t == 42.0 and ev.mono == 12.0

    def test_stays_silent_when_the_refault_counter_resets(self):
        # workingset_refault_anon goes backwards between prev and cur: the
        # observer's cgroup was recreated (a real production container
        # restart). rfd/s must come back "unmeasurable", not a bogus event
        # built by subtracting a smaller number from a bigger one and
        # clamping the sign away.
        config = DetectorConfig(victim_rfd_warn=1.0)
        prev = rec({OBSERVER: metrics(wra=1000, zswpin=0)})
        cur = rec({OBSERVER: metrics(wra=100, zswpin=0)})  # wra went backwards
        d = Detector(config, limits={}, roles={OBSERVER: "observer"})
        events = d.observe(prev, cur, dt=1.0)
        assert all(e.kind != "victim_pressure" for e in events)


class TestTopology:
    def test_appeared_and_disappeared_events(self):
        d = Detector(DetectorConfig(), limits={})
        events = d.topology(appeared=["/new.scope"], disappeared=["/gone.scope"], t=1.0, mono=1.0)
        by_kind = {(e.kind, e.target, e.severity) for e in events}
        assert ("cgroup_appeared", "/new.scope", "info") in by_kind
        assert ("cgroup_disappeared", "/gone.scope", "warn") in by_kind

    def test_disappearance_clears_latched_threshold_state(self):
        config = DetectorConfig(psi_some_warn=20.0)
        d = Detector(config, limits={})
        low = rec({SUBJECT: metrics(some10=1.0)})
        hot = rec({SUBJECT: metrics(some10=25.0)})
        first = d.observe(low, hot, dt=1.0)
        assert [e.kind for e in first] == ["psi_spike"]
        assert d.observe(hot, hot, dt=1.0) == []  # sustained: no repeat

        d.topology(appeared=[], disappeared=[SUBJECT], t=2.0, mono=2.0)

        # A new task lands under the same cgroup path; without the latch
        # reset this would stay silent forever since the "band" never drops.
        refired = d.observe(hot, hot, dt=1.0)
        assert [e.kind for e in refired] == ["psi_spike"]

    def test_disappearance_bypasses_hysteresis_even_at_an_elevated_but_unrearmed_value(self):
        """Two features (the rearm_ratio hysteresis and the topology latch
        reset) interact here, and a plausible bug is that they don't: the
        latch reset could easily have been implemented against some *other*
        piece of state than `self._bands` and missed this. Sets up a cgroup
        stuck "elevated but not yet rearmed" (dipped below warn but not
        below warn*rearm_ratio, so hysteresis is deliberately suppressing a
        re-fire) and shows the *same* re-crossing is suppressed without a
        restart but fires immediately once `topology()` has seen the task
        leave — a real container restart must get a clean read, not inherit
        the old task's half-armed state.
        """
        config = DetectorConfig(psi_some_warn=20.0, rearm_ratio=0.5)  # floor = 10.0

        # Control: no disappearance in between — the re-crossing to 25.0 is
        # correctly suppressed because 15.0 never fell below the floor.
        control = Detector(config, limits={})
        low = rec({SUBJECT: metrics(some10=1.0)})
        hot = rec({SUBJECT: metrics(some10=25.0)})
        partial = rec({SUBJECT: metrics(some10=15.0)})  # below warn, above floor
        assert [e.kind for e in control.observe(low, hot, dt=1.0)] == ["psi_spike"]
        assert control.observe(hot, partial, dt=1.0) == []  # not rearmed yet
        assert control.observe(partial, hot, dt=1.0) == []  # still suppressed

        # Treatment: identical metric sequence, but the cgroup is reported
        # as having disappeared and reappeared between the dip and the
        # re-crossing.
        treated = Detector(config, limits={})
        assert [e.kind for e in treated.observe(low, hot, dt=1.0)] == ["psi_spike"]
        assert treated.observe(hot, partial, dt=1.0) == []
        treated.topology(appeared=[], disappeared=[SUBJECT], t=1.0, mono=1.0)
        assert [e.kind for e in treated.observe(partial, hot, dt=1.0)] == ["psi_spike"]


class TestLimitsChanged:
    def test_no_change_is_no_event(self):
        old = _FakeEffective(memory_max=1000, memory_high=800, cpu_cores=2.0)
        new = _FakeEffective(memory_max=1000, memory_high=800, cpu_cores=2.0)
        assert Detector(DetectorConfig(), limits={}).limits_changed(SUBJECT, old, new, t=0.0, mono=0.0) == []

    def test_a_tightened_cap_is_reported_with_old_and_new(self):
        old = _FakeEffective(memory_max=2_000_000_000)
        new = _FakeEffective(memory_max=1_000_000_000)
        events = Detector(DetectorConfig(), limits={}).limits_changed(SUBJECT, old, new, t=3.0, mono=3.0)
        assert len(events) == 1
        ev = events[0]
        assert ev.kind == "limit_drift"
        assert ev.severity == "warn"
        assert ev.target == SUBJECT
        assert "memory_max" in ev.data["changed"]
        assert ev.data["old"]["memory_max"] == 2_000_000_000
        assert ev.data["new"]["memory_max"] == 1_000_000_000

    def test_io_max_dict_changes_are_detected_too(self):
        old = _FakeEffective(io_max={"254:0": {"rbps": 1000}})
        new = _FakeEffective(io_max={"254:0": {"rbps": 500}})
        events = Detector(DetectorConfig(), limits={}).limits_changed(SUBJECT, old, new, t=0.0, mono=0.0)
        assert events[0].data["changed"] == ["io_max"]


# ── hysteresis on re-arming (added during integration) ──────────────────────

def _psi_pair(before: float, after: float):
    """Two samples differing only in one cgroup's memory PSI."""
    def one(value):
        return {"cg": {"/x": {"psi_mem": {"some_avg10": value, "some_total": 0},
                              "mem": {"current": 1000}, "memstat": {}, "memev": {},
                              "cpu": {}, "io": {}}}}
    return one(before), one(after)


def test_a_metric_flapping_at_its_threshold_fires_once(monkeypatch):
    """Regression: on a real host a metric sitting at its threshold re-crossed
    it several times a second, and 29 sampled cgroups turned that into 67
    duplicate events in 20 idle seconds."""
    det = Detector(DetectorConfig(psi_some_warn=20.0), {})
    fired = 0
    # oscillate just above and just below the warn line
    for value in (25.0, 19.0, 25.0, 18.0, 26.0, 19.5, 24.0):
        prev, cur = _psi_pair(0.0, value)
        det._bands.setdefault(("/x", "psi_spike"), None)
        fired += len([e for e in det.observe(prev, cur, 1.0) if e.kind == "psi_spike"])
    assert fired == 1, f"expected one event for one sustained excursion, got {fired}"


def test_a_genuine_retreat_re_arms_the_event():
    det = Detector(DetectorConfig(psi_some_warn=20.0), {})
    prev, cur = _psi_pair(0.0, 25.0)
    assert any(e.kind == "psi_spike" for e in det.observe(prev, cur, 1.0))
    # fall clearly below warn * rearm_ratio (10.0), then spike again
    prev, cur = _psi_pair(0.0, 2.0)
    det.observe(prev, cur, 1.0)
    prev, cur = _psi_pair(0.0, 30.0)
    assert any(e.kind == "psi_spike" for e in det.observe(prev, cur, 1.0))


def test_rearm_ratio_of_one_restores_bare_edge_triggering():
    det = Detector(DetectorConfig(psi_some_warn=20.0, rearm_ratio=1.0), {})
    fired = 0
    for value in (25.0, 19.0, 25.0):
        prev, cur = _psi_pair(0.0, value)
        fired += len([e for e in det.observe(prev, cur, 1.0) if e.kind == "psi_spike"])
    assert fired == 2
