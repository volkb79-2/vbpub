"""Tests for the adaptive sampling loop.

Nothing here sleeps or reads the real clock: `Sampler` takes an injected
`clock`/`sleep`, and every test drives both by hand so the whole file runs in
milliseconds. The interval sequences asserted below are the literal
arithmetic from DESIGN.md §4.4 (`hot_interval=0.25`, `backoff=1.5`,
`idle_interval=2.0`) — if the adaptive rule changes, these numbers should
fail loudly.
"""

from __future__ import annotations

import math

import pytest

from lib.sampler import Sampler, SamplerConfig, activity_score
from tests.conftest import SUBJECT, cg_metrics


# ── fakes: no real time or IO anywhere in this file ─────────────────────────

class FakeClock:
    """Monotonic-shaped: increments by `step` on every call, never sleeps."""

    def __init__(self, step: float = 1.0):
        self._t = 0.0
        self._step = step

    def __call__(self) -> float:
        value = self._t
        self._t += self._step
        return value


class FakeSleep:
    """Records what it would have slept, instead of sleeping."""

    def __init__(self):
        self.calls = []

    def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


class FakeMembership:
    """`.refresh()` returns a scripted sequence, then `([], [])` forever."""

    def __init__(self, script=None):
        self.script = list(script) if script else []
        self.calls = 0

    def refresh(self):
        self.calls += 1
        if self.script:
            return self.script.pop(0)
        return ([], [])


def make_should_stop(n_ticks: int):
    """A predicate that lets exactly `n_ticks` iterations of `run()` happen."""
    count = [0]

    def _should_stop() -> bool:
        if count[0] >= n_ticks:
            return True
        count[0] += 1
        return False

    return _should_stop


def quiet_sample_fn(_membership):
    # Identical every call: every counter delta is zero, so activity_score
    # is 0.0 and only the backoff/hot_threshold arithmetic is exercised.
    return {"cg": {SUBJECT: cg_metrics(current=1024**3, usage_usec=0)}}


# ── activity_score() in isolation ───────────────────────────────────────────

class TestActivityScore:
    def test_empty_records_score_zero(self):
        assert activity_score({}, {}, 1.0) == 0.0

    def test_dt_zero_or_negative_is_zero_not_a_crash(self):
        cur = {"cg": {SUBJECT: cg_metrics(current=1, usage_usec=1)}}
        assert activity_score(cur, cur, 0.0) == 0.0
        assert activity_score(cur, cur, -1.0) == 0.0

    def test_vanished_cgroup_is_skipped_not_an_error(self):
        prev = {"cg": {
            SUBJECT: cg_metrics(current=1024**3, usage_usec=0),
            "/other": cg_metrics(current=1024**3, usage_usec=0),
        }}
        # /other is gone from cur entirely.
        cur = {"cg": {SUBJECT: cg_metrics(current=1024**3, usage_usec=0)}}
        score = activity_score(prev, cur, 1.0)
        assert score == 0.0
        assert not math.isnan(score)

    def test_newly_appeared_cgroup_has_no_baseline_and_is_skipped(self):
        prev = {"cg": {}}
        cur = {"cg": {SUBJECT: cg_metrics(current=999 * 1024**2, usage_usec=999_999_999)}}
        # No prior sample for SUBJECT to diff against; must not raise, and
        # contributes nothing (Sampler.run() snaps hot for topology changes
        # through a different path, not through the score).
        assert activity_score(prev, cur, 1.0) == 0.0

    def test_missing_metric_group_is_skipped_not_fatal(self):
        # As if this target only asked for @metrics=mem: every other group
        # (cpu, io, psi_*) is simply absent, not present-and-zero.
        prev_metrics = {"mem": {"current": 1024**3}}
        cur_metrics = {"mem": {"current": 1024**3 + 4 * 1024 * 1024}}
        prev = {"cg": {SUBJECT: prev_metrics}}
        cur = {"cg": {SUBJECT: cur_metrics}}
        score = activity_score(prev, cur, 1.0)
        assert not math.isnan(score)
        assert 0.0 < score <= 1.0  # 4 MiB/s / 8 MiB/s == 0.5

    def test_counter_reset_contributes_nothing_not_a_negative_spike(self):
        prev = {"cg": {SUBJECT: cg_metrics(current=1024**3, usage_usec=500_000)}}
        # usage_usec goes backwards: the cgroup was recreated.
        cur = {"cg": {SUBJECT: cg_metrics(current=1024**3, usage_usec=100_000)}}
        score = activity_score(prev, cur, 1.0)
        assert score == 0.0
        assert not math.isnan(score)

    def test_memory_signal_normalised_by_8_mib_s(self):
        prev = {"cg": {SUBJECT: cg_metrics(current=1024**3, usage_usec=0)}}
        cur = {"cg": {SUBJECT: cg_metrics(current=1024**3 + 4 * 1024 * 1024, usage_usec=0)}}
        assert activity_score(prev, cur, 1.0) == pytest.approx(0.5)

    def test_memory_signal_is_absolute_delta_a_drop_also_counts(self):
        prev = {"cg": {SUBJECT: cg_metrics(current=1024**3, usage_usec=0)}}
        cur = {"cg": {SUBJECT: cg_metrics(current=1024**3 - 4 * 1024 * 1024, usage_usec=0)}}
        assert activity_score(prev, cur, 1.0) == pytest.approx(0.5)

    def test_io_signal_normalised_by_50_mib_s(self):
        prev = {"cg": {SUBJECT: cg_metrics(current=1, usage_usec=0, rbytes=0, wbytes=0)}}
        cur = {"cg": {SUBJECT: cg_metrics(current=1, usage_usec=0, rbytes=25 * 1024 * 1024, wbytes=0)}}
        assert activity_score(prev, cur, 1.0) == pytest.approx(0.5)

    def test_psi_signal_normalised_by_20(self):
        prev = {"cg": {SUBJECT: cg_metrics(current=1, usage_usec=0, some10=0.0)}}
        cur = {"cg": {SUBJECT: cg_metrics(current=1, usage_usec=0, some10=10.0)}}
        assert activity_score(prev, cur, 1.0) == pytest.approx(0.5)

    def test_cpu_signal_uses_host_cores_available(self):
        import os
        cores = max(1, os.cpu_count() or 1)
        one_core_usec_per_s = 1_000_000
        prev = {"cg": {SUBJECT: cg_metrics(current=1, usage_usec=0)}}
        cur = {"cg": {SUBJECT: cg_metrics(current=1, usage_usec=one_core_usec_per_s)}}
        assert activity_score(prev, cur, 1.0) == pytest.approx(min(1.0, 1.0 / cores))

    def test_score_is_the_max_signal_not_their_sum(self):
        # memory alone would be 0.5 and io alone would be 0.5; the combined
        # score must still be 0.5, not 1.0.
        prev = {"cg": {SUBJECT: cg_metrics(current=1024**3, usage_usec=0, rbytes=0, wbytes=0)}}
        cur = {"cg": {SUBJECT: cg_metrics(
            current=1024**3 + 4 * 1024 * 1024, usage_usec=0, rbytes=25 * 1024 * 1024, wbytes=0,
        )}}
        assert activity_score(prev, cur, 1.0) == pytest.approx(0.5)

    def test_score_is_clamped_at_one(self):
        prev = {"cg": {SUBJECT: cg_metrics(current=0, usage_usec=0)}}
        cur = {"cg": {SUBJECT: cg_metrics(current=1024**3, usage_usec=0)}}  # a huge jump
        assert activity_score(prev, cur, 1.0) == 1.0

    def test_max_across_multiple_cgroups(self):
        other = "/wings.slice/wings-prod.slice"
        prev = {"cg": {
            SUBJECT: cg_metrics(current=1024**3, usage_usec=0),
            other: cg_metrics(current=1024**3, usage_usec=0),
        }}
        cur = {"cg": {
            SUBJECT: cg_metrics(current=1024**3, usage_usec=0),  # quiet
            other: cg_metrics(current=1024**3 + 4 * 1024 * 1024, usage_usec=0),  # busy
        }}
        assert activity_score(prev, cur, 1.0) == pytest.approx(0.5)

    def test_vanished_between_ticks_is_simply_not_visited(self):
        # The inverse of test_newly_appeared_cgroup_...: a cgroup that was
        # in `prev` but is gone from `cur` (the more common real case — the
        # task it belonged to exited between two samples). It must not be
        # dereferenced from `cur_cg` at all, let alone raise a KeyError.
        prev = {"cg": {SUBJECT: cg_metrics(current=1024**3, usage_usec=0)}}
        cur = {"cg": {}}
        assert activity_score(prev, cur, 1.0) == 0.0

    def test_missing_mem_group_skips_only_the_memory_signal(self):
        # "mem" absent (as if @metrics=cpu was requested) while cpu is
        # present and genuinely busy: the memory signal must contribute
        # nothing, not crash and not zero out the cpu signal that IS there.
        prev_metrics = {"cpu": {"usage_usec": 0}}
        cur_metrics = {"cpu": {"usage_usec": 1_000_000}}  # 1 core-second/s
        prev = {"cg": {SUBJECT: prev_metrics}}
        cur = {"cg": {SUBJECT: cur_metrics}}
        score = activity_score(prev, cur, 1.0)
        assert not math.isnan(score)
        assert score > 0.0  # the cpu signal alone must still register

    def test_negative_psi_value_is_clamped_not_propagated_negative(self):
        # Real PSI never reports negative, but nothing here parses the
        # value — a caller passing corrupt/synthetic data must not get a
        # negative "activity" out the other end. Only psi_mem is present
        # (as if @metrics=psi were requested alone) so the max-across-groups
        # in _psi_signal can't mask the negative value with an absent
        # channel's implicit "not present" — cg_metrics's fixed psi_cpu/
        # psi_io zeros would otherwise win the max() and hide this.
        prev = {"cg": {SUBJECT: {"psi_mem": {"some_avg10": 0.0}}}}
        cur = {"cg": {SUBJECT: {"psi_mem": {"some_avg10": -5.0}}}}
        score = activity_score(prev, cur, 1.0)
        assert score == 0.0
        assert not math.isnan(score)

    def test_nan_signal_is_clamped_to_zero_not_propagated(self):
        # float("nan") is exactly what Python's own float() parser produces
        # from the literal text "nan" — a corrupted PSI read is not required
        # to look exotic to trigger this.
        prev = {"cg": {SUBJECT: cg_metrics(current=1, usage_usec=0, some10=0.0)}}
        cur_metrics = cg_metrics(current=1, usage_usec=0, some10=0.0)
        cur_metrics["psi_mem"]["some_avg10"] = float("nan")
        cur = {"cg": {SUBJECT: cur_metrics}}
        score = activity_score(prev, cur, 1.0)
        assert score == 0.0
        assert not math.isnan(score)


class TestIoBytesRate:
    """`_io_bytes_rate` in isolation — the branch structure inside its device
    loop (a new device with no baseline, a counter reset on one or both
    fields) is easiest to pin down directly rather than by fighting
    `activity_score`'s normalisation into hitting exact edge values."""

    def test_device_with_no_baseline_is_skipped(self):
        from lib.sampler import _io_bytes_rate
        # A disk attached mid-run: present in cur, absent from prev.
        prev_io = {}
        cur_io = {"254:0": {"rbytes": 1024, "wbytes": 2048}}
        assert _io_bytes_rate(prev_io, cur_io, 1.0) is None

    def test_device_with_both_counters_reset_contributes_nothing(self):
        from lib.sampler import _io_bytes_rate
        # The device was recreated (or its counters wrapped): both rbytes
        # and wbytes go backwards. Neither is measurable, so the device is
        # exactly as if it were never sampled, not a huge negative rate.
        prev_io = {"254:0": {"rbytes": 1_000_000, "wbytes": 2_000_000}}
        cur_io = {"254:0": {"rbytes": 500, "wbytes": 900}}
        assert _io_bytes_rate(prev_io, cur_io, 1.0) is None

    def test_one_device_reset_does_not_hide_another_device_that_is_fine(self):
        from lib.sampler import _io_bytes_rate
        prev_io = {
            "254:0": {"rbytes": 1_000_000, "wbytes": 2_000_000},  # will reset
            "8:0": {"rbytes": 0, "wbytes": 0},
        }
        cur_io = {
            "254:0": {"rbytes": 100, "wbytes": 200},  # reset: contributes nothing
            "8:0": {"rbytes": 1024, "wbytes": 1024},  # normal: 2048 bytes/s
        }
        assert _io_bytes_rate(prev_io, cur_io, 1.0) == pytest.approx(2048.0)


# ── Sampler.run() interval scheduling ───────────────────────────────────────

class TestSamplerScheduling:
    def test_quiet_run_backs_off_multiplicatively_to_idle(self):
        config = SamplerConfig(hot_interval=0.25, idle_interval=2.0, backoff=1.5, hot_threshold=0.15)
        sleep = FakeSleep()
        sampler = Sampler(FakeMembership(), config, quiet_sample_fn, clock=FakeClock(), sleep=sleep)

        sampler.run(make_should_stop(8), on_sample=lambda record: [], on_topology=lambda a, d: None)

        expected = [0.25, 0.375, 0.5625, 0.84375, 1.265625, 1.8984375, 2.0, 2.0]
        assert sleep.calls == pytest.approx(expected)

    def test_first_tick_has_no_previous_sample_and_is_hot(self):
        config = SamplerConfig()
        sleep = FakeSleep()
        sampler = Sampler(FakeMembership(), config, quiet_sample_fn, clock=FakeClock(), sleep=sleep)

        sampler.run(make_should_stop(1), on_sample=lambda record: [], on_topology=lambda a, d: None)

        assert sleep.calls == [config.hot_interval]

    def test_event_snaps_next_interval_to_hot_then_backoff_resumes(self):
        config = SamplerConfig(hot_interval=0.25, idle_interval=2.0, backoff=1.5, hot_threshold=0.15)
        sleep = FakeSleep()
        sampler = Sampler(FakeMembership(), config, quiet_sample_fn, clock=FakeClock(), sleep=sleep)

        seen = [0]

        def on_sample(record):
            seen[0] += 1
            if seen[0] == 4:
                return ["a detected event"]
            return []

        sampler.run(make_should_stop(6), on_sample=on_sample, on_topology=lambda a, d: None)

        # ticks 1-3 quiet backoff, tick 4's event snaps the *next* sleep to
        # hot, ticks 5-6 resume backing off from hot again.
        expected = [0.25, 0.375, 0.5625, 0.25, 0.375, 0.5625]
        assert sleep.calls == pytest.approx(expected)

    def test_topology_change_snaps_to_hot_and_calls_on_topology(self):
        config = SamplerConfig(hot_interval=0.25, idle_interval=2.0, backoff=1.5, discovery_interval=2.0)
        sleep = FakeSleep()
        membership = FakeMembership(script=[([], []), (["/new.scope"], [])])
        sampler = Sampler(membership, config, quiet_sample_fn, clock=FakeClock(step=1.0), sleep=sleep)

        topology_calls = []
        sampler.run(
            make_should_stop(4),
            on_sample=lambda record: [],
            on_topology=lambda appeared, disappeared: topology_calls.append((appeared, disappeared)),
        )

        # discovery_interval=2.0 with a 1s clock step means refresh() runs at
        # mono=0 (first tick, always) and next at mono=2 (third tick).
        assert topology_calls == [(["/new.scope"], [])]
        expected = [0.25, 0.375, 0.25, 0.375]
        assert sleep.calls == pytest.approx(expected)

    def test_force_hot_snaps_the_next_interval(self):
        config = SamplerConfig(hot_interval=0.25, idle_interval=2.0, backoff=1.5)
        sleep = FakeSleep()
        sampler = Sampler(FakeMembership(), config, quiet_sample_fn, clock=FakeClock(), sleep=sleep)

        seen = [0]

        def on_sample(record):
            seen[0] += 1
            if seen[0] == 3:
                sampler.force_hot()  # simulates an out-of-band mark being noticed
            return []

        sampler.run(make_should_stop(5), on_sample=on_sample, on_topology=lambda a, d: None)

        expected = [0.25, 0.375, 0.25, 0.375, 0.5625]
        assert sleep.calls == pytest.approx(expected)

    def test_measured_activity_alone_triggers_hot_without_an_explicit_event(self):
        config = SamplerConfig(hot_interval=0.25, idle_interval=2.0, backoff=1.5, hot_threshold=0.15)
        sleep = FakeSleep()
        mem_by_call = [1024**3, 1024**3, 1024**3 + 50 * 1024 * 1024, 1024**3 + 50 * 1024 * 1024]
        call = [-1]

        def sample_fn(_membership):
            call[0] += 1
            idx = min(call[0], len(mem_by_call) - 1)
            return {"cg": {SUBJECT: cg_metrics(current=mem_by_call[idx], usage_usec=0)}}

        sampler = Sampler(FakeMembership(), config, sample_fn, clock=FakeClock(), sleep=sleep)
        sampler.run(make_should_stop(4), on_sample=lambda record: [], on_topology=lambda a, d: None)

        # tick3's huge memory jump crosses hot_threshold on its own merit.
        expected = [0.25, 0.375, 0.25, 0.375]
        assert sleep.calls == pytest.approx(expected)

    def test_max_duration_stops_without_a_trailing_sleep(self):
        config = SamplerConfig(hot_interval=0.25, idle_interval=2.0, backoff=1.5, max_duration=2.5)
        sleep = FakeSleep()
        sampler = Sampler(FakeMembership(), config, quiet_sample_fn, clock=FakeClock(step=1.0), sleep=sleep)

        samples = []

        def on_sample(record):
            samples.append(record)
            return []

        sampler.run(should_stop=lambda: False, on_sample=on_sample, on_topology=lambda a, d: None)

        # mono goes 0, 1, 2, 3 — the tick at mono=3 is delivered (on_sample
        # still sees it) but max_duration=2.5 stops the loop before sleeping.
        assert [s["mono"] for s in samples] == [0.0, 1.0, 2.0, 3.0]
        assert len(sleep.calls) == 3

    def test_tick_numbers_seq_and_mono_independently_of_scheduling(self):
        config = SamplerConfig()
        sampler = Sampler(FakeMembership(), config, quiet_sample_fn, clock=FakeClock(step=0.25), sleep=FakeSleep())
        first = sampler.tick()
        second = sampler.tick()
        assert first["seq"] == 0
        assert second["seq"] == 1
        assert first["mono"] == 0.0
        assert second["mono"] == pytest.approx(0.25)
        assert "cg" in first and SUBJECT in first["cg"]

    def test_tick_includes_proc_and_host_when_sample_fn_provides_them(self):
        def sample_fn(_membership):
            return {"cg": {}, "proc": {"4242": {"rss_anon": 1}}, "host": {"loadavg": [1.0, 0.5, 0.1]}}

        sampler = Sampler(FakeMembership(), SamplerConfig(), sample_fn, clock=FakeClock(), sleep=FakeSleep())
        record = sampler.tick()
        assert record["proc"] == {"4242": {"rss_anon": 1}}
        assert record["host"] == {"loadavg": [1.0, 0.5, 0.1]}

    def test_tick_omits_proc_and_host_when_sample_fn_does_not_provide_them(self):
        sampler = Sampler(FakeMembership(), SamplerConfig(), quiet_sample_fn, clock=FakeClock(), sleep=FakeSleep())
        record = sampler.tick()
        assert "proc" not in record
        assert "host" not in record


class TestSamplerResilience:
    """DESIGN.md §1's "absent is not zero, and a profiler that dies on a bad
    read is worse than no profiler" applies one level up here too: a single
    bad tick — a raising `sample_fn`, a vanished cgroup, a `sample_fn` that
    forgot to return anything — must degrade, not take the whole run down.
    """

    def test_sample_fn_returning_none_degrades_to_an_empty_sample(self):
        sampler = Sampler(FakeMembership(), SamplerConfig(), lambda _m: None, clock=FakeClock(), sleep=FakeSleep())
        record = sampler.tick()
        assert record["cg"] == {}
        assert "proc" not in record and "host" not in record

    def test_sample_fn_returning_empty_dict_degrades_to_an_empty_sample(self):
        sampler = Sampler(FakeMembership(), SamplerConfig(), lambda _m: {}, clock=FakeClock(), sleep=FakeSleep())
        record = sampler.tick()
        assert record["cg"] == {}

    def test_sample_fn_raising_does_not_crash_tick(self):
        def sample_fn(_membership):
            raise RuntimeError("metrics.py had a bug, or /proc briefly wasn't there")

        sampler = Sampler(FakeMembership(), SamplerConfig(), sample_fn, clock=FakeClock(), sleep=FakeSleep())
        record = sampler.tick()  # must not raise
        assert record["cg"] == {}
        assert record["seq"] == 0

    def test_sample_fn_raising_mid_run_does_not_abort_the_loop(self):
        # Fails on tick 2 only; ticks 1, 3, 4 are quiet. A crash on tick 2
        # would lose every sample taken so far, not just that one tick's.
        config = SamplerConfig(hot_interval=0.25, idle_interval=2.0, backoff=1.5)
        calls = [0]

        def sample_fn(_membership):
            calls[0] += 1
            if calls[0] == 2:
                raise ValueError("boom")
            return {"cg": {SUBJECT: cg_metrics(current=1024**3, usage_usec=0)}}

        sleep = FakeSleep()
        sampler = Sampler(FakeMembership(), config, sample_fn, clock=FakeClock(), sleep=sleep)
        delivered = []
        sampler.run(
            make_should_stop(4),
            on_sample=lambda record: delivered.append(record) or [],
            on_topology=lambda a, d: None,
        )
        assert len(delivered) == 4  # all four ticks were delivered, none lost
        assert delivered[1]["cg"] == {}  # tick 2 degraded to empty, not skipped
        # tick 1 forced-hot (first sample); tick 2 has no prior score to
        # compare against a *blank* record so it is also forced (score=0
        # from an empty vs non-empty cg is still a real signal-less read,
        # but the point under test is only "did not crash and kept going").
        assert len(sleep.calls) == 4

    def test_cgroup_vanishing_mid_run_does_not_crash_the_loop(self):
        # sample_fn reports SUBJECT on ticks 1-2, then it is gone (the task
        # exited) for ticks 3-4 — the shape a real container exit produces.
        calls = [0]

        def sample_fn(_membership):
            calls[0] += 1
            if calls[0] <= 2:
                return {"cg": {SUBJECT: cg_metrics(current=1024**3, usage_usec=0)}}
            return {"cg": {}}

        sleep = FakeSleep()
        sampler = Sampler(FakeMembership(), SamplerConfig(), sample_fn, clock=FakeClock(), sleep=sleep)
        delivered = []
        sampler.run(
            make_should_stop(4),
            on_sample=lambda record: delivered.append(record) or [],
            on_topology=lambda a, d: None,
        )
        assert [r["cg"] for r in delivered[2:]] == [{}, {}]

    def test_should_stop_true_on_the_first_call_takes_zero_samples(self):
        config = SamplerConfig()
        sleep = FakeSleep()
        sampled = []
        sampler = Sampler(
            FakeMembership(), config, lambda _m: sampled.append(1) or {"cg": {}}, clock=FakeClock(), sleep=sleep,
        )
        sampler.run(should_stop=lambda: True, on_sample=lambda record: [], on_topology=lambda a, d: None)
        assert sampled == []
        assert sleep.calls == []
        assert sampler._seq == 0  # tick() was never called either

    def test_max_duration_stops_exactly_on_a_tick_boundary(self):
        # mono lands on 0.0, 1.0, 2.0 exactly (integer clock step) with
        # max_duration=2.0: the >= comparison must catch equality, not only
        # "strictly past", or a run configured for exactly N seconds runs
        # one tick too many.
        config = SamplerConfig(max_duration=2.0)
        sleep = FakeSleep()
        sampler = Sampler(FakeMembership(), config, quiet_sample_fn, clock=FakeClock(step=1.0), sleep=sleep)
        delivered = []
        sampler.run(
            should_stop=lambda: False,
            on_sample=lambda record: delivered.append(record) or [],
            on_topology=lambda a, d: None,
        )
        assert [r["mono"] for r in delivered] == [0.0, 1.0, 2.0]
        assert len(sleep.calls) == 2  # the mono==2.0 tick is delivered but does not sleep
