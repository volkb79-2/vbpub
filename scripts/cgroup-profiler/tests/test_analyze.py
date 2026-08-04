"""Tests for lib/analyze.py.

Most of ``build()``'s end-to-end coverage runs against the real
``lib.store.RunDir``. ``FakeRunDir`` below stays as a second, minimal
implementation of the same shape (``.path``, ``.read(name)``,
``.read_manifest()`` — DESIGN.md §4.3) so ``analyze.build`` is proven to work
against *any* object with that shape, per its own contract, not just the one
concrete class.
"""

from __future__ import annotations

import json
import math
import warnings
from pathlib import Path
from typing import Any, Dict, Iterator

import numpy as np
import pandas as pd
import pytest

from lib import analyze
from lib.model import Analysis, Mark, Phase, Proposal, Series
from lib.store import RunDir

from conftest import OBSERVER, SUBJECT, cg_metrics, make_sample


class FakeRunDir:
    """Stand-in for ``store.RunDir`` — see module docstring."""

    def __init__(self, path: Path):
        self.path = str(path)

    def read(self, name: str) -> Iterator[Dict[str, Any]]:
        file_path = Path(self.path) / name
        if not file_path.exists():
            return
        for line in file_path.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue  # tolerate a truncated final line, per DESIGN.md §3

    def read_manifest(self) -> Dict[str, Any]:
        return json.loads((Path(self.path) / "manifest.json").read_text())


def _isnan(x: Any) -> bool:
    return isinstance(x, float) and math.isnan(x)


# ── to_frame ─────────────────────────────────────────────────────────────

class TestToFrame:
    def test_shape_and_index(self, sample_records):
        df = analyze.to_frame(sample_records)
        assert isinstance(df.index, pd.TimedeltaIndex)
        assert list(df.index.total_seconds())[:3] == [0.0, 1.0, 2.0]
        assert (SUBJECT, "mem.current") in df.columns
        assert (OBSERVER, "psi_mem.some_avg10") in df.columns

    def test_raw_gauge_values_round_trip(self, sample_records):
        df = analyze.to_frame(sample_records)
        # seq=0 -> mono=0, idle, subject current == 1 GiB exactly (see conftest).
        assert df[(SUBJECT, "mem.current")].iloc[0] == 1 * 1024**3

    def test_absent_metric_is_nan_not_zero(self):
        """A target sampled without a group (metrics=mem only, say) must show
        NaN for the missing group's fields, never a fabricated 0."""
        records = [
            make_sample(0, 0.0, cgroups={SUBJECT: {"mem": {"current": 100}}}),
            make_sample(1, 1.0, cgroups={SUBJECT: {"mem": {"current": 200}}}),
        ]
        df = analyze.to_frame(records)
        assert (SUBJECT, "psi_mem.some_avg10") not in df.columns
        assert df[(SUBJECT, "mem.current")].tolist() == [100.0, 200.0]

    def test_counter_reset_yields_nan_rate_not_negative_spike(self):
        """A cgroup recreated mid-run: the counter goes backwards. The
        derived rate must be NaN, never a huge negative number."""
        records = [
            make_sample(0, 0.0, cgroups={SUBJECT: cg_metrics(current=0, usage_usec=1_000_000)}),
            make_sample(1, 1.0, cgroups={SUBJECT: cg_metrics(current=0, usage_usec=2_000_000)}),
            make_sample(2, 2.0, cgroups={SUBJECT: cg_metrics(current=0, usage_usec=100)}),  # reset
            make_sample(3, 3.0, cgroups={SUBJECT: cg_metrics(current=0, usage_usec=1_100)}),
        ]
        df = analyze.to_frame(records)
        cores = df[(SUBJECT, "cpu.cores")]
        assert cores.iloc[1] == pytest.approx(1.0)   # (2e6-1e6)/1e6 usec-to-cores over 1s
        assert _isnan(cores.iloc[2])                  # the reset tick: unknown, not negative
        assert cores.iloc[3] > 0                       # recovers once the counter is monotonic again

    def test_never_fills_a_gap(self, sample_records):
        """Sanity check on the central contract: to_frame must not call
        fillna anywhere reachable from a normal build — assert no all-NaN
        column got turned into zeros by checking a genuinely-missing series
        stays missing end to end."""
        records = [make_sample(0, 0.0, cgroups={SUBJECT: {"mem": {"current": 5}}})]
        df = analyze.to_frame(records)
        assert (SUBJECT, "io.rbytes_per_s") not in df.columns

    def test_no_samples_at_all_yields_an_empty_frame(self):
        df = analyze.to_frame([])
        assert df.empty
        assert list(df.columns) == []

    def test_a_tick_where_every_cgroup_is_unreadable_stays_in_the_index_as_nan(self):
        """A total collector outage for one tick — every target's `cg` entry
        absent — must still leave a row for that `mono`, all-NaN, so a chart
        breaks there instead of drawing straight through the gap (the earlier
        version of this code dropped the tick from the index entirely)."""
        records = [
            make_sample(0, 0.0, cgroups={SUBJECT: {"mem": {"current": 10}}}),
            make_sample(1, 1.0, cgroups={}),  # nothing readable this tick
            make_sample(2, 2.0, cgroups={SUBJECT: {"mem": {"current": 30}}}),
        ]
        df = analyze.to_frame(records)
        assert list(df.index.total_seconds()) == [0.0, 1.0, 2.0]
        assert _isnan(df[(SUBJECT, "mem.current")].iloc[1])

    def test_single_sample_has_no_computable_rate(self):
        """One tick has no `dt` to divide by — a rate must be NaN, not a
        crash from a zero/undefined interval."""
        records = [make_sample(0, 0.0, cgroups={SUBJECT: cg_metrics(current=1, usage_usec=1_000_000)})]
        df = analyze.to_frame(records)
        assert _isnan(df[(SUBJECT, "cpu.cores")].iloc[0])


class TestFlattenGroup:
    """`_flatten_group` is the one place a raw sample record's shape meets
    this module's assumptions; each branch here is a real "what if the
    collector wrote something odd" case, not padding."""

    def test_a_group_whose_payload_is_not_a_dict_is_skipped(self):
        out = analyze._flatten_group({"mem": "not-a-dict"})
        assert out == {}

    def test_io_device_entry_that_is_not_a_dict_is_skipped(self):
        out = analyze._flatten_group({"io": {"254:0": "garbage"}})
        assert out == {}

    def test_io_field_with_none_value_is_excluded_from_the_sum(self):
        out = analyze._flatten_group({"io": {"254:0": {"rbytes": 100, "wbytes": None}}})
        assert out == {"io.rbytes": 100}

    def test_io_with_no_usable_device_contributes_no_columns(self):
        out = analyze._flatten_group({"io": {}})
        assert out == {}

    def test_non_numeric_non_none_field_is_dropped(self):
        out = analyze._flatten_group({"mem": {"current": 10, "weird": "a string"}})
        assert out == {"mem.current": 10}

    def test_none_valued_field_is_kept_as_none(self):
        """A None here becomes NaN once it reaches the frame — it must not
        be dropped outright, or an explicit "not measurable" tick would
        look identical to a metric that was never sampled at all."""
        out = analyze._flatten_group({"mem": {"current": None}})
        assert out == {"mem.current": None}


# ── build_series ─────────────────────────────────────────────────────────

class TestBuildSeries:
    def test_empty_df_yields_no_series(self):
        assert analyze.build_series(analyze.to_frame([])) == []

    def test_produces_series_for_known_metrics_only(self, sample_records):
        df = analyze.to_frame(sample_records)
        series = analyze.build_series(df)
        keys = {s.key for s in series}
        assert "mem.current" in keys
        assert "cpu.cores" in keys
        assert "faults.rfd_per_s" in keys
        # cpu.usage_usec is a raw counter, not in the chart registry.
        assert "cpu.usage_usec" not in keys

    def test_none_breaks_the_line_not_zero(self):
        records = [
            make_sample(0, 0.0, cgroups={SUBJECT: {"mem": {"current": 10}}}),
            make_sample(1, 1.0, cgroups={}),  # cgroup vanished this tick
            make_sample(2, 2.0, cgroups={SUBJECT: {"mem": {"current": 30}}}),
        ]
        df = analyze.to_frame(records)
        series = [s for s in analyze.build_series(df) if s.key == "mem.current"][0]
        assert series.v == [10.0, None, 30.0]

    def test_all_values_are_json_safe(self, sample_records):
        """No stray NaN/np.float64 should leak into Series.v — JSON has no
        NaN token, and the model contract is an explicit None."""
        df = analyze.to_frame(sample_records)
        for s in analyze.build_series(df):
            for v in s.v:
                assert v is None or isinstance(v, float)
                if isinstance(v, float):
                    assert not math.isnan(v)


# ── resample_frame ───────────────────────────────────────────────────────

class TestResampleFrame:
    def test_uses_pandas_resample_and_preserves_gaps(self, sample_records):
        df = analyze.to_frame(sample_records)
        resampled = analyze.resample_frame(df, "5s")
        assert isinstance(resampled.index, pd.TimedeltaIndex)
        assert resampled.index.freq is not None or len(resampled) > 0
        # A bucket with no data anywhere must stay NaN, not become 0.
        empty_df = analyze.to_frame([make_sample(0, 0.0, cgroups={SUBJECT: {"mem": {"current": 1}}})])
        out = analyze.resample_frame(empty_df, "1s")
        assert len(out) == 1  # only the one real tick

    def test_frame_with_no_rows_at_all_is_returned_unchanged(self):
        df = analyze.to_frame([])
        assert analyze.resample_frame(df, "1s") is df


# ── changepoint detection ────────────────────────────────────────────────

class TestDetectChangepoints:
    def test_finds_the_step_at_10s_with_the_default_penalty(self, sample_records):
        """The fixture's docstring is explicit: 'the step at t=10s is what
        changepoint detection is expected to find.' Assert it using the
        function's own default (auto) penalty — the point is that the
        mechanism works out of the box on a short run, not that some
        hand-tuned constant can be found that makes it work."""
        df = analyze.to_frame(sample_records)
        columns = [(SUBJECT, "mem.current"), (SUBJECT, "psi_mem.some_avg10")]
        boundaries = analyze.detect_changepoints(df, columns)
        assert boundaries, "expected at least one detected changepoint"
        assert any(abs(b - 10.0) <= 2.0 for b in boundaries), boundaries

    def test_also_finds_the_return_to_idle_at_25s_with_the_default_penalty(self, sample_records):
        df = analyze.to_frame(sample_records)
        columns = [(SUBJECT, "mem.current"), (SUBJECT, "psi_mem.some_avg10")]
        boundaries = analyze.detect_changepoints(df, columns)
        assert any(abs(b - 25.0) <= 2.0 for b in boundaries), boundaries

    def test_explicit_penalty_overrides_the_auto_default(self, sample_records):
        """A very high explicit penalty must suppress even the fixture's
        clean step — proving the explicit value, not the auto one, was
        actually used."""
        df = analyze.to_frame(sample_records)
        columns = [(SUBJECT, "mem.current"), (SUBJECT, "psi_mem.some_avg10")]
        assert analyze.detect_changepoints(df, columns, penalty=1000.0) == []

    def test_auto_penalty_grows_with_sample_count(self):
        assert analyze._auto_penalty(40) == pytest.approx(math.log(40))
        assert analyze._auto_penalty(1000) == pytest.approx(math.log(1000))
        assert analyze._auto_penalty(1000) > analyze._auto_penalty(40)

    def test_auto_penalty_has_a_floor_for_very_small_n(self):
        assert analyze._auto_penalty(2) == 2.0  # log(2) < 2.0; the floor wins
        assert analyze._auto_penalty(0) == 2.0

    def test_auto_penalty_does_not_overfit_a_large_noisy_run(self):
        """The whole point of scaling the penalty with n: pure noise across
        many samples must not turn into a boundary at every wiggle. A fixed
        low constant (the kind that is needed to fire at all on 40 samples)
        would fail this at n=2000; the auto penalty must not."""
        rng = np.random.default_rng(42)
        n = 2000
        rows = [
            make_sample(i, float(i), cgroups={SUBJECT: {"mem": {"current": int(1_000_000 + rng.normal(0, 5))}}})
            for i in range(n)
        ]
        df = analyze.to_frame(rows)
        boundaries = analyze.detect_changepoints(df, [(SUBJECT, "mem.current")])
        assert len(boundaries) <= 5, (len(boundaries), boundaries[:10])

    def test_min_gap_collapses_nearby_boundaries(self):
        """A very low penalty makes Pelt report a raw boundary every 5 ticks
        on 60 noisy points (verified empirically); `min_gap=10` must then
        collapse every pair closer together than that into one, exercising
        both the "keep" and the "drop, too close" side of the merge."""
        rng = np.random.default_rng(0)
        rows = [
            make_sample(i, float(i), cgroups={SUBJECT: {"mem": {"current": int(1000 + rng.normal(0, 50))}}})
            for i in range(60)
        ]
        df = analyze.to_frame(rows)
        boundaries = analyze.detect_changepoints(df, [(SUBJECT, "mem.current")], penalty=0.01, min_gap=10.0)
        assert len(boundaries) >= 2  # the merge only means something if there was more than one raw hit
        for a, b in zip(boundaries, boundaries[1:]):
            assert b - a >= 10.0

    def test_fewer_than_four_usable_rows_returns_no_boundaries(self):
        """Pelt needs enough points to fit anything; 1-3 rows must short
        circuit rather than be handed to ruptures."""
        rows = [make_sample(i, float(i), cgroups={SUBJECT: {"mem": {"current": 100 + i}}}) for i in range(3)]
        df = analyze.to_frame(rows)
        assert analyze.detect_changepoints(df, [(SUBJECT, "mem.current")]) == []

    def test_empty_frame_returns_no_boundaries(self):
        df = analyze.to_frame([])
        assert analyze.detect_changepoints(df, [(SUBJECT, "mem.current")]) == []

    def test_unknown_columns_are_ignored_not_raised(self, sample_records):
        df = analyze.to_frame(sample_records)
        assert analyze.detect_changepoints(df, [("/no/such/target", "mem.current")]) == []


# ── auto_phases ──────────────────────────────────────────────────────────

class TestAutoPhases:
    def _known(self):
        return [
            Phase(name="startup", t0=0.0, t1=10.0, source="mark"),
            Phase(name="job-a", t0=10.0, t1=25.0, source="mark"),
            Phase(name="idle", t0=25.0, t1=39.0, source="mark"),
        ]

    def test_known_labels_survive_unchanged_when_no_extra_structure(self, sample_records):
        df = analyze.to_frame(sample_records)
        known = self._known()
        result = analyze.auto_phases(df, known, 0.0, 39.0)
        assert [(p.name, p.t0, p.t1, p.source) for p in result] == \
               [(p.name, p.t0, p.t1, p.source) for p in known]
        assert not any(p.name.startswith("auto-") for p in result)

    def test_no_known_phases_produces_pure_auto_segments(self, sample_records):
        df = analyze.to_frame(sample_records)
        result = analyze.auto_phases(df, [], 0.0, 39.0)
        assert result
        assert all(p.name.startswith("auto-") for p in result)
        assert result[0].t0 == 0.0
        assert result[-1].t1 == 39.0
        # contiguous, no gaps or overlaps
        for a, b in zip(result, result[1:]):
            assert a.t1 == b.t0

    def test_subdivides_a_known_phase_without_renaming_its_lead_segment(self):
        """A strong changepoint strictly inside a known phase (not near either
        of its boundaries) must split it: the leading piece keeps the known
        name, the trailing piece is named auto-N — the label is never
        overridden, but the extra structure is still surfaced."""
        rows = []
        for i in range(60):
            mono = float(i)
            # One big known phase [0, 60). A sharp, sustained level shift at
            # mono=30, far from both the 0 and 60 boundaries.
            current = 1000 if mono < 30 else 9000
            psi = 0.1 if mono < 30 else 9.0
            rows.append(make_sample(i, mono, cgroups={
                SUBJECT: {
                    "mem": {"current": current},
                    "psi_mem": {"some_avg10": psi},
                },
            }))
        df = analyze.to_frame(rows)
        known = [Phase(name="whole-run", t0=0.0, t1=60.0, source="mark")]
        result = analyze.auto_phases(df, known, 0.0, 60.0)
        assert result[0].name == "whole-run"
        assert result[0].t0 == 0.0
        assert len(result) >= 2
        assert any(p.name.startswith("auto-") for p in result[1:])
        assert result[-1].t1 == 60.0
        for a, b in zip(result, result[1:]):
            assert a.t1 == b.t0


# ── correlate ────────────────────────────────────────────────────────────

class TestCorrelate:
    def test_empty_frame_yields_no_rows(self):
        assert analyze.correlate(analyze.to_frame([]), [OBSERVER], [SUBJECT]) == []

    def test_a_target_is_never_correlated_against_itself(self, sample_records):
        """If the same key shows up in both observers and subjects (a
        misconfigured target list, say), it must not produce a self-pair."""
        df = analyze.to_frame(sample_records)
        rows = analyze.correlate(df, [SUBJECT], [SUBJECT, OBSERVER])
        assert all(not (r["observer"] == r["subject"]) for r in rows)
        assert any(r["subject"] == OBSERVER for r in rows)

    def test_fewer_than_two_overlapping_samples_yields_r_none_without_calling_corr(self):
        """n=1 (or 0): not enough to define a coefficient at all — this must
        short-circuit before ever asking pandas for one, not rely on corr()
        happening to return NaN on a single point."""
        records = [
            make_sample(0, 0.0, cgroups={
                SUBJECT: {"mem": {"current": 100}},
                OBSERVER: {"psi_mem": {"some_avg10": 5.0}},
            }),
            make_sample(1, 1.0, cgroups={
                OBSERVER: {"psi_mem": {"some_avg10": 6.0}},  # subject absent this tick
            }),
        ]
        df = analyze.to_frame(records)
        rows = analyze.correlate(df, [OBSERVER], [SUBJECT])
        row = [r for r in rows if r["metric"] == "psi_mem.some_avg10~mem.current"][0]
        assert row["n"] == 1
        assert row["r"] is None

    def test_every_row_carries_n(self, sample_records):
        df = analyze.to_frame(sample_records)
        rows = analyze.correlate(df, [OBSERVER], [SUBJECT])
        assert rows
        for row in rows:
            assert set(row) == {"observer", "subject", "metric", "r", "n"}
            assert isinstance(row["n"], int)

    def test_finds_the_correlation_the_tool_exists_for(self, sample_records):
        """The observer's rfd/s and PSI step in lockstep with the subject's
        ramp (10s-25s) by construction of the fixture; correlate must surface
        a strong positive r with a real sample count."""
        df = analyze.to_frame(sample_records)
        rows = analyze.correlate(df, [OBSERVER], [SUBJECT])
        strong = [r for r in rows if r["r"] is not None and r["r"] > 0.5]
        assert strong, rows
        for row in strong:
            assert row["n"] >= 30

    def test_no_overlap_yields_n_zero_and_r_none(self, sample_records):
        df = analyze.to_frame(sample_records)
        rows = analyze.correlate(df, [OBSERVER], ["/no/such/target"])
        assert rows == []  # subject columns simply do not exist on the frame


class TestCorrelateConstantSeries:
    """A constant series (observed on a real run: an idle target with zero
    variance for the whole window) makes Pearson's r 0/0 — undefined. numpy
    computes that as NaN and raises RuntimeWarning while doing it; neither
    may leak: the coefficient must come back None, never a fabricated 0.0 or
    1.0, and the warning must not spam a real run's console.
    """

    @staticmethod
    def _constant_observer_df():
        records = [
            make_sample(i, float(i), cgroups={
                SUBJECT: {"mem": {"current": 1000 + i * 50}},
                OBSERVER: {"psi_mem": {"some_avg10": 5.0}},  # identical every tick
            })
            for i in range(10)
        ]
        return analyze.to_frame(records)

    def test_r_is_none_not_a_fabricated_coefficient(self):
        df = self._constant_observer_df()
        rows = analyze.correlate(df, [OBSERVER], [SUBJECT])
        row = [r for r in rows if r["metric"] == "psi_mem.some_avg10~mem.current"][0]
        assert row["r"] is None
        assert row["n"] == 10

    def test_no_runtime_warning_escapes(self):
        df = self._constant_observer_df()
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            analyze.correlate(df, [OBSERVER], [SUBJECT])  # must not raise


class TestNoneIfNan:
    def test_none_stays_none(self):
        assert analyze._none_if_nan(None) is None

    def test_nan_becomes_none(self):
        assert analyze._none_if_nan(float("nan")) is None

    def test_finite_value_passes_through_as_float(self):
        assert analyze._none_if_nan(5) == 5.0

    def test_non_numeric_input_becomes_none(self):
        """``math.isnan`` raises ``TypeError`` on a non-float-like input;
        that must be treated the same as "not a number", not propagate."""
        assert analyze._none_if_nan("not-a-number") is None


class TestPhaseAndTargetStats:
    def test_phase_stats_empty_df_and_no_phases(self, sample_records):
        df = analyze.to_frame(sample_records)
        assert analyze._phase_stats(analyze.to_frame([]), [Phase(name="p", t0=0.0, t1=1.0)]) == []
        assert analyze._phase_stats(df, []) == []

    def test_phase_bin_with_zero_samples_for_one_column_is_skipped(self):
        """Target "b" is entirely unsampled during phase "late" — that
        (target, metric) combination must be left out of per_phase rather
        than reported as a fabricated zero."""
        records = [
            make_sample(0, 0.0, cgroups={"a": {"mem": {"current": 10}}, "b": {"mem": {"current": 20}}}),
            make_sample(1, 5.0, cgroups={"a": {"mem": {"current": 15}}}),  # b vanished this tick
        ]
        df = analyze.to_frame(records)
        phases = [Phase(name="early", t0=0.0, t1=3.0), Phase(name="late", t0=3.0, t1=10.0)]
        stats = analyze._phase_stats(df, phases)
        assert [s for s in stats if s["target"] == "b" and s["phase"] == "late"] == []
        b_early = [s for s in stats if s["target"] == "b" and s["phase"] == "early"]
        assert b_early and b_early[0]["n"] == 1

    def test_target_stats_empty_df(self):
        assert analyze._target_stats(analyze.to_frame([])) == []

    def test_target_stats_skips_a_column_that_is_entirely_nan(self):
        records = [
            make_sample(0, 0.0, cgroups={"a": {"mem": {"current": 10}, "psi_mem": {"some_avg10": None}}}),
            make_sample(1, 1.0, cgroups={"a": {"mem": {"current": 20}, "psi_mem": {"some_avg10": None}}}),
        ]
        df = analyze.to_frame(records)
        stats = analyze._target_stats(df)
        assert not any(s["metric"] == "psi_mem.some_avg10" for s in stats)
        assert any(s["metric"] == "mem.current" for s in stats)


# ── per-phase / per-target stats (exercised through build()) ────────────

class TestBuildEndToEnd:
    """Exercised against the real ``lib.store.RunDir`` — it landed while this
    module was being written, so there is no reason to test only against the
    stand-in. ``TestBuildAcceptsAnyRunDirShape`` below covers the duck-typing
    contract on its own.
    """

    def _run_dir(self, run_dir_with_data: Path) -> RunDir:
        return RunDir(str(run_dir_with_data.parent), run_id=run_dir_with_data.name, create=False)

    def test_build_returns_analysis(self, run_dir_with_data):
        analysis = analyze.build(self._run_dir(run_dir_with_data))
        assert isinstance(analysis, Analysis)

    def test_phases_come_from_marks(self, run_dir_with_data):
        analysis = analyze.build(self._run_dir(run_dir_with_data))
        names = [p.name for p in analysis.phases]
        assert names == ["startup", "job-a", "settle"]

    def test_events_loaded(self, run_dir_with_data):
        analysis = analyze.build(self._run_dir(run_dir_with_data))
        assert len(analysis.events) == 1
        assert analysis.events[0].kind == "memory_high_breach"

    def test_series_targets_remapped_to_manifest_keys(self, run_dir_with_data):
        analysis = analyze.build(self._run_dir(run_dir_with_data))
        targets = {s.target for s in analysis.series}
        assert targets <= {"gate", "soulmask"}
        assert "gate" in targets and "soulmask" in targets

    def test_series_roles_come_from_manifest(self, run_dir_with_data):
        analysis = analyze.build(self._run_dir(run_dir_with_data))
        roles = {s.target: s.role for s in analysis.series}
        assert roles["gate"] == "subject"
        assert roles["soulmask"] == "observer"

    def test_correlations_present_with_n(self, run_dir_with_data):
        analysis = analyze.build(self._run_dir(run_dir_with_data))
        assert analysis.correlations
        assert all("n" in c for c in analysis.correlations)

    def test_per_phase_and_per_target_populated(self, run_dir_with_data):
        analysis = analyze.build(self._run_dir(run_dir_with_data))
        assert analysis.per_phase
        assert analysis.per_target
        assert {row["phase"] for row in analysis.per_phase} == {"startup", "job-a", "settle"}

    def test_proposals_is_a_list_even_when_empty(self, run_dir_with_data):
        """This run's manifest carries limits={} — no proposal can be made
        from nothing, and make_proposals must not crash on that."""
        analysis = analyze.build(self._run_dir(run_dir_with_data))
        assert isinstance(analysis.proposals, list)


class _RaisingRunDir:
    """A run whose ``.read`` always fails — not "file missing" (which every
    real RunDir already handles internally) but a genuine I/O error, the
    case ``_read_safe`` exists for."""

    def __init__(self, path: str):
        self.path = path

    def read(self, name: str):
        raise OSError(f"simulated failure reading {name}")

    def read_manifest(self) -> Dict[str, Any]:
        return {"run_id": "r", "started": 0.0, "ended": 0.0, "targets": [], "host": {}, "limits": {}}


class TestReadSafe:
    def test_oserror_becomes_an_empty_list(self):
        assert analyze._read_safe(_RaisingRunDir("/x"), "samples.jsonl") == []

    def test_build_tolerates_a_run_whose_read_always_raises(self):
        analysis = analyze.build(_RaisingRunDir("/x"))
        assert analysis.series == []
        assert analysis.events == []
        assert analysis.phases  # still one "run" phase over the empty window


class TestRemapTargets:
    def test_empty_df_is_returned_unchanged(self):
        df = analyze.to_frame([])
        assert analyze._remap_targets(df, {}) is df


class TestBuildDescendantSummarisation:
    """Integration check for DESIGN.md §4.9a, through build(): a followed
    fan-out must fold to ``top_n`` kept series plus one "other", and the
    named target's own series must never be touched. Measured on a real run
    (dev-background.slice@follow): 29 cgroups -> 435 series before this
    existed; the mechanism this test checks is what brings that back down.
    """

    def _run_dir(self, tmp_path: Path):
        run = tmp_path / "run"
        run.mkdir()
        gate = "/dev.slice/dev-background.slice"
        descendants = [f"{gate}/docker-d{i}.scope" for i in range(10)]
        records = []
        for tick in range(3):
            cgroups = {gate: cg_metrics(current=1_000_000, usage_usec=tick * 10_000)}
            for i, path in enumerate(descendants):
                cgroups[path] = cg_metrics(current=(i + 1) * 1000, usage_usec=0)
            records.append(make_sample(tick, float(tick), cgroups=cgroups))
        with (run / "samples.jsonl").open("w") as fh:
            for r in records:
                fh.write(json.dumps(r) + "\n")
        (run / "manifest.json").write_text(json.dumps({
            "run_id": "descendant-test",
            "started": records[0]["t"],
            "ended": records[-1]["t"],
            "targets": [{"key": "gate", "cgroup": gate, "label": "dev-background.slice", "role": "subject"}],
            "host": {}, "limits": {},
        }))
        return FakeRunDir(run), gate, descendants

    def test_descendant_fan_out_folds_to_top_n_plus_other(self, tmp_path):
        run, gate, descendants = self._run_dir(tmp_path)
        analysis = analyze.build(run)
        mem_series = [s for s in analysis.series if s.key == "mem.current"]
        targets = {s.target for s in mem_series}
        assert "gate" in targets  # the named target: always kept
        assert "other" in targets
        kept_descendants = targets - {"gate", "other"}
        assert len(kept_descendants) == 6  # default top_n
        assert kept_descendants <= set(descendants)

    def test_named_target_series_are_never_touched_by_folding(self, tmp_path):
        run, _gate, _descendants = self._run_dir(tmp_path)
        analysis = analyze.build(run)
        gate_series = [s for s in analysis.series if s.target == "gate" and s.key == "mem.current"]
        assert len(gate_series) == 1
        assert gate_series[0].v == [1_000_000.0, 1_000_000.0, 1_000_000.0]


class TestBuildAcceptsAnyRunDirShape:
    """``build(run)`` is documented to need only ``.path``/``.read``/
    ``.read_manifest`` — not the concrete ``lib.store.RunDir`` class. Proven
    here with an independent implementation of that shape.
    """

    def test_build_works_against_the_minimal_stand_in(self, run_dir_with_data):
        analysis = analyze.build(FakeRunDir(run_dir_with_data))
        assert isinstance(analysis, Analysis)
        assert [p.name for p in analysis.phases] == ["startup", "job-a", "settle"]
        assert analysis.proposals == []  # this fixture's manifest carries limits={}


# ── mark time resolution / dedup (the wrapper-mode crash fix) ───────────
#
# `phases.emit_mark` deliberately never sets `mono` (a mark from a foreign
# container has no idea when this run's clock started); `build()` is the
# caller with the manifest needed to resolve it. Skipping this step is what
# crashed every real wrapper-mode run: wall-clock epoch seconds and
# monotonic run seconds landed in the same `pd.cut` boundary list, which
# pandas rejects as non-monotonic. `run_dir_with_data`'s marks (no `mono`
# field, two marks at an identical wall-clock instant) reproduce exactly the
# real fixture the coordinator hit this with; `TestBuildEndToEnd` and
# `TestBuildAcceptsAnyRunDirShape` above already prove `build()` no longer
# crashes on it. These tests isolate the two helpers that fix it.

class TestResolveMarkTimes:
    def test_backfills_mono_from_wall_clock_minus_started(self):
        started = 1_000_000.0
        marks = [Mark(t=started + 12.5, name="a", kind="phase")]
        resolved = analyze._resolve_mark_times(marks, started, t_start=0.0, t_end=100.0)
        assert resolved[0].mono == pytest.approx(12.5)

    def test_explicit_mono_is_left_alone(self):
        started = 1_000_000.0
        marks = [Mark(t=started + 999, name="a", kind="phase", mono=7.0)]
        resolved = analyze._resolve_mark_times(marks, started, t_start=0.0, t_end=100.0)
        assert resolved[0].mono == 7.0

    def test_clamps_a_mark_written_before_the_collector_attached(self):
        started = 1_000_000.0
        marks = [Mark(t=started - 50, name="early", kind="phase")]
        resolved = analyze._resolve_mark_times(marks, started, t_start=0.0, t_end=100.0)
        assert resolved[0].mono == 0.0

    def test_clamps_a_mark_written_after_the_collector_stopped(self):
        started = 1_000_000.0
        marks = [Mark(t=started + 500, name="late", kind="phase")]
        resolved = analyze._resolve_mark_times(marks, started, t_start=0.0, t_end=100.0)
        assert resolved[0].mono == 100.0

    def test_does_not_mutate_the_input_mark(self):
        started = 1_000_000.0
        original = Mark(t=started + 5, name="a", kind="phase")
        analyze._resolve_mark_times([original], started, t_start=0.0, t_end=100.0)
        assert original.mono is None


class TestDedupeSimultaneousPhaseMarks:
    def test_keeps_the_last_of_two_identical_instant_phase_marks(self):
        """The exact real scenario: `"<cmd>:done"` immediately followed by
        `"settle"`, `time.time()` returning the same value for both."""
        marks = [
            Mark(t=0.0, mono=25.0, name="idle", kind="phase"),
            Mark(t=0.0, mono=25.0, name="settle", kind="phase"),
        ]
        out = analyze._dedupe_simultaneous_phase_marks(marks)
        assert [m.name for m in out] == ["settle"]

    def test_distinct_instants_are_both_kept(self):
        marks = [
            Mark(t=0.0, mono=10.0, name="a", kind="phase"),
            Mark(t=0.0, mono=25.0, name="b", kind="phase"),
        ]
        out = analyze._dedupe_simultaneous_phase_marks(marks)
        assert [m.name for m in out] == ["a", "b"]

    def test_event_marks_at_the_same_instant_are_never_collapsed(self):
        """Only `phase` marks create boundaries; two simultaneous `event`
        marks are two point annotations, not a degenerate span."""
        marks = [
            Mark(t=0.0, mono=25.0, name="e1", kind="event"),
            Mark(t=0.0, mono=25.0, name="e2", kind="event"),
        ]
        out = analyze._dedupe_simultaneous_phase_marks(marks)
        assert [m.name for m in out] == ["e1", "e2"]

    def test_three_way_tie_keeps_only_the_last(self):
        marks = [
            Mark(t=0.0, mono=25.0, name="first", kind="phase"),
            Mark(t=0.0, mono=25.0, name="second", kind="phase"),
            Mark(t=0.0, mono=25.0, name="third", kind="phase"),
        ]
        out = analyze._dedupe_simultaneous_phase_marks(marks)
        assert [m.name for m in out] == ["third"]

    def test_sorts_by_resolved_position_before_deduping(self):
        """Marks may arrive out of chronological order (a foreign writer, a
        skewed clock); dedup must act on time order, not file order."""
        marks = [
            Mark(t=0.0, mono=25.0, name="second", kind="phase"),
            Mark(t=0.0, mono=10.0, name="first", kind="phase"),
        ]
        out = analyze._dedupe_simultaneous_phase_marks(marks)
        assert [m.name for m in out] == ["first", "second"]


# ── summarise_descendants (DESIGN.md §4.9a) ──────────────────────────────

def _descendant_series(target: str, group: str, key: str, values, unit: str = "bytes", t=None) -> Series:
    tt = t if t is not None else [float(i) for i in range(len(values))]
    return Series(key=key, target=target, label=target, unit=unit, group=group, t=list(tt), v=list(values))


class TestSummariseDescendants:
    def test_named_targets_are_never_folded_even_past_top_n(self):
        named = [_descendant_series(f"n{i}", "memory", "mem.current", [100.0]) for i in range(10)]
        out = analyze.summarise_descendants(named, named={s.target for s in named}, top_n=6)
        assert out == named

    def test_within_top_n_nothing_is_folded(self):
        members = [_descendant_series(f"d{i}", "memory", "mem.current", [float(i)]) for i in range(4)]
        out = analyze.summarise_descendants(members, named=set(), top_n=6)
        assert {s.target for s in out} == {f"d{i}" for i in range(4)}
        assert not any(s.target == "other" for s in out)

    def test_no_descendants_returns_the_input_series_unchanged(self):
        named = [_descendant_series("n0", "memory", "mem.current", [1.0])]
        out = analyze.summarise_descendants(named, named={"n0"}, top_n=6)
        assert out == named

    def test_additive_unit_tail_is_summed(self):
        # peaks 100..800 for d0..d7; top_n=6 keeps d7..d2, folds d1(200)+d0(100).
        members = [_descendant_series(f"d{i}", "memory", "mem.current",
                                       [(i + 1) * 100.0] * 3, unit="bytes") for i in range(8)]
        out = analyze.summarise_descendants(members, named=set(), top_n=6)
        kept = {s.target for s in out if s.target != "other"}
        assert kept == {"d2", "d3", "d4", "d5", "d6", "d7"}
        folded = [s for s in out if s.target == "other"]
        assert len(folded) == 1
        assert folded[0].label == "other (2 cgroups)"
        assert folded[0].v == [300.0, 300.0, 300.0]  # 100 + 200 at every tick
        assert folded[0].unit == "bytes"
        assert folded[0].group == "memory"
        assert folded[0].key == "mem.current"
        assert set(folded[0].meta["folded_targets"]) == {"d0", "d1"}
        assert folded[0].meta["folded_count"] == 2

    def test_non_additive_unit_tail_is_maxed_not_summed(self):
        """PSI is a saturation percentage — summing it across cgroups would
        invent a number nothing in the kernel ever produced."""
        members = [_descendant_series(f"d{i}", "pressure", "psi_mem.some_avg10",
                                       [(i + 1) * 10.0] * 2, unit="pct") for i in range(8)]
        out = analyze.summarise_descendants(members, named=set(), top_n=6)
        folded = [s for s in out if s.target == "other"][0]
        assert folded.label == "other (max of 2)"
        assert folded.v == [20.0, 20.0]  # max(d0=10, d1=20), never their sum (30)

    def test_ratio_unit_is_also_maxed(self):
        members = [_descendant_series(f"d{i}", "io", "some.ratio", [float(i)], unit="ratio")
                   for i in range(7)]
        out = analyze.summarise_descendants(members, named=set(), top_n=6)
        folded = [s for s in out if s.target == "other"][0]
        assert folded.label == "other (max of 1)"

    def test_fold_uses_present_members_and_is_none_only_when_all_absent(self):
        kept_anchor = _descendant_series("d0", "memory", "mem.current", [10.0, None, 30.0, 0.0])
        m1 = _descendant_series("d1", "memory", "mem.current", [None, 20.0, None, None])
        m2 = _descendant_series("d2", "memory", "mem.current", [5.0, 5.0, 5.0, None])
        out = analyze.summarise_descendants([kept_anchor, m1, m2], named=set(), top_n=1)
        kept = {s.target for s in out if s.target != "other"}
        assert kept == {"d0"}  # highest peak (30) is the only one that fits top_n=1
        folded = [s for s in out if s.target == "other"][0]
        assert folded.v == [5.0, 25.0, 5.0, None]

    def test_folding_aligns_by_timestamp_value_not_list_position(self):
        """Nothing guarantees every followed cgroup was sampled on an
        identical grid; a positional zip would silently misalign two members
        whose `t` lists differ in length."""
        m0 = _descendant_series("d0", "io", "io.rbytes_per_s", [1.0, 2.0, 3.0], unit="bytes/s",
                                 t=[0.0, 1.0, 2.0])
        m1 = _descendant_series("d1", "io", "io.rbytes_per_s", [100.0, 100.0], unit="bytes/s",
                                 t=[1.0, 2.0])
        m2 = _descendant_series("d2", "io", "io.rbytes_per_s", [9.0], unit="bytes/s", t=[3.0])
        out = analyze.summarise_descendants([m0, m1, m2], named=set(), top_n=0)
        folded = [s for s in out if s.target == "other"][0]
        assert folded.t == [0.0, 1.0, 2.0, 3.0]
        assert folded.v == [1.0, 102.0, 103.0, 9.0]

    def test_top_n_zero_folds_everything(self):
        members = [_descendant_series(f"d{i}", "memory", "mem.current", [1.0]) for i in range(3)]
        out = analyze.summarise_descendants(members, named=set(), top_n=0)
        assert {s.target for s in out} == {"other"}

    def test_ranking_uses_absolute_value_so_a_large_negative_peak_survives(self):
        members = [
            _descendant_series("big-negative", "memory", "delta", [-500.0]),
            _descendant_series("small-positive", "memory", "delta", [10.0]),
            _descendant_series("mid-positive", "memory", "delta", [50.0]),
        ]
        out = analyze.summarise_descendants(members, named=set(), top_n=2)
        kept = {s.target for s in out if s.target != "other"}
        assert kept == {"big-negative", "mid-positive"}

    def test_grouping_is_independent_per_group_and_key(self):
        """Two different (panel, metric) pairs each past top_n must fold
        separately — the "other" bucket for one metric must never absorb
        members of a different metric or panel."""
        mem_members = [_descendant_series(f"m{i}", "memory", "mem.current", [float(i)]) for i in range(8)]
        io_members = [_descendant_series(f"i{i}", "io", "io.rbytes_per_s", [float(i)], unit="bytes/s")
                      for i in range(8)]
        out = analyze.summarise_descendants(mem_members + io_members, named=set(), top_n=6)
        folded = [s for s in out if s.target == "other"]
        assert len(folded) == 2
        by_group = {s.group: s for s in folded}
        assert set(by_group["memory"].meta["folded_targets"]) == {"m0", "m1"}
        assert set(by_group["io"].meta["folded_targets"]) == {"i0", "i1"}


# ── make_proposals ───────────────────────────────────────────────────────
#
# Schema decided by the coordinator (cgprofile.py's writer): `Analysis.limits`
# is `{cgroup_path: {"resolved": {memory_max, memory_max_by, memory_high,
# memory_high_by, memory_swap_max, memory_swap_max_by, strict_min,
# recursive_min, strict_low, recursive_low, protection_mode, cpu_cores,
# cpu_cores_by, io_max, io_max_by, pids_max, warnings}, "described": [...],
# "fingerprint": "..."}}`. There is no top-level "mount_flags" (protection_mode
# is already resolved per cgroup) and no "own" LimitSet (only the resolved,
# chain-wide reading) — see lib/analyze.py's proposals-section comment and
# `_governing_floor` for how `_check_pinned` adapts to that.

def _resolved(
    *,
    memory_max=None,
    memory_high=None,
    strict_min=0,
    recursive_min=0,
    strict_low=0,
    recursive_low=0,
    protection_mode="strict",
    warnings=None,
) -> Dict[str, Any]:
    return {
        "memory_max": memory_max,
        "memory_high": memory_high,
        "strict_min": strict_min,
        "recursive_min": recursive_min,
        "strict_low": strict_low,
        "recursive_low": recursive_low,
        "protection_mode": protection_mode,
        "warnings": list(warnings or []),
    }


def _analysis_with_limits(*, resolved=None, host=None, targets=None) -> Analysis:
    """``resolved`` is ``{cgroup_path: <output of _resolved(...)>}``; wrapped
    here into the full ``manifest["limits"]`` entry shape (with an empty
    "described"/"fingerprint", since only "resolved" is exercised)."""
    limits = {
        path: {"resolved": r, "described": [], "fingerprint": "test"}
        for path, r in (resolved or {}).items()
    }
    return Analysis(
        manifest={"run_id": "synthetic"},
        targets=targets or [],
        host=host or {},
        limits=limits,
    )


class TestEffectiveLimitsShapeHandling:
    """`_effective_limits` must degrade to "nothing to reason over" for any
    shape that is not the structured ``"resolved"`` one — most importantly
    the shape a pre-this-schema run's manifest carries: a bare list of
    `limits.describe()` strings per cgroup path, with no "resolved" key."""

    def test_no_limits_key_at_all(self):
        analysis = Analysis(manifest={"run_id": "r"})
        assert analyze.make_proposals(analysis) == []

    def test_empty_limits_dict(self):
        analysis = Analysis(manifest={"run_id": "r"}, limits={})
        assert analyze.make_proposals(analysis) == []

    def test_bare_described_list_shape_does_not_crash_or_fabricate(self):
        """The older/degraded shape: a cgroup path mapped straight to
        describe()'s string lines, no "resolved" dict at all."""
        analysis = Analysis(
            manifest={"run_id": "r"},
            host={"MemTotal": 16 * 1024**3},
            limits={
                "/dev.slice/dev-background.slice": [
                    "memory.max: 8.0G (bound by /dev.slice/dev-background.slice)",
                    "memory.min: 0B",
                ],
            },
        )
        assert analyze.make_proposals(analysis) == []

    def test_entry_is_a_dict_but_has_no_resolved_key(self):
        analysis = Analysis(
            manifest={"run_id": "r"},
            host={"MemTotal": 16 * 1024**3},
            limits={"/some/path": {"described": ["a line"], "fingerprint": "x"}},
        )
        assert analyze.make_proposals(analysis) == []

    def test_resolved_present_but_not_a_dict(self):
        analysis = Analysis(
            manifest={"run_id": "r"},
            host={"MemTotal": 16 * 1024**3},
            limits={"/some/path": {"resolved": ["not", "a", "dict"]}},
        )
        assert analyze.make_proposals(analysis) == []


class TestMakeProposals:
    def test_no_limits_data_yields_no_proposals(self):
        analysis = _analysis_with_limits()
        assert analyze.make_proposals(analysis) == []

    def test_oversubscription_detected(self):
        analysis = _analysis_with_limits(
            host={"MemTotal": 16 * 1024**3},
            resolved={
                "/dev.slice": _resolved(memory_max=None),
                "/dev.slice/dev-interactive.slice": _resolved(memory_max=10 * 1024**3),
                "/dev.slice/dev-background.slice": _resolved(memory_max=10 * 1024**3),
            },
        )
        proposals = analyze.make_proposals(analysis)
        hit = [p for p in proposals if p.id.startswith("oversubscribed:")]
        assert hit, proposals
        assert hit[0].confidence == "inferred"
        assert hit[0].evidence
        assert hit[0].change

    def test_no_oversubscription_when_a_child_is_uncapped(self):
        analysis = _analysis_with_limits(
            host={"MemTotal": 16 * 1024**3},
            resolved={
                "/dev.slice": _resolved(memory_max=None),
                "/dev.slice/dev-interactive.slice": _resolved(memory_max=None),
                "/dev.slice/dev-background.slice": _resolved(memory_max=10 * 1024**3),
            },
        )
        assert not [p for p in analyze.make_proposals(analysis) if p.id.startswith("oversubscribed:")]

    def test_no_oversubscription_with_a_single_child(self):
        """< 2 children under a parent: nothing to sum, never a finding."""
        analysis = _analysis_with_limits(
            host={"MemTotal": 16 * 1024**3},
            resolved={"/dev.slice/dev-background.slice": _resolved(memory_max=20 * 1024**3)},
        )
        assert analyze.make_proposals(analysis) == []

    def test_root_cgroup_in_the_snapshot_does_not_crash_oversubscription(self):
        """``util.ancestors("/")`` has no parent to index into — the root
        must be skipped before that lookup, not after."""
        analysis = _analysis_with_limits(
            host={"MemTotal": 16 * 1024**3},
            resolved={
                "/": _resolved(memory_max=None),
                "/dev.slice/dev-interactive.slice": _resolved(memory_max=10 * 1024**3),
                "/dev.slice/dev-background.slice": _resolved(memory_max=10 * 1024**3),
            },
        )
        hit = [p for p in analyze.make_proposals(analysis) if p.id.startswith("oversubscribed:")]
        assert hit

    def test_no_oversubscription_when_host_ram_unknown(self):
        analysis = _analysis_with_limits(
            resolved={
                "/dev.slice/dev-interactive.slice": _resolved(memory_max=10 * 1024**3),
                "/dev.slice/dev-background.slice": _resolved(memory_max=10 * 1024**3),
            },
        )
        assert analyze.make_proposals(analysis) == []

    def test_oversubscription_boundary_exactly_at_host_ram_does_not_fire(self):
        """total == host RAM exactly must not be "past" it."""
        analysis = _analysis_with_limits(
            host={"MemTotal": 16 * 1024**3},
            resolved={
                "/dev.slice/dev-interactive.slice": _resolved(memory_max=8 * 1024**3),
                "/dev.slice/dev-background.slice": _resolved(memory_max=8 * 1024**3),
            },
        )
        assert not [p for p in analyze.make_proposals(analysis) if p.id.startswith("oversubscribed:")]

    def test_oversubscription_boundary_one_byte_over_fires(self):
        analysis = _analysis_with_limits(
            host={"MemTotal": 16 * 1024**3},
            resolved={
                "/dev.slice/dev-interactive.slice": _resolved(memory_max=8 * 1024**3),
                "/dev.slice/dev-background.slice": _resolved(memory_max=8 * 1024**3 + 1),
            },
        )
        hit = [p for p in analyze.make_proposals(analysis) if p.id.startswith("oversubscribed:")]
        assert hit

    def test_recursiveprot_gap_detected_on_low(self):
        analysis = _analysis_with_limits(
            resolved={
                "/wings.slice/wings-prod.slice": _resolved(
                    strict_min=5 * 1024**3, recursive_min=5 * 1024**3,
                    strict_low=0, recursive_low=6 * 1024**3,
                    warnings=["memory.low: 6.0G protection declared by /wings.slice is discarded"],
                ),
            },
        )
        proposals = analyze.make_proposals(analysis)
        hit = [p for p in proposals if p.id.startswith("recursiveprot-gap:")]
        assert hit, proposals
        assert hit[0].confidence == "inferred"
        assert "memory_recursiveprot" in hit[0].change[1]
        assert any("discarded" in e for e in hit[0].evidence)  # the limits.py warning carried through

    def test_recursiveprot_gap_detected_on_min_only(self):
        analysis = _analysis_with_limits(
            resolved={
                "/wings.slice/wings-prod.slice": _resolved(
                    strict_min=0, recursive_min=8 * 1024**3,
                    strict_low=0, recursive_low=0,
                ),
            },
        )
        hit = [p for p in analyze.make_proposals(analysis) if p.id.startswith("recursiveprot-gap:")]
        assert hit

    def test_recursiveprot_gap_absent_when_entry_says_recursive_mode(self):
        """protection_mode == "recursive" for this cgroup (the resolved
        mode already accounts for memory_recursiveprot, this schema carries
        no separate mount-flags field): nothing is being discarded."""
        analysis = _analysis_with_limits(
            resolved={
                "/wings.slice/wings-prod.slice": _resolved(
                    protection_mode="recursive",
                    strict_min=5 * 1024**3, recursive_min=5 * 1024**3,
                    strict_low=0, recursive_low=6 * 1024**3,
                ),
            },
        )
        assert analyze.make_proposals(analysis) == []

    def test_recursiveprot_no_gap_when_readings_match_exactly(self):
        """Boundary: recursive == strict on both axes -> nothing discarded."""
        analysis = _analysis_with_limits(
            resolved={
                "/wings.slice/wings-prod.slice": _resolved(
                    strict_min=5 * 1024**3, recursive_min=5 * 1024**3,
                    strict_low=6 * 1024**3, recursive_low=6 * 1024**3,
                ),
            },
        )
        assert analyze.make_proposals(analysis) == []

    def test_pinned_between_min_and_high_detected(self):
        analysis = _analysis_with_limits(
            resolved={
                "/wings.slice/wings-prod.slice": _resolved(
                    memory_high=5700 * 1024**2, strict_min=5500 * 1024**2,
                ),
            },
        )
        proposals = analyze.make_proposals(analysis)
        hit = [p for p in proposals if p.id.startswith("pinned:")]
        assert hit, proposals
        assert hit[0].confidence == "inferred"

    def test_pinned_uses_strict_low_when_min_absent(self):
        analysis = _analysis_with_limits(
            resolved={
                "/wings.slice/wings-prod.slice": _resolved(
                    memory_high=5700 * 1024**2, strict_low=5500 * 1024**2,
                ),
            },
        )
        hit = [p for p in analyze.make_proposals(analysis) if p.id.startswith("pinned:")]
        assert hit

    def test_pinned_uses_recursive_reading_under_recursive_mode(self):
        """Governing floor follows protection_mode: under recursive mode the
        strict_* fields (possibly zero) must not be consulted at all."""
        analysis = _analysis_with_limits(
            resolved={
                "/w": _resolved(
                    protection_mode="recursive",
                    memory_high=5700 * 1024**2,
                    strict_min=0, recursive_min=5500 * 1024**2,
                ),
            },
        )
        hit = [p for p in analyze.make_proposals(analysis) if p.id.startswith("pinned:")]
        assert hit

    def test_not_pinned_with_healthy_headroom(self):
        analysis = _analysis_with_limits(
            resolved={
                "/wings.slice/wings-prod.slice": _resolved(
                    memory_high=6 * 1024**3, strict_min=2 * 1024**3,
                ),
            },
        )
        assert not [p for p in analyze.make_proposals(analysis) if p.id.startswith("pinned:")]

    def test_pinned_boundary_headroom_exactly_15pct_fires(self):
        analysis = _analysis_with_limits(
            resolved={
                "/w": _resolved(memory_high=1_000_000, strict_min=850_000),  # (1e6-8.5e5)/1e6 == 0.15
            },
        )
        assert [p for p in analyze.make_proposals(analysis) if p.id.startswith("pinned:")]

    def test_pinned_boundary_just_over_15pct_does_not_fire(self):
        analysis = _analysis_with_limits(
            resolved={"/w": _resolved(memory_high=1_000_000, strict_min=849_999)},
        )
        assert not [p for p in analyze.make_proposals(analysis) if p.id.startswith("pinned:")]

    def test_pinned_absent_when_floor_is_zero(self):
        analysis = _analysis_with_limits(
            resolved={"/w": _resolved(memory_high=1_000_000, strict_min=0, strict_low=0)},
        )
        assert analyze.make_proposals(analysis) == []

    def test_pinned_absent_when_ceiling_missing(self):
        analysis = _analysis_with_limits(
            resolved={"/w": _resolved(memory_high=None, strict_min=500)},
        )
        assert analyze.make_proposals(analysis) == []

    def test_pinned_absent_when_floor_at_or_past_ceiling(self):
        analysis = _analysis_with_limits(
            resolved={"/w": _resolved(memory_high=1000, strict_min=1000)},
        )
        assert analyze.make_proposals(analysis) == []

    def test_shared_tier_with_unrelated_stack_detected(self):
        analysis = _analysis_with_limits(
            targets=[{"key": "gate", "cgroup": "/dev.slice/dev-background.slice", "role": "subject"}],
            resolved={
                "/dev.slice/dev-background.slice": _resolved(),
                "/dev.slice/dev-background.slice/docker-otherstack.scope": _resolved(),
            },
        )
        proposals = analyze.make_proposals(analysis)
        hit = [p for p in proposals if p.id.startswith("shared-tier:")]
        assert hit, proposals
        assert hit[0].confidence == "inferred"
        assert "otherstack" in hit[0].evidence[0]

    def test_no_shared_tier_proposal_when_sole_occupant(self):
        analysis = _analysis_with_limits(
            targets=[{"key": "gate", "cgroup": "/dev.slice/dev-background.slice", "role": "subject"}],
            resolved={"/dev.slice/dev-background.slice": _resolved()},
        )
        assert not [p for p in analyze.make_proposals(analysis) if p.id.startswith("shared-tier:")]

    def test_root_cgroup_in_the_snapshot_does_not_crash_shared_tier(self):
        analysis = _analysis_with_limits(
            targets=[{"key": "gate", "cgroup": "/dev.slice/dev-background.slice", "role": "subject"}],
            resolved={
                "/": _resolved(),
                "/dev.slice/dev-background.slice": _resolved(),
                "/dev.slice/dev-background.slice/docker-otherstack.scope": _resolved(),
            },
        )
        hit = [p for p in analyze.make_proposals(analysis) if p.id.startswith("shared-tier:")]
        assert hit

    def test_no_shared_tier_proposal_when_co_tenant_is_itself_a_target(self):
        """The "unrelated" cgroup is also one of this run's own targets
        (e.g. the observer) -> not unrelated, no proposal."""
        analysis = _analysis_with_limits(
            targets=[
                {"key": "gate", "cgroup": "/dev.slice/dev-background.slice", "role": "subject"},
                {"key": "sibling", "cgroup": "/dev.slice/dev-background.slice/docker-sibling.scope",
                 "role": "subject"},
            ],
            resolved={
                "/dev.slice/dev-background.slice": _resolved(),
                "/dev.slice/dev-background.slice/docker-sibling.scope": _resolved(),
            },
        )
        assert not [p for p in analyze.make_proposals(analysis) if p.id.startswith("shared-tier:")]

    def test_no_shared_tier_proposal_for_an_observer_role_target(self):
        """Only subject-role targets are checked for co-tenancy — an
        observer sharing a tier with something else is not this check's
        concern."""
        analysis = _analysis_with_limits(
            targets=[{"key": "obs", "cgroup": "/wings.slice/wings-prod.slice", "role": "observer"}],
            resolved={
                "/wings.slice/wings-prod.slice": _resolved(),
                "/wings.slice/wings-prod.slice/docker-other.scope": _resolved(),
            },
        )
        assert not [p for p in analyze.make_proposals(analysis) if p.id.startswith("shared-tier:")]

    def test_no_shared_tier_proposal_without_any_declared_targets(self):
        analysis = _analysis_with_limits(
            resolved={
                "/dev.slice/dev-background.slice": _resolved(),
                "/dev.slice/dev-background.slice/docker-otherstack.scope": _resolved(),
            },
        )
        assert analyze.make_proposals(analysis) == []

    def test_every_proposal_has_evidence_and_confidence(self):
        analysis = _analysis_with_limits(
            host={"MemTotal": 16 * 1024**3},
            targets=[{"key": "gate", "cgroup": "/dev.slice/dev-background.slice", "role": "subject"}],
            resolved={
                "/dev.slice": _resolved(memory_max=None),
                "/dev.slice/dev-interactive.slice": _resolved(memory_max=10 * 1024**3),
                "/dev.slice/dev-background.slice": _resolved(
                    memory_max=10 * 1024**3, memory_high=5700 * 1024**2, strict_min=5500 * 1024**2,
                ),
                "/dev.slice/dev-background.slice/docker-otherstack.scope": _resolved(),
            },
        )
        proposals = analyze.make_proposals(analysis)
        assert len(proposals) >= 3  # oversubscribed + pinned + shared-tier, at least
        for p in proposals:
            assert isinstance(p, Proposal)
            assert p.evidence
            assert p.change
            assert p.confidence in ("observed", "inferred", "speculative")
